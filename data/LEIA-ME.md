# Pasta `data/`

Coloque aqui o arquivo **`cinerocket.db`** (camada Gold do CineData, ~580 MB).
Ele não vai pro GitHub porque passa do limite de 100 MB por arquivo.

Arquivos que aparecem aqui depois de rodar o projeto (também fora do git):

| arquivo | o que é | como gerar |
|---|---|---|
| `cinerocket.db` | banco SQLite da atividade | baixar da pasta compartilhada |
| `indice_sinopses.npz` | embeddings das sinopses (busca semântica) | `python scripts/indexar_sinopses.py` |
| `modelos/` | modelo de embedding baixado do HuggingFace | automático na indexação |
| `cache_respostas.db` | cache das respostas do agente | automático |
