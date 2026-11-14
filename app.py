"""
Interface do sistema GraphRAG/KAG (Pessoa 6).

Uso (na raiz do repositório, com o Neo4j ligado):
    streamlit run app.py

Fluxo de uma pergunta:
  1. Busca híbrida (src/recuperacao.py): Cypher + busca vetorial + expansão no grafo.
  2. Grounding (src/grounding.py): fatos do subgrafo com evidências -> resposta
     do LLM citando [F1], [F2]... As citações são verificadas.
  3. Visualização do subgrafo, dos fatos citados e do Cypher gerado.
"""

import graphviz
import streamlit as st

from src.embeddings import E5Embedder
from src.grounding import responder
from src.ingestao import carregar_env, conectar
from src.recuperacao import _client_llm, executar_busca_hibrida

st.set_page_config(page_title="GraphRAG + KAG", page_icon="🔎", layout="wide")
carregar_env()

RELACOES_SEMANTICAS = {
    "TRABALHOU_EM", "ESTUDOU_EM", "FUNDOU", "CRIOU", "DESENVOLVEU", "INFLUENCIADA_POR", "ESCRITO_EM",
    "EXECUTA_EM", "UTILIZA", "SUCESSOR_DE", "RECEBEU", "COLABOROU_COM", "NASCEU_EM", "SEDIADA_EM",
}

EXEMPLOS = [
    "Quais linguagens foram criadas por pessoas que trabalharam no Bell Labs?",
    "Qual processador o Altair 8800 utiliza e qual empresa o desenvolveu?",
    "Em que linguagem o Unix foi escrito e quem criou essa linguagem?",
]


@st.cache_resource(show_spinner="Carregando o modelo de embedding (só na primeira vez)...")
def carregar_embedder():
    return E5Embedder()


@st.cache_resource
def carregar_cliente_llm():
    return _client_llm()


# ----------------------------------------------------------------------------- barra lateral
with st.sidebar:
    st.header("Configurações")
    cliente_llm = carregar_cliente_llm()
    usar_llm = st.toggle(
        "Gerar resposta com LLM",
        value=cliente_llm is not None,
        disabled=cliente_llm is None,
        help="Sem chave de API no .env, a resposta lista os fatos do grafo mais relevantes.",
    )
    if cliente_llm is None:
        st.caption("Nenhum LLM configurado no .env (ANTHROPIC_API_KEY ou OPENAI_API_KEY).")
    else:
        st.caption(f"Modelo: {cliente_llm[1]}")
    profundidade = st.slider("Saltos na travessia (multi-hop)", 1, 3, 2)
    st.divider()
    st.markdown("**Exemplos de perguntas**")
    for exemplo in EXEMPLOS:
        st.caption(exemplo)

# ----------------------------------------------------------------------------- página
st.title("GraphRAG + KAG")
st.subheader("História da Computação")
st.write(
    "Faça uma pergunta. A resposta é montada só com fatos do grafo de conhecimento, "
    "e cada afirmação indica de qual fato veio, com o trecho da Wikipédia que a comprova."
)

pergunta = st.text_input("Digite sua pergunta:", placeholder=EXEMPLOS[0])

if st.button("Buscar", type="primary"):
    if not pergunta.strip():
        st.warning("Digite uma pergunta antes de buscar.")
        st.stop()

    executor = None
    try:
        with st.spinner("Consultando o grafo de conhecimento..."):
            executor = conectar()
            resultado = executar_busca_hibrida(
                executor, pergunta, profundidade=profundidade, embedder=carregar_embedder(), usar_llm=usar_llm
            )
            resposta = responder(
                executor, pergunta, resultado.nos, embedder=carregar_embedder(),
                usar_llm=usar_llm, cliente=cliente_llm if usar_llm else None,
            )

        # --- resposta fundamentada
        st.subheader("Resposta")
        if resposta.abstencao:
            st.warning(resposta.texto)
        else:
            st.markdown(resposta.texto)

        col1, col2, col3 = st.columns(3)
        col1.metric("Confiança da recuperação", f"{resultado.confianca:.0%}")
        col2.metric("Fatos citados", f"{len(resposta.citados)} de {len(resposta.fatos)}")
        col3.metric("Fundamentação", "verificada" if resposta.fundamentada else "não verificada")
        if resposta.citacoes_invalidas:
            st.error(
                "O LLM citou fatos que não existem no contexto e essas citações foram removidas: "
                + ", ".join(resposta.citacoes_invalidas)
            )
        st.caption(
            "A confiança indica a força dos sinais de recuperação (busca vetorial, Cypher e grafo); "
            "não é uma garantia de correção."
        )

        # --- fatos citados com evidência
        st.subheader("Evidências da resposta")
        if resposta.fatos_citados:
            for f in resposta.fatos_citados:
                with st.container(border=True):
                    st.markdown(f"**[{f['ref']}] {f['origem']} → {f['relacao']} → {f['destino']}**")
                    st.info(f.get("evidencia") or "Sem trecho disponível.")
                    fonte = f"[{f['documento']}]({f['url']})" if f.get("url") else "documento não identificado"
                    st.caption(
                        f"Fonte: {fonte} · Confiança da extração: {(f.get('confianca') or 0):.0%} · "
                        f"Chunks: {', '.join(f.get('chunk_ids') or [])}"
                    )
        else:
            st.info("Nenhum fato do grafo foi citado.")

        # --- subgrafo: as entidades e relações dos fatos usados na resposta
        st.subheader("Subgrafo utilizado")
        if resposta.fatos:
            citados = set(resposta.citados)
            grafo = graphviz.Digraph()
            grafo.attr(rankdir="LR")
            for f in resposta.fatos:
                grafo.node(f["origem_id"], label=f["origem"])
                grafo.node(f["destino_id"], label=f["destino"])
            for f in resposta.fatos:
                destaque = f["ref"] in citados
                grafo.edge(
                    f["origem_id"], f["destino_id"], label=f"{f['relacao']} [{f['ref']}]",
                    color="blue" if destaque else "gray", penwidth="2" if destaque else "1",
                    fontcolor="blue" if destaque else "gray",
                )
            st.graphviz_chart(grafo, width="stretch")
            st.caption("Fatos enviados ao LLM. Em azul, os citados na resposta.")
        else:
            st.info("Nenhum fato foi recuperado para esta pergunta.")

        # --- detalhes
        with st.expander("Todos os fatos enviados ao LLM"):
            if resposta.fatos:
                st.dataframe(
                    [{k: f.get(k) for k in ("ref", "origem", "relacao", "destino", "similaridade", "confianca", "documento")}
                     for f in resposta.fatos],
                    width="stretch",
                )
            else:
                st.info("Nenhum fato encontrado no subgrafo.")
        with st.expander("Entidades encontradas pela busca híbrida"):
            st.dataframe(
                [n for n in (resultado.nos or []) if n.get("tipo") not in {"Chunk", "Documento"}], width="stretch"
            )
        with st.expander("Consulta Cypher gerada"):
            st.code(resultado.cypher, language="cypher")

    except Exception as erro:
        st.error(f"Erro ao consultar o grafo: {erro}")
    finally:
        if executor is not None:
            executor.fechar()
