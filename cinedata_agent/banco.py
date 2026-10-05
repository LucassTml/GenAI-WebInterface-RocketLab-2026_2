"""
Acesso ao banco SQLite (camada Gold do CineData).

Decisões importantes:
- O banco é aberto com a URI "mode=ro" (read-only). Mesmo que um SQL de
  escrita passe pelo guardrail, o sqlite recusa.
- Além disso coloquei um authorizer: é uma função que o sqlite chama para
  cada operação ao compilar o SQL. Só libero SELECT, leitura de coluna,
  funções e CTE recursiva. Qualquer outra coisa (PRAGMA, ATTACH, INSERT...)
  é negada na hora.
- Timeout: o sqlite não tem timeout de consulta nativo, então uso o
  progress_handler, que é chamado a cada N instruções da VM do sqlite. Se
  passou do tempo, ele retorna 1 e a consulta é interrompida.
- Abro uma conexão nova por consulta. Pro sqlite isso é barato e evita dor
  de cabeça com threads (o Streamlit roda cada sessão numa thread).
"""

import difflib
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .guardrails import validar_sql


class BancoNaoEncontrado(FileNotFoundError):
    pass


@dataclass
class ResultadoConsulta:
    sql: str
    colunas: list[str] = field(default_factory=list)
    linhas: list[tuple] = field(default_factory=list)
    total_linhas: int = 0          # quantas linhas a consulta retornou (até o limite de leitura)
    truncado: bool = False         # True se tinha mais linhas que MAX_LINHAS_RESULTADO
    tempo_segundos: float = 0.0
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.erro is None

    def para_dict(self) -> dict:
        # formato serializável que vai como "artifact" da tool (não vai pro LLM)
        return {
            "sql": self.sql,
            "colunas": self.colunas,
            "linhas": [list(linha) for linha in self.linhas],
            "total_linhas": self.total_linhas,
            "truncado": self.truncado,
            "tempo_segundos": round(self.tempo_segundos, 3),
            "erro": self.erro,
        }


# operações que o authorizer deixa passar
_ACOES_PERMITIDAS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}


def _authorizer(acao, arg1, arg2, nome_db, origem):
    if acao in _ACOES_PERMITIDAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def conectar(db_path: Path | None = None) -> sqlite3.Connection:
    caminho = Path(db_path or config.DB_PATH)
    if not caminho.exists():
        raise BancoNaoEncontrado(
            f"Banco não encontrado em '{caminho}'. Coloque o arquivo cinerocket.db na "
            f"pasta data/ (ou ajuste CINEDATA_DB no .env)."
        )
    # as_posix pra URI funcionar no Windows também
    uri = f"file:{caminho.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

    # Ajustes de performance ANTES de ligar o authorizer (depois ele bloqueia
    # PRAGMA). O cache padrão do sqlite é só 2 MB; com cache maior + tabelas
    # temporárias em memória, a consulta da dupla ator-diretor caiu de 1-2,5
    # min para ~17s no meu notebook (medido em notebooks/exploracao_dados.ipynb).
    conn.execute("PRAGMA cache_size = -131072")   # ~128 MB
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")  # 256 MB
    conn.execute("PRAGMA query_only = ON")

    conn.set_authorizer(_authorizer)
    return conn


def executar_consulta(
    sql: str,
    max_linhas: int | None = None,
    timeout: int | None = None,
    db_path: Path | None = None,
) -> ResultadoConsulta:
    """Valida e executa um SELECT. Nunca levanta exceção de SQL: o erro volta
    dentro do ResultadoConsulta, pra tool conseguir devolver a mensagem pro
    LLM e ele tentar corrigir a consulta."""
    max_linhas = max_linhas or config.MAX_LINHAS_RESULTADO
    timeout = timeout or config.TIMEOUT_SQL_SEGUNDOS
    resultado = ResultadoConsulta(sql=sql)

    try:
        sql_limpo = validar_sql(sql)
    except Exception as e:  # SQLBloqueado
        resultado.erro = f"Consulta bloqueada pelo guardrail: {e}"
        return resultado
    resultado.sql = sql_limpo

    inicio = time.perf_counter()
    conn = conectar(db_path)

    def _checar_tempo():
        # retornar algo "verdadeiro" interrompe a consulta
        return 1 if time.perf_counter() - inicio > timeout else 0

    conn.set_progress_handler(_checar_tempo, 20_000)
    try:
        cur = conn.execute(sql_limpo)
        resultado.colunas = [d[0] for d in (cur.description or [])]
        # leio uma linha a mais só pra saber se truncou
        linhas = cur.fetchmany(max_linhas + 1)
        resultado.truncado = len(linhas) > max_linhas
        resultado.linhas = linhas[:max_linhas]
        resultado.total_linhas = len(resultado.linhas)
    except sqlite3.OperationalError as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = (
                f"A consulta passou do limite de {timeout}s e foi interrompida. "
                "Tente filtrar antes de fazer os JOINs ou agregar em uma CTE."
            )
        elif "not authorized" in msg:
            msg = "Operação não autorizada: o acesso ao banco é somente leitura."
        resultado.erro = msg
    except sqlite3.Error as e:
        resultado.erro = f"{type(e).__name__}: {e}"
    finally:
        resultado.tempo_segundos = time.perf_counter() - inicio
        conn.close()

    return resultado


def banco_disponivel(db_path: Path | None = None) -> bool:
    return Path(db_path or config.DB_PATH).exists()


_cache_esquema: dict[str, list[str]] | None = None


def esquema_do_banco() -> dict[str, list[str]]:
    """{tabela: [colunas]} lido do próprio banco. Uso isso pra montar dicas
    quando o LLM erra nome de coluna/tabela. Aqui uso uma conexão sem o
    authorizer porque o SQL é meu (PRAGMA), não do modelo."""
    global _cache_esquema
    if _cache_esquema is None:
        caminho = Path(config.DB_PATH).resolve().as_posix()
        conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        try:
            tabelas = [
                t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                if t != "alembic_version"  # tabela de controle de migração, não é dado
            ]
            _cache_esquema = {t: [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')] for t in tabelas}
        finally:
            conn.close()
    return _cache_esquema


def dica_para_erro(erro: str) -> str | None:
    """Transforma 'no such column: popularidade' em algo que ajuda o modelo
    a se corrigir sozinho na próxima tentativa (sem eu ter que gastar uma
    chamada extra com uma tool de 'descrever tabela')."""
    esquema = esquema_do_banco()
    m = re.search(r"no such column: (?:\w+\.)?(\w+)", erro)
    if m:
        coluna = m.group(1)
        onde = [t for t, cols in esquema.items() if coluna in cols]
        if onde:
            return f"Dica: a coluna '{coluna}' existe na(s) tabela(s): {', '.join(onde)}. Faça o JOIN com ela."
        parecidas = difflib.get_close_matches(coluna, [c for cols in esquema.values() for c in cols], n=3)
        if parecidas:
            return f"Dica: a coluna '{coluna}' não existe. Talvez você quis dizer: {', '.join(parecidas)}."
        return f"Dica: a coluna '{coluna}' não existe em nenhuma tabela."
    m = re.search(r"no such table: (\w+)", erro)
    if m:
        return f"Dica: a tabela '{m.group(1)}' não existe. Tabelas disponíveis: {', '.join(esquema)}."
    # esse apareceu testando com modelo pequeno: JOIN sem alias na coluna sk_movie_id
    m = re.search(r"ambiguous column name: (\w+)", erro)
    if m:
        return (
            f"Dica: a coluna '{m.group(1)}' existe em mais de uma tabela do JOIN. "
            f"Use apelidos nas tabelas e prefixe a coluna (ex.: m.{m.group(1)})."
        )
    return None
