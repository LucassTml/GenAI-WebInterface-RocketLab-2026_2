"""
Testes do fallback entre modelos. Simulo os erros do OpenRouter com
exceções do pacote openai, sem fazer requisição de verdade.
"""

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from cinedata_agent import llm


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

    assert modelo == "b:free"
    assert cliente.requisicoes_feitas == 2  # a falha também conta na cota
    # na próxima chamada o modelo lotado vai pro fim da fila
    assert cliente._ordem_dos_modelos() == ["b:free", "a:free"]


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
    assert resposta.text == "agora sim" and modelo == "b:free"


def test_limite_de_tentativas_por_chamada(chats):
    for nome in "abcde":
        chats[nome] = ChatQueFalha(erro=_erro(openai.InternalServerError, 502, "bad gateway"))
    cliente = llm.LLMComFallback(modelos=list("abcde"), provedor="openrouter")
    with pytest.raises(llm.NenhumModeloDisponivel):
        cliente.invocar([])
    assert cliente.requisicoes_feitas == llm.MAX_TENTATIVAS_POR_CHAMADA
