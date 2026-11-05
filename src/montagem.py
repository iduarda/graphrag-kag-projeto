"""
Montagem do JSON final de extração (Pessoa 2).

O LLM devolve, para cada chunk, entidades e relações "brutas" (relações apontam para
entidades pelo NOME). Este módulo transforma essas respostas no JSON do contrato
(docs/formato_extracao.md), aplicando as checagens contra alucinação:

1. Tipos de entidade e de relação fora do modelo são descartados.
2. Nomes são normalizados pela lista canônica (data/entidades_canonicas.json):
   "Laboratórios Bell" -> "Bell Labs", "MIT" -> "Instituto de Tecnologia de Massachusetts".
3. Propriedades fora da tabela, anos que não são inteiros e subtipos inválidos são removidos.
4. Relações com origem/destino de tipo errado são descartadas; se o tipo só estiver
   invertido (ex.: CRIOU Linguagem -> Pessoa), a direção é corrigida.
5. A `evidencia` precisa existir LITERALMENTE no chunk (ignorando espaços, maiúsculas e
   aspas). Se não existir, a relação é descartada.
6. Relações com confiança < CONFIANCA_MINIMA são descartadas.
7. Entidades repetidas em vários chunks são juntadas em uma só (chunk_ids, aliases).

Cada descarte/correção é contado em `estatisticas` para o artigo.
"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from src.contrato import SUBTIPOS, TIPOS_ENTIDADE, TIPOS_RELACAO, VERSAO_FORMATO, gerar_id, validar_extracao

CONFIANCA_MINIMA = 0.6
RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_CANONICAS = RAIZ / "data" / "entidades_canonicas.json"

# Erros comuns de grafia do tipo -> tipo correto
_SINONIMOS_TIPO = {"organização": "Organizacao", "organizacao": "Organizacao", "prêmio": "Premio",
                   "premio": "Premio", "pessoa": "Pessoa", "linguagem": "Linguagem", "hardware": "Hardware",
                   "tecnologia": "Tecnologia", "local": "Local"}


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, só letras/dígitos separados por um espaço."""
    t = unicodedata.normalize("NFKD", str(texto).lower()).encode("ascii", "ignore").decode()
    t = t.replace("+", " p ").replace("#", " sharp ")
    return " ".join(re.findall(r"[a-z0-9]+", t))


# ------------------------------------------------------------------ lista canônica
class Canonicas:
    def __init__(self, caminho: Path = ARQUIVO_CANONICAS):
        self.lista = json.loads(caminho.read_text(encoding="utf-8"))
        self.por_nome: dict[str, dict] = {}
        self.por_alias: dict[tuple[str, str], dict] = {}
        self.por_arquivo: dict[str, dict] = {}
        for e in self.lista:
            self.por_nome[normalizar(e["nome"])] = e
            for a in e["aliases"]:
                self.por_alias.setdefault((normalizar(a), e["tipo"]), e)
            if e.get("arquivo"):
                self.por_arquivo[e["arquivo"]] = e

    def resolver(self, nome: str, tipo: str) -> dict | None:
        """Nome exato da lista vale para qualquer tipo; apelido só vale para o mesmo tipo."""
        n = normalizar(nome)
        if n in self.por_nome:
            return self.por_nome[n]
        return self.por_alias.get((n, tipo))

    def texto_para_prompt(self) -> str:
        linhas = []
        for e in self.lista:
            apelidos = ", ".join(e["aliases"][:6])
            linhas.append(f"{e['nome']} | {e['tipo']}" + (f" | {apelidos}" if apelidos else ""))
        return "\n".join(linhas)


# ------------------------------------------------------------------ propriedades
def _como_ano(valor):
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if 1000 <= valor <= 2100 else None
    if isinstance(valor, float) and valor.is_integer():
        return _como_ano(int(valor))
    if isinstance(valor, str) and re.fullmatch(r"\s*\d{4}\s*", valor):
        return _como_ano(int(valor))
    return None


def _limpar_props(props, aceitas: set[str], subtipos: set[str] | None, est: Counter, prefixo: str) -> dict:
    if not isinstance(props, dict):
        return {}
    limpas = {}
    for chave, valor in props.items():
        if valor in (None, "", []):
            continue
        if chave not in aceitas:
            est[f"{prefixo}_prop_fora_do_modelo"] += 1
            continue
        if chave.startswith("ano"):
            ano = _como_ano(valor)
            if ano is None:
                est[f"{prefixo}_ano_invalido"] += 1
                continue
            if not isinstance(valor, int):
                est[f"{prefixo}_ano_convertido"] += 1
            limpas[chave] = ano
        elif chave == "tipo":
            v = normalizar(valor).replace(" ", "_")
            if subtipos and v in subtipos:
                limpas[chave] = v
            else:
                est[f"{prefixo}_subtipo_invalido"] += 1
        elif chave == "paradigmas":
            if isinstance(valor, str):
                valor = [valor]
            if isinstance(valor, list):
                limpas[chave] = [str(v) for v in valor if v]
        else:
            limpas[chave] = str(valor)
    return limpas


# ------------------------------------------------------------------ evidência
_ASPAS = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "´": "'", "`": "'",
                        "–": "-", "—": "-"})


def localizar_evidencia(evidencia: str, texto: str) -> str | None:
    """Devolve o trecho ORIGINAL do chunk que corresponde à evidência, ou None se não existir."""
    if not evidencia or not isinstance(evidencia, str):
        return None
    ev = evidencia.strip().strip('"').strip()
    if not ev:
        return None
    if ev in texto:
        return ev
    # tolera diferenças de espaços, maiúsculas e tipos de aspas/traços
    palavras = re.findall(r"\w+|[^\w\s]", ev.translate(_ASPAS), flags=re.UNICODE)
    if len(palavras) < 2:
        return None
    padrao = r"\s*".join(re.escape(p) for p in palavras)
    m = re.search(padrao, texto.translate(_ASPAS), flags=re.IGNORECASE)
    if m:  # translate não muda o tamanho do texto, então os índices valem no original
        return texto[m.start():m.end()]
    return None


# ------------------------------------------------------------------ montagem
def _tipo_valido(tipo) -> str | None:
    if tipo in TIPOS_ENTIDADE:
        return tipo
    return _SINONIMOS_TIPO.get(str(tipo).strip().lower())


def _relacao_cabe(tipo_rel: str, t_o: str, t_d: str) -> bool:
    origens, destinos, _ = TIPOS_RELACAO[tipo_rel]
    if t_o not in origens:
        return False
    return t_d == t_o if destinos == "MESMO" else t_d in destinos


def montar(chunks_doc: dict, bruto: dict, canon: Canonicas, nome_arquivo: str) -> tuple[dict, Counter]:
    """
    chunks_doc: conteúdo de data/chunks/<arquivo>.json  ({"documento", "chunks"})
    bruto:      conteúdo de data/extracoes_brutas/<arquivo>.json
                ({"modelo_llm", "data", "chunks": [{"chunk_id", "entidades", "relacoes"}]})
    Retorna (json_final, estatisticas).
    """
    est: Counter = Counter()
    doc = chunks_doc["documento"]
    chunks = chunks_doc["chunks"]
    texto_chunk = {c["id"]: c["texto"] for c in chunks}

    entidades: dict[str, dict] = {}
    nome_para_id_doc: dict[str, str] = {}

    def registrar_entidade(nome, tipo, aliases, descricao, props, chunk_id, conta=True) -> str | None:
        if conta:
            est["entidades_brutas"] += 1
        tipo_ok = _tipo_valido(tipo)
        if not nome or not isinstance(nome, str) or not tipo_ok:
            est["entidade_tipo_invalido"] += 1
            return None
        if tipo_ok != tipo:
            est["entidade_tipo_corrigido"] += 1
        nome = nome.strip()
        c = canon.resolver(nome, tipo_ok)
        if c:
            if normalizar(c["nome"]) != normalizar(nome):
                est["entidade_nome_canonizado"] += 1
                aliases = list(aliases or []) + [nome]
            if c["tipo"] != tipo_ok:
                est["entidade_tipo_canonizado"] += 1
            nome, tipo_ok = c["nome"], c["tipo"]
        eid = gerar_id(tipo_ok, nome)
        if eid.endswith(":"):
            est["entidade_tipo_invalido"] += 1
            return None
        props = _limpar_props(props, TIPOS_ENTIDADE[tipo_ok], SUBTIPOS.get(tipo_ok), est, "entidade")
        if c:
            for k, v in c.get("propriedades", {}).items():
                props.setdefault(k, v)
        ent = entidades.get(eid)
        if ent is None:
            ent = entidades[eid] = {"id": eid, "nome": nome, "tipo": tipo_ok, "aliases": [],
                                    "descricao": None, "propriedades": {}, "chunk_ids": []}
            if c:
                ent["aliases"] = list(c["aliases"])
        for a in aliases or []:
            if isinstance(a, str) and a.strip() and normalizar(a) != normalizar(nome) \
                    and normalizar(a) not in {normalizar(x) for x in ent["aliases"]}:
                ent["aliases"].append(a.strip())
        if descricao and isinstance(descricao, str) and not ent["descricao"]:
            ent["descricao"] = descricao.strip()
        for k, v in props.items():
            if k not in ent["propriedades"]:
                ent["propriedades"][k] = v
            elif ent["propriedades"][k] != v:
                est["entidade_prop_conflitante"] += 1
        if chunk_id and chunk_id not in ent["chunk_ids"]:
            ent["chunk_ids"].append(chunk_id)
        for forma in [nome, *ent["aliases"]]:
            nome_para_id_doc.setdefault(normalizar(forma), eid)
        return eid

    # entidade principal (vem da lista canônica, não do LLM)
    principal = canon.por_arquivo.get(nome_arquivo)
    if principal is None:  # documento fora da lista: usa o título
        nome_p = re.sub(r"\s*\(.*?\)\s*$", "", doc["titulo"])
        principal = {"nome": nome_p, "tipo": "Tecnologia", "aliases": [doc["titulo"]], "propriedades": {}}
    id_principal = gerar_id(principal["tipo"], principal["nome"])

    relacoes: list[dict] = []
    vistas: set[tuple] = set()

    for resp in bruto.get("chunks", []):
        cid = resp.get("chunk_id")
        if cid not in texto_chunk:
            est["chunk_desconhecido"] += 1
            continue
        texto = texto_chunk[cid]
        nome_para_id_chunk: dict[str, str] = {}
        for e in resp.get("entidades", []) or []:
            if not isinstance(e, dict):
                continue
            eid = registrar_entidade(e.get("nome"), e.get("tipo"), e.get("aliases"), e.get("descricao"),
                                     e.get("propriedades"), cid)
            if eid:
                for forma in [e.get("nome"), *(e.get("aliases") or [])]:
                    if isinstance(forma, str):
                        nome_para_id_chunk[normalizar(forma)] = eid

        for r in resp.get("relacoes", []) or []:
            if not isinstance(r, dict):
                continue
            est["relacoes_brutas"] += 1
            tipo_rel = str(r.get("tipo", "")).strip().upper()
            if tipo_rel not in TIPOS_RELACAO:
                est["relacao_tipo_fora_do_modelo"] += 1
                continue

            def achar(nome):
                n = normalizar(nome or "")
                eid = nome_para_id_chunk.get(n) or nome_para_id_doc.get(n)
                if eid is None:
                    c = canon.resolver(nome or "", "")
                    if c:
                        eid = nome_para_id_doc.get(normalizar(c["nome"]))
                return eid

            o, d = achar(r.get("origem")), achar(r.get("destino"))
            if not o or not d:
                est["relacao_entidade_inexistente"] += 1
                continue
            if o == d:
                est["relacao_autorreferente"] += 1
                continue
            t_o, t_d = entidades[o]["tipo"], entidades[d]["tipo"]
            if not _relacao_cabe(tipo_rel, t_o, t_d):
                if t_o != t_d and _relacao_cabe(tipo_rel, t_d, t_o):
                    o, d = d, o
                    est["relacao_direcao_corrigida"] += 1
                else:
                    est["relacao_tipos_incompativeis"] += 1
                    continue

            evid = localizar_evidencia(r.get("evidencia"), texto)
            if evid is None:
                est["relacao_evidencia_nao_encontrada"] += 1
                continue
            if evid != r.get("evidencia"):
                est["relacao_evidencia_ajustada"] += 1

            conf = r.get("confianca")
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                est["relacao_confianca_invalida"] += 1
                continue
            conf = min(max(conf, 0.0), 1.0)
            if conf < CONFIANCA_MINIMA:
                est["relacao_confianca_baixa"] += 1
                continue

            if tipo_rel == "COLABOROU_COM" and o > d:  # relação simétrica: ordem fixa
                o, d = d, o
            chave = (o, tipo_rel, d, cid)
            if chave in vistas:
                est["relacao_duplicada"] += 1
                continue
            vistas.add(chave)

            props = _limpar_props(r.get("propriedades"), TIPOS_RELACAO[tipo_rel][2], None, est, "relacao")
            for eid in (o, d):  # a relação também prova que a entidade aparece no chunk
                if cid not in entidades[eid]["chunk_ids"]:
                    entidades[eid]["chunk_ids"].append(cid)
            relacoes.append({"origem": o, "tipo": tipo_rel, "destino": d, "propriedades": props,
                             "chunk_id": cid, "evidencia": evid, "confianca": round(conf, 2)})

    # garante a entidade principal, com os chunks em que o nome aparece
    if id_principal not in entidades:
        est["principal_adicionada"] += 1
    formas = [normalizar(f) for f in [principal["nome"], *principal["aliases"]] if len(normalizar(f)) > 2]
    cids = [c["id"] for c in chunks if any(f" {f} " in f" {normalizar(c['texto'])} " for f in formas)]
    for i, cid in enumerate(cids or [chunks[0]["id"]]):
        registrar_entidade(principal["nome"], principal["tipo"], principal["aliases"], None,
                           principal.get("propriedades"), cid, conta=False)

    # ordena chunk_ids pela ordem do documento e limpa campos vazios
    ordem = {c["id"]: c["ordem"] for c in chunks}
    lista_ent = []
    for ent in entidades.values():
        ent["chunk_ids"].sort(key=ordem.get)
        if not ent["descricao"]:
            del ent["descricao"]
        lista_ent.append(ent)

    final = {
        "versao_formato": VERSAO_FORMATO,
        "documento": doc,
        "extracao": {"modelo_llm": bruto.get("modelo_llm"), "data": bruto.get("data"),
                     "prompt": "prompts/extracao.md", "confianca_minima": CONFIANCA_MINIMA},
        "entidade_principal": id_principal,
        "chunks": chunks,
        "entidades": lista_ent,
        "relacoes": relacoes,
    }
    est["entidades_finais"] = len(lista_ent)
    est["relacoes_finais"] = len(relacoes)
    return final, est


def montagem_ingenua(chunks_doc: dict, bruto: dict, nome_arquivo: str, canon: Canonicas) -> dict:
    """
    Monta o JSON SEM nenhuma correção (só gerar_id e junção por id), para medir quantos
    arquivos o LLM acertaria "de primeira" no validador (src/contrato.py).
    """
    ents: dict[str, dict] = {}
    rels = []
    for resp in bruto.get("chunks", []):
        cid = resp.get("chunk_id")
        mapa = {}
        for e in resp.get("entidades", []) or []:
            if not isinstance(e, dict) or not e.get("nome"):
                continue
            eid = gerar_id(str(e.get("tipo", "")), e["nome"])
            mapa[e["nome"]] = eid
            ent = ents.setdefault(eid, {"id": eid, "nome": e["nome"], "tipo": e.get("tipo"),
                                        "aliases": e.get("aliases") or [], "propriedades": e.get("propriedades") or {},
                                        "chunk_ids": []})
            if cid not in ent["chunk_ids"]:
                ent["chunk_ids"].append(cid)
        for r in resp.get("relacoes", []) or []:
            if not isinstance(r, dict):
                continue
            rels.append({"origem": mapa.get(r.get("origem"), r.get("origem")), "tipo": r.get("tipo"),
                         "destino": mapa.get(r.get("destino"), r.get("destino")),
                         "propriedades": r.get("propriedades") or {}, "chunk_id": cid,
                         "evidencia": r.get("evidencia"), "confianca": r.get("confianca")})
    principal = canon.por_arquivo.get(nome_arquivo, {})
    return {"versao_formato": VERSAO_FORMATO, "documento": chunks_doc["documento"],
            "entidade_principal": gerar_id(principal.get("tipo", ""), principal.get("nome", "")),
            "chunks": chunks_doc["chunks"], "entidades": list(ents.values()), "relacoes": rels}


def erros_de_primeira(chunks_doc: dict, bruto: dict, nome_arquivo: str, canon: Canonicas) -> list[str]:
    erros = validar_extracao(montagem_ingenua(chunks_doc, bruto, nome_arquivo, canon))
    # o validador não confere se a evidência é literal; somamos essa checagem
    texto = {c["id"]: c["texto"] for c in chunks_doc["chunks"]}
    for resp in bruto.get("chunks", []):
        for r in resp.get("relacoes", []) or []:
            if isinstance(r, dict) and r.get("evidencia") and r["evidencia"] not in texto.get(resp.get("chunk_id"), ""):
                erros.append(f"evidência não literal no chunk {resp.get('chunk_id')}")
    return erros
