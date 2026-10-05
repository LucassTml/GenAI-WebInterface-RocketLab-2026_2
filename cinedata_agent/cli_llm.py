"""
Usa as CLIs de IA instaladas no PC (agy, claude, codex, opencode) como se
fossem um modelo do LangChain.

Por que existe: essas ferramentas usam o login/assinatura que a pessoa já tem
(Google Antigravity, Claude Code, ChatGPT/Codex, OpenCode), então dá pra rodar
o agente sem chave de API nenhuma.

O problema é que pela linha de comando não existe "tool calling" nativo: a CLI
recebe texto e devolve texto. Então fiz um protocolo simples:
  1. monto um prompt único com o prompt de sistema, a lista das NOSSAS
     ferramentas (executar_sql, busca semântica) e a conversa até agora;
  2. peço pro modelo responder <tool_call>{"name": ..., "arguments": {...}}</tool_call>
     quando quiser usar uma ferramenta, ou o texto final quando terminar;
  3. leio a saída e transformo em AIMessage com tool_calls de verdade.
Assim o resto do grafo (ToolNode, guardrails, cache...) nem percebe que o
"modelo" é uma CLI.

Segurança: cada chamada roda numa pasta temporária vazia, com as ferramentas
próprias da CLI desligadas ou em modo somente leitura (cada CLI tem um jeito,
ver _montar_comando). O agente nunca dá permissão pra CLI mexer em arquivo.
"""

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

LIMITE_ARGUMENTO_WINDOWS = 25_000  # a linha de comando do Windows aceita ~32 mil caracteres

PROTOCOLO = """
# Como usar as ferramentas (IMPORTANTE)
Você está rodando dentro de outro programa. NÃO use ferramentas próprias (terminal, arquivos, internet,
edição de código): as únicas ferramentas são as listadas abaixo, e quem executa é o programa.

Para chamar uma ferramenta, responda SOMENTE com um bloco assim, sem nenhum texto antes ou depois:
<tool_call>{"name": "NOME_DA_FERRAMENTA", "arguments": {...}}</tool_call>
O conteúdo precisa ser um JSON válido. O programa executa e devolve o resultado na conversa.
Quando já tiver os dados para responder, escreva a resposta final normalmente (sem <tool_call>).

## Ferramentas disponíveis
"""

_RE_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_RE_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_RE_JSON_CERCADO = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ErroCLI(Exception):
    """Falha ao rodar a CLI (não instalada, sem login, timeout...)."""


# ------------------------------------------------------------------ prompt --

def _descrever_ferramentas(ferramentas: list[dict]) -> str:
    linhas = []
    for f in ferramentas:
        func = f.get("function", f)
        params = json.dumps(func.get("parameters", {}), ensure_ascii=False)
        linhas.append(f"- {func['name']}: {func.get('description', '').strip()}\n  Parâmetros (JSON Schema): {params}")
    return "\n".join(linhas)


def montar_prompt(mensagens, ferramentas: list[dict]) -> tuple[str, str]:
    """Devolve (prompt_de_sistema, conversa)."""
    sistema = "\n\n".join(m.text for m in mensagens if isinstance(m, SystemMessage))
    if ferramentas:
        sistema += "\n" + PROTOCOLO + _descrever_ferramentas(ferramentas)

    partes = []
    for m in mensagens:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, HumanMessage):
            partes.append(f"USUÁRIO:\n{m.text}")
        elif isinstance(m, ToolMessage):
            partes.append(f"RESULTADO DA FERRAMENTA ({m.name or 'ferramenta'}):\n{m.text}")
        elif isinstance(m, AIMessage):
            texto = m.text.strip()
            for chamada in m.tool_calls:
                bloco = json.dumps({"name": chamada["name"], "arguments": chamada["args"]}, ensure_ascii=False)
                texto += f"\n<tool_call>{bloco}</tool_call>"
            partes.append(f"ASSISTENTE:\n{texto.strip()}")
    partes.append("ASSISTENTE (sua vez: uma chamada <tool_call> OU a resposta final):")
    return sistema.strip(), "\n\n".join(partes)


def interpretar_saida(texto: str) -> AIMessage:
    """Transforma a saída da CLI em AIMessage (com tool_calls se houver)."""
    texto = _RE_ANSI.sub("", texto).strip()
    achados = _RE_TOOL_CALL.findall(texto) or [
        j for j in _RE_JSON_CERCADO.findall(texto) if '"name"' in j and '"arguments"' in j
    ]
    chamadas = []
    for bruto in achados:
        try:
            dados = json.loads(bruto)
        except json.JSONDecodeError:
            continue
        if isinstance(dados, dict) and dados.get("name"):
            chamadas.append({
                "name": dados["name"],
                "args": dados.get("arguments") or {},
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "tool_call",
            })
    if chamadas:
        return AIMessage(content="", tool_calls=chamadas)
    return AIMessage(content=texto)


# ------------------------------------------------------------- execução ----

def _limpar_saida_opencode(texto: str) -> str:
    # o opencode imprime um cabeçalho tipo "> plan · nome-do-modelo" antes da resposta
    linhas = [l for l in _RE_ANSI.sub("", texto).splitlines() if not l.startswith("> ")]
    return "\n".join(linhas).strip()


def _montar_comando(ferramenta: str, exe: str, modelo: str | None, sistema: str, conversa: str,
                    pasta: Path, timeout: int) -> tuple[list[str], str | None, Path | None]:
    """Devolve (comando, texto_para_stdin, arquivo_de_saida)."""
    completo = f"{sistema}\n\n{conversa}"

    if ferramenta == "claude":
        # --tools "" desliga TODAS as ferramentas do Claude Code; o prompt de
        # sistema vai separado e a conversa entra pelo stdin
        cmd = [exe, "-p", "--tools", "", "--no-session-persistence", "--output-format", "text",
               "--system-prompt", sistema]
        if modelo:
            cmd += ["--model", modelo]
        return cmd, conversa, None

    if ferramenta == "codex":
        saida = pasta / "resposta.txt"
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
               "-C", str(pasta), "-o", str(saida)]
        if modelo:
            cmd += ["-m", modelo]
        return cmd + ["-"], completo, saida

    if ferramenta == "opencode":
        # agente "plan" = sem edição de arquivo. No Windows o opencode é um .cmd
        cmd = ["cmd", "/c", exe, "run", "--agent", "plan"] if exe.lower().endswith((".cmd", ".bat")) \
            else [exe, "run", "--agent", "plan"]
        if modelo:
            cmd += ["-m", modelo]
        return cmd, completo, None

    if ferramenta == "agy":
        # o agy só aceita o prompt colado no -p (-p="..."); se ficar grande
        # demais pra linha de comando do Windows, mando por arquivo
        cmd = [exe, "--mode", "plan", "--print-timeout", f"{timeout}s"]
        if modelo:
            cmd += ["--model", modelo]
        if len(completo) > LIMITE_ARGUMENTO_WINDOWS:
            (pasta / "prompt.txt").write_text(completo, encoding="utf-8")
            completo = "Leia o arquivo prompt.txt desta pasta e siga exatamente as instruções dele."
        return cmd + [f"-p={completo}"], None, None

    raise ErroCLI(f"CLI desconhecida: {ferramenta}")


def executar_cli(ferramenta: str, modelo: str | None, sistema: str, conversa: str, timeout: int = 300) -> str:
    exe = shutil.which(ferramenta)
    if not exe:
        raise ErroCLI(f"A CLI '{ferramenta}' não foi encontrada no PATH. Instale e faça login nela primeiro.")

    with tempfile.TemporaryDirectory(prefix="cinedata_cli_") as tmp:
        pasta = Path(tmp)
        cmd, entrada, arquivo_saida = _montar_comando(ferramenta, exe, modelo, sistema, conversa, pasta, timeout)
        try:
            proc = subprocess.run(
                cmd, input=entrada.encode("utf-8") if entrada is not None else None,
                stdin=None if entrada is not None else subprocess.DEVNULL,
                capture_output=True, timeout=timeout, cwd=pasta,
            )
        except subprocess.TimeoutExpired:
            raise ErroCLI(f"A CLI '{ferramenta}' passou de {timeout}s sem responder.") from None

        saida = proc.stdout.decode("utf-8", errors="replace")
        if arquivo_saida and arquivo_saida.exists():
            saida = arquivo_saida.read_text(encoding="utf-8", errors="replace")
        if ferramenta == "opencode":
            saida = _limpar_saida_opencode(saida)

        if proc.returncode != 0 or not saida.strip():
            erro = proc.stderr.decode("utf-8", errors="replace").strip()[-400:]
            raise ErroCLI(
                f"A CLI '{ferramenta}' falhou (código {proc.returncode}). Ela está logada? {erro or saida[-300:]}"
            )
        return saida.strip()


# ------------------------------------------------------- modelo LangChain --

class ChatCLI(BaseChatModel):
    ferramenta: str                 # "agy", "claude", "codex" ou "opencode"
    modelo: str | None = None       # None = modelo padrão configurado na CLI
    timeout: int = 300
    ferramentas_json: list[dict] = []

    @property
    def _llm_type(self) -> str:
        return f"cli-{self.ferramenta}"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"ferramentas_json": [convert_to_openai_tool(t) for t in tools]})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        sistema, conversa = montar_prompt(messages, self.ferramentas_json)
        texto = executar_cli(self.ferramenta, self.modelo, sistema, conversa, self.timeout)
        return ChatResult(generations=[ChatGeneration(message=interpretar_saida(texto))])
