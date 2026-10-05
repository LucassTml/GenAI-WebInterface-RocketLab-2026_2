"""
O agente, montado como um grafo do LangGraph.

    START ──> guardrail_entrada ──(bloqueou)──> END
                     │
                     v
                  agente <───────────┐
                     │               │
           pediu ferramenta?         │
             sim ──> ferramentas ────┘
             não ──> END

Por que montei o grafo na mão em vez de usar o create_agent pronto:
  - o nó de guardrail roda ANTES do LLM e consegue encerrar sem gastar
    requisição nenhuma;
  - consigo controlar quantas vezes o LLM é chamado por pergunta (cota!);
  - fica mais fácil de explicar/debugar cada etapa.

Memória: o InMemorySaver guarda o histórico de cada conversa (thread_id).
Na hora de chamar o LLM só vão as últimas N mensagens, pra não estourar
contexto nem gastar token à toa.
"""

import time
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from . import config
from .busca_semantica import indice_disponivel
from .cache import CacheRespostas
from .ferramentas import buscar_filmes_por_sinopse, executar_sql
from .guardrails import verificar_pergunta
from .llm import ErroLLM, LLMComFallback
from .prompts import montar_prompt_sistema

AVISO_ULTIMA_CHAMADA = (
    "[aviso do sistema] Você atingiu o limite de consultas para esta pergunta. Responda agora ao "
    "usuário usando apenas os resultados que já obteve, sem chamar ferramentas. Se não deu para "
    "concluir, explique o que faltou."
)
AVISO_SEM_BUSCA_SEMANTICA = (
    "\n# Observação\nA ferramenta de busca semântica está desativada (índice não gerado). Para "
    "perguntas sobre o tema dos filmes, use executar_sql com LIKE na coluna sinopse (texto em inglês)."
)


class EstadoAgente(MessagesState):
    chamadas_llm: int
    modelo_usado: str
    bloqueado: bool


@dataclass
class RespostaAgente:
    texto: str
    consultas: list[dict] = field(default_factory=list)  # artifacts das tools (sql, colunas, linhas)
    modelo: str | None = None
    chamadas_llm: int = 0       # chamadas que deram certo
    requisicoes: int = 0        # tudo que foi enviado pro provedor (inclui falhas) = gasto de cota
    tempo_segundos: float = 0.0
    do_cache: bool = False
    bloqueado: bool = False
    erro: bool = False


class AgenteCineData:
    def __init__(self, modelos=None, provedor=None, usar_cache: bool | None = None, checkpointer=None):
        self.busca_semantica_ativa = indice_disponivel()
        self.ferramentas = [executar_sql]
        if self.busca_semantica_ativa:
            self.ferramentas.append(buscar_filmes_por_sinopse)

        self.llm = LLMComFallback(self.ferramentas, modelos=modelos, provedor=provedor)
        self.prompt_sistema = montar_prompt_sistema()
        if not self.busca_semantica_ativa:
            self.prompt_sistema += AVISO_SEM_BUSCA_SEMANTICA

        usar_cache = config.CACHE_ATIVO if usar_cache is None else usar_cache
        self.cache = CacheRespostas() if usar_cache else None
        self.memoria = checkpointer or InMemorySaver()
        self.grafo = self._montar_grafo()

    # ------------------------------------------------------------- nós ----
    def _no_guardrail(self, estado: EstadoAgente) -> dict:
        pergunta = estado["messages"][-1].text
        ok, resposta_pronta = verificar_pergunta(pergunta)
        # zera os contadores a cada pergunta nova
        if not ok:
            return {"messages": [AIMessage(resposta_pronta)], "bloqueado": True, "chamadas_llm": 0, "modelo_usado": ""}
        return {"bloqueado": False, "chamadas_llm": 0, "modelo_usado": ""}

    def _no_agente(self, estado: EstadoAgente) -> dict:
        chamadas = estado.get("chamadas_llm", 0)
        ultima_chance = chamadas + 1 >= config.MAX_CHAMADAS_LLM

        # memória de curto prazo: só as últimas N mensagens, sempre começando
        # numa mensagem do usuário (senão pode cortar um par tool_call/resposta)
        historico = trim_messages(
            estado["messages"],
            max_tokens=config.MAX_MENSAGENS_HISTORICO,
            token_counter=len,
            strategy="last",
            start_on="human",
        )
        mensagens = [SystemMessage(self.prompt_sistema), *historico]
        if ultima_chance:
            mensagens.append(HumanMessage(AVISO_ULTIMA_CHAMADA))

        # Obs: mesmo na última chance as tools continuam "ligadas". Preferi não
        # chamar sem tools porque o histórico tem tool_calls de antes e nem
        # todo provider aceita isso sem a definição das tools. O aviso acima +
        # o corte abaixo resolvem sem esse risco.
        resposta, modelo = self.llm.invocar(mensagens)

        if ultima_chance and resposta.tool_calls:
            # Alguns modelos ignoram o aviso e pedem ferramenta mesmo assim.
            # Não dá pra deixar uma tool_call sem resposta no histórico (a API
            # reclama na pergunta seguinte), então descarto a chamada.
            resposta = AIMessage(
                resposta.text
                or "Não consegui concluir essa análise dentro do limite de consultas. "
                "Tente reformular a pergunta de forma mais específica."
            )
        return {"messages": [resposta], "chamadas_llm": chamadas + 1, "modelo_usado": modelo}

    # ---------------------------------------------------------- rotas -----
    @staticmethod
    def _rota_guardrail(estado: EstadoAgente) -> str:
        return END if estado.get("bloqueado") else "agente"

    @staticmethod
    def _rota_agente(estado: EstadoAgente) -> str:
        ultima = estado["messages"][-1]
        return "ferramentas" if getattr(ultima, "tool_calls", None) else END

    def _montar_grafo(self):
        grafo = StateGraph(EstadoAgente)
        grafo.add_node("guardrail_entrada", self._no_guardrail)
        grafo.add_node("agente", self._no_agente)
        grafo.add_node("ferramentas", ToolNode(self.ferramentas))

        grafo.add_edge(START, "guardrail_entrada")
        grafo.add_conditional_edges("guardrail_entrada", self._rota_guardrail, ["agente", END])
        grafo.add_conditional_edges("agente", self._rota_agente, ["ferramentas", END])
        grafo.add_edge("ferramentas", "agente")
        return grafo.compile(checkpointer=self.memoria)

    # ------------------------------------------------------- interface ----
    @staticmethod
    def nova_conversa() -> str:
        return str(uuid.uuid4())

    def _config(self, id_conversa: str) -> dict:
        # cada volta agente->ferramentas são 2 passos no grafo
        return {"configurable": {"thread_id": id_conversa}, "recursion_limit": 2 * config.MAX_CHAMADAS_LLM + 5}

    def historico(self, id_conversa: str) -> list:
        return self.grafo.get_state(self._config(id_conversa)).values.get("messages", [])

    def perguntar(self, pergunta: str, id_conversa: str = "padrao", usar_cache: bool = True) -> RespostaAgente:
        inicio = time.perf_counter()
        cfg = self._config(id_conversa)
        primeira_da_conversa = len(self.historico(id_conversa)) == 0
        usar_cache = usar_cache and self.cache is not None and primeira_da_conversa

        # 1) cache (só na primeira pergunta da conversa, ver cache.py)
        if usar_cache:
            salvo = self.cache.buscar(pergunta)
            if salvo:
                # coloca a troca na memória mesmo vindo do cache, senão a
                # pergunta seguinte ("e em 2020?") ficaria sem contexto
                self.grafo.update_state(
                    cfg, {"messages": [HumanMessage(pergunta), AIMessage(salvo["resposta"])]}, as_node="agente"
                )
                return RespostaAgente(
                    texto=salvo["resposta"], consultas=salvo["consultas"], modelo=salvo["modelo"],
                    tempo_segundos=time.perf_counter() - inicio, do_cache=True,
                )

        # 2) roda o grafo
        requisicoes_antes = self.llm.requisicoes_feitas
        try:
            estado = self.grafo.invoke({"messages": [HumanMessage(pergunta)]}, cfg)
        except ErroLLM as e:
            return RespostaAgente(
                texto=str(e), erro=True,
                requisicoes=self.llm.requisicoes_feitas - requisicoes_antes,
                tempo_segundos=time.perf_counter() - inicio,
            )

        # 3) separa só o que aconteceu nesta pergunta
        mensagens = estado["messages"]
        idx_pergunta = max(i for i, m in enumerate(mensagens) if isinstance(m, HumanMessage) and m.text == pergunta)
        desta_rodada = mensagens[idx_pergunta + 1 :]
        consultas = [m.artifact for m in desta_rodada if isinstance(m, ToolMessage) and m.artifact]
        texto = desta_rodada[-1].text if desta_rodada else ""

        resposta = RespostaAgente(
            texto=texto,
            consultas=consultas,
            modelo=estado.get("modelo_usado") or None,
            chamadas_llm=estado.get("chamadas_llm", 0),
            requisicoes=self.llm.requisicoes_feitas - requisicoes_antes,
            tempo_segundos=time.perf_counter() - inicio,
            bloqueado=estado.get("bloqueado", False),
        )

        # só guarda no cache resposta "boa": se todas as consultas deram erro ou
        # o agente bateu no limite de chamadas, a próxima tentativa tem que ir
        # pro LLM de novo (senão a falha ficaria presa no cache por 72h)
        alguma_consulta_ok = not consultas or any(not c.get("erro") for c in consultas)
        bateu_limite = resposta.chamadas_llm >= config.MAX_CHAMADAS_LLM
        if usar_cache and not resposta.bloqueado and texto.strip() and alguma_consulta_ok and not bateu_limite:
            self.cache.salvar(pergunta, texto, consultas, resposta.modelo)
        return resposta
