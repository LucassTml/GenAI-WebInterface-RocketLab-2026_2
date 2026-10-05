from cinedata_agent import provedores


def test_todos_os_provedores_tem_modelo_padrao():
    for pid in provedores.IDS:
        assert provedores.modelos(pid), pid


def test_modelos_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("MODELOS_GOOGLE", "gemini-a, gemini-b")
    assert provedores.modelos("google") == ["gemini-a", "gemini-b"]


def test_placeholder_nao_conta_como_chave(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-COLE_SUA_CHAVE_AQUI")
    assert not provedores.tem_chave("openrouter")
    assert provedores.tem_chave("ollama")  # não precisa de chave


def test_salvar_no_env(monkeypatch, tmp_path):
    arquivo = tmp_path / ".env"
    arquivo.write_text("OUTRA=1\n", encoding="utf-8")
    monkeypatch.setattr(provedores, "ARQUIVO_ENV", arquivo)
    provedores.salvar_no_env("GOOGLE_API_KEY", "abc123")
    texto = arquivo.read_text(encoding="utf-8")
    assert "OUTRA=1" in texto and "GOOGLE_API_KEY=abc123" in texto
    import os
    assert os.environ["GOOGLE_API_KEY"] == "abc123"
    monkeypatch.delenv("GOOGLE_API_KEY")


def test_ordem_auto_ignora_nome_invalido(monkeypatch):
    monkeypatch.setenv("ORDEM_PROVEDORES_AUTO", "openrouter,inventado,agy")
    assert provedores.ordem_auto() == ["openrouter", "agy"]
