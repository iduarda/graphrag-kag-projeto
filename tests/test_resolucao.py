"""Resolução de entidades: o que deve fundir, o que NUNCA deve fundir e se o resultado é estável."""

import json
import random

from src.resolucao import (agregar_entidades, numeros_do_nome, remapear_relacoes, resolver)
from tests.conftest import doc_minimo, e_json, ent, r_json, tabela_de


# ----------------------------------------------------------------------------- o que deve fundir
def test_sigla_e_nome_completo_viram_um_no():
    nsa = ent("NSA", aliases=["Agência de Segurança Nacional"], n_docs=3)
    longo = ent("Agência de Segurança Nacional", n_docs=1)
    r = resolver(tabela_de(nsa, longo))
    assert len(r.entidades) == 1
    assert r.mapa[longo.id] == nsa.id  # a mais citada vira o canônico
    assert "Agência de Segurança Nacional" in r.entidades[nsa.id].aliases


def test_sufixo_corporativo_nao_cria_no_novo():
    a, b = ent("AT&T", n_docs=5), ent("AT&T Corporation")
    r = resolver(tabela_de(a, b))
    assert r.mapa[b.id] == a.id
    assert r.fusoes[0].regra in {"apelido", "sufixo"}


def test_grafia_quase_igual_funde():
    a = ent("Laboratório Nacional Lawrence Livermore")
    b = ent("Laboratório Nacional de Lawrence Livermore")
    assert len(resolver(tabela_de(a, b)).entidades) == 1


def test_canonico_prefere_a_entidade_dona_de_uma_pagina():
    pagina = ent("Intel", principal=True, n_docs=1)
    citada = ent("Intel Corporation", aliases=["Intel"], n_docs=40)
    r = resolver(tabela_de(pagina, citada))
    assert r.mapa[citada.id] == pagina.id


def test_aliases_do_absorvido_sao_preservados_e_canonico_nao_vira_alias_de_si_mesmo():
    a = ent("NSA", aliases=["Agência de Segurança Nacional"], n_docs=2)
    b = ent("Agência de Segurança Nacional", aliases=["Agência Nacional de Segurança"])
    fundida = resolver(tabela_de(a, b)).entidades[a.id]
    assert {"Agência Nacional de Segurança", "Agência de Segurança Nacional"} <= fundida.aliases
    assert "NSA" not in fundida.aliases


# ----------------------------------------------------------------------------- o que NÃO deve fundir
def test_apelido_compartilhado_nao_basta():
    """'Mark I' é apelido das duas máquinas, mas Harvard Mark I e Manchester Mark I são diferentes."""
    harvard = ent("Harvard Mark I", "Hardware", aliases=["Mark I"])
    manchester = ent("Manchester Mark I", "Hardware", aliases=["Mark I"])
    assert len(resolver(tabela_de(harvard, manchester)).entidades) == 2


def test_modelos_que_so_diferem_no_numero_nao_fundem():
    pares = [("Intel 8086", "Intel 80286"), ("Motorola 68000", "Motorola 6800"),
             ("Windows 8", "Windows 98"), ("Pentium II", "Pentium III"), ("MicroVAX I", "MicroVAX II")]
    for a, b in pares:
        r = resolver(tabela_de(ent(a, "Hardware"), ent(b, "Hardware")))
        assert len(r.entidades) == 2, f"{a} e {b} foram fundidos por engano"


def test_numeros_do_nome():
    assert numeros_do_nome("Intel 80286") == {"80286"}
    assert numeros_do_nome("Pentium III") == {"iii"}
    assert numeros_do_nome("Alan Turing") == frozenset()


def test_tipos_diferentes_nunca_fundem_mesmo_com_nome_igual():
    pessoa = ent("Turing", "Pessoa")
    premio = ent("Turing", "Premio")
    assert len(resolver(tabela_de(pessoa, premio)).entidades) == 2


def test_par_bloqueado_manualmente_nao_funde(tmp_path):
    a = ent("Macintosh", "Hardware", aliases=["Macintosh 128K"])
    b = ent("Macintosh 128K", "Hardware")
    arquivo = tmp_path / "manual.json"
    arquivo.write_text(json.dumps({"nao_fundir": [[a.id, b.id]]}))
    assert len(resolver(tabela_de(a, b), manual=arquivo).entidades) == 2
    assert len(resolver(tabela_de(a, b)).entidades) == 1  # sem o bloqueio, fundiria


def test_bloqueio_vale_tambem_por_transitividade(tmp_path):
    """A~B e B~C seriam fundidos; como A e C estão bloqueados, C tem que ficar de fora."""
    a = ent("Alfa Sistemas", n_docs=3, aliases=["Beta Sistemas"])
    b = ent("Beta Sistemas", n_docs=2, aliases=["Gama Sistemas"])
    c = ent("Gama Sistemas", n_docs=1)
    arquivo = tmp_path / "manual.json"
    arquivo.write_text(json.dumps({"nao_fundir": [[a.id, c.id]]}))
    r = resolver(tabela_de(a, b, c), manual=arquivo)
    assert r.mapa[a.id] != r.mapa[c.id]


# ----------------------------------------------------------------------------- manual
def test_fusao_manual_define_o_canonico(tmp_path):
    tcp = ent("TCP", "Tecnologia", n_docs=1)
    longo = ent("Transmission Control Protocol", "Tecnologia", n_docs=9)
    arquivo = tmp_path / "manual.json"
    arquivo.write_text(json.dumps({"fundir": [[tcp.id, longo.id]]}))
    r = resolver(tabela_de(tcp, longo), manual=arquivo)
    assert r.mapa[longo.id] == tcp.id
    assert r.fusoes[0].regra == "manual"


def test_fusao_manual_entre_tipos_diferentes_e_ignorada(tmp_path):
    a, b = ent("X", "Pessoa"), ent("Y", "Premio")
    arquivo = tmp_path / "manual.json"
    arquivo.write_text(json.dumps({"fundir": [[a.id, b.id]]}))
    assert len(resolver(tabela_de(a, b), manual=arquivo).entidades) == 2


# ----------------------------------------------------------------------------- estabilidade
def test_resultado_nao_depende_da_ordem_de_entrada():
    nomes = [("NSA", ["Agência de Segurança Nacional"]), ("Agência de Segurança Nacional", []),
             ("AT&T", []), ("AT&T Corporation", []), ("Intel 8086", []), ("Intel 80286", [])]
    base = [ent(n, aliases=a) for n, a in nomes]
    esperado = resolver(tabela_de(*base)).mapa
    for semente in range(5):
        embaralhado = base[:]
        random.Random(semente).shuffle(embaralhado)
        assert resolver(tabela_de(*embaralhado)).mapa == esperado


def test_resolver_duas_vezes_da_o_mesmo_resultado():
    t = tabela_de(ent("NSA", aliases=["Agência de Segurança Nacional"]), ent("Agência de Segurança Nacional"))
    assert resolver(t).mapa == resolver(t).mapa


# ----------------------------------------------------------------------------- embedding só sugere
def test_embedding_gera_candidato_mas_nunca_funde():
    a, b = ent("Empresa Alfa"), ent("Companhia Beta")
    vetores = {a.id: [1.0, 0.0], b.id: [0.99, 0.01]}
    r = resolver(tabela_de(a, b), embeddings=vetores)
    assert len(r.entidades) == 2  # nada foi fundido
    assert r.candidatos_revisao and r.candidatos_revisao[0]["cosseno"] > 0.93


# ----------------------------------------------------------------------------- agregação e relações
def test_agregar_junta_a_mesma_entidade_de_documentos_diferentes():
    d1 = doc_minimo(1, [e_json("Bell Labs", "Organizacao", ["1_0"], aliases=["Laboratórios Bell"])])
    d2 = doc_minimo(2, [e_json("Bell Labs", "Organizacao", ["2_0"], aliases=["Bell Telephone Laboratories"])])
    t = agregar_entidades([d1, d2])
    e = t["organizacao:bell_labs"]
    assert e.n_docs == 2
    assert e.aliases == {"Laboratórios Bell", "Bell Telephone Laboratories"}


def test_relacoes_repetidas_viram_uma_com_todos_os_chunks():
    p, o = e_json("Ken Thompson", "Pessoa", ["1_0", "1_1"]), e_json("Bell Labs", "Organizacao", ["1_0", "1_1"])
    rels = [r_json(p["id"], "TRABALHOU_EM", o["id"], "1_0", 0.7, "ev A"),
            r_json(p["id"], "TRABALHOU_EM", o["id"], "1_1", 0.95, "ev B", cargo="pesquisador")]
    agregadas, est = remapear_relacoes([doc_minimo(1, [p, o], rels)], {})
    assert len(agregadas) == 1
    r = agregadas[0]
    assert r["chunk_ids"] == ["1_0", "1_1"]
    assert r["confianca"] == 0.95 and r["evidencia"] == "ev B"  # fica a evidência mais confiável
    assert r["propriedades"] == {"cargo": "pesquisador"}
    assert est["relacoes_de_entrada"] == 2 and est["relacoes_agregadas"] == 1


def test_relacoes_sao_reapontadas_para_o_canonico():
    p = e_json("Ken Thompson", "Pessoa", ["1_0"])
    velho, novo = e_json("Laboratórios Bell", "Organizacao", ["1_0"]), e_json("Bell Labs", "Organizacao", ["1_1"])
    rels = [r_json(p["id"], "TRABALHOU_EM", velho["id"], "1_0"), r_json(p["id"], "TRABALHOU_EM", novo["id"], "1_1")]
    mapa = {velho["id"]: novo["id"]}
    agregadas, _ = remapear_relacoes([doc_minimo(1, [p, velho, novo], rels)], mapa)
    assert len(agregadas) == 1 and agregadas[0]["destino"] == novo["id"]
    assert agregadas[0]["chunk_ids"] == ["1_0", "1_1"]


def test_relacao_que_vira_autorreferente_apos_a_fusao_e_descartada():
    a, b = e_json("NSA", "Organizacao", ["1_0"]), e_json("Agência de Segurança Nacional", "Organizacao", ["1_0"])
    rels = [r_json(a["id"], "SUCESSOR_DE", b["id"], "1_0")]
    agregadas, est = remapear_relacoes([doc_minimo(1, [a, b], rels)], {b["id"]: a["id"]})
    assert agregadas == [] and est["autorreferentes_apos_fusao"] == 1


def test_colaboracao_e_simetrica_depois_de_remapear():
    x, y = e_json("Ana", "Pessoa", ["1_0"]), e_json("Zeca", "Pessoa", ["1_0"])
    d1 = doc_minimo(1, [x, y], [r_json(x["id"], "COLABOROU_COM", y["id"], "1_0")])
    d2 = doc_minimo(2, [x, y], [r_json(y["id"], "COLABOROU_COM", x["id"], "2_0")])
    agregadas, _ = remapear_relacoes([d1, d2], {})
    assert len(agregadas) == 1 and len(agregadas[0]["chunk_ids"]) == 2


def test_remapear_e_idempotente():
    p, o = e_json("Ken", "Pessoa", ["1_0"]), e_json("Bell Labs", "Organizacao", ["1_0"])
    docs = [doc_minimo(1, [p, o], [r_json(p["id"], "TRABALHOU_EM", o["id"], "1_0")])]
    assert remapear_relacoes(docs, {})[0] == remapear_relacoes(docs, {})[0]
