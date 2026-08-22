"""Связь локальных descriptor лиц с сущностями Person Home Assistant."""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_FACE_ENCODING,
    CONF_FACE_ENGINE,
    CONF_FACE_NAME,
    CONF_FACE_PERSON_ENTITY_ID,
    FACE_ENGINE_PORTABLE_V1,
)


@dataclass(slots=True)
class KnownFace:
    """Локальный descriptor лица и необязательная ссылка на Person Home Assistant."""

    name: str
    encoding: list[float] = field(default_factory=list)
    engine: str = FACE_ENGINE_PORTABLE_V1
    person_entity_id: str | None = None

    @property
    def identity_key(self) -> str:
        """Стабильный runtime-ключ, не зависящий от friendly_name Person."""

        if self.person_entity_id:
            return f"person:{self.person_entity_id}"
        return f"legacy:{self.name}"

    def as_dict(self) -> dict[str, object]:
        """Сериализовать descriptor без изменения старого формата хранения."""

        payload: dict[str, object] = {
            CONF_FACE_NAME: self.name,
            CONF_FACE_ENCODING: list(self.encoding),
            CONF_FACE_ENGINE: self.engine,
        }
        if self.person_entity_id:
            payload[CONF_FACE_PERSON_ENTITY_ID] = self.person_entity_id
        return payload


def normalize_person_entity_id(
    hass: HomeAssistant, person_entity_id: str | None
) -> str | None:
    """Проверить, что выбранная сущность существует и относится к домену person."""

    if person_entity_id is None:
        return None
    normalized = str(person_entity_id).strip()
    if not normalized:
        return None
    if not normalized.startswith("person."):
        raise HomeAssistantError("Выбранная сущность не является Person Home Assistant")
    if hass.states.get(normalized) is None:
        raise HomeAssistantError("Выбранный человек Home Assistant больше не существует")
    return normalized


def resolve_person_name(
    hass: HomeAssistant,
    person_entity_id: str | None,
    *,
    fallback: str,
) -> str:
    """Получить актуальное friendly_name Person, сохранив fallback для legacy-записей."""

    if person_entity_id:
        state = hass.states.get(person_entity_id)
        if state is not None:
            friendly_name = state.attributes.get("friendly_name")
            if isinstance(friendly_name, str) and friendly_name.strip():
                return friendly_name.strip()
    return (fallback or "").strip()


def face_display_name(hass: HomeAssistant, face: KnownFace) -> str:
    """Вернуть актуальное отображаемое имя лица."""

    return resolve_person_name(
        hass,
        face.person_entity_id,
        fallback=face.name,
    )
