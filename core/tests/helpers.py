from typing import Any


def get_response_results(response) -> list[dict[str, Any]]:
    """
    Devuelve los resultados de una respuesta paginada o una lista directa.

    Respuesta paginada:
        {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [...]
        }

    Respuesta sin paginación:
        [...]
    """
    data = response.data

    if isinstance(data, dict) and "results" in data:
        return data["results"]

    if isinstance(data, list):
        return data

    raise AssertionError(
        "La respuesta no contiene una lista ni una propiedad 'results'."
    )


def get_public_ids(response) -> set[str]:
    """
    Obtiene los public_id retornados por un endpoint de listado.
    """
    return {
        str(item["public_id"])
        for item in get_response_results(response)
    }