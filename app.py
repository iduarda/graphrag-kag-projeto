
import streamlit as st
import graphviz

from src.ingestao import conectar
from src.recuperacao import executar_busca_hibrida
from src.algoritmos_grafo import buscar_evidencias_relacao


st.set_page_config(
    page_title="GraphRAG + KAG",
    page_icon="🔎",
    layout="wide"
)

RELACOES_SEMANTICAS = {
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
}


st.title("GraphRAG + KAG")
st.subheader("Exploração inteligente de conhecimento")

st.write(
    "Faça perguntas sobre a base de conhecimento "
    "e explore as entidades, relações e evidências do grafo."
)

pergunta = st.text_input(
    "Digite sua pergunta:",
    placeholder="Ex.: Quais computadores utilizaram o Intel 8080?"
)

if st.button("Buscar"):
    if not pergunta.strip():
        st.warning("Digite uma pergunta antes de buscar.")

    else:
        executor = None

        try:
            with st.spinner("Consultando o grafo de conhecimento..."):
                executor = conectar()
                resultado = executar_busca_hibrida(
                    executor,
                    pergunta
                )

            st.subheader("Resposta")
            st.write(resultado.resposta)

            st.metric(
                "Índice de confiança calculado",
                f"{resultado.confianca:.0%}"
            )

            st.caption(
                "Indicador interno da recuperação; não representa "
                "uma garantia de correção da resposta."
            )

            nos = resultado.nos or []
            arestas = resultado.arestas or []

            evidencias_relacoes = []

            entidades = [
                n for n in nos
                if n.get("tipo") not in {"Chunk", "Documento"}
            ]

            evidencias = [
                n for n in nos
                if n.get("tipo") in {"Chunk", "Documento"}
            ]

            for origem in entidades:
                for destino in entidades:
                    if origem["id"] != destino["id"]:
                        encontradas = buscar_evidencias_relacao(
                            executor,
                            origem["nome"],
                            destino["nome"]
                        )
                        evidencias_relacoes.extend(encontradas)

            relacoes_semanticas = [
                a for a in arestas
                if a.get("tipo") in RELACOES_SEMANTICAS
            ]

            outras_relacoes = [
                a for a in arestas
                if a.get("tipo") not in RELACOES_SEMANTICAS
            ]

            st.subheader("Visualização do subgrafo")

            if nos:
                grafo = graphviz.Digraph()
                grafo.attr(rankdir="LR")

                nos_visiveis = nos[:30]
                ids_visiveis = set()
                ids_por_nome = {}

                for no in nos_visiveis:
                    identificador = str(no["id"])
                    ids_visiveis.add(identificador)

                    if no.get("nome"):
                        ids_por_nome[no["nome"]] = identificador

                    grafo.node(
                        identificador,
                        label=(
                            f"{no.get('nome') or identificador}\n"
                            f"[{no.get('tipo') or 'Entidade'}]"
                        )
                    )

                for aresta in arestas[:40]:
                    origem = str(aresta.get("de", ""))
                    destino = str(aresta.get("para", ""))

                    if origem in ids_visiveis and destino in ids_visiveis:
                        grafo.edge(
                            origem,
                            destino,
                            label=str(aresta.get("tipo", ""))
                        )

                for evidencia in evidencias_relacoes:
                    origem = ids_por_nome.get(evidencia["origem"])
                    destino = ids_por_nome.get(evidencia["destino"])

                    if origem and destino:
                        grafo.edge(
                            origem,
                            destino,
                            label=evidencia["relacao"],
                            color="blue"
                        )

                st.graphviz_chart(
                    grafo,
                    use_container_width=True
                )
            else:
                st.info("Não há nós suficientes para desenhar o subgrafo.")

            st.subheader("Entidades encontradas")

            if entidades:
                st.dataframe(entidades, use_container_width=True)
            else:
                st.info("Nenhuma entidade encontrada.")

            st.subheader("Relações semânticas")

            if relacoes_semanticas:
                st.dataframe(
                    relacoes_semanticas,
                    use_container_width=True
                )
            else:
                st.info("Nenhuma relação semântica encontrada.")

            with st.expander("Evidências das relações semânticas", expanded=True):
                if evidencias_relacoes:
                    for evidencia in evidencias_relacoes:
                        st.markdown(
                            f"### {evidencia['origem']} → "
                            f"{evidencia['relacao']} → "
                            f"{evidencia['destino']}"
                        )

                        st.info(
                            evidencia.get("evidencia")
                            or "Sem descrição disponível."
                        )

                        col1, col2 = st.columns(2)

                        with col1:
                            st.metric(
                                "Confiança da evidência",
                                f"{evidencia.get('confianca', 0):.0%}"
                            )

                        with col2:
                            st.caption(
                                "Chunks: "
                                + ", ".join(
                                    evidencia.get("chunk_ids") or []
                                )
                            )

                        st.divider()
                else:
                    st.info("Nenhuma evidência direta encontrada.")

            with st.expander("Documentos e chunks utilizados como evidência"):
                if evidencias:
                    st.dataframe(evidencias, use_container_width=True)
                else:
                    st.info("Nenhuma evidência documental encontrada.")

            with st.expander("Outras relações do grafo"):
                if outras_relacoes:
                    st.dataframe(
                        outras_relacoes,
                        use_container_width=True
                    )
                else:
                    st.info("Nenhuma outra relação encontrada.")

            with st.expander("Consultar código Cypher"):
                st.code(resultado.cypher, language="cypher")

        except Exception as erro:
            st.error(f"Erro ao consultar o grafo: {erro}")

        finally:
            if executor is not None:
                executor.fechar()
