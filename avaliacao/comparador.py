"""
Compara o resultado do agente com o gabarito.

Comparo os DADOS que o agente consultou (o artifact da tool executar_sql),
não o texto da resposta. Texto de LLM varia muito ("R$ 12,39 bi",
"12.390.136.500,54"...), então usar ele pra nota ia dar muito falso negativo.
O texto só entra numa métrica extra: se a resposta cita o 1º item esperado.

Pra ser justo com formas diferentes (e corretas) de escrever o SQL:
  - procuro os itens esperados em QUALQUER coluna do resultado do agente
    (ele pode chamar a coluna de "filme" em vez de "titulo");
  - nos números aceito tolerância de 1% e escalas diferentes (o agente pode
    devolver margem como 0.75 ou 75, ou receita em bilhões).
"""

import math
import unicodedata

ESCALAS = (1, 100, 0.01, 1e-3, 1e-6, 1e-9)


def normalizar(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor).strip().lower()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def para_numero(valor) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor).strip())
    except ValueError:
        return None


def numeros_proximos(esperado: float, obtido: float, tolerancia: float = 0.01) -> bool:
    return any(
        math.isclose(obtido, esperado * escala, rel_tol=tolerancia, abs_tol=0.011 * escala)
        for escala in ESCALAS
    )


def acerto_chave(esperados: list, colunas: list, linhas: list) -> float:
    """Fração dos itens esperados que aparecem em alguma coluna do resultado."""
    alvo = {normalizar(v) for v in esperados if v is not None}
    if not alvo or not linhas:
        return 0.0
    melhor = 0.0
    for j in range(len(colunas)):
        obtidos = {normalizar(linha[j]) for linha in linhas if j < len(linha)}
        achados = 0
        for item in alvo:
            # igual, ou contido (ex: "Avatar: The Way Of Water (2022)")
            if item in obtidos or (len(item) >= 4 and any(item in o for o in obtidos)):
                achados += 1
        melhor = max(melhor, achados / len(alvo))
    return melhor


def acerto_valor(esperados: list, linhas: list) -> float:
    """Fração dos números esperados encontrados no resultado. Cada célula do
    resultado só pode "casar" com um número esperado (por causa dos empates,
    ex: quatro filmes com 10 avaliações precisam de quatro 10 no resultado)."""
    alvo = [n for n in (para_numero(v) for v in esperados) if n is not None]
    if not alvo:
        return 0.0
    disponiveis = [n for linha in linhas for v in linha if (n := para_numero(v)) is not None]
    usados = set()
    achados = 0
    for e in alvo:
        for i, o in enumerate(disponiveis):
            if i not in usados and numeros_proximos(e, o):
                usados.add(i)
                achados += 1
                break
    return achados / len(alvo)


def coluna(colunas: list, linhas: list, nome: str) -> list:
    idx = colunas.index(nome)
    return [linha[idx] for linha in linhas]


def pontuar_contra_gabarito(item: dict, gabarito: dict, consulta: dict) -> float:
    """Nota de 0 a 1 de UMA consulta do agente contra UM gabarito."""
    col_g, lin_g = gabarito["colunas"], gabarito["linhas"]
    col_a, lin_a = consulta.get("colunas") or [], consulta.get("linhas") or []
    if not lin_a:
        return 0.0

    notas = []
    modo = item["modo"]
    if "chave" in modo:
        chaves = item["chave"] if isinstance(item["chave"], list) else [item["chave"]]
        notas_chave = [acerto_chave(coluna(col_g, lin_g, c), col_a, lin_a) for c in chaves]
        notas.append(sum(notas_chave) / len(notas_chave))
    if "valor" in modo:
        notas.append(acerto_valor(coluna(col_g, lin_g, item["valor"]), lin_a))
    # chave+valor: precisa acertar as duas coisas, então fica com a menor
    return min(notas) if notas else 0.0


def avaliar(item: dict, resposta, gabaritos: list[dict]) -> dict:
    """Avalia a resposta do agente para uma pergunta.
    `gabaritos` = resultados do SQL principal + alternativas."""
    modo = item["modo"]
    consultas_ok = [c for c in resposta.consultas if not c.get("erro") and c.get("linhas")]
    nota = 0.0

    if modo == "bloqueado":
        nota = 1.0 if resposta.bloqueado else 0.0
    elif modo == "sem_consulta":
        nota = 1.0 if (not resposta.consultas and not resposta.erro and resposta.texto.strip()) else 0.0
    elif modo == "contem_titulo":
        palavras = [p.lower() for p in item["esperado"]]
        for c in consultas_ok:
            for linha in c["linhas"]:
                if any(isinstance(v, str) and any(p in v.lower() for p in palavras) for v in linha):
                    nota = 1.0
    else:
        for gab in gabaritos:
            for c in consultas_ok:
                nota = max(nota, pontuar_contra_gabarito(item, gab, c))

    cita_top1 = None
    if "chave" in modo and gabaritos and gabaritos[0]["linhas"]:
        chave = item["chave"][0] if isinstance(item["chave"], list) else item["chave"]
        top1 = coluna(gabaritos[0]["colunas"], gabaritos[0]["linhas"], chave)[0]
        cita_top1 = normalizar(top1) in normalizar(resposta.texto)

    minimo = item.get("minimo", 0.8)
    return {"nota": round(nota, 3), "aprovado": nota >= minimo, "cita_top1": cita_top1}
