"""
Gera os chunks de cada página de data/raw/ (etapa 1 da extração, Pessoa 2).

Saída: data/chunks/<mesmo nome do raw>.json com o bloco "documento" e a lista
"chunks" já no formato do contrato (docs/formato_extracao.md).

Uso (na raiz do repositório):
    python scripts/gerar_chunks.py
"""

import json
import statistics
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.chunking import MAX_TOKENS, _contar_tokens, dividir_em_chunks, metodo_contagem  # noqa: E402

PASTA_RAW = RAIZ / "data" / "raw"
PASTA_CHUNKS = RAIZ / "data" / "chunks"


def main() -> None:
    PASTA_CHUNKS.mkdir(parents=True, exist_ok=True)
    tamanhos, tokens, por_doc = [], [], []
    for arquivo in sorted(PASTA_RAW.glob("*.json")):
        raw = json.loads(arquivo.read_text(encoding="utf-8"))
        doc = {
            "id": raw["pageid"],
            "titulo": raw["titulo"],
            "url": raw["url"],
            "categoria": raw["categoria"],
            "wikidata_id": raw.get("wikidata_id"),
        }
        chunks = [
            {"id": f"{doc['id']}_{i}", "ordem": i, "texto": t}
            for i, t in enumerate(dividir_em_chunks(raw["texto"]))
        ]
        saida = {"documento": doc, "chunks": chunks}
        (PASTA_CHUNKS / arquivo.name).write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
        tamanhos += [len(c["texto"]) for c in chunks]
        tokens += [_contar_tokens(c["texto"]) for c in chunks]
        por_doc.append(len(chunks))

    print(f"{len(por_doc)} documentos, {len(tamanhos)} chunks")
    print(f"caracteres por chunk: min {min(tamanhos)}, média {statistics.mean(tamanhos):.0f}, máx {max(tamanhos)}")
    print(f"tokens por chunk ({metodo_contagem()}): média {statistics.mean(tokens):.0f}, máx {max(tokens)} (limite {MAX_TOKENS})")
    print(f"chunks por documento: min {min(por_doc)}, média {statistics.mean(por_doc):.1f}, máx {max(por_doc)}")


if __name__ == "__main__":
    main()
