"""Roda a preparação (sem banco) nas 147 extrações reais do repositório e confere as invariantes do grafo."""

import pytest

from src.contrato import TIPOS_RELACAO
from src import ingestao as pipeline

docs, rejeitados = pipeline.ler_extracoes()
pytestmark = pytest.mark.skipif(not docs, reason="data/extracoes/ vazio")
prep = pipeline.preparar(docs) if docs else None


def test_todas_as_extracoes_sao_validas():
    assert rejeitados == []


def test_a_resolucao_reduz_entidades_sem_exagero():
    est = prep.estatisticas
    assert est["entidades_depois_da_resolucao"] < est["entidades_antes_da_resolucao"]
    # fundir mais de 5% do corpus seria sinal de limiar frouxo demais
    assert est["entidades_fundidas"] / est["entidades_antes_da_resolucao"] < 0.05


def test_todo_id_original_aponta_para_uma_entidade_existente():
    assert all(c in prep.entidades for c in prep.mapa.values())


def test_relacoes_so_ligam_entidades_que_existem_e_nao_sao_autorreferentes():
    for r in prep.relacoes:
        assert r["origem"] in prep.entidades and r["destino"] in prep.entidades
        assert r["origem"] != r["destino"]


def test_relacoes_continuam_respeitando_os_tipos_do_contrato_apos_a_fusao():
    for r in prep.relacoes:
        origens, destinos, _ = TIPOS_RELACAO[r["tipo"]]
        t_o, t_d = prep.entidades[r["origem"]].tipo, prep.entidades[r["destino"]].tipo
        assert t_o in origens
        assert t_d == t_o if destinos == "MESMO" else t_d in destinos


def test_nenhuma_relacao_repetida_no_resultado():
    chaves = [(r["origem"], r["tipo"], r["destino"]) for r in prep.relacoes]
    assert len(chaves) == len(set(chaves))


def test_toda_relacao_traz_chunks_evidencia_e_confianca():
    for r in prep.relacoes:
        assert r["chunk_ids"] and r["evidencia"] and 0 <= r["confianca"] <= 1


def test_entidade_principal_de_cada_documento_existe_no_resultado():
    for d in docs:
        assert prep.mapa[d["entidade_principal"]] in prep.entidades


def test_entidades_ligadas_a_chunks_que_existem():
    ids_chunks = {c["id"] for d in docs for c in d["chunks"]}
    for d in docs:
        for e in d["entidades"]:
            assert set(e["chunk_ids"]) <= ids_chunks


def test_fusoes_conhecidas_aconteceram():
    m = prep.mapa
    assert m["organizacao:agencia_de_seguranca_nacional"] == m["organizacao:nsa"]
    assert m["organizacao:at_t_corporation"] == m["organizacao:at_t"]
    assert m["tecnologia:transmission_control_protocol"] == m["tecnologia:tcp"]


def test_casos_que_nao_podem_fundir_continuam_separados():
    m = prep.mapa
    assert m["hardware:harvard_mark_i"] != m["hardware:manchester_mark_i"]
    assert m["hardware:intel_80286"] != m["hardware:intel_8086"]
    assert m["hardware:motorola_6800"] != m["hardware:motorola_68000"]
    assert m["tecnologia:windows_8"] != m["tecnologia:windows_98"]
    assert m["tecnologia:internet"] != m["tecnologia:ip"]
    assert m["linguagem:c"] != m["linguagem:cpp"]


def test_resultado_e_reproduzivel():
    assert pipeline.preparar(docs).mapa == prep.mapa
