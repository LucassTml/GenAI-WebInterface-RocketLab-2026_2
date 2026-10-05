"""
Tela "Modelos e chaves" da interface web.

Aqui dá pra fazer pela interface tudo que antes exigia editar o .env:
  - escolher o provedor padrão e a ordem do modo auto;
  - colar/trocar a chave de API de cada empresa;
  - escolher quais modelos usar (e consultar quais a conta enxerga);
  - testar um modelo com 1 chamada antes de usar no chat.
Tudo que é salvo aqui vai pro arquivo .env (via provedores.salvar_no_env),
então continua valendo na próxima vez que o programa abrir.
"""

import streamlit as st

from cinedata_agent import llm, provedores

DICA_LOGIN_CLI = {
    "agy": "Faça login uma vez rodando `agy` no terminal. Modelos: `agy models`.",
    "claude-code": "Faça login uma vez rodando `claude` no terminal (comando /login).",
    "codex": "Faça login uma vez com `codex login`.",
    "opencode-cli": "Configure com `opencode auth login` (ou o provedor que você já usa no OpenCode).",
}


def _salvar(variavel: str, valor: str, ao_salvar):
    provedores.salvar_no_env(variavel, valor)
    ao_salvar()  # limpa os agentes em cache pra pegar a configuração nova


def _bloco_provedor(pid: str, status: dict, ao_salvar):
    p = provedores.obter(pid)
    ok, motivo = status[pid]
    with st.expander(f"{'✅' if ok else '⚪'} {p.nome}  ·  `{pid}`", expanded=False):
        st.caption(p.descricao)
        st.markdown(f"**Status:** {motivo}")

        # ---- chave
        if p.var_chave:
            atual = provedores.chave(pid)
            st.markdown(
                f"**Chave** (`{p.var_chave}`): {'configurada `' + provedores.mascarar(atual) + '`' if atual else 'não configurada'}"
                + (f" · [gerar chave]({p.site_chave})" if p.site_chave else "")
            )
            col1, col2 = st.columns([4, 1])
            nova = col1.text_input("Nova chave", type="password", key=f"chave_{pid}", label_visibility="collapsed",
                                   placeholder="cole a chave aqui")
            if col2.button("Salvar", key=f"salvar_chave_{pid}", width="stretch", disabled=not nova.strip()):
                _salvar(p.var_chave, nova.strip(), ao_salvar)
                st.success("Chave salva no .env.")
                st.rerun()
        elif p.eh_cli:
            st.markdown(f"**Sem chave:** usa o login da própria CLI. {DICA_LOGIN_CLI.get(pid, '')}")
        else:
            st.markdown("**Sem chave:** roda local. Para iniciar: `powershell -ExecutionPolicy Bypass -File "
                        "scripts\\iniciar_ollama.ps1`")

        # ---- modelos
        st.markdown(f"**Modelos** (`{p.var_modelos}`, em ordem de fallback, separados por vírgula)")
        texto = st.text_input("Modelos", value=", ".join(provedores.modelos(pid)), key=f"modelos_{pid}",
                              label_visibility="collapsed")
        c1, c2, c3 = st.columns(3)
        if c1.button("Salvar modelos", key=f"salvar_modelos_{pid}", width="stretch"):
            limpos = ",".join(m.strip() for m in texto.split(",") if m.strip())
            _salvar(p.var_modelos, limpos, ao_salvar)
            st.success("Modelos salvos no .env.")
        if c2.button("Listar modelos da conta", key=f"listar_{pid}", width="stretch", disabled=not ok):
            try:
                lista = llm.listar_modelos_disponiveis(pid)
                if lista:
                    st.session_state[f"lista_{pid}"] = lista
                else:
                    st.info("Essa CLI não lista modelos. Use 'padrao' ou digite o nome do modelo.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Não consegui listar: {e}")
        modelo_teste = provedores.modelos(pid)[0]
        if c3.button(f"Testar {modelo_teste}", key=f"testar_{pid}", width="stretch", disabled=not ok,
                     help="Faz 1 chamada pedindo pro modelo usar a ferramenta executar_sql"):
            with st.spinner("Testando (1 chamada)..."):
                funcionou, detalhe = llm.testar_modelo(pid, modelo_teste)
            (st.success if funcionou else st.error)(detalhe)

        lista = st.session_state.get(f"lista_{pid}")
        if lista:
            escolhidos = st.multiselect(f"{len(lista)} modelos disponíveis — escolha na ordem de preferência",
                                        lista, key=f"multi_{pid}")
            if escolhidos and st.button("Usar os escolhidos", key=f"usar_{pid}"):
                _salvar(p.var_modelos, ",".join(escolhidos), ao_salvar)
                st.success("Modelos salvos no .env.")
                st.rerun()


def mostrar_pagina_configuracao(ao_salvar):
    st.title("⚙️ Modelos e chaves")
    st.caption(
        "Tudo que você salvar aqui vai para o arquivo .env do projeto. Chaves de API ficam só no seu PC "
        "(o .env está no .gitignore e nunca vai pro GitHub)."
    )
    status = llm.status_provedores()

    st.subheader("Provedor padrão")
    opcoes = list(provedores.VALIDOS)
    atual = llm.provedor_padrao()
    novo = st.selectbox("Usado quando o programa abre (PROVEDOR_LLM)", opcoes,
                        index=opcoes.index(atual) if atual in opcoes else 0,
                        format_func=lambda x: "auto (tenta vários em sequência)" if x == "auto"
                        else f"{'✅' if status[x][0] else '⚪'} {provedores.obter(x).nome}")
    if novo != atual and st.button("Salvar provedor padrão"):
        _salvar("PROVEDOR_LLM", novo, ao_salvar)
        st.success(f"Provedor padrão: {novo}")
        st.rerun()

    st.subheader("Ordem do modo auto")
    st.caption("O modo auto tenta os provedores nesta ordem, pulando os que não estão disponíveis. "
               "Se a cota de um acabar ou ele cair, passa pro próximo.")
    ordem = st.multiselect("Provedores (a ordem de seleção é a ordem de tentativa)", provedores.IDS,
                           default=provedores.ordem_auto(), key="ordem_auto")
    if st.button("Salvar ordem do auto") and ordem:
        _salvar("ORDEM_PROVEDORES_AUTO", ",".join(ordem), ao_salvar)
        st.success("Ordem salva.")

    st.subheader("APIs (chave no .env)")
    for pid in provedores.IDS:
        if not provedores.obter(pid).eh_cli:
            _bloco_provedor(pid, status, ao_salvar)

    st.subheader("CLIs instaladas no PC (usam o seu login, sem chave)")
    st.caption("O agente chama a CLI numa pasta temporária vazia, com as ferramentas dela desligadas ou em modo "
               "somente leitura. Cada pergunta leva de 15 s a 1 min por causa da inicialização da CLI.")
    for pid in provedores.IDS:
        if provedores.obter(pid).eh_cli:
            _bloco_provedor(pid, status, ao_salvar)
