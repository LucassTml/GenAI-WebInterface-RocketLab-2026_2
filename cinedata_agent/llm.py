"""
Conexão com o LLM + fallback entre modelos e entre provedores.

Os provedores estão catalogados em provedores.py. Quatro jeitos de conectar:
  - API compatível com OpenAI (OpenRouter, NVIDIA, Google Gemini, OpenCode Zen,
    OpenAI): ChatOpenAI do LangChain trocando só base_url e chave;
  - Anthropic: SDK oficial da Anthropic via langchain-anthropic;
  - Ollama: modelo local, também pela API compatível com OpenAI;
  - CLIs (agy, Claude Code, Codex, OpenCode): ChatCLI, ver cli_llm.py.
Cada "alvo" é um par (provedor, modelo).

Sobre o fallback (o motivo de existir este arquivo):
- Modelos :free compartilham capacidade. Quando o provider lota, a API
  devolve 429. Isso NÃO é a nossa cota, então vale tentar o próximo modelo.
- Quando é a cota diária do OpenRouter (50 req/dia) que acabou, o 429 vem com
  "free-models-per-day". Aí não adianta trocar de modelo DO OPENROUTER: todos
  vão falhar e cada falha CONTA na cota. No modo de um provedor só, paro na
  hora e aviso. No modo "auto", tiro o OpenRouter da fila e sigo pro próximo.
- max_retries=0: o padrão do LangChain é tentar de novo 2x sozinho, o que
  queimaria 3 requisições num erro só.
- Modelo que deu 429 fica "de castigo" por alguns minutos, pra próxima
  pergunta já começar pelo modelo que está funcionando.
"""

import os
import time
from typing import NamedTuple

import httpx
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from . import config, provedores
from .cli_llm import ChatCLI, ErroCLI
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
    return provedores.tem_chave(provedor)


def ollama_rodando(timeout: float = 1.5) -> bool:
    raiz = config.OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1").replace("localhost", "127.0.0.1")
    try:
        return httpx.get(f"{raiz}/api/version", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def disponivel(provedor_id: str) -> tuple[bool, str]:
    p = provedores.obter(provedor_id)
    if p.tipo == "ollama":
        ok = ollama_rodando()
        return ok, "rodando" if ok else "servidor do Ollama não está rodando"
    if p.eh_cli:
        caminho = provedores.caminho_cli(provedor_id)
        return bool(caminho), f"instalada ({caminho})" if caminho else f"'{p.comando_cli}' não encontrada no PATH"
    ok = provedores.tem_chave(provedor_id)
    return ok, "chave configurada" if ok else f"falta {p.var_chave} no .env"


def status_provedores() -> dict[str, tuple[bool, str]]:
    """{provedor: (disponível?, explicação)} — usado na interface e no modo auto."""
    return {pid: disponivel(pid) for pid in provedores.IDS}


def modelos_do_provedor(provedor: str) -> list[str]:
    return provedores.modelos(provedor)


def provedor_padrao() -> str:
    return os.getenv("PROVEDOR_LLM", config.PROVEDOR_LLM).strip().lower()


def montar_alvos(provedor: str, modelos: list[str] | None = None) -> list[Alvo]:
    if provedor not in provedores.VALIDOS:
        raise ErroLLM(f"PROVEDOR_LLM='{provedor}' não existe. Use um de: {', '.join(provedores.VALIDOS)}.")
    if provedor != "auto":
        return [Alvo(provedor, m) for m in (modelos or provedores.modelos(provedor))]

    # modo auto: só entra provedor disponível (com chave, CLI instalada ou
    # Ollama rodando). Assim não gasto tentativa com quem nem vai responder.
    alvos = []
    for p in provedores.ordem_auto():
        if disponivel(p)[0]:
            alvos.extend(Alvo(p, m) for m in provedores.modelos(p))
    return alvos


def criar_chat(modelo: str, provedor: str | None = None):
    provedor = provedor or provedor_padrao()
    p = provedores.obter(provedor)
    timeout = config.TIMEOUT_LLM_SEGUNDOS

    if p.eh_cli:
        # "padrao" = deixa a CLI usar o modelo que ela já tem configurado
        return ChatCLI(ferramenta=p.comando_cli, modelo=None if modelo in ("", "padrao") else modelo,
                       timeout=max(timeout * 3, 300))

    if p.tipo == "ollama":
        # o Ollama ignora a chave, mas o cliente exige algo; e modelo local
        # numa GPU de notebook é lento, por isso o timeout maior
        return ChatOpenAI(model=modelo, base_url=config.OLLAMA_BASE_URL, api_key="ollama",
                          temperature=config.TEMPERATURA, max_retries=0, timeout=timeout * 3)

    chave = provedores.chave(provedor)
    if not chave:
        raise ChaveInvalida(
            f"A variável {p.var_chave} não está configurada. Gere a chave em {p.site_chave} e coloque no "
            f".env (ou na tela 'Modelos e chaves' da interface web)."
        )

    if p.tipo == "anthropic":
        from langchain_anthropic import ChatAnthropic  # import aqui: só carrega se usar

        # Sem temperature (os modelos Claude atuais recusam) e com max_tokens
        # explícito: o padrão do langchain-anthropic é o máximo do modelo
        # (128k), e uma requisição sem streaming desse tamanho dá timeout no SDK.
        return ChatAnthropic(model=modelo, api_key=chave, max_tokens=16000, max_retries=0, timeout=timeout)

    extra = {"temperature": config.TEMPERATURA} if p.usa_temperatura else {}
    headers = {"X-Title": "CineData Agent - Rocket Lab"} if provedor == "openrouter" else None
    return ChatOpenAI(model=modelo, base_url=p.base_url, api_key=chave, max_retries=0, timeout=timeout,
                      default_headers=headers, **extra)


# --------------------------------------------------------- tratamento erro --

def _texto_erro(e: Exception) -> str:
    corpo = getattr(e, "body", None)
    return f"{e} {corpo}".lower()


def _tipo_erro(e: Exception) -> str | None:
    """Classifica erros do SDK da OpenAI, da Anthropic, do httpx e das CLIs.
    Os dois SDKs usam os mesmos nomes de classe e o atributo status_code."""
    if isinstance(e, ErroCLI):
        return "falha"
    nome = type(e).__name__
    status = getattr(e, "status_code", None)
    if nome == "AuthenticationError" or status in (401, 403):
        return "chave"
    if status == 429:
        return "limite"
    if status == 402:
        return "credito"
    if status is not None:
        return "http"
    if "Timeout" in nome or "Connection" in nome or isinstance(e, httpx.HTTPError):
        return "conexao"
    return None


def _resposta_vazia(msg: AIMessage) -> bool:
    # alguns modelos free às vezes devolvem 200 OK sem texto e sem tool call
    return not msg.tool_calls and not (msg.text or "").strip()


MSG_COTA_OPENROUTER = (
    "A cota diária de modelos gratuitos do OpenRouter acabou (50 requisições/dia). Ela renova à meia-noite "
    "UTC (21h no horário de Brasília). Enquanto isso dá pra usar respostas do cache ou trocar de provedor "
    "(PROVEDOR_LLM=auto, nvidia, google, ollama, agy, claude-code...)."
)


class LLMComFallback:
    def __init__(self, ferramentas=None, modelos: list[str] | None = None, provedor: str | None = None):
        self.provedor = (provedor or provedor_padrao()).lower()
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
                "Nenhum provedor disponível no modo auto: configure alguma chave no .env (ou na tela 'Modelos e "
                "chaves'), inicie o Ollama (scripts/iniciar_ollama.ps1) ou inclua uma CLI em ORDEM_PROVEDORES_AUTO."
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
            except Exception as e:  # noqa: BLE001 - classifico abaixo e relanço o que não conheço
                tipo = _tipo_erro(e)
                if tipo is None:
                    raise
                texto = _texto_erro(e)

                if tipo == "chave":
                    msg = f"O provedor '{alvo.provedor}' recusou a chave (erro 401/403). Confira a chave no .env."
                    if not self.modo_auto:
                        raise ChaveInvalida(msg) from None
                    self._tirar_provedor(alvo.provedor, msg)
                    erros.append(f"{alvo.rotulo}: chave recusada")
                elif tipo == "limite":
                    if alvo.provedor == "openrouter" and ("per-day" in texto or "per day" in texto):
                        if not self.modo_auto:
                            raise CotaDiariaEsgotada(MSG_COTA_OPENROUTER) from None
                        self._tirar_provedor("openrouter", "cota diária esgotada")
                        erros.append(f"{alvo.rotulo}: cota diária esgotada")
                    elif "per-min" in texto or "per min" in texto:
                        if not self.modo_auto:
                            raise LimitePorMinuto(
                                "Muitas requisições em sequência (limite por minuto). Espere um minuto e tente de novo."
                            ) from None
                        pular_agora.add(alvo.provedor)
                        erros.append(f"{alvo.rotulo}: limite por minuto")
                    else:
                        # provider lotado / rate limit: põe o modelo de castigo e tenta o próximo
                        self._pausado_ate[alvo.rotulo] = time.time() + PAUSA_MODELO_LOTADO_SEG
                        erros.append(f"{alvo.rotulo}: 429")
                elif tipo == "credito":
                    msg = f"O provedor '{alvo.provedor}' retornou 402 (sem saldo/créditos na conta)."
                    if not self.modo_auto:
                        raise ErroLLM(msg) from None
                    self._tirar_provedor(alvo.provedor, msg)
                    erros.append(f"{alvo.rotulo}: 402")
                elif tipo == "http":
                    # 404 (modelo não existe / sem tools), 5xx, 400...
                    self._pausado_ate[alvo.rotulo] = time.time() + PAUSA_MODELO_LOTADO_SEG
                    erros.append(f"{alvo.rotulo}: HTTP {getattr(e, 'status_code', '?')}")
                elif tipo == "falha":
                    erros.append(f"{alvo.rotulo}: {e}")
                    pular_agora.add(alvo.provedor)  # CLI quebrada: não adianta tentar outro modelo dela agora
                else:  # conexao
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
        provs = {a.provedor for a in self.alvos}
        if provs and all(self._provedores_fora.get(p) == "cota diária esgotada" for p in provs):
            raise CotaDiariaEsgotada(MSG_COTA_OPENROUTER)
        motivos = [f"{p}: {m}" for p, m in self._provedores_fora.items()]
        raise NenhumModeloDisponivel(
            "Nenhum modelo conseguiu responder agora. Tentativas: "
            + "; ".join(erros + motivos)
            + ". Espere alguns minutos, troque os modelos ou use outro provedor (PROVEDOR_LLM)."
        )


def consultar_cota(timeout: float = 10) -> dict:
    """Consulta o endpoint /key do OpenRouter (não é chamada de modelo, não gasta cota).
    Retorna o JSON de "data" ou {"erro": "..."}."""
    chave = provedores.chave("openrouter")
    if not chave:
        return {"erro": "OPENROUTER_API_KEY não configurada no .env"}
    try:
        r = httpx.get(f"{config.OPENROUTER_BASE_URL}/key", headers={"Authorization": f"Bearer {chave}"},
                      timeout=timeout)
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


# ------------------------------------------- ferramentas da tela de config --

PERGUNTA_TESTE = "Quantos gêneros de filme existem no catálogo? Use a ferramenta executar_sql (tabela dim_genres)."


def testar_modelo(provedor: str, modelo: str) -> tuple[bool, str]:
    """Faz UMA chamada com tool calling e diz se o modelo serve pro agente
    (usado pela tela 'Modelos e chaves' e por scripts/testar_provedores.py)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from .ferramentas import executar_sql

    try:
        chat = criar_chat(modelo, provedor).bind_tools([executar_sql])
    except ErroLLM as e:
        return False, str(e)
    inicio = time.time()
    try:
        resposta = chat.invoke([SystemMessage("Você é um agente que responde consultando um banco SQLite."),
                                HumanMessage(PERGUNTA_TESTE)])
    except Exception as e:  # noqa: BLE001 - aqui eu quero mostrar qualquer erro
        codigo = getattr(e, "status_code", "")
        return False, f"erro {type(e).__name__} {codigo}: {str(e)[:200]}".strip()
    tempo = time.time() - inicio
    if resposta.tool_calls:
        sql = str(resposta.tool_calls[0]["args"].get("consulta", ""))[:80]
        return True, f"OK em {tempo:.1f}s, pediu a ferramenta: {sql}"
    return False, f"respondeu só com texto em {tempo:.1f}s (não usou a ferramenta, não serve pro agente)"


def listar_modelos_disponiveis(provedor: str) -> list[str]:
    """Pergunta pro próprio provedor quais modelos a conta enxerga."""
    import subprocess

    p = provedores.obter(provedor)
    if p.tipo == "ollama":
        raiz = config.OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1").replace("localhost", "127.0.0.1")
        dados = httpx.get(f"{raiz}/api/tags", timeout=5).json()
        return sorted(m["name"] for m in dados.get("models", []))
    if p.id == "agy":
        caminho = provedores.caminho_cli("agy")
        if not caminho:
            return []
        saida = subprocess.run([caminho, "models"], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        return [linha.split()[0] for linha in saida.splitlines() if linha.strip() and "\t" in linha]
    if p.eh_cli:
        return []  # claude/codex/opencode: usar "padrao" ou digitar o nome
    chave = provedores.chave(provedor)
    if not chave:
        raise ChaveInvalida(f"Configure a {p.var_chave} primeiro.")
    if p.tipo == "anthropic":
        import anthropic

        return [m.id for m in anthropic.Anthropic(api_key=chave).models.list()]
    r = httpx.get(f"{p.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {chave}"}, timeout=20)
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))
