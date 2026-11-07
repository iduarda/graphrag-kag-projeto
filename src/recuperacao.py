"""Camada de recuperação GraphRAG/KAG sobre Neo4j.

Fluxo híbrido (`executar_busca_hibrida`):
  1. Text-to-Cypher (LLM ou regras) + validação de somente-leitura.
  2. Execução do Cypher validado (busca estruturada).
  3. Busca vetorial (índice vetorial do Neo4j, similaridade de cosseno).
  4. União das duas fontes como "sementes" e expansão no grafo
     (vizinhança de 1 salto ou travessia multi-hop de 2+ saltos).
  5. Resposta padrão: resposta, nós, arestas, Cypher gerado e confiança.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from math import sqrt
from typing import Any, Iterable, Sequence

MAX_PROFUNDIDADE = 4
MAX_SEMENTES = 3

# Relações e rótulos conhecidos do grafo (usados no prompt do LLM).
ROTULOS = ["Pessoa", "Organizacao", "Linguagem", "Hardware", "Tecnologia", "Premio", "Local"]
RELACOES = ["TRABALHOU_EM", "CRIOU", "ESTUDOU_EM"]

# Procedimentos CALL de leitura permitidos (índices vetorial e fulltext).
_CALLS_PERMITIDOS = {
    "db.index.vector.querynodes",
    "db.index.fulltext.querynodes",
}

_PROIBIDOS = [
    r"\bDELETE\b",
    r"\bDETACH\b",
    r"\bDROP\b",
    r"\bCREATE\b",
    r"\bSET\b",
    r"\bREMOVE\b",
    r"\bMERGE\b",
    r"\bFOREACH\b",
    r"\bLOAD\s+CSV\b",
    r"\bAPOC\.",
    r"\bDBMS\b",
]

_RE_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Similaridade de cosseno

def similaridade_cosseno(a: Sequence[float], b: Sequence[float]) -> float:
    """Calcula a similaridade de cosseno entre dois vetores normalizados ou não."""
    if len(a) != len(b):
        raise ValueError("vetores com comprimentos diferentes")
    if not a:
        return 0.0
    numerador = sum(x * y for x, y in zip(a, b))
    norma_a = sqrt(sum(x * x for x in a))
    norma_b = sqrt(sum(x * x for x in b))
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return numerador / (norma_a * norma_b)


def ranquear_por_cosseno(vetor: Sequence[float], itens: Iterable[dict], limite: int = 5) -> list[dict]:
    """Ordena itens por similaridade de cosseno.

    Itens com a chave "embedding" têm o score recalculado em Python; os demais
    mantêm o score devolvido pelo índice vetorial do Neo4j (que já é cosseno).
    """
    pontuados: list[dict] = []
    for item in itens:
        copia = {k: v for k, v in item.items() if k != "embedding"}
        emb = item.get("embedding")
        if emb is not None:
            try:
                copia["score"] = similaridade_cosseno(vetor, emb)
            except ValueError:
                copia["score"] = 0.0
        else:
            copia["score"] = float(item.get("score", 0.0))
        pontuados.append(copia)
    pontuados.sort(key=lambda r: r["score"], reverse=True)
    return pontuados[:limite]



# Validação de Cypher (somente leitura)

def _sem_literais(texto: str) -> str:
    """Remove literais de string para que palavras dentro deles não disparem regras."""
    texto = re.sub(r"'(?:[^'\\]|\\.)*'", "''", texto)
    texto = re.sub(r'"(?:[^"\\]|\\.)*"', '""', texto)
    return texto


def validar_cypher(cypher: str) -> bool:
    """Valida uma consulta Cypher como leitura segura, sem comandos de escrita.

    Levanta ValueError se a consulta for vazia, tiver múltiplas instruções,
    operações de escrita/administração, CALL fora da lista permitida, ou não
    começar com MATCH / OPTIONAL MATCH / CALL permitido e retornar resultado.
    """
    if not isinstance(cypher, str) or not cypher.strip():
        raise ValueError("consulta Cypher vazia")

    texto = _sem_literais(cypher.strip())

    if ";" in texto:
        raise ValueError("consulta Cypher não pode conter múltiplas instruções (';')")

    for padrao in _PROIBIDOS:
        if re.search(padrao, texto, flags=re.IGNORECASE):
            raise ValueError(f"consulta Cypher contém operação proibida: {padrao!r}")

    for chamada in re.finditer(r"\bCALL\b\s*([\w.{]*)", texto, flags=re.IGNORECASE):
        nome = chamada.group(1).lower()
        if nome not in _CALLS_PERMITIDOS:
            raise ValueError(f"CALL não permitido: {nome or '{...}'!r}")

    if not re.match(r"\s*(OPTIONAL\s+MATCH|MATCH|CALL)\b", texto, flags=re.IGNORECASE):
        raise ValueError("consulta Cypher deve começar com MATCH, OPTIONAL MATCH ou CALL permitido")
    if not re.search(r"\bRETURN\b", texto, flags=re.IGNORECASE):
        raise ValueError("consulta Cypher deve retornar um resultado")
    return True



# Text-to-Cypher

def _escapar(valor: str) -> str:
    """Escapa um valor para uso dentro de literal Cypher entre aspas simples."""
    return valor.replace("\\", "\\\\").replace("'", "\\'")


def _extrair_origem(pergunta: str) -> str:
    """Extrai um nome ou entidade da pergunta para usar no Cypher gerado."""
    texto = pergunta.strip().rstrip("?")
    candidatos = re.findall(r"\b[A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Ú][a-zà-ú]+)+\b", texto)
    if candidatos:
        return candidatos[-1]
    padrao = re.search(
        r"(?:no|na|em|de|por|para)\s+([A-ZÀ-Úa-zà-ú0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9\- ]{1,80})$",
        texto,
        flags=re.IGNORECASE,
    )
    if padrao:
        return padrao.group(1).strip()
    palavras = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", texto)
    if len(palavras) >= 2:
        return " ".join(palavras[:2])
    return texto


def _extrair_cypher_json(texto: str | None) -> str:
    """Extrai uma query Cypher de uma resposta em JSON ou em bloco de código."""
    if not texto or not texto.strip():
        raise ValueError("resposta vazia do modelo LLM")
    texto = texto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:cypher|cql|json|text)?\s*", "", texto, flags=re.IGNORECASE)
        texto = re.sub(r"\s*```$", "", texto)
        texto = texto.strip()
    if texto.startswith("{"):
        try:
            payload = json.loads(texto)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            query = payload.get("cypher") or payload.get("query") or payload.get("consulta")
            if isinstance(query, str):
                return query.strip().rstrip("; \n")
    return texto.rstrip("; \n")


def _garantir_limite(cypher: str, limite: int) -> str:
    """Acrescenta LIMIT quando o LLM esqueceu de limitar os resultados."""
    if re.search(r"\bLIMIT\b", cypher, flags=re.IGNORECASE):
        return cypher
    return f"{cypher} LIMIT {int(limite)}"


def _client_llm(provedor: str | None = None, modelo: str | None = None):
    """Cria (cliente, modelo) para OpenAI ou Anthropic; None se não configurado."""
    provedor = (provedor or os.getenv("GRAPHRAG_LLM_PROVIDER") or "openai").lower()
    modelo = modelo or os.getenv("GRAPHRAG_LLM_MODEL") or (
        "gpt-4o-mini" if provedor == "openai" else "claude-sonnet-5-5"
    )

    if provedor == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            import anthropic

            return anthropic.Anthropic(api_key=api_key), modelo
        except Exception:
            return None

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai

        return openai.OpenAI(api_key=api_key), modelo
    except Exception:
        return None


def _validar_tipo(tipo: str) -> str:
    if not isinstance(tipo, str) or not _RE_IDENTIFICADOR.match(tipo):
        raise ValueError(f"rótulo inválido: {tipo!r}")
    return tipo


def gerar_cypher(
    pergunta: str,
    *,
    tipo: str = "Entidade",
    limite: int = 10,
    profundidade: int = 2,
    usar_llm: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Gera uma consulta Cypher segura a partir de uma pergunta do usuário.

    Com `usar_llm=True` tenta o LLM (Text-to-Cypher); qualquer falha ou saída
    inválida cai no gerador por regras. As consultas por regras devolvem
    também os ids dos nós para que o fluxo híbrido possa expandi-los.
    """
    q = (pergunta or "").strip()
    if not q:
        raise ValueError("pergunta vazia")
    tipo = _validar_tipo(tipo)
    limite = max(1, int(limite))
    profundidade = int(profundidade)

    if usar_llm:
        try:
            return gerar_cypher_llm(
                q, provider=provider, model=model, tipo=tipo, limite=limite, profundidade=profundidade
            )
        except Exception:
            pass

    base = _escapar(_extrair_origem(q).lower())
    baixa = q.lower()

    if "trabalhou" in baixa:
        return (
            "MATCH (p:Pessoa)-[:TRABALHOU_EM]->(o:Organizacao) "
            f"WHERE toLower(o.nome) CONTAINS '{base}' "
            "RETURN p.id AS pessoa_id, p.nome AS pessoa, "
            f"o.id AS organizacao_id, o.nome AS organizacao LIMIT {limite}"
        )

    if "criou" in baixa and "linguagem" in baixa:
        return (
            "MATCH (p:Pessoa)-[:CRIOU]->(l:Linguagem) "
            f"WHERE toLower(p.nome) CONTAINS '{base}' "
            "RETURN p.id AS pessoa_id, p.nome AS pessoa, "
            f"l.id AS linguagem_id, l.nome AS linguagem LIMIT {limite}"
        )

    if "estudou" in baixa:
        return (
            "MATCH (p:Pessoa)-[:ESTUDOU_EM]->(o:Organizacao) "
            f"WHERE toLower(p.nome) CONTAINS '{base}' "
            "RETURN p.id AS pessoa_id, p.nome AS pessoa, "
            f"o.id AS organizacao_id, o.nome AS organizacao LIMIT {limite}"
        )

    if profundidade >= 2 and "quem" in baixa and "linguagem" in baixa:
        return (
            "MATCH (p:Pessoa)-[:CRIOU]->(l:Linguagem) "
            f"WHERE toLower(p.nome) CONTAINS '{base}' OR toLower(l.nome) CONTAINS '{base}' "
            "RETURN DISTINCT p.id AS criador_id, p.nome AS criador, "
            f"l.id AS linguagem_id, l.nome AS linguagem LIMIT {limite}"
        )

    return (
        f"MATCH (n:{tipo}) WHERE toLower(n.nome) CONTAINS '{base}' "
        f"RETURN n.id AS id, n.nome AS nome, labels(n) AS labels LIMIT {limite}"
    )


def gerar_cypher_llm(
    pergunta: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    cliente: tuple[Any, str] | None = None,
    tipo: str = "Entidade",
    limite: int = 10,
    profundidade: int = 2,
) -> str:
    """Text-to-Cypher com LLM; a saída é validada antes de ser devolvida.

    Se o ambiente não estiver configurado, se a chamada falhar ou se o Cypher
    gerado não passar em `validar_cypher`, cai no gerador por regras.
    `cliente` permite injetar um par (api, modelo), útil em testes.
    """
    q = (pergunta or "").strip()
    if not q:
        raise ValueError("pergunta vazia")

    def _fallback() -> str:
        return gerar_cypher(q, tipo=tipo, limite=limite, profundidade=profundidade)

    if cliente is None:
        cliente = _client_llm(provider, model)
    if cliente is None:
        return _fallback()

    api, modelo = cliente
    sistema = (
        "Você é um gerador de consultas Cypher seguro para Neo4j. "
        "Responda somente com a consulta Cypher final, sem explicações. "
        "Use apenas MATCH/OPTIONAL MATCH e RETURN, sempre com LIMIT, sem comandos de escrita e sem ';'. "
        f"Rótulos de nós: {', '.join(ROTULOS)}. "
        f"Tipos de relação (direção sujeito -> objeto): {', '.join(RELACOES)}. "
        "Exemplos: (Pessoa)-[:TRABALHOU_EM]->(Organizacao), (Pessoa)-[:CRIOU]->(Linguagem), "
        "(Pessoa)-[:ESTUDOU_EM]->(Organizacao). "
        "Todo nó tem as propriedades id e nome. "
        "Prefira WHERE toLower(n.nome) CONTAINS 'termo' e retorne também n.id."
    )
    usuario = (
        f"Pergunta: {q}\n\n"
        "Gere uma Cypher de leitura para responder essa pergunta no grafo. "
        "Se houver entidade mencionada, use-a como filtro. "
        "Retorne apenas a query final em um bloco ```cypher ... ``` ou como texto puro."
    )

    try:
        if hasattr(api, "messages"):
            resposta = api.messages.create(
                model=modelo,
                max_tokens=1024,
                temperature=0,
                system=sistema,
                messages=[{"role": "user", "content": usuario}],
            )
            texto = resposta.content[0].text
        else:
            resposta = api.chat.completions.create(
                model=modelo,
                temperature=0,
                messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            )
            texto = resposta.choices[0].message.content
    except Exception:
        return _fallback()

    try:
        cypher = _garantir_limite(_extrair_cypher_json(texto), limite)
        validar_cypher(cypher)
    except ValueError:
        return _fallback()
    return cypher



# Utilitários de nós/arestas

def _exigir_executor(executor: Any) -> None:
    if not hasattr(executor, "ler"):
        raise TypeError("executor precisa ter o método 'ler'")


def _unir_nos(listas: Iterable[Iterable[dict]]) -> list[dict]:
    vistos: dict[str, dict] = {}
    for lista in listas:
        for no in lista:
            no_id = no.get("id")
            if no_id and no_id not in vistos:
                vistos[no_id] = no
    return list(vistos.values())


def _unir_arestas(listas: Iterable[Iterable[dict]]) -> list[dict]:
    vistas: dict[tuple, dict] = {}
    for lista in listas:
        for aresta in lista:
            chave = (aresta.get("de"), aresta.get("para"), aresta.get("tipo"))
            if chave[0] and chave[1] and chave not in vistas:
                vistas[chave] = aresta
    return list(vistas.values())


def _nos_dos_candidatos(candidatos: Iterable[dict]) -> list[dict]:
    nos = []
    for c in candidatos:
        if c.get("id"):
            labels = c.get("labels") or []
            nos.append({"id": c["id"], "nome": c.get("nome") or c["id"], "tipo": labels[0] if labels else "Entidade"})
    return nos


def _nos_das_linhas(linhas: Iterable[dict]) -> list[dict]:
    """Extrai nós de linhas de resultado com colunas `id`/`nome` ou `<alias>_id`/`<alias>`."""
    nos = []
    for linha in linhas:
        for chave, valor in linha.items():
            if not isinstance(valor, str) or not (chave == "id" or chave.endswith("_id")):
                continue
            nome = linha.get("nome" if chave == "id" else chave[:-3])
            labels = linha.get("labels") if chave == "id" else None
            nos.append(
                {
                    "id": valor,
                    "nome": nome if isinstance(nome, str) else valor,
                    "tipo": labels[0] if labels else "Entidade",
                }
            )
    return nos


# Busca vetorial

def _montar_vetor_consulta(pergunta: str, embedder: Any | None = None) -> list[float] | None:
    """Codifica a pergunta; devolve None se não houver embedder utilizável."""
    if embedder is None or not hasattr(embedder, "codificar_consulta"):
        return None
    vetor = embedder.codificar_consulta(pergunta)
    vetor = [float(x) for x in vetor] if vetor is not None else []
    return vetor or None


def buscar_vetorial(
    executor: Any,
    pergunta: str,
    *,
    limite: int = 5,
    tipo: str = "Entidade",
    embedder: Any | None = None,
) -> list[dict]:
    """Busca semântica por similaridade de cosseno em entidades ou chunks.

    Sem embedder não há vetor de consulta, então devolve lista vazia (nunca
    inventa candidatos).
    """
    _exigir_executor(executor)
    vetor = _montar_vetor_consulta(pergunta, embedder)
    if vetor is None:
        return []

    indice = "chunk_embedding" if tipo == "Chunk" else "entidade_embedding"
    query = (
        f"CALL db.index.vector.queryNodes('{indice}', $limite, $v) "
        "YIELD node, score RETURN node.id AS id, node.nome AS nome, labels(node) AS labels, score "
        "ORDER BY score DESC LIMIT $limite"
    )
    validar_cypher(query)
    linhas = executor.ler(query, limite=limite, v=vetor) or []
    return ranquear_por_cosseno(vetor, linhas, limite)



# Vizinhança e travessia multi-hop

def travessia_multi_hop(
    executor: Any, origem: str, *, profundidade: int = 2, limite: int = 20
) -> dict[str, Any]:
    """Travessia multi-hop (2 a MAX_PROFUNDIDADE saltos) a partir de um nó.

    O intervalo `*1..N` do Cypher não aceita parâmetro, então N é um inteiro
    validado e interpolado na query.
    """
    _exigir_executor(executor)
    profundidade = max(2, min(int(profundidade), MAX_PROFUNDIDADE))
    query = (
        f"MATCH caminho=(n {{id: $origem}})-[*1..{profundidade}]-(m) "
        "RETURN [x IN nodes(caminho) | {id: x.id, nome: x.nome, tipo: labels(x)[0]}] AS nos, "
        "[r IN relationships(caminho) | {de: startNode(r).id, para: endNode(r).id, tipo: type(r)}] AS arestas "
        "LIMIT $limite"
    )
    validar_cypher(query)
    linhas = executor.ler(query, origem=origem, limite=limite) or []

    nos: list[dict] = []
    arestas: list[dict] = []
    for linha in linhas:
        for item in linha.get("nos") or []:
            if isinstance(item, dict) and item.get("id"):
                nos.append({"id": item["id"], "nome": item.get("nome") or item["id"], "tipo": item.get("tipo") or "Entidade"})
        for item in linha.get("arestas") or []:
            if isinstance(item, dict):
                arestas.append(
                    {
                        "de": item.get("de") or item.get("startNode"),
                        "para": item.get("para") or item.get("endNode"),
                        "tipo": item.get("tipo") or item.get("type"),
                    }
                )
    return {"nos": _unir_nos([nos]), "arestas": _unir_arestas([arestas]), "profundidade": profundidade}


def buscar_vizinhanca(
    executor: Any, entidade_id: str, *, profundidade: int = 1, limite: int = 20
) -> dict[str, Any]:
    """Busca a vizinhança de um nó, retornando nós e arestas.

    `profundidade=1` devolve os vizinhos diretos (em ambas as direções);
    `profundidade>=2` delega para `travessia_multi_hop`.
    """
    _exigir_executor(executor)
    if profundidade >= 2:
        return travessia_multi_hop(executor, entidade_id, profundidade=profundidade, limite=limite)

    query = """
    MATCH (n {id: $entidade_id})
    OPTIONAL MATCH (n)-[r]-(m)
    RETURN n.id AS id, n.nome AS nome, labels(n)[0] AS tipo,
           m.id AS vizinho_id, m.nome AS vizinho_nome, labels(m)[0] AS vizinho_tipo,
           type(r) AS relacao, startNode(r).id AS de, endNode(r).id AS para
    LIMIT $limite
    """
    validar_cypher(query)
    linhas = executor.ler(query, entidade_id=entidade_id, limite=limite) or []

    nos: list[dict] = []
    arestas: list[dict] = []
    for linha in linhas:
        if linha.get("id"):
            nos.append({"id": linha["id"], "nome": linha.get("nome") or linha["id"], "tipo": linha.get("tipo") or "Entidade"})
        vizinho_id = linha.get("vizinho_id")
        if vizinho_id:
            nos.append(
                {
                    "id": vizinho_id,
                    "nome": linha.get("vizinho_nome") or vizinho_id,
                    "tipo": linha.get("vizinho_tipo") or "Entidade",
                }
            )
            arestas.append(
                {
                    "de": linha.get("de") or linha.get("id"),
                    "para": linha.get("para") or vizinho_id,
                    "tipo": linha.get("relacao") or "VIZINHO",
                }
            )
    return {"nos": _unir_nos([nos]), "arestas": _unir_arestas([arestas]), "profundidade": 1}



# Resposta padrão

def resposta_padrao(
    resposta: str, nos: Iterable[dict], arestas: Iterable[dict], cypher: str, confianca: float
) -> dict[str, Any]:
    """Objeto-padrão da resposta que vai para a interface e para o front-end."""
    return {
        "resposta": resposta,
        "nos": list(nos),
        "arestas": list(arestas),
        "cypher": cypher,
        "confianca": float(confianca),
    }


@dataclass
class RespostaGraphRAG:
    resposta: str
    nos: list[dict] = field(default_factory=list)
    arestas: list[dict] = field(default_factory=list)
    cypher: str = ""
    confianca: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return resposta_padrao(self.resposta, self.nos, self.arestas, self.cypher, self.confianca)


def _calcular_confianca(candidatos: list[dict], linhas: list[dict], nos: list[dict], arestas: list[dict]) -> float:
    """Confiança em [0, 1] combinando os sinais disponíveis.

    - score vetorial máximo (peso 0.5, só se houve busca vetorial);
    - Cypher estruturado devolveu linhas (peso 0.3);
    - expansão no grafo achou relações (1.0) ou só nós (0.5) (peso 0.2).
    Os pesos são renormalizados sobre os sinais disponíveis. Sem evidência = 0.
    """
    if not (candidatos or linhas or nos):
        return 0.0
    sinais: list[tuple[float, float]] = []
    if candidatos:
        melhor = max(float(c.get("score", 0.0)) for c in candidatos)
        sinais.append((0.5, min(1.0, max(0.0, melhor))))
    sinais.append((0.3, 1.0 if linhas else 0.0))
    sinais.append((0.2, 1.0 if arestas else (0.5 if nos else 0.0)))
    total = sum(p for p, _ in sinais)
    return round(sum(p * v for p, v in sinais) / total, 3)


def executar_busca_hibrida(
    executor: Any,
    pergunta: str,
    *,
    tipo: str = "Entidade",
    profundidade: int = 2,
    limite: int = 5,
    embedder: Any | None = None,
    usar_llm: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> RespostaGraphRAG:
    """Busca híbrida: Cypher estruturado + vetorial + expansão no grafo."""
    cypher = gerar_cypher(
        pergunta,
        tipo=tipo,
        limite=limite,
        profundidade=profundidade,
        usar_llm=usar_llm or bool(os.getenv("GRAPHRAG_USE_LLM")),
        provider=provider,
        model=model,
    )
    validar_cypher(cypher)

    # Busca estruturada: executa o Cypher já validado.
    try:
        linhas = executor.ler(cypher) or []
    except Exception:
        linhas = []  # Cypher do LLM pode ser válido na sintaxe e falhar no esquema.

    # Busca vetorial (vazia se não houver embedder).
    candidatos = buscar_vetorial(executor, pergunta, limite=limite, tipo=tipo, embedder=embedder)

    # Sementes = candidatos vetoriais + nós achados pelo Cypher (sem duplicar).
    nos_semente = _unir_nos([_nos_dos_candidatos(candidatos), _nos_das_linhas(linhas)])
    sementes = [n["id"] for n in nos_semente][:MAX_SEMENTES]

    # Expansão: vizinhança (1 salto) ou travessia multi-hop (2+ saltos).
    listas_nos: list[list[dict]] = [nos_semente]
    listas_arestas: list[list[dict]] = []
    for semente in sementes:
        viz = buscar_vizinhanca(executor, semente, profundidade=profundidade, limite=limite)
        listas_nos.append(viz["nos"])
        listas_arestas.append(viz["arestas"])
    nos = _unir_nos(listas_nos)
    arestas = _unir_arestas(listas_arestas)

    if nos:
        principais = ", ".join(n["nome"] for n in nos[:5])
        resposta = (
            f"Encontrei {len(nos)} nós e {len(arestas)} relações relevantes para: {pergunta}. "
            f"Principais: {principais}."
        )
    else:
        resposta = f"Não encontrei evidência suficiente para responder: {pergunta}."

    confianca = _calcular_confianca(candidatos, linhas, nos, arestas)
    return RespostaGraphRAG(resposta=resposta, nos=nos, arestas=arestas, cypher=cypher, confianca=confianca)


# Alias para manter compatibilidade com o nome anterior (com acento).
buscar_vizinhança = buscar_vizinhanca