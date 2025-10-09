"""Тесты схемы сервиса добавления лиц."""
from __future__ import annotations

import pytest
pytest.importorskip("voluptuous", reason="Схема сервиса использует voluptuous для валидации")

from custom_components.intersvyaz import (
    ADD_KNOWN_FACE_SCHEMA,
    VolInvalid,
    _validate_add_face_payload,
)


def test_add_known_face_schema_allows_single_face() -> None:
    """При передаче одиночного лица схема должна пропускать данные."""

    payload = {
        "entry_id": "entry",
        "name": "Гость",
        "image_url": "https://example.com/guest.jpg",
    }
    validated = _validate_add_face_payload(ADD_KNOWN_FACE_SCHEMA(payload))
    assert validated["name"] == "Гость"


def test_add_known_face_schema_allows_batch_faces() -> None:
    """Список faces должен успешно проходить проверку."""

    payload = {
        "entry_id": "entry",
        "faces": [
            {"name": "Гость", "image_base64": "aGVsbG8="},
            {"name": "Друг", "image_url": "https://example.com/friend.jpg"},
        ],
    }
    validated = _validate_add_face_payload(ADD_KNOWN_FACE_SCHEMA(payload))
    assert len(validated["faces"]) == 2


def test_add_known_face_schema_rejects_mixed_modes() -> None:
    """Одновременное указание faces и одиночных полей должно блокироваться."""

    payload = {
        "entry_id": "entry",
        "name": "Гость",
        "faces": [{"name": "Друг", "image_base64": "aGVsbG8="}],
    }
    with pytest.raises(VolInvalid):
        _validate_add_face_payload(ADD_KNOWN_FACE_SCHEMA(payload))


def test_add_known_face_schema_requires_image_source() -> None:
    """Для каждого лица необходимо указать ровно один источник изображения."""

    payload = {"entry_id": "entry", "name": "Гость"}
    with pytest.raises(VolInvalid):
        _validate_add_face_payload(ADD_KNOWN_FACE_SCHEMA(payload))

    payload_batch = {
        "entry_id": "entry",
        "faces": [{"name": "Друг"}],
    }
    with pytest.raises(VolInvalid):
        _validate_add_face_payload(ADD_KNOWN_FACE_SCHEMA(payload_batch))
