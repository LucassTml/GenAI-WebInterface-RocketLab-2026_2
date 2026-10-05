"""
Atalho único pra rodar o projeto sem precisar ativar o .venv nem estar na
pasta certa. É chamado pelo cinedata.cmd (Windows), que já usa o Python do
.venv sozinho.

    cinedata web                         interface web (provedor do .env)
    cinedata web --provedor agy          interface web já com outro provedor
    cinedata chat                        chat no terminal
    cinedata chat --provedor claude-code
    cinedata chat "Quais são os 5 filmes mais populares?"
    cinedata provedores                  lista provedores e status
    cinedata testar --provedor google    testa tool calling (1 chamada por modelo)
    cinedata ollama                      inicia o Ollama local
    cinedata cota                        cota do OpenRouter
    cinedata avaliar --estimar           avaliação automática
    cinedata testes                      roda o pytest
"""

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PY = sys.executable

COMANDOS = {
    "web": ([PY, "-m", "streamlit", "run", "app.py"], "interface web (http://localhost:8501)"),
    "chat": ([PY, "main.py", "--mostrar-sql"], "chat no terminal (aceita --provedor, --modelo e uma pergunta)"),
    "provedores": ([PY, "main.py", "--listar-provedores"], "lista os 11 provedores e o status de cada um"),
    "testar": ([PY, "scripts/testar_provedores.py"], "testa os modelos (aceita --provedor X, --listar)"),
    "ollama": (["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/iniciar_ollama.ps1"],
               "liga o Ollama na mão (fica ligado até você parar)"),
    "cota": ([PY, "scripts/verificar_cota.py"], "cota restante do OpenRouter (não gasta cota)"),
    "modelos-free": ([PY, "scripts/listar_modelos_free.py"], "modelos gratuitos do OpenRouter com tool calling"),
    "avaliar": ([PY, "avaliacao/avaliar.py"], "avaliação automática (aceita --estimar, --provedor, --ids...)"),
    "gabarito": ([PY, "avaliacao/gerar_gabarito.py"], "gera avaliacao/gabarito.md"),
    "indexar": ([PY, "scripts/indexar_sinopses.py"], "gera o índice da busca semântica (~50 min)"),
    "testes": ([PY, "-m", "pytest", "-q"], "roda os testes automatizados"),
}


def ajuda():
    print("Uso: cinedata <comando> [opções]\n")
    for nome, (_, descricao) in COMANDOS.items():
        print(f"  {nome:<13} {descricao}")
    print("\nExemplos:\n  cinedata web --provedor agy\n  cinedata chat --provedor google\n"
          "  cinedata chat \"Quais são os 5 filmes mais populares?\"")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "ajuda", "help"):
        ajuda()
        return 0
    comando, resto = args[0], args[1:]
    if comando not in COMANDOS:
        print(f"Comando '{comando}' não existe.\n")
        ajuda()
        return 1

    ambiente = dict(os.environ)
    if comando == "web" and "--provedor" in resto:
        # o streamlit não repassa argumentos pro app, então vai por variável de ambiente
        i = resto.index("--provedor")
        if i + 1 < len(resto):
            ambiente["PROVEDOR_LLM"] = resto[i + 1]
            resto = resto[:i] + resto[i + 2:]

    cmd = COMANDOS[comando][0] + resto
    try:
        return subprocess.call(cmd, cwd=RAIZ, env=ambiente)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
