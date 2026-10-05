"""
Gera o índice de embeddings das sinopses usado pela busca semântica.

Uso:
    python scripts/indexar_sinopses.py            # índice completo (~50 min na CPU)
    python scripts/indexar_sinopses.py --limite 2000   # versão rápida pra testar

Só precisa rodar uma vez. O resultado fica em data/indice_sinopses.npz.
Sem o índice o agente continua funcionando, só não usa a busca semântica.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cinedata_agent import config  # noqa: E402
from cinedata_agent.busca_semantica import construir_indice  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Gera o índice semântico das sinopses")
    parser.add_argument("--limite", type=int, default=None, help="indexa só os N primeiros filmes (teste)")
    parser.add_argument("--modelo", default=config.MODELO_EMBEDDING)
    args = parser.parse_args()

    if not config.DB_PATH.exists():
        sys.exit(f"Banco não encontrado em {config.DB_PATH}")

    print(f"Modelo de embedding: {args.modelo}")
    print(f"Saída: {config.INDICE_SINOPSES_PATH}")
    inicio = time.time()
    total = construir_indice(modelo=args.modelo, limite=args.limite)
    minutos = (time.time() - inicio) / 60
    print(f"\nPronto! {total} sinopses indexadas em {minutos:.1f} min.")


if __name__ == "__main__":
    # console do Windows não é UTF-8 por padrão e quebra os acentos
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
