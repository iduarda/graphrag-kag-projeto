"""
Pipeline de ingestão: data/extracoes/ -> resolução de entidades -> Neo4j -> embeddings (Pessoa 3).

Uso (na raiz do repositório, com o Neo4j ligado e o schema criado):
    python scripts/ingerir_neo4j.py                    # carga completa + embeddings
    python scripts/ingerir_neo4j.py --so-resolver      # NÃO usa o banco: só valida, resolve e gera relatórios
    python scripts/ingerir_neo4j.py --sem-embeddings   # carrega o grafo, deixa os embeddings para depois
    python scripts/ingerir_neo4j.py --limpar           # apaga TODO o grafo antes de carregar

Saídas: data/resolucao/fusoes.json, data/resolucao/candidatos_revisao.json, data/relatorio_ingestao.json
É seguro rodar mais de uma vez (tudo usa MERGE).
"""

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import ingestao as pipeline  # noqa: E402
from src.embeddings import E5Embedder  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracoes", type=Path, default=pipeline.PASTA_EXTRACOES)
    ap.add_argument("--manual", type=Path, default=pipeline.ARQUIVO_MANUAL, help="ajustes manuais da resolução")
    ap.add_argument("--so-resolver", action="store_true", help="não conecta ao Neo4j nem gera embeddings")
    ap.add_argument("--sem-embeddings", action="store_true")
    ap.add_argument("--recalcular-embeddings", action="store_true", help="refaz também os que já existem")
    ap.add_argument("--sugerir-por-embedding", action="store_true",
                    help="usa o modelo para sugerir pares parecidos em candidatos_revisao.json (não funde nada)")
    ap.add_argument("--limpar", action="store_true", help="apaga todo o grafo antes de carregar")
    a = ap.parse_args()

    embedder = None if (a.so_resolver or a.sem_embeddings) and not a.sugerir_por_embedding else E5Embedder()
    if a.so_resolver:
        relatorio = pipeline.executar_pipeline(None, embedder, a.extracoes, a.manual,
                                               sugerir_por_embedding=a.sugerir_por_embedding)
    else:
        with pipeline.conectar() as ex:
            relatorio = pipeline.executar_pipeline(
                ex, None if a.sem_embeddings else embedder, a.extracoes, a.manual, limpar=a.limpar,
                recalcular_embeddings=a.recalcular_embeddings, sugerir_por_embedding=a.sugerir_por_embedding,
            )
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
