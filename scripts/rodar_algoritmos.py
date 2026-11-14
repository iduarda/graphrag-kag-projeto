"""
Roda os algoritmos de grafo (PageRank, Louvain e Dijkstra) no Neo4j e salva os
resultados em data/resultados/algoritmos.json, para a interface e para o artigo.

Também grava `pagerank` e `comunidade` em cada entidade. O PageRank gravado é
usado pelo grounding para priorizar os fatos mais centrais.

Uso (na raiz do repositório, com o Neo4j ligado):
    python scripts/rodar_algoritmos.py
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.ingestao import conectar  # noqa: E402
from src import algoritmos_grafo as alg  # noqa: E402

SAIDA = RAIZ / "data" / "resultados" / "algoritmos.json"

# Pares usados para mostrar caminhos entre personagens de "grupos" diferentes do corpus.
PARES_CAMINHO = [
    ("Alan Turing", "Linus Torvalds"),
    ("Grace Hopper", "Python"),
    ("Dennis Ritchie", "Steve Jobs"),
    ("Tim Berners-Lee", "Bill Gates"),
]


def main() -> None:
    ex = conectar()
    try:
        projecao = alg.criar_projecao(ex)[0]
        print(f"Projeção: {projecao['nodeCount']} nós, {projecao['relationshipCount']} relações")

        alg.gravar_pagerank(ex)
        alg.gravar_louvain(ex)
        print("PageRank e comunidades gravados nos nós.")

        pagerank = alg.executar_pagerank(ex, limite=15)
        comunidades = alg.executar_louvain(ex, limite=10)
        total_comunidades = ex.ler(
            "MATCH (n:Entidade) WHERE n.comunidade IS NOT NULL RETURN count(DISTINCT n.comunidade) AS total"
        )[0]["total"]

        caminhos = []
        for origem, destino in PARES_CAMINHO:
            r = alg.executar_dijkstra(ex, origem, destino)
            passos = alg.explicar_caminho(ex, r["caminho"]) if r["caminho"] else []
            caminhos.append({"origem": origem, "destino": destino, **r, "passos": passos})
    finally:
        ex.fechar()

    resultado = {
        "projecao": projecao,
        "pagerank_top15": pagerank,
        "total_comunidades": total_comunidades,
        "maiores_comunidades": comunidades,
        "caminhos": caminhos,
    }
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nTop 15 por PageRank:")
    for i, p in enumerate(pagerank, start=1):
        print(f"  {i:2}. {p['entidade']} ({p['importancia']:.2f})")
    print(f"\n{total_comunidades} comunidades. Maiores:")
    for c in comunidades:
        print(f"  {c['quantidade']:4} entidades: {', '.join(c['exemplos'])}")
    print("\nCaminhos mais curtos:")
    for c in caminhos:
        print(f"  {c['origem']} -> {c['destino']}: {' -> '.join(c['caminho']) or 'sem caminho'}")
    print(f"\nResultados salvos em {SAIDA}")


if __name__ == "__main__":
    main()
