# Formato JSON da extração

Contrato entre a **extração** (Pessoa 2) e a **ingestão no Neo4j** (Pessoa 3).
A Pessoa 2 gera **um JSON por documento** em `data/extracoes/`, com o mesmo nome do arquivo de `data/raw/`
(ex.: `data/raw/Dennis_Ritchie.json` → `data/extracoes/Dennis_Ritchie.json`).
A Pessoa 3 lê esses arquivos e cria os nós e relações descritos em [`modelo_lpg.md`](modelo_lpg.md).

Exemplo completo e válido: [`exemplo_extracao.json`](exemplo_extracao.json) (texto ilustrativo).

**Antes de entregar qualquer extração, rode o validador:**

```bash
python scripts/validar_extracao.py              # valida tudo em data/extracoes/
python scripts/validar_extracao.py caminho.json # valida um arquivo
```

As regras abaixo estão implementadas em `src/contrato.py`. Se o formato mudar, mude os dois juntos.

---

## Estrutura geral

```json
{
  "versao_formato": "1.0",
  "documento": { ... },
  "extracao": { ... },
  "entidade_principal": "pessoa:dennis_ritchie",
  "chunks": [ ... ],
  "entidades": [ ... ],
  "relacoes": [ ... ]
}
```

| Campo | Obrigatório | Descrição |
|---|---|---|
| `versao_formato` | sim | Sempre `"1.0"` nesta versão |
| `documento` | sim | Dados da página, copiados do arquivo de `data/raw/` |
| `extracao` | não | Qual modelo de LLM fez a extração e quando (útil para o artigo) |
| `entidade_principal` | sim | Id da entidade de que a página trata. Vira a relação `(:Documento)-[:SOBRE]->(entidade)` |
| `chunks` | sim | Os trechos em que o texto foi dividido |
| `entidades` | sim | As entidades encontradas no documento |
| `relacoes` | sim | As relações encontradas entre essas entidades |

---

## `documento`

| Campo | Tipo | Origem em `data/raw/` |
|---|---|---|
| `id` | inteiro | `pageid` |
| `titulo` | texto | `titulo` |
| `url` | texto | `url` |
| `categoria` | texto | `categoria` |
| `wikidata_id` | texto ou `null` | `wikidata_id` |

## `chunks`

| Campo | Tipo | Regra |
|---|---|---|
| `id` | texto | `"<documento.id>_<ordem>"`, ex.: `"12345_0"` |
| `ordem` | inteiro | Posição do chunk no documento, começando em 0 |
| `texto` | texto | O trecho. Deve caber em **512 tokens** (limite do modelo de embedding) |

> Sugestão de chunking: dividir por parágrafos e juntar até ~1.500 caracteres, com sobreposição de uma frase entre chunks vizinhos.

## `entidades`

| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `id` | texto | sim | Gerado por `gerar_id(tipo, nome)` (ver abaixo) |
| `nome` | texto | sim | Nome canônico, como aparece no título da Wikipédia quando houver |
| `tipo` | texto | sim | Um de: `Pessoa`, `Organizacao`, `Linguagem`, `Hardware`, `Tecnologia`, `Premio`, `Local` |
| `aliases` | lista de textos | não | Outras formas do nome encontradas no texto |
| `descricao` | texto | não | Uma frase sobre a entidade, baseada no texto |
| `propriedades` | objeto | não | Só as propriedades da tabela abaixo |
| `chunk_ids` | lista de textos | sim | Chunks em que a entidade aparece. Vira `(:Chunk)-[:MENCIONA]->(entidade)` |

**Uma entidade aparece uma única vez por arquivo**, mesmo que seja citada em vários chunks: basta listar todos em `chunk_ids`.

### Propriedades aceitas por tipo

| Tipo | Propriedades | Valores |
|---|---|---|
| `Pessoa` | `ano_nascimento`, `ano_morte` | anos (inteiros) |
| `Organizacao` | `tipo`, `ano_fundacao` | `tipo`: `empresa`, `universidade`, `laboratorio`, `agencia`, `fundacao` |
| `Linguagem` | `ano`, `paradigmas` | `paradigmas`: lista de textos |
| `Hardware` | `ano`, `tipo` | `tipo`: `computador`, `processador`, `periferico` |
| `Tecnologia` | `ano`, `tipo` | `tipo`: `sistema`, `rede`, `protocolo`, `invencao`, `conceito` |
| `Premio` | — | — |
| `Local` | `tipo` | `tipo`: `pais`, `cidade` |

Anos são sempre **inteiros** (`1972`, não `"1972"` nem `"década de 1970"`). Se o texto não diz o ano exato, **não preencha**.

### Regra do `id`

```python
from src.contrato import gerar_id
gerar_id("Pessoa", "Alan Turing")                     # "pessoa:alan_turing"
gerar_id("Linguagem", "C++")                          # "linguagem:cpp"
gerar_id("Organizacao", "Universidade da Pensilvânia") # "organizacao:universidade_da_pensilvania"
```

Tipo em minúsculas + `:` + nome sem acentos, em minúsculas, com `_` no lugar de espaços e símbolos.
Como todo documento usa a mesma regra, "Bell Labs" citado em 20 páginas diferentes vira sempre o mesmo nó.

> A resolução de entidades da Pessoa 3 cuida dos casos que a regra não pega (ex.: "Turing" e "Alan Turing", "Laboratórios Bell" e "Bell Labs"), usando `aliases`, a busca fulltext e os embeddings.

## `relacoes`

| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `origem` | texto | sim | `id` de uma entidade **deste arquivo** |
| `tipo` | texto | sim | Um dos tipos da tabela abaixo |
| `destino` | texto | sim | `id` de uma entidade **deste arquivo** |
| `propriedades` | objeto | não | Só as propriedades aceitas pelo tipo |
| `chunk_id` | texto | sim | Chunk em que a relação foi encontrada |
| `evidencia` | texto | sim | Trecho **copiado do texto** que comprova a relação |
| `confianca` | número | sim | De 0 a 1 |

Se a mesma relação aparece em dois chunks, registre-a duas vezes (uma por `chunk_id`). A Pessoa 3 junta as duas em uma relação só no grafo, com os dois chunks em `chunk_ids`.

### Tipos de relação permitidos

| Relação | Origem | Destino | Propriedades |
|---|---|---|---|
| `TRABALHOU_EM` | Pessoa | Organizacao | `cargo`, `ano_inicio`, `ano_fim` |
| `ESTUDOU_EM` | Pessoa | Organizacao | `curso`, `ano` |
| `FUNDOU` | Pessoa | Organizacao | `ano` |
| `CRIOU` | Pessoa | Linguagem, Hardware, Tecnologia | `ano` |
| `DESENVOLVEU` | Organizacao | Linguagem, Hardware, Tecnologia | `ano` |
| `INFLUENCIADA_POR` | Linguagem | Linguagem | — |
| `ESCRITO_EM` | Tecnologia | Linguagem | — |
| `EXECUTA_EM` | Tecnologia, Linguagem | Hardware | — |
| `UTILIZA` | Hardware | Hardware | — |
| `SUCESSOR_DE` | qualquer | mesmo tipo da origem | — |
| `RECEBEU` | Pessoa | Premio | `ano` |
| `COLABOROU_COM` | Pessoa | Pessoa | `contexto` |
| `NASCEU_EM` | Pessoa | Local | — |
| `SEDIADA_EM` | Organizacao | Local | — |

A direção importa: `C -INFLUENCIADA_POR-> B` quer dizer "C foi influenciada por B", e `Intel 8086 -SUCESSOR_DE-> Intel 8080` quer dizer "o 8086 veio depois do 8080".

---

## Regras contra alucinação (para o prompt de extração)

1. **Só extraia o que está escrito no chunk.** Conhecimento prévio do LLM não entra, mesmo que seja verdade.
2. **`evidencia` é obrigatória** e precisa ser um trecho real do chunk. Ela é o que a interface mostra como prova.
3. **Na dúvida, não extraia.** Uma relação a menos é melhor que uma relação inventada.
4. **Relação fora da tabela não entra.** Se o texto traz uma relação que não se encaixa em nenhum tipo, ela fica de fora. Se acontecer muito, avise para discutirmos um tipo novo.
