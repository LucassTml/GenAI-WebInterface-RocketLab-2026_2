"""
Testes do fallback entre modelos. Simulo os erros do OpenRouter com
exceções do pacote openai, sem fazer requisição de verdade.
"""

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from cinedata_agent import config, llm


def _erro(classe, status, mensagem):
    resposta = httpx.Response(status, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    return classe(mensagem, response=resposta, body={"error": {"message": mensagem, "code": status}})


class ChatQueFalha:
    def __init__(self, erro=None, resposta=None):
        self.erro, self.resposta = erro, resposta
        self.chamadas = 0

    def bind_tools(self, tools, **kw):
        return self

    def invoke(self, mensagens):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.resposta


@pytest.fixture
def chats(monkeypatch):
    mapa = {}
    monkeypatch.setattr(llm, "criar_chat", lambda modelo, provedor=None: mapa[modelo])
    return mapa


def test_fallback_quando_provider_lotado(chats):
    chats["a:free"] = ChatQueFalha(erro=_erro(openai.RateLimitError, 429, "Provider returned error: rate-limited upstream"))
    chats["b:free"] = ChatQueFalha(resposta=AIMessage("oi"))
    cliente = llm.LLMComFallback(modelos=["a:free", "b:free"], provedor="openrouter")

    resposta, modelo = cliente.invocar([])

    assert modelo == "openrouter:b:free"
    assert cliente.requisicoes_feitas == 2  # a falha também conta na cota
    # na próxima chamada o modelo lotado vai pro fim da fila
    assert [a.modelo for a in cliente._ordem()] == ["b:free", "a:free"]


def test_cota_diaria_para_na_hora(chats):
    chats["a:free"] = ChatQueFalha(erro=_erro(openai.RateLimitError, 429, "Rate limit exceeded: free-models-per-day"))
    chats["b:free"] = ChatQueFalha(resposta=AIMessage("oi"))
    cliente = llm.LLMComFallback(modelos=["a:free", "b:free"], provedor="openrouter")

    with pytest.raises(llm.CotaDiariaEsgotada):
        cliente.invocar([])
    # não pode ter tentado o segundo modelo (ia gastar cota à toa)
    assert chats["b:free"].chamadas == 0


def test_chave_invalida(chats):
    chats["a:free"] = ChatQueFalha(erro=_erro(openai.AuthenticationError, 401, "No auth credentials found"))
    cliente = llm.LLMComFallback(modelos=["a:free", "b:free"], provedor="openrouter")
    with pytest.raises(llm.ChaveInvalida):
        cliente.invocar([])


def test_resposta_vazia_tenta_outro(chats):
    chats["a:free"] = ChatQueFalha(resposta=AIMessage(""))
    chats["b:free"] = ChatQueFalha(resposta=AIMessage("agora sim"))
    cliente = llm.LLMComFallback(modelos=["a:free", "b:free"], provedor="openrouter")
    resposta, modelo = cliente.invocar([])
    assert resposta.text == "agora sim" and modelo == "openrouter:b:free"


def test_limite_de_tentativas_por_chamada(chats):
    for nome in "abcde":
        chats[nome] = ChatQueFalha(erro=_erro(openai.InternalServerError, 502, "bad gateway"))
    cliente = llm.LLMComFallback(modelos=list("abcde"), provedor="openrouter")
    with pytest.raises(llm.NenhumModeloDisponivel):
        cliente.invocar([])
    assert cliente.requisicoes_feitas == llm.MAX_TENTATIVAS_POR_CHAMADA


def test_resposta_degenerada_tenta_outro(chats):
    chats["a:free"] = ChatQueFalha(resposta=AIMessage("The user asked: qual o lucro? I need to compute ,y a.y,.t"))
    chats["b:free"] = ChatQueFalha(resposta=AIMessage("O lucro médio de Science Fiction é R$ 520,8 mi."))
    cliente = llm.LLMComFallback(modelos=["a:free", "b:free"], provedor="openrouter")
    resposta, modelo = cliente.invocar([])
    assert modelo == "openrouter:b:free" and cliente.requisicoes_feitas == 2


# ------------------------------------------------- modo auto (vários provedores)

@pytest.fixture
def chats_por_provedor(monkeypatch):
    mapa = {}
    monkeypatch.setattr(llm, "criar_chat", lambda modelo, provedor=None: mapa[(provedor, modelo)])
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-v1-teste")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-teste")
    monkeypatch.setattr(config, "MODELOS_LLM", ["or-a:free", "or-b:free"])
    monkeypatch.setattr(config, "MODELOS_NVIDIA", ["nv-a"])
    monkeypatch.setattr(config, "MODELOS_OLLAMA", ["local"])
    monkeypatch.setattr(config, "ORDEM_PROVEDORES_AUTO", ["openrouter", "nvidia", "ollama"])
    monkeypatch.setattr(llm, "ollama_rodando", lambda timeout=1.5: True)
    return mapa


def test_auto_monta_fila_com_todos_os_provedores(chats_por_provedor):
    cliente = llm.LLMComFallback(provedor="auto")
    assert cliente.modelos == ["openrouter:or-a:free", "openrouter:or-b:free", "nvidia:nv-a", "ollama:local"]


def test_auto_ignora_provedor_sem_chave_e_ollama_parado(chats_por_provedor, monkeypatch):
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(llm, "ollama_rodando", lambda timeout=1.5: False)
    cliente = llm.LLMComFallback(provedor="auto")
    assert cliente.modelos == ["openrouter:or-a:free", "openrouter:or-b:free"]


def test_auto_cota_do_openrouter_acaba_e_vai_pra_nvidia(chats_por_provedor):
    m = chats_por_provedor
    m[("openrouter", "or-a:free")] = ChatQueFalha(erro=_erro(openai.RateLimitError, 429, "free-models-per-day"))
    m[("openrouter", "or-b:free")] = ChatQueFalha(resposta=AIMessage("nao devia chegar aqui"))
    m[("nvidia", "nv-a")] = ChatQueFalha(resposta=AIMessage("Resposta vinda da NVIDIA."))
    cliente = llm.LLMComFallback(provedor="auto")

    resposta, modelo = cliente.invocar([])

    assert modelo == "nvidia:nv-a"
    assert m[("openrouter", "or-b:free")].chamadas == 0  # o resto do OpenRouter foi pulado
    # na pergunta seguinte o OpenRouter nem é tentado (não gasta requisição)
    cliente.invocar([])
    assert m[("openrouter", "or-a:free")].chamadas == 1
    assert cliente.requisicoes_feitas == 3


def test_auto_chave_recusada_pula_provedor(chats_por_provedor):
    m = chats_por_provedor
    m[("openrouter", "or-a:free")] = ChatQueFalha(erro=_erro(openai.AuthenticationError, 401, "bad key"))
    m[("nvidia", "nv-a")] = ChatQueFalha(erro=_erro(openai.AuthenticationError, 401, "bad key"))
    m[("ollama", "local")] = ChatQueFalha(resposta=AIMessage("Resposta do modelo local."))
    resposta, modelo = llm.LLMComFallback(provedor="auto").invocar([])
    assert modelo == "ollama:local"


def test_provedor_invalido():
    with pytest.raises(llm.ErroLLM):
        llm.LLMComFallback(provedor="chatgpt")
