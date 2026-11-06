"""
Resolução de entidades (Pessoa 3).

A Pessoa 2 gera um JSON por documento e o mesmo `id` (regra de `gerar_id`) já une as
menções idênticas entre páginas. Sobram as variações que a regra não pega, por exemplo
"NSA" x "Agência de Segurança Nacional" ou "AT&T" x "AT&T Corporation".

Estratégia em camadas, sempre DENTRO DO MESMO TIPO de entidade e do mais seguro ao mais arriscado:

1. Nome de uma entidade é apelido da outra (ou os nomes são iguais após normalizar).
   Atenção: dividir um apelido NÃO basta ("Mark I" é apelido do Harvard Mark I e do
   Manchester Mark I, que são máquinas diferentes).
2. Nomes iguais depois de tirar sufixos corporativos ("AT&T Corporation" -> "AT&T").
3. Nomes quase iguais (rapidfuzz) com limiar alto.
4. Similaridade de embedding: só GERA CANDIDATOS para revisão humana, nunca funde sozinha.

Ajustes manuais ficam em data/resolucao_manual.json ("fundir" e "nao_fundir").
Toda fusão é registrada (regra + motivo) para a revisão e para o artigo.

Créditos: rapidfuzz (MIT) para a similaridade de strings.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from src.montagem import normalizar

LIMIAR_FUZZY = 94  # 0-100; alto de propósito (preferimos deixar duplicata a fundir entidades diferentes)
LIMIAR_EMBEDDING = 0.93  # cosseno; só gera candidato para revisão

# Palavras que não mudam a identidade de uma organização ("AT&T Corporation" == "AT&T").
SUFIXOS_CORPORATIVOS = {
    "inc", "corp", "corporation", "co", "company", "ltd", "llc", "gmbh", "sa", "ag", "plc",
    "corporacao", "companhia", "ltda",
}


# ----------------------------------------------------------------------------- dados
@dataclass
class Entidade:
    """Uma entidade do corpus, já agregada entre todos os documentos."""

    id: str
    nome: str
    tipo: str
    aliases: set[str] = field(default_factory=set)
    descricao: str | None = None
    propriedades: dict = field(default_factory=dict)
    n_docs: int = 0
    principal: bool = False  # é a entidade "principal" (dona da página) de algum documento
    wikidata_id: str | None = None

    def formas(self) -> set[str]:
        return {self.nome, *self.aliases}


@dataclass
class Fusao:
    canonico: str
    absorvido: str
    regra: str
    motivo: str


@dataclass
class Resultado:
    entidades: dict[str, Entidade]  # id canônico -> entidade fundida
    mapa: dict[str, str]  # id original -> id canônico (inclui os que não mudaram)
    fusoes: list[Fusao]
    candidatos_revisao: list[dict]


# ----------------------------------------------------------------------------- agregação
def agregar_entidades(documentos: list[dict]) -> dict[str, Entidade]:
    """Junta as entidades de todos os documentos validados em uma só tabela por id."""
    tabela: dict[str, Entidade] = {}
    for doc in documentos:
        for e in doc["entidades"]:
            x = tabela.get(e["id"])
            if x is None:
                x = tabela[e["id"]] = Entidade(id=e["id"], nome=e["nome"], tipo=e["tipo"])
            x.aliases |= {a for a in e.get("aliases") or [] if a and a != x.nome}
            x.n_docs += 1
            # descrição: fica a mais longa (costuma ser a mais informativa)
            d = e.get("descricao")
            if d and (not x.descricao or len(d) > len(x.descricao)):
                x.descricao = d
            for k, v in (e.get("propriedades") or {}).items():
                x.propriedades.setdefault(k, v)  # primeiro valor vence; conflitos são raros
        pid = doc["entidade_principal"]
        if pid in tabela:
            tabela[pid].principal = True
            tabela[pid].wikidata_id = doc["documento"].get("wikidata_id")
    return tabela


# ----------------------------------------------------------------------------- comparações
def chave_sem_sufixo(nome: str) -> str:
    """Nome normalizado sem sufixos corporativos: 'AT&T Corporation' -> 'at t'."""
    palavras = [p for p in normalizar(nome).split() if p not in SUFIXOS_CORPORATIVOS]
    return " ".join(palavras)


def _nome_e_apelido(a: Entidade, b: Entidade) -> str | None:
    na, nb = normalizar(a.nome), normalizar(b.nome)
    if na == nb:
        return f"nomes iguais após normalizar: '{a.nome}' == '{b.nome}'"
    if na in {normalizar(x) for x in b.aliases}:
        return f"nome '{a.nome}' é apelido de '{b.nome}'"
    if nb in {normalizar(x) for x in a.aliases}:
        return f"nome '{b.nome}' é apelido de '{a.nome}'"
    return None


def _sufixo(a: Entidade, b: Entidade) -> str | None:
    ka, kb = chave_sem_sufixo(a.nome), chave_sem_sufixo(b.nome)
    if ka and ka == kb and normalizar(a.nome) != normalizar(b.nome):
        return f"iguais sem sufixo corporativo: '{a.nome}' ~ '{b.nome}'"
    return None


ROMANOS = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}


def numeros_do_nome(nome: str) -> frozenset[str]:
    """Tokens com dígitos ou algarismos romanos: distinguem modelos e versões."""
    return frozenset(t for t in normalizar(nome).split() if t in ROMANOS or any(c.isdigit() for c in t))


def _fuzzy(a: Entidade, b: Entidade) -> str | None:
    ka, kb = chave_sem_sufixo(a.nome), chave_sem_sufixo(b.nome)
    if min(len(ka), len(kb)) < 6:  # nomes curtos (siglas) enganam o fuzzy
        return None
    # "Intel 80286" x "Intel 8086", "Windows 8" x "Windows 98": só o número muda, são coisas diferentes.
    if numeros_do_nome(a.nome) != numeros_do_nome(b.nome):
        return None
    nota = fuzz.token_sort_ratio(ka, kb)
    if nota >= LIMIAR_FUZZY:
        return f"nomes quase iguais ({nota:.0f}): '{a.nome}' ~ '{b.nome}'"
    return None


REGRAS = [("apelido", _nome_e_apelido), ("sufixo", _sufixo), ("fuzzy", _fuzzy)]


# ----------------------------------------------------------------------------- union-find
class _Grupos:
    """Union-find que RECUSA uma união se ela juntaria um par bloqueado (inclusive via transitividade)."""

    def __init__(self, ids, bloqueados: set[frozenset]):
        self.pai = {i: i for i in ids}
        self.membros = {i: {i} for i in ids}
        self.bloqueados = bloqueados

    def achar(self, i):
        while self.pai[i] != i:
            self.pai[i] = self.pai[self.pai[i]]
            i = self.pai[i]
        return i

    def unir(self, a, b) -> bool:
        ra, rb = self.achar(a), self.achar(b)
        if ra == rb:
            return True
        if any(_par(x, y) in self.bloqueados for x in self.membros[ra] for y in self.membros[rb]):
            return False
        self.pai[rb] = ra
        self.membros[ra] |= self.membros.pop(rb)
        return True


def _par(a: str, b: str) -> frozenset:
    return frozenset((a, b))


def carregar_manual(caminho: Path | None) -> tuple[list[tuple[str, str]], set[frozenset]]:
    """Lê data/resolucao_manual.json. Em "fundir", o PRIMEIRO id de cada par é o nome canônico preferido."""
    if not caminho or not Path(caminho).exists():
        return [], set()
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    fundir = [tuple(p) for p in dados.get("fundir", [])]
    bloquear = {_par(*p) for p in dados.get("nao_fundir", [])}
    return fundir, bloquear


def _escolher_canonico(membros: list[Entidade], preferidos: set[str] = frozenset()) -> Entidade:
    """Prioridade: preferido no arquivo manual > dona de uma página > mais documentos > nome mais longo."""
    return max(membros, key=lambda e: (e.id in preferidos, e.principal, e.n_docs, len(e.nome), e.id))


# ----------------------------------------------------------------------------- resolução
def resolver(
    tabela: dict[str, Entidade],
    manual: Path | None = None,
    embeddings: dict[str, list[float]] | None = None,
) -> Resultado:
    """
    Resolve duplicatas. `embeddings` (id -> vetor normalizado) é opcional e só serve para
    sugerir candidatos de revisão.
    """
    fundir_manual, bloqueados = carregar_manual(manual)
    grupos = _Grupos(tabela, bloqueados)
    motivo_da_uniao: dict[frozenset, tuple[str, str]] = {}
    preferidos = {par[0] for par in fundir_manual}

    por_tipo: dict[str, list[Entidade]] = defaultdict(list)
    for e in tabela.values():
        por_tipo[e.tipo].append(e)

    # 1-3) regras automáticas, par a par dentro do tipo.
    # Pré-filtro por índice de palavras para não comparar todos contra todos.
    propostas: list[tuple[int, str, str, str, str]] = []  # (prioridade, id_a, id_b, regra, motivo)
    for tipo, lista in por_tipo.items():
        indice: dict[str, list[Entidade]] = defaultdict(list)
        for e in lista:
            for forma in e.formas():
                for palavra in set(normalizar(forma).split()):
                    if len(palavra) > 1:
                        indice[palavra].append(e)
        vistos: set[frozenset] = set()
        for grupo in indice.values():
            if len(grupo) > 150:  # palavra comum demais ("de", "computer"): não gera pares
                continue
            for i, a in enumerate(grupo):
                for b in grupo[i + 1 :]:
                    p = _par(a.id, b.id)
                    if a.id == b.id or p in vistos:
                        continue
                    vistos.add(p)
                    if p in bloqueados:
                        continue
                    for prioridade, (nome_regra, regra) in enumerate(REGRAS):
                        motivo = regra(a, b)
                        if motivo:
                            x, y = sorted((a.id, b.id))
                            propostas.append((prioridade, x, y, nome_regra, motivo))
                            break

    # manual primeiro (prioridade -1), depois as regras mais seguras; ordem fixa = resultado reproduzível
    for a, b in fundir_manual:
        if a in tabela and b in tabela and tabela[a].tipo == tabela[b].tipo:
            propostas.append((-1, a, b, "manual", "definido em resolucao_manual.json"))
    for _, a, b, nome_regra, motivo in sorted(propostas):
        if grupos.unir(a, b):
            motivo_da_uniao.setdefault(_par(a, b), (nome_regra, motivo))

    membros: dict[str, list[Entidade]] = defaultdict(list)
    for i in tabela:
        membros[grupos.achar(i)].append(tabela[i])

    entidades: dict[str, Entidade] = {}
    mapa: dict[str, str] = {}
    fusoes: list[Fusao] = []
    for lista in membros.values():
        canon = _escolher_canonico(lista, preferidos)
        fundida = Entidade(
            id=canon.id, nome=canon.nome, tipo=canon.tipo, descricao=canon.descricao,
            propriedades=dict(canon.propriedades), n_docs=sum(m.n_docs for m in lista),
            principal=any(m.principal for m in lista), wikidata_id=canon.wikidata_id,
        )
        for m in sorted(lista, key=lambda e: e.id):
            mapa[m.id] = canon.id
            if m.id == canon.id:
                fundida.aliases |= m.aliases
                continue
            fundida.aliases |= m.formas()
            if not fundida.descricao:
                fundida.descricao = m.descricao
            for k, v in m.propriedades.items():
                fundida.propriedades.setdefault(k, v)
            fundida.wikidata_id = fundida.wikidata_id or m.wikidata_id
            regra, motivo = _motivo(motivo_da_uniao, m.id, canon.id, [x.id for x in lista])
            fusoes.append(Fusao(canon.id, m.id, regra, motivo))
        fundida.aliases = {a for a in fundida.aliases if normalizar(a) != normalizar(fundida.nome)}
        entidades[fundida.id] = fundida

    candidatos = _candidatos_embedding(entidades, embeddings, bloqueados) if embeddings else []
    return Resultado(entidades, mapa, sorted(fusoes, key=lambda f: (f.canonico, f.absorvido)), candidatos)


def _motivo(motivos, absorvido, canonico, todos):
    """Motivo da fusão: direto com o canônico, ou o primeiro encontrado no grupo."""
    direto = motivos.get(_par(absorvido, canonico))
    if direto:
        return direto
    for outro in todos:
        m = motivos.get(_par(absorvido, outro))
        if m:
            return m[0], m[1] + " (transitivo)"
    return "desconhecida", ""


def _candidatos_embedding(entidades, embeddings, bloqueados) -> list[dict]:
    """Pares do mesmo tipo com cosseno alto que NÃO foram fundidos: ficam para revisão."""
    import numpy as np

    saida = []
    for tipo in {e.tipo for e in entidades.values()}:
        ids = [i for i, e in entidades.items() if e.tipo == tipo and i in embeddings]
        if len(ids) < 2:
            continue
        m = np.array([embeddings[i] for i in ids], dtype=float)
        sim = m @ m.T
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                if sim[x, y] >= LIMIAR_EMBEDDING and _par(ids[x], ids[y]) not in bloqueados:
                    saida.append({"a": ids[x], "b": ids[y], "cosseno": round(float(sim[x, y]), 4)})
    return sorted(saida, key=lambda c: -c["cosseno"])


# ----------------------------------------------------------------------------- relações
def remapear_relacoes(documentos: list[dict], mapa: dict[str, str]) -> tuple[list[dict], dict[str, int]]:
    """
    Reaponta as relações para os ids canônicos e junta as repetidas (mesma origem, tipo e
    destino) em uma só, acumulando `chunk_ids`. Devolve (relacoes_agregadas, estatisticas).
    """
    est = {"relacoes_de_entrada": 0, "autorreferentes_apos_fusao": 0, "relacoes_agregadas": 0}
    acumulado: dict[tuple, dict] = {}
    for doc in documentos:
        for r in doc["relacoes"]:
            est["relacoes_de_entrada"] += 1
            o, d = mapa.get(r["origem"], r["origem"]), mapa.get(r["destino"], r["destino"])
            if o == d:
                est["autorreferentes_apos_fusao"] += 1
                continue
            if r["tipo"] == "COLABOROU_COM" and o > d:  # relação simétrica: ordem fixa
                o, d = d, o
            chave = (o, r["tipo"], d)
            a = acumulado.get(chave)
            if a is None:
                a = acumulado[chave] = {
                    "origem": o, "tipo": r["tipo"], "destino": d, "propriedades": {},
                    "chunk_ids": [], "confianca": 0.0, "evidencia": "",
                }
            if r["chunk_id"] not in a["chunk_ids"]:
                a["chunk_ids"].append(r["chunk_id"])
            if r["confianca"] > a["confianca"] or not a["evidencia"]:
                a["confianca"], a["evidencia"] = r["confianca"], r["evidencia"]
            for k, v in (r.get("propriedades") or {}).items():
                a["propriedades"].setdefault(k, v)
    for a in acumulado.values():
        a["chunk_ids"].sort(key=_ordem_chunk)
        a["confianca"] = round(a["confianca"], 2)
    est["relacoes_agregadas"] = len(acumulado)
    return sorted(acumulado.values(), key=lambda a: (a["origem"], a["tipo"], a["destino"])), est


def _ordem_chunk(cid: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)_(\d+)", cid)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
