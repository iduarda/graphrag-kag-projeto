"""
Avaliação comparativa GraphRAG x RAG só vetorial (ver src/avaliacao_sistema.py).

Precisa do Neo4j ligado e de um LLM configurado no .env, por exemplo:
    GRAPHRAG_LLM_PROVIDER=anthropic
    ANTHROPIC_API_KEY=...
ou
    GRAPHRAG_LLM_PROVIDER=openai
    OPENAI_API_KEY=...            (e OPENAI_BASE_URL para Gemini/Groq/Ollama)
    GRAPHRAG_LLM_MODEL=...

Uso (na raiz do repositório):
    python scripts/avaliar_sistema.py

Resultado em data/resultados/avaliacao_sistema.json.
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.avaliacao_sistema import avaliar  # noqa: E402
from src.embeddings import E5Embedder  # noqa: E402
from src.ingestao import carregar_env, conectar  # noqa: E402
from src.recuperacao import _client_llm  # noqa: E402

SAIDA = RAIZ / "data" / "resultados" / "avaliacao_sistema.json"


def main() -> None:
    carregar_env()
    cliente = _client_llm()
    if cliente is None:
        sys.exit("Nenhum LLM configurado. Defina ANTHROPIC_API_KEY ou OPENAI_API_KEY no .env (veja o topo deste arquivo).")

    print(f"LLM: {cliente[1]}. Carregando o modelo de embedding...")
    embedder = E5Embedder()
    ex = conectar()
    try:
        resultado = avaliar(ex, embedder, cliente, log=lambda m: print(m, flush=True))
    finally:
        ex.fechar()

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + f"{'Métrica':38} {'RAG vetorial':>13} {'GraphRAG':>10}")
    rv, gr = resultado["resumo"]["rag_vetorial"], resultado["resumo"]["graphrag"]
    for chave, rotulo in [
        ("acerto_medio_pct", "Acerto médio (%)"),
        ("respostas_corretas", "Respostas totalmente corretas"),
        ("respostas_fundamentadas_pct", "Respostas fundamentadas (%)"),
        ("frases_sem_citacao_pct", "Frases sem citação (%)"),
        ("citacoes_invalidas", "Citações inventadas"),
        ("abstencoes", "Abstenções"),
        ("erros", "Perguntas com erro (refazer)"),
    ]:
        print(f"{rotulo:38} {rv[chave]:>13} {gr[chave]:>10}")
    if rv["erros"] or gr["erros"]:
        print("\nATENÇÃO: houve erros (veja as linhas com '!'). Esses números NÃO valem para o artigo; rode de novo.")
    print(f"\nDetalhes em {SAIDA}")


if __name__ == "__main__":
    main()
