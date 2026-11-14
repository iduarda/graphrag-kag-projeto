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

import html

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

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


CORES_TIPO = {
    "pessoa": "#4C78A8", "organizacao": "#F58518", "linguagem": "#54A24B", "hardware": "#B279A2",
    "tecnologia": "#E45756", "premio": "#EECA3B", "local": "#9D755D",
}


def desenhar_subgrafo(fatos: list[dict], citados: set[str]) -> str:
    """HTML interativo (vis.js via pyvis) do subgrafo de fatos enviados ao LLM."""
    rede = Network(height="540px", width="100%", directed=True, cdn_resources="in_line", bgcolor="#ffffff")
    rede.barnes_hut(gravity=-6000, spring_length=160)
    for f in fatos:
        for nid, nome in ((f["origem_id"], f["origem"]), (f["destino_id"], f["destino"])):
            tipo = nid.split(":", 1)[0]
            rede.add_node(nid, label=nome, title=f"{nome} ({tipo})", color=CORES_TIPO.get(tipo, "#888888"))
    for f in fatos:
        citado = f["ref"] in citados
        rede.add_edge(
            f["origem_id"], f["destino_id"],
            label=f"{f['relacao']} [{f['ref']}]",
            title=html.escape(f"[{f['ref']}] {f.get('evidencia') or ''}"),
            color="#1f5fd1" if citado else "#bbbbbb",
            width=3 if citado else 1,
            dashes=bool(f.get("caminho")),
            font={"size": 13, "color": "#1f5fd1" if citado else "#777777", "strokeWidth": 4, "strokeColor": "#ffffff"},
        )
    return rede.generate_html(notebook=False)


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

        # --- subgrafo interativo: as entidades e relações dos fatos usados na resposta
        st.subheader("Subgrafo utilizado")
        if resposta.fatos:
            st.caption(
                "Arraste os nós e use a roda do mouse para dar zoom. Passe o mouse sobre uma relação para ver a "
                "evidência. Em azul, as relações citadas na resposta; tracejadas, as que vieram do menor caminho "
                "entre as entidades da pergunta."
            )
            html_subgrafo = desenhar_subgrafo(resposta.fatos, set(resposta.citados))
            if hasattr(st, "iframe"):  # Streamlit novo
                st.iframe(html_subgrafo, height=560)
            else:  # versões antigas
                components.html(html_subgrafo, height=560, scrolling=False)
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
