"""
Carga no Neo4j (Pessoa 3).

Escreve o grafo descrito em docs/modelo_lpg.md:
  camada de procedência:  (:Documento)-[:TEM_CHUNK]->(:Chunk)-[:PROXIMO]->(:Chunk)
  camada de conhecimento: (:Entidade:<Tipo>) e as relações entre elas
  ligações:               (:Chunk)-[:MENCIONA]->(:Entidade), (:Documento)-[:SOBRE]->(:Entidade)

Princípios:
- IDEMPOTENTE: tudo usa MERGE pela chave das constraints; rodar duas vezes não duplica nada.
- EM LOTE: UNWIND com `TAMANHO_LOTE` linhas por transação (nada de uma query por nó).
- SEGURO: rótulos e tipos de relação não podem ser parâmetros em Cypher, então são inseridos
  no texto da query SOMENTE depois de checados contra listas fixas (contrato.py).

O carregador não conhece o driver: recebe um "executor" com os métodos `escrever`, `autocommit`
e `ler`. `ExecutorNeo4j` é a versão real; os testes usam um executor falso.

Créditos: driver oficial `neo4j` (Apache 2.0).
"""

from __future__ import annotations

from typing import Iterable, Iterator, Protocol

from src.contrato import TIPOS_ENTIDADE, TIPOS_RELACAO

TAMANHO_LOTE = 500


# ----------------------------------------------------------------------------- executor
class Executor(Protocol):
    def escrever(self, query: str, **params) -> None: ...
    def autocommit(self, query: str, **params) -> None: ...
    def ler(self, query: str, **params) -> list[dict]: ...


class ExecutorNeo4j:
    """Executor real, sobre o driver oficial."""

    def __init__(self, uri: str, usuario: str, senha: str, banco: str | None = None):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(usuario, senha))
        self.banco = banco
        self.driver.verify_connectivity()

    def _sessao(self):
        return self.driver.session(database=self.banco) if self.banco else self.driver.session()

    def escrever(self, query: str, **params) -> None:
        with self._sessao() as s:
            s.execute_write(lambda tx: tx.run(query, **params).consume())

    def autocommit(self, query: str, **params) -> None:
        """Para comandos que não podem rodar dentro de uma transação explícita (CALL {} IN TRANSACTIONS)."""
        with self._sessao() as s:
            s.run(query, **params).consume()

    def ler(self, query: str, **params) -> list[dict]:
        with self._sessao() as s:
            return s.execute_read(lambda tx: [r.data() for r in tx.run(query, **params)])

    def fechar(self) -> None:
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()


def em_lotes(linhas: list, tamanho: int = TAMANHO_LOTE) -> Iterator[list]:
    for i in range(0, len(linhas), tamanho):
        yield linhas[i : i + tamanho]


# ----------------------------------------------------------------------------- queries
Q_DOCUMENTO = """
UNWIND $rows AS row
MERGE (d:Documento {id: row.id})
SET d.titulo = row.titulo, d.url = row.url, d.categoria = row.categoria,
    d.wikidata_id = row.wikidata_id, d.semente = row.semente,
    d.data_coleta = row.data_coleta, d.licenca = row.licenca
"""

Q_CHUNK = """
UNWIND $rows AS row
MERGE (c:Chunk {id: row.id})
SET c.texto = row.texto, c.ordem = row.ordem
WITH c, row
MATCH (d:Documento {id: row.documento_id})
MERGE (d)-[:TEM_CHUNK]->(c)
"""

Q_PROXIMO = """
UNWIND $rows AS row
MATCH (a:Chunk {id: row.de}), (b:Chunk {id: row.para})
MERGE (a)-[:PROXIMO]->(b)
"""

Q_ENTIDADE = """
UNWIND $rows AS row
MERGE (e:Entidade {{id: row.id}})
SET e:`{rotulo}`
SET e.nome = row.nome, e.aliases = row.aliases, e.descricao = row.descricao,
    e.wikidata_id = row.wikidata_id
SET e += row.props
"""

Q_MENCIONA = """
UNWIND $rows AS row
MATCH (c:Chunk {id: row.chunk_id})
MATCH (e:Entidade {id: row.entidade_id})
MERGE (c)-[:MENCIONA]->(e)
"""

Q_SOBRE = """
UNWIND $rows AS row
MATCH (d:Documento {id: row.documento_id})
MATCH (e:Entidade {id: row.entidade_id})
MERGE (d)-[:SOBRE]->(e)
"""

Q_RELACAO = """
UNWIND $rows AS row
MATCH (a:Entidade {{id: row.origem}}), (b:Entidade {{id: row.destino}})
MERGE (a)-[r:`{tipo}`]->(b)
SET r.chunk_ids = row.chunk_ids, r.confianca = row.confianca, r.evidencia = row.evidencia
SET r += row.props
"""

Q_LIMPAR = "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS"


def query_entidade(tipo: str) -> str:
    if tipo not in TIPOS_ENTIDADE:  # nunca monte um rótulo a partir de dado não conferido
        raise ValueError(f"tipo de entidade inválido: {tipo!r}")
    return Q_ENTIDADE.format(rotulo=tipo)


def query_relacao(tipo: str) -> str:
    if tipo not in TIPOS_RELACAO:
        raise ValueError(f"tipo de relação inválido: {tipo!r}")
    return Q_RELACAO.format(tipo=tipo)


# ----------------------------------------------------------------------------- montagem das linhas
def linhas_documento(doc: dict, extra: dict | None = None) -> dict:
    """`extra` vem de data/raw/ (semente, data_coleta, licenca), que o JSON da extração não traz."""
    d = doc["documento"]
    extra = extra or {}
    return {
        "id": d["id"], "titulo": d["titulo"], "url": d["url"], "categoria": d["categoria"],
        "wikidata_id": d.get("wikidata_id"), "semente": extra.get("semente"),
        "data_coleta": extra.get("data_coleta"), "licenca": extra.get("licenca"),
    }


def linhas_chunks(doc: dict) -> tuple[list[dict], list[dict]]:
    """Chunks do documento e os pares (chunk -> próximo chunk) na ordem de leitura."""
    did = doc["documento"]["id"]
    chunks = sorted(doc["chunks"], key=lambda c: c["ordem"])
    nos = [{"id": c["id"], "texto": c["texto"], "ordem": c["ordem"], "documento_id": did} for c in chunks]
    pares = [{"de": a["id"], "para": b["id"]} for a, b in zip(chunks, chunks[1:])]
    return nos, pares


def linhas_entidade(e) -> dict:
    return {
        "id": e.id, "nome": e.nome, "aliases": sorted(e.aliases), "descricao": e.descricao,
        "wikidata_id": e.wikidata_id, "props": dict(e.propriedades),
    }


def linhas_mencoes(docs: list[dict], mapa: dict[str, str]) -> list[dict]:
    """(chunk, entidade canônica), sem repetição. Duas entidades fundidas no mesmo chunk viram uma aresta."""
    vistos: set[tuple[str, str]] = set()
    for doc in docs:
        for e in doc["entidades"]:
            for cid in e["chunk_ids"]:
                vistos.add((cid, mapa.get(e["id"], e["id"])))
    return [{"chunk_id": c, "entidade_id": e} for c, e in sorted(vistos)]


def linhas_sobre(docs: list[dict], mapa: dict[str, str]) -> list[dict]:
    return [
        {"documento_id": d["documento"]["id"], "entidade_id": mapa.get(d["entidade_principal"], d["entidade_principal"])}
        for d in docs
    ]


# ----------------------------------------------------------------------------- carga
def limpar_grafo(ex: Executor) -> None:
    ex.autocommit(Q_LIMPAR)


def _enviar(ex: Executor, query: str, linhas: Iterable[dict]) -> int:
    linhas = list(linhas)
    for lote in em_lotes(linhas):
        ex.escrever(query, rows=lote)
    return len(linhas)


def carregar(ex: Executor, docs: list[dict], entidades: dict, mapa: dict[str, str], relacoes: list[dict],
             extras_docs: dict[int, dict] | None = None) -> dict[str, int]:
    """
    Ordem importa: documentos -> chunks -> entidades -> ligações. Devolve quantas linhas foram enviadas
    em cada etapa (o grafo final pode ter menos, porque MERGE junta repetidos).
    """
    extras_docs = extras_docs or {}
    enviados: dict[str, int] = {}

    enviados["documentos"] = _enviar(
        ex, Q_DOCUMENTO, (linhas_documento(d, extras_docs.get(d["documento"]["id"])) for d in docs)
    )

    todos_chunks, todos_pares = [], []
    for d in docs:
        nos, pares = linhas_chunks(d)
        todos_chunks += nos
        todos_pares += pares
    enviados["chunks"] = _enviar(ex, Q_CHUNK, todos_chunks)
    enviados["proximo"] = _enviar(ex, Q_PROXIMO, todos_pares)

    por_tipo: dict[str, list[dict]] = {}
    for e in entidades.values():
        por_tipo.setdefault(e.tipo, []).append(linhas_entidade(e))
    enviados["entidades"] = 0
    for tipo in sorted(por_tipo):
        enviados["entidades"] += _enviar(ex, query_entidade(tipo), por_tipo[tipo])

    enviados["menciona"] = _enviar(ex, Q_MENCIONA, linhas_mencoes(docs, mapa))
    enviados["sobre"] = _enviar(ex, Q_SOBRE, linhas_sobre(docs, mapa))

    por_rel: dict[str, list[dict]] = {}
    for r in relacoes:
        por_rel.setdefault(r["tipo"], []).append(
            {"origem": r["origem"], "destino": r["destino"], "chunk_ids": r["chunk_ids"],
             "confianca": r["confianca"], "evidencia": r["evidencia"], "props": r["propriedades"]}
        )
    enviados["relacoes"] = 0
    for tipo in sorted(por_rel):
        enviados["relacoes"] += _enviar(ex, query_relacao(tipo), por_rel[tipo])
    return enviados


# ----------------------------------------------------------------------------- conferência
def contar_grafo(ex: Executor) -> dict:
    """Contagens reais do banco (para o relatório e para os testes de idempotência)."""
    nos = ex.ler("MATCH (n) UNWIND labels(n) AS rotulo RETURN rotulo, count(*) AS n ORDER BY rotulo")
    rels = ex.ler("MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) AS n ORDER BY tipo")
    return {"nos": {r["rotulo"]: r["n"] for r in nos}, "relacoes": {r["tipo"]: r["n"] for r in rels}}
