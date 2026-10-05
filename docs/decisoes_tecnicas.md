# Decisões técnicas

Aqui eu explico **por que** fiz cada escolha do projeto. O README explica *como rodar*;
este arquivo explica *o raciocínio*.

---

## 1. Antes do código: entender os dados

Antes de escrever qualquer prompt eu passei um tempo explorando o `cinerocket.db`
(está tudo no `notebooks/exploracao_dados.ipynb`). Isso foi a parte mais importante do
projeto, porque **um agente Text-to-SQL só acerta se souber as pegadinhas dos dados**, e
tinha várias:

| O que encontrei | Consequência se ninguém avisar o LLM | Regra que coloquei no prompt |
|---|---|---|
| `lucro_usd`/`lucro_brl` **nunca são NULL**: sem receita, lucro = −orçamento; sem orçamento, lucro = receita | "produtora com maior lucro" soma prejuízo falso de filmes sem receita | lucro só com receita **e** orçamento informados (a não ser que o usuário diga outro critério) |
| Só ~3,5% dos filmes têm receita (3.373 de 95.645) | ranking de receita cheio de NULL | filtrar `IS NOT NULL` |
| 59% dos orçamentos foram convertidos para R$ com **câmbio truncado** (3,0 / 4,0 / 5,0), diferente do câmbio da receita | a margem em R$ não bate com a margem na moeda original (pouco, mas muda) | margens calculadas em **US$** |
| Margem média simples por gênero dá **−534% a −152.024%** (filmes com receita de US$ 1) | "gênero com maior margem" vira sorteio de outlier | margem média = Σlucro / Σreceita (média ponderada pela receita) |
| `nota_tmdb = 0` com `qtd_tmdb = 0` em 33 mil filmes | "divergência TMDB × IMDb" vira lista de filmes sem voto | usar `nota_tmdb` só com `qtd_tmdb > 0` |
| 4 filmes com **popularidade = ano** (ex.: *La Fellinette* = 2020.0) — erro de carga | 3 deles aparecem no top 5 de popularidade | não remover, mas avisar o usuário |
| Títulos repetidos (*Emesis Blue* aparece 36 vezes) | agrupar por título soma filmes diferentes | agrupar por `sk_movie_id` |
| `dim_people` tem um registro por papel (a mesma pessoa pode ser Ator e Diretor) | dupla "ator–diretor" com a mesma pessoa | filtrar `tipo_pessoa` e nomes diferentes |
| Filmes até 2029 (planejados / em produção) | "últimos 5 anos" pegando filme que nem saiu | `status_filme = 'Lançado'` e data ≤ hoje |

Também medi o tempo das consultas. A da dupla ator–diretor levava de **86 a 148 s**. Minha
primeira aposta foi reescrever com CTEs filtrando o tipo de pessoa antes do JOIN, mas não era isso.
O que resolveu foi a **configuração da conexão**: o SQLite usa só 2 MB de cache por padrão; com
`cache_size` de 128 MB, `temp_store = MEMORY` e `mmap_size`, a consulta caiu para **~17 s**, com ou
sem CTE (16,6 s × 17,8 s). Os PRAGMAs ficam em `banco.py`.

---

## 2. Framework: LangGraph com grafo montado à mão

Opções que considerei: `create_agent` pronto do LangChain, LangGraph "na mão", smolagents,
ou um loop manual com o SDK da OpenAI.

Fui de **LangGraph montando o grafo explicitamente** porque:

1. **Guardrail sem custo**: o primeiro nó checa a pergunta *antes* de chamar o LLM. Se for
   prompt injection ou pedido de `DROP TABLE`, o grafo termina ali — 0 requisições gastas.
2. **Controle de cota**: consigo contar quantas vezes o LLM foi chamado na pergunta e forçar a
   resposta na última chamada permitida (`MAX_CHAMADAS_LLM`, padrão 5).
3. **Memória pronta**: o checkpointer do LangGraph (`InMemorySaver`) guarda a conversa por
   `thread_id`, então "e no ano seguinte?" funciona.
4. É fácil de explicar e de testar cada nó separadamente.

```
START → guardrail_entrada ─(bloqueou)→ END
              │
              ▼
           agente ◄──────────┐
              │              │
     pediu ferramenta?       │
       sim → ferramentas ────┘
       não → END
```

---

## 3. Modelo e fallback

- **OpenRouter** com modelos `:free` que suportam *tool calling* (conferido na API pública
  `/api/v1/models`, campo `supported_parameters`). O `ChatOpenAI` do LangChain funciona
  direto, só trocando a `base_url`.
- **Fallback** entre modelos (`llm.py`): a lista `MODELOS_LLM` é tentada em ordem.
  - 429 com "provider" → o modelo está lotado → tenta o próximo e deixa esse "de castigo" por
    3 min.
  - 429 com `free-models-per-day` → a **cota** acabou → para na hora. Trocar de modelo não
    resolve e cada tentativa *também conta na cota* (isso está no guia do OpenRouter).
  - `max_retries=0`: o padrão do LangChain é tentar de novo 2×, o que transformaria 1 erro em
    3 requisições gastas.
  - No máximo 3 tentativas por chamada.
- **Ollama (opcional)**: o mesmo código roda com um modelo local apontando a `base_url` para
  `localhost:11434/v1`. Usei para testar o fluxo inteiro sem gastar cota. Modelos pequenos
  (3-4B) erram muito o SQL, mas servem para testar o encadeamento.
- **NVIDIA e modo auto**: depois incluí a API da NVIDIA (build.nvidia.com), que também é compatível com a
  da OpenAI. Cada modelo virou um "alvo" `provedor:modelo`, e no modo `auto` a fila é OpenRouter → NVIDIA →
  Ollama, só com os provedores disponíveis.
- **CLIs como provedor (agy, Claude Code, Codex, OpenCode)**: elas usam o login/assinatura que a pessoa já tem, mas
  pela linha de comando não existe *tool calling* nativo. Fiz um adaptador (`cli_llm.py`) que monta um prompt
  único com o protocolo `<tool_call>{json}</tool_call>`, roda a CLI numa pasta temporária vazia (com as ferramentas
  dela desligadas ou em modo leitura) e converte a saída numa tool call de verdade. O grafo não precisou mudar
  nada. Testei as quatro de ponta a ponta e todas acertaram a pergunta de teste.
- **Anthropic** entra pelo SDK oficial (`langchain-anthropic`), e não pelo endpoint compatível com OpenAI. Os
  modelos Claude atuais recusam `temperature`, então esse provedor não manda o parâmetro. A diferença em relação ao fallback entre modelos é o tratamento
  por provedor: cota diária esgotada ou chave recusada tira o *provedor inteiro* da fila (não adianta tentar
  outro modelo dele), enquanto 429 de "lotado" só põe aquele modelo de castigo. Detalhes de uso em
  [`como_rodar.md`](como_rodar.md).

---

## 4. Economia de requisições (cota de 50/dia)

Cada decisão abaixo existe para gastar menos chamadas ao LLM:

| Decisão | Economia |
|---|---|
| Esquema completo no prompt (em vez de tools `listar_tabelas`/`descrever_tabela`) | 1-2 chamadas por pergunta |
| Dica automática quando o SQL erra coluna/tabela (`dica_para_erro`) | evita uma tentativa às cegas |
| Gráfico gerado por regra (ano → linha; texto+número → barras), sem tool de gráfico | 1 chamada por pergunta |
| Cache de respostas (SQLite) na 1ª pergunta de cada conversa | 100% numa pergunta repetida |
| Guardrail de entrada antes do LLM | 100% em pergunta maliciosa |
| Limite de chamadas por pergunta | evita loop de correção infinito |

Uma pergunta típica gasta **2 requisições**: uma para o modelo gerar o SQL (tool call) e outra
para ele escrever a resposta com o resultado.

---

## 5. Segurança (guardrails em camadas)

1. **Pergunta** (`guardrails.verificar_pergunta`): regex para prompt injection e pedidos de
   escrita. Ajustei para não barrar frases legítimas como "excluindo os filmes sem receita".
2. **SQL** (`guardrails.validar_sql`): só uma instrução, começando com `SELECT`/`WITH`, sem
   palavras de escrita fora de strings, sem tabelas internas do SQLite.
3. **Banco** (`banco.py`): conexão `mode=ro` + `PRAGMA query_only` + **authorizer** do SQLite
   que só libera `SELECT`, leitura e funções. Mesmo um SQL que passe pela regex é recusado
   pelo próprio SQLite (tem teste para isso em `tests/test_banco.py`).
4. **Timeout** de 90 s por consulta (via `progress_handler`) e limite de linhas lidas.
5. O prompt instrui o modelo a recusar perguntas fora do catálogo.
6. **Saída** (`guardrails.resposta_parece_valida`): criado depois da avaliação real. Numa resposta o modelo
   gratuito vazou o raciocínio em inglês ("The user asked: ...") e terminou gerando lixo. Medi dois sinais nas 17
   respostas reais — começo com raciocínio em inglês e letras soltas sem sentido (49 na resposta quebrada, no
   máximo 2 nas boas) — e uso os dois: se a resposta final parecer degenerada, o fallback tenta outro modelo.

---

## 6. Busca semântica (agente híbrido)

Pergunta tipo "filmes sobre viagem no tempo" não funciona com SQL (`LIKE '%time travel%'`
perde quem escreveu "travels back to 1985"). Então gerei embeddings das sinopses e criei a
tool `buscar_filmes_por_sinopse`, que combina:

- **semântica** para achar o tema (similaridade de cosseno com numpy — são ~82 mil vetores,
  não precisei de banco vetorial);
- **SQL** para aplicar filtros (gênero, ano, nota mínima) e trazer dados da camada Gold.

Testei 3 modelos de embedding com 3 mil sinopses:

| Modelo | Velocidade (CPU do notebook) | "tubarão gigante ataca pessoas no mar" |
|---|---|---|
| potion-multilingual-128M (estático) | ~1.300 docs/s | Bad Blood, Martians Vs Mexicans... ❌ |
| bge-small-en-v1.5 | ~14-50 docs/s | filmes brasileiros aleatórios ❌ (não entende português) |
| **paraphrase-multilingual-MiniLM-L12-v2** | ~40 docs/s | **The Meg, Sky Sharks** ✅ |

As sinopses estão em inglês e as perguntas em português, então precisava de um modelo
multilíngue. Ordenar os textos por tamanho antes de montar os lotes (menos *padding*) e cortar em
128 tokens levou a geração de ~29 para ~40 docs/s. A indexação completa (81.785 sinopses) levou
52 min no meu notebook, mas só precisa rodar uma vez.

---

## 7. Avaliação

`avaliacao/perguntas.yaml` tem as 14 perguntas do enunciado + 3 de comportamento (busca
semântica, guardrail e pergunta fora do escopo), cada uma com um **SQL de referência**
conferido à mão.

A métrica é *execution accuracy* (a mesma ideia do Spider/BIRD): comparo o **resultado** da
consulta do agente com o resultado do gabarito, e não o texto do SQL — existem vários SQLs
certos para a mesma pergunta. Para ser justo:

- os itens esperados podem estar em qualquer coluna (o agente pode chamar de "filme" em vez
  de "titulo");
- números com tolerância de 1% e escalas diferentes (margem 0,75 ou 75%; receita em bilhões);
- quando o ranking tem empate (ex.: 7 filmes com 9 avaliações), comparo só os valores;
- quando a pergunta é ambígua (moeda do "lucro médio"), aceito alternativas;
- e, além dos dados, o texto final precisa passar no guardrail de saída (resposta degenerada reprova).

**Resultado (05/10/2026, `nvidia/nemotron-3.5-lightning:free`): 17/17.** A rodada completa deu 16/17; a
`fin_02` foi refeita depois de dois ajustes que a própria avaliação revelou (LIMIT em agregação por gênero e texto
degenerado). Detalhes em `avaliacao/resultados/avaliacao_final.md`.

---

## 8. O que eu faria com mais tempo

- Conectar na minha própria camada Gold no Databricks (`databricks-sql-connector`) em vez do
  SQLite.
- LLM-as-judge para avaliar a *qualidade do texto* da resposta (hoje avalio os dados).
- Persistir a memória das conversas em disco (`SqliteSaver`) em vez de memória RAM.
- Few-shot dinâmico: guardar perguntas que deram certo e usar as mais parecidas como exemplo.
