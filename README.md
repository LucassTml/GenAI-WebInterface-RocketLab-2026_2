# 🎬 CineData Assistente — agente Text-to-SQL

Agente de IA que responde perguntas em **português** sobre o catálogo de filmes da CineData
Analytics, consultando **em tempo real** a camada Gold do Data Lakehouse (SQLite). A ideia é que
qualquer pessoa da empresa consiga tirar dúvidas sobre os dados sem saber SQL.

> Atividade de GenAI — Rocket Lab 2026 (Visagio)

Exemplo do tipo de resposta esperada (os números vêm do [gabarito](avaliacao/gabarito.md)):

```
Você> Qual gênero tem a maior margem de lucro média?

CineData> O gênero com maior margem de lucro média é Horror, com 75,92%.
          Critério: só filmes com receita e orçamento informados, margem calculada em US$
          como lucro total ÷ receita total do gênero (a média simples das margens é distorcida
          por filmes com receita de US$ 1).
```

## Sumário

- [O que tem no projeto](#o-que-tem-no-projeto)
- [Como funciona](#como-funciona)
- [Passo a passo para rodar](#passo-a-passo-para-rodar)
- [Como usar](#como-usar)
- [Avaliação automática](#avaliação-automática)
- [Testes](#testes)
- [Decisões técnicas](#decisões-técnicas)
- [Limitações conhecidas](#limitações-conhecidas)
- [Estrutura de pastas](#estrutura-de-pastas)

## O que tem no projeto

**Requisitos da atividade**
- Agente Text-to-SQL em Python com **LangGraph**, usando modelos gratuitos do **OpenRouter** com tool calling.
- Consultas **somente leitura** sobre as 10 tabelas da camada Gold (`cinerocket.db`).
- Responde às 5 categorias de perguntas do enunciado (bilheteria, popularidade, elenco, gêneros/produtoras e avaliações).

**Extras que implementei**
| Extra | Onde | Resumo |
|---|---|---|
| Guardrails em 3 camadas | `guardrails.py`, `banco.py` | pergunta (injection / pedido de escrita) → SQL (só SELECT) → banco read-only com *authorizer* |
| Fallback entre modelos gratuitos | `llm.py` | se um modelo está lotado (429), tenta o próximo; se a cota acabou, para na hora |
| Cache de respostas | `cache.py` | pergunta repetida não gasta requisição |
| Memória de conversa | `agente.py` | "e qual foi o segundo?" funciona |
| Agente híbrido (SQL + busca semântica) | `busca_semantica.py`, `ferramentas.py` | "filmes sobre viagem no tempo" usando embeddings das sinopses |
| Interface visual + gráficos | `app.py`, `graficos.py` | chat em Streamlit com tabela, SQL e gráfico automático |
| Avaliação com respostas esperadas | `avaliacao/` | 17 perguntas com SQL de referência e nota automática |
| Análise exploratória | `notebooks/exploracao_dados.ipynb` | as "pegadinhas" dos dados que viraram regras do prompt |
| Testes automatizados | `tests/` | 70 testes, rodam sem gastar cota (LLM falso) |
| Modelo local (opcional) | `llm.py` | mesmo código roda com Ollama quando a cota acaba |

## Como funciona

```
                ┌──────────────────────┐
 pergunta ───►  │  guardrail_entrada   │──(injection / pedido de escrita)──► resposta pronta (0 req)
                └──────────┬───────────┘
                           ▼
                ┌──────────────────────┐   tool call    ┌──────────────────────────────┐
                │   agente (LLM via    │ ─────────────► │ ferramentas                  │
                │   OpenRouter + fall- │ ◄───────────── │  • executar_sql (read-only)  │
                │   back de modelos)   │   resultado    │  • buscar_filmes_por_sinopse │
                └──────────┬───────────┘                └──────────────────────────────┘
                           ▼
                 resposta em português + dados (tabela / SQL / gráfico)
```

1. O **guardrail** barra prompt injection e pedidos de alteração do banco antes de gastar requisição.
2. O **LLM** recebe um prompt com o esquema das 10 tabelas e as **regras de negócio** que descobri
   explorando os dados (ex.: lucro nunca é nulo, nota TMDB = 0 significa "sem voto", câmbio do
   orçamento truncado...). Ele decide qual ferramenta usar e escreve o SQL.
3. A ferramenta valida e executa o SQL (somente leitura, timeout de 90 s). Se der erro, a mensagem
   volta pro modelo com uma **dica** (ex.: "a coluna `popularidade` está em `fact_movies_performance`").
4. O modelo escreve a resposta. A interface mostra também a tabela completa, o SQL e um gráfico.

Uma pergunta normal gasta **2 requisições** (gerar o SQL + escrever a resposta). O limite por
pergunta é 5.

## Passo a passo para rodar

### 1. Pré-requisitos
- **Python 3.11 ou mais novo** (testei no 3.14) — https://www.python.org/downloads/
- **Git** — https://git-scm.com/downloads
- Uma conta gratuita no **OpenRouter** (passo 5)

### 2. Clonar o repositório
```bash
git clone https://github.com/LucassTml/GenAI-WebInterface-RocketLab-2026_2.git cinedata-agent
cd cinedata-agent
```

### 3. Criar o ambiente virtual e instalar as dependências
Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Linux / macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
> Se o PowerShell bloquear o `Activate.ps1`, rode antes:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 4. Colocar o banco de dados
O `cinerocket.db` (~580 MB) **não está no repositório** (o GitHub não aceita arquivos > 100 MB).
Baixe da pasta compartilhada da atividade e coloque em:
```
cinedata-agent/data/cinerocket.db
```

### 5. Configurar a chave do OpenRouter
1. Crie uma conta em https://openrouter.ai e gere uma chave em https://openrouter.ai/keys
   (começa com `sk-or-v1-`).
2. Copie o arquivo de exemplo e cole a chave:
   ```powershell
   Copy-Item .env.example .env      # Windows
   # cp .env.example .env           # Linux/macOS
   ```
   Abra o `.env` e troque `sk-or-v1-COLE_SUA_CHAVE_AQUI` pela sua chave.
3. Confira se está tudo certo (não gasta cota):
   ```bash
   python scripts/verificar_cota.py
   ```

### 6. (Opcional) Gerar o índice da busca semântica
Só é necessário para perguntas sobre o **tema** dos filmes ("filmes sobre tubarões"). Roda uma vez,
leva **~50 min** numa CPU de notebook e baixa um modelo de ~220 MB:
```bash
python scripts/indexar_sinopses.py
```
Sem o índice o agente funciona normalmente, só usa `LIKE` na sinopse no lugar da busca semântica.

### 7. Rodar
Interface web:
```bash
streamlit run app.py
```
Abre em http://localhost:8501.

Terminal:
```bash
python main.py                                        # conversa
python main.py "Quais são os 5 filmes mais populares?"  # pergunta única
python main.py --mostrar-sql                          # mostra o SQL e a tabela
```

## Como usar

Perguntas que o agente responde (todas estão como botão na barra lateral da interface):

| Categoria | Exemplos |
|---|---|
| Bilheteria e Finanças | Top 10 filmes com maior receita em R$ · Lucro médio por gênero · Filmes com maior margem de lucro |
| Popularidade e Engajamento | 5 filmes mais populares · Maior divergência TMDB × IMDb · Nota média IMDb por ano |
| Elenco e Equipe | Ator com mais filmes nos últimos 5 anos · Diretores com maior nota (mín. 5 filmes) · Dupla ator–diretor |
| Gêneros e Produtoras | Filmes por gênero · Produtora com maior lucro · Gênero com maior margem média |
| Avaliações dos Usuários | Filmes mais avaliados · Maior divergência usuários × IMDb |
| Busca por tema | Filmes sobre viagem no tempo · Terror com casa mal-assombrada depois de 2020 |

As respostas esperadas de cada uma estão em [`avaliacao/gabarito.md`](avaliacao/gabarito.md).

**Comandos do terminal:** `/nova` (limpa a memória), `/sql` (mostra SQL), `/cota` (cota restante),
`/exemplos`, `/sair`.

### Sobre a cota do OpenRouter
O plano gratuito permite **50 requisições por dia** (renova às 21h de Brasília), e requisições que
dão erro também contam. O projeto já economiza o máximo possível, mas vale saber:
- cada pergunta nova gasta ~2 requisições; pergunta repetida vem do cache (0);
- `python scripts/verificar_cota.py` mostra quantas restam;
- se aparecer "provider lotado", o agente troca de modelo sozinho;
- a lista de modelos pode ser trocada no `.env` (`MODELOS_LLM`). Para ver os modelos gratuitos com
  tool calling disponíveis hoje: `python scripts/listar_modelos_free.py`.

### (Opcional) Rodar com modelo local (Ollama)
Útil quando a cota do dia acabou. Instale o [Ollama](https://ollama.com/download), baixe um modelo
com suporte a tools e mude o `.env`:
```bash
ollama pull qwen2.5:3b
```
```
PROVEDOR_LLM=ollama
OLLAMA_MODELO=qwen2.5:3b
```
O `qwen2.5:3b` coube inteiro numa GPU de 4 GB (MX570) e respondeu em 15-30 s por pergunta. Com GPU
melhor dá pra usar `qwen3:8b`. Modelos pequenos erram bem mais o SQL que os do OpenRouter (num teste
com 5 perguntas o de 3B acertou 4), então servem mais para testar o fluxo.

## Avaliação automática

`avaliacao/perguntas.yaml` tem as **14 perguntas do enunciado + 3 de comportamento** (busca
semântica, guardrail e pergunta fora do escopo), cada uma com um SQL de referência conferido à mão.
A nota compara o **resultado** da consulta do agente com o do gabarito (*execution accuracy*), com
tolerância para nomes de coluna, escalas e empates.

```bash
python avaliacao/gerar_gabarito.py                 # gera avaliacao/gabarito.md (não usa LLM)
python avaliacao/avaliar.py --estimar              # quantas requisições vai gastar
python avaliacao/avaliar.py --categoria "Elenco e Equipe"
python avaliacao/avaliar.py --ids fin_01 pop_01
python avaliacao/avaliar.py                        # todas (~40 requisições!)
```
O relatório fica em `avaliacao/resultados/avaliacao_<data>.md`. Como a avaliação completa gasta
quase a cota inteira do dia, recomendo rodar por categoria. As respostas ficam em cache, então
rodar de novo não gasta nada.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```
São 70 testes cobrindo guardrails, segurança do banco (inclusive tentando burlar a validação),
timeout, fallback entre modelos, cache, memória, limite de chamadas e o comparador da avaliação.
Os testes do agente usam um **LLM falso**, então não gastam cota.

## Decisões técnicas

O raciocínio completo está em [`docs/decisoes_tecnicas.md`](docs/decisoes_tecnicas.md). Resumo:

- **Explorei os dados antes do prompt.** Achei 9 "pegadinhas" (lucro nunca nulo, câmbio truncado no
  orçamento, nota TMDB 0 sem voto, popularidade com o ano no lugar...). Cada uma virou uma regra
  no prompt. Sem isso o agente responderia várias perguntas do enunciado errado.
- **LangGraph com o grafo montado à mão**, pra ter um guardrail que roda antes do LLM e controlar
  quantas chamadas cada pergunta pode gastar.
- **Esquema inteiro no prompt** em vez de tools de "listar tabelas": economiza 1-2 requisições
  por pergunta.
- **Gráfico por regra** (ano → linha, texto + número → barras) em vez de pedir pro LLM.
- **Busca semântica com embeddings multilíngues** (sinopses em inglês, perguntas em português),
  combinada com filtros SQL.

## Limitações conhecidas

- Modelos gratuitos variam bastante de qualidade e disponibilidade; o mesmo modelo pode responder
  diferente em dias diferentes.
- A memória da conversa fica em RAM (some ao reiniciar).
- A consulta de "dupla ator–diretor" é pesada (~15-50 s) por causa das chaves em hash de 64
  caracteres.
- Perguntas muito ambíguas ("qual o melhor filme?") são respondidas com um critério escolhido
  pelo agente, que ele explica na resposta.

## Estrutura de pastas

```
cinedata-agent/
├── app.py                    # interface Streamlit
├── main.py                   # interface de terminal
├── cinedata_agent/
│   ├── agente.py             # grafo LangGraph (guardrail → agente ⇄ ferramentas)
│   ├── prompts.py            # prompt de sistema: esquema + regras de negócio
│   ├── ferramentas.py        # tools: executar_sql e buscar_filmes_por_sinopse
│   ├── banco.py              # conexão read-only, authorizer, timeout, dicas de erro
│   ├── guardrails.py         # validação da pergunta e do SQL
│   ├── llm.py                # OpenRouter/Ollama + fallback entre modelos + cota
│   ├── cache.py              # cache de respostas (SQLite)
│   ├── busca_semantica.py    # índice de embeddings das sinopses
│   ├── graficos.py           # gráfico automático
│   └── config.py             # leitura do .env
├── avaliacao/                # perguntas + gabarito + avaliação automática
├── notebooks/                # análise exploratória dos dados
├── scripts/                  # indexar sinopses, verificar cota, listar modelos
├── tests/                    # pytest (sem gastar cota)
├── docs/decisoes_tecnicas.md
└── data/                     # cinerocket.db e arquivos gerados (fora do git)
```
