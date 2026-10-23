"""
Valida os JSONs de extração contra o contrato (docs/formato_extracao.md).

Uso (na raiz do repositório):
    python scripts/validar_extracao.py                       # valida tudo em data/extracoes/
    python scripts/validar_extracao.py docs/exemplo_extracao.json   # valida um arquivo

Sai com código 1 se algum arquivo tiver erro (útil para testes automáticos).
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.contrato import validar_extracao  # noqa: E402

PASTA_PADRAO = RAIZ / "data" / "extracoes"


def main() -> None:
    if len(sys.argv) > 1:
        arquivos = [Path(a) for a in sys.argv[1:]]
    else:
        arquivos = sorted(PASTA_PADRAO.glob("*.json"))
        if not arquivos:
            sys.exit(f"Nenhum JSON encontrado em {PASTA_PADRAO}")

    total_erros = 0
    arquivos_com_erro = 0
    for arquivo in arquivos:
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            erros = [f"não foi possível ler o arquivo: {e}"]
        else:
            erros = validar_extracao(dados)

        if erros:
            arquivos_com_erro += 1
            total_erros += len(erros)
            print(f"\n[ERRO] {arquivo.name} ({len(erros)} problema(s))")
            for e in erros:
                print(f"   - {e}")
        else:
            print(f"[ok]   {arquivo.name}")

    print(f"\n{len(arquivos)} arquivo(s) verificado(s), {arquivos_com_erro} com erro, {total_erros} problema(s) no total.")
    sys.exit(1 if arquivos_com_erro else 0)


if __name__ == "__main__":
    main()
