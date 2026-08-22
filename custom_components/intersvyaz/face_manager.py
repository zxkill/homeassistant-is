"""Управление локальным распознаванием лиц и автооткрытием домофона."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, List, Optional, Sequence

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_FACE_ENCODING,
    CONF_FACE_NAME,
    CONF_KNOWN_FACES,
    DATA_FACE_MANAGER,
    DOMAIN,
    FACE_EVENT_COOLDOWN_SECONDS,
    FACE_RECOGNITION_COOLDOWN_SECONDS,
    FACE_RECOGNITION_DISTANCE_THRESHOLD,
)
from .events import fire_face_recognized, fire_unknown_person
from .recognition import DlibFaceRecognitionEngine, FaceRecognitionResult

_LOGGER = logging.getLogger(f"{DOMAIN}.face_manager")


@dataclass
class KnownFace:
    """Данные известного лица, сохранённые в настройках интеграции."""

    name: str
    encoding: List[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[float] | str]:
        """Преобразовать структуру в словарь для сериализации."""

        return {CONF_FACE_NAME: self.name, CONF_FACE_ENCODING: list(self.encoding)}


class FaceRecognitionManager:
    """Хранит лица, анализирует кадры и управляет автооткрытием."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        match_threshold: float = FACE_RECOGNITION_DISTANCE_THRESHOLD,
        cooldown_seconds: float = FACE_RECOGNITION_COOLDOWN_SECONDS,
        event_cooldown_seconds: float = FACE_EVENT_COOLDOWN_SECONDS,
        engine: DlibFaceRecognitionEngine | None = None,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._match_threshold = float(match_threshold)
        self._cooldown_seconds = float(cooldown_seconds)
        self._event_cooldown_seconds = float(event_cooldown_seconds)
        self._engine = engine or DlibFaceRecognitionEngine()
        self._known_faces: list[KnownFace] = []
        self._door_cooldown: dict[str, float] = {}
        self._event_cooldown: dict[tuple[str, str], float] = {}
        self._last_frame_hash: dict[str, bytes] = {}
        self._lock = asyncio.Lock()
        self._load_known_faces_from_entry(entry.options.get(CONF_KNOWN_FACES, []))

    @property
    def library_available(self) -> bool:
        """Сообщить, доступны ли зависимости локального движка."""

        return self._engine.available

    def _load_known_faces_from_entry(self, stored: Iterable[dict[str, object]]) -> None:
        """Загрузить список известных лиц из опций записи."""

        self._known_faces.clear()
        for item in stored or []:
            if not isinstance(item, dict):
                continue
            name = item.get(CONF_FACE_NAME)
            encoding = item.get(CONF_FACE_ENCODING)
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(encoding, Iterable):
                continue
            try:
                vector = [float(value) for value in encoding]
            except (TypeError, ValueError):
                _LOGGER.warning("Пропущены повреждённые данные лица name=%s", name)
                continue
            if not vector:
                continue
            self._known_faces.append(KnownFace(name=name.strip(), encoding=vector))

        _LOGGER.info(
            "Локальная база лиц загружена: entry_id=%s count=%s engine_available=%s",
            self._entry.entry_id,
            len(self._known_faces),
            self.library_available,
        )

    def list_known_faces(self) -> list[KnownFace]:
        """Вернуть копию текущего списка известных лиц."""

        return list(self._known_faces)

    def list_known_face_names(self) -> list[str]:
        """Вернуть список имён известных лиц без раскрытия векторов."""

        return [face.name for face in self._known_faces]

    async def async_add_known_face(self, name: str, image_bytes: bytes) -> None:
        """Добавить или заменить лицо по фотографии."""

        normalized_name = (name or "").strip()
        if not normalized_name:
            raise HomeAssistantError("Имя лица не может быть пустым")
        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")
        if not self.library_available:
            raise HomeAssistantError(
                "Локальный движок распознавания лиц не загрузился. Проверьте журнал Home Assistant"
            )

        _LOGGER.info(
            "Начинаем регистрацию лица '%s': entry_id=%s bytes=%s",
            normalized_name,
            self._entry.entry_id,
            len(image_bytes),
        )
        encoding = await self._hass.async_add_executor_job(
            self._engine.extract_single_encoding,
            image_bytes,
        )

        async with self._lock:
            self._known_faces = [
                face for face in self._known_faces if face.name != normalized_name
            ]
            self._known_faces.append(
                KnownFace(name=normalized_name, encoding=list(encoding))
            )
            self._async_store_faces()

        _LOGGER.info(
            "Лицо '%s' зарегистрировано: descriptor_size=%s total_faces=%s",
            normalized_name,
            len(encoding),
            len(self._known_faces),
        )

    async def async_remove_known_face(self, name: str) -> None:
        """Удалить лицо из локальной базы."""

        async with self._lock:
            before = len(self._known_faces)
            self._known_faces = [face for face in self._known_faces if face.name != name]
            if len(self._known_faces) == before:
                raise HomeAssistantError(
                    f"Лицо с именем '{name}' не найдено в интеграции Intersvyaz"
                )
            self._async_store_faces()
        _LOGGER.info("Лицо '%s' удалено из локальной базы", name)

    async def async_process_image(
        self,
        door_uid: str,
        image_bytes: bytes,
        open_callback: Callable[[], Optional[Awaitable[None]]] | None,
    ) -> None:
        """Проанализировать кадр, создать событие и при совпадении открыть дверь."""

        if not self.library_available:
            _LOGGER.debug("Распознавание uid=%s пропущено: движок недоступен", door_uid)
            return
        if not self._known_faces:
            _LOGGER.debug("Распознавание uid=%s пропущено: локальная база лиц пуста", door_uid)
            return
        if not image_bytes:
            _LOGGER.debug("Распознавание uid=%s пропущено: пустой кадр", door_uid)
            return

        frame_hash = hashlib.sha256(image_bytes).digest()
        if self._last_frame_hash.get(door_uid) == frame_hash:
            _LOGGER.debug("Повторный идентичный кадр uid=%s пропущен", door_uid)
            return
        self._last_frame_hash[door_uid] = frame_hash

        known_faces: Sequence[tuple[str, Sequence[float]]] = [
            (face.name, face.encoding) for face in self._known_faces
        ]

        try:
            result = await self._hass.async_add_executor_job(
                self._engine.recognize,
                image_bytes,
                known_faces,
                self._match_threshold,
            )
        except HomeAssistantError as err:
            _LOGGER.error("Ошибка анализа кадра uid=%s: %s", door_uid, err)
            return

        await self._async_handle_result(door_uid, result, open_callback)

    async def _async_handle_result(
        self,
        door_uid: str,
        result: FaceRecognitionResult,
        open_callback: Callable[[], Optional[Awaitable[None]]] | None,
    ) -> None:
        """Обработать результат распознавания и защитные интервалы."""

        if result.faces_detected <= 0:
            _LOGGER.debug("В кадре uid=%s лица не обнаружены", door_uid)
            return

        if not result.matched or not result.matched_name:
            _LOGGER.debug(
                "В кадре uid=%s найден неизвестный посетитель: faces=%s best_distance=%s",
                door_uid,
                result.faces_detected,
                result.distance,
            )
            if self._allow_event(door_uid, "<unknown>"):
                fire_unknown_person(
                    self._hass,
                    door_uid=door_uid,
                    distance=result.distance,
                    threshold=self._match_threshold,
                    faces_detected=result.faces_detected,
                )
            return

        match_name = result.matched_name
        _LOGGER.info(
            "Распознано лицо '%s': uid=%s distance=%.4f threshold=%.4f faces=%s",
            match_name,
            door_uid,
            result.distance if result.distance is not None else -1.0,
            self._match_threshold,
            result.faces_detected,
        )

        if self._allow_event(door_uid, match_name):
            fire_face_recognized(
                self._hass,
                door_uid=door_uid,
                person=match_name,
                distance=result.distance,
                threshold=self._match_threshold,
                faces_detected=result.faces_detected,
            )

        now = time.monotonic()
        last_open = self._door_cooldown.get(door_uid, 0.0)
        if now - last_open < self._cooldown_seconds:
            _LOGGER.debug(
                "Автооткрытие uid=%s заблокировано cooldown: elapsed=%.1f required=%.1f",
                door_uid,
                now - last_open,
                self._cooldown_seconds,
            )
            return

        if not callable(open_callback):
            _LOGGER.warning(
                "Лицо '%s' распознано для uid=%s, но обработчик открытия отсутствует",
                match_name,
                door_uid,
            )
            return

        try:
            result_value = open_callback()
            if asyncio.iscoroutine(result_value):
                await result_value
        except Exception as err:  # pragma: no cover - защитный сценарий
            _LOGGER.exception(
                "Не удалось автоматически открыть uid=%s по лицу '%s': %s",
                door_uid,
                match_name,
                err,
            )
            return

        self._door_cooldown[door_uid] = time.monotonic()
        _LOGGER.info("Домофон uid=%s автоматически открыт для '%s'", door_uid, match_name)

    def _allow_event(self, door_uid: str, person_key: str) -> bool:
        """Ограничить частоту одинаковых событий, не блокируя само распознавание."""

        key = (door_uid, person_key)
        now = time.monotonic()
        previous = self._event_cooldown.get(key, 0.0)
        if now - previous < self._event_cooldown_seconds:
            return False
        self._event_cooldown[key] = now
        return True

    def _async_store_faces(self) -> None:
        """Сохранить локальную базу лиц в options config entry."""

        options = dict(self._entry.options)
        options[CONF_KNOWN_FACES] = [face.as_dict() for face in self._known_faces]
        self._hass.config_entries.async_update_entry(self._entry, options=options)

        domain_store = self._hass.data.setdefault(DOMAIN, {})
        entry_store = domain_store.setdefault(self._entry.entry_id, {})
        entry_store[DATA_FACE_MANAGER] = self
        _LOGGER.debug(
            "Локальная база лиц сохранена: entry_id=%s count=%s",
            self._entry.entry_id,
            len(self._known_faces),
        )
