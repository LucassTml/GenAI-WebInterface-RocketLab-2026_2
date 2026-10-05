"""
Conexão com o LLM + fallback entre modelos e entre provedores.

OpenRouter, NVIDIA (build.nvidia.com) e Ollama falam a mesma API no formato
da OpenAI, então dá pra usar o ChatOpenAI do LangChain pros três, só trocando
a base_url e a chave. Cada "alvo" é um par (provedor, modelo).

Sobre o fallback (o motivo de existir este arquivo):
- Modelos :free compartilham capacidade. Quando o provider lota, a API
  devolve 429 com "provider" no erro. Isso NÃO é a nossa cota, então vale
  tentar o próximo modelo da lista.
- Quando é a cota diária do OpenRouter (50 req/dia) que acabou, o 429 vem com
  "free-models-per-day". Aí não adianta trocar de modelo DO OPENROUTER: todos
  vão falhar e cada falha CONTA na cota. No modo de um provedor só, paro na
  hora e aviso. No modo "auto", tiro o OpenRouter da fila e sigo pra NVIDIA
  e depois pro Ollama local.
- max_retries=0 no ChatOpenAI: o padrão do LangChain é tentar de novo 2x
  sozinho, o que queimaria 3 requisições num erro só.
- Modelo que deu 429 fica "de castigo" por alguns minutos, pra próxima
  pergunta já começar pelo modelo que está funcionando.
"""

import time
from typing import NamedTuple

import httpx
import openai
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from . import config
from .guardrails import resposta_parece_valida

PAUSA_MODELO_LOTADO_SEG = 180
MAX_TENTATIVAS_POR_CHAMADA = 3  # nunca gasta mais que isso numa chamada só


class ErroLLM(Exception):
    """Erro "amigável": a mensagem já é mostrada direto pro usuário."""


class ChaveInvalida(ErroLLM):
    pass


class CotaDiariaEsgotada(ErroLLM):
    pass


class LimitePorMinuto(ErroLLM):
    pass


class NenhumModeloDisponivel(ErroLLM):
    pass


class Alvo(NamedTuple):
    provedor: str
    modelo: str

    @property
    def rotulo(self) -> str:
        return f"{self.provedor}:{self.modelo}"


# ------------------------------------------------------------ provedores ---

def chave_configurada(provedor: str = "openrouter") -> bool:
    if provedor == "ollama":
        return True  # não usa chave
    chave = config.NVIDIA_API_KEY if provedor == "nvidia" else config.OPENROUTER_API_KEY
    return bool(chave) and "COLE_SUA_CHAVE" not in chave


def ollama_rodando(timeout: float = 1.5) -> bool:
    raiz = config.OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1")
    try:
        return httpx.get(f"{raiz}/api/version", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def status_provedores() -> dict[str, tuple[bool, str]]:
    """{provedor: (disponível?, explicação)} — usado na interface e no modo auto."""
    tem_or, tem_nv, ollama_ok = chave_configurada("openrouter"), chave_configurada("nvidia"), ollama_rodando()
    return {
        "openrouter": (tem_or, "chave configurada" if tem_or else "falta OPENROUTER_API_KEY no .env"),
        "nvidia": (tem_nv, "chave configurada" if tem_nv else "falta NVIDIA_API_KEY no .env"),
        "ollama": (ollama_ok, "rodando" if ollama_ok else "servidor do Ollama não está rodando"),
    }


def modelos_do_provedor(provedor: str) -> list[str]:
    return {
        "openrouter": config.MODELOS_LLM,
        "nvidia": config.MODELOS_NVIDIA,
        "ollama": config.MODELOS_OLLAMA,
    }[provedor]


def montar_alvos(provedor: str, modelos: list[str] | None = None) -> list[Alvo]:
    if provedor not in config.PROVEDORES_VALIDOS:
        raise ErroLLM(f"PROVEDOR_LLM='{provedor}' não existe. Use um de: {', '.join(config.PROVEDORES_VALIDOS)}.")
    if provedor != "auto":
        return [Alvo(provedor, m) for m in (modelos or modelos_do_provedor(provedor))]

    # modo auto: só entra provedor que tem chave (ou, no caso do Ollama, que
    # está rodando). Assim não gasto tentativa com provedor que nem vai responder.
    alvos = []
    for p in config.ORDEM_PROVEDORES_AUTO:
        if p not in ("openrouter", "nvidia", "ollama"):
            continue
        disponivel = ollama_rodando() if p == "ollama" else chave_configurada(p)
        if disponivel:
            alvos.extend(Alvo(p, m) for m in modelos_do_provedor(p))
    return alvos


def criar_chat(modelo: str, provedor: str | None = None) -> ChatOpenAI:
    provedor = provedor or config.PROVEDOR_LLM
    comum = dict(model=modelo, temperature=config.TEMPERATURA, max_retries=0)

    if provedor == "ollama":
        # o Ollama ignora a chave, mas o cliente exige algo; e modelo local
        # numa GPU de notebook é lento, por isso o timeout maior
        return ChatOpenAI(base_url=config.OLLAMA_BASE_URL, api_key="ollama",
                          timeout=config.TIMEOUT_LLM_SEGUNDOS * 3, **comum)

    if provedor == "nvidia":
        if not chave_configurada("nvidia"):
            raise ChaveInvalida(
                "A variável NVIDIA_API_KEY não está configurada. Gere a chave em https://build.nvidia.com "
                "(botão 'Get API Key', começa com nvapi-) e coloque no arquivo .env."
            )
        return ChatOpenAI(base_url=config.NVIDIA_BASE_URL, api_key=config.NVIDIA_API_KEY,
                          timeout=config.TIMEOUT_LLM_SEGUNDOS, **comum)

    if not chave_configurada("openrouter"):
        raise ChaveInvalida(
            "A variável OPENROUTER_API_KEY não está configurada. Crie a chave em "
            "https://openrouter.ai/keys e coloque no arquivo .env (veja o README)."
        )
    return ChatOpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
        timeout=config.TIMEOUT_LLM_SEGUNDOS,
        # header opcional que o OpenRouter usa pra identificar o app no painel
        default_headers={"X-Title": "CineData Agent - Rocket Lab"},
        **comum,
    )


def _texto_erro(e: Exception) -> str:
    corpo = getattr(e, "body", None)
    return f"{e} {corpo}".lower()


def _resposta_vazia(msg: AIMessage) -> bool:
    # alguns modelos free às vezes devolvem 200 OK sem texto e sem tool call
    return not msg.tool_calls and not (msg.text or "").strip()


MSG_COTA_OPENROUTER = (
    "A cota diária de modelos gratuitos do OpenRouter acabou (50 requisições/dia). Ela renova à meia-noite "
    "UTC (21h no horário de Brasília). Enquanto isso dá pra usar respostas do cache ou trocar de provedor: "
    "PROVEDOR_LLM=nvidia, ollama ou auto."
)


class LLMComFallback:
    def __init__(self, ferramentas=None, modelos: list[str] | None = None, provedor: str | None = None):
        self.provedor = (provedor or config.PROVEDOR_LLM).lower()
        self.alvos = montar_alvos(self.provedor, modelos)
        self.modelos = [a.rotulo for a in self.alvos]  # só pra mostrar na interface
        self.ferramentas = list(ferramentas or [])
        self._chats = {}
        self._pausado_ate: dict[str, float] = {}
        # provedores que saíram da fila (cota esgotada / chave recusada)
        self._provedores_fora: dict[str, str] = {}
        # conta TODAS as requisições (inclusive as que falharam), porque todas
        # contam na cota do OpenRouter
        self.requisicoes_feitas = 0

    @property
    def modo_auto(self) -> bool:
        return self.provedor == "auto"

    def _chat(self, alvo: Alvo, com_ferramentas: bool):
        chave = (alvo, com_ferramentas)
        if chave not in self._chats:
            chat = criar_chat(alvo.modelo, alvo.provedor)
            if com_ferramentas and self.ferramentas:
                chat = chat.bind_tools(self.ferramentas)
            self._chats[chave] = chat
        return self._chats[chave]

    def _ordem(self) -> list[Alvo]:
        agora = time.time()
        ativos = [a for a in self.alvos if a.provedor not in self._provedores_fora]
        livres = [a for a in ativos if self._pausado_ate.get(a.rotulo, 0) <= agora]
        pausados = [a for a in ativos if a not in livres]
        return livres + pausados  # pausados vão pro fim, não somem

    def _tirar_provedor(self, provedor: str, motivo: str):
        self._provedores_fora[provedor] = motivo

    def invocar(self, mensagens, com_ferramentas: bool = True) -> tuple[AIMessage, str]:
        """Chama o primeiro alvo que responder. Retorna (resposta, "provedor:modelo")."""
        if not self.alvos:
            raise NenhumModeloDisponivel(
                "Nenhum provedor disponível no modo auto: configure OPENROUTER_API_KEY ou NVIDIA_API_KEY no .env, "
                "ou inicie o Ollama (scripts/iniciar_ollama.ps1)."
            )
        erros, tentativas, pular_agora = [], 0, set()

        for alvo in self._ordem():
            if alvo.provedor in self._provedores_fora or alvo.provedor in pular_agora:
                continue
            if tentativas >= MAX_TENTATIVAS_POR_CHAMADA:
                break
            try:
                chat = self._chat(alvo, com_ferramentas)  # sem chave, para aqui (0 req)
            except ChaveInvalida as e:
                if not self.modo_auto:
                    raise
                self._tirar_provedor(alvo.provedor, str(e))
                continue

            tentativas += 1
            self.requisicoes_feitas += 1
            try:
                resposta = chat.invoke(mensagens)

            except openai.AuthenticationError:
                msg = f"O provedor '{alvo.provedor}' recusou a chave (erro 401). Confira a chave no .env."
                if not self.modo_auto:
                    raise ChaveInvalida(msg)
                self._tirar_provedor(alvo.provedor, msg)
                erros.append(f"{alvo.rotulo}: 401")
                continue
            except openai.RateLimitError as e:
                texto = _texto_erro(e)
                if alvo.provedor == "openrouter" and ("per-day" in texto or "per day" in texto):
                    if not self.modo_auto:
                        raise CotaDiariaEsgotada(MSG_COTA_OPENROUTER)
                    self._tirar_provedor("openrouter", "cota diária esgotada")
                    erros.append(f"{alvo.rotulo}: cota diária esgotada")
                    continue
                if "per-min" in texto or "per min" in texto:
                    if not self.modo_auto:
                        raise LimitePorMinuto(
                            "Muitas requisições em sequência (limite por minuto). Espere um minuto e tente de novo."
                        )
                    pular_agora.add(alvo.provedor)
                    erros.append(f"{alvo.rotulo}: limite por minuto")
                    continue
                # provider lotado (OpenRouter) ou rate limit da NVIDIA: próximo
                self._pausado_ate[alvo.rotulo] = time.time() + PAUSA_MODELO_LOTADO_SEG
                erros.append(f"{alvo.rotulo}: 429")
                continue
            except openai.APIStatusError as e:
                if e.status_code == 402:
                    msg = (f"O provedor '{alvo.provedor}' retornou 402 (sem saldo/créditos). Confira a conta "
                           f"(OpenRouter: openrouter.ai/settings/credits; NVIDIA: build.nvidia.com).")
                    if not self.modo_auto:
                        raise ErroLLM(msg)
                    self._tirar_provedor(alvo.provedor, msg)
                    erros.append(f"{alvo.rotulo}: 402")
                    continue
                # 404 (modelo saiu do ar / sem suporte a tools), 5xx, 400 etc.
                self._pausado_ate[alvo.rotulo] = time.time() + PAUSA_MODELO_LOTADO_SEG
                erros.append(f"{alvo.rotulo}: HTTP {e.status_code}")
                continue
            except (openai.APITimeoutError, openai.APIConnectionError, httpx.HTTPError) as e:
                if alvo.provedor == "ollama":
                    erros.append(f"{alvo.rotulo}: não consegui falar com o Ollama em {config.OLLAMA_BASE_URL} "
                                 f"(ele está rodando?)")
                    pular_agora.add("ollama")
                else:
                    erros.append(f"{alvo.rotulo}: {type(e).__name__}")
                continue

            if _resposta_vazia(resposta):
                erros.append(f"{alvo.rotulo}: resposta vazia")
                continue
            if not resposta.tool_calls and not resposta_parece_valida(resposta.text):
                # guardrail de saída: raciocínio vazado / texto degenerado
                erros.append(f"{alvo.rotulo}: resposta degenerada")
                continue
            return resposta, alvo.rotulo

        # chegou aqui: ninguém respondeu
        provedores = {a.provedor for a in self.alvos}
        if provedores and all(self._provedores_fora.get(p) == "cota diária esgotada" for p in provedores):
            raise CotaDiariaEsgotada(MSG_COTA_OPENROUTER)
        motivos = [f"{p}: {m}" for p, m in self._provedores_fora.items()]
        raise NenhumModeloDisponivel(
            "Nenhum modelo conseguiu responder agora. Tentativas: "
            + "; ".join(erros + motivos)
            + ". Espere alguns minutos, troque os modelos no .env ou use outro provedor (PROVEDOR_LLM)."
        )


def consultar_cota(timeout: float = 10) -> dict:
    """Consulta o endpoint /key do OpenRouter (não é chamada de modelo, não gasta cota).
    Retorna o JSON de "data" ou {"erro": "..."}."""
    if not chave_configurada("openrouter"):
        return {"erro": "OPENROUTER_API_KEY não configurada no .env"}
    try:
        r = httpx.get(
            f"{config.OPENROUTER_BASE_URL}/key",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("data", {})
    except httpx.HTTPStatusError as e:
        return {"erro": f"HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        return {"erro": str(e)}


def resumo_cota(dados: dict) -> str:
    if "erro" in dados:
        return f"não foi possível consultar a cota ({dados['erro']})"
    free = dados.get("free_model_daily_requests")
    if isinstance(free, dict) and "remaining" in free:
        return f"{free.get('used', '?')} usadas de {free.get('limit', '?')} hoje (restam {free['remaining']})"
    # se o formato mudar, mostra o que veio mesmo
    return f"limite={dados.get('limit')} uso={dados.get('usage')} free_tier={dados.get('is_free_tier')}"
