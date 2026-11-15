"""
Avaliação sem chave de API, passo 1 de 2: exporta os contextos das 12 perguntas.

Para cada pergunta de data/avaliacao/perguntas_multihop.json e cada sistema
(RAG só vetorial e GraphRAG), monta o prompt completo que o LLM receberia e salva
em data/avaliacao/contextos_llm.json. As respostas esperadas NÃO vão para esse
arquivo, para não influenciar quem gerar as respostas.

Depois, as respostas são geradas por um LLM numa sessão interativa (com os prompts
do arquivo, um item por vez) e salvas em data/avaliacao/respostas_llm.json, no formato
    {"modelo_llm": "...", "respostas": {"q01_rag_vetorial": "texto...", ...}}

Passo 2: python scripts/pontuar_respostas.py

Uso (na raiz do repositório, com o Neo4j ligado):
    python scripts/exportar_contextos.py
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.avaliacao_sistema import exportar_contextos  # noqa: E402
from src.embeddings import E5Embedder  # noqa: E402
from src.ingestao import conectar  # noqa: E402

SAIDA = RAIZ / "data" / "avaliacao" / "contextos_llm.json"


def main() -> None:
    print("Carregando o modelo de embedding...", flush=True)
    embedder = E5Embedder()
    ex = conectar()
    try:
        itens = exportar_contextos(ex, embedder, log=lambda m: print(m, flush=True))
    finally:
        ex.fechar()
    SAIDA.write_text(json.dumps(itens, ensure_ascii=False, indent=2), encoding="utf-8")
    automaticas = sum("resposta_automatica" in i for i in itens)
    print(f"\n{len(itens)} contextos salvos em {SAIDA}")
    print(f"{len(itens) - automaticas} precisam de resposta do LLM; {automaticas} já são abstenção automática (sem contexto).")


if __name__ == "__main__":
    main()
