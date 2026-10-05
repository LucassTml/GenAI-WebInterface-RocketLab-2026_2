"""
Lista os modelos :free do OpenRouter que suportam tool calling.

A lista de modelos gratuitos muda bastante (modelo entra e sai toda semana),
então em vez de confiar numa lista fixa eu consulto a API pública de modelos
(não precisa de chave e não gasta cota). Use o resultado pra montar a
variável MODELOS_LLM do .env.

    python scripts/listar_modelos_free.py
"""

import sys

import httpx

URL = "https://openrouter.ai/api/v1/models"

if __name__ == "__main__":
    # console do Windows não é UTF-8 por padrão e quebra os acentos
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    modelos = httpx.get(URL, timeout=30).json()["data"]
    gratis = [
        m for m in modelos
        if (m["id"].endswith(":free") or m["id"] == "openrouter/free")
        and "tools" in (m.get("supported_parameters") or [])
    ]
    gratis.sort(key=lambda m: m.get("created", 0), reverse=True)
    print(f"{len(gratis)} modelos gratuitos com suporte a tools:\n")
    for m in gratis:
        print(f"  {m['id']:<55} contexto: {m.get('context_length', 0):>9,}")
    print("\nExemplo pro .env:")
    print("MODELOS_LLM=" + ",".join(m["id"] for m in gratis[:3]) + ",openrouter/free")
