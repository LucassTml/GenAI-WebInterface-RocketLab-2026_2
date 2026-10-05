# Modelos: quais dá pra usar e como trocar

O agente nasceu usando só os modelos gratuitos do OpenRouter (que é o que a atividade pedia), mas com o limite
de 50 requisições por dia isso ficou apertado pra testar. Então fui adicionando outras opções. Hoje dá pra
escolher entre **11 provedores** + um modo **auto**, e trocar de um pro outro sem mexer no código.

> ⚠️ **Aviso: só o OpenRouter foi avaliado de verdade.** Ele acertou 17 de 17 perguntas na avaliação
> automática (`avaliacao/resultados/avaliacao_final.md`). Os outros provedores, marcados com **🧪 beta** no
> programa, responderam certo nos testes rápidos que eu fiz, mas não passaram pela avaliação completa. Eles podem
> errar o SQL, demorar mais ou dar erro. Se acontecer, volta pro OpenRouter.

## Os provedores

| `PROVEDOR_LLM` | O que é | Precisa de | Custo | Situação |
|---|---|---|---|---|
| `openrouter` | modelos `:free` do OpenRouter | `OPENROUTER_API_KEY` | grátis (50 req/dia) | ✅ avaliado (17/17) |
| `nvidia` | API da NVIDIA (build.nvidia.com) | `NVIDIA_API_KEY` | créditos grátis | 🧪 beta |
| `google` | Gemini API (Google AI Studio) | `GOOGLE_API_KEY` | plano grátis | 🧪 beta |
| `opencode` | OpenCode Zen (API) | `OPENCODE_API_KEY` | modelos `-free` grátis | 🧪 beta |
| `anthropic` | API do Claude | `ANTHROPIC_API_KEY` | pago | 🧪 beta |
| `openai` | API da OpenAI | `OPENAI_API_KEY` | pago | 🧪 beta |
| `ollama` | modelo rodando no PC | Ollama instalado | grátis, offline | 🧪 beta |
| `agy` | Antigravity CLI | `agy` instalado e logado | sua conta | 🧪 beta |
| `claude-code` | Claude Code CLI | `claude` instalado e logado | sua assinatura | 🧪 beta |
| `codex` | Codex CLI | `codex` instalado e logado | sua assinatura | 🧪 beta |
| `opencode-cli` | OpenCode CLI | `opencode` instalado | depende do que tiver configurado nele | 🧪 beta |
| `auto` | vários em sequência | pelo menos um dos de cima | — | 🧪 beta |

Os de **API** precisam de chave. Os de **CLI** usam o login que você já fez na ferramenta, então não tem chave
nenhuma pra colocar.

## Como trocar de modelo

Tem três jeitos. Use o que for mais prático.

### 1) Pela interface (o mais fácil)
```powershell
.\cinedata web
```
- **Trocar na hora:** seletor **Provedor do LLM**, na barra lateral do chat. Dá pra trocar no meio da conversa
  que ela não se perde.
- **Configurar chaves e modelos:** tela **⚙️ Modelos e chaves**, no topo da barra lateral. Em cada provedor dá pra:
  - colar a chave (ela vai pro `.env` e aparece mascarada);
  - escrever a lista de modelos;
  - clicar em **Listar modelos da conta**, que pergunta pro provedor quais modelos sua chave enxerga;
  - clicar em **Testar**, que faz 1 chamada e diz se aquele modelo sabe usar a ferramenta de SQL.

  Na mesma tela também ficam o provedor padrão e a ordem do modo auto.

### 2) Pelo arquivo `.env`
Tudo fica no `.env`, na pasta do projeto. O `.env.example` tem todas as variáveis e o link de onde gerar cada chave.
```
PROVEDOR_LLM=google
GOOGLE_API_KEY=cole-a-chave-aqui
MODELOS_GOOGLE=gemini-flash-latest,gemini-3.8-flash
```
Cada provedor tem a sua variável de modelos (`MODELOS_LLM`, `MODELOS_NVIDIA`, `MODELOS_GOOGLE`, `MODELOS_AGY`...).
A lista é separada por vírgula e **a ordem importa**: se o primeiro modelo falhar ou estiver lotado, o agente tenta o
próximo. Nas CLIs, `padrao` quer dizer "usa o modelo que a CLI já tem configurado".

### 3) Pela linha de comando (vale só pra aquela execução)
```powershell
.\cinedata chat --provedor agy
.\cinedata chat --provedor claude-code --modelo sonnet
.\cinedata web --provedor google
.\cinedata avaliar --provedor codex --sem-cache --ids fin_01
.\cinedata provedores          # mostra o que está disponível
```

## Onde pegar cada chave

| Variável | Onde gerar |
|---|---|
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| `NVIDIA_API_KEY` | https://build.nvidia.com → "Get API Key" (começa com `nvapi-`) |
| `GOOGLE_API_KEY` | https://aistudio.google.com/apikey |
| `OPENCODE_API_KEY` | https://opencode.ai/auth |
| `ANTHROPIC_API_KEY` | https://platform.claude.com/settings/keys |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |

O `.env` está no `.gitignore`, então as chaves não vão pro GitHub.

## As CLIs (agy, Claude Code, Codex, OpenCode)

Essas ferramentas não têm *tool calling* pela linha de comando, então fiz um adaptador (`cinedata_agent/cli_llm.py`).
Ele manda um prompt explicando um formato de resposta (`<tool_call>{...}</tool_call>`), roda o SQL que o modelo
pediu e devolve o resultado na chamada seguinte. Pra CLI não mexer em nada do PC, cada chamada roda numa pasta
temporária vazia, com as ferramentas dela desligadas (Claude Code) ou em modo só leitura (Codex, OpenCode e agy).

Login, se ainda não fez: `agy` (conta Google), `claude` → `/login`, `codex login`, `opencode auth login`.
Cada pergunta leva uns 15 a 30 s por causa da inicialização da CLI.

## Ollama (modelo local)

- **Só liga quando você escolhe o Ollama.** Se o provedor for outro, ele nem é iniciado. O modo auto também
  não liga o Ollama: só usa se ele já estiver rodando.
- **Desliga quando você fecha o programa**, mesmo se fechar o terminal direto ou o programa travar. Uso um
  Job Object do Windows pra isso, ver `cinedata_agent/ollama_local.py`.
- Se o Ollama já estava rodando antes (ex.: o instalado que abre com o Windows), o programa usa e **não** fecha,
  porque não foi ele que abriu.
- O `.\cinedata ollama` liga o Ollama "na mão" e ele fica ligado até você parar.

Ele procura o Ollama instalado ou a versão portátil na pasta `..\ollama-portable`. O modelo padrão é o
`qwen2.5:3b`, que coube inteiro numa GPU de 4 GB. Modelos pequenos assim erram bem mais o SQL.

## Modo auto

```
PROVEDOR_LLM=auto
ORDEM_PROVEDORES_AUTO=openrouter,nvidia,google,opencode,ollama
```
Ele tenta na ordem, só com os provedores que estão prontos (com chave, CLI instalada ou Ollama já ligado).
- Modelo lotado (erro 429) fica 3 minutos de castigo.
- Se a cota diária do OpenRouter acabar ou uma chave for recusada, o provedor inteiro sai da fila, pra não
  ficar gastando requisição à toa.

## Quando dá problema

| O que aparece | O que fazer |
|---|---|
| `No module named ...` / arquivo não encontrado | usa o atalho `.\cinedata` (ele já usa o `.venv`) ou ativa o `.venv` |
| `falta XXX_API_KEY no .env` | coloca a chave na tela Modelos e chaves ou no `.env` |
| `recusou a chave (401/403)` | chave errada ou revogada |
| `HTTP 404` num modelo | o nome mudou: usa "Listar modelos da conta" |
| "respondeu só com texto" no teste | esse modelo não sabe usar ferramentas, tira da lista |
| `A CLI 'X' falhou ... Ela está logada?` | abre a CLI no terminal e faz login |
| resposta igual trocando de provedor | veio do cache: desliga "Usar cache" ou usa `--sem-cache` |

## Onde isso está no código

- `cinedata_agent/provedores.py`: a lista dos provedores (URL, variável da chave, variável dos modelos, se é beta)
- `cinedata_agent/llm.py`: conexão com cada um e o fallback entre modelos e provedores
- `cinedata_agent/cli_llm.py`: o adaptador das CLIs
- `cinedata_agent/ollama_local.py`: liga/desliga do Ollama
- `interface_config.py`: a tela "Modelos e chaves"
