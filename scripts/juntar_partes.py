"""
Junta respostas brutas gravadas em partes (data/extracoes_brutas/_partes/<doc>/*.json)
em um único arquivo data/extracoes_brutas/<doc>.json e confere cobertura e evidências.

Usado quando a extração é feita em lotes (ex.: por um assistente/LLM em sessão interativa
em vez da API). Uso:
    python scripts/juntar_partes.py Dennis_Ritchie.json [Unix.json ...]
    python scripts/juntar_partes.py --modelo claude-sonnet-5-5 --todos
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.montagem import localizar_evidencia  # noqa: E402

PASTA_CHUNKS = RAIZ / "data" / "chunks"
PASTA_BRUTAS = RAIZ / "data" / "extracoes_brutas"
PASTA_PARTES = PASTA_BRUTAS / "_partes"


def juntar(nome: str, modelo: str) -> bool:
    chunks = json.loads((PASTA_CHUNKS / nome).read_text(encoding="utf-8"))["chunks"]
    texto = {c["id"]: c["texto"] for c in chunks}
    respostas, problemas = {}, []
    for parte in sorted((PASTA_PARTES / nome.removesuffix(".json")).glob("*.json")):
        try:
            lista = json.loads(parte.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problemas.append(f"{parte.name}: JSON inválido ({e})")
            continue
        for r in lista if isinstance(lista, list) else [lista]:
            respostas[r.get("chunk_id")] = r
    faltando = [c["id"] for c in chunks if c["id"] not in respostas]
    if faltando:
        problemas.append(f"chunks sem resposta: {faltando}")
    for cid, r in respostas.items():
        if cid not in texto:
            problemas.append(f"chunk_id desconhecido: {cid}")
            continue
        for rel in r.get("relacoes", []):
            if localizar_evidencia(rel.get("evidencia"), texto[cid]) is None:
                problemas.append(f"{cid}: evidência não encontrada: {str(rel.get('evidencia'))[:80]!r}")
    ordenadas = [respostas[c["id"]] for c in chunks if c["id"] in respostas]
    saida = {"modelo_llm": modelo, "data": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "chunks": ordenadas}
    (PASTA_BRUTAS / nome).write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    n_e = sum(len(r.get("entidades", [])) for r in ordenadas)
    n_r = sum(len(r.get("relacoes", [])) for r in ordenadas)
    status = "[ok]" if not problemas else "[problemas]"
    print(f"{status} {nome}: {len(ordenadas)}/{len(chunks)} chunks, {n_e} entidades, {n_r} relações")
    for p in problemas[:30]:
        print("   -", p)
    return not problemas


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("arquivos", nargs="*")
    p.add_argument("--modelo", default="claude-sonnet-5-5")
    p.add_argument("--todos", action="store_true")
    a = p.parse_args()
    nomes = [d.name + ".json" for d in sorted(PASTA_PARTES.iterdir()) if d.is_dir()] if a.todos else a.arquivos
    ok = all([juntar(n if n.endswith(".json") else n + ".json", a.modelo) for n in nomes])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
