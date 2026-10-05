"""
Conexão com o LLM + fallback entre modelos gratuitos.

O OpenRouter tem uma API compatível com a da OpenAI, então dá pra usar o
ChatOpenAI do LangChain só trocando a base_url. O mesmo vale pro Ollama
(modelo local), que deixei como opção pra quando a cota diária acabar.

Sobre o fallback (o motivo de existir este arquivo):
- Modelos :free compartilham capacidade. Quando o provider lota, a API
  devolve 429 com "provider" no erro. Isso NÃO é a nossa cota, então vale
  tentar o próximo modelo da lista.
- Quando é a cota diária (50 req/dia) que acabou, o 429 vem com
  "free-models-per-day". Aí não adianta trocar de modelo: todos vão falhar e
  cada falha CONTA na cota. Então paro na hora e aviso o usuário.
- max_retries=0 no ChatOpenAI: o padrão do LangChain é tentar de novo 2x
  sozinho, o que queimaria 3 requisições num erro só.
- Modelo que deu 429 fica "de castigo" por alguns minutos, pra próxima
  pergunta já começar pelo modelo que está funcionando.
"""

import time

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


def chave_configurada() -> bool:
    chave = config.OPENROUTER_API_KEY
    return bool(chave) and "COLE_SUA_CHAVE" not in chave


def criar_chat(modelo: str, provedor: str | None = None) -> ChatOpenAI:
    provedor = provedor or config.PROVEDOR_LLM
    if provedor == "ollama":
        return ChatOpenAI(
            model=modelo,
            base_url=config.OLLAMA_BASE_URL,
            api_key="ollama",  # o Ollama ignora, mas o cliente exige algo
            temperature=config.TEMPERATURA,
            max_retries=0,
            timeout=config.TIMEOUT_LLM_SEGUNDOS * 3,  # modelo local na CPU/GPU fraca é lento
        )

    if not chave_configurada():
        raise ChaveInvalida(
            "A variável OPENROUTER_API_KEY não está configurada. Crie a chave em "
            "https://openrouter.ai/keys e coloque no arquivo .env (veja o README)."
        )
    return ChatOpenAI(
        model=modelo,
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
        temperature=config.TEMPERATURA,
        max_retries=0,
        timeout=config.TIMEOUT_LLM_SEGUNDOS,
        # headers opcionais que o OpenRouter usa pra identificar o app no painel
        default_headers={"X-Title": "CineData Agent - Rocket Lab"},
    )


def _texto_erro(e: Exception) -> str:
    corpo = getattr(e, "body", None)
    return f"{e} {corpo}".lower()


def _resposta_vazia(msg: AIMessage) -> bool:
    # alguns modelos free às vezes devolvem 200 OK sem texto e sem tool call
    return not msg.tool_calls and not (msg.text or "").strip()


class LLMComFallback:
    def __init__(self, ferramentas=None, modelos: list[str] | None = None, provedor: str | None = None):
        self.provedor = provedor or config.PROVEDOR_LLM
        if modelos:
            self.modelos = list(modelos)
        elif self.provedor == "ollama":
            self.modelos = [config.OLLAMA_MODELO]
        else:
            self.modelos = list(config.MODELOS_LLM)
        self.ferramentas = list(ferramentas or [])
        self._chats = {}
        self._pausado_ate: dict[str, float] = {}
        # conta TODAS as requisições (inclusive as que falharam), porque todas
        # contam na cota do OpenRouter
        self.requisicoes_feitas = 0

    def _chat(self, modelo: str, com_ferramentas: bool):
        chave = (modelo, com_ferramentas)
        if chave not in self._chats:
            chat = criar_chat(modelo, self.provedor)
            if com_ferramentas and self.ferramentas:
                chat = chat.bind_tools(self.ferramentas)
            self._chats[chave] = chat
        return self._chats[chave]

    def _ordem_dos_modelos(self) -> list[str]:
        agora = time.time()
        livres = [m for m in self.modelos if self._pausado_ate.get(m, 0) <= agora]
        pausados = [m for m in self.modelos if m not in livres]
        return livres + pausados  # pausados vão pro fim, não somem

    def invocar(self, mensagens, com_ferramentas: bool = True) -> tuple[AIMessage, str]:
        """Chama o primeiro modelo que responder. Retorna (resposta, nome_do_modelo)."""
        erros = []
        for modelo in self._ordem_dos_modelos()[:MAX_TENTATIVAS_POR_CHAMADA]:
            chat = self._chat(modelo, com_ferramentas)  # se a chave não existe, para aqui (0 req)
            self.requisicoes_feitas += 1
            try:
                resposta = chat.invoke(mensagens)

            except openai.AuthenticationError:
                raise ChaveInvalida(
                    "O OpenRouter recusou a chave (erro 401). Confira a OPENROUTER_API_KEY no .env."
                )
            except openai.RateLimitError as e:
                texto = _texto_erro(e)
                if "per-day" in texto or "per day" in texto:
                    raise CotaDiariaEsgotada(
                        "A cota diária de modelos gratuitos do OpenRouter acabou (50 requisições/dia). "
                        "Ela renova à meia-noite UTC (21h no horário de Brasília). Enquanto isso dá pra "
                        "usar respostas do cache ou rodar com um modelo local (PROVEDOR_LLM=ollama)."
                    )
                if "per-min" in texto or "per min" in texto:
                    raise LimitePorMinuto(
                        "Muitas requisições em sequência (limite de 20 por minuto). Espere um minuto e tente de novo."
                    )
                # provider lotado: tenta o próximo modelo
                self._pausado_ate[modelo] = time.time() + PAUSA_MODELO_LOTADO_SEG
                erros.append(f"{modelo}: 429 (provider lotado)")
                continue
            except openai.APIStatusError as e:
                if e.status_code == 402:
                    raise ErroLLM(
                        "O OpenRouter retornou 402 (saldo negativo na conta). Modelos :free não cobram, "
                        "confira o saldo em https://openrouter.ai/settings/credits."
                    )
                # 404 (modelo saiu do ar / sem suporte a tools), 5xx, 400 etc.
                self._pausado_ate[modelo] = time.time() + PAUSA_MODELO_LOTADO_SEG
                erros.append(f"{modelo}: HTTP {e.status_code}")
                continue
            except (openai.APITimeoutError, openai.APIConnectionError, httpx.HTTPError) as e:
                if self.provedor == "ollama":
                    raise ErroLLM(
                        f"Não consegui falar com o Ollama em {config.OLLAMA_BASE_URL}. Ele está rodando? ({e})"
                    )
                erros.append(f"{modelo}: {type(e).__name__}")
                continue

            if _resposta_vazia(resposta):
                erros.append(f"{modelo}: resposta vazia")
                continue
            if not resposta.tool_calls and not resposta_parece_valida(resposta.text):
                # guardrail de saída: raciocínio vazado / texto degenerado
                erros.append(f"{modelo}: resposta degenerada")
                continue
            return resposta, modelo

        raise NenhumModeloDisponivel(
            "Nenhum modelo gratuito conseguiu responder agora. Tentativas: "
            + "; ".join(erros)
            + ". Espere alguns minutos ou troque a lista MODELOS_LLM no .env."
        )


def consultar_cota(timeout: float = 10) -> dict:
    """Consulta o endpoint /key do OpenRouter (não é chamada de modelo, não gasta cota).
    Retorna o JSON de "data" ou {"erro": "..."}."""
    if not chave_configurada():
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
