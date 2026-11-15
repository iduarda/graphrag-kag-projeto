"""
Algoritmos de grafo com o Neo4j Graph Data Science (Pessoa 5).

- PageRank: importância das entidades. É gravado em cada nó (`pagerank`) e usado
  pelo grounding (src/grounding.py) para priorizar os fatos mais centrais no
  contexto enviado ao LLM.
- Louvain: comunidades de entidades relacionadas, gravadas em `comunidade`.
- Dijkstra: menor caminho entre duas entidades, usado para explicar como elas se ligam.

A projeção é NÃO DIRECIONADA: para responder "como X se liga a Y" o caminho pode
seguir as relações nos dois sentidos (ex.: Turing <-ESTUDOU_EM- ... ).
"""

RELACOES_SEMANTICAS = [
    "TRABALHOU_EM",
    "ESTUDOU_EM",
    "FUNDOU",
    "CRIOU",
    "DESENVOLVEU",
    "INFLUENCIADA_POR",
    "ESCRITO_EM",
    "EXECUTA_EM",
    "UTILIZA",
    "SUCESSOR_DE",
    "RECEBEU",
    "COLABOROU_COM",
    "NASCEU_EM",
    "SEDIADA_EM",
]

NOME_PADRAO = "p5_semantico"


def _projecao_relacoes() -> dict:
    return {tipo: {"orientation": "UNDIRECTED"} for tipo in RELACOES_SEMANTICAS}


def criar_projecao(ex, nome_grafo=NOME_PADRAO):
    existente = ex.ler(
        "CALL gds.graph.exists($nome) YIELD exists RETURN exists",
        nome=nome_grafo,
    )

    if existente and existente[0]["exists"]:
        ex.ler(
            "CALL gds.graph.drop($nome) YIELD graphName RETURN graphName",
            nome=nome_grafo,
        )

    return ex.ler(
        """
        CALL gds.graph.project($nome, 'Entidade', $relacoes)
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
        """,
        nome=nome_grafo,
        relacoes=_projecao_relacoes(),
    )


def gravar_pagerank(ex, nome_grafo=NOME_PADRAO):
    """Grava o PageRank de cada entidade na propriedade `pagerank`."""
    ex.escrever(
        "CALL gds.pageRank.write($nome, {writeProperty: 'pagerank'}) YIELD nodePropertiesWritten RETURN nodePropertiesWritten",
        nome=nome_grafo,
    )


def gravar_louvain(ex, nome_grafo=NOME_PADRAO):
    """Grava a comunidade (Louvain) de cada entidade na propriedade `comunidade`."""
    ex.escrever(
        "CALL gds.louvain.write($nome, {writeProperty: 'comunidade'}) YIELD communityCount RETURN communityCount",
        nome=nome_grafo,
    )


def executar_pagerank(ex, limite=10, nome_grafo=NOME_PADRAO):
    return ex.ler(
        """
        CALL gds.pageRank.stream($nome)
        YIELD nodeId, score
        WITH gds.util.asNode(nodeId) AS n, score
        RETURN n.nome AS entidade, labels(n) AS rotulos, score AS importancia
        ORDER BY importancia DESC
        LIMIT $limite
        """,
        nome=nome_grafo,
        limite=limite,
    )


def executar_louvain(ex, limite=10, nome_grafo=NOME_PADRAO):
    return ex.ler(
        """
        CALL gds.louvain.stream($nome)
        YIELD nodeId, communityId
        WITH communityId, gds.util.asNode(nodeId) AS n
        ORDER BY coalesce(n.pagerank, 0.0) DESC
        WITH communityId, collect(n.nome) AS entidades
        RETURN
            communityId AS comunidade,
            size(entidades) AS quantidade,
            entidades[0..6] AS exemplos
        ORDER BY quantidade DESC
        LIMIT $limite
        """,
        nome=nome_grafo,
        limite=limite,
    )


def executar_dijkstra(ex, origem, destino, nome_grafo=NOME_PADRAO):
    resultados = ex.ler(
        """
        MATCH (a:Entidade {nome: $origem})
        MATCH (b:Entidade {nome: $destino})
        WITH a, b LIMIT 1
        CALL gds.shortestPath.dijkstra.stream(
            $nome,
            {sourceNode: a, targetNode: b}
        )
        YIELD totalCost, nodeIds
        RETURN
            [nodeId IN nodeIds | gds.util.asNode(nodeId).nome] AS caminho,
            totalCost AS custo
        """,
        nome=nome_grafo,
        origem=origem,
        destino=destino,
    )

    if not resultados:
        return {"caminho": [], "custo": None}

    return resultados[0]


def explicar_caminho(ex, caminho):
    """Para cada par vizinho do caminho, a relação que os liga e a evidência."""
    passos = []
    for a, b in zip(caminho, caminho[1:]):
        linhas = ex.ler(
            """
            MATCH (x:Entidade {nome: $a})-[r]-(y:Entidade {nome: $b})
            WHERE r.evidencia IS NOT NULL
            RETURN startNode(r).nome AS origem, type(r) AS relacao, endNode(r).nome AS destino,
                   r.evidencia AS evidencia
            ORDER BY r.confianca DESC LIMIT 1
            """,
            a=a,
            b=b,
        )
        if linhas:
            passos.append(linhas[0])
    return passos


def buscar_evidencias_relacao(ex, origem, destino):
    return ex.ler(
        """
        MATCH (a:Entidade {nome: $origem})-[r]->(b:Entidade {nome: $destino})
        RETURN
            a.nome AS origem,
            type(r) AS relacao,
            b.nome AS destino,
            r.evidencia AS evidencia,
            r.confianca AS confianca,
            r.chunk_ids AS chunk_ids
        """,
        origem=origem,
        destino=destino,
    )
