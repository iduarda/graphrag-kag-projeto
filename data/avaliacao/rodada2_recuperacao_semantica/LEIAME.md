# Rodada 2 — recuperação semântica (sem caminhos mínimos)

Recuperação de fatos por sementes (entidades citadas + busca vetorial), expansão de 2 saltos
só pelas relações semânticas e seleção por similaridade com a pergunta (`recuperar_fatos`),
ainda sem os fatos de caminhos mínimos entre as entidades citadas.

Resultado (acerto médio): RAG vetorial 75,0% × GraphRAG 62,5%.
Mesmos arquivos e procedimento da rodada 1 (ver `../rodada1_recuperacao_original/LEIAME.md`).
