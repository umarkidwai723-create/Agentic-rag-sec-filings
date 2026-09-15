import streamlit as st

from src.agent.graph import run_query


st.set_page_config(
    page_title="Agentic RAG for SEC Financial Filings",
    page_icon="📊",
    layout="wide"
)


st.title("📊 Agentic RAG for SEC Financial Filings")
st.write(
    "Ask questions about Tesla, Ford, and Rivian SEC 10-K filings."
)

st.divider()


query = st.text_input(
    "Ask a financial question",
    placeholder="e.g. What was Tesla's revenue in 2024?"
)


if st.button("Ask Question", type="primary"):

    if not query.strip():
        st.warning("Please enter a question.")
    else:
        with st.spinner("Analyzing your question..."):

            try:
                result = run_query(query)

                st.subheader("Answer")

                answer = result.get("final_answer", "")
                st.write(answer)

                st.divider()

                # System information
                col1, col2, col3 = st.columns(3)

                with col1:
                    route = result.get("route", "unknown")
                    st.metric("Route", route.upper())

                with col2:
                    validated = result.get("validated", None)

                    if validated is True:
                        st.metric("Validated", "✓ Yes")
                    elif validated is False:
                        st.metric("Validated", "✗ No")
                    else:
                        st.metric("Validated", "N/A")

                with col3:
                    st.metric(
                        "Query Status",
                        "Success"
                    )

                # Validation details
                validation_note = result.get(
                    "validation_note",
                    ""
                )

                if validation_note:
                    st.info(
                        f"Validation: {validation_note}"
                    )

                # Show SQL when numeric query is used
                sql = result.get("sql", "")

                if sql:
                    with st.expander("View generated SQL"):
                        st.code(sql, language="sql")

            except Exception as e:
                st.error(
                    f"Something went wrong: {str(e)}"
                )


st.divider()

st.caption(
    "Powered by LangGraph • ChromaDB • SQL Server • SEC EDGAR"
)
