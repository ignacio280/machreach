from student.routes import _PERIOD_POPUP_HTML


def test_period_results_popup_has_complete_spanish_runtime_copy():
    assert "document.documentElement.lang" in _PERIOD_POPUP_HTML
    for text in (
        "Resultados semanales",
        "Resultados mensuales",
        "Ranking de la semana pasada",
        "Ranking del mes pasado",
        "El período",
        "Sin clasificación",
        "Sin XP en este período",
        "País",
        "Universidad",
        "Carrera",
        "jugadores",
        "Ganaste",
        "monedas",
        "Perfecto, entendido",
    ):
        assert text in _PERIOD_POPUP_HTML
