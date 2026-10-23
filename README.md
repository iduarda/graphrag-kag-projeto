# GraphRAG & KAG — Projeto Final

Sistema de Geração Aumentada por Recuperação baseado em grafos de conhecimento (GraphRAG/KAG), que combina o Neo4j com LLMs para responder perguntas complexas com rastreabilidade e menos alucinações.

**Disciplina:** Tendências em Ciência da Computação — Bacharelado em Ciência da Computação
**Docente:** Ricardo Roberto de Lima
**Período:** 2026 / Outubro – Dezembro

> **Domínio do projeto:** História da Computação — pessoas, organizações, linguagens, máquinas e tecnologias que marcaram a computação, a partir de 147 artigos da Wikipédia em português.

---

## Equipe

| Integrante | Responsabilidade |
|---|---|
| _Maria Eduarda Zumbé_ | Modelagem LPG no Neo4j, repositório e coordenação do artigo |
| _Anna Maria Albino_ | Extração de entidades e relações (NER/RE) |
| _Nome 3_ | Pipeline de ingestão e resolução de entidades |
| _Nome 4_ | Recuperação híbrida (vetorial + Cypher) e multi-hop |
| _Nome 5_ | Algoritmos de grafo, grounding e avaliação |
| _Nome 6_ | Interface (Streamlit) e visualização do subgrafo |

---

## Arquitetura

```
Documentos → [1. Text-to-Graph] → [2. Neo4j LPG] → [3. Recuperação Híbrida] → [4. Interface + Grafo]
```

1. **Text-to-Graph:** extração de entidades e relações com LLM, com resolução de entidades duplicadas.
2. **Neo4j LPG:** modelo de grafo com constraints e índices BTree, Fulltext e Vector.
3. **Recuperação híbrida:** busca vetorial + consultas Cypher, travessia multi-hop e algoritmos de grafo (PageRank, Louvain, Shortest Path).
4. **Interface:** Streamlit com visualização do subgrafo usado e exibição da procedência da resposta.

---

## Estrutura do repositório

```
.
├── data/
│   ├── paginas.csv           # Lista das 147 páginas (categoria, título, semente)
│   └── raw/                  # Um JSON por página coletada + _relatorio.csv
├── docs/
│   ├── modelo_lpg.md         # Modelo do grafo (nós, relações, propriedades)
│   ├── formato_extracao.md   # Formato JSON da extração (Pessoa 2 -> Pessoa 3)
│   └── exemplo_extracao.json # Exemplo válido do formato
├── schema/
│   ├── schema.cypher         # Cria constraints e índices no Neo4j
│   └── verificar_schema.cypher  # Testa se o schema está funcionando
├── scripts/
│   ├── coletar_wikipedia.py  # Coleta dos textos pela API da Wikipédia
│   └── validar_extracao.py   # Valida os JSONs de extração
├── src/
│   └── contrato.py           # Tipos, relações, regra de ids e validação do formato
├── neo4j/
│   └── import/               # Arquivos para importação no Neo4j
├── docker-compose.yml        # Banco Neo4j com APOC e GDS
├── requirements.txt          # Dependências Python
├── .env.example              # Modelo das variáveis de ambiente (copiar para .env)
├── .gitignore                # Arquivos que não vão para o GitHub (.env, banco local, .venv)
└── README.md
```

_A estrutura será atualizada conforme os módulos forem criados._

---

## Dataset

147 artigos da **Wikipédia em português** sobre história da computação, divididos em 5 categorias:

| Categoria | Páginas |
|---|---|
| Pessoa | 40 |
| Organização | 29 |
| Linguagem | 29 |
| Hardware | 30 |
| Sistema / Invenção / Evento | 19 |

As páginas foram escolhidas a partir de 20 "sementes" (ex.: Alan Turing, Bell Labs, Unix, ENIAC) e das páginas que elas citam, para garantir conexões entre os documentos e permitir perguntas multi-hop.

Cada arquivo em `data/raw/` contém: título, URL, ID do Wikidata, categoria, data da coleta, licença e o texto limpo (sem as seções de referências e ligações externas).

**O dataset já está no repositório — não é preciso coletar de novo.** Para refazer a coleta (por exemplo, após mudar a lista):

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python scripts/coletar_wikipedia.py
```

O script pula as páginas já baixadas; use `--forcar` para baixar tudo de novo.

---

## Como rodar o Neo4j localmente

### Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado e aberto
- [Git](https://git-scm.com/)
- [GitHub](https://github.com/)

### Passo a passo

1. **Clone o repositório:**
   ```bash
   git clone <URL_DO_REPOSITORIO>
   cd <NOME_DA_PASTA>
   ```

2. **Crie o arquivo `.env`** copiando o modelo:
   ```bash
   cp .env.example .env
   ```
   No Windows (PowerShell): `copy .env.example .env`

   Abra o `.env` e defina uma senha com pelo menos 8 caracteres:
   ```
   NEO4J_PASSWORD=sua_senha_aqui
   ```

3. **Suba o banco:**
   ```bash
   docker compose up -d
   ```
   Na primeira vez, o Docker baixa a imagem e os plugins, o que pode levar alguns minutos.

4. **Acesse o Neo4j Browser** em [http://localhost:7474](http://localhost:7474)
   - Usuário: `neo4j`
   - Senha: a que você definiu no `.env`

5. **Confirme que os plugins estão ativos** rodando no Browser:
   ```cypher
   RETURN gds.version();
   ```
   ```cypher
   RETURN apoc.version();
   ```
   Os dois devem retornar a versão (ex.: `5.26.x`).

### Criar o schema (constraints e índices)

Com o banco ligado, rode na raiz do repositório (troque `SUA_SENHA` pela senha do `.env`):

**Windows (PowerShell):**
```powershell
Get-Content schema/schema.cypher | docker exec -i neo4j-graphrag cypher-shell -u neo4j -p "SUA_SENHA"
```

**Linux/Mac:**
```bash
docker exec -i neo4j-graphrag cypher-shell -u neo4j -p "SUA_SENHA" < schema/schema.cypher
```

O script pode ser rodado mais de uma vez sem duplicar nada. Ele cria:

| Tipo | Qtd. | Detalhe |
|---|---|---|
| Constraints de unicidade | 11 | `id` de cada rótulo, `Documento.url` |
| Índices range (BTree) | 6 | nome, categoria, título, ano |
| Índices fulltext | 2 | `entidade_fulltext`, `chunk_fulltext` |
| Índices vetoriais | 2 | `chunk_embedding`, `entidade_embedding` (768 dim., cosseno) |

Para conferir se tudo funciona, rode o `schema/verificar_schema.cypher` do mesmo jeito. Ele cria nós de teste, testa cada índice e apaga os nós no final. Resultado esperado: 11 constraints, índices RANGE 17 / FULLTEXT 2 / VECTOR 2 todos `ONLINE`, e uma linha encontrada em cada um dos 5 testes.

Detalhes do modelo em [`docs/modelo_lpg.md`](docs/modelo_lpg.md).

### Formato da extração

As extrações de entidades e relações seguem o formato descrito em [`docs/formato_extracao.md`](docs/formato_extracao.md). Antes de carregar no banco, valide:

```bash
python scripts/validar_extracao.py
```

### Comandos úteis

| Ação | Comando |
|---|---|
| Ligar o banco | `docker compose up -d` |
| Desligar o banco | `docker compose down` |
| Ver os logs | `docker compose logs -f neo4j` |

Os dados ficam salvos em `neo4j/data/` na sua máquina, que não vai para o GitHub.

### Conexão pelo Python

| Variável | Valor local |
|---|---|
| URI | `bolt://localhost:7687` |
| Usuário | `neo4j` |
| Senha | a do `.env` |

---

## Bibliotecas e créditos

_A preencher conforme o projeto avança (exigência do professor: toda biblioteca de código aberto usada deve ser documentada e creditada)._

| Biblioteca | Uso no projeto | Licença |
|---|---|---|
| [Wikipédia em português](https://pt.wikipedia.org/) | Textos do dataset | CC BY-SA 4.0 |
| [Requests](https://requests.readthedocs.io/) | Requisições HTTP na coleta | Apache 2.0 |
| [Neo4j](https://neo4j.com/) | Banco de dados de grafos | GPLv3 (Community) |
| [APOC](https://neo4j.com/labs/apoc/) | Procedimentos utilitários | Apache 2.0 |
| [Neo4j Graph Data Science](https://neo4j.com/docs/graph-data-science/) | Algoritmos de grafo | GPLv3 |

---

## Entregáveis

- [x] Fase 1 — Proposta e modelagem LPG
  - [x] Domínio e dataset (147 páginas da Wikipédia)
  - [x] Modelo LPG conceitual
  - [x] Schema no Neo4j (constraints e índices)
  - [x] Formato JSON de entidades e relações
- [ ] Fase 2 — Ingestão e pipeline NLP/LLM
- [ ] Fase 3 — Algoritmos e GraphRAG/KAG
- [ ] Fase 4 — Código final, interface, artigo (SBC/IEEE) e defesa