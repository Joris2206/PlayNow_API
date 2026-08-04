from typing import Any


def get_response_results(response) -> list[dict[str, Any]]:
    data = response.data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    raise AssertionError("La respuesta no contiene una lista ni 'results'.")


def get_public_ids(response) -> set[str]:
    return {str(item["public_id"]) for item in get_response_results(response)}
