# Como rodar: provedores de LLM, chaves e modelos

O agente roda com **11 provedores**, em dois grupos, mais o modo **auto**:

- **APIs**, que precisam de chave no `.env`: OpenRouter, NVIDIA, Google Gemini, OpenCode Zen, Anthropic e OpenAI.
  O Ollama local também entra aqui, mas sem chave.
- **CLIs instaladas no PC**, que usam o login que você já tem: Antigravity (`agy`), Claude Code (`claude`), Codex
  (`codex`) e OpenCode (`opencode`).

O catálogo fica em `cinedata_agent/provedores.py`, e a conexão em `cinedata_agent/llm.py` e
`cinedata_agent/cli_llm.py`.

| `PROVEDOR_LLM` | O que usa | Chave (`.env`) | Modelos (`.env`) | Custo |
|---|---|---|---|---|
| `openrouter` (padrão) | modelos `:free` do OpenRouter | `OPENROUTER_API_KEY` | `MODELOS_LLM` | grátis, 50 req/dia |
| `nvidia` | build.nvidia.com | `NVIDIA_API_KEY` | `MODELOS_NVIDIA` | créditos grátis |
| `google` | Gemini API (AI Studio) | `GOOGLE_API_KEY` | `MODELOS_GOOGLE` | plano grátis (flash) |
| `opencode` | OpenCode Zen | `OPENCODE_API_KEY` | `MODELOS_OPENCODE` | modelos `-free` sem custo |
| `anthropic` | API do Claude (SDK oficial) | `ANTHROPIC_API_KEY` | `MODELOS_ANTHROPIC` | pago por uso |
| `openai` | API da OpenAI | `OPENAI_API_KEY` | `MODELOS_OPENAI` | pago por uso |
| `ollama` | modelo local | — | `OLLAMA_MODELO` | grátis, offline |
| `agy` | Antigravity CLI | — (login do agy) | `MODELOS_AGY` | sua conta Google |
| `claude-code` | Claude Code CLI | — (login do claude) | `MODELOS_CLAUDE_CODE` | sua assinatura |
| `codex` | Codex CLI | — (login do codex) | `MODELOS_CODEX` | sua assinatura |
| `opencode-cli` | OpenCode CLI | — (config do opencode) | `MODELOS_OPENCODE_CLI` | depende do provedor configurado nele |
| `auto` | vários em sequência | — | `ORDEM_PROVEDORES_AUTO` | — |

`MODELOS_*` é uma lista separada por vírgula, **em ordem de fallback**: se o primeiro modelo der erro ou estiver
lotado, o agente tenta o próximo. Nas CLIs, `padrao` significa "o modelo que a CLI já usa".

No modo `auto`, o agente tenta os provedores na ordem de `ORDEM_PROVEDORES_AUTO`, só entre os disponíveis (com
chave, CLI instalada ou Ollama rodando). Se a cota diária de um acabar ou a chave for recusada, esse provedor sai
da fila e o agente passa pro próximo.

---

## Como abrir o projeto: o atalho `cinedata`
Não precisa ativar o `.venv` nem estar na pasta certa: o `cinedata.cmd` usa o Python do ambiente virtual sozinho
(e cria o `.venv` se ainda não existir).

```powershell
cd C:\Users\melt9\Documents\Codes\AtvGenAi\cinedata-agent
.\cinedata web                          # interface web
.\cinedata web --provedor agy           # interface web já usando o agy
.\cinedata chat --provedor claude-code  # chat no terminal
.\cinedata chat "Quais são os 5 filmes mais populares?"
.\cinedata provedores                   # o que está disponível
.\cinedata testar --provedor google     # testa os modelos de um provedor
.\cinedata ollama                       # inicia o Ollama
.\cinedata                              # lista todos os comandos
```
Estando na pasta de cima (`AtvGenAi`), use `.\cinedata-agent\cinedata web`.

---

## Três jeitos de configurar (escolha um)

### 1. Pela interface web (mais fácil)
```powershell
streamlit run app.py
```
Na barra lateral, escolha a tela **⚙️ Modelos e chaves**. Para cada provedor dá pra:
- colar a chave e clicar em **Salvar**: vai pro `.env`, mostrado mascarado (`sk-or-…abcd`);
- editar a lista de modelos e clicar em **Salvar modelos**;
- clicar em **Listar modelos da conta**, que pergunta ao provedor quais modelos a sua chave enxerga, e escolher
  da lista;
- clicar em **Testar**, que faz 1 chamada e diz se o modelo sabe usar a ferramenta `executar_sql`.

No topo da tela também ficam o **provedor padrão** e a **ordem do modo auto**. No chat, o seletor
**Provedor do LLM** da barra lateral troca o provedor na hora, sem perder a conversa.

### 2. Editando o `.env`
Abra `cinedata-agent\.env` e preencha as linhas `..._API_KEY=`. O modelo completo, com o link para gerar cada
chave, está em `.env.example`.

### 3. Pela linha de comando (só para aquela execução)
```powershell
python main.py --provedor agy                          # usa o agy
python main.py --provedor claude-code --modelo sonnet  # Claude Code com um modelo específico
python main.py --provedor google --modelo gemini-3.8-flash
python avaliacao\avaliar.py --provedor codex --sem-cache --ids fin_01
```

---

## Onde pegar cada chave

| Provedor | Onde | Observação |
|---|---|---|
| OpenRouter | https://openrouter.ai/keys | começa com `sk-or-v1-` |
| NVIDIA | https://build.nvidia.com → "Get API Key" | começa com `nvapi-` |
| Google Gemini | https://aistudio.google.com/apikey | plano gratuito para os modelos flash |
| OpenCode Zen | https://opencode.ai/auth | modelos com sufixo `-free` não cobram |
| Anthropic | https://platform.claude.com/settings/keys | cobra por uso |
| OpenAI | https://platform.openai.com/api-keys | cobra por uso |

## As CLIs (agy, claude, codex, opencode)

Não usam chave: aproveitam o login que você já fez em cada uma. Se ainda não fez:

| CLI | Login | Ver modelos |
|---|---|---|
| Antigravity (`agy`) | rode `agy` uma vez e entre com a conta Google | `agy models` |
| Claude Code (`claude`) | rode `claude` e use `/login` | aliases `opus`, `sonnet`, `haiku` |
| Codex (`codex`) | `codex login` | ex.: `gpt-5.6-terra` (o padrão da CLI) |
| OpenCode (`opencode`) | `opencode auth login` | `opencode models` (formato `provedor/modelo`) |

**Como funciona por baixo:** a CLI não tem *tool calling* pela linha de comando, então o agente manda um prompt
explicando o protocolo `<tool_call>{"name": ..., "arguments": ...}</tool_call>` e converte a resposta em uma chamada
de ferramenta de verdade (`cli_llm.py`). Por segurança, cada chamada roda numa pasta temporária vazia:
- Claude Code: `--tools ""` (todas as ferramentas desligadas);
- Codex: `--sandbox read-only`;
- OpenCode: agente `plan` (sem edição);
- agy: `--mode plan`.

Cada chamada leva uns 10-30 s por causa da inicialização da CLI.

No teste feito em 05/10/2026, todas responderam corretamente "quantos gêneros existem e qual tem mais filmes":
agy em 28 s, Claude Code em 14 s, Codex em 20 s e OpenCode em 29 s.

### Rodar o projeto de dentro do agy (Antigravity CLI)
O `agy` também é um agente de terminal. Dá pra abrir o projeto nele e pedir pra ele rodar as coisas:
```powershell
cd C:\Users\melt9\Documents\Codes\AtvGenAi\cinedata-agent
agy
```
E dentro dele, por exemplo: *"rode `.venv\Scripts\python.exe main.py --provedor agy "Quais são os 5 filmes mais
populares?"`"*. Ou direto, sem entrar no modo interativo:
```powershell
agy -p="Rode .venv\Scripts\python.exe main.py --listar-provedores e me diga quais provedores estão disponíveis"
```

## Ollama (modelo local)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\iniciar_ollama.ps1
```
O script acha o Ollama instalado **ou** a versão portátil em `..\ollama-portable`, sobe o servidor com contexto de
8k tokens e baixa o modelo do `.env` se ainda não tiver. Para parar: `Get-Process ollama* | Stop-Process`.

## Comandos úteis
```powershell
.\.venv\Scripts\Activate.ps1                    # SEMPRE antes (senão dá "No module named ...")
python main.py --listar-provedores              # status de todos os provedores
python scripts\testar_provedores.py --listar    # status + modelos configurados
python scripts\testar_provedores.py --provedor google   # testa tool calling (1 chamada por modelo)
python scripts\verificar_cota.py                # cota do OpenRouter (não gasta cota)
python scripts\listar_modelos_free.py           # modelos grátis do OpenRouter com tool calling
streamlit run app.py                            # interface web
python main.py --provedor auto --mostrar-sql    # chat no terminal
pytest                                          # testes (não gastam cota)
```

## VS Code / Antigravity IDE (opcional)
A pasta `.vscode/` traz configurações prontas, que funcionam em qualquer editor baseado no VS Code:
- na aba **Run and Debug** (`Ctrl+Shift+D`) há uma configuração "Web (Streamlit) - …" e uma "Terminal - chat …"
  para cada provedor;
- em **Terminal → Run Task** há as *tasks*: iniciar/parar Ollama, chat, listar/testar provedores e testes.

## Dicas
- O cache de respostas é compartilhado entre provedores. Para comparar provedores na mesma pergunta, desligue
  "Usar cache de respostas" ou use `--sem-cache`.
- A legenda de cada resposta mostra quem respondeu, no formato `provedor:modelo` (ex.: `agy:gemini-3.8-flash-medium`).
- O `.env` está no `.gitignore`, então as chaves nunca vão pro GitHub.
