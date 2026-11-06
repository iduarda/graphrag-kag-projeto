"""Ferramentas compartilhadas pelos testes da ingestão (Pessoa 3)."""

import hashlib
import math

import pytest

from src.contrato import gerar_id
from src.resolucao import Entidade


class ExecutorFalso:
    """Registra as queries em vez de falar com o Neo4j. `respostas` mapeia um trecho da query -> linhas devolvidas."""

    def __init__(self, respostas: dict[str, list[dict]] | None = None):
        self.escritas: list[tuple[str, dict]] = []
        self.autocommits: list[str] = []
        self.leituras: list[tuple[str, dict]] = []
        self.respostas = respostas or {}

    def escrever(self, query, **params):
        self.escritas.append((query, params))

    def autocommit(self, query, **params):
        self.autocommits.append(query)

    def ler(self, query, **params):
        self.leituras.append((query, params))
        for trecho, linhas in self.respostas.items():
            if trecho in query:
                return linhas
        return []


class EmbedderFalso:
    """Vetores determinísticos e normalizados; não baixa modelo nenhum."""

    def __init__(self, dimensao=768):
        self.dimensao = dimensao
        self.textos_vistos: list[str] = []

    def _vetor(self, texto):
        h = hashlib.sha256(texto.encode()).digest()
        v = [(h[i % len(h)] - 127.5) for i in range(self.dimensao)]
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v]

    def codificar_documentos(self, textos):
        self.textos_vistos += textos
        return [self._vetor(t) for t in textos]

    def codificar_consulta(self, pergunta):
        return self._vetor("query: " + pergunta)


def ent(nome, tipo="Organizacao", aliases=(), principal=False, n_docs=1, descricao=None, props=None):
    """Cria uma Entidade já no formato agregado, com id gerado pela regra do contrato."""
    return Entidade(id=gerar_id(tipo, nome), nome=nome, tipo=tipo, aliases=set(aliases), principal=principal,
                    n_docs=n_docs, descricao=descricao, propriedades=dict(props or {}))


def tabela_de(*entidades):
    return {e.id: e for e in entidades}


def doc_minimo(doc_id=1, entidades=(), relacoes=(), chunks=2, principal=None):
    """Um JSON de extração mínimo e válido segundo o contrato."""
    cks = [{"id": f"{doc_id}_{i}", "ordem": i, "texto": f"texto {i} do documento {doc_id}"} for i in range(chunks)]
    return {
        "versao_formato": "1.0",
        "documento": {"id": doc_id, "titulo": f"Doc {doc_id}", "url": f"https://exemplo/{doc_id}",
                      "categoria": "Pessoa", "wikidata_id": None},
        "entidade_principal": principal or entidades[0]["id"],
        "chunks": cks,
        "entidades": list(entidades),
        "relacoes": list(relacoes),
    }


def e_json(nome, tipo, chunk_ids, aliases=(), **props):
    return {"id": gerar_id(tipo, nome), "nome": nome, "tipo": tipo, "aliases": list(aliases),
            "propriedades": props, "chunk_ids": list(chunk_ids)}


def r_json(origem, tipo, destino, chunk_id, confianca=0.9, evidencia="trecho", **props):
    return {"origem": origem, "tipo": tipo, "destino": destino, "propriedades": props,
            "chunk_id": chunk_id, "evidencia": evidencia, "confianca": confianca}


@pytest.fixture
def executor():
    return ExecutorFalso()


@pytest.fixture
def embedder():
    return EmbedderFalso()
