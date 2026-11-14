
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


def criar_projecao(ex, nome_grafo="p5_semantico"):
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
        CALL gds.graph.project(
            $nome,
            'Entidade',
            $relacoes
        )
        YIELD graphName, nodeCount, relationshipCount
        RETURN graphName, nodeCount, relationshipCount
        """,
        nome=nome_grafo,
        relacoes=RELACOES_SEMANTICAS,
    )


def executar_pagerank(ex, limite=10, nome_grafo="p5_semantico"):
    return ex.ler(
        """
        CALL gds.pageRank.stream($nome)
        YIELD nodeId, score
        RETURN
            gds.util.asNode(nodeId).nome AS entidade,
            score AS importancia
        ORDER BY importancia DESC
        LIMIT $limite
        """,
        nome=nome_grafo,
        limite=limite,
    )


def executar_louvain(ex, limite=10, nome_grafo="p5_semantico"):
    return ex.ler(
        """
        CALL gds.louvain.stream($nome)
        YIELD nodeId, communityId
        WITH communityId, collect(gds.util.asNode(nodeId).nome) AS entidades
        RETURN
            communityId AS comunidade,
            size(entidades) AS quantidade,
            entidades[0..5] AS exemplos
        ORDER BY quantidade DESC
        LIMIT $limite
        """,
        nome=nome_grafo,
        limite=limite,
    )


def executar_dijkstra(ex, origem, destino, nome_grafo="p5_semantico"):
    resultados = ex.ler(
        """
        MATCH (a:Entidade {nome: $origem})
        CALL {
            WITH a
            MATCH (b:Entidade {nome: $destino})
            RETURN id(b) AS targetNode
        }
        CALL gds.shortestPath.dijkstra.stream(
            $nome,
            {
                sourceNode: id(a),
                targetNode: targetNode
            }
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
