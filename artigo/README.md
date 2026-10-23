# Artigo técnico (template SBC)

O artigo é dividido em um arquivo por seção, em `secoes/`. **Cada pessoa edita só o(s) seu(s) arquivo(s)**,
assim ninguém tem conflito no Git. O topo de cada arquivo traz um roteiro do que escrever.

Trechos ainda não escritos usam o comando `\pendente{...}` e aparecem **em vermelho** no PDF.
Antes da entrega, não pode sobrar nenhum.

## Quem escreve o quê

| Arquivo | Seção | Responsável | Situação |
|---|---|---|---|
| `00_resumo.tex` | Abstract e Resumo | Pessoa 1 (Maria Eduarda) | por último |
| `01_introducao.tex` | 1. Introdução | Pessoa 1 (Maria Eduarda) | depois das seções técnicas |
| `02_fundamentacao.tex` | 2. Fundamentação Teórica e Trabalhos Relacionados | Pessoa 4 | pendente |
| `03_dataset.tex` | 3. Dataset | Pessoa 1 (Maria Eduarda) | **escrita** |
| `04_modelagem.tex` | 4. Modelagem do Grafo | Pessoa 1 (Maria Eduarda) | **escrita** |
| `05_extracao.tex` | 5. Extração de Entidades e Relações | Pessoa 2 (Anna Maria Albino) | pendente |
| `06_ingestao.tex` | 6. Ingestão e Resolução de Entidades | Pessoa 3 | pendente |
| `07_recuperacao.tex` | 7. Recuperação Híbrida e Multi-hop | Pessoa 4 | pendente |
| `08_algoritmos_grounding.tex` | 8. Algoritmos de Grafo e Ancoragem | Pessoa 5 | pendente |
| `09_interface.tex` | 9. Interface e Explicabilidade | Pessoa 6 | pendente |
| `10_avaliacao.tex` | 10. Avaliação e Resultados | Pessoa 5 | pendente |
| `11_conclusao.tex` | 11. Conclusão | Pessoa 1 (Maria Eduarda) | depois da avaliação |

Referências ficam em `referencias.bib` (qualquer um pode acrescentar; confira os dados na fonte antes).

## Como gerar o PDF (Overleaf)

O estilo oficial da SBC (`sbc-template.sty` e `sbc.bst`) vem no template do Overleaf, por isso ele não está neste repositório.

1. No [Overleaf](https://www.overleaf.com/): **New Project → Templates**, procure **"SBC Conferences Template"** e abra como template.
2. Apague o `.tex` de exemplo do template, mas **mantenha** `sbc-template.sty` e `sbc.bst`.
3. Envie (Upload) `main.tex`, `referencias.bib` e a pasta `secoes/` deste repositório.
4. Em *Menu*, defina `main.tex` como documento principal e compile.

Sempre que alguém atualizar uma seção no GitHub, reenvie o arquivo correspondente ao Overleaf.
