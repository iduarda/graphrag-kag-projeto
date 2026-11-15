"""
Avaliação sem chave de API, passo 2 de 2: pontua as respostas geradas na sessão interativa.

Lê data/avaliacao/contextos_llm.json (passo 1) e data/avaliacao/respostas_llm.json,
confere as citações de cada resposta contra o seu contexto e calcula as mesmas
métricas de scripts/avaliar_sistema.py. Se existir data/avaliacao/julgamento_llm.json
(revisor independente), o acerto passa a vir desse julgamento. Não precisa do Neo4j nem de chave de API.

Uso (na raiz do repositório):
    python scripts/pontuar_respostas.py

Resultado em data/resultados/avaliacao_sistema.json.
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.avaliacao_sistema import aplicar_julgamento, imprimir_tabela, pontuar_respostas  # noqa: E402

CONTEXTOS = RAIZ / "data" / "avaliacao" / "contextos_llm.json"
RESPOSTAS = RAIZ / "data" / "avaliacao" / "respostas_llm.json"
JULGAMENTO = RAIZ / "data" / "avaliacao" / "julgamento_llm.json"  # opcional: revisor independente
SAIDA = RAIZ / "data" / "resultados" / "avaliacao_sistema.json"


def main() -> None:
    itens = json.loads(CONTEXTOS.read_text(encoding="utf-8"))
    dados = json.loads(RESPOSTAS.read_text(encoding="utf-8"))
    resultado = pontuar_respostas(itens, dados["respostas"], dados.get("modelo_llm", "?"))
    if JULGAMENTO.exists():
        aplicar_julgamento(resultado, json.loads(JULGAMENTO.read_text(encoding="utf-8")))

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    for linha in resultado["detalhes"]:
        rv, gr = linha["rag_vetorial"], linha["graphrag"]
        print(f"{linha['id']}: RAG vetorial acerto={rv['acerto']:.2f} | GraphRAG acerto={gr['acerto']:.2f}")
    imprimir_tabela(resultado)
    print(f"\nDetalhes em {SAIDA}")


if __name__ == "__main__":
    main()
