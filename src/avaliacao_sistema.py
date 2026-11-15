"""
Avaliação comparativa: GraphRAG (nosso sistema) x RAG só vetorial (Pessoa 5).

Os dois sistemas recebem as mesmas perguntas multi-hop (data/avaliacao/perguntas_multihop.json),
usam o mesmo LLM e as mesmas regras de citação. A única diferença é o contexto:
  - RAG vetorial: os k trechos (chunks) mais parecidos com a pergunta, numerados [C1], [C2]...
  - GraphRAG: os fatos do subgrafo recuperado, com evidência, numerados [F1], [F2]...

Perguntas em que o LLM falhou ficam marcadas com "erro" e devem ser refeitas,
nunca contadas como erro do sistema.

Métricas por pergunta:
  - acerto: fração das respostas esperadas que aparecem na resposta (0 a 1);
  - correta: acerto == 1;
  - fundamentada: cita ao menos uma fonte real e nenhuma inventada (ou se absteve);
  - frases_sem_citacao: fração das frases sem nenhuma citação (afirmações sem suporte);
  - citacoes_invalidas: citações a fontes que não estavam no contexto.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from src.grounding import (
    FRASE_ABSTENCAO,
    SISTEMA as SISTEMA_GROUNDING,
    recuperar_fatos,
    chamar_llm,
    formatar_fatos,
    ids_de_entidades,
    responder_com_fatos,
)
from src.recuperacao import executar_busca_hibrida

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_PERGUNTAS = RAIZ / "data" / "avaliacao" / "perguntas_multihop.json"

SISTEMA_RAG = f"""Você responde perguntas sobre a história da computação usando SOMENTE os trechos fornecidos.

Regras obrigatórias:
1. Use apenas os trechos da lista. Não use conhecimento próprio, mesmo que seja verdadeiro.
2. Toda afirmação deve terminar com a citação do(s) trecho(s) que a sustentam, no formato [C1] ou [C1][C3].
3. Para perguntas de vários passos, encadeie as informações e cite cada passo.
4. Se os trechos não forem suficientes, comece a resposta com "{FRASE_ABSTENCAO}" e diga o que falta.
5. Responda em português, em no máximo 5 frases."""

Q_CHUNKS = """
CALL db.index.vector.queryNodes('chunk_embedding', $k, $v) YIELD node, score
OPTIONAL MATCH (d:Documento)-[:TEM_CHUNK]->(node)
RETURN node.id AS id, node.texto AS texto, d.titulo AS documento, score
ORDER BY score DESC
"""


# ----------------------------------------------------------------------------- métricas
def _menciona(texto: str, termo: str) -> bool:
    """Busca o termo como palavra inteira ('C' não casa com 'C++' nem com 'Cobol')."""
    padrao = r"(?<![\w+#])" + re.escape(termo) + r"(?![\w+#])"
    return re.search(padrao, texto, flags=re.IGNORECASE) is not None


def calcular_acerto(texto: str, esperadas: list[list[str]]) -> float:
    if not esperadas:
        return 0.0
    achadas = sum(1 for alternativas in esperadas if any(_menciona(texto, a) for a in alternativas))
    return achadas / len(esperadas)


_RE_QUALQUER_CITACAO = re.compile(r"\[(?:F|C)\d+\]")


def frases_sem_citacao(texto: str) -> float:
    """Fração das frases da resposta que não trazem nenhuma citação."""
    texto = re.sub(r"^\s*[-•]\s*", "", texto or "", flags=re.MULTILINE)
    # "frase. [C1][C2] Próxima" -> "frase [C1][C2]. Próxima": a citação depois do ponto é da frase anterior.
    texto = re.sub(r"([.!?])\s*((?:\[(?:F|C)\d+\])+)", r" \2\1", texto)
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n+", texto) if len(f.strip()) > 15]
    frases = [f for f in frases if not f.startswith(FRASE_ABSTENCAO) and not f.endswith(":")]
    if not frases:
        return 0.0
    return sum(1 for f in frases if not _RE_QUALQUER_CITACAO.search(f)) / len(frases)


def verificar(texto: str, refs_validas: set[str], prefixo: str) -> tuple[list[str], list[str]]:
    citadas = list(dict.fromkeys(re.findall(rf"\[({prefixo}\d+)\]", texto or "")))
    return [c for c in citadas if c in refs_validas], [c for c in citadas if c not in refs_validas]


# ----------------------------------------------------------------------------- contextos
def contexto_rag(executor: Any, embedder: Any, pergunta: str, k: int = 5) -> dict:
    """Contexto do RAG só vetorial: os k chunks mais parecidos com a pergunta, numerados [C#]."""
    vetor = embedder.codificar_consulta(pergunta)
    chunks = executor.ler(Q_CHUNKS, k=k, v=vetor) or []
    contexto = "\n\n".join(f"[C{i}] ({c.get('documento') or '?'}) {c['texto']}" for i, c in enumerate(chunks, 1))
    return {
        "sistema": SISTEMA_RAG,
        "usuario": f"Trechos disponíveis:\n{contexto}\n\nPergunta: {pergunta}",
        "refs": [f"C{i}" for i in range(1, len(chunks) + 1)],
        "prefixo": "C",
    }


def contexto_graphrag(executor: Any, embedder: Any, pergunta: str, usar_llm_cypher: bool = False) -> dict:
    """Contexto do GraphRAG: fatos do subgrafo recuperado pela busca híbrida, numerados [F#]."""
    busca = executar_busca_hibrida(executor, pergunta, profundidade=2, embedder=embedder, usar_llm=usar_llm_cypher)
    fatos = recuperar_fatos(executor, pergunta, embedder=embedder, sementes_extras=ids_de_entidades(busca.nos))
    return {
        "sistema": SISTEMA_GROUNDING,
        "usuario": f"Fatos disponíveis:\n{formatar_fatos(fatos)}\n\nPergunta: {pergunta}",
        "refs": [f["ref"] for f in fatos],
        "prefixo": "F",
        "cypher": busca.cypher,
    }


def resposta_avaliada(texto: str, ctx: dict, esperadas: list[list[str]]) -> dict:
    """Confere as citações do texto contra o contexto e calcula as métricas."""
    texto = (texto or "").strip()
    validas, invalidas = verificar(texto, set(ctx["refs"]), ctx["prefixo"])
    return pontuar({"texto": texto, "citadas": validas, "invalidas": invalidas, "contexto": len(ctx["refs"]),
                    "abstencao": texto.startswith(FRASE_ABSTENCAO)}, esperadas)


# ----------------------------------------------------------------------------- sistemas (com API)
def responder_rag_vetorial(executor: Any, embedder: Any, cliente: tuple, pergunta: str, k: int = 5) -> dict:
    ctx = contexto_rag(executor, embedder, pergunta, k)
    texto = chamar_llm(cliente, ctx["sistema"], ctx["usuario"]).strip()
    validas, invalidas = verificar(texto, set(ctx["refs"]), "C")
    return {"texto": texto, "citadas": validas, "invalidas": invalidas, "contexto": len(ctx["refs"]),
            "abstencao": texto.startswith(FRASE_ABSTENCAO)}


def responder_graphrag(executor: Any, embedder: Any, cliente: tuple, pergunta: str) -> dict:
    busca = executar_busca_hibrida(executor, pergunta, profundidade=2, embedder=embedder, usar_llm=True)
    fatos = recuperar_fatos(executor, pergunta, embedder=embedder, sementes_extras=ids_de_entidades(busca.nos))
    r = responder_com_fatos(pergunta, fatos, cliente)
    if fatos and not r.usou_llm:
        # O LLM falhou e a resposta caiu no modo extrativo: isso NÃO pode contar como resposta do sistema.
        raise RuntimeError("o LLM falhou na geração da resposta do GraphRAG")
    return {"texto": r.texto, "citadas": r.citados, "invalidas": r.citacoes_invalidas, "contexto": len(fatos),
            "abstencao": r.abstencao, "cypher": busca.cypher}


def pontuar(resposta: dict, esperadas: list[list[str]]) -> dict:
    acerto = calcular_acerto(resposta["texto"], esperadas)
    fundamentada = resposta["abstencao"] or (bool(resposta["citadas"]) and not resposta["invalidas"])
    return {
        **resposta,
        "acerto": round(acerto, 3),
        "correta": acerto == 1.0,
        "fundamentada": fundamentada,
        "frases_sem_citacao": round(frases_sem_citacao(resposta["texto"]), 3),
    }


def resumir(resultados: list[dict]) -> dict:
    n = len(resultados) or 1
    return {
        "perguntas": len(resultados),
        "acerto_medio_pct": round(100 * sum(r["acerto"] for r in resultados) / n, 1),
        "respostas_corretas": sum(r["correta"] for r in resultados),
        "respostas_fundamentadas_pct": round(100 * sum(r["fundamentada"] for r in resultados) / n, 1),
        "frases_sem_citacao_pct": round(100 * sum(r["frases_sem_citacao"] for r in resultados) / n, 1),
        "citacoes_invalidas": sum(len(r["invalidas"]) for r in resultados),
        "abstencoes": sum(r["abstencao"] for r in resultados),
        "erros": sum(bool(r.get("erro")) for r in resultados),
        # Só existem quando há julgamento do revisor (aplicar_julgamento):
        "incorretas": sum(r.get("veredito") == "incorreta" for r in resultados),
        "respostas_com_afirmacao_sem_suporte": sum(bool(r.get("sem_suporte")) for r in resultados),
        "afirmacoes_sem_suporte": sum(len(r.get("sem_suporte") or []) for r in resultados),
    }


def avaliar(executor: Any, embedder: Any, cliente: tuple, perguntas: list[dict] | None = None, log=print) -> dict:
    perguntas = perguntas or json.loads(ARQUIVO_PERGUNTAS.read_text(encoding="utf-8"))
    por_sistema: dict[str, list[dict]] = {"rag_vetorial": [], "graphrag": []}
    detalhes = []
    for p in perguntas:
        linha = {"id": p["id"], "pergunta": p["pergunta"], "saltos": p["saltos"]}
        log(f"{p['id']}: {p['pergunta']}")
        for nome, func in (("rag_vetorial", responder_rag_vetorial), ("graphrag", responder_graphrag)):
            log(f"   ... {nome}")
            inicio = time.time()
            try:
                r = pontuar(func(executor, embedder, cliente, p["pergunta"]), p["esperadas"])
            except Exception as erro:  # uma falha não derruba a avaliação inteira
                r = pontuar({"texto": f"ERRO: {erro}", "citadas": [], "invalidas": [], "contexto": 0,
                             "abstencao": False}, p["esperadas"])
                r["erro"] = True
                log(f"   ! {nome} falhou em {p['id']}: {erro}")
            r["segundos"] = round(time.time() - inicio, 1)
            por_sistema[nome].append(r)
            linha[nome] = r
        log(f"   = RAG vetorial acerto={linha['rag_vetorial']['acerto']:.2f} ({linha['rag_vetorial']['segundos']} s) | "
            f"GraphRAG acerto={linha['graphrag']['acerto']:.2f} ({linha['graphrag']['segundos']} s)")
        detalhes.append(linha)
    return {
        "modelo_llm": cliente[1],
        "resumo": {nome: resumir(rs) for nome, rs in por_sistema.items()},
        "detalhes": detalhes,
    }


# ----------------------------------------------------------------------------- fluxo sem API
# Para quando não há chave de API: os contextos são exportados para um arquivo, as respostas
# são geradas por um LLM numa sessão interativa (com os mesmos prompts) e depois pontuadas aqui.
SISTEMAS = ("rag_vetorial", "graphrag")


def exportar_contextos(executor: Any, embedder: Any, perguntas: list[dict] | None = None, log=print) -> list[dict]:
    """Um item por pergunta e sistema, com o prompt completo. As respostas esperadas NÃO são incluídas."""
    perguntas = perguntas or json.loads(ARQUIVO_PERGUNTAS.read_text(encoding="utf-8"))
    itens = []
    for p in perguntas:
        log(f"{p['id']}: {p['pergunta']}")
        for nome, func in (("rag_vetorial", contexto_rag), ("graphrag", contexto_graphrag)):
            ctx = func(executor, embedder, p["pergunta"])
            item = {"id": f"{p['id']}_{nome}", "pergunta_id": p["id"], "sistema_avaliado": nome, **ctx}
            if not ctx["refs"]:
                # Sem contexto, a única resposta válida é a abstenção; nem precisa de LLM.
                item["resposta_automatica"] = f"{FRASE_ABSTENCAO}: nenhum contexto foi recuperado."
            log(f"   {nome}: {len(ctx['refs'])} itens de contexto")
            itens.append(item)
    return itens


def pontuar_respostas(itens: list[dict], respostas: dict[str, str], modelo: str,
                      perguntas: list[dict] | None = None) -> dict:
    """Junta contextos + respostas e calcula as mesmas métricas da avaliação com API."""
    perguntas = perguntas or json.loads(ARQUIVO_PERGUNTAS.read_text(encoding="utf-8"))
    por_id = {p["id"]: p for p in perguntas}
    por_sistema: dict[str, list[dict]] = {nome: [] for nome in SISTEMAS}
    detalhes: dict[str, dict] = {}
    for item in itens:
        p = por_id[item["pergunta_id"]]
        texto = item.get("resposta_automatica") or respostas.get(item["id"])
        if texto is None:
            r = resposta_avaliada("", item, p["esperadas"])
            r["erro"] = True
            r["texto"] = "ERRO: resposta ausente"
        else:
            r = resposta_avaliada(texto, item, p["esperadas"])
        if item.get("cypher"):
            r["cypher"] = item["cypher"]
        por_sistema[item["sistema_avaliado"]].append(r)
        detalhes.setdefault(p["id"], {"id": p["id"], "pergunta": p["pergunta"], "saltos": p["saltos"]})
        detalhes[p["id"]][item["sistema_avaliado"]] = r
    return {
        "modelo_llm": modelo,
        "modo": "sessao_interativa",
        "resumo": {nome: resumir(rs) for nome, rs in por_sistema.items()},
        "detalhes": list(detalhes.values()),
    }


def aplicar_julgamento(resultado: dict, julgamentos: list[dict]) -> dict:
    """Troca o acerto por contagem de nomes pelo julgamento do revisor independente.

    A contagem automática só verifica se o nome esperado aparece no texto, e conta como acerto
    uma resposta que cita o nome para dizer que NÃO o encontrou. O revisor julga se o item foi
    de fato afirmado como resposta e se cada afirmação é sustentada pelo contexto recebido.
    """
    por_id = {j["id"]: j for j in julgamentos}
    por_sistema: dict[str, list[dict]] = {nome: [] for nome in SISTEMAS}
    for linha in resultado["detalhes"]:
        for nome in SISTEMAS:
            r = linha[nome]
            j = por_id.get(f"{linha['id']}_{nome}")
            if j is None:
                raise ValueError(f"falta o julgamento de {linha['id']}_{nome}")
            r["acerto_por_nome"] = r["acerto"]
            r["acerto"] = float(j["acerto"])
            r["veredito"] = j["veredito"]
            r["correta"] = j["veredito"] == "correta"
            r["abstencao"] = j["veredito"] == "abstencao"
            r["sem_suporte"] = j.get("sem_suporte") or []
            r["citacao_errada"] = j.get("citacao_errada") or []
            r["comentario_revisor"] = j.get("comentario", "")
            por_sistema[nome].append(r)
    resultado["resumo"] = {nome: resumir(rs) for nome, rs in por_sistema.items()}
    resultado["julgamento"] = "revisor independente (LLM), contra o gabarito e contra o contexto de cada resposta"
    return resultado


def imprimir_tabela(resultado: dict) -> None:
    rv, gr = resultado["resumo"]["rag_vetorial"], resultado["resumo"]["graphrag"]
    print("\n" + f"{'Métrica':38} {'RAG vetorial':>13} {'GraphRAG':>10}")
    for chave, rotulo in [
        ("acerto_medio_pct", "Acerto médio (%)"),
        ("respostas_corretas", "Respostas totalmente corretas"),
        ("respostas_fundamentadas_pct", "Respostas fundamentadas (%)"),
        ("frases_sem_citacao_pct", "Frases sem citação (%)"),
        ("citacoes_invalidas", "Citações inventadas"),
        ("abstencoes", "Abstenções"),
        ("erros", "Perguntas com erro (refazer)"),
    ] + ([
        ("incorretas", "Respostas incorretas"),
        ("respostas_com_afirmacao_sem_suporte", "Respostas c/ afirmação sem suporte"),
        ("afirmacoes_sem_suporte", "Afirmações sem suporte (total)"),
    ] if "julgamento" in resultado else []):
        print(f"{rotulo:38} {rv[chave]:>13} {gr[chave]:>10}")
    if "julgamento" not in resultado:
        print("\n(Acerto calculado pela presença dos nomes esperados; sem julgamento do revisor.)")
    if rv["erros"] or gr["erros"]:
        print("\nATENÇÃO: houve erros. Esses números NÃO valem para o artigo.")
