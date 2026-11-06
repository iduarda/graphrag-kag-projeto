"""
Integração com um Neo4j REAL (docker compose up -d).

ATENÇÃO: estes testes APAGAM TODO O GRAFO do banco. Por isso só rodam se você pedir:
    NEO4J_TESTES_PODEM_APAGAR=sim pytest -m neo4j

Eles aplicam schema/schema.cypher, carregam 3 documentos, rodam a carga duas vezes e conferem
constraints, idempotência, índice vetorial e fulltext.
"""

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from src import carregador
from src import ingestao as pipeline
from tests.conftest import EmbedderFalso

pytestmark = pytest.mark.neo4j

SCHEMA = Path(pipeline.RAIZ) / "schema" / "schema.cypher"
AMOSTRA = ["Alan_Turing.json", "Bell_Labs.json", "Dennis_Ritchie.json"]


@pytest.fixture(scope="module")
def ex():
    if os.environ.get("NEO4J_TESTES_PODEM_APAGAR") != "sim":
        pytest.skip("defina NEO4J_TESTES_PODEM_APAGAR=sim (estes testes apagam o grafo)")
    pipeline.carregar_env()
    try:
        executor = pipeline.conectar()
    except Exception as e:  # banco desligado, senha errada...
        pytest.skip(f"Neo4j indisponível: {e}")
    carregador.limpar_grafo(executor)
    comandos = re.sub(r"//.*", "", SCHEMA.read_text(encoding="utf-8"))
    for comando in [c.strip() for c in comandos.split(";") if c.strip()]:
        executor.autocommit(comando)
    yield executor
    executor.fechar()


@pytest.fixture(scope="module")
def pasta_amostra(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("extracoes")
    for nome in AMOSTRA:
        origem = pipeline.PASTA_EXTRACOES / nome
        if origem.exists():
            shutil.copy(origem, pasta / nome)
    if not list(pasta.glob("*.json")):
        pytest.skip("amostra não encontrada em data/extracoes/")
    return pasta


def _rodar(ex, pasta):
    return pipeline.executar_pipeline(ex, EmbedderFalso(), pasta_extracoes=pasta, salvar=False)


def test_carga_cria_o_grafo(ex, pasta_amostra):
    r = _rodar(ex, pasta_amostra)
    nos = r["grafo_final"]["nos"]
    assert nos["Documento"] == len(list(pasta_amostra.glob("*.json")))
    assert nos["Chunk"] > 0 and nos["Entidade"] > 0
    assert {"TEM_CHUNK", "MENCIONA", "SOBRE"} <= set(r["grafo_final"]["relacoes"])


def test_carga_duas_vezes_nao_duplica_nada(ex, pasta_amostra):
    antes = _rodar(ex, pasta_amostra)["grafo_final"]
    depois = _rodar(ex, pasta_amostra)["grafo_final"]
    assert antes == depois


def test_constraint_de_unicidade_barra_duplicata(ex):
    ex.escrever("CREATE (:Pessoa:Entidade {id: 'pessoa:teste_duplicado', nome: 'T'})")
    with pytest.raises(Exception):
        ex.escrever("CREATE (:Pessoa:Entidade {id: 'pessoa:teste_duplicado', nome: 'T'})")
    ex.escrever("MATCH (n {id: 'pessoa:teste_duplicado'}) DETACH DELETE n")


def test_todos_os_chunks_e_entidades_tem_embedding(ex):
    sem = ex.ler("MATCH (n) WHERE (n:Chunk OR n:Entidade) AND n.embedding IS NULL RETURN count(n) AS n")
    assert sem[0]["n"] == 0


def test_indice_vetorial_responde(ex):
    vetor = EmbedderFalso().codificar_consulta("Quem criou o Unix?")
    achados = ex.ler("CALL db.index.vector.queryNodes('chunk_embedding', 3, $v) YIELD node, score "
                     "RETURN node.id AS id, score", v=vetor)
    assert len(achados) == 3


def test_fulltext_acha_entidade_pelo_apelido(ex):
    achados = ex.ler("CALL db.index.fulltext.queryNodes('entidade_fulltext', 'Turing') YIELD node "
                     "RETURN node.nome AS nome")
    assert any("Turing" in a["nome"] for a in achados)


def test_toda_relacao_extraida_guarda_a_procedencia(ex):
    sem = ex.ler("MATCH ()-[r]->() WHERE r.confianca IS NOT NULL AND "
                 "(r.chunk_ids IS NULL OR size(r.chunk_ids) = 0 OR r.evidencia IS NULL) RETURN count(r) AS n")
    assert sem[0]["n"] == 0
