"""
Grounding (ancoragem) das respostas no subgrafo recuperado — Pessoa 5.

Fluxo:
  1. `recuperar_fatos`: sementes (entidades citadas pelo nome na pergunta + busca vetorial)
     -> expansão de até 2 saltos só pelas relações semânticas -> reordenação dos fatos pela
     semelhança com a pergunta (embeddings), completando as cadeias de vários passos.
     Cada fato traz a evidência (trecho do texto), a confiança e o documento de origem,
     e é numerado [F1], [F2]...
  2. `responder_com_fatos`: o LLM recebe SOMENTE esses fatos e precisa citar [F#] em
     cada afirmação. Se os fatos não bastam, ele deve dizer isso (abstenção).
  3. `verificar_citacoes`: confere se cada citação existe. Citações inventadas são
     removidas e a resposta é marcada como não fundamentada.

Sem LLM configurado, a resposta é montada diretamente com os fatos mais relevantes
(também citados), então a interface nunca mostra algo que não está no grafo.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.recuperacao import _client_llm

LIMITE_FATOS = 20
FRASE_ABSTENCAO = "Não encontrei no grafo informação suficiente para responder"

Q_FATOS = """
MATCH (a:Entidade)-[r]->(b:Entidade)
WHERE a.id IN $ids AND b.id IN $ids AND r.evidencia IS NOT NULL
OPTIONAL MATCH (d:Documento)-[:TEM_CHUNK]->(c:Chunk {id: r.chunk_ids[0]})
RETURN a.id AS origem_id, a.nome AS origem, type(r) AS relacao,
       b.id AS destino_id, b.nome AS destino,
       r.evidencia AS evidencia, r.confianca AS confianca, r.chunk_ids AS chunk_ids,
       d.titulo AS documento, d.url AS url,
       coalesce(a.pagerank, 0.0) + coalesce(b.pagerank, 0.0) AS relevancia
ORDER BY relevancia DESC, confianca DESC
LIMIT $limite
"""

SISTEMA = f"""Você responde perguntas sobre a história da computação usando SOMENTE os fatos fornecidos.

Regras obrigatórias:
1. Use apenas os fatos da lista. Não use conhecimento próprio, mesmo que seja verdadeiro.
2. Toda afirmação deve terminar com a citação do(s) fato(s) que a sustentam, no formato [F1] ou [F1][F3].
3. Para perguntas de vários passos, encadeie os fatos e cite cada passo do caminho.
4. Se os fatos não forem suficientes, comece a resposta com "{FRASE_ABSTENCAO}" e diga o que falta.
5. Responda em português, em no máximo 5 frases."""


@dataclass
class RespostaFundamentada:
    texto: str
    fatos: list[dict] = field(default_factory=list)
    citados: list[str] = field(default_factory=list)
    citacoes_invalidas: list[str] = field(default_factory=list)
    usou_llm: bool = False
    abstencao: bool = False

    @property
    def fundamentada(self) -> bool:
        """Verdadeiro se a resposta cita pelo menos um fato real e nenhum inventado (ou se absteve)."""
        if self.abstencao:
            return True
        return bool(self.citados) and not self.citacoes_invalidas

    @property
    def fatos_citados(self) -> list[dict]:
        return [f for f in self.fatos if f["ref"] in self.citados]


# ----------------------------------------------------------------------------- fatos
RELACOES_SEMANTICAS = [
    "TRABALHOU_EM", "ESTUDOU_EM", "FUNDOU", "CRIOU", "DESENVOLVEU", "INFLUENCIADA_POR", "ESCRITO_EM",
    "EXECUTA_EM", "UTILIZA", "SUCESSOR_DE", "RECEBEU", "COLABOROU_COM", "NASCEU_EM", "SEDIADA_EM",
]
MAX_SEMENTES_NOME = 6
MAX_SEMENTES_VETOR = 3
LIMITE_CANDIDATOS = 600
NUCLEO = 12  # fatos escolhidos só pela semelhança; o resto completa as cadeias (multi-hop)

Q_NOMES = "MATCH (e:Entidade)-[r]-(:Entidade) WITH DISTINCT e RETURN e.id AS id, e.nome AS nome"

# Expansão de até 2 saltos SÓ pelas relações semânticas (sem passar por chunks e documentos).
Q_EXPANDIR = f"""
MATCH (s:Entidade) WHERE s.id IN $sementes
MATCH caminho = (s)-[:{'|'.join(RELACOES_SEMANTICAS)}*1..2]-(:Entidade)
WITH caminho LIMIT 20000
UNWIND relationships(caminho) AS r
WITH DISTINCT r
WHERE r.evidencia IS NOT NULL
WITH r, startNode(r) AS a, endNode(r) AS b
OPTIONAL MATCH (d:Documento)-[:TEM_CHUNK]->(c:Chunk {{id: r.chunk_ids[0]}})
RETURN a.id AS origem_id, a.nome AS origem, type(r) AS relacao, b.id AS destino_id, b.nome AS destino,
       r.evidencia AS evidencia, r.confianca AS confianca, r.chunk_ids AS chunk_ids,
       d.titulo AS documento, d.url AS url,
       coalesce(a.pagerank, 0.0) + coalesce(b.pagerank, 0.0) AS relevancia
LIMIT $limite
"""

Q_VETOR_ENTIDADES = """
CALL db.index.vector.queryNodes('entidade_embedding', $k, $v) YIELD node, score
WHERE EXISTS { (node)-[]-(:Entidade) }
RETURN node.id AS id, node.nome AS nome, score
"""

_cache_nomes: dict[int, list[tuple[str, str]]] = {}
_cache_vetores: dict[tuple, list[float]] = {}


def ids_de_entidades(nos: list[dict]) -> list[str]:
    """Ids das entidades do subgrafo (ignora Chunk e Documento)."""
    return [n["id"] for n in nos if n.get("id") and n.get("tipo") not in {"Chunk", "Documento"}]


def entidades_citadas(executor: Any, pergunta: str) -> list[str]:
    """Ids das entidades cujo nome aparece na pergunta, como palavra inteira (nomes longos primeiro).

    Nomes de até 3 caracteres ("C", "Go", "ABC") só casam com a mesma grafia, para "sol" não virar "SOL".
    """
    chave = id(executor)
    if chave not in _cache_nomes:
        _cache_nomes[chave] = [(l["id"], l["nome"]) for l in executor.ler(Q_NOMES) or [] if l.get("nome")]
    achadas = []
    for eid, nome in _cache_nomes[chave]:
        padrao = r"(?<![\w+#])" + re.escape(nome) + r"(?![\w+#])"
        if re.search(padrao, pergunta, 0 if len(nome) <= 3 else re.IGNORECASE):
            achadas.append((len(nome), eid))
    return [eid for _, eid in sorted(achadas, reverse=True)][:MAX_SEMENTES_NOME]


def _cosseno(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vetores do E5 já são normalizados


def _texto_fato(f: dict) -> str:
    return f'{f["origem"]} {f["relacao"].lower().replace("_", " ")} {f["destino"]}. {f.get("evidencia") or ""}'


def _ordenar(fatos: list[dict], pergunta: str, embedder: Any | None) -> list[dict]:
    """Ordena pela semelhança com a pergunta (com embedder) ou por confiança e PageRank (sem)."""
    if embedder is None or not fatos:
        return sorted(fatos, key=lambda f: (f.get("confianca") or 0, f.get("relevancia") or 0), reverse=True)
    vq = embedder.codificar_consulta(pergunta)
    faltando = [f for f in fatos if (f["origem_id"], f["relacao"], f["destino_id"]) not in _cache_vetores]
    if faltando:
        for f, v in zip(faltando, embedder.codificar_documentos([_texto_fato(f) for f in faltando])):
            _cache_vetores[(f["origem_id"], f["relacao"], f["destino_id"])] = v
    for f in fatos:
        f["similaridade"] = round(_cosseno(vq, _cache_vetores[(f["origem_id"], f["relacao"], f["destino_id"])]), 4)
    return sorted(fatos, key=lambda f: f["similaridade"], reverse=True)


def _selecionar(ordenados: list[dict], limite: int, sementes: set[str] | None = None) -> list[dict]:
    """Os NUCLEO fatos mais parecidos com a pergunta + o segundo passo das cadeias (multi-hop).

    As vagas restantes vão primeiro para fatos que AVANÇAM a partir das pontas já escolhidas,
    sem tocar as sementes (ex.: "C -SUCESSOR_DE-> B" puxa "Ken Thompson -CRIOU-> B"). Sem isso,
    as vagas se enchem de mais fatos de 1 salto sobre a própria semente.
    """
    sementes = sementes or set()
    escolhidos = ordenados[:NUCLEO]
    pontas = ({f["origem_id"] for f in escolhidos} | {f["destino_id"] for f in escolhidos}) - sementes
    for f in ordenados[NUCLEO:]:
        if len(escolhidos) >= limite:
            break
        toca_ponta = f["origem_id"] in pontas or f["destino_id"] in pontas
        toca_semente = f["origem_id"] in sementes or f["destino_id"] in sementes
        if toca_ponta and not toca_semente:
            escolhidos.append(f)
    for f in ordenados[NUCLEO:]:  # se sobrar espaço, completa por semelhança
        if len(escolhidos) >= limite:
            break
        if f not in escolhidos:
            escolhidos.append(f)
    return escolhidos


def recuperar_fatos(
    executor: Any, pergunta: str, *, embedder: Any | None = None, sementes_extras: list[str] | None = None,
    limite: int = LIMITE_FATOS,
) -> list[dict]:
    """Fatos para o grounding: sementes -> expansão semântica de 2 saltos -> reordenação -> F1..Fn.

    Sementes: entidades citadas pelo nome na pergunta + as mais parecidas na busca vetorial
    + (opcional) entidades achadas pela busca híbrida.
    """
    sementes = entidades_citadas(executor, pergunta)
    if embedder is not None:
        vetor = embedder.codificar_consulta(pergunta)
        linhas = executor.ler(Q_VETOR_ENTIDADES, k=MAX_SEMENTES_VETOR * 3, v=vetor) or []
        sementes += [l["id"] for l in linhas][:MAX_SEMENTES_VETOR]
    sementes += list(sementes_extras or [])[:MAX_SEMENTES_VETOR]
    sementes = list(dict.fromkeys(s for s in sementes if s))
    if not sementes:
        return []
    candidatos = executor.ler(Q_EXPANDIR, sementes=sementes, limite=LIMITE_CANDIDATOS) or []
    escolhidos = _selecionar(_ordenar(candidatos, pergunta, embedder), limite, set(sementes))
    return [{"ref": f"F{i}", **f} for i, f in enumerate(escolhidos, start=1)]


def buscar_fatos(executor: Any, ids: list[str], limite: int = LIMITE_FATOS) -> list[dict]:
    """Relações entre um conjunto fixo de entidades (usado quando já se tem o subgrafo)."""
    if not ids:
        return []
    linhas = executor.ler(Q_FATOS, ids=list(dict.fromkeys(ids)), limite=limite) or []
    return [{"ref": f"F{i}", **linha} for i, linha in enumerate(linhas, start=1)]


def formatar_fatos(fatos: list[dict]) -> str:
    """Texto enviado ao LLM: um fato por linha, com a evidência entre aspas."""
    return "\n".join(
        f'[{f["ref"]}] {f["origem"]} -{f["relacao"]}-> {f["destino"]}. Evidência: "{f.get("evidencia") or ""}"'
        for f in fatos
    )


# ----------------------------------------------------------------------------- citações
_RE_CITACAO = re.compile(r"\[(F\d+)\]")


def verificar_citacoes(texto: str, fatos: list[dict]) -> tuple[list[str], list[str]]:
    """Separa as citações válidas das inventadas, mantendo a ordem de aparição."""
    existentes = {f["ref"] for f in fatos}
    citadas = list(dict.fromkeys(_RE_CITACAO.findall(texto or "")))
    validas = [c for c in citadas if c in existentes]
    invalidas = [c for c in citadas if c not in existentes]
    return validas, invalidas


def remover_citacoes_invalidas(texto: str, invalidas: list[str]) -> str:
    for ref in invalidas:
        texto = texto.replace(f"[{ref}]", "")
    return texto


# ----------------------------------------------------------------------------- LLM
TIMEOUT_LLM = 60  # segundos por chamada


def _erro_temporario(erro: Exception) -> bool:
    """Erros que costumam passar sozinhos: limite de uso, servidor sobrecarregado, demora."""
    texto = f"{type(erro).__name__} {erro}".lower()
    return any(t in texto for t in (
        "429", "rate limit", "quota", "resource_exhausted", "overloaded", "503", "500", "unavailable",
        "timeout", "timed out", "apiconnectionerror",
    ))


def chamar_llm(cliente: tuple[Any, str], sistema: str, usuario: str, max_tokens: int = 4096, tentativas: int = 3) -> str:
    """Chamada única para Anthropic ou para APIs compatíveis com OpenAI.

    - max_tokens alto: modelos que "pensam" antes de responder (ex.: Gemini 2.5) gastam
      parte do limite no raciocínio; com limite baixo a resposta volta vazia.
    - Resposta vazia vira erro explícito (nunca passa como resposta válida).
    - Cada chamada tem no máximo TIMEOUT_LLM segundos (sem isso, a biblioteca espera até 10 min).
    - Erros temporários (429, sobrecarga, demora): espera e tenta de novo (15 s, 30 s).
    """
    api, modelo = cliente
    if hasattr(api, "with_options"):
        api = api.with_options(timeout=TIMEOUT_LLM, max_retries=0)
    for tentativa in range(1, tentativas + 1):
        try:
            if hasattr(api, "messages"):
                r = api.messages.create(
                    model=modelo, max_tokens=max_tokens, temperature=0, system=sistema,
                    messages=[{"role": "user", "content": usuario}],
                )
                texto = "".join(getattr(b, "text", "") or "" for b in r.content)
            else:
                r = api.chat.completions.create(
                    model=modelo, temperature=0, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                )
                texto = r.choices[0].message.content or ""
                if not texto.strip():
                    motivo = getattr(r.choices[0], "finish_reason", "?")
                    raise ValueError(f"o LLM devolveu resposta vazia (finish_reason={motivo})")
            if not texto.strip():
                raise ValueError("o LLM devolveu resposta vazia")
            return texto
        except Exception as erro:
            if tentativa < tentativas and _erro_temporario(erro):
                time.sleep(15 * 2 ** (tentativa - 1))
                continue
            raise


def resposta_sem_llm(pergunta: str, fatos: list[dict], maximo: int = 5) -> RespostaFundamentada:
    """Resposta extrativa: lista os fatos mais relevantes, já citados."""
    if not fatos:
        return RespostaFundamentada(texto=f"{FRASE_ABSTENCAO}: {pergunta}", abstencao=True)
    principais = fatos[:maximo]
    linhas = [f'{f["origem"]} {f["relacao"].lower().replace("_", " ")} {f["destino"]} [{f["ref"]}].' for f in principais]
    texto = "Fatos do grafo mais relevantes para a pergunta:\n" + "\n".join(f"- {l}" for l in linhas)
    return RespostaFundamentada(texto=texto, fatos=fatos, citados=[f["ref"] for f in principais])


def responder_com_fatos(
    pergunta: str, fatos: list[dict], cliente: tuple[Any, str] | None = None
) -> RespostaFundamentada:
    """Gera a resposta citando os fatos; sem cliente LLM, usa a resposta extrativa."""
    if cliente is None:
        return resposta_sem_llm(pergunta, fatos)
    if not fatos:
        return RespostaFundamentada(texto=f"{FRASE_ABSTENCAO}: {pergunta}", usou_llm=True, abstencao=True)

    usuario = f"Fatos disponíveis:\n{formatar_fatos(fatos)}\n\nPergunta: {pergunta}"
    try:
        texto = chamar_llm(cliente, SISTEMA, usuario).strip()
    except Exception:
        return resposta_sem_llm(pergunta, fatos)

    validas, invalidas = verificar_citacoes(texto, fatos)
    return RespostaFundamentada(
        texto=remover_citacoes_invalidas(texto, invalidas),
        fatos=fatos,
        citados=validas,
        citacoes_invalidas=invalidas,
        usou_llm=True,
        abstencao=texto.startswith(FRASE_ABSTENCAO),
    )


def responder(
    executor: Any, pergunta: str, nos: list[dict] | None = None, *, embedder: Any | None = None,
    usar_llm: bool = True, cliente: tuple[Any, str] | None = None,
) -> RespostaFundamentada:
    """Atalho usado pela interface: fatos recuperados para a pergunta -> resposta citada."""
    fatos = recuperar_fatos(executor, pergunta, embedder=embedder, sementes_extras=ids_de_entidades(nos or []))
    if usar_llm and cliente is None:
        cliente = _client_llm()
    return responder_com_fatos(pergunta, fatos, cliente if usar_llm else None)
