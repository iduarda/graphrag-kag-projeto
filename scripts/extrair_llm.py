"""
Extração de entidades e relações com um LLM, chunk a chunk (etapa 2, Pessoa 2).

Lê data/chunks/<arquivo>.json, envia cada chunk ao LLM com o prompt de
prompts/extracao.md e grava as respostas brutas em data/extracoes_brutas/<arquivo>.json.
Depois rode scripts/montar_extracao.py para gerar os JSONs finais validados.

Provedores suportados (escolha com --provedor):
  anthropic  -> API da Anthropic (variável ANTHROPIC_API_KEY)         pip install anthropic
  openai     -> qualquer API compatível com OpenAI: OpenAI, Google Gemini, Groq,
                Ollama local etc. (OPENAI_API_KEY e, se preciso, OPENAI_BASE_URL)  pip install openai
                Ex.: Gemini  -> OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
                     Ollama  -> OPENAI_BASE_URL=http://localhost:11434/v1  (OPENAI_API_KEY=ollama)

Uso (na raiz do repositório):
    python scripts/extrair_llm.py --provedor anthropic --modelo claude-sonnet-5-5
    python scripts/extrair_llm.py --provedor openai --modelo gemini-2.5-flash Dennis_Ritchie.json Unix.json
    python scripts/extrair_llm.py --imprimir-prompt Dennis_Ritchie.json     # só mostra o prompt

O script pula documentos já extraídos (use --forcar para refazer).
Temperatura 0 para a extração ser o mais reprodutível possível.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.montagem import Canonicas  # noqa: E402

PASTA_CHUNKS = RAIZ / "data" / "chunks"
PASTA_BRUTAS = RAIZ / "data" / "extracoes_brutas"
ARQUIVO_PROMPT = RAIZ / "prompts" / "extracao.md"


def montar_prompt(nome_arquivo: str, documento: dict, canon: Canonicas) -> str:
    principal = canon.por_arquivo.get(nome_arquivo, {"nome": documento["titulo"], "tipo": "?"})
    return (ARQUIVO_PROMPT.read_text(encoding="utf-8")
            .replace("{TITULO}", documento["titulo"])
            .replace("{ENTIDADE_PRINCIPAL}", principal["nome"])
            .replace("{TIPO_PRINCIPAL}", principal["tipo"])
            .replace("{LISTA_CANONICA}", canon.texto_para_prompt()))


def extrair_json(resposta: str) -> dict:
    """Tira o objeto JSON da resposta (tolera ```json ... ``` e texto em volta)."""
    texto = resposta.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim < 0:
        raise ValueError("resposta sem JSON")
    return json.loads(texto[inicio:fim + 1])


class Cliente:
    def __init__(self, provedor: str, modelo: str):
        self.provedor, self.modelo = provedor, modelo
        if provedor == "anthropic":
            import anthropic
            self.api = anthropic.Anthropic()
        else:
            import openai
            self.api = openai.OpenAI()

    def perguntar(self, sistema: str, usuario: str) -> str:
        if self.provedor == "anthropic":
            r = self.api.messages.create(
                model=self.modelo, max_tokens=4096, temperature=0,
                system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": usuario}])
            return r.content[0].text
        r = self.api.chat.completions.create(
            model=self.modelo, temperature=0,
            messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}])
        return r.choices[0].message.content


def extrair_documento(arquivo: Path, cliente: Cliente, canon: Canonicas, pausa: float) -> dict:
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    sistema = montar_prompt(arquivo.name, dados["documento"], canon)
    respostas = []
    for chunk in dados["chunks"]:
        usuario = f"chunk_id: {chunk['id']}\n\nTrecho:\n\"\"\"\n{chunk['texto']}\n\"\"\""
        for tentativa in range(3):
            try:
                resp = extrair_json(cliente.perguntar(sistema, usuario))
                break
            except Exception as e:  # JSON inválido ou erro de rede: tenta de novo
                print(f"   ! {chunk['id']} tentativa {tentativa + 1}: {e}")
                time.sleep(2 * (tentativa + 1))
        else:
            resp = {"entidades": [], "relacoes": [], "erro": "falhou 3 vezes"}
        resp["chunk_id"] = chunk["id"]
        respostas.append(resp)
        time.sleep(pausa)
    return {"modelo_llm": cliente.modelo, "data": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chunks": respostas}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("arquivos", nargs="*", help="nomes em data/chunks/ (padrão: todos)")
    p.add_argument("--provedor", choices=["anthropic", "openai"], default="anthropic")
    p.add_argument("--modelo", default="claude-sonnet-5-5")
    p.add_argument("--pausa", type=float, default=0.0, help="segundos entre chamadas (limites de free tier)")
    p.add_argument("--forcar", action="store_true")
    p.add_argument("--imprimir-prompt", metavar="ARQUIVO")
    a = p.parse_args()

    canon = Canonicas()
    if a.imprimir_prompt:
        dados = json.loads((PASTA_CHUNKS / a.imprimir_prompt).read_text(encoding="utf-8"))
        print(montar_prompt(a.imprimir_prompt, dados["documento"], canon))
        return

    PASTA_BRUTAS.mkdir(parents=True, exist_ok=True)
    arquivos = [PASTA_CHUNKS / n for n in a.arquivos] if a.arquivos else sorted(PASTA_CHUNKS.glob("*.json"))
    cliente = Cliente(a.provedor, a.modelo)
    for arq in arquivos:
        destino = PASTA_BRUTAS / arq.name
        if destino.exists() and not a.forcar:
            print(f"[pulado] {arq.name}")
            continue
        print(f"[extraindo] {arq.name}")
        bruto = extrair_documento(arq, cliente, canon, a.pausa)
        destino.write_text(json.dumps(bruto, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
