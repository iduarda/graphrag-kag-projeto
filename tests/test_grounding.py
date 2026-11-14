from types import SimpleNamespace

from src.avaliacao_sistema import calcular_acerto, frases_sem_citacao, pontuar
from src.grounding import (
    FRASE_ABSTENCAO,
    buscar_fatos,
    ids_de_entidades,
    responder_com_fatos,
    verificar_citacoes,
)

FATOS = [
    {"ref": "F1", "origem_id": "linguagem:c", "origem": "C", "relacao": "INFLUENCIADA_POR",
     "destino_id": "linguagem:b", "destino": "B", "evidencia": "C foi criada a partir da linguagem B", "confianca": 0.9},
    {"ref": "F2", "origem_id": "pessoa:ken_thompson", "origem": "Ken Thompson", "relacao": "CRIOU",
     "destino_id": "linguagem:b", "destino": "B", "evidencia": "Thompson criou a linguagem B", "confianca": 0.95},
]


class FakeExecutor:
    def __init__(self, linhas):
        self.linhas, self.chamadas = linhas, []

    def ler(self, query, **params):
        self.chamadas.append(params)
        return self.linhas


class ExecutorGrafo:
    """Simula o Neo4j: nomes das entidades, expansão semântica e nenhum resultado vetorial/chunk."""

    NOMES = [{"id": "linguagem:c", "nome": "C"}, {"id": "linguagem:b", "nome": "B"},
             {"id": "pessoa:ken_thompson", "nome": "Ken Thompson"}, {"id": "tecnologia:sol", "nome": "SOL"}]

    def __init__(self):
        self.sementes = None

    def ler(self, query, **params):
        if "RETURN e.id AS id, e.nome AS nome" in query:
            return self.NOMES
        if "s.id IN $sementes" in query:
            self.sementes = params["sementes"]
            return [{k: v for k, v in f.items() if k != "ref"} for f in FATOS]
        return []


class FakeLLM:
    """Imita o cliente da Anthropic: api.messages.create(...).content[0].text"""

    def __init__(self, texto):
        self.messages = SimpleNamespace(create=lambda **_: SimpleNamespace(content=[SimpleNamespace(text=texto)]))


def test_ids_de_entidades_ignora_chunks_e_documentos():
    nos = [{"id": "pessoa:a", "tipo": "Pessoa"}, {"id": "1_0", "tipo": "Chunk"}, {"id": "1", "tipo": "Documento"}]
    assert ids_de_entidades(nos) == ["pessoa:a"]


def test_buscar_fatos_faz_uma_consulta_e_numera():
    ex = FakeExecutor([{"origem": "C"}, {"origem": "B"}])
    fatos = buscar_fatos(ex, ["linguagem:c", "linguagem:b", "linguagem:c"])
    assert [f["ref"] for f in fatos] == ["F1", "F2"]
    assert len(ex.chamadas) == 1
    assert ex.chamadas[0]["ids"] == ["linguagem:c", "linguagem:b"]  # sem repetição


def test_buscar_fatos_sem_ids_nao_consulta():
    ex = FakeExecutor([{"origem": "C"}])
    assert buscar_fatos(ex, []) == []
    assert ex.chamadas == []


def test_verificar_citacoes_separa_inventadas():
    validas, invalidas = verificar_citacoes("A [F1]. B [F9][F2]. De novo [F1].", FATOS)
    assert validas == ["F1", "F2"]
    assert invalidas == ["F9"]


def test_resposta_com_llm_fundamentada():
    llm = (FakeLLM("A linguagem C foi influenciada pela B [F1], criada por Ken Thompson [F2]."), "modelo-teste")
    r = responder_com_fatos("Quem criou a linguagem que influenciou C?", FATOS, llm)
    assert r.usou_llm and r.fundamentada
    assert r.citados == ["F1", "F2"]
    assert [f["ref"] for f in r.fatos_citados] == ["F1", "F2"]


def test_resposta_com_citacao_inventada_nao_e_fundamentada():
    llm = (FakeLLM("C veio de B [F1] e de Algol [F7]."), "modelo-teste")
    r = responder_com_fatos("pergunta", FATOS, llm)
    assert not r.fundamentada
    assert r.citacoes_invalidas == ["F7"]
    assert "[F7]" not in r.texto


def test_abstencao_conta_como_fundamentada():
    llm = (FakeLLM(f"{FRASE_ABSTENCAO}: faltam dados sobre o tema."), "modelo-teste")
    r = responder_com_fatos("pergunta", FATOS, llm)
    assert r.abstencao and r.fundamentada


def test_sem_llm_usa_fatos_citados():
    r = responder_com_fatos("pergunta", FATOS, None)
    assert not r.usou_llm and r.fundamentada
    assert "[F1]" in r.texto


def test_sem_fatos_abstem():
    r = responder_com_fatos("pergunta", [], None)
    assert r.abstencao


def test_falha_do_llm_cai_na_resposta_extrativa():
    class Quebrado:
        messages = SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("sem rede")))

    r = responder_com_fatos("pergunta", FATOS, (Quebrado(), "m"))
    assert not r.usou_llm and r.fundamentada


# ----------------------------------------------------------------------------- métricas da avaliação
def test_acerto_usa_palavra_inteira():
    esperadas = [["C"], ["C++"], ["B"]]
    assert calcular_acerto("Foram criadas C++ e Cobol.", esperadas) == 1 / 3
    assert calcular_acerto("Foram criadas C, C++ e B.", esperadas) == 1.0


def test_acerto_aceita_alternativas():
    assert calcular_acerto("Foi o Ritchie.", [["Dennis Ritchie", "Ritchie"]]) == 1.0


def test_frases_sem_citacao():
    assert frases_sem_citacao("Unix foi escrito em C [F1]. C foi criada por Dennis Ritchie em 1972.") == 0.5
    assert frases_sem_citacao("Tudo citado aqui no texto [C1]. E aqui também no texto [C2].") == 0.0


def test_pontuar():
    r = pontuar({"texto": "Ken Thompson criou a B [F2].", "citadas": ["F2"], "invalidas": [], "abstencao": False},
                [["Ken Thompson", "Thompson"]])
    assert r["correta"] and r["fundamentada"] and r["frases_sem_citacao"] == 0.0


# ----------------------------------------------------------------------------- falhas do LLM
class FakeOpenAI:
    """Imita o cliente compatível com OpenAI (usado para o Gemini)."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

        def create(**_):
            self.chamadas += 1
            r = self.respostas.pop(0)
            if isinstance(r, Exception):
                raise r
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r), finish_reason="length")])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_resposta_vazia_do_llm_vira_erro():
    import pytest
    from src.grounding import chamar_llm

    with pytest.raises(ValueError, match="vazia"):
        chamar_llm((FakeOpenAI([None]), "gemini"), "s", "u")


def test_limite_de_uso_tenta_de_novo(monkeypatch):
    import src.grounding as g

    monkeypatch.setattr(g.time, "sleep", lambda _: None)
    api = FakeOpenAI([RuntimeError("Error code: 429 RESOURCE_EXHAUSTED"), "Resposta [F1]."])
    assert g.chamar_llm((api, "gemini"), "s", "u") == "Resposta [F1]."
    assert api.chamadas == 2


def test_avaliacao_marca_falha_do_llm_como_erro(monkeypatch):
    from src import avaliacao_sistema as av
    from src.recuperacao import RespostaGraphRAG

    nos = [{"id": "pessoa:ken_thompson", "nome": "Ken Thompson", "tipo": "Pessoa"}]
    monkeypatch.setattr(av, "executar_busca_hibrida", lambda *a, **k: RespostaGraphRAG(resposta="", nos=nos))

    Ex = ExecutorGrafo

    class Emb:
        def codificar_consulta(self, _):
            return [0.1, 0.2]

        def codificar_documentos(self, textos):
            return [[0.1, 0.2] for _ in textos]

    perguntas = [{"id": "q1", "pergunta": "Quem criou B?", "saltos": 1, "esperadas": [["Ken Thompson"]]}]
    resultado = av.avaliar(Ex(), Emb(), (FakeOpenAI([None] * 20), "gemini"), perguntas, log=lambda *_: None)
    assert resultado["resumo"]["graphrag"]["erros"] == 1
    assert resultado["resumo"]["rag_vetorial"]["erros"] == 1


def test_demora_do_llm_tenta_de_novo_e_desiste(monkeypatch):
    import pytest
    import src.grounding as g

    monkeypatch.setattr(g.time, "sleep", lambda _: None)
    api = FakeOpenAI([TimeoutError("Request timed out.")] * 3)
    with pytest.raises(TimeoutError):
        g.chamar_llm((api, "gemini"), "s", "u")
    assert api.chamadas == 3


def test_prazo_e_aplicado_quando_o_cliente_permite():
    import src.grounding as g

    usados = {}

    class ComPrazo(FakeOpenAI):
        def with_options(self, **opcoes):
            usados.update(opcoes)
            return self

    assert g.chamar_llm((ComPrazo(["ok"]), "gemini"), "s", "u") == "ok"
    assert usados == {"timeout": g.TIMEOUT_LLM, "max_retries": 0}


def test_fluxo_sem_api_exporta_sem_gabarito_e_pontua(monkeypatch):
    from src import avaliacao_sistema as av
    from src.recuperacao import RespostaGraphRAG

    nos = [{"id": "pessoa:ken_thompson", "nome": "Ken Thompson", "tipo": "Pessoa"}]
    monkeypatch.setattr(av, "executar_busca_hibrida", lambda *a, **k: RespostaGraphRAG(resposta="", nos=nos, cypher="MATCH"))

    Ex = ExecutorGrafo  # sem chunks para o RAG

    class Emb:
        def codificar_consulta(self, _):
            return [0.1]

        def codificar_documentos(self, textos):
            return [[0.1] for _ in textos]

    perguntas = [{"id": "q1", "pergunta": "Quem criou a linguagem que influenciou a C?", "saltos": 2,
                  "esperadas": [["Ken Thompson", "Thompson"]]}]
    itens = av.exportar_contextos(Ex(), Emb(), perguntas, log=lambda *_: None)

    assert [i["id"] for i in itens] == ["q1_rag_vetorial", "q1_graphrag"]
    assert all("esperadas" not in i for i in itens)                       # gabarito não vaza
    assert "resposta_automatica" in itens[0]                               # RAG sem chunks -> abstenção
    assert itens[1]["refs"] == ["F1", "F2"] and "[F2] Ken Thompson" in itens[1]["usuario"]

    respostas = {"q1_graphrag": "A C foi influenciada pela B [F1], criada por Ken Thompson [F2]. Inventado [F9]."}
    resultado = av.pontuar_respostas(itens, respostas, "modelo-teste", perguntas)
    gr = resultado["detalhes"][0]["graphrag"]
    assert gr["correta"] and gr["invalidas"] == ["F9"] and not gr["fundamentada"]
    assert resultado["resumo"]["rag_vetorial"]["abstencoes"] == 1
    assert resultado["resumo"]["graphrag"]["erros"] == 0


def test_citacao_depois_do_ponto_conta_para_a_frase_anterior():
    texto = "Ele trabalhou no CERN, em Genebra. [C1][C2] Lá escreveu o programa Enquire. [C1]"
    assert frases_sem_citacao(texto) == 0.0



# ----------------------------------------------------------------------------- recuperação de fatos
def test_entidades_citadas_usa_palavra_inteira_e_grafia_exata_em_nomes_curtos():
    from src.grounding import _cache_nomes, entidades_citadas

    ex = ExecutorGrafo()
    _cache_nomes.pop(id(ex), None)
    achadas = entidades_citadas(ex, "Quem criou a linguagem que influenciou a linguagem C? Fazia sol.")
    assert achadas == ["linguagem:c"]  # "sol" minúsculo não vira a entidade "SOL"; "C" casa


def test_recuperar_fatos_sem_sementes_nao_expande():
    from src.grounding import recuperar_fatos

    ex = ExecutorGrafo()
    assert recuperar_fatos(ex, "Pergunta sem nenhuma entidade conhecida?") == []
    assert ex.sementes is None


def test_recuperar_fatos_ordena_por_semelhanca_e_numera():
    from src import grounding as g

    class Emb:
        def codificar_consulta(self, _):
            return [1.0, 0.0]

        def codificar_documentos(self, textos):
            # fato sobre "Ken Thompson criou B" é o mais parecido com a pergunta
            return [[0.9, 0.1] if "Ken Thompson" in t else [0.2, 0.8] for t in textos]

    g._cache_vetores.clear()
    ex = ExecutorGrafo()
    fatos = g.recuperar_fatos(ex, "Quem criou a linguagem que influenciou a linguagem C?", embedder=Emb())
    assert "linguagem:c" in ex.sementes
    assert [f["ref"] for f in fatos] == ["F1", "F2"]
    assert fatos[0]["origem"] == "Ken Thompson" and fatos[0]["similaridade"] == 0.9


def test_selecao_completa_cadeias_multi_hop():
    from src import grounding as g

    base = {"relacao": "R", "evidencia": "e"}
    ordenados = [{**base, "origem_id": f"x{i}", "destino_id": f"y{i}"} for i in range(g.NUCLEO)]
    ligado = {**base, "origem_id": "y0", "destino_id": "z"}      # continua a cadeia do 1º fato
    solto = {**base, "origem_id": "w1", "destino_id": "w2"}      # parecido, mas sem ligação
    escolhidos = g._selecionar(ordenados + [solto, ligado], limite=g.NUCLEO + 1)
    assert ligado in escolhidos and solto not in escolhidos



def test_selecao_prefere_segundo_passo_a_mais_fatos_da_semente():
    from src import grounding as g

    base = {"relacao": "R", "evidencia": "e"}
    # núcleo: fatos da semente "fairchild" com várias pessoas
    nucleo = [{**base, "origem_id": f"pessoa{i}", "destino_id": "fairchild"} for i in range(g.NUCLEO)]
    mais_um_da_semente = {**base, "origem_id": "outra_pessoa", "destino_id": "fairchild"}
    segundo_passo = {**base, "origem_id": "pessoa0", "destino_id": "intel"}  # pessoa0 fundou a Intel
    escolhidos = g._selecionar(nucleo + [mais_um_da_semente, segundo_passo], limite=g.NUCLEO + 1,
                               sementes={"fairchild"})
    assert segundo_passo in escolhidos and mais_um_da_semente not in escolhidos
