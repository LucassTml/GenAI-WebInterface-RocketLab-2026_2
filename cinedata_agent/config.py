"""
Configurações do projeto.

Tudo que pode mudar entre máquinas (chave da API, caminho do banco, lista de
modelos...) vem do arquivo .env. Assim não fica nada sensível no código e dá
pra trocar de modelo sem mexer em nenhum .py.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# pasta raiz do projeto (um nível acima deste arquivo)
RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, padrao))
    except ValueError:
        return padrao


def _env_bool(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "s", "yes")


def _caminho(nome: str, padrao: Path) -> Path:
    # aceita caminho relativo (em relação à raiz do projeto) ou absoluto
    valor = os.getenv(nome)
    if not valor:
        return padrao
    p = Path(valor)
    return p if p.is_absolute() else RAIZ / p


# ---------------------------------------------------------------- banco ---
PASTA_DADOS = RAIZ / "data"
DB_PATH = _caminho("CINEDATA_DB", PASTA_DADOS / "cinerocket.db")

# limite de tempo de uma consulta. Algumas consultas com a bridge de pessoas
# (745 mil linhas) levam de 15 a 50s no meu notebook, então deixei 90s.
TIMEOUT_SQL_SEGUNDOS = _env_int("TIMEOUT_SQL_SEGUNDOS", 90)

# quantas linhas do resultado vão para o LLM. Mandar mais que isso só gasta
# token e o modelo se perde. A interface mostra o resultado completo.
MAX_LINHAS_PARA_LLM = _env_int("MAX_LINHAS_PARA_LLM", 30)
MAX_LINHAS_RESULTADO = _env_int("MAX_LINHAS_RESULTADO", 500)

# ------------------------------------------------------------------ LLM ---
# Provedor padrão. A lista completa (OpenRouter, NVIDIA, Google, OpenCode Zen,
# Anthropic, OpenAI, Ollama e as CLIs agy/claude/codex/opencode), com as
# variáveis de chave e de modelos de cada um, fica em provedores.py.
# "auto" tenta os provedores de ORDEM_PROVEDORES_AUTO em sequência.
PROVEDOR_LLM = os.getenv("PROVEDOR_LLM", "openrouter").strip().lower()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

TEMPERATURA = float(os.getenv("TEMPERATURA", "0"))
TIMEOUT_LLM_SEGUNDOS = _env_int("TIMEOUT_LLM_SEGUNDOS", 90)

# Máximo de chamadas ao LLM por pergunta. Com 50 requisições/dia no plano
# grátis, não dá pra deixar o agente ficar num loop tentando consertar SQL.
# Na última chamada permitida o modelo recebe um aviso pra responder com o
# que já tem (ver agente.py).
MAX_CHAMADAS_LLM = _env_int("MAX_CHAMADAS_LLM", 5)

# quantas mensagens antigas da conversa vão junto (memória de curto prazo)
MAX_MENSAGENS_HISTORICO = _env_int("MAX_MENSAGENS_HISTORICO", 12)

# ---------------------------------------------------------------- cache ---
CACHE_ATIVO = _env_bool("CACHE_ATIVO", True)
CACHE_PATH = _caminho("CACHE_PATH", PASTA_DADOS / "cache_respostas.db")
CACHE_VALIDADE_HORAS = _env_int("CACHE_VALIDADE_HORAS", 72)

# ------------------------------------------------------ busca semântica ---
INDICE_SINOPSES_PATH = _caminho("INDICE_SINOPSES_PATH", PASTA_DADOS / "indice_sinopses.npz")
PASTA_MODELOS_EMBEDDING = PASTA_DADOS / "modelos"
MODELO_EMBEDDING = os.getenv(
    "MODELO_EMBEDDING", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
