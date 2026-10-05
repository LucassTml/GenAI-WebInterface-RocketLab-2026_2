"""
Testa os provedores/modelos configurados com UMA chamada de tool calling cada
e mostra quais funcionam com o agente.

    python scripts/testar_provedores.py                       # todos os disponíveis, menos o OpenRouter
    python scripts/testar_provedores.py --provedor google
    python scripts/testar_provedores.py --provedor agy --modelo gemini-3.8-flash-low
    python scripts/testar_provedores.py --provedor todos      # inclui OpenRouter (gasta 1 req/modelo da cota)
    python scripts/testar_provedores.py --listar              # só mostra quais provedores estão disponíveis

Por padrão o OpenRouter fica de fora, pra não gastar a cota diária à toa.
Um modelo só serve pro agente se ele devolver uma tool call (pedido pra
rodar o executar_sql); se responder só com texto, ele não sabe usar tools.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cinedata_agent import llm, provedores  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Testa tool calling em cada provedor/modelo")
    parser.add_argument("--provedor", choices=[*provedores.IDS, "todos"], default=None)
    parser.add_argument("--modelo", help="testa só este modelo")
    parser.add_argument("--listar", action="store_true", help="só lista os provedores e o status")
    args = parser.parse_args()

    status = llm.status_provedores()
    if args.listar:
        for pid, (ok, motivo) in status.items():
            p = provedores.obter(pid)
            print(f"{'OK ' if ok else '-- '} {pid:<14} {p.nome:<32} {motivo}")
            print(f"     modelos ({p.var_modelos}): {', '.join(provedores.modelos(pid))}")
        return

    if args.provedor == "todos":
        alvo = provedores.IDS
    elif args.provedor:
        alvo = [args.provedor]
    else:
        alvo = [p for p in provedores.IDS if p != "openrouter"]

    for pid in alvo:
        ok, motivo = status[pid]
        print()
        print(f"== {pid} ({motivo})")
        if not ok:
            continue
        for modelo in [args.modelo] if args.modelo else provedores.modelos(pid):
            print(f"  {modelo:<40} ", end="", flush=True)
            funcionou, detalhe = llm.testar_modelo(pid, modelo)
            print(("OK  " if funcionou else "FALHOU ") + detalhe)


if __name__ == "__main__":
    main()
