"""
Interface de linha de comando do agente CineData.

    python main.py                                  # modo conversa (com memória)
    python main.py "Top 10 filmes com maior receita"  # pergunta única
    python main.py --mostrar-sql                    # mostra o SQL e a tabela de cada consulta
    python main.py --provedor ollama                # modelo local (Ollama)
    python main.py --provedor nvidia                # API da NVIDIA (precisa de NVIDIA_API_KEY)
    python main.py --provedor auto                  # OpenRouter -> NVIDIA -> Ollama

Comandos dentro do chat: /nova  /sql  /cota  /exemplos  /ajuda  /sair
"""

import argparse
import sys

from tabulate import tabulate

from cinedata_agent import config
from cinedata_agent.agente import AgenteCineData
from cinedata_agent.banco import banco_disponivel
from cinedata_agent.llm import consultar_cota, resumo_cota

EXEMPLOS = [
    "Quais são os 10 filmes com maior receita em R$?",
    "Qual o lucro médio por gênero, considerando apenas filmes com receita informada?",
    "Quais são os 5 filmes mais populares?",
    "Qual a nota média IMDb por ano de lançamento?",
    "Quais diretores têm a maior nota média, com no mínimo 5 filmes?",
    "Qual a dupla ator-diretor que mais trabalhou junta?",
    "Qual gênero tem a maior margem de lucro média?",
    "Quais filmes falam sobre viagem no tempo?",
]

AJUDA = """
Comandos:
  /nova      começa uma conversa nova (limpa a memória)
  /sql       liga/desliga a exibição do SQL e da tabela de resultado
  /cota      mostra quantas requisições gratuitas ainda restam hoje no OpenRouter
  /exemplos  mostra perguntas de exemplo
  /sair      encerra
"""


def mostrar_consultas(consultas: list[dict]):
    for i, c in enumerate(consultas, 1):
        print(f"\n  ── consulta {i} ({c.get('ferramenta', 'sql')}) ──")
        print("  " + str(c.get("sql", "")).strip().replace("\n", "\n  "))
        if c.get("erro"):
            print(f"  ✗ erro: {c['erro']}")
        elif c.get("linhas"):
            # sinopse inteira no terminal fica ilegível, então corto os textos longos
            linhas = [[v[:57] + "..." if isinstance(v, str) and len(v) > 60 else v for v in linha]
                      for linha in c["linhas"][:10]]
            tabela = tabulate(linhas, headers=c["colunas"], tablefmt="simple")
            print("  " + tabela.replace("\n", "\n  "))
            if c["total_linhas"] > 10:
                print(f"  ... ({c['total_linhas']} linhas no total)")


def responder(agente: AgenteCineData, pergunta: str, id_conversa: str, mostrar_sql: bool):
    print("\nPensando...", end="\r", flush=True)
    resp = agente.perguntar(pergunta, id_conversa)
    print(" " * 20, end="\r")
    print(f"\nCineData> {resp.texto}\n")
    if mostrar_sql and resp.consultas:
        mostrar_consultas(resp.consultas)
        print()
    origem = "guardrail" if resp.bloqueado else ("cache" if resp.do_cache else (resp.modelo or "-"))
    print(f"   [{origem} · {resp.requisicoes} req · {resp.tempo_segundos:.1f}s]")


def main():
    # o terminal do Windows às vezes não está em UTF-8 e quebra os acentos.
    # O stdin também: testando com a pergunta vindo por pipe, "tubarões"
    # chegava no modelo como "tubarÃµes" e a busca semântica trazia lixo.
    for fluxo in (sys.stdout, sys.stdin):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Agente Text-to-SQL da CineData Analytics")
    parser.add_argument("pergunta", nargs="*", help="pergunta única (sem isso abre o modo conversa)")
    parser.add_argument("--mostrar-sql", action="store_true", help="mostra SQL e resultado das consultas")
    parser.add_argument("--sem-cache", action="store_true", help="não usa o cache de respostas")
    parser.add_argument("--provedor", choices=list(config.PROVEDORES_VALIDOS), default=None,
                        help="sobrescreve o PROVEDOR_LLM do .env")
    parser.add_argument("--modelo", default=None, help="força um modelo específico")
    args = parser.parse_args()

    if not banco_disponivel():
        sys.exit(f"Banco não encontrado em {config.DB_PATH}. Veja o README (passo 3).")

    agente = AgenteCineData(
        modelos=[args.modelo] if args.modelo else None,
        provedor=args.provedor,
        usar_cache=False if args.sem_cache else None,
    )
    id_conversa = agente.nova_conversa()

    if args.pergunta:
        responder(agente, " ".join(args.pergunta), id_conversa, args.mostrar_sql)
        return

    mostrar_sql = args.mostrar_sql
    print("=" * 64)
    print(" CineData Assistente - pergunte sobre o catálogo de filmes")
    print(f" provedor: {agente.llm.provedor} | modelos: {', '.join(agente.llm.modelos)}")
    print(f" busca semântica: {'ativa' if agente.busca_semantica_ativa else 'desativada (rode scripts/indexar_sinopses.py)'}")
    print(" digite /ajuda para ver os comandos")
    print("=" * 64)

    while True:
        try:
            pergunta = input("\nVocê> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAté mais!")
            break
        if not pergunta:
            continue

        comando = pergunta.lower()
        if comando in ("/sair", "/exit", "/q", "sair"):
            print("Até mais!")
            break
        if comando == "/ajuda":
            print(AJUDA)
        elif comando == "/nova":
            id_conversa = agente.nova_conversa()
            print("Conversa nova iniciada (memória limpa).")
        elif comando == "/sql":
            mostrar_sql = not mostrar_sql
            print(f"Exibição de SQL: {'ligada' if mostrar_sql else 'desligada'}")
        elif comando == "/cota":
            print("Cota OpenRouter:", resumo_cota(consultar_cota()))
        elif comando == "/exemplos":
            print("\n".join(f"  - {e}" for e in EXEMPLOS))
        else:
            responder(agente, pergunta, id_conversa, mostrar_sql)


if __name__ == "__main__":
    main()
