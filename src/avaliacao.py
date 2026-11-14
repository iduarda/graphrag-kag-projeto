
import json
from pathlib import Path
from collections import Counter


RAIZ = Path(__file__).resolve().parent.parent
PASTA_AVALIACAO = RAIZ / "data" / "avaliacao"

VEREDITOS_VALIDOS = {"correta", "parcial", "incorreta"}


def avaliar_relacoes():
    caminho_amostra = PASTA_AVALIACAO / "amostra_relacoes.json"
    caminho_julgamentos = PASTA_AVALIACAO / "julgamento_relacoes.json"

    with open(caminho_amostra, encoding="utf-8") as arquivo:
        amostra = json.load(arquivo)

    with open(caminho_julgamentos, encoding="utf-8") as arquivo:
        julgamentos = json.load(arquivo)

    numeros_amostra = [item["n"] for item in amostra]
    numeros_julgamentos = [item["n"] for item in julgamentos]

    duplicados_amostra = [
        n for n, quantidade in Counter(numeros_amostra).items()
        if quantidade > 1
    ]

    duplicados_julgamentos = [
        n for n, quantidade in Counter(numeros_julgamentos).items()
        if quantidade > 1
    ]

    sem_julgamento = sorted(set(numeros_amostra) - set(numeros_julgamentos))
    sem_relacao = sorted(set(numeros_julgamentos) - set(numeros_amostra))

    invalidos = [
        item["n"] for item in julgamentos
        if item.get("veredito", "").strip().lower() not in VEREDITOS_VALIDOS
    ]

    if duplicados_amostra or duplicados_julgamentos or sem_julgamento or sem_relacao or invalidos:
        raise ValueError(
            "Base de avaliação inconsistente: "
            f"duplicados na amostra={duplicados_amostra}; "
            f"duplicados nos julgamentos={duplicados_julgamentos}; "
            f"sem julgamento={sem_julgamento}; "
            f"sem relação={sem_relacao}; "
            f"vereditos inválidos={invalidos}"
        )

    total_amostra = len(amostra)
    total_julgamentos = len(julgamentos)

    vereditos = Counter(
        item["veredito"].strip().lower()
        for item in julgamentos
    )

    corretas = vereditos["correta"]
    parciais = vereditos["parcial"]
    incorretas = vereditos["incorreta"]

    return {
        "base_consistente": True,
        "total_amostra": total_amostra,
        "total_julgamentos": total_julgamentos,
        "corretas": corretas,
        "parciais": parciais,
        "incorretas": incorretas,
        "percentual_corretas": round(corretas / total_julgamentos * 100, 2) if total_julgamentos else 0,
        "percentual_parciais": round(parciais / total_julgamentos * 100, 2) if total_julgamentos else 0,
        "percentual_incorretas": round(incorretas / total_julgamentos * 100, 2) if total_julgamentos else 0,
        "cobertura": round(total_julgamentos / total_amostra * 100, 2) if total_amostra else 0,
    }
