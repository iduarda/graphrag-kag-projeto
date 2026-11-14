"""
Testa a conexão com o LLM configurado no .env e mostra o motivo completo de qualquer erro.

Uso (na raiz do repositório):
    python scripts/testar_llm.py
"""

import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.ingestao import carregar_env  # noqa: E402


def mascarar(chave: str | None) -> str:
    if not chave:
        return "(vazia)"
    return f"{chave[:4]}...{chave[-2:]} ({len(chave)} caracteres)"


def main() -> None:
    carregar_env()
    provedor = (os.getenv("GRAPHRAG_LLM_PROVIDER") or "openai").lower()
    modelo = os.getenv("GRAPHRAG_LLM_MODEL")
    print(f"Provedor: {provedor}")
    print(f"Modelo:   {modelo}")
    if provedor == "anthropic":
        print(f"Chave:    {mascarar(os.getenv('ANTHROPIC_API_KEY'))}")
    else:
        chave = os.getenv("OPENAI_API_KEY")
        print(f"Chave:    {mascarar(chave)}")
        print(f"Base URL: {os.getenv('OPENAI_BASE_URL')}")
        if chave and chave != chave.strip():
            print("  ! A chave tem espaços no começo ou no fim.")

    if provedor == "anthropic":
        import anthropic

        api = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        testes = {"simples": dict(model=modelo, max_tokens=50, messages=[{"role": "user", "content": "Diga oi."}])}
        chamar = api.messages.create
    else:
        import openai

        api = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_BASE_URL") or None,
                            timeout=180, max_retries=0)
        base = dict(model=modelo, messages=[{"role": "user", "content": "Diga oi."}])
        # Pedido do tamanho real da avaliação: ~5 trechos de texto + regras de citação.
        trecho = ("Dennis Ritchie trabalhou no Bell Labs, onde criou a linguagem C a partir da linguagem B, "
                  "criada por Ken Thompson. O Unix foi reescrito em C e executado no PDP-11. ") * 12
        longo = [{"role": "system", "content": "Responda usando só os trechos e cite [C1] a [C5] em cada frase."},
                 {"role": "user", "content": "\n\n".join(f"[C{i}] {trecho}" for i in range(1, 6))
                  + "\n\nPergunta: Quem criou a linguagem que influenciou a C?"}]
        testes = {
            "simples": base,
            "com temperature=0": {**base, "temperature": 0},
            "com max_tokens=4096": {**base, "max_tokens": 4096},
            "com mensagem de sistema": {**base, "messages": [{"role": "system", "content": "Responda curto."},
                                                             {"role": "user", "content": "Diga oi."}]},
            "LONGO, raciocínio padrão": {**base, "messages": longo, "temperature": 0, "max_tokens": 4096},
            "LONGO, reasoning_effort=low": {**base, "messages": longo, "temperature": 0, "max_tokens": 4096,
                                            "reasoning_effort": "low"},
            "LONGO, reasoning_effort=none": {**base, "messages": longo, "temperature": 0, "max_tokens": 4096,
                                             "reasoning_effort": "none"},
        }
        chamar = api.chat.completions.create

    print()
    for nome, params in testes.items():
        print(f"... {nome}", flush=True)
        inicio = time.time()
        try:
            r = chamar(**params)
            texto = r.content[0].text if hasattr(r, "content") else r.choices[0].message.content
            print(f"[ok]   {nome} ({time.time() - inicio:.1f} s): {(texto or '')[:120]!r}")
        except Exception as erro:
            corpo = getattr(getattr(erro, "response", None), "text", "")
            print(f"[ERRO] {nome} ({time.time() - inicio:.1f} s): {erro}")
            if corpo:
                print(f"       resposta do servidor: {corpo[:600]}")


if __name__ == "__main__":
    main()
