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
# Provedores suportados (todos falam a API no formato da OpenAI):
#   openrouter -> modelos :free (padrão da atividade, 50 req/dia)
#   nvidia     -> API da NVIDIA (build.nvidia.com), precisa de NVIDIA_API_KEY
#   ollama     -> modelo rodando localmente, sem internet e sem cota
#   auto       -> tenta na ordem de ORDEM_PROVEDORES_AUTO; se a cota de um
#                 acabar ou ele cair, passa pro próximo
PROVEDORES_VALIDOS = ("openrouter", "nvidia", "ollama", "auto")
PROVEDOR_LLM = os.getenv("PROVEDOR_LLM", "openrouter").strip().lower()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Ordem de preferência dos modelos gratuitos. Se o primeiro der erro 429 de
# provider lotado, o agente tenta o próximo (fallback).
# Escolhi modelos :free que suportam tool calling (conferido em
# https://openrouter.ai/api/v1/models, campo supported_parameters).
MODELOS_PADRAO = (
    "nvidia/nemotron-3.5-lightning:free,"
    "qwen/qwen3.8-27b:free,"
    "google/gemma-4-31b-it:free,"
    "openrouter/free"
)
MODELOS_LLM = [m.strip() for m in os.getenv("MODELOS_LLM", MODELOS_PADRAO).split(",") if m.strip()]

# NVIDIA (https://build.nvidia.com -> "Get API Key", começa com nvapi-).
# Escolhi modelos da lista de https://integrate.api.nvidia.com/v1/models; o
# primeiro é da mesma família que acertou 17/17 na avaliação pelo OpenRouter.
# Pra conferir quais respondem com tool calling: python scripts/testar_provedores.py
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
MODELOS_NVIDIA_PADRAO = (
    "nvidia/nemotron-3.5-lightning-30b-a3b,"
    "nvidia/nemotron-3-super-120b-a12b,"
    "openai/gpt-oss-20b"
)
MODELOS_NVIDIA = [m.strip() for m in os.getenv("MODELOS_NVIDIA", MODELOS_NVIDIA_PADRAO).split(",") if m.strip()]

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# aceita mais de um modelo separado por vírgula (ex.: qwen2.5:3b,qwen3:4b)
OLLAMA_MODELO = os.getenv("OLLAMA_MODELO", "qwen2.5:3b")
MODELOS_OLLAMA = [m.strip() for m in OLLAMA_MODELO.split(",") if m.strip()]

# ordem usada quando PROVEDOR_LLM=auto
ORDEM_PROVEDORES_AUTO = [
    p.strip() for p in os.getenv("ORDEM_PROVEDORES_AUTO", "openrouter,nvidia,ollama").split(",") if p.strip()
]

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
