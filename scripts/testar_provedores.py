"""
Testa os provedores/modelos configurados com UMA chamada de tool calling cada
e mostra quais funcionam com o agente.

    python scripts/testar_provedores.py                    # NVIDIA + Ollama
    python scripts/testar_provedores.py --provedor nvidia
    python scripts/testar_provedores.py --provedor openrouter   # gasta 1 req/modelo da cota de 50/dia

Por padrão o OpenRouter fica de fora, pra não gastar a cota diária à toa.
Um modelo só serve pro agente se ele devolver uma tool call (pedido pra
rodar o executar_sql); se responder só com texto, ele não sabe usar tools.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from cinedata_agent import llm  # noqa: E402
from cinedata_agent.ferramentas import executar_sql  # noqa: E402

PERGUNTA = "Quantos gêneros de filme existem no catálogo? Use a ferramenta executar_sql (tabela dim_genres)."


def testar(provedor: str, modelo: str) -> str:
    try:
        chat = llm.criar_chat(modelo, provedor).bind_tools([executar_sql])
    except llm.ErroLLM as e:
        return f"sem configuração: {e}"
    inicio = time.time()
    try:
        resposta = chat.invoke([SystemMessage("Você é um agente que responde usando SQL (SQLite)."),
                                HumanMessage(PERGUNTA)])
    except Exception as e:  # noqa: BLE001 - aqui eu quero mostrar qualquer erro
        codigo = getattr(e, "status_code", "")
        return f"ERRO {type(e).__name__} {codigo}".strip()
    tempo = time.time() - inicio
    if resposta.tool_calls:
        sql = resposta.tool_calls[0]["args"].get("consulta", "")[:70]
        return f"OK  ({tempo:.1f}s) tool call -> {sql}"
    return f"SEM TOOL CALL ({tempo:.1f}s): respondeu só com texto, não serve pro agente"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Testa tool calling em cada provedor/modelo")
    parser.add_argument("--provedor", choices=["openrouter", "nvidia", "ollama", "todos"], default=None)
    parser.add_argument("--modelo", help="testa só este modelo")
    args = parser.parse_args()

    if args.provedor == "todos":
        provedores = ["openrouter", "nvidia", "ollama"]
    elif args.provedor:
        provedores = [args.provedor]
    else:
        provedores = ["nvidia", "ollama"]

    status = llm.status_provedores()
    for p in provedores:
        ok, motivo = status[p]
        print(f"\n== {p} ({motivo})")
        if not ok:
            continue
        for modelo in [args.modelo] if args.modelo else llm.modelos_do_provedor(p):
            print(f"  {modelo:<45} ", end="", flush=True)
            print(testar(p, modelo))


if __name__ == "__main__":
    main()
