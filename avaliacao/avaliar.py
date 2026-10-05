"""
Avaliação automática do agente.

Roda as perguntas de avaliacao/perguntas.yaml no agente, compara com o
gabarito (ver comparador.py) e salva um relatório em avaliacao/resultados/.

ATENÇÃO COM A COTA: cada pergunta gasta ~2-3 requisições. As 17 perguntas
dão uns 40 requisições, quase a cota diária inteira (50). Por isso:
  - dá pra rodar só algumas:     --ids fin_01 pes_03   ou   --categoria "Elenco e Equipe"
  - dá pra ver a estimativa antes: --estimar
  - respostas ficam no cache, então rodar de novo a mesma pergunta não gasta nada
    (use --sem-cache pra forçar o agente a responder de novo)

Exemplos:
    python avaliacao/avaliar.py --estimar
    python avaliacao/avaliar.py --categoria "Bilheteria e Finanças"
    python avaliacao/avaliar.py --ids fin_01 pop_01
    python avaliacao/avaliar.py                       # tudo
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from avaliacao.comparador import avaliar  # noqa: E402
from avaliacao.gerar_gabarito import carregar_perguntas, rodar_gabarito  # noqa: E402
from cinedata_agent import config  # noqa: E402
from cinedata_agent.agente import AgenteCineData  # noqa: E402
from cinedata_agent.cache import CacheRespostas  # noqa: E402
from cinedata_agent.llm import consultar_cota  # noqa: E402

PASTA_RESULTADOS = RAIZ / "avaliacao" / "resultados"
CUSTO_ESTIMADO = {"bloqueado": 0, "sem_consulta": 1}  # o resto: ~3 requisições


def estimar_requisicoes(perguntas, usar_cache: bool) -> int:
    cache = CacheRespostas() if usar_cache else None
    total = 0
    for p in perguntas:
        if cache and cache.buscar(p["pergunta"]):
            continue
        total += CUSTO_ESTIMADO.get(p["modo"], 3)
    return total


def main():
    parser = argparse.ArgumentParser(description="Avaliação do agente CineData")
    parser.add_argument("--ids", nargs="*", help="ids das perguntas (ex: fin_01 pes_03)")
    parser.add_argument("--categoria", help="roda só uma categoria")
    parser.add_argument("--sem-cache", action="store_true", help="ignora o cache de respostas")
    parser.add_argument("--estimar", action="store_true", help="só mostra quantas requisições vai gastar")
    parser.add_argument("--forcar", action="store_true", help="roda mesmo se a cota parecer insuficiente")
    parser.add_argument("--pausa", type=float, default=3.0, help="segundos entre perguntas (limite 20 req/min)")
    parser.add_argument("--provedor", choices=list(config.PROVEDORES_VALIDOS), default=None,
                        help="sobrescreve o PROVEDOR_LLM do .env (ex.: nvidia, ollama, auto)")
    args = parser.parse_args()

    perguntas = carregar_perguntas()
    if args.ids:
        perguntas = [p for p in perguntas if p["id"] in args.ids]
    if args.categoria:
        perguntas = [p for p in perguntas if p["categoria"].lower() == args.categoria.lower()]
    if not perguntas:
        sys.exit("Nenhuma pergunta selecionada.")

    usar_cache = config.CACHE_ATIVO and not args.sem_cache
    estimativa = estimar_requisicoes(perguntas, usar_cache)
    print(f"{len(perguntas)} pergunta(s) selecionada(s). Estimativa: ~{estimativa} requisições ao LLM.")

    provedor = args.provedor or config.PROVEDOR_LLM
    if provedor == "openrouter" and estimativa > 0:
        cota = consultar_cota()
        restantes = (cota.get("free_model_daily_requests") or {}).get("remaining") if "erro" not in cota else None
        if restantes is not None:
            print(f"Cota restante hoje no OpenRouter: {restantes}")
            if restantes < estimativa and not args.forcar and not args.estimar:
                sys.exit("Cota provavelmente insuficiente. Rode menos perguntas (--ids/--categoria) ou use --forcar.")
    if args.estimar:
        return

    agente = AgenteCineData(usar_cache=usar_cache, provedor=provedor)
    resultados = []
    for i, item in enumerate(perguntas, 1):
        print(f"\n[{i}/{len(perguntas)}] {item['id']} - {item['pergunta']}")
        gabaritos = rodar_gabarito(item) if "sql" in item else []

        # cada pergunta numa conversa nova, pra uma não influenciar a outra
        resposta = agente.perguntar(item["pergunta"], id_conversa=agente.nova_conversa())
        nota = avaliar(item, resposta, gabaritos)

        status = "OK " if nota["aprovado"] else "ERRO"
        origem = "cache" if resposta.do_cache else (resposta.modelo or "-")
        print(f"    {status} nota={nota['nota']:.2f} | {resposta.requisicoes} req | {resposta.tempo_segundos:.1f}s | {origem}")

        resultados.append({
            "id": item["id"], "categoria": item["categoria"], "pergunta": item["pergunta"],
            **nota,
            "modelo": resposta.modelo, "do_cache": resposta.do_cache, "requisicoes": resposta.requisicoes,
            "tempo_segundos": round(resposta.tempo_segundos, 1), "erro_agente": resposta.erro,
            "sqls": [c.get("sql") for c in resposta.consultas], "resposta": resposta.texto,
        })

        if resposta.erro and "cota" in resposta.texto.lower():
            print("\nCota do dia acabou, parando a avaliação aqui (relatório parcial).")
            break
        if not resposta.do_cache and i < len(perguntas):
            time.sleep(args.pausa)

    salvar_relatorio(resultados)


def salvar_relatorio(resultados: list[dict]):
    PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M")
    aprovadas = sum(r["aprovado"] for r in resultados)
    gasto = sum(r["requisicoes"] for r in resultados)

    linhas = [
        f"# Avaliação do agente - {datetime.now():%d/%m/%Y %H:%M}\n",
        f"**Acertos: {aprovadas}/{len(resultados)} ({aprovadas / len(resultados):.0%})** · "
        f"requisições gastas: {gasto} · modelos: {', '.join(sorted({r['modelo'] or '-' for r in resultados}))}\n",
        "| id | categoria | nota dados | texto ok? | aprovada? | cita top-1 | req | tempo (s) | origem |",
        "|----|-----------|------------|-----------|-----------|------------|-----|-----------|--------|",
    ]
    for r in resultados:
        cita = {True: "sim", False: "não", None: "-"}[r["cita_top1"]]
        origem = "cache" if r["do_cache"] else (r["modelo"] or "-")
        texto_ok = "sim" if r.get("texto_ok", True) else "não"
        linhas.append(
            f"| {r['id']} | {r['categoria']} | {r['nota']:.2f} | {texto_ok} | {'✅' if r['aprovado'] else '❌'} "
            f"| {cita} | {r['requisicoes']} | {r['tempo_segundos']} | {origem} |"
        )
    linhas.append("\n## Respostas\n")
    for r in resultados:
        linhas.append(f"### {r['id']} - {r['pergunta']}\n")
        for sql in r["sqls"]:
            linhas.append(f"```sql\n{sql}\n```")
        linhas.append(f"\n{r['resposta']}\n")

    arq_md = PASTA_RESULTADOS / f"avaliacao_{carimbo}.md"
    arq_md.write_text("\n".join(linhas), encoding="utf-8")
    (PASTA_RESULTADOS / f"avaliacao_{carimbo}.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nAcertos: {aprovadas}/{len(resultados)} | requisições gastas: {gasto}")
    print(f"Relatório salvo em {arq_md}")


if __name__ == "__main__":
    # console do Windows não é UTF-8 por padrão e quebra os acentos
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
