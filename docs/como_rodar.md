# Como rodar: provedores de LLM e Antigravity / VS Code

O agente funciona com **quatro opções de provedor**. Todas usam o mesmo código: OpenRouter, NVIDIA e Ollama
falam a API no formato da OpenAI, então só muda a `base_url` e a chave (`cinedata_agent/llm.py`).

| `PROVEDOR_LLM` | O que usa | Precisa de | Limite | Quando usar |
|---|---|---|---|---|
| `openrouter` (padrão) | modelos `:free` do OpenRouter | `OPENROUTER_API_KEY` | 50 req/dia | é o da atividade; foi o avaliado (17/17) |
| `nvidia` | API da NVIDIA (build.nvidia.com) | `NVIDIA_API_KEY` (`nvapi-...`) | créditos/limite por minuto da conta | quando a cota do OpenRouter acabar |
| `ollama` | modelo rodando no seu PC | Ollama instalado ou a pasta `ollama-portable` | nenhum (offline) | testar sem internet e sem gastar cota |
| `auto` | OpenRouter → NVIDIA → Ollama | pelo menos um dos três | — | o mais robusto: se um cai ou a cota acaba, passa pro próximo |

No modo `auto` só entram os provedores disponíveis (com chave configurada; o Ollama só se estiver rodando). Se a
cota diária do OpenRouter acabar, ele sai da fila e as próximas perguntas vão direto pra NVIDIA/Ollama, sem
gastar requisição tentando de novo. A ordem pode ser trocada em `ORDEM_PROVEDORES_AUTO`.

Existem três jeitos de escolher o provedor (do mais fixo pro mais rápido de trocar):
1. no `.env`: `PROVEDOR_LLM=auto`;
2. na linha de comando: `python main.py --provedor nvidia` (também vale pro `avaliacao/avaliar.py`);
3. na interface web: seletor **"Provedor do LLM"** na barra lateral (dá pra trocar no meio da conversa; a memória é mantida).

---

## 1. Configurar a NVIDIA

1. Entre em https://build.nvidia.com e faça login.
2. Abra qualquer modelo (ex.: *nemotron*) e clique em **Get API Key** → **Generate Key**. A chave começa com `nvapi-`.
3. Cole no `.env`:
   ```
   NVIDIA_API_KEY=nvapi-...
   ```
4. Confira quais modelos respondem com *tool calling* (1 chamada por modelo):
   ```powershell
   python scripts\testar_provedores.py --provedor nvidia
   ```
   Se algum aparecer como `SEM TOOL CALL` ou `ERRO`, tire ele da lista `MODELOS_NVIDIA` do `.env`.
   A lista de modelos disponíveis está em https://integrate.api.nvidia.com/v1/models.

Modelos que deixei como padrão: `nvidia/nemotron-3.5-lightning-30b-a3b` (mesma família do modelo que acertou
17/17 pelo OpenRouter), `nvidia/nemotron-3-super-120b-a12b` e `openai/gpt-oss-20b`.

## 2. Configurar o Ollama (modelo local)

O script abaixo acha o Ollama instalado **ou** a versão portátil em `..\ollama-portable`, sobe o servidor com
contexto de 8k tokens e baixa o modelo do `.env` se ainda não tiver:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\iniciar_ollama.ps1
```

Para trocar o modelo: `OLLAMA_MODELO=qwen2.5:3b` no `.env` (aceita vários separados por vírgula). O `qwen2.5:3b`
cabe inteiro numa GPU de 4 GB e responde em ~15-30 s; com GPU maior dá pra usar `qwen3:8b`. Modelos pequenos erram
mais o SQL, então servem mais pra testar o fluxo do que pra responder a banca.

Para parar: `Get-Process ollama* | Stop-Process` (ou a task **Parar Ollama**, abaixo).

## 3. Rodar pelo Antigravity (ou VS Code)

O projeto já vem com a pasta `.vscode/` configurada. O Antigravity é baseado no VS Code e lê esses arquivos.

### Primeira vez
1. **File → Open Folder** e abra a pasta `cinedata-agent`.
2. Se ainda não existir o `.venv`: **Terminal → Run Task… → Instalar dependências (.venv)**.
3. Confirme o interpretador: `Ctrl+Shift+P` → **Python: Select Interpreter** → `.venv\Scripts\python.exe`
   (o `settings.json` já aponta pra ele).
4. Confira o `.env` (chaves do OpenRouter e/ou da NVIDIA).

### Opção A — botão Play / F5 (aba Run and Debug, `Ctrl+Shift+D`)
Escolha uma configuração na lista e aperte **F5**:

| Configuração | O que faz |
|---|---|
| Web (Streamlit) - provedor do .env | abre a interface web em http://localhost:8501 |
| Web (Streamlit) - auto (OpenRouter > NVIDIA > Ollama) | sobe o Ollama antes e usa o modo auto |
| Web (Streamlit) - NVIDIA | interface web usando só a NVIDIA |
| Web (Streamlit) - Ollama local | sobe o Ollama e usa só o modelo local |
| Terminal - chat (provedor do .env / NVIDIA / Ollama local / auto) | chat no terminal integrado, mostrando o SQL |
| Testar provedores (1 chamada por modelo) | diz quais modelos da NVIDIA/Ollama sabem usar tools |
| Avaliação - estimar custo | quantas requisições a avaliação vai gastar |
| Testes (pytest) | roda os testes automatizados |

Dá pra colocar *breakpoints* no código (ex.: em `cinedata_agent/agente.py`, função `_no_agente`) e ver as
mensagens que vão pro LLM.

### Opção B — Tasks (sem depender de extensão)
**Terminal → Run Task…**: `Iniciar Ollama`, `Parar Ollama`, `Web: Streamlit (provedor do .env)`,
`Web: Streamlit (auto)`, `Chat no terminal (auto)`, `Testar provedores`, `Verificar cota do OpenRouter`,
`Testes (pytest)`, `Instalar dependências (.venv)`.

### Opção C — terminal integrado (`` Ctrl+` ``)
```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py                              # web, provedor do .env
python main.py --provedor auto --mostrar-sql      # terminal, modo auto
python main.py --provedor ollama                  # terminal, só local
python main.py --provedor nvidia "Quais são os 5 filmes mais populares?"
python avaliacao\avaliar.py --provedor nvidia --sem-cache --categoria "Elenco e Equipe"
```

## Dicas
- O cache de respostas é compartilhado entre provedores. Pra comparar provedores na mesma pergunta, desligue
  "Usar cache de respostas" na interface ou use `--sem-cache`.
- A legenda de cada resposta mostra quem respondeu no formato `provedor:modelo` (ex.: `nvidia:openai/gpt-oss-20b`).
- `python scripts\testar_provedores.py --provedor todos` testa também o OpenRouter, mas gasta 1 requisição da cota
  diária por modelo.
