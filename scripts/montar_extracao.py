"""
Monta e valida os JSONs finais de extração (etapa 3, Pessoa 2).

Entrada: data/chunks/<arquivo>.json + data/extracoes_brutas/<arquivo>.json
Saída:   data/extracoes/<arquivo>.json  (formato de docs/formato_extracao.md)
         data/relatorio_extracao.json   (números para o artigo)

Uso (na raiz do repositório):
    python scripts/montar_extracao.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.contrato import validar_extracao  # noqa: E402
from src.montagem import Canonicas, erros_de_primeira, montar  # noqa: E402

PASTA_CHUNKS = RAIZ / "data" / "chunks"
PASTA_BRUTAS = RAIZ / "data" / "extracoes_brutas"
PASTA_FINAL = RAIZ / "data" / "extracoes"
ARQUIVO_RELATORIO = RAIZ / "data" / "relatorio_extracao.json"


def main() -> None:
    canon = Canonicas()
    PASTA_FINAL.mkdir(parents=True, exist_ok=True)
    total = Counter()
    por_doc = {}
    aprovados_de_primeira = 0
    invalidos = []
    tipos_ent, tipos_rel, modelos = Counter(), Counter(), Counter()
    ids_globais = set()
    triplas = set()

    brutas = sorted(PASTA_BRUTAS.glob("*.json"))
    for arq in brutas:
        chunks_doc = json.loads((PASTA_CHUNKS / arq.name).read_text(encoding="utf-8"))
        bruto = json.loads(arq.read_text(encoding="utf-8"))
        n_resp = len(bruto.get("chunks", []))
        if n_resp != len(chunks_doc["chunks"]):
            print(f"[aviso] {arq.name}: {n_resp} respostas para {len(chunks_doc['chunks'])} chunks")

        if not erros_de_primeira(chunks_doc, bruto, arq.name, canon):
            aprovados_de_primeira += 1

        final, est = montar(chunks_doc, bruto, canon, arq.name)
        erros = validar_extracao(final)
        if erros:
            invalidos.append(arq.name)
            print(f"[ERRO] {arq.name}: {erros[:3]}")
            continue
        (PASTA_FINAL / arq.name).write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        total.update(est)
        modelos[bruto.get("modelo_llm")] += 1
        tipos_ent.update(e["tipo"] for e in final["entidades"])
        tipos_rel.update(r["tipo"] for r in final["relacoes"])
        ids_globais.update(e["id"] for e in final["entidades"])
        triplas.update((r["origem"], r["tipo"], r["destino"]) for r in final["relacoes"])
        por_doc[arq.name] = {"chunks": len(final["chunks"]), "entidades": est["entidades_finais"],
                             "relacoes": est["relacoes_finais"]}

    n = len(brutas)
    rb, rf = total["relacoes_brutas"], total["relacoes_finais"]
    relatorio = {
        "documentos": n,
        "documentos_validos": n - len(invalidos),
        "aprovados_pelo_validador_de_primeira": aprovados_de_primeira,
        "pct_aprovados_de_primeira": round(100 * aprovados_de_primeira / n, 1) if n else 0,
        "chunks": sum(d["chunks"] for d in por_doc.values()),
        "entidades_brutas": total["entidades_brutas"],
        "entidades_finais_por_documento_somadas": total["entidades_finais"],
        "entidades_unicas_no_corpus": len(ids_globais),
        "relacoes_brutas": rb,
        "relacoes_finais": rf,
        "relacoes_unicas_no_corpus": len(triplas),
        "pct_relacoes_aproveitadas": round(100 * rf / rb, 1) if rb else 0,
        "entidades_por_tipo": dict(tipos_ent.most_common()),
        "relacoes_por_tipo": dict(tipos_rel.most_common()),
        "descartes_e_correcoes": {k: v for k, v in sorted(total.items())
                                  if k.startswith(("entidade_", "relacao_", "principal_", "chunk_"))},
        "modelos_llm": dict(modelos),
        "invalidos": invalidos,
        "por_documento": por_doc,
    }
    ARQUIVO_RELATORIO.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{n} documentos | válidos: {n - len(invalidos)} | aprovados de primeira: "
          f"{aprovados_de_primeira} ({relatorio['pct_aprovados_de_primeira']}%)")
    print(f"entidades: {total['entidades_brutas']} brutas -> {len(ids_globais)} únicas no corpus")
    print(f"relações:  {rb} brutas -> {rf} finais ({relatorio['pct_relacoes_aproveitadas']}%) -> {len(triplas)} únicas no corpus")
    print("descartes/correções:", json.dumps(relatorio["descartes_e_correcoes"], ensure_ascii=False))
    print(f"relatório em {ARQUIVO_RELATORIO.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
