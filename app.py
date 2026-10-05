"""
Interface web (Streamlit) do agente CineData.

    streamlit run app.py

A interface não era obrigatória, mas ajuda muito a mostrar o agente pra quem
não é técnico: chat com memória, tabela com o resultado, SQL usado e gráfico
automático de cada consulta.
"""

import streamlit as st

from cinedata_agent import config
from cinedata_agent.agente import AgenteCineData
from cinedata_agent.banco import banco_disponivel
from cinedata_agent.graficos import para_dataframe, sugerir_grafico
from cinedata_agent.llm import consultar_cota, resumo_cota, status_provedores
from langgraph.checkpoint.memory import InMemorySaver

st.set_page_config(page_title="CineData Assistente", page_icon="🎬", layout="wide")

EXEMPLOS = {
    "Bilheteria e Finanças": [
        "Quais são os 10 filmes com maior receita em R$?",
        "Qual o lucro médio por gênero, considerando apenas filmes com receita informada?",
        "Quais filmes têm a maior margem de lucro, entre os que possuem receita e orçamento informados?",
    ],
    "Popularidade e Engajamento": [
        "Quais são os 5 filmes mais populares?",
        "Quais filmes têm a maior divergência entre a nota TMDB e a nota IMDb?",
        "Qual a nota média IMDb por ano de lançamento?",
    ],
    "Elenco e Equipe": [
        "Qual ator teve mais participações em filmes lançados nos últimos 5 anos?",
        "Quais diretores têm a maior nota média, com no mínimo 5 filmes?",
        "Qual a dupla ator-diretor que mais trabalhou junta?",
    ],
    "Gêneros e Produtoras": [
        "Qual a quantidade de filmes por gênero?",
        "Qual produtora tem o maior lucro total?",
        "Qual gênero tem a maior margem de lucro média?",
    ],
    "Avaliações dos Usuários": [
        "Quais são os filmes mais avaliados pelos usuários?",
        "Em quais filmes a nota média dos usuários mais diverge da nota IMDb?",
    ],
    "Busca por tema (semântica)": [
        "Quais filmes falam sobre viagem no tempo?",
        "Me indica filmes de terror sobre casas mal-assombradas lançados depois de 2020",
    ],
}


ROTULOS_PROVEDOR = {
    "auto": "Automático (OpenRouter → NVIDIA → Ollama)",
    "openrouter": "OpenRouter (modelos :free)",
    "nvidia": "NVIDIA (build.nvidia.com)",
    "ollama": "Ollama (modelo local)",
}


@st.cache_resource
def carregar_memoria() -> InMemorySaver:
    # uma memória só para todos os provedores: dá pra trocar de provedor no
    # meio da conversa sem perder o contexto
    return InMemorySaver()


@st.cache_resource(show_spinner="Carregando o agente...")
def carregar_agente(provedor: str) -> AgenteCineData:
    # cache_resource: cada agente é criado uma vez só e reaproveitado entre
    # as recargas da página
    return AgenteCineData(provedor=provedor, usar_cache=True, checkpointer=carregar_memoria())


def iniciar_sessao():
    if "mensagens" not in st.session_state:
        st.session_state.mensagens = []
    if "id_conversa" not in st.session_state:
        st.session_state.id_conversa = AgenteCineData.nova_conversa()
    if "pergunta_pendente" not in st.session_state:
        st.session_state.pergunta_pendente = None


def nova_conversa():
    st.session_state.mensagens = []
    st.session_state.id_conversa = AgenteCineData.nova_conversa()


def mostrar_consultas(consultas: list[dict], chave: str):
    validas = [c for c in consultas if c.get("linhas")]
    erros = [c for c in consultas if c.get("erro")]
    if not consultas:
        return
    rotulo = f"📊 Dados consultados ({len(validas)} consulta(s)"
    rotulo += f", {len(erros)} com erro corrigido)" if erros else ")"
    with st.expander(rotulo):
        for i, c in enumerate(consultas):
            if c.get("erro"):
                st.caption(f"Tentativa {i + 1} deu erro e o agente corrigiu: `{c['erro'][:150]}`")
                continue
            df = para_dataframe(c)
            aba_grafico, aba_tabela, aba_sql = st.tabs(["Gráfico", "Tabela", "SQL"])
            with aba_grafico:
                fig = sugerir_grafico(df)
                if fig is not None:
                    st.plotly_chart(fig, key=f"graf_{chave}_{i}")
                else:
                    st.caption("Sem gráfico para esse formato de resultado.")
            with aba_tabela:
                st.dataframe(df, hide_index=True)
                if c.get("truncado"):
                    st.caption(f"Mostrando as primeiras {len(df)} linhas.")
            with aba_sql:
                linguagem = "sql" if c.get("ferramenta") == "executar_sql" else "text"
                st.code(c.get("sql", ""), language=linguagem)


def mostrar_mensagem(msg: dict, indice: int):
    with st.chat_message(msg["papel"], avatar="🧑" if msg["papel"] == "user" else "🎬"):
        st.markdown(msg["texto"])
        if msg["papel"] == "assistant":
            mostrar_consultas(msg.get("consultas", []), chave=str(indice))
            meta = msg.get("meta")
            if meta:
                st.caption(meta)


# ------------------------------------------------------------------ página --
iniciar_sessao()

with st.sidebar:
    st.title("🎬 CineData")
    st.caption("Agente Text-to-SQL sobre a camada Gold")

    status = status_provedores()
    opcoes = list(ROTULOS_PROVEDOR)
    padrao = config.PROVEDOR_LLM if config.PROVEDOR_LLM in opcoes else "openrouter"

    def _rotulo(p):
        if p == "auto":
            return ROTULOS_PROVEDOR[p]
        return ("✅ " if status[p][0] else "⚪ ") + ROTULOS_PROVEDOR[p]

    provedor = st.selectbox("Provedor do LLM", opcoes, index=opcoes.index(padrao), format_func=_rotulo,
                            help="Dá pra trocar no meio da conversa; a memória é mantida.")
    if provedor != "auto" and not status[provedor][0]:
        st.warning(f"{ROTULOS_PROVEDOR[provedor]}: {status[provedor][1]}.")

    usar_cache = st.toggle("Usar cache de respostas", value=config.CACHE_ATIVO,
                           help="Pergunta repetida não gasta requisição do LLM")
    st.button("🗑️ Nova conversa", on_click=nova_conversa, width="stretch")

    st.subheader("Status")
    st.markdown(f"- Banco: {'✅' if banco_disponivel() else '❌ não encontrado'}")
    if banco_disponivel():
        agente = carregar_agente(provedor)
        st.markdown(f"- Busca semântica: {'✅ ativa' if agente.busca_semantica_ativa else '⚠️ índice não gerado'}")
        for p, (ok, motivo) in status.items():
            st.markdown(f"- {p}: {'✅' if ok else '⚪'} {motivo}")
        with st.expander("Modelos (ordem de fallback)"):
            for m in agente.llm.modelos or ["nenhum disponível"]:
                st.markdown(f"- `{m}`")
    if status["openrouter"][0] and st.button("Ver cota do OpenRouter", width="stretch"):
        st.info(resumo_cota(consultar_cota()))

    st.subheader("Perguntas de exemplo")
    for categoria, perguntas in EXEMPLOS.items():
        with st.expander(categoria):
            for p in perguntas:
                if st.button(p, key=f"ex_{p}", width="stretch"):
                    st.session_state.pergunta_pendente = p

st.title("CineData Assistente")
st.caption(
    "Pergunte em português sobre bilheteria, notas, elenco, gêneros, produtoras e avaliações. "
    "O agente escreve o SQL, consulta a camada Gold e explica o resultado."
)

if not banco_disponivel():
    st.error(f"Banco não encontrado em `{config.DB_PATH}`. Coloque o `cinerocket.db` na pasta `data/`.")
    st.stop()

agente = carregar_agente(provedor)

for i, msg in enumerate(st.session_state.mensagens):
    mostrar_mensagem(msg, i)

pergunta = st.chat_input("Ex.: Quais são os 10 filmes com maior receita em R$?")
if st.session_state.pergunta_pendente:
    pergunta = st.session_state.pergunta_pendente
    st.session_state.pergunta_pendente = None

if pergunta:
    st.session_state.mensagens.append({"papel": "user", "texto": pergunta})
    mostrar_mensagem(st.session_state.mensagens[-1], len(st.session_state.mensagens) - 1)

    with st.spinner("Consultando a camada Gold..."):
        resp = agente.perguntar(pergunta, st.session_state.id_conversa, usar_cache=usar_cache)

    if resp.bloqueado:
        origem = "🛡️ guardrail"
    elif resp.do_cache:
        origem = "💾 cache"
    else:
        origem = f"🤖 {resp.modelo or '-'}"
    meta = f"{origem} · {resp.requisicoes} requisição(ões) · {resp.tempo_segundos:.1f}s"
    if resp.erro:
        meta = "⚠️ " + meta
    st.session_state.mensagens.append(
        {"papel": "assistant", "texto": resp.texto, "consultas": resp.consultas, "meta": meta}
    )
    st.rerun()
