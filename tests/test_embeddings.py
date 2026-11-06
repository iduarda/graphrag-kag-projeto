"""Embeddings: prefixos do E5, dimensão, só pendentes (sem baixar modelo: o modelo é simulado)."""

import math

import pytest

from src import embeddings
from src.embeddings import (DIMENSAO, E5Embedder, gravar_embeddings_chunks, gravar_embeddings_entidades,
                                     texto_da_entidade, verificar_dimensao)
from tests.conftest import EmbedderFalso, ExecutorFalso


class ModeloFalso:
    """Imita sentence_transformers.SentenceTransformer.encode e guarda o que recebeu."""

    def __init__(self):
        self.recebido, self.args = [], {}

    def encode(self, textos, **kw):
        self.recebido += list(textos)
        self.args = kw
        return [[1.0] + [0.0] * (DIMENSAO - 1) for _ in textos]


def _e5():
    e5 = E5Embedder()
    e5._modelo = ModeloFalso()
    return e5, e5._modelo


def test_documentos_levam_o_prefixo_passage():
    e5, modelo = _e5()
    e5.codificar_documentos(["Unix foi criado no Bell Labs"])
    assert modelo.recebido == ["passage: Unix foi criado no Bell Labs"]


def test_consulta_leva_o_prefixo_query():
    e5, modelo = _e5()
    e5.codificar_consulta("Quem criou o C?")
    assert modelo.recebido == ["query: Quem criou o C?"]


def test_vetores_sao_normalizados_pelo_modelo():
    e5, modelo = _e5()
    e5.codificar_documentos(["a"])
    assert modelo.args["normalize_embeddings"] is True


def test_dimensao_do_modelo_bate_com_o_schema():
    assert E5Embedder().dimensao == DIMENSAO == 768


def test_texto_da_entidade():
    assert texto_da_entidade("Unix", "Sistema operacional.") == "Unix. Sistema operacional."
    assert texto_da_entidade("Unix", None) == "Unix"


def test_so_grava_o_que_ainda_nao_tem_embedding():
    pendentes = [{"id": "1_0", "texto": "a"}, {"id": "1_1", "texto": "b"}]
    ex = ExecutorFalso({"c.embedding IS NULL": pendentes})
    emb = EmbedderFalso()
    assert gravar_embeddings_chunks(ex, emb) == 2
    assert emb.textos_vistos == ["a", "b"]
    linhas = [l for _, p in ex.escritas for l in p["rows"]]
    assert [l["id"] for l in linhas] == ["1_0", "1_1"]
    assert all(len(l["embedding"]) == 768 for l in linhas)
    assert all(math.isclose(sum(x * x for x in l["embedding"]), 1.0, rel_tol=1e-6) for l in linhas)


def test_recalcular_e_repassado_para_a_query():
    ex = ExecutorFalso()
    gravar_embeddings_chunks(ex, EmbedderFalso(), recalcular=True)
    assert ex.leituras[0][1] == {"recalcular": True}


def test_sem_pendentes_nao_escreve_nada():
    ex = ExecutorFalso()
    assert gravar_embeddings_chunks(ex, EmbedderFalso()) == 0 and ex.escritas == []


def test_entidades_sao_embutidas_com_nome_e_descricao():
    ex = ExecutorFalso({"e.embedding IS NULL": [{"id": "x", "nome": "Unix", "descricao": "Um SO."}]})
    emb = EmbedderFalso()
    gravar_embeddings_entidades(ex, emb)
    assert emb.textos_vistos == ["Unix. Um SO."]


def test_vetor_com_dimensao_errada_e_recusado():
    class Ruim(EmbedderFalso):
        def codificar_documentos(self, textos):
            return [[0.1, 0.2] for _ in textos]

    ex = ExecutorFalso({"c.embedding IS NULL": [{"id": "1_0", "texto": "a"}]})
    with pytest.raises(ValueError):
        gravar_embeddings_chunks(ex, Ruim())


def test_modelo_com_dimensao_diferente_do_schema_e_recusado():
    with pytest.raises(ValueError, match="768"):
        verificar_dimensao(ExecutorFalso(), EmbedderFalso(dimensao=384))


def test_indice_vetorial_com_dimensao_diferente_e_recusado():
    ex = ExecutorFalso({"SHOW VECTOR INDEXES": [{"name": "chunk_embedding",
                                                 "options": {"indexConfig": {"vector.dimensions": 384}}}]})
    with pytest.raises(ValueError, match="chunk_embedding"):
        verificar_dimensao(ex, EmbedderFalso())


def test_indice_vetorial_correto_passa():
    ex = ExecutorFalso({"SHOW VECTOR INDEXES": [{"name": "chunk_embedding",
                                                 "options": {"indexConfig": {"vector.dimensions": 768}}}]})
    verificar_dimensao(ex, EmbedderFalso())
