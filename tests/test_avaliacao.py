from src.avaliacao import avaliar_relacoes


def test_avaliar_relacoes_com_base_real():
    resultado = avaliar_relacoes()

    assert resultado["base_consistente"] is True
    assert resultado["total_amostra"] == 150
    assert resultado["total_julgamentos"] == 150

    assert resultado["corretas"] == 141
    assert resultado["parciais"] == 8
    assert resultado["incorretas"] == 1

    assert resultado["percentual_corretas"] == 94.0
    assert resultado["percentual_parciais"] == 5.33
    assert resultado["percentual_incorretas"] == 0.67
    assert resultado["cobertura"] == 100.0