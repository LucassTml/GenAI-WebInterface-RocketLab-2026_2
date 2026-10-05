"""
Mostra quantas requisições gratuitas ainda restam hoje no OpenRouter.

Consultar o endpoint /key NÃO conta como requisição de modelo, então dá pra
rodar à vontade antes de começar a testar.

    python scripts/verificar_cota.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cinedata_agent.llm import consultar_cota, resumo_cota  # noqa: E402

if __name__ == "__main__":
    # console do Windows não é UTF-8 por padrão e quebra os acentos
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    dados = consultar_cota()
    print("Cota de modelos gratuitos:", resumo_cota(dados))
    if "--json" in sys.argv:
        print(json.dumps(dados, indent=2, ensure_ascii=False))
    print("Lembrete: o contador zera à meia-noite UTC (21h em Brasília).")
