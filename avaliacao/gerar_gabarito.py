"""
Roda os SQLs de referência de avaliacao/perguntas.yaml e gera o arquivo
avaliacao/gabarito.md com as respostas esperadas.

Não usa LLM nenhum (custo zero), então pode rodar quantas vezes quiser.

    python avaliacao/gerar_gabarito.py
"""

import sys
from pathlib import Path

import yaml
from tabulate import tabulate

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from cinedata_agent.banco import executar_consulta  # noqa: E402

ARQ_PERGUNTAS = RAIZ / "avaliacao" / "perguntas.yaml"
ARQ_SAIDA = RAIZ / "avaliacao" / "gabarito.md"


def carregar_perguntas() -> list[dict]:
    with open(ARQ_PERGUNTAS, encoding="utf-8") as f:
        return yaml.safe_load(f)["perguntas"]


def rodar_gabarito(item: dict) -> list[dict]:
    """Executa o SQL principal + alternativas. Retorna lista de resultados."""
    resultados = []
    for sql in [item["sql"], *item.get("alternativas", [])]:
        r = executar_consulta(sql, timeout=180)
        if not r.ok:
            raise RuntimeError(f"Gabarito {item['id']} com erro: {r.erro}")
        resultados.append({"colunas": r.colunas, "linhas": [list(linha) for linha in r.linhas], "tempo": r.tempo_segundos})
    return resultados


def main():
    perguntas = carregar_perguntas()
    partes = [
        "# Gabarito da avaliação\n",
        "Respostas esperadas para o conjunto de perguntas de `perguntas.yaml`, geradas "
        "executando os SQLs de referência direto no `cinerocket.db` (`python avaliacao/gerar_gabarito.py`).\n",
        "> Obs: a pergunta `pes_01` usa `date('now')`, então o número pode mudar dependendo do dia em que roda.\n",
    ]
    for item in perguntas:
        print(f"[{item['id']}] {item['pergunta']}")
        partes.append(f"\n## {item['id']} - {item['pergunta']}\n")
        partes.append(f"*Categoria:* {item['categoria']} · *Critério:* `{item['modo']}`\n")
        if "sql" not in item:
            esperado = {
                "bloqueado": "o guardrail deve recusar sem chamar o LLM.",
                "sem_consulta": "o agente deve recusar educadamente, sem executar SQL.",
                "contem_titulo": f"algum título contendo: {', '.join(item.get('esperado', []))}.",
            }[item["modo"]]
            partes.append(f"\nEsperado: {esperado}\n")
            continue

        resultado = rodar_gabarito(item)[0]
        tabela = tabulate(resultado["linhas"][:20], headers=resultado["colunas"], tablefmt="github", floatfmt=".2f")
        partes.append(f"\n```sql\n{item['sql'].strip()}\n```\n\n{tabela}\n")
        print(f"    ok ({len(resultado['linhas'])} linhas, {resultado['tempo']:.1f}s)")

    ARQ_SAIDA.write_text("\n".join(partes), encoding="utf-8")
    print(f"\nGabarito salvo em {ARQ_SAIDA}")


if __name__ == "__main__":
    # console do Windows não é UTF-8 por padrão e quebra os acentos
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
