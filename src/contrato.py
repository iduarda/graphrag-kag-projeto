"""
Contrato de dados da extração (Pessoa 2 -> Pessoa 3).

Define os tipos de entidade, os tipos de relação permitidos, a regra de
geração de ids e a função que valida um JSON de extração. É a versão em
código de docs/formato_extracao.md e de docs/modelo_lpg.md.

Uso:
    from src.contrato import gerar_id, validar_extracao
    gerar_id("Pessoa", "Alan Turing")   # -> "pessoa:alan_turing"
    erros = validar_extracao(dados)     # -> [] se estiver tudo certo
"""

import re
import unicodedata

VERSAO_FORMATO = "1.0"

# Tipos de entidade e as propriedades extras aceitas em cada um.
TIPOS_ENTIDADE = {
    "Pessoa": {"ano_nascimento", "ano_morte"},
    "Organizacao": {"tipo", "ano_fundacao"},
    "Linguagem": {"ano", "paradigmas"},
    "Hardware": {"ano", "tipo"},
    "Tecnologia": {"ano", "tipo"},
    "Premio": set(),
    "Local": {"tipo"},
}

# Valores permitidos para a propriedade "tipo" de alguns rótulos.
SUBTIPOS = {
    "Organizacao": {"empresa", "universidade", "laboratorio", "agencia", "fundacao"},
    "Hardware": {"computador", "processador", "periferico"},
    "Tecnologia": {"sistema", "rede", "protocolo", "invencao", "conceito"},
    "Local": {"pais", "cidade"},
}

# Relação -> (tipos de origem permitidos, tipos de destino permitidos, propriedades extras).
# "MESMO" em destino significa: o destino precisa ter o mesmo tipo da origem.
CRIAVEIS = {"Linguagem", "Hardware", "Tecnologia"}
TIPOS_RELACAO = {
    "TRABALHOU_EM": ({"Pessoa"}, {"Organizacao"}, {"cargo", "ano_inicio", "ano_fim"}),
    "ESTUDOU_EM": ({"Pessoa"}, {"Organizacao"}, {"curso", "ano"}),
    "FUNDOU": ({"Pessoa"}, {"Organizacao"}, {"ano"}),
    "CRIOU": ({"Pessoa"}, CRIAVEIS, {"ano"}),
    "DESENVOLVEU": ({"Organizacao"}, CRIAVEIS, {"ano"}),
    "INFLUENCIADA_POR": ({"Linguagem"}, {"Linguagem"}, set()),
    "ESCRITO_EM": ({"Tecnologia"}, {"Linguagem"}, set()),
    "EXECUTA_EM": ({"Tecnologia", "Linguagem"}, {"Hardware"}, set()),
    "UTILIZA": ({"Hardware"}, {"Hardware"}, set()),
    "SUCESSOR_DE": (set(TIPOS_ENTIDADE), "MESMO", set()),
    "RECEBEU": ({"Pessoa"}, {"Premio"}, {"ano"}),
    "COLABOROU_COM": ({"Pessoa"}, {"Pessoa"}, {"contexto"}),
    "NASCEU_EM": ({"Pessoa"}, {"Local"}, set()),
    "SEDIADA_EM": ({"Organizacao"}, {"Local"}, set()),
}


def gerar_id(tipo: str, nome: str) -> str:
    """
    Gera o id canônico de uma entidade: '<tipo em minúsculas>:<nome normalizado>'.
    Remove acentos, troca '+' por 'p' e '#' por 'sharp' (para C++ e C#),
    e troca qualquer outro caractere por '_'.

    >>> gerar_id("Pessoa", "Alan Turing")
    'pessoa:alan_turing'
    >>> gerar_id("Linguagem", "C++")
    'linguagem:cpp'
    >>> gerar_id("Organizacao", "Universidade da Pensilvânia")
    'organizacao:universidade_da_pensilvania'
    """
    texto = nome.strip().lower().replace("+", "p").replace("#", "sharp")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return f"{tipo.lower()}:{texto}"


def _eh_ano(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and 1000 <= valor <= 2100


def validar_extracao(dados: dict) -> list[str]:
    """Valida um JSON de extração. Retorna a lista de erros (vazia = válido)."""
    erros: list[str] = []

    def erro(onde: str, msg: str) -> None:
        erros.append(f"{onde}: {msg}")

    if not isinstance(dados, dict):
        return ["raiz: o arquivo precisa ser um objeto JSON"]

    for campo in ("versao_formato", "documento", "chunks", "entidades", "relacoes", "entidade_principal"):
        if campo not in dados:
            erro("raiz", f"campo obrigatório ausente: '{campo}'")
    if erros:
        return erros

    if dados["versao_formato"] != VERSAO_FORMATO:
        erro("versao_formato", f"esperado '{VERSAO_FORMATO}', veio '{dados['versao_formato']}'")

    # --- documento ---
    doc = dados["documento"]
    for campo in ("id", "titulo", "url", "categoria"):
        if not doc.get(campo):
            erro("documento", f"campo obrigatório ausente ou vazio: '{campo}'")
    doc_id = doc.get("id")
    if doc_id is not None and not isinstance(doc_id, int):
        erro("documento.id", "deve ser o pageid da Wikipédia (número inteiro)")

    # --- chunks ---
    ids_chunks: set[str] = set()
    for i, chunk in enumerate(dados["chunks"]):
        onde = f"chunks[{i}]"
        cid, ordem, texto = chunk.get("id"), chunk.get("ordem"), chunk.get("texto")
        if not isinstance(ordem, int):
            erro(onde, "'ordem' deve ser um número inteiro")
        elif cid != f"{doc_id}_{ordem}":
            erro(onde, f"id deve ser '<documento.id>_<ordem>' = '{doc_id}_{ordem}', veio '{cid}'")
        if not texto or not isinstance(texto, str):
            erro(onde, "'texto' vazio")
        if cid in ids_chunks:
            erro(onde, f"id de chunk repetido: '{cid}'")
        ids_chunks.add(cid)
    if not ids_chunks:
        erro("chunks", "o documento precisa ter pelo menos um chunk")

    # --- entidades ---
    tipo_por_id: dict[str, str] = {}
    for i, ent in enumerate(dados["entidades"]):
        onde = f"entidades[{i}]"
        eid, nome, tipo = ent.get("id"), ent.get("nome"), ent.get("tipo")
        if not nome:
            erro(onde, "'nome' vazio")
        if tipo not in TIPOS_ENTIDADE:
            erro(onde, f"tipo '{tipo}' inválido; use um de {sorted(TIPOS_ENTIDADE)}")
            continue
        onde = f"entidades[{i}] ({eid})"
        if not isinstance(eid, str) or not eid.startswith(tipo.lower() + ":"):
            erro(onde, f"id deve começar com '{tipo.lower()}:' (use gerar_id)")
        if eid in tipo_por_id:
            erro(onde, "id repetido no mesmo arquivo; junte as duas entidades em uma")
        tipo_por_id[eid] = tipo

        aliases = ent.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            erro(onde, "'aliases' deve ser uma lista de textos")

        props = ent.get("propriedades", {})
        if not isinstance(props, dict):
            erro(onde, "'propriedades' deve ser um objeto")
            props = {}
        for chave, valor in props.items():
            if chave not in TIPOS_ENTIDADE[tipo]:
                erro(onde, f"propriedade '{chave}' não existe para {tipo}; aceitas: {sorted(TIPOS_ENTIDADE[tipo]) or 'nenhuma'}")
            elif chave.startswith("ano") and not _eh_ano(valor):
                erro(onde, f"'{chave}' deve ser um ano (inteiro), veio {valor!r}")
            elif chave == "tipo" and valor not in SUBTIPOS.get(tipo, set()):
                erro(onde, f"tipo '{valor}' inválido para {tipo}; use um de {sorted(SUBTIPOS[tipo])}")
            elif chave == "paradigmas" and not isinstance(valor, list):
                erro(onde, "'paradigmas' deve ser uma lista")

        chunk_ids = ent.get("chunk_ids", [])
        if not chunk_ids:
            erro(onde, "'chunk_ids' vazio: toda entidade precisa dizer em que chunk apareceu")
        for c in chunk_ids:
            if c not in ids_chunks:
                erro(onde, f"chunk '{c}' não existe neste documento")

    if dados["entidade_principal"] not in tipo_por_id:
        erro("entidade_principal", f"'{dados['entidade_principal']}' não está na lista de entidades")

    # --- relações ---
    vistas: set[tuple] = set()
    for i, rel in enumerate(dados["relacoes"]):
        origem, tipo_rel, destino = rel.get("origem"), rel.get("tipo"), rel.get("destino")
        onde = f"relacoes[{i}] ({origem} -{tipo_rel}-> {destino})"
        if tipo_rel not in TIPOS_RELACAO:
            erro(onde, f"tipo de relação inválido; use um de {sorted(TIPOS_RELACAO)}")
            continue
        if origem not in tipo_por_id:
            erro(onde, "origem não está na lista de entidades")
        if destino not in tipo_por_id:
            erro(onde, "destino não está na lista de entidades")
        if origem == destino:
            erro(onde, "origem e destino são a mesma entidade")
        if origem in tipo_por_id and destino in tipo_por_id:
            tipos_origem, tipos_destino, _ = TIPOS_RELACAO[tipo_rel]
            t_o, t_d = tipo_por_id[origem], tipo_por_id[destino]
            if t_o not in tipos_origem:
                erro(onde, f"{tipo_rel} não pode sair de {t_o}; origem aceita: {sorted(tipos_origem)}")
            if tipos_destino == "MESMO":
                if t_d != t_o:
                    erro(onde, f"{tipo_rel} precisa ligar entidades do mesmo tipo ({t_o} -> {t_d})")
            elif t_d not in tipos_destino:
                erro(onde, f"{tipo_rel} não pode chegar em {t_d}; destino aceita: {sorted(tipos_destino)}")

        props = rel.get("propriedades", {})
        aceitas = TIPOS_RELACAO[tipo_rel][2]
        for chave, valor in (props or {}).items():
            if chave not in aceitas:
                erro(onde, f"propriedade '{chave}' não existe em {tipo_rel}; aceitas: {sorted(aceitas) or 'nenhuma'}")
            elif chave.startswith("ano") and not _eh_ano(valor):
                erro(onde, f"'{chave}' deve ser um ano (inteiro), veio {valor!r}")

        if rel.get("chunk_id") not in ids_chunks:
            erro(onde, f"chunk_id '{rel.get('chunk_id')}' não existe neste documento")
        if not rel.get("evidencia"):
            erro(onde, "'evidencia' vazia: copie o trecho do texto que comprova a relação")
        conf = rel.get("confianca")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 <= conf <= 1:
            erro(onde, f"'confianca' deve ser um número entre 0 e 1, veio {conf!r}")

        chave_rel = (origem, tipo_rel, destino, rel.get("chunk_id"))
        if chave_rel in vistas:
            erro(onde, "relação repetida no mesmo chunk")
        vistas.add(chave_rel)

    return erros
