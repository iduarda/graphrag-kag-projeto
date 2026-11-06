"""
Pipeline de ingestão (Pessoa 3): data/extracoes/*.json -> validação -> resolução de entidades
-> Neo4j -> embeddings.

Cada etapa é uma função pequena e testável; `executar_pipeline` só encadeia.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from src.contrato import validar_extracao
from src import carregador, embeddings, resolucao

RAIZ = Path(__file__).resolve().parent.parent
PASTA_EXTRACOES = RAIZ / "data" / "extracoes"
PASTA_RAW = RAIZ / "data" / "raw"
ARQUIVO_MANUAL = RAIZ / "data" / "resolucao_manual.json"
PASTA_RESOLUCAO = RAIZ / "data" / "resolucao"
ARQUIVO_RELATORIO = RAIZ / "data" / "relatorio_ingestao.json"


# ----------------------------------------------------------------------------- ambiente
def carregar_env(arquivo: Path | None = None) -> None:
    """Lê o .env (CHAVE=valor) sem depender de bibliotecas extras. Variáveis já definidas não são sobrescritas."""
    arquivo = arquivo or RAIZ / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def conectar() -> carregador.ExecutorNeo4j:
    carregar_env()
    senha = os.environ.get("NEO4J_PASSWORD")
    if not senha:
        raise SystemExit("Defina NEO4J_PASSWORD no .env (veja o README).")
    return carregador.ExecutorNeo4j(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"), os.environ.get("NEO4J_USER", "neo4j"), senha
    )


# ----------------------------------------------------------------------------- leitura
def ler_extracoes(pasta: Path = PASTA_EXTRACOES) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    """Lê e valida todos os JSONs. Arquivo inválido NÃO entra no grafo; vai para a lista de rejeitados."""
    validos, rejeitados = [], []
    for arquivo in sorted(Path(pasta).glob("*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            rejeitados.append((arquivo.name, [f"não foi possível ler: {e}"]))
            continue
        erros = validar_extracao(dados)
        if erros:
            rejeitados.append((arquivo.name, erros))
        else:
            validos.append(dados)
    return validos, rejeitados


def ler_extras_raw(pasta: Path = PASTA_RAW) -> dict[int, dict]:
    """semente, data_coleta e licenca de cada página (não vêm no JSON de extração), indexados por pageid."""
    extras: dict[int, dict] = {}
    if not Path(pasta).exists():
        return extras
    for arquivo in Path(pasta).glob("*.json"):
        try:
            d = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(d, dict) and "pageid" in d:
            extras[d["pageid"]] = {k: d.get(k) for k in ("semente", "data_coleta", "licenca")}
    return extras


# ----------------------------------------------------------------------------- preparação
@dataclass
class Preparado:
    entidades: dict
    mapa: dict[str, str]
    relacoes: list[dict]
    fusoes: list[resolucao.Fusao]
    candidatos_revisao: list[dict]
    estatisticas: dict = field(default_factory=dict)


def preparar(docs: list[dict], manual: Path | None = ARQUIVO_MANUAL, vetores: dict | None = None) -> Preparado:
    """Tudo o que dá para fazer SEM banco: agregar, resolver entidades e reapontar relações."""
    tabela = resolucao.agregar_entidades(docs)
    res = resolucao.resolver(tabela, manual=manual, embeddings=vetores)
    relacoes, est_rel = resolucao.remapear_relacoes(docs, res.mapa)
    est = {
        "documentos": len(docs),
        "chunks": sum(len(d["chunks"]) for d in docs),
        "entidades_antes_da_resolucao": len(tabela),
        "entidades_depois_da_resolucao": len(res.entidades),
        "entidades_fundidas": len(res.fusoes),
        "fusoes_por_regra": _contar([f.regra for f in res.fusoes]),
        "candidatos_para_revisao": len(res.candidatos_revisao),
        **est_rel,
    }
    return Preparado(res.entidades, res.mapa, relacoes, res.fusoes, res.candidatos_revisao, est)


def _contar(itens: list[str]) -> dict[str, int]:
    saida: dict[str, int] = {}
    for i in itens:
        saida[i] = saida.get(i, 0) + 1
    return dict(sorted(saida.items()))


def vetores_para_revisao(docs: list[dict], embedder: embeddings.Embedder) -> dict[str, list[float]]:
    """Embeddings de todas as entidades (antes da resolução), só para sugerir pares a revisar."""
    tabela = resolucao.agregar_entidades(docs)
    ids = sorted(tabela)
    textos = [embeddings.texto_da_entidade(tabela[i].nome, tabela[i].descricao) for i in ids]
    return dict(zip(ids, embedder.codificar_documentos(textos)))


# ----------------------------------------------------------------------------- relatórios
def salvar_fusoes(prep: Preparado, pasta: Path = PASTA_RESOLUCAO) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    fusoes = [{"canonico": f.canonico, "absorvido": f.absorvido, "regra": f.regra, "motivo": f.motivo} for f in prep.fusoes]
    (pasta / "fusoes.json").write_text(json.dumps(fusoes, ensure_ascii=False, indent=2), encoding="utf-8")
    (pasta / "candidatos_revisao.json").write_text(
        json.dumps(prep.candidatos_revisao, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def salvar_relatorio(relatorio: dict, arquivo: Path = ARQUIVO_RELATORIO) -> None:
    arquivo.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- execução
def executar_pipeline(
    ex: carregador.Executor | None,
    embedder: embeddings.Embedder | None,
    pasta_extracoes: Path = PASTA_EXTRACOES,
    manual: Path | None = ARQUIVO_MANUAL,
    limpar: bool = False,
    recalcular_embeddings: bool = False,
    sugerir_por_embedding: bool = False,
    salvar: bool = True,
) -> dict:
    """
    ex=None faz só a parte offline (validar + resolver + relatórios), sem tocar no Neo4j.
    embedder=None pula os embeddings.
    """
    docs, rejeitados = ler_extracoes(pasta_extracoes)
    if not docs:
        raise SystemExit(f"Nenhuma extração válida em {pasta_extracoes}")

    vetores = vetores_para_revisao(docs, embedder) if (sugerir_por_embedding and embedder) else None
    prep = preparar(docs, manual, vetores)
    relatorio = {**prep.estatisticas, "arquivos_rejeitados": [{"arquivo": n, "erros": e[:5]} for n, e in rejeitados]}

    if ex is not None:
        if embedder is not None:
            embeddings.verificar_dimensao(ex, embedder)
        if limpar:
            carregador.limpar_grafo(ex)
        relatorio["linhas_enviadas"] = carregador.carregar(
            ex, docs, prep.entidades, prep.mapa, prep.relacoes, ler_extras_raw()
        )
        if embedder is not None:
            relatorio["embeddings_gravados"] = {
                "chunks": embeddings.gravar_embeddings_chunks(ex, embedder, recalcular_embeddings),
                "entidades": embeddings.gravar_embeddings_entidades(ex, embedder, recalcular_embeddings),
            }
        relatorio["grafo_final"] = carregador.contar_grafo(ex)

    if salvar:
        salvar_fusoes(prep)
        salvar_relatorio(relatorio)
    return relatorio
