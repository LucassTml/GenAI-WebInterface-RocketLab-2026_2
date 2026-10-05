"""Testes do comparador da avaliação (não usam LLM nem banco)."""

from avaliacao.comparador import acerto_chave, acerto_valor, avaliar, numeros_proximos
from cinedata_agent.agente import RespostaAgente

GABARITO = {
    "colunas": ["titulo", "ano", "receita_brl"],
    "linhas": [["Avatar: The Way Of Water", 2022, 12390136500.54], ["Avengers: Endgame", 2019, 11094720000.0]],
}
ITEM = {"id": "x", "modo": "chave+valor", "chave": "titulo", "valor": "receita_brl"}


def test_chave_em_coluna_com_outro_nome_e_caixa_diferente():
    nota = acerto_chave(["Avatar: The Way Of Water", "Avengers: Endgame"], ["filme"], [["avatar: the way of water"], ["Barbie"]])
    assert nota == 0.5


def test_valor_com_escala_diferente():
    # agente devolveu em bilhões e arredondado
    assert numeros_proximos(12390136500.54, 12.39)
    # margem como fração em vez de porcentagem
    assert numeros_proximos(75.92, 0.7592)
    assert not numeros_proximos(75.92, 60.0)


def test_valor_com_empates_precisa_de_todas_as_ocorrencias():
    assert acerto_valor([10, 10, 10], [["a", 10], ["b", 10]]) == 2 / 3


def test_avaliar_resposta_certa():
    consulta = {"colunas": ["titulo", "receita"], "linhas": [r[::2] for r in GABARITO["linhas"]], "erro": None}
    resp = RespostaAgente(texto="O maior é Avatar: The Way Of Water", consultas=[consulta])
    resultado = avaliar(ITEM, resp, [GABARITO])
    assert resultado["aprovado"] and resultado["cita_top1"]


def test_avaliar_resposta_errada():
    consulta = {"colunas": ["titulo", "receita"], "linhas": [["Barbie", 1.0]], "erro": None}
    resp = RespostaAgente(texto="Barbie", consultas=[consulta])
    assert not avaliar(ITEM, resp, [GABARITO])["aprovado"]


def test_consulta_com_erro_nao_conta():
    consulta = {"colunas": ["titulo", "receita"], "linhas": [r[::2] for r in GABARITO["linhas"]], "erro": "algo"}
    resp = RespostaAgente(texto="", consultas=[consulta])
    assert avaliar(ITEM, resp, [GABARITO])["nota"] == 0


def test_modos_especiais():
    assert avaliar({"modo": "bloqueado"}, RespostaAgente(texto="não", bloqueado=True), [])["aprovado"]
    assert avaliar({"modo": "sem_consulta"}, RespostaAgente(texto="Só sei de filmes"), [])["aprovado"]
    consulta = {"colunas": ["titulo"], "linhas": [["The Meg"]], "erro": None}
    item = {"modo": "contem_titulo", "esperado": ["shark", "meg"]}
    assert avaliar(item, RespostaAgente(texto="...", consultas=[consulta]), [])["aprovado"]
