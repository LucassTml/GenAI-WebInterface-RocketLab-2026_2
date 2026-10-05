"""
Ferramentas (tools) que o LLM pode chamar.

Usei response_format="content_and_artifact" nas duas tools. Assim cada tool
devolve duas coisas:
  - content: texto curto (tabela markdown) que vai pro LLM ler;
  - artifact: o resultado completo (sql, colunas, linhas) que NÃO vai pro
    LLM, mas fica guardado na ToolMessage. A interface usa isso pra mostrar a
    tabela inteira, o SQL e o gráfico sem gastar token nenhum.
"""

import unicodedata

from langchain_core.tools import tool
from tabulate import tabulate

from . import config
from .banco import conectar, dica_para_erro, executar_consulta
from .busca_semantica import IndiceIndisponivel, IndiceSinopses

TAMANHO_MAX_TEXTO = 160  # corta sinopse/textos longos antes de mandar pro LLM


def _encurtar(valor):
    if isinstance(valor, str) and len(valor) > TAMANHO_MAX_TEXTO:
        return valor[: TAMANHO_MAX_TEXTO - 3] + "..."
    return valor


def formatar_tabela(colunas: list[str], linhas: list) -> str:
    linhas_curtas = [[_encurtar(v) for v in linha] for linha in linhas]
    # disable_numparse evita o tabulate transformar 12390136500.54 em notação científica
    return tabulate(linhas_curtas, headers=colunas, tablefmt="github", disable_numparse=True)


@tool(response_format="content_and_artifact")
def executar_sql(consulta: str) -> tuple[str, dict]:
    """Executa UMA consulta SQL de leitura (SELECT ou WITH, dialeto SQLite) na camada Gold do
    CineData e devolve o resultado em formato de tabela.

    Args:
        consulta: o comando SQL completo. Use LIMIT em listagens.
    """
    resultado = executar_consulta(consulta)
    artefato = resultado.para_dict()
    artefato["ferramenta"] = "executar_sql"

    if not resultado.ok:
        texto = f"ERRO ao executar a consulta: {resultado.erro}"
        dica = dica_para_erro(resultado.erro)
        if dica:
            texto += f"\n{dica}"
        return texto, artefato

    if resultado.total_linhas == 0:
        return "A consulta rodou sem erro, mas não retornou nenhuma linha.", artefato

    mostrar = resultado.linhas[: config.MAX_LINHAS_PARA_LLM]
    texto = formatar_tabela(resultado.colunas, mostrar)
    if resultado.total_linhas > len(mostrar) or resultado.truncado:
        total = f"mais de {resultado.total_linhas}" if resultado.truncado else str(resultado.total_linhas)
        texto += f"\n\n(mostrando {len(mostrar)} de {total} linhas)"
    return texto, artefato


# o LLM às vezes manda o gênero em português, então traduzo pros nomes do banco
_GENEROS_PT = {
    "acao": "Action", "aventura": "Adventure", "animacao": "Animation", "comedia": "Comedy",
    "crime": "Crime", "policial": "Crime", "documentario": "Documentary", "drama": "Drama",
    "familia": "Family", "fantasia": "Fantasy", "historia": "History", "historico": "History",
    "terror": "Horror", "horror": "Horror", "musica": "Music", "musical": "Music",
    "misterio": "Mystery", "romance": "Romance", "ficcao cientifica": "Science Fiction",
    "suspense": "Thriller", "thriller": "Thriller", "filme de tv": "Tv Movie",
    "guerra": "War", "faroeste": "Western", "western": "Western",
}


def _traduzir_genero(genero: str) -> str:
    chave = "".join(
        c for c in unicodedata.normalize("NFKD", genero.strip().lower()) if not unicodedata.combining(c)
    )
    return _GENEROS_PT.get(chave, genero.strip())


def _ids_que_passam_no_filtro(genero, ano_inicial, ano_final, nota_imdb_minima) -> set[str] | None:
    """Aplica os filtros estruturados via SQL e devolve os sk_movie_id que
    passam. Retorna None se não tiver filtro nenhum (= todos permitidos)."""
    condicoes, params, joins = [], [], []
    if genero:
        joins.append(
            "JOIN bridge_movie_genre bg ON bg.sk_movie_id = m.sk_movie_id "
            "JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id"
        )
        condicoes.append("g.nome_genero = ? COLLATE NOCASE")
        params.append(_traduzir_genero(genero))
    if ano_inicial:
        condicoes.append("m.ano_lancamento >= ?")
        params.append(int(ano_inicial))
    if ano_final:
        condicoes.append("m.ano_lancamento <= ?")
        params.append(int(ano_final))
    if nota_imdb_minima is not None:
        joins.append("JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id")
        condicoes.append("f.nota_imdb >= ?")
        params.append(float(nota_imdb_minima))

    if not condicoes:
        return None

    sql = f"SELECT DISTINCT m.sk_movie_id FROM dim_movies m {' '.join(joins)} WHERE {' AND '.join(condicoes)}"
    conn = conectar()
    try:
        return {linha[0] for linha in conn.execute(sql, params)}
    finally:
        conn.close()


@tool(response_format="content_and_artifact")
def buscar_filmes_por_sinopse(
    descricao: str,
    quantidade: int = 10,
    genero: str | None = None,
    ano_inicial: int | None = None,
    ano_final: int | None = None,
    nota_imdb_minima: float | None = None,
) -> tuple[str, dict]:
    """Busca semântica: encontra filmes cuja SINOPSE fala sobre um tema/enredo, mesmo que use
    outras palavras (ex.: "tubarão gigante atacando pessoas", "viagem no tempo", "assalto a banco").
    Pode combinar com filtros estruturados. Use para perguntas sobre o conteúdo dos filmes.

    Args:
        descricao: o tema ou enredo procurado (de preferência em inglês, idioma das sinopses).
        quantidade: quantos filmes retornar (1 a 30).
        genero: filtro opcional de gênero (ex.: "Horror", "Comedy").
        ano_inicial: filtro opcional, ano de lançamento mínimo.
        ano_final: filtro opcional, ano de lançamento máximo.
        nota_imdb_minima: filtro opcional, nota IMDb mínima (0 a 10).
    """
    artefato = {"ferramenta": "buscar_filmes_por_sinopse", "sql": f"[busca semântica] {descricao}",
                "colunas": [], "linhas": [], "total_linhas": 0, "truncado": False, "erro": None}
    quantidade = max(1, min(int(quantidade or 10), 30))

    try:
        indice = IndiceSinopses.obter()
    except IndiceIndisponivel as e:
        artefato["erro"] = str(e)
        return f"ERRO: {e}", artefato

    permitidos = _ids_que_passam_no_filtro(genero, ano_inicial, ano_final, nota_imdb_minima)
    if permitidos is not None and not permitidos:
        return "Nenhum filme passa nesses filtros (gênero/ano/nota).", artefato

    achados = indice.buscar(descricao, k=quantidade, ids_permitidos=permitidos)
    if not achados:
        return "Nenhum filme encontrado para essa descrição.", artefato

    # enriquece com dados da camada Gold (aqui é a parte SQL do híbrido)
    ids = [a[0] for a in achados]
    marcadores = ",".join("?" * len(ids))
    sql = f"""
        SELECT m.sk_movie_id, m.titulo, m.ano_lancamento,
               (SELECT group_concat(g.nome_genero, ', ')
                  FROM bridge_movie_genre bg JOIN dim_genres g ON g.sk_genre_id = bg.sk_genre_id
                 WHERE bg.sk_movie_id = m.sk_movie_id) AS generos,
               f.nota_imdb, ROUND(f.popularidade, 2) AS popularidade, m.sinopse
        FROM dim_movies m
        LEFT JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
        WHERE m.sk_movie_id IN ({marcadores})
    """
    conn = conectar()
    try:
        info = {linha[0]: linha[1:] for linha in conn.execute(sql, ids)}
    finally:
        conn.close()

    colunas = ["titulo", "ano", "generos", "nota_imdb", "popularidade", "similaridade", "sinopse"]
    linhas = []
    for sk, score in achados:
        if sk in info:
            titulo, ano, generos, nota, pop, sinopse = info[sk]
            linhas.append([titulo, ano, generos, nota, pop, round(score, 3), sinopse])

    artefato.update({"colunas": colunas, "linhas": linhas, "total_linhas": len(linhas)})
    return formatar_tabela(colunas, linhas), artefato


TODAS_FERRAMENTAS = [executar_sql, buscar_filmes_por_sinopse]
