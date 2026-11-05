"""
Divisão dos textos da Wikipédia em chunks (Pessoa 2).

Estratégia (docs/formato_extracao.md):
- O texto é quebrado em parágrafos (linhas). Os títulos de seção ("== Carreira ==")
  viram o início de um bloco novo e ficam no chunk como contexto, sem os sinais "==".
- Parágrafos são juntados até 1.400 caracteres (MAX_CARACTERES); com a frase de
  sobreposição o chunk fica em ~1.500 caracteres.
- Parágrafos maiores que o limite são quebrados em frases.
- Entre dois chunks vizinhos repete-se a última frase do anterior (sobreposição de
  uma frase), para que uma relação que cruza a fronteira não se perca.
- Cada chunk precisa caber em 512 tokens do multilingual-e5-base. Se o tokenizer
  estiver instalado, a contagem é exata; senão, usa-se a estimativa conservadora
  de 1 token a cada 3,5 caracteres (o E5 dá ~4 caracteres/token em português).

Uso:
    from src.chunking import dividir_em_chunks
    textos = dividir_em_chunks(texto)   # -> lista de strings
"""

import re

MAX_CARACTERES = 1400     # alvo do agrupamento; com a frase de sobreposição fica em ~1.500-1.700
MAX_SOBREPOSICAO = 300
MIN_CARACTERES = 300      # chunk menor que isso é juntado ao vizinho, se couber
MAX_TOKENS = 512
MODELO_EMBEDDING = "intfloat/multilingual-e5-base"

_RE_TITULO = re.compile(r"^\s*(=+)\s*(.*?)\s*\1\s*$")
# Fim de frase: . ! ? seguido de espaço e letra maiúscula/dígito/aspas.
# Evita quebrar em abreviações comuns ("c.", "Dr.", "Sr.", iniciais "C.").
_RE_FRASE = re.compile(r"(?<!\b[A-Z])(?<!\bc)(?<!\bDr)(?<!\bSr)(?<!\bSt)(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"“(])")

_tokenizer = None
_tokenizer_carregado = False


def _contar_tokens(texto: str) -> int:
    """Conta tokens com o tokenizer do E5 se disponível; senão estima (conservador)."""
    global _tokenizer, _tokenizer_carregado
    if not _tokenizer_carregado:
        _tokenizer_carregado = True
        try:
            from transformers import AutoTokenizer
            _tokenizer = AutoTokenizer.from_pretrained(MODELO_EMBEDDING)
        except Exception:
            _tokenizer = None
    if _tokenizer is not None:
        return len(_tokenizer("passage: " + texto)["input_ids"])
    return int(len(texto) / 3.5) + 4


def metodo_contagem() -> str:
    _contar_tokens("")
    return "tokenizer E5" if _tokenizer is not None else "estimativa (caracteres/3,5)"


def dividir_frases(paragrafo: str) -> list[str]:
    return [f.strip() for f in _RE_FRASE.split(paragrafo) if f.strip()]


def _blocos(texto: str) -> list[str]:
    """Transforma o texto em parágrafos; o título de seção é colado ao parágrafo seguinte."""
    blocos: list[str] = []
    titulo_pendente = None
    for linha in texto.split("\n"):
        linha = linha.strip()
        if not linha:
            continue
        m = _RE_TITULO.match(linha)
        if m:
            titulo_pendente = m.group(2)
            blocos.append(None)  # marca de "começa seção nova"
            continue
        if titulo_pendente:
            linha = f"{titulo_pendente}: {linha}" if len(titulo_pendente) < 80 else linha
            titulo_pendente = None
        blocos.append(linha)
    return blocos


def _quebrar_grande(paragrafo: str) -> list[str]:
    """Quebra um parágrafo maior que o limite em pedaços de frases inteiras."""
    pedacos, atual = [], ""
    for frase in dividir_frases(paragrafo):
        while len(frase) > MAX_CARACTERES:          # frase gigante (raro): corta em espaços
            corte = frase.rfind(" ", 0, MAX_CARACTERES)
            corte = corte if corte > 0 else MAX_CARACTERES
            if atual:
                pedacos.append(atual)
                atual = ""
            pedacos.append(frase[:corte].strip())
            frase = frase[corte:].strip()
        if atual and len(atual) + 1 + len(frase) > MAX_CARACTERES:
            pedacos.append(atual)
            atual = frase
        else:
            atual = f"{atual} {frase}".strip()
    if atual:
        pedacos.append(atual)
    return pedacos


def dividir_em_chunks(texto: str) -> list[str]:
    # 1) parágrafos (com quebra dos muito grandes) e marcas de seção
    unidades: list[str | None] = []
    for b in _blocos(texto):
        if b is None:
            unidades.append(None)
        elif len(b) > MAX_CARACTERES:
            unidades.extend(_quebrar_grande(b))
        else:
            unidades.append(b)

    # 2) junta parágrafos até o limite; seção nova fecha o chunk se ele já for razoável
    grupos: list[list[str]] = []
    atual: list[str] = []
    tamanho = 0
    for u in unidades:
        if u is None:
            if tamanho >= MIN_CARACTERES:
                grupos.append(atual)
                atual, tamanho = [], 0
            continue
        if atual and tamanho + 1 + len(u) > MAX_CARACTERES:
            grupos.append(atual)
            atual, tamanho = [], 0
        atual.append(u)
        tamanho += len(u) + 1
    if atual:
        # último pedaço muito pequeno: junta ao anterior se couber
        if grupos and tamanho < MIN_CARACTERES and sum(len(x) + 1 for x in grupos[-1]) + tamanho <= MAX_CARACTERES:
            grupos[-1].extend(atual)
        else:
            grupos.append(atual)

    textos = ["\n".join(g) for g in grupos if g]

    # 3) sobreposição: a última frase do chunk anterior abre o próximo
    resultado: list[str] = []
    for i, t in enumerate(textos):
        if i > 0:
            frases_ant = dividir_frases(textos[i - 1].split("\n")[-1])
            ultima = frases_ant[-1] if frases_ant else ""
            if ultima and len(ultima) <= MAX_SOBREPOSICAO:
                t = f"{ultima}\n{t}"
        resultado.append(t)

    # 4) garantia do limite de tokens do modelo de embedding
    final: list[str] = []
    for t in resultado:
        if _contar_tokens(t) <= MAX_TOKENS:
            final.append(t)
        else:  # sem a sobreposição costuma caber; senão, divide ao meio por frases
            frases = dividir_frases(t.replace("\n", " "))
            meio = len(frases) // 2 or 1
            final.extend([" ".join(frases[:meio]), " ".join(frases[meio:])])
    return [t for t in final if t.strip()]
