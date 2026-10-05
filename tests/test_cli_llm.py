"""Testes do adaptador de CLIs (agy/claude/codex/opencode) — sem chamar CLI nenhuma."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from cinedata_agent import cli_llm
from cinedata_agent.agente import AgenteCineData
from cinedata_agent.ferramentas import executar_sql
from conftest import requer_banco


def test_prompt_tem_protocolo_ferramentas_e_conversa():
    ferramentas = cli_llm.ChatCLI(ferramenta="claude").bind_tools([executar_sql]).ferramentas_json
    msgs = [
        SystemMessage("Você é o CineData."),
        HumanMessage("quantos gêneros?"),
        AIMessage("", tool_calls=[{"name": "executar_sql", "args": {"consulta": "SELECT 1"}, "id": "c1"}]),
        ToolMessage("| n |\n| 19 |", tool_call_id="c1", name="executar_sql"),
    ]
    sistema, conversa = cli_llm.montar_prompt(msgs, ferramentas)
    assert "Você é o CineData." in sistema and "<tool_call>" in sistema and "executar_sql" in sistema
    assert "USUÁRIO:\nquantos gêneros?" in conversa
    assert '"consulta": "SELECT 1"' in conversa
    assert "RESULTADO DA FERRAMENTA (executar_sql)" in conversa


def test_interpreta_tool_call():
    msg = cli_llm.interpretar_saida(
        'Vou consultar.\n<tool_call>{"name": "executar_sql", "arguments": {"consulta": "SELECT 1"}}</tool_call>'
    )
    assert msg.tool_calls[0]["name"] == "executar_sql"
    assert msg.tool_calls[0]["args"] == {"consulta": "SELECT 1"}


def test_interpreta_json_em_bloco_de_codigo():
    msg = cli_llm.interpretar_saida('```json\n{"name": "executar_sql", "arguments": {"consulta": "SELECT 2"}}\n```')
    assert msg.tool_calls[0]["args"]["consulta"] == "SELECT 2"


def test_resposta_final_e_ansi_removido():
    msg = cli_llm.interpretar_saida("\x1b[32mSão 19 gêneros.\x1b[0m")
    assert not msg.tool_calls and msg.text == "São 19 gêneros."


def test_cli_inexistente_vira_erro_cli(monkeypatch):
    monkeypatch.setattr(cli_llm.shutil, "which", lambda nome: None)
    try:
        cli_llm.executar_cli("agy", None, "s", "c")
    except cli_llm.ErroCLI as e:
        assert "não foi encontrada" in str(e)
    else:
        raise AssertionError("devia ter dado ErroCLI")


@requer_banco
def test_agente_completo_com_cli_falsa(monkeypatch, cache_temporario):
    """O grafo inteiro funcionando com uma 'CLI' que responde pelo protocolo de texto."""
    respostas = iter([
        '<tool_call>{"name": "executar_sql", "arguments": {"consulta": "SELECT COUNT(*) AS n FROM dim_genres"}}</tool_call>',
        "Existem 19 gêneros no catálogo.",
    ])
    recebidos = []

    def cli_falsa(ferramenta, modelo, sistema, conversa, timeout=300):
        recebidos.append(conversa)
        return next(respostas)

    monkeypatch.setattr(cli_llm, "executar_cli", cli_falsa)
    monkeypatch.setattr(cli_llm.shutil, "which", lambda nome: f"C:/fake/{nome}.exe")
    agente = AgenteCineData(provedor="agy", modelos=["gemini-teste"], usar_cache=False)

    resp = agente.perguntar("quantos gêneros existem?", id_conversa="cli")

    assert resp.texto == "Existem 19 gêneros no catálogo."
    assert resp.modelo == "agy:gemini-teste"
    assert resp.consultas[0]["linhas"][0][0] == 19
    assert "RESULTADO DA FERRAMENTA" in recebidos[1]  # a 2ª chamada recebeu o resultado do SQL
