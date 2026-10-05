"""
Gráfico automático a partir do resultado de uma consulta.

Pensei em criar uma tool "gerar_grafico" pro LLM chamar, mas isso seria
mais uma requisição por pergunta. Como o formato do resultado já diz muito
sobre o gráfico certo, fiz umas regras simples:

  - tem coluna de ano + coluna numérica  -> gráfico de linha (série no tempo)
  - tem coluna de texto + coluna numérica -> barras horizontais (ranking)
  - qualquer outra coisa (1 linha só, só texto, muitas linhas) -> sem gráfico

Custo zero de LLM e acerta na grande maioria das perguntas da atividade.
"""

import pandas as pd
import plotly.express as px

MAX_LINHAS_GRAFICO = 30
# colunas que são número mas não faz sentido plotar como medida
_COLUNAS_IGNORADAS = {"id", "id_filme", "sk_movie_id"}


def para_dataframe(consulta: dict) -> pd.DataFrame:
    return pd.DataFrame(consulta.get("linhas") or [], columns=consulta.get("colunas") or [])


def _eh_coluna_de_ano(nome: str, serie: pd.Series) -> bool:
    if "ano" not in nome.lower() and "year" not in nome.lower():
        return False
    valores = pd.to_numeric(serie, errors="coerce").dropna()
    return not valores.empty and valores.between(1900, 2100).all()


def sugerir_grafico(df: pd.DataFrame, titulo: str | None = None):
    """Retorna uma figura do plotly ou None se não fizer sentido plotar."""
    if df.empty or len(df) < 2 or len(df) > MAX_LINHAS_GRAFICO:
        return None

    numericas = [
        c for c in df.columns
        if c not in _COLUNAS_IGNORADAS and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    anos = [c for c in df.columns if _eh_coluna_de_ano(c, df[c])]
    # no pandas 3 texto virou dtype "str" (antes era object), então checo os dois
    textos = [
        c for c in df.columns
        if c not in _COLUNAS_IGNORADAS and c not in numericas
        and (pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object)
    ]

    # série temporal: ano no eixo x
    if anos:
        x = anos[0]
        medidas = [c for c in numericas if c != x]
        if medidas and not textos:
            dados = df.sort_values(x)
            fig = px.line(dados, x=x, y=medidas[0], markers=True, title=titulo)
            fig.update_xaxes(dtick=1)
            return _ajustar(fig)

    # ranking: primeira coluna de texto (nome/título) x primeira medida
    if textos and numericas:
        rotulo = textos[0]
        medidas = [c for c in numericas if c not in anos] or numericas
        dados = df.copy()
        # títulos repetidos (ex: "Die Hart 2") somariam no mesmo eixo; ponho o ano junto
        if dados[rotulo].duplicated().any() and anos:
            dados[rotulo] = dados[rotulo].astype(str) + " (" + dados[anos[0]].astype(str) + ")"
        if dados[rotulo].duplicated().any():
            dados[rotulo] = dados[rotulo].astype(str) + " #" + (dados.groupby(rotulo).cumcount() + 1).astype(str)
        # inverte pra o 1º do ranking ficar em cima
        fig = px.bar(dados.iloc[::-1], x=medidas[0], y=rotulo, orientation="h", title=titulo, text_auto=".3s")
        fig.update_layout(height=max(300, 28 * len(dados) + 120))
        return _ajustar(fig)

    return None


def _ajustar(fig):
    fig.update_layout(margin=dict(l=10, r=10, t=50 if fig.layout.title.text else 20, b=10))
    return fig
