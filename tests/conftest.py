"""
Fixtures compartilhadas dos testes.

A ideia principal: testar o agente SEM gastar a cota do OpenRouter. Pra isso
troco o criar_chat() por um modelo falso que devolve respostas prontas
(inclusive tool calls). Assim dá pra testar o grafo, o guardrail, o limite
de chamadas, o cache e a memória de graça e em poucos segundos.
"""

import sys
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cinedata_agent import config, llm  # noqa: E402

requer_banco = pytest.mark.skipif(not config.DB_PATH.exists(), reason="cinerocket.db não encontrado em data/")


class ModeloFalso(GenericFakeChatModel):
    """Devolve as mensagens da lista em ordem e guarda o que recebeu."""

    recebidas: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.recebidas.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


@pytest.fixture
def modelo_falso(monkeypatch):
    """Uso: fake = modelo_falso([AIMessage(...), AIMessage(...)])"""

    def _criar(respostas):
        fake = ModeloFalso(messages=iter(respostas))
        fake.recebidas = []
        monkeypatch.setattr(llm, "criar_chat", lambda modelo, provedor=None: fake)
        return fake

    return _criar


@pytest.fixture
def cache_temporario(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_PATH", tmp_path / "cache_teste.db")
    return tmp_path / "cache_teste.db"
