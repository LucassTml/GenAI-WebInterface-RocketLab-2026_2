import sqlite3

import pytest

from cinedata_agent.banco import conectar, executar_consulta
from conftest import requer_banco

pytestmark = requer_banco


def test_consulta_simples():
    r = executar_consulta("SELECT COUNT(*) AS qtd FROM dim_genres")
    assert r.ok
    assert r.colunas == ["qtd"]
    assert r.linhas[0][0] == 19


def test_erro_de_sql_volta_como_mensagem():
    r = executar_consulta("SELECT coluna_que_nao_existe FROM dim_movies")
    assert not r.ok
    assert "no such column" in r.erro


def test_guardrail_bloqueia_antes_de_chegar_no_banco():
    r = executar_consulta("DROP TABLE dim_movies")
    assert not r.ok
    assert "guardrail" in r.erro


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TEMP TABLE t (x INT)",
        "ATTACH DATABASE ':memory:' AS outro",
        "PRAGMA query_only = OFF",
        "DELETE FROM dim_genres",
    ],
)
def test_authorizer_bloqueia_mesmo_sem_guardrail(sql):
    # aqui eu chamo a conexão direto, pulando o validar_sql, pra garantir que
    # a segunda camada (authorizer + read-only) segura sozinha
    conn = conectar()
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    finally:
        conn.close()


def test_timeout_interrompe_consulta_pesada():
    # produto cartesiano gigante, não termina em 1 segundo nem a pau
    sql = "SELECT COUNT(*) FROM dim_people a, dim_people b"
    r = executar_consulta(sql, timeout=1)
    assert not r.ok
    assert "interrompida" in r.erro
    assert r.tempo_segundos < 10


def test_resultado_truncado():
    r = executar_consulta("SELECT titulo FROM dim_movies", max_linhas=20)
    assert r.ok
    assert r.total_linhas == 20
    assert r.truncado


def test_dica_quando_coluna_esta_em_outra_tabela():
    from cinedata_agent.banco import dica_para_erro

    dica = dica_para_erro("no such column: popularidade")
    assert "fact_movies_performance" in dica


def test_dica_quando_tabela_nao_existe():
    from cinedata_agent.banco import dica_para_erro

    dica = dica_para_erro("no such table: bridge_movie_reviews")
    assert "dim_reviews" in dica and "alembic_version" not in dica


def test_dica_com_nome_parecido():
    from cinedata_agent.banco import dica_para_erro

    assert "nota_imdb" in dica_para_erro("no such column: f.nota_imbd")


def test_dica_coluna_ambigua():
    from cinedata_agent.banco import dica_para_erro

    assert "m.sk_movie_id" in dica_para_erro("ambiguous column name: sk_movie_id")
