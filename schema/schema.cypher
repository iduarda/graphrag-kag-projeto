// =====================================================================
// Schema do grafo — História da Computação (GraphRAG & KAG)
// Cria as constraints e os índices definidos em docs/modelo_lpg.md.
//
// Pode ser executado mais de uma vez: o "IF NOT EXISTS" evita erros
// e não duplica nada.
//
// Como rodar (PowerShell, na raiz do repositório, com o Docker ligado):
//   Get-Content schema/schema.cypher | docker exec -i neo4j-graphrag cypher-shell -u neo4j -p SUA_SENHA
// =====================================================================


// ---------------------------------------------------------------------
// 1. CONSTRAINTS DE UNICIDADE — camada de conhecimento
//    Cada constraint também cria automaticamente um índice (range)
//    na propriedade, então as buscas por id já ficam rápidas.
// ---------------------------------------------------------------------

// Rótulo genérico: nenhuma entidade com id repetido, de qualquer tipo.
CREATE CONSTRAINT entidade_id_unico IF NOT EXISTS
FOR (n:Entidade) REQUIRE n.id IS UNIQUE;

// Um por rótulo específico: usados pelos MERGE da ingestão (Pessoa 3).
CREATE CONSTRAINT pessoa_id_unico IF NOT EXISTS
FOR (n:Pessoa) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT organizacao_id_unico IF NOT EXISTS
FOR (n:Organizacao) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT linguagem_id_unico IF NOT EXISTS
FOR (n:Linguagem) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT hardware_id_unico IF NOT EXISTS
FOR (n:Hardware) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT tecnologia_id_unico IF NOT EXISTS
FOR (n:Tecnologia) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT premio_id_unico IF NOT EXISTS
FOR (n:Premio) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT local_id_unico IF NOT EXISTS
FOR (n:Local) REQUIRE n.id IS UNIQUE;


// ---------------------------------------------------------------------
// 2. CONSTRAINTS DE UNICIDADE — camada de procedência
// ---------------------------------------------------------------------

CREATE CONSTRAINT documento_id_unico IF NOT EXISTS
FOR (d:Documento) REQUIRE d.id IS UNIQUE;

CREATE CONSTRAINT documento_url_unico IF NOT EXISTS
FOR (d:Documento) REQUIRE d.url IS UNIQUE;

CREATE CONSTRAINT chunk_id_unico IF NOT EXISTS
FOR (c:Chunk) REQUIRE c.id IS UNIQUE;

// Obs.: constraints de existência (ex.: "nome não pode ser nulo") só
// existem no Neo4j Enterprise. Na Community, isso é validado no código
// da ingestão.


// ---------------------------------------------------------------------
// 3. ÍNDICES RANGE (BTree) — filtros e buscas exatas no Cypher
// ---------------------------------------------------------------------

CREATE INDEX entidade_nome IF NOT EXISTS
FOR (n:Entidade) ON (n.nome);

CREATE INDEX documento_categoria IF NOT EXISTS
FOR (d:Documento) ON (d.categoria);

CREATE INDEX documento_titulo IF NOT EXISTS
FOR (d:Documento) ON (d.titulo);

CREATE INDEX linguagem_ano IF NOT EXISTS
FOR (n:Linguagem) ON (n.ano);

CREATE INDEX hardware_ano IF NOT EXISTS
FOR (n:Hardware) ON (n.ano);

CREATE INDEX tecnologia_ano IF NOT EXISTS
FOR (n:Tecnologia) ON (n.ano);


// ---------------------------------------------------------------------
// 4. ÍNDICES FULLTEXT — busca por palavras e nomes aproximados
// ---------------------------------------------------------------------

// Acha entidades pelo nome, apelido ou descrição (ex.: "Turing").
CREATE FULLTEXT INDEX entidade_fulltext IF NOT EXISTS
FOR (n:Entidade) ON EACH [n.nome, n.aliases, n.descricao];

// Busca por palavras-chave dentro dos trechos de texto.
CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS
FOR (c:Chunk) ON EACH [c.texto];


// ---------------------------------------------------------------------
// 5. ÍNDICES VETORIAIS — busca semântica
//    768 dimensões = modelo intfloat/multilingual-e5-base
// ---------------------------------------------------------------------

CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX entidade_embedding IF NOT EXISTS
FOR (n:Entidade) ON (n.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};
