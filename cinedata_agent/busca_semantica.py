"""
Busca semântica nas sinopses (a parte "híbrida" do agente).

SQL é ótimo pra número, mas péssimo pra pergunta do tipo "quais filmes falam
sobre viagem no tempo?". Um LIKE '%time travel%' perde todos os filmes que
descrevem a mesma ideia com outras palavras. Por isso gerei embeddings das
sinopses e faço busca por similaridade de cosseno.

Como funciona:
1. scripts/indexar_sinopses.py lê titulo + sinopse de todos os filmes que
   têm sinopse (ignora os 'Sem descrição'), gera um vetor para cada um e
   salva tudo num .npz (ids + matriz de vetores normalizados).
2. Na hora da pergunta, gero o vetor da descrição e faço um produto escalar
   com a matriz inteira. São ~82 mil vetores, então numpy resolve em
   milissegundos e não precisei de banco vetorial (FAISS, Chroma...).
3. Os filtros (ano, gênero, nota mínima) são aplicados via SQL na camada Gold.
   Isso é o "híbrido": semântica pra achar o tema + SQL pra filtrar/enriquecer.

Biblioteca: fastembed (roda em ONNX na CPU, não precisa de PyTorch).

Modelo: testei 3 opções com 3 mil sinopses (ver docs/decisoes_tecnicas.md):
  - potion-multilingual-128M: rapidíssimo (~1300 docs/s), mas com pergunta
    em português trazia filme nada a ver;
  - bge-small-en-v1.5: bom em inglês, péssimo com pergunta em português;
  - paraphrase-multilingual-MiniLM-L12-v2: ~40 docs/s no meu notebook, mas
    "tubarão gigante ataca pessoas" trouxe The Meg e Sky Sharks. Ficou esse,
    porque as sinopses estão em inglês e as perguntas em português.
"""

import os
import sqlite3
from pathlib import Path

import numpy as np

from . import config

# o fastembed usa o huggingface_hub pra baixar o modelo; no Windows sem modo
# desenvolvedor ele fica dando warning de symlink a cada download
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


class IndiceIndisponivel(Exception):
    pass


MAX_TOKENS = 128  # o MiniLM multilíngue foi treinado com no máximo 128 tokens


def _carregar_modelo(nome: str):
    from fastembed import TextEmbedding  # import aqui pq é pesado e é opcional

    modelo = TextEmbedding(nome, cache_dir=str(config.PASTA_MODELOS_EMBEDDING))
    try:
        # por padrão o fastembed deixa até 512 tokens; cortar em 128 deixa
        # mais rápido e é o limite que o modelo viu no treino mesmo
        modelo.model.tokenizer.enable_truncation(max_length=MAX_TOKENS)
    except AttributeError:
        pass  # se a versão do fastembed mudar a estrutura interna, segue sem truncar
    return modelo


def _normalizar(matriz: np.ndarray) -> np.ndarray:
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return matriz / normas


def construir_indice(
    db_path: Path | None = None,
    saida: Path | None = None,
    modelo: str | None = None,
    limite: int | None = None,
    tamanho_lote: int = 32,
) -> int:
    """Gera o índice de embeddings. Retorna quantos filmes foram indexados.
    No meu notebook levou 52 min para 81.785 sinopses (só precisa rodar uma vez)."""
    from tqdm import tqdm

    db_path = Path(db_path or config.DB_PATH)
    saida = Path(saida or config.INDICE_SINOPSES_PATH)
    modelo = modelo or config.MODELO_EMBEDDING

    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    sql = """
        SELECT sk_movie_id, titulo, sinopse
        FROM dim_movies
        WHERE sinopse IS NOT NULL
          AND sinopse <> 'Sem descrição'
          AND length(sinopse) >= 20
    """
    if limite:
        sql += f" LIMIT {int(limite)}"
    linhas = conn.execute(sql).fetchall()
    conn.close()

    # junto o título com a sinopse: o título às vezes carrega o tema
    # ("Sharknado", "Time Trap") e ajuda bastante a busca
    textos = [f"{titulo}. {sinopse}" for _, titulo, sinopse in linhas]

    # Truque que achei testando: ordenar os textos por tamanho antes de montar
    # os lotes. Cada lote é preenchido (padding) até o tamanho do maior texto,
    # então misturar sinopse curta com longa desperdiça muita conta.
    # Junto com o corte em 128 tokens, passou de ~29 para ~40 docs/s no meu teste.
    ordem = sorted(range(len(textos)), key=lambda i: len(textos[i]))
    ids = [linhas[i][0] for i in ordem]
    textos = [textos[i] for i in ordem]

    emb_model = _carregar_modelo(modelo)
    vetores = []
    for i in tqdm(range(0, len(textos), tamanho_lote), desc="Gerando embeddings", unit="lote"):
        lote = textos[i : i + tamanho_lote]
        vetores.extend(emb_model.embed(lote, batch_size=tamanho_lote))

    matriz = _normalizar(np.asarray(vetores, dtype=np.float32))
    saida.parent.mkdir(parents=True, exist_ok=True)
    # float16 corta o arquivo pela metade e a perda de precisão não muda o ranking
    np.savez_compressed(saida, ids=np.array(ids), vetores=matriz.astype(np.float16), modelo=np.array(modelo))
    return len(ids)


class IndiceSinopses:
    """Carrega o índice uma vez só e reaproveita (singleton simples)."""

    _instancia = None

    def __init__(self, caminho: Path):
        if not caminho.exists():
            raise IndiceIndisponivel(
                "O índice de sinopses ainda não foi gerado. Rode: python scripts/indexar_sinopses.py"
            )
        dados = np.load(caminho, allow_pickle=False)
        self.ids = dados["ids"]
        self.vetores = dados["vetores"].astype(np.float32)
        self.nome_modelo = str(dados["modelo"])
        self._modelo = None

    @classmethod
    def obter(cls) -> "IndiceSinopses":
        if cls._instancia is None:
            cls._instancia = cls(Path(config.INDICE_SINOPSES_PATH))
        return cls._instancia

    def _vetor_consulta(self, texto: str) -> np.ndarray:
        if self._modelo is None:
            self._modelo = _carregar_modelo(self.nome_modelo)
        vetor = np.asarray(list(self._modelo.embed([texto]))[0], dtype=np.float32)
        return vetor / (np.linalg.norm(vetor) or 1.0)

    def buscar(self, texto: str, k: int = 10, ids_permitidos: set[str] | None = None) -> list[tuple[str, float]]:
        """Retorna [(sk_movie_id, similaridade)] dos k mais parecidos."""
        scores = self.vetores @ self._vetor_consulta(texto)
        if ids_permitidos is not None:
            mascara = np.isin(self.ids, list(ids_permitidos))
            scores = np.where(mascara, scores, -np.inf)
        k = min(k, len(scores))
        # argpartition é bem mais rápido que ordenar o vetor inteiro
        topo = np.argpartition(-scores, k - 1)[:k]
        topo = topo[np.argsort(-scores[topo])]
        return [(str(self.ids[i]), float(scores[i])) for i in topo if np.isfinite(scores[i])]


def indice_disponivel() -> bool:
    return Path(config.INDICE_SINOPSES_PATH).exists()
