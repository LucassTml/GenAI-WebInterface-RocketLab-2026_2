"""
Cache de respostas em SQLite.

Motivo: cota de 50 requisições/dia. Se alguém faz a mesma pergunta duas
vezes (o que acontece muito durante teste e na avaliação), não faz sentido
gastar mais 2-3 requisições pra chegar na mesma resposta.

Cuidado que eu tive: com memória de conversa, a pergunta "e em 2020?" depende
do que foi perguntado antes. Por isso o cache só é usado na PRIMEIRA pergunta
de uma conversa (sem histórico). Pergunta de acompanhamento sempre vai pro LLM.

A chave é a pergunta normalizada (minúscula, sem acento, sem pontuação e sem
espaço duplicado), então "Top 10 filmes com maior receita?" e
"top 10 filmes com maior receita" caem na mesma entrada.
"""

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from . import config


def normalizar_pergunta(pergunta: str) -> str:
    texto = unicodedata.normalize("NFKD", pergunta.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s%$-]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _chave(pergunta: str) -> str:
    return hashlib.sha256(normalizar_pergunta(pergunta).encode()).hexdigest()


class CacheRespostas:
    def __init__(self, caminho: Path | None = None, validade_horas: int | None = None):
        self.caminho = Path(caminho or config.CACHE_PATH)
        self.validade_seg = (validade_horas or config.CACHE_VALIDADE_HORAS) * 3600
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS respostas (
                       chave TEXT PRIMARY KEY,
                       pergunta TEXT NOT NULL,
                       resposta TEXT NOT NULL,
                       consultas TEXT NOT NULL,
                       modelo TEXT,
                       criado_em REAL NOT NULL
                   )"""
            )

    @contextmanager
    def _conectar(self):
        # esse é um banco separado só pro cache (o da camada Gold é read-only).
        # O "with conn" do sqlite3 só faz commit, não fecha a conexão, por isso
        # o close() explícito (no Windows arquivo aberto não pode ser apagado).
        conn = sqlite3.connect(self.caminho, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def buscar(self, pergunta: str) -> dict | None:
        with self._conectar() as conn:
            linha = conn.execute(
                "SELECT resposta, consultas, modelo, criado_em FROM respostas WHERE chave = ?",
                (_chave(pergunta),),
            ).fetchone()
        if not linha:
            return None
        resposta, consultas, modelo, criado_em = linha
        if time.time() - criado_em > self.validade_seg:
            return None  # venceu, deixa o agente responder de novo
        return {"resposta": resposta, "consultas": json.loads(consultas), "modelo": modelo, "criado_em": criado_em}

    def salvar(self, pergunta: str, resposta: str, consultas: list[dict], modelo: str | None):
        with self._conectar() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO respostas VALUES (?, ?, ?, ?, ?, ?)",
                (_chave(pergunta), pergunta, resposta, json.dumps(consultas, default=str), modelo, time.time()),
            )

    def limpar(self) -> int:
        with self._conectar() as conn:
            return conn.execute("DELETE FROM respostas").rowcount

    def tamanho(self) -> int:
        with self._conectar() as conn:
            return conn.execute("SELECT COUNT(*) FROM respostas").fetchone()[0]
