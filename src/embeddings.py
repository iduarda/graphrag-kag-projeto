"""
Embeddings de chunks e entidades (Pessoa 3).

Modelo decidido na Fase 1 (docs/modelo_lpg.md): `intfloat/multilingual-e5-base`
- 768 dimensões, similaridade por cosseno (igual aos dois índices vetoriais do schema);
- o E5 exige prefixo: "passage: " ao INDEXAR e "query: " ao BUSCAR;
- vetores normalizados (normalize_embeddings=True).

A Pessoa 4 deve gerar o vetor da pergunta com `E5Embedder.codificar_consulta`, que usa este
mesmo modelo e o prefixo "query: ". Assim indexação e busca nunca ficam fora de sincronia.

Créditos: sentence-transformers (Apache 2.0) e o modelo intfloat/multilingual-e5-base (MIT).
"""

from __future__ import annotations

from typing import Protocol

from src.carregador import Executor, em_lotes

MODELO_PADRAO = "intfloat/multilingual-e5-base"
DIMENSAO = 768
PREFIXO_DOCUMENTO = "passage: "
PREFIXO_CONSULTA = "query: "
TAMANHO_LOTE_EMBEDDING = 32


class Embedder(Protocol):
    dimensao: int

    def codificar_documentos(self, textos: list[str]) -> list[list[float]]: ...
    def codificar_consulta(self, pergunta: str) -> list[float]: ...


class E5Embedder:
    """Modelo real. O import pesado só acontece no primeiro uso."""

    def __init__(self, modelo: str = MODELO_PADRAO):
        self.nome_modelo = modelo
        self.dimensao = DIMENSAO
        self._modelo = None

    def _carregar(self):
        if self._modelo is None:
            from sentence_transformers import SentenceTransformer

            self._modelo = SentenceTransformer(self.nome_modelo)
        return self._modelo

    def _codificar(self, textos: list[str]) -> list[list[float]]:
        vetores = self._carregar().encode(
            textos, batch_size=TAMANHO_LOTE_EMBEDDING, normalize_embeddings=True, show_progress_bar=False
        )
        return [[float(x) for x in v] for v in vetores]

    def codificar_documentos(self, textos: list[str]) -> list[list[float]]:
        return self._codificar([PREFIXO_DOCUMENTO + t for t in textos])

    def codificar_consulta(self, pergunta: str) -> list[float]:
        return self._codificar([PREFIXO_CONSULTA + pergunta])[0]


# ----------------------------------------------------------------------------- textos
def texto_da_entidade(nome: str, descricao: str | None) -> str:
    """O que é embutido de cada entidade: nome + descrição (como definido em docs/modelo_lpg.md)."""
    return f"{nome}. {descricao}" if descricao else nome


# ----------------------------------------------------------------------------- queries
Q_CHUNKS_PENDENTES = """
MATCH (c:Chunk) WHERE $recalcular OR c.embedding IS NULL
RETURN c.id AS id, c.texto AS texto ORDER BY c.id
"""
Q_ENTIDADES_PENDENTES = """
MATCH (e:Entidade) WHERE $recalcular OR e.embedding IS NULL
RETURN e.id AS id, e.nome AS nome, e.descricao AS descricao ORDER BY e.id
"""
Q_GRAVAR_CHUNK = "UNWIND $rows AS row MATCH (n:Chunk {id: row.id}) SET n.embedding = row.embedding"
Q_GRAVAR_ENTIDADE = "UNWIND $rows AS row MATCH (n:Entidade {id: row.id}) SET n.embedding = row.embedding"
Q_DIMENSAO_INDICES = "SHOW VECTOR INDEXES YIELD name, options RETURN name, options"


# ----------------------------------------------------------------------------- gravação
def verificar_dimensao(ex: Executor, embedder: Embedder) -> None:
    """Falha cedo se a dimensão do modelo não bater com a dos índices vetoriais criados pelo schema."""
    if embedder.dimensao != DIMENSAO:
        raise ValueError(
            f"o schema usa {DIMENSAO} dimensões, mas o embedder produz {embedder.dimensao}. "
            "Recrie os índices (schema/schema.cypher) ou troque o modelo."
        )
    for indice in ex.ler(Q_DIMENSAO_INDICES):
        try:
            dim = indice["options"]["indexConfig"]["vector.dimensions"]
        except (KeyError, TypeError):
            continue  # formato inesperado: não bloqueia a carga
        if int(dim) != embedder.dimensao:
            raise ValueError(f"índice {indice['name']} tem {dim} dimensões, o embedder produz {embedder.dimensao}")


def _gravar(ex: Executor, embedder: Embedder, itens: list[dict], textos: list[str], query: str) -> int:
    total = 0
    for lote_itens, lote_textos in zip(em_lotes(itens, TAMANHO_LOTE_EMBEDDING), em_lotes(textos, TAMANHO_LOTE_EMBEDDING)):
        vetores = embedder.codificar_documentos(lote_textos)
        if any(len(v) != embedder.dimensao for v in vetores):
            raise ValueError("embedder devolveu vetor com dimensão diferente da declarada")
        ex.escrever(query, rows=[{"id": i["id"], "embedding": v} for i, v in zip(lote_itens, vetores)])
        total += len(lote_itens)
    return total


def gravar_embeddings_chunks(ex: Executor, embedder: Embedder, recalcular: bool = False) -> int:
    """Só embute os chunks que ainda não têm vetor (reexecutar não gasta processamento à toa)."""
    itens = ex.ler(Q_CHUNKS_PENDENTES, recalcular=recalcular)
    return _gravar(ex, embedder, itens, [i["texto"] for i in itens], Q_GRAVAR_CHUNK)


def gravar_embeddings_entidades(ex: Executor, embedder: Embedder, recalcular: bool = False) -> int:
    itens = ex.ler(Q_ENTIDADES_PENDENTES, recalcular=recalcular)
    return _gravar(ex, embedder, itens, [texto_da_entidade(i["nome"], i["descricao"]) for i in itens], Q_GRAVAR_ENTIDADE)
