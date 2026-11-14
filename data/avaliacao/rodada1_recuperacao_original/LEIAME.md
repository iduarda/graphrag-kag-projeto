# Rodada 1 — recuperação original

Avaliação feita com a primeira versão da recuperação do GraphRAG, em que os fatos vinham
da expansão genérica da busca híbrida (`executar_busca_hibrida` + `buscar_fatos`): todas as
relações do grafo, inclusive as que passam por chunks, e só 5 caminhos por semente.

- `contextos_llm.json`: prompts exportados por `scripts/exportar_contextos.py`
- `respostas_llm.json`: respostas do Claude Sonnet 5.5, um agente independente por prompt
- `julgamento_llm.json`: julgamento de um revisor independente (gabarito + contexto)
- `resultado.json`: métricas calculadas por `scripts/pontuar_respostas.py`

Resultado (acerto médio): RAG vetorial 79,2% × GraphRAG 23,6%. O grafo continha as respostas;
a falha estava na recuperação, que foi refeita (ver `src/grounding.py`, `recuperar_fatos`).
