"""
Guardrails do agente.

São duas camadas:

1) verificar_pergunta(): roda ANTES de chamar o LLM. Barra pergunta vazia,
   gigante, tentativa de prompt injection e pedido para alterar o banco.
   Como não gasta requisição, é a forma mais barata de proteger a cota.

2) validar_sql(): roda antes de executar o SQL gerado pelo modelo. Só deixa
   passar UMA instrução de leitura (SELECT / WITH ... SELECT).

3) resposta_parece_valida(): roda na resposta final do LLM. Pega quando o
   modelo gratuito "vaza" o raciocínio ou degenera em texto sem sentido; aí
   o fallback tenta outro modelo (ver llm.py).

Mesmo que alguém dê um jeito de passar pelas regex daqui, o banco ainda é
aberto em modo somente leitura e com um authorizer do sqlite que nega
qualquer coisa que não seja leitura (ver banco.py). A ideia é ter várias
camadas, porque regex sozinha nunca é 100%.
"""

import re
import unicodedata

TAMANHO_MAXIMO_PERGUNTA = 1500


class SQLBloqueado(Exception):
    """SQL recusado pelo guardrail. A mensagem volta pro LLM corrigir."""


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


# --------------------------------------------------------------------------
# 1) Guardrail de entrada
# --------------------------------------------------------------------------

# frases típicas de prompt injection (em pt e en). Comparo sem acento e em
# minúsculo pra não precisar escrever todas as variações.
_PADROES_INJECTION = [
    r"ignor[ea]\w* (todas )?(as |suas )?(instruc|regras|orientac)",
    r"esquec[ea]\w* (todas )?(as |suas )?(instruc|regras)",
    r"ignore (all |the )?(previous|above|prior) (instructions|rules)",
    r"disregard (all |the )?(previous|above) ",
    r"(mostre|revele|exiba|imprima|repita)\w* (o |seu |teu )?(system prompt|prompt do sistema|prompt inicial|suas instrucoes)",
    r"system prompt",
    r"voce agora e (um|uma) ",
    r"a partir de agora voce (e|sera|vai ser)",
    r"modo (dan|desenvolvedor|developer)",
    r"\bjailbreak\b",
]

# pedidos para modificar dados. O agente é somente leitura, então nem vale a
# pena mandar isso pro modelo.
# Obs: deixei só verbos no imperativo/infinitivo e alvos "estruturais"
# (tabela, banco...). Na primeira versão eu bloqueava "excluindo registros" e
# isso pegava pergunta legítima tipo "excluindo os filmes sem receita, qual o
# lucro médio?". Falso positivo aqui é pior que falso negativo, porque o banco
# já é read-only de qualquer forma.
_VERBOS_ESCRITA = (
    r"(apague|apagar|delete|deletar|exclua|excluir|remova|remover|drope|dropar|"
    r"altere|alterar|atualize|atualizar|insira|inserir|modifique|modificar|"
    r"trunque|truncar|renomeie|renomear)"
)
_ALVOS_ESCRITA = r"(tabela|tabelas|banco|base de dados|schema|esquema|coluna|colunas|view|indice)"
_PADRAO_ESCRITA = re.compile(rf"\b{_VERBOS_ESCRITA}\b.{{0,40}}\b{_ALVOS_ESCRITA}\b")
# SQL de escrita colado direto na pergunta
_PADRAO_SQL_ESCRITA = re.compile(
    r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+(table|view|index)|alter\s+table|create\s+(table|view|index)|truncate\s+table|attach\s+database)\b"
)

RESPOSTA_INJECTION = (
    "Não posso seguir esse tipo de instrução. Eu sou o assistente de dados da "
    "CineData e só respondo perguntas sobre o catálogo de filmes (bilheteria, "
    "notas, elenco, gêneros, produtoras e avaliações). Como posso ajudar?"
)
RESPOSTA_ESCRITA = (
    "Eu tenho acesso **somente de leitura** à camada Gold, então não consigo "
    "alterar, inserir ou apagar dados. Posso te ajudar a *consultar* e analisar "
    "as informações do catálogo de filmes."
)


def verificar_pergunta(pergunta: str) -> tuple[bool, str | None]:
    """Retorna (ok, resposta_pronta). Se ok=False, a resposta pronta já vai
    direto pro usuário sem chamar o LLM."""
    texto = (pergunta or "").strip()
    if not texto:
        return False, "Pode mandar sua pergunta sobre o catálogo de filmes."
    if len(texto) > TAMANHO_MAXIMO_PERGUNTA:
        return False, (
            f"Sua pergunta ficou muito longa ({len(texto)} caracteres). "
            f"Tenta resumir em até {TAMANHO_MAXIMO_PERGUNTA} caracteres."
        )

    normalizado = _sem_acento(texto.lower())

    for padrao in _PADROES_INJECTION:
        if re.search(padrao, normalizado):
            return False, RESPOSTA_INJECTION

    if _PADRAO_ESCRITA.search(normalizado) or _PADRAO_SQL_ESCRITA.search(normalizado):
        return False, RESPOSTA_ESCRITA

    return True, None


# --------------------------------------------------------------------------
# 2) Guardrail de SQL
# --------------------------------------------------------------------------

# palavras que não podem aparecer em hipótese nenhuma (fora de strings).
# REPLACE ficou de fora de propósito porque replace() é uma função de texto
# válida num SELECT; o "REPLACE INTO" é tratado separado.
_PALAVRAS_PROIBIDAS = [
    "insert", "update", "delete", "drop", "alter", "create", "attach", "detach",
    "pragma", "vacuum", "reindex", "analyze", "truncate", "grant", "revoke",
    "begin", "commit", "rollback", "savepoint", "release", "load_extension",
]
_RE_PROIBIDAS = re.compile(r"\b(" + "|".join(_PALAVRAS_PROIBIDAS) + r")\b", re.IGNORECASE)
_RE_REPLACE_INTO = re.compile(r"\breplace\s+into\b", re.IGNORECASE)


def _remover_comentarios(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _remover_strings(sql: str) -> str:
    # troca o conteúdo de 'strings' e "identificadores" por vazio, pra não
    # bloquear um filme chamado "Drop Dead Gorgeous" por exemplo
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r'"(?:[^"]|"")*"', '""', sql)
    return sql


def validar_sql(sql: str) -> str:
    """Valida o SQL e devolve a versão limpa (sem ; no final).
    Levanta SQLBloqueado com uma mensagem explicando o motivo."""
    if not sql or not sql.strip():
        raise SQLBloqueado("A consulta veio vazia.")

    # às vezes o modelo manda o SQL dentro de ```sql ... ```
    limpo = re.sub(r"^```(?:sql)?|```$", "", sql.strip(), flags=re.IGNORECASE).strip()
    limpo = _remover_comentarios(limpo).strip()
    limpo = limpo.rstrip(";").strip()

    sem_strings = _remover_strings(limpo)

    if ";" in sem_strings:
        raise SQLBloqueado("Envie apenas UMA instrução SQL por vez (sem ';' no meio).")

    primeira_palavra = sem_strings.split(None, 1)[0].lower() if sem_strings.split() else ""
    if primeira_palavra not in ("select", "with"):
        raise SQLBloqueado(
            "Somente consultas de leitura são permitidas (a consulta deve começar com SELECT ou WITH)."
        )

    proibida = _RE_PROIBIDAS.search(sem_strings)
    if proibida:
        raise SQLBloqueado(
            f"A palavra-chave '{proibida.group(1).upper()}' não é permitida. O acesso é somente leitura."
        )
    if _RE_REPLACE_INTO.search(sem_strings):
        raise SQLBloqueado("REPLACE INTO não é permitido. O acesso é somente leitura.")

    # tabelas internas do sqlite não fazem parte do modelo dimensional
    if re.search(r"\bsqlite_\w+", sem_strings, re.IGNORECASE):
        raise SQLBloqueado("Consultas às tabelas internas do SQLite não são permitidas.")

    return limpo


# --------------------------------------------------------------------------
# 3) Guardrail de saída
# --------------------------------------------------------------------------
# Esse surgiu na avaliação com o OpenRouter: numa das respostas o modelo
# gratuito "vazou" o raciocínio interno em inglês ("The user asked: ...") e
# no final começou a gerar lixo (",y a.y,.Tur..."). Os DADOS estavam certos,
# mas o texto era inutilizável. Medi dois sinais nas 17 respostas reais:
#   - resposta começando com raciocínio em inglês: só a quebrada tinha;
#   - letras soltas sem sentido (y, t, g...): 49 na quebrada, no máximo 2
#     nas boas (ex.: o "m" de "5 m").
_RE_RACIOCINIO_VAZADO = re.compile(
    r"^\s*(the user|we need to|i need to|let me|let's|okay|ok,|so,? the|first,|we have)", re.IGNORECASE
)
# consoante sozinha (não pega "a", "e", "o", "é", nem o R de R$)
_RE_LETRA_SOLTA = re.compile(r"(?<![A-Za-zÀ-ÿ])[b-df-hj-np-tv-zB-DF-HJ-NP-TV-Z](?![A-Za-zÀ-ÿ$])")


def resposta_parece_valida(texto: str) -> bool:
    """False se o texto final do LLM parece raciocínio vazado ou lixo."""
    texto = (texto or "").strip()
    if not texto:
        return False
    if _RE_RACIOCINIO_VAZADO.match(texto):
        return False
    sem_codigo = re.sub(r"`[^`]*`", "", texto)  # nomes de coluna em `crase` não contam
    soltas = len(_RE_LETRA_SOLTA.findall(sem_codigo))
    palavras = len(re.findall(r"[A-Za-zÀ-ÿ]+", sem_codigo)) or 1
    return not (soltas >= 10 and soltas / palavras > 0.04)
