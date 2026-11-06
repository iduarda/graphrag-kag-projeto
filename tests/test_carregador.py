"""Carregador: lotes, ordem, segurança das queries e idempotência (sem banco, com executor falso)."""

import pytest

from src import carregador
from src.carregador import (TAMANHO_LOTE, carregar, linhas_chunks, linhas_mencoes, linhas_sobre,
                                     query_entidade, query_relacao)
from src.resolucao import agregar_entidades, remapear_relacoes, resolver
from tests.conftest import ExecutorFalso, doc_minimo, e_json, r_json


def _docs_exemplo():
    p, o = e_json("Ken Thompson", "Pessoa", ["1_0", "1_1"]), e_json("Bell Labs", "Organizacao", ["1_1"])
    d1 = doc_minimo(1, [p, o], [r_json(p["id"], "TRABALHOU_EM", o["id"], "1_1")], principal=p["id"], chunks=3)
    o2 = e_json("Laboratórios Bell", "Organizacao", ["2_0"], aliases=["Bell Labs"])
    d2 = doc_minimo(2, [o2], [], principal=o2["id"])
    return [d1, d2]


def _preparar(docs):
    res = resolver(agregar_entidades(docs))
    rels, _ = remapear_relacoes(docs, res.mapa)
    return res, rels


# ----------------------------------------------------------------------------- segurança
def test_rotulo_invalido_e_recusado():
    with pytest.raises(ValueError):
        query_entidade("Pessoa`) DETACH DELETE (n) //")


def test_tipo_de_relacao_invalido_e_recusado():
    with pytest.raises(ValueError):
        query_relacao("CRIOU]->(x) DETACH DELETE (x) //")


def test_queries_validas_trazem_o_rotulo_e_o_tipo():
    assert "SET e:`Pessoa`" in query_entidade("Pessoa")
    assert "[r:`CRIOU`]" in query_relacao("CRIOU")


def test_nenhuma_query_de_carga_usa_create():
    """Idempotência depende de MERGE: CREATE duplicaria nós a cada execução."""
    for q in (carregador.Q_DOCUMENTO, carregador.Q_CHUNK, carregador.Q_PROXIMO, carregador.Q_MENCIONA,
              carregador.Q_SOBRE, query_entidade("Pessoa"), query_relacao("CRIOU")):
        assert "CREATE " not in q.upper().replace("MERGE", "")
        assert "MERGE" in q


# ----------------------------------------------------------------------------- linhas
def test_chunks_formam_uma_lista_encadeada_na_ordem_de_leitura():
    nos, pares = linhas_chunks(doc_minimo(7, [e_json("X", "Pessoa", ["7_0"])], chunks=4))
    assert [n["id"] for n in nos] == ["7_0", "7_1", "7_2", "7_3"]
    assert pares == [{"de": "7_0", "para": "7_1"}, {"de": "7_1", "para": "7_2"}, {"de": "7_2", "para": "7_3"}]
    assert all(n["documento_id"] == 7 for n in nos)


def test_documento_de_um_chunk_nao_tem_proximo():
    assert linhas_chunks(doc_minimo(1, [e_json("X", "Pessoa", ["1_0"])], chunks=1))[1] == []


def test_mencoes_apontam_para_o_canonico_e_nao_repetem():
    docs = _docs_exemplo()
    res, _ = _preparar(docs)
    mencoes = linhas_mencoes(docs, res.mapa)
    pares = [(m["chunk_id"], m["entidade_id"]) for m in mencoes]
    assert len(pares) == len(set(pares))
    assert all(e in res.entidades for _, e in pares)


def test_sobre_aponta_para_o_canonico():
    docs = _docs_exemplo()
    res, _ = _preparar(docs)
    assert all(s["entidade_id"] in res.entidades for s in linhas_sobre(docs, res.mapa))


# ----------------------------------------------------------------------------- carga
def test_carga_envia_tudo_e_na_ordem_certa():
    docs = _docs_exemplo()
    res, rels = _preparar(docs)
    ex = ExecutorFalso()
    enviados = carregar(ex, docs, res.entidades, res.mapa, rels)
    assert enviados["documentos"] == 2 and enviados["chunks"] == 5 and enviados["proximo"] == 3
    assert enviados["entidades"] == len(res.entidades) and enviados["relacoes"] == len(rels)
    queries = [q for q, _ in ex.escritas]
    primeira = lambda trecho: next(i for i, q in enumerate(queries) if trecho in q)  # noqa: E731
    assert primeira("MERGE (d:Documento") < primeira("MERGE (c:Chunk") < primeira("MERGE (e:Entidade")
    assert primeira("MERGE (e:Entidade") < primeira("MENCIONA") < primeira("[r:`TRABALHOU_EM`]")


def test_lotes_nao_passam_do_limite():
    docs = [doc_minimo(1, [e_json("X", "Pessoa", ["1_0"])], chunks=2)]
    docs[0]["chunks"] = [{"id": f"1_{i}", "ordem": i, "texto": "t"} for i in range(TAMANHO_LOTE * 2 + 10)]
    res, rels = _preparar(docs)
    ex = ExecutorFalso()
    carregar(ex, docs, res.entidades, res.mapa, rels)
    lotes_chunk = [p["rows"] for q, p in ex.escritas if "MERGE (c:Chunk" in q]
    assert [len(l) for l in lotes_chunk] == [TAMANHO_LOTE, TAMANHO_LOTE, 10]


def test_carregar_duas_vezes_envia_exatamente_as_mesmas_linhas():
    docs = _docs_exemplo()
    res, rels = _preparar(docs)
    a, b = ExecutorFalso(), ExecutorFalso()
    carregar(a, docs, res.entidades, res.mapa, rels)
    carregar(b, docs, res.entidades, res.mapa, rels)
    assert a.escritas == b.escritas


def test_entidade_fundida_leva_os_aliases_do_absorvido():
    docs = _docs_exemplo()
    res, rels = _preparar(docs)
    ex = ExecutorFalso()
    carregar(ex, docs, res.entidades, res.mapa, rels)
    linhas = [l for q, p in ex.escritas if "SET e:`Organizacao`" in q for l in p["rows"]]
    assert len(linhas) == 1  # Bell Labs e Laboratórios Bell viraram um nó
    assert {"Bell Labs", "Laboratórios Bell"} == {linhas[0]["nome"], *linhas[0]["aliases"]}


def test_limpar_usa_autocommit():
    ex = ExecutorFalso()
    carregador.limpar_grafo(ex)
    assert ex.autocommits and "DETACH DELETE" in ex.autocommits[0]
