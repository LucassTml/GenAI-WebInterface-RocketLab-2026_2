"""
Catálogo de provedores de LLM.

Cada provedor diz: como conectar (API compatível com OpenAI, SDK da
Anthropic, Ollama local ou CLI instalada no PC), em qual variável do .env fica
a chave e em qual fica a lista de modelos.

As funções leem o os.environ NA HORA (e não na importação), porque a tela de
configuração da interface web grava chave/modelos no .env e no ambiente, e eu
queria que valesse sem precisar reiniciar o programa.
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import set_key

from . import config

ARQUIVO_ENV = config.RAIZ / ".env"


SELO_BETA = "🧪 beta"
AVISO_BETA = (
    "🧪 **Provedor em beta.** Só o OpenRouter passou pela avaliação completa do projeto (17/17). Este provedor "
    "funcionou nos testes rápidos, mas ainda pode errar o SQL, demorar ou falhar. Se der problema, volte para o "
    "OpenRouter."
)


@dataclass(frozen=True)
class Provedor:
    id: str
    nome: str
    tipo: str                 # "openai" (API compatível), "anthropic", "ollama" ou "cli"
    var_modelos: str
    modelos_padrao: str
    descricao: str
    var_chave: str | None = None
    base_url: str | None = None
    site_chave: str | None = None
    comando_cli: str | None = None
    usa_temperatura: bool = True  # modelos de raciocínio recusam temperature != padrão
    # Só o OpenRouter foi avaliado de verdade (17/17). Os outros funcionaram no
    # teste rápido, mas ainda não passaram pela avaliação completa: ficam "beta".
    beta: bool = True

    @property
    def nome_com_selo(self) -> str:
        return f"{self.nome} {SELO_BETA}" if self.beta else self.nome

    @property
    def eh_cli(self) -> bool:
        return self.tipo == "cli"


PROVEDORES: dict[str, Provedor] = {p.id: p for p in [
    # ---------------------------------------------------------- APIs
    Provedor(
        "openrouter", "OpenRouter (modelos :free)", "openai", "MODELOS_LLM",
        "nvidia/nemotron-3.5-lightning:free,qwen/qwen3.8-27b:free,google/gemma-4-31b-it:free,openrouter/free",
        "Gratuito, 50 requisições/dia. Foi o provedor usado na avaliação (17/17).",
        var_chave="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1",
        site_chave="https://openrouter.ai/keys", beta=False,
    ),
    Provedor(
        "nvidia", "NVIDIA (build.nvidia.com)", "openai", "MODELOS_NVIDIA",
        "nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-super-120b-a12b,openai/gpt-oss-20b",
        "Créditos gratuitos com limite por minuto. Chave começa com nvapi-.",
        var_chave="NVIDIA_API_KEY", base_url="https://integrate.api.nvidia.com/v1",
        site_chave="https://build.nvidia.com",
    ),
    Provedor(
        "google", "Google Gemini (AI Studio)", "openai", "MODELOS_GOOGLE",
        "gemini-flash-latest,gemini-3.8-flash,gemini-3.5-flash-lite",
        "Plano gratuito do Gemini API (modelos flash). Usa o endpoint compatível com OpenAI do Google.",
        var_chave="GOOGLE_API_KEY", base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        site_chave="https://aistudio.google.com/apikey",
    ),
    Provedor(
        "opencode", "OpenCode Zen (API)", "openai", "MODELOS_OPENCODE",
        "nemotron-3.5-lightning-free,deepseek-v4-flash-free,fledge-alpha-free",
        "Gateway do OpenCode. Os modelos com sufixo -free não cobram (precisa de conta/chave).",
        var_chave="OPENCODE_API_KEY", base_url="https://opencode.ai/zen/v1",
        site_chave="https://opencode.ai/auth", usa_temperatura=False,
    ),
    Provedor(
        "anthropic", "Anthropic (API do Claude)", "anthropic", "MODELOS_ANTHROPIC",
        "claude-opus-5-5",
        "Pago por uso (créditos da conta). Usa o SDK oficial da Anthropic via langchain-anthropic.",
        var_chave="ANTHROPIC_API_KEY", site_chave="https://platform.claude.com/settings/keys",
        usa_temperatura=False,
    ),
    Provedor(
        "openai", "OpenAI (API)", "openai", "MODELOS_OPENAI",
        "gpt-5.6-terra,gpt-5.4-mini",
        "Pago por uso. É a mesma família de modelos que o Codex usa.",
        var_chave="OPENAI_API_KEY", base_url="https://api.openai.com/v1",
        site_chave="https://platform.openai.com/api-keys", usa_temperatura=False,
    ),
    Provedor(
        "ollama", "Ollama (modelo local)", "ollama", "OLLAMA_MODELO",
        "qwen2.5:3b",
        "Roda no seu PC, sem internet e sem cota. Inicie com scripts/iniciar_ollama.ps1.",
    ),
    # ---------------------------------------------------------- CLIs
    # Usam o login que você já tem em cada ferramenta (assinatura), sem chave
    # no .env. Modelo "padrao" = o modelo padrão configurado na própria CLI.
    Provedor(
        "agy", "Antigravity CLI (agy)", "cli", "MODELOS_AGY",
        "gemini-3.8-flash-medium",
        "Usa o login do Google Antigravity. Liste os modelos com: agy models",
        comando_cli="agy", usa_temperatura=False,
    ),
    Provedor(
        "claude-code", "Claude Code CLI (claude)", "cli", "MODELOS_CLAUDE_CODE",
        "padrao",
        "Usa o login do Claude Code. Modelos: padrao, opus, sonnet, haiku ou o ID completo.",
        comando_cli="claude", usa_temperatura=False,
    ),
    Provedor(
        "codex", "Codex CLI (codex)", "cli", "MODELOS_CODEX",
        "padrao",
        "Usa o login do Codex (ChatGPT). Modelo padrão da CLI ou ex.: gpt-5.6-terra.",
        comando_cli="codex", usa_temperatura=False,
    ),
    Provedor(
        "opencode-cli", "OpenCode CLI (opencode)", "cli", "MODELOS_OPENCODE_CLI",
        "padrao",
        "Usa o login/config do OpenCode. Modelo no formato provedor/modelo (ex.: opencode/big-pickle).",
        comando_cli="opencode", usa_temperatura=False,
    ),
]}

IDS = list(PROVEDORES)
VALIDOS = (*IDS, "auto")


def obter(provedor_id: str) -> Provedor:
    try:
        return PROVEDORES[provedor_id]
    except KeyError:
        raise ValueError(f"Provedor '{provedor_id}' não existe. Use um de: {', '.join(VALIDOS)}.") from None


def chave(provedor_id: str) -> str:
    p = obter(provedor_id)
    if not p.var_chave:
        return ""
    valor = os.getenv(p.var_chave, "").strip()
    return "" if "COLE_SUA_CHAVE" in valor else valor


def tem_chave(provedor_id: str) -> bool:
    p = obter(provedor_id)
    return True if not p.var_chave else bool(chave(provedor_id))


def modelos(provedor_id: str) -> list[str]:
    p = obter(provedor_id)
    texto = os.getenv(p.var_modelos) or p.modelos_padrao
    return [m.strip() for m in texto.split(",") if m.strip()]


def caminho_cli(provedor_id: str) -> str | None:
    p = obter(provedor_id)
    return shutil.which(p.comando_cli) if p.comando_cli else None


def ordem_auto() -> list[str]:
    texto = os.getenv("ORDEM_PROVEDORES_AUTO", "openrouter,nvidia,google,opencode,ollama")
    return [p.strip() for p in texto.split(",") if p.strip() in PROVEDORES]


def mascarar(valor: str) -> str:
    return f"{valor[:6]}…{valor[-4:]}" if len(valor) > 12 else "****"


def salvar_no_env(variavel: str, valor: str) -> None:
    """Grava no .env (cria se não existir) e já aplica no processo atual."""
    Path(ARQUIVO_ENV).touch(exist_ok=True)
    set_key(str(ARQUIVO_ENV), variavel, valor, quote_mode="never")
    os.environ[variavel] = valor


def eh_beta(provedor_id: str) -> bool:
    """O modo auto também conta como beta, porque pode cair num provedor beta."""
    return provedor_id == "auto" or obter(provedor_id).beta
