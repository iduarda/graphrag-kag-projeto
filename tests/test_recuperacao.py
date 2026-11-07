import pytest

from src.recuperacao import (
    RespostaGraphRAG,
    buscar_vetorial,
    buscar_vizinhanca,
    buscar_vizinhança,
    executar_busca_hibrida,
    gerar_cypher,
    gerar_cypher_llm,
    ranquear_por_cosseno,
    resposta_padrao,
    similaridade_cosseno,
    travessia_multi_hop,
    validar_cypher,
)


# cosseno
def test_similaridade_cosseno():
    assert similaridade_cosseno([1.0, 0.0, 1.0], [1.0, 0.0, 0.0]) > 0.7


def test_similaridade_cosseno_casos_limite():
    assert similaridade_cosseno([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)
    assert similaridade_cosseno([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert similaridade_cosseno([0.0, 0.0], [1.0, 1.0]) == 0.0
    with pytest.raises(ValueError):
        similaridade_cosseno([1.0], [1.0, 2.0])


def test_ranquear_por_cosseno_usa_embedding_quando_presente():
    itens = [
        {"id": "a", "embedding": [0.0, 1.0]},
        {"id": "b", "embedding": [1.0, 0.0]},
        {"id": "c", "score": 0.5},
    ]
    ranking = ranquear_por_cosseno([1.0, 0.0], itens, limite=2)
    assert [r["id"] for r in ranking] == ["b", "c"]
    assert "embedding" not in ranking[0]


# validação
def test_validar_cypher_rejeita_comandos_dangerosos():
    consulta_ok = "MATCH (p:Pessoa)-[:TRABALHOU_EM]->(o:Organizacao) RETURN p.nome, o.nome LIMIT 10"
    consulta_ruim = "MATCH (n) DETACH DELETE n RETURN count(n)"

    assert validar_cypher(consulta_ok)
    with pytest.raises(ValueError):
        validar_cypher(consulta_ruim)


@pytest.mark.parametrize(
    "consulta",
    [
        "MATCH (n) SET n.x = 1 RETURN n",
        "MATCH (n) REMOVE n.x RETURN n",
        "MERGE (n:Pessoa {id: 'x'}) RETURN n",
        "CREATE (n:Pessoa) RETURN n",
        "MATCH (n) RETURN n; MATCH (m) DETACH DELETE m",
        "CALL dbms.components() YIELD name RETURN name",
        "CALL apoc.cypher.run('MATCH (n) RETURN n', {}) YIELD value RETURN value",
        "LOAD CSV FROM 'file:///x.csv' AS l MATCH (n) RETURN n",
        "",
        "   ",
    ],
)
def test_validar_cypher_rejeita_escrita_e_administracao(consulta):
    with pytest.raises(ValueError):
        validar_cypher(consulta)


def test_validar_cypher_exige_match_e_return():
    with pytest.raises(ValueError):
        validar_cypher("RETURN 1")
    with pytest.raises(ValueError):
        validar_cypher("MATCH (n:Pessoa)")


def test_validar_cypher_aceita_leituras_legitimas():
    # WITH/UNWIND são leitura; "reset" e literais não devem disparar SET.
    assert validar_cypher("MATCH (n:Pessoa) WITH collect(n.nome) AS nomes UNWIND nomes AS x RETURN x LIMIT 5")
    assert validar_cypher("MATCH (n) WHERE n.nome = 'SET me' RETURN n.reset_count LIMIT 1")
    assert validar_cypher(
        "CALL db.index.vector.queryNodes('entidade_embedding', 5, $v) YIELD node, score RETURN node.id, score"
    )


# text-to-cypher
def test_gerar_cypher_gera_query_estruturada():
    consulta = gerar_cypher("Quem trabalhou no Bell Labs?")
    texto = consulta.upper()
    assert "MATCH" in texto
    assert "RETURN" in texto
    assert "BELL" in texto or "BELL LABS" in texto
    assert validar_cypher(consulta)


def test_gerar_cypher_todas_as_regras_sao_validas():
    perguntas = [
        "Quem trabalhou no Bell Labs?",
        "Quem criou a linguagem C?",
        "Onde Dennis Ritchie estudou?",
        "Quem criou linguagens de programação?",
        "Alan Turing",
    ]
    for p in perguntas:
        assert validar_cypher(gerar_cypher(p, profundidade=2))


def test_gerar_cypher_nao_reutiliza_variavel_de_caminho():
    consulta = gerar_cypher("Quem criou linguagens de programação?", profundidade=2)
    assert "p=(p" not in consulta


def test_gerar_cypher_escapa_aspas_e_valida_rotulo():
    consulta = gerar_cypher("Quem trabalhou na O'Reilly Media?")
    assert validar_cypher(consulta)
    with pytest.raises(ValueError):
        gerar_cypher("Alan Turing", tipo="Pessoa) DETACH DELETE (n")


class _ClienteAnthropicFalso:
    """Imita anthropic.Anthropic().messages.create(...)."""

    def __init__(self, texto):
        self.texto = texto
        self.chamadas = []
        self.messages = self

    def create(self, **kwargs):
        self.chamadas.append(kwargs)

        class _Bloco:
            text = self.texto

        class _Resp:
            content = [_Bloco()]

        return _Resp()


class _ClienteOpenAIFalso:
    """Imita openai.OpenAI().chat.completions.create(...)."""

    def __init__(self, texto):
        self.texto = texto
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        texto = self.texto

        class _Msg:
            content = texto

        class _Escolha:
            message = _Msg()

        class _Resp:
            choices = [_Escolha()]

        return _Resp()


def test_gerar_cypher_llm_usa_saida_valida_e_inclui_relacoes_no_prompt():
    cliente = _ClienteAnthropicFalso(
        "```cypher\nMATCH (p:Pessoa)-[:CRIOU]->(l:Linguagem) RETURN p.nome, l.nome\n```"
    )
    cypher = gerar_cypher_llm("Quem criou C?", cliente=(cliente, "modelo-falso"), limite=7)
    assert cypher.startswith("MATCH (p:Pessoa)-[:CRIOU]->(l:Linguagem)")
    assert cypher.endswith("LIMIT 7")  # LIMIT acrescentado
    sistema = cliente.chamadas[0]["system"]
    assert "TRABALHOU_EM" in sistema and "CRIOU" in sistema and "Pessoa" in sistema


def test_gerar_cypher_llm_aceita_json_e_formato_openai():
    cliente = _ClienteOpenAIFalso('{"cypher": "MATCH (n:Pessoa) RETURN n.nome LIMIT 3;"}')
    cypher = gerar_cypher_llm("Quem?", cliente=(cliente, "m"))
    assert cypher == "MATCH (n:Pessoa) RETURN n.nome LIMIT 3"


def test_gerar_cypher_llm_cai_no_fallback_quando_saida_e_perigosa():
    cliente = _ClienteAnthropicFalso("MATCH (n) DETACH DELETE n RETURN count(n)")
    cypher = gerar_cypher_llm("Quem trabalhou no Bell Labs?", cliente=(cliente, "m"))
    assert "DELETE" not in cypher.upper()
    assert validar_cypher(cypher)


def test_gerar_cypher_llm_sem_credenciais_usa_regras(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cypher = gerar_cypher_llm("Quem trabalhou no Bell Labs?")
    assert validar_cypher(cypher)


def test_gerar_cypher_usar_llm_propaga_cliente_via_fallback_sem_quebrar(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cypher = gerar_cypher("Quem trabalhou no Bell Labs?", usar_llm=True)
    assert "TRABALHOU_EM" in cypher


# busca vetorial
class _Embedder:
    def codificar_consulta(self, pergunta):
        return [0.1, 0.2, 0.3]


def test_buscar_vetorial_usa_cosseno_e_retorna_resultados():
    consultas = []

    class FakeExecutor:
        def ler(self, query, **params):
            consultas.append((query, params))
            return [
                {"id": "pessoa:dennis_ritchie", "nome": "Dennis Ritchie", "score": 0.84},
                {"id": "pessoa:alan_turing", "nome": "Alan Turing", "score": 0.97},
            ]

    resultados = buscar_vetorial(FakeExecutor(), "Alan Turing", limite=2, tipo="Entidade", embedder=_Embedder())
    assert [r["id"] for r in resultados] == ["pessoa:alan_turing", "pessoa:dennis_ritchie"]
    query, params = consultas[0]
    assert "db.index.vector.queryNodes('entidade_embedding'" in query
    assert params["v"] == [0.1, 0.2, 0.3]


def test_buscar_vetorial_chunk_usa_indice_de_chunks():
    consultas = []

    class FakeExecutor:
        def ler(self, query, **params):
            consultas.append(query)
            return []

    assert buscar_vetorial(FakeExecutor(), "x", tipo="Chunk", embedder=_Embedder()) == []
    assert "chunk_embedding" in consultas[0]


def test_buscar_vetorial_sem_embedder_nao_inventa_candidatos():
    class FakeExecutor:
        def ler(self, query, **params):
            raise AssertionError("não deveria consultar o banco sem vetor")

    assert buscar_vetorial(FakeExecutor(), "Alan Turing") == []


# vizinhança / multi-hop
def test_buscar_vizinhanca_retorna_nos_e_arestas():
    class FakeExecutor:
        def ler(self, query, **params):
            return [
                {
                    "id": "pessoa:alan_turing",
                    "nome": "Alan Turing",
                    "tipo": "Pessoa",
                    "vizinho_id": "organizacao:bell_labs",
                    "vizinho_nome": "Bell Labs",
                    "vizinho_tipo": "Organizacao",
                    "relacao": "TRABALHOU_EM",
                }
            ]

    vizinhanca = buscar_vizinhança(FakeExecutor(), "pessoa:alan_turing", profundidade=1)
    assert vizinhanca["nos"][0]["id"] == "pessoa:alan_turing"
    assert vizinhanca["arestas"][0]["tipo"] == "TRABALHOU_EM"
    assert vizinhanca["arestas"][0]["de"] == "pessoa:alan_turing"
    assert buscar_vizinhança is buscar_vizinhanca


def test_buscar_vizinhanca_sem_dados_nao_inventa_no():
    class FakeExecutor:
        def ler(self, query, **params):
            return []

    assert buscar_vizinhanca(FakeExecutor(), "x") == {"nos": [], "arestas": [], "profundidade": 1}


def test_travessia_multi_hop_interpola_profundidade_e_le_caminhos():
    consultas = []

    class FakeExecutor:
        def ler(self, query, **params):
            consultas.append((query, params))
            return [
                {
                    "nos": [
                        {"id": "a", "nome": "A", "tipo": "Pessoa"},
                        {"id": "b", "nome": "B", "tipo": "Organizacao"},
                        {"id": "c", "nome": "C", "tipo": "Pessoa"},
                    ],
                    "arestas": [
                        {"de": "a", "para": "b", "tipo": "TRABALHOU_EM"},
                        {"de": "c", "para": "b", "tipo": "TRABALHOU_EM"},
                    ],
                },
                {  # caminho repetido: deve ser deduplicado
                    "nos": [{"id": "a", "nome": "A", "tipo": "Pessoa"}],
                    "arestas": [{"de": "a", "para": "b", "tipo": "TRABALHOU_EM"}],
                },
            ]

    resultado = travessia_multi_hop(FakeExecutor(), "a", profundidade=3, limite=10)
    query, params = consultas[0]
    assert "*1..3" in query
    assert "$profundidade" not in query
    assert "profundidade" not in params
    assert [n["id"] for n in resultado["nos"]] == ["a", "b", "c"]
    assert len(resultado["arestas"]) == 2
    assert resultado["profundidade"] == 3


def test_travessia_multi_hop_impoe_minimo_2_e_maximo():
    consultas = []

    class FakeExecutor:
        def ler(self, query, **params):
            consultas.append(query)
            return []

    travessia_multi_hop(FakeExecutor(), "a", profundidade=1)
    travessia_multi_hop(FakeExecutor(), "a", profundidade=99)
    assert "*1..2" in consultas[0]
    assert "*1..4" in consultas[1]


def test_buscar_vizinhanca_profundidade_2_delega_para_multi_hop():
    consultas = []

    class FakeExecutor:
        def ler(self, query, **params):
            consultas.append(query)
            return []

    buscar_vizinhanca(FakeExecutor(), "a", profundidade=2)
    assert "*1..2" in consultas[0]


# resposta padrão
def test_resposta_padrao_exibe_chave_esperada():
    resposta = resposta_padrao("Resposta", [{"id": "a"}], [{"tipo": "TRABALHOU_EM"}], "MATCH (a) RETURN a", 0.91)
    assert set(resposta) == {"resposta", "nos", "arestas", "cypher", "confianca"}
    assert resposta["resposta"] == "Resposta"
    assert resposta["cypher"] == "MATCH (a) RETURN a"
    assert resposta["confianca"] == 0.91
    assert isinstance(resposta, dict)


def test_respostagraphrag_as_dict():
    r = RespostaGraphRAG("ok", [{"id": "a"}], [], "MATCH (a) RETURN a", 0.5)
    assert r.as_dict()["confianca"] == 0.5


# ---------------------------------------------------------------- fluxo híbrido
def test_executar_busca_hibrida_retornas_objeto_padrao():
    class FakeExecutor:
        def ler(self, query, **params):
            if "vector.queryNodes" in query:
                return [{"id": "pessoa:alan_turing", "nome": "Alan Turing", "score": 0.96}]
            if "MATCH (n" in query:
                return [{"id": "pessoa:alan_turing", "nome": "Alan Turing", "tipo": "Pessoa"}]
            return [{"id": "pessoa:alan_turing", "nome": "Alan Turing"}]

    resultado = executar_busca_hibrida(FakeExecutor(), "Quem trabalhou no Bell Labs?", tipo="Entidade")
    assert isinstance(resultado, RespostaGraphRAG)
    assert resultado.cypher
    assert resultado.confianca >= 0.0


class _ExecutorHibrido:
    def __init__(self):
        self.consultas = []

    def ler(self, query, **params):
        self.consultas.append(query)
        if "vector.queryNodes" in query:
            return [{"id": "pessoa:ken_thompson", "nome": "Ken Thompson", "labels": ["Pessoa"], "score": 0.9}]
        if "TRABALHOU_EM]->(o:Organizacao)" in query and "pessoa_id" in query:
            return [
                {
                    "pessoa_id": "pessoa:dennis_ritchie",
                    "pessoa": "Dennis Ritchie",
                    "organizacao_id": "organizacao:bell_labs",
                    "organizacao": "Bell Labs",
                }
            ]
        if "[*1.." in query:
            return [
                {
                    "nos": [
                        {"id": "pessoa:ken_thompson", "nome": "Ken Thompson", "tipo": "Pessoa"},
                        {"id": "organizacao:bell_labs", "nome": "Bell Labs", "tipo": "Organizacao"},
                    ],
                    "arestas": [{"de": "pessoa:ken_thompson", "para": "organizacao:bell_labs", "tipo": "TRABALHOU_EM"}],
                }
            ]
        return []


def test_hibrida_executa_cypher_valido_e_combina_vetorial_com_estruturado():
    executor = _ExecutorHibrido()
    resultado = executar_busca_hibrida(
        executor, "Quem trabalhou no Bell Labs?", profundidade=2, embedder=_Embedder()
    )

    assert resultado.cypher in executor.consultas  # Cypher gerado foi de fato executado
    assert any("vector.queryNodes" in q for q in executor.consultas)  # e a busca vetorial também
    assert any("[*1.." in q for q in executor.consultas)  # e a travessia multi-hop

    ids = {n["id"] for n in resultado.nos}
    assert {"pessoa:ken_thompson", "pessoa:dennis_ritchie", "organizacao:bell_labs"} <= ids
    assert len(ids) == len(resultado.nos)  # sem duplicados
    assert {"de": "pessoa:ken_thompson", "para": "organizacao:bell_labs", "tipo": "TRABALHOU_EM"} in resultado.arestas
    assert 0.0 < resultado.confianca <= 1.0
    assert set(resultado.as_dict()) == {"resposta", "nos", "arestas", "cypher", "confianca"}


def test_hibrida_sem_embedder_ainda_usa_resultado_do_cypher():
    executor = _ExecutorHibrido()
    resultado = executar_busca_hibrida(executor, "Quem trabalhou no Bell Labs?", profundidade=1)
    assert {"pessoa:dennis_ritchie", "organizacao:bell_labs"} <= {n["id"] for n in resultado.nos}
    assert not any("vector.queryNodes" in q for q in executor.consultas)


def test_hibrida_sem_evidencia_tem_confianca_zero():
    class VazioExecutor:
        def ler(self, query, **params):
            return []

    resultado = executar_busca_hibrida(VazioExecutor(), "Quem trabalhou no Bell Labs?")
    assert resultado.nos == [] and resultado.arestas == []
    assert resultado.confianca == 0.0
    assert resultado.resposta.startswith("Não encontrei")


def test_hibrida_tolera_erro_na_execucao_do_cypher():
    class QuebradoExecutor:
        def ler(self, query, **params):
            if "queryNodes" in query:
                return [{"id": "x", "nome": "X", "score": 0.8}]
            if "RETURN n.id AS id, n.nome AS nome, labels(n) AS labels LIMIT" in query:
                raise RuntimeError("falha no Neo4j")
            return []

    resultado = executar_busca_hibrida(QuebradoExecutor(), "Alan Turing", embedder=_Embedder())
    assert [n["id"] for n in resultado.nos] == ["x"]