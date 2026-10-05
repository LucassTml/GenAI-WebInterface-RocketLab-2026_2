"""Testes do liga/desliga do Ollama (sem abrir o Ollama de verdade)."""

from cinedata_agent import llm, ollama_local


def test_nao_liga_nem_desliga_ollama_que_ja_estava_rodando(monkeypatch):
    monkeypatch.setattr(ollama_local, "rodando", lambda timeout=1.5: True)
    monkeypatch.setattr(ollama_local, "_processo", None)
    ok, msg = ollama_local.garantir()
    assert ok and "já estava rodando" in msg
    assert not ollama_local.iniciado_por_mim()
    ollama_local.parar()  # não pode quebrar nem matar nada


def test_sem_ollama_instalado(monkeypatch):
    monkeypatch.setattr(ollama_local, "rodando", lambda timeout=1.5: False)
    monkeypatch.setattr(ollama_local, "executavel", lambda: None)
    ok, msg = ollama_local.garantir()
    assert not ok and "não encontrado" in msg


def test_outros_provedores_nao_ligam_o_ollama(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ollama_local, "garantir", lambda *a, **k: chamadas.append(1) or (True, "ok"))
    monkeypatch.setattr(llm, "ollama_rodando", lambda timeout=1.5: False)
    llm.LLMComFallback(provedor="openrouter", modelos=["x"])
    llm.LLMComFallback(provedor="agy")
    llm.LLMComFallback(provedor="auto")
    assert chamadas == []
    llm.LLMComFallback(provedor="ollama")
    assert chamadas == [1]


def test_auto_so_usa_ollama_se_ja_estiver_ligado(monkeypatch):
    monkeypatch.setenv("ORDEM_PROVEDORES_AUTO", "ollama")
    monkeypatch.setattr(llm, "ollama_rodando", lambda timeout=1.5: False)
    assert llm.montar_alvos("auto") == []
    monkeypatch.setattr(llm, "ollama_rodando", lambda timeout=1.5: True)
    assert llm.montar_alvos("auto")[0].provedor == "ollama"
