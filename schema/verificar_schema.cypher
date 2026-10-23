// =====================================================================
// Verificação do schema
// Cria alguns nós de teste, confere se cada tipo de índice responde e
// apaga os nós de teste no final. Não mexe em nenhum outro dado.
//
// Como rodar (PowerShell, na raiz do repositório):
//   Get-Content schema/verificar_schema.cypher | docker exec -i neo4j-graphrag cypher-shell -u neo4j -p SUA_SENHA
// =====================================================================

// Espera todos os índices terminarem de ser construídos.
CALL db.awaitIndexes(300);

// --- Contagem do que foi criado --------------------------------------
// Esperado: 11 constraints de unicidade.
SHOW CONSTRAINTS YIELD name, type
WHERE type CONTAINS 'UNIQUENESS'
RETURN count(*) AS constraints_unicidade;

// Esperado: RANGE = 17 (11 das constraints + 6 criados à parte),
// FULLTEXT = 2, VECTOR = 2. Todos com estado ONLINE.
SHOW INDEXES YIELD name, type, state
WHERE type IN ['RANGE', 'FULLTEXT', 'VECTOR']
RETURN type, state, count(*) AS quantidade
ORDER BY type;

// --- Dados de teste ---------------------------------------------------
// Obs.: textos sem acento de propósito, porque o PowerShell estraga acentos ao
// enviar o arquivo para o cypher-shell.
// Ids começam com "teste:" para serem apagados com segurança no final.
CREATE (p:Entidade:Pessoa {
  id: 'teste:pessoa_exemplo',
  nome: 'Pessoa Exemplo',
  aliases: ['apelidoexclusivo'],
  descricao: 'No criado apenas para testar o schema.',
  embedding: [i IN range(1, 768) | 0.01]
})
CREATE (c:Chunk {
  id: 'teste:chunk_exemplo',
  texto: 'Trecho de teste sobre o computador palavrachaveteste.',
  ordem: 0,
  embedding: [i IN range(1, 768) | 0.01]
})
RETURN 'nos de teste criados' AS etapa;

CALL db.awaitIndexes(300);

// --- Teste 1: fulltext pelo nome ---------------------------------------
// Esperado: 1 linha com 'Pessoa Exemplo'.
CALL db.index.fulltext.queryNodes('entidade_fulltext', 'Exemplo')
YIELD node, score
RETURN 'fulltext_nome' AS teste, node.nome AS encontrado;

// --- Teste 2: fulltext pelo apelido (lista de aliases) -----------------
// Esperado: 1 linha. Se vier vazio, a versão do Neo4j não indexa listas
// no fulltext e os aliases precisarão ser guardados também como texto.
CALL db.index.fulltext.queryNodes('entidade_fulltext', 'apelidoexclusivo')
YIELD node, score
RETURN 'fulltext_alias' AS teste, node.nome AS encontrado;

// --- Teste 3: fulltext nos chunks --------------------------------------
// Esperado: 1 linha.
CALL db.index.fulltext.queryNodes('chunk_fulltext', 'palavrachaveteste')
YIELD node, score
RETURN 'fulltext_chunk' AS teste, node.id AS encontrado;

// --- Teste 4: busca vetorial nos chunks --------------------------------
// Esperado: 1 linha com score próximo de 1.0.
CALL db.index.vector.queryNodes('chunk_embedding', 1, [i IN range(1, 768) | 0.01])
YIELD node, score
RETURN 'vetorial_chunk' AS teste, node.id AS encontrado, score;

// --- Teste 5: busca vetorial nas entidades -----------------------------
// Esperado: 1 linha com score próximo de 1.0.
CALL db.index.vector.queryNodes('entidade_embedding', 1, [i IN range(1, 768) | 0.01])
YIELD node, score
RETURN 'vetorial_entidade' AS teste, node.nome AS encontrado, score;

// --- Limpeza ------------------------------------------------------------
MATCH (n) WHERE n.id STARTS WITH 'teste:'
DETACH DELETE n
RETURN count(n) AS nos_de_teste_apagados;
