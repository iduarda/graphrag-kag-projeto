"""
Coleta das páginas da Wikipédia em português usadas como dataset do projeto.

Lê a lista em data/paginas.csv (colunas: categoria, titulo, semente),
baixa o texto de cada página pela API da Wikipédia e salva um JSON por
página em data/raw/. No final, gera data/raw/_relatorio.csv e mostra no
terminal os títulos que não foram encontrados.

Uso (na raiz do repositório):
    python scripts/coletar_wikipedia.py

Páginas já baixadas são puladas. Para baixar tudo de novo, use --forcar.
"""

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_LISTA = RAIZ / "data" / "paginas.csv"
PASTA_SAIDA = RAIZ / "data" / "raw"

API_URL = "https://pt.wikipedia.org/w/api.php"
# A Wikimedia pede um User-Agent que identifique o projeto.
# Inclui um contato (o repositório), como a política da Wikimedia pede.
USER_AGENT = "GraphRAG-KAG-ProjetoFinal/1.0 (https://github.com/iduarda/graphrag-kag-projeto; projeto academico) python-requests"
PAUSA_ENTRE_PAGINAS = 1.5  # segundos entre páginas, para não sobrecarregar a API
MAX_TENTATIVAS = 5  # tentativas por página quando a API recusa ou a conexão falha
MAX_ERROS_SEGUIDOS = 5  # interrompe a coleta se tantas páginas seguidas falharem

# Seções do fim do artigo que não têm conteúdo útil para extração.
SECOES_DESCARTADAS = [
    "Referências",
    "Ligações externas",
    "Bibliografia",
    "Ver também",
    "Notas",
    "Leitura adicional",
    "Leituras adicionais",
    "Fontes",
]


def nome_de_arquivo(titulo: str) -> str:
    """Converte um título em nome de arquivo seguro: 'C++' -> 'Cpp.json'."""
    titulo = titulo.replace("+", "p").replace("#", "sharp")
    sem_acento = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", sem_acento).strip("_") + ".json"


def limpar_texto(texto: str) -> str:
    """Corta as seções finais (Referências, Ligações externas...) e espaços extras."""
    for secao in SECOES_DESCARTADAS:
        padrao = re.compile(r"^==+\s*" + re.escape(secao) + r"\s*==+\s*$", re.MULTILINE)
        achado = padrao.search(texto)
        if achado:
            texto = texto[: achado.start()]
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def requisitar_com_espera(sessao: requests.Session, params: dict) -> dict:
    """Faz a requisição e, se a API pedir para esperar (429) ou falhar, tenta de novo."""
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        espera = 10 * 2 ** (tentativa - 1)  # 10s, 20s, 40s, 80s...
        try:
            resposta = sessao.get(API_URL, params=params, timeout=30)
        except requests.RequestException as erro:
            motivo = f"falha de conexão ({type(erro).__name__})"
        else:
            if resposta.status_code == 200:
                return resposta.json()
            if resposta.status_code == 429 or resposta.status_code >= 500:
                motivo = f"API respondeu {resposta.status_code}"
                retry_after = resposta.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    espera = max(espera, int(retry_after))
            else:
                # Erros como 403 não melhoram esperando: mostra o motivo e para.
                raise requests.HTTPError(
                    f"API respondeu {resposta.status_code}: {resposta.text[:200]}", response=resposta
                )
        if tentativa == MAX_TENTATIVAS:
            raise requests.ConnectionError(f"{motivo}; desistindo após {MAX_TENTATIVAS} tentativas")
        print(f"      {motivo}, aguardando {espera}s e tentando de novo...")
        time.sleep(espera)
    raise AssertionError("inalcançável")


def buscar_pagina(sessao: requests.Session, titulo: str) -> dict | None:
    """Baixa uma página pela API. Retorna None se a página não existir."""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "redirects": 1,
        "titles": titulo,
        "prop": "extracts|info|pageprops",
        "explaintext": 1,
        "inprop": "url",
        "ppprop": "wikibase_item|disambiguation",
    }
    dados = requisitar_com_espera(sessao, params)

    pagina = dados["query"]["pages"][0]
    if pagina.get("missing"):
        return None

    props = pagina.get("pageprops", {})
    return {
        "titulo": pagina["title"],
        "pageid": pagina["pageid"],
        "url": pagina["fullurl"],
        "wikidata_id": props.get("wikibase_item"),
        "desambiguacao": "disambiguation" in props,
        "redirecionado": bool(dados["query"].get("redirects")),
        "texto": limpar_texto(pagina.get("extract", "")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forcar", action="store_true", help="baixa de novo mesmo as páginas já salvas")
    args = parser.parse_args()

    if not ARQUIVO_LISTA.exists():
        sys.exit(f"Arquivo não encontrado: {ARQUIVO_LISTA}")

    with open(ARQUIVO_LISTA, encoding="utf-8") as f:
        paginas = list(csv.DictReader(f))

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    sessao = requests.Session()
    sessao.headers["User-Agent"] = USER_AGENT

    relatorio = []
    erros_seguidos = 0
    print(f"Coletando {len(paginas)} páginas...\n")

    for i, linha in enumerate(paginas, start=1):
        solicitado = linha["titulo"]
        destino = PASTA_SAIDA / nome_de_arquivo(solicitado)
        prefixo = f"[{i:3}/{len(paginas)}] {solicitado}"

        if destino.exists() and not args.forcar:
            salvo = json.loads(destino.read_text(encoding="utf-8"))
            n = len(salvo["texto"])
            status = "desambiguacao" if salvo["desambiguacao"] else ("texto_curto" if n < 1500 else "ok")
            print(f"{prefixo} -> já baixada ({status}), pulando")
            relatorio.append({"titulo_solicitado": solicitado, "status": status, "titulo_final": salvo["titulo"], "caracteres": n})
            continue

        try:
            pagina = buscar_pagina(sessao, solicitado)
        except requests.RequestException as erro:
            print(f"{prefixo} -> ERRO: {erro}")
            relatorio.append({"titulo_solicitado": solicitado, "status": "erro", "titulo_final": "", "caracteres": ""})
            erros_seguidos += 1
            if erros_seguidos >= MAX_ERROS_SEGUIDOS:
                print(f"\n{MAX_ERROS_SEGUIDOS} páginas seguidas falharam. Interrompendo a coleta.")
                print("Espere alguns minutos e rode de novo: o script continua de onde parou.")
                break
            continue
        erros_seguidos = 0

        if pagina is None:
            print(f"{prefixo} -> NÃO ENCONTRADA")
            relatorio.append({"titulo_solicitado": solicitado, "status": "nao_encontrada", "titulo_final": "", "caracteres": ""})
            continue

        if pagina["desambiguacao"]:
            status = "desambiguacao"
        elif len(pagina["texto"]) < 1500:
            status = "texto_curto"
        else:
            status = "ok"

        registro = {
            "titulo_solicitado": solicitado,
            "categoria": linha["categoria"],
            "semente": linha["semente"] == "sim",
            **{k: v for k, v in pagina.items() if k != "texto"},
            "data_coleta": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "licenca": "CC BY-SA 4.0",
            "texto": pagina["texto"],
        }
        destino.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")

        extra = f" (redirecionada para '{pagina['titulo']}')" if pagina["redirecionado"] else ""
        print(f"{prefixo} -> {status}, {len(pagina['texto'])} caracteres{extra}")
        relatorio.append({
            "titulo_solicitado": solicitado,
            "status": status,
            "titulo_final": pagina["titulo"],
            "caracteres": len(pagina["texto"]),
        })
        time.sleep(PAUSA_ENTRE_PAGINAS)

    with open(PASTA_SAIDA / "_relatorio.csv", "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=["titulo_solicitado", "status", "titulo_final", "caracteres"])
        escritor.writeheader()
        escritor.writerows(relatorio)

    problemas = [r for r in relatorio if r["status"] not in ("ok", "ja_existia")]
    print(f"\nConcluído. Relatório em {PASTA_SAIDA / '_relatorio.csv'}")
    if problemas:
        print(f"\n{len(problemas)} página(s) precisam de atenção:")
        for r in problemas:
            print(f"  - {r['titulo_solicitado']}: {r['status']}")
    else:
        print("Todas as páginas foram baixadas sem problemas.")


if __name__ == "__main__":
    main()