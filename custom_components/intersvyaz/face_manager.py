"""Локальное распознавание лиц и безопасное автооткрытие домофона."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_AUTO_OPEN_COOLDOWN_SECONDS,
    CONF_FACE_ENCODING,
    CONF_FACE_ENGINE,
    CONF_FACE_EVENT_COOLDOWN_SECONDS,
    CONF_FACE_NAME,
    CONF_FACE_PERSON_ENTITY_ID,
    CONF_KNOWN_FACES,
    CONF_RECOGNITION_MODE,
    CONF_RECOGNITION_REQUIRED_MATCHES,
    CONF_RECOGNITION_THRESHOLD,
    DEFAULT_RECOGNITION_MODE,
    DOOR_EVENT_FACE_RECOGNIZED,
    DOOR_EVENT_UNKNOWN_PERSON,
    FACE_ENGINE_PORTABLE_V1,
    FACE_EVENT_COOLDOWN_SECONDS,
    FACE_RECOGNITION_COOLDOWN_SECONDS,
    FACE_RECOGNITION_DISTANCE_THRESHOLD,
    FACE_REQUIRED_MATCHES_DEFAULT,
    FACE_REQUIRED_MATCHES_MAX,
    FACE_REQUIRED_MATCHES_MIN,
    RECOGNITION_MODE_AUTO_OPEN,
    RECOGNITION_MODE_OFF,
    RECOGNITION_MODES,
)
from .events import emit_door_event, face_payload
from .face_identity import (
    KnownFace,
    face_display_name,
    normalize_person_entity_id,
    resolve_person_name,
)
from .recognition import FaceRecognitionResult, PortableFaceRecognitionEngine
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.face_manager")


@dataclass(slots=True)
class _MatchStreak:
    identity_key: str
    count: int


class FaceRecognitionManager:
    """Хранит базу лиц и принимает решение о распознавании/автооткрытии."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        *,
        engine: PortableFaceRecognitionEngine | None = None,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._engine = engine or PortableFaceRecognitionEngine()
        self._known_faces: list[KnownFace] = []
        self._last_frame_hash: dict[str, bytes] = {}
        self._door_open_cooldown: dict[str, float] = {}
        self._event_cooldown: dict[tuple[str, str], float] = {}
        self._streaks: dict[str, _MatchStreak] = {}

        self._mode = DEFAULT_RECOGNITION_MODE
        self._threshold = FACE_RECOGNITION_DISTANCE_THRESHOLD
        self._required_matches = FACE_REQUIRED_MATCHES_DEFAULT
        self._open_cooldown = FACE_RECOGNITION_COOLDOWN_SECONDS
        self._event_cooldown_seconds = FACE_EVENT_COOLDOWN_SECONDS

        self._load_known_faces(entry.options.get(CONF_KNOWN_FACES, []))
        self.refresh_options()

        _LOGGER.info(
            "[FACE][READY] entry_id=%s faces=%s linked_people=%s mode=%s threshold=%.2f "
            "required=%s engine=%s",
            entry.entry_id,
            len(self._known_faces),
            sum(1 for face in self._known_faces if face.person_entity_id),
            self._mode,
            self._threshold,
            self._required_matches,
            self._engine.engine_id,
        )

    @property
    def library_available(self) -> bool:
        return self._engine.available

    @property
    def recognition_mode(self) -> str:
        return self._mode

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def required_matches(self) -> int:
        return self._required_matches

    def refresh_options(self) -> None:
        """Применить options без перезагрузки интеграции."""

        options = self._entry.options
        mode = str(options.get(CONF_RECOGNITION_MODE, DEFAULT_RECOGNITION_MODE))
        self._mode = mode if mode in RECOGNITION_MODES else DEFAULT_RECOGNITION_MODE
        self._threshold = _clamp_float(
            options.get(CONF_RECOGNITION_THRESHOLD),
            default=FACE_RECOGNITION_DISTANCE_THRESHOLD,
            minimum=0.10,
            maximum=0.55,
        )
        self._required_matches = _clamp_int(
            options.get(CONF_RECOGNITION_REQUIRED_MATCHES),
            default=FACE_REQUIRED_MATCHES_DEFAULT,
            minimum=FACE_REQUIRED_MATCHES_MIN,
            maximum=FACE_REQUIRED_MATCHES_MAX,
        )
        self._open_cooldown = _clamp_float(
            options.get(CONF_AUTO_OPEN_COOLDOWN_SECONDS),
            default=FACE_RECOGNITION_COOLDOWN_SECONDS,
            minimum=5,
            maximum=600,
        )
        self._event_cooldown_seconds = _clamp_float(
            options.get(CONF_FACE_EVENT_COOLDOWN_SECONDS),
            default=FACE_EVENT_COOLDOWN_SECONDS,
            minimum=1,
            maximum=300,
        )
        _LOGGER.debug(
            "[FACE][OPTIONS] mode=%s threshold=%.2f required=%s open_cooldown=%ss event_cooldown=%ss",
            self._mode,
            self._threshold,
            self._required_matches,
            self._open_cooldown,
            self._event_cooldown_seconds,
        )

    def list_known_faces(self) -> list[KnownFace]:
        return list(self._known_faces)

    def list_known_face_names(self) -> list[str]:
        """Сохранить старый API: вернуть отображаемые имена лиц."""

        return [self.display_name(face) for face in self._known_faces]

    def list_unlinked_faces(self) -> list[KnownFace]:
        return [face for face in self._known_faces if not face.person_entity_id]

    def display_name(self, face: KnownFace) -> str:
        """Получить актуальное friendly_name связанного Person, если он доступен."""

        return face_display_name(self._hass, face)

    def face_choices(self, *, only_unlinked: bool = False) -> dict[str, str]:
        """Вернуть choices identity_key -> человекочитаемая подпись для options flow."""

        faces = self.list_unlinked_faces() if only_unlinked else self._known_faces
        choices: dict[str, str] = {}
        for face in faces:
            label = self.display_name(face)
            if face.person_entity_id:
                label = f"{label} ({face.person_entity_id})"
            choices[face.identity_key] = label
        return choices

    async def async_add_known_face(
        self,
        name: str,
        image_bytes: bytes,
        *,
        person_entity_id: str | None = None,
    ) -> None:
        """Добавить/заменить лицо и при необходимости связать его с Person HA."""

        normalized_person = normalize_person_entity_id(self._hass, person_entity_id)
        normalized_name = resolve_person_name(self._hass, normalized_person, fallback=name)
        if not normalized_name:
            raise HomeAssistantError("Имя не может быть пустым")
        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")
        if not self.library_available:
            raise HomeAssistantError(
                "Локальный движок распознавания недоступен. Проверьте requirements и журнал"
            )

        _LOGGER.info(
            "[FACE][ENROLL_BEGIN] entry_id=%s image_bytes=%s linked_to_person=%s",
            self._entry.entry_id,
            len(image_bytes),
            bool(normalized_person),
        )
        encoding = await self._hass.async_add_executor_job(
            self._engine.extract_single_encoding, image_bytes
        )

        # Для Person Home Assistant одна личность должна иметь ровно одну активную запись.
        # Одновременно заменяем старую legacy-запись с тем же именем, чтобы после миграции
        # пользователь не получил два конкурирующих descriptor одного человека.
        self._known_faces = [
            face
            for face in self._known_faces
            if not (
                (normalized_person and face.person_entity_id == normalized_person)
                or (
                    not face.person_entity_id
                    and self.display_name(face).casefold() == normalized_name.casefold()
                )
            )
        ]
        self._known_faces.append(
            KnownFace(
                name=normalized_name,
                encoding=encoding,
                engine=self._engine.engine_id,
                person_entity_id=normalized_person,
            )
        )
        await self._async_store_faces(ensure_safe_mode=True)
        _LOGGER.info(
            "[FACE][ENROLL_OK] descriptor=%s total_faces=%s linked_to_person=%s",
            len(encoding),
            len(self._known_faces),
            bool(normalized_person),
        )

    async def async_link_known_face(
        self, identity_key: str, person_entity_id: str
    ) -> None:
        """Связать уже существующий legacy descriptor с Person Home Assistant."""

        face = self._find_face(identity_key)
        if face is None:
            raise HomeAssistantError("Выбранное лицо не найдено")
        normalized_person = normalize_person_entity_id(self._hass, person_entity_id)
        if not normalized_person:
            raise HomeAssistantError("Не выбран человек Home Assistant")

        new_name = resolve_person_name(self._hass, normalized_person, fallback=face.name)
        self._known_faces = [
            item
            for item in self._known_faces
            if item is face or item.person_entity_id != normalized_person
        ]
        face.person_entity_id = normalized_person
        face.name = new_name
        await self._async_store_faces()
        _LOGGER.info(
            "[FACE][LINK_OK] entry_id=%s linked_to_person=true total_faces=%s",
            self._entry.entry_id,
            len(self._known_faces),
        )

    async def async_remove_known_face(self, identifier: str) -> None:
        """Удалить лицо по identity_key, имени или person entity_id."""

        normalized = (identifier or "").strip()
        before = len(self._known_faces)
        self._known_faces = [
            face
            for face in self._known_faces
            if not self._face_matches_identifier(face, normalized)
        ]
        if len(self._known_faces) == before:
            raise HomeAssistantError(f"Лицо '{normalized}' не найдено")
        await self._async_store_faces()
        _LOGGER.info("[FACE][REMOVE_OK] total_faces=%s", len(self._known_faces))

    async def async_process_image(
        self,
        door_uid: str,
        image_bytes: bytes,
        open_callback: Callable[[], Awaitable[None]] | None,
    ) -> FaceRecognitionResult | None:
        """Проанализировать кадр и, в режиме auto_open, безопасно открыть дверь."""

        if self._mode == RECOGNITION_MODE_OFF:
            return None
        if not self.library_available or not image_bytes:
            return None

        digest = hashlib.blake2b(image_bytes, digest_size=12).digest()
        if self._last_frame_hash.get(door_uid) == digest:
            _LOGGER.debug("Повторный идентичный кадр door=%s пропущен", safe_door_ref(door_uid))
            return None
        self._last_frame_hash[door_uid] = digest

        known = [(face.identity_key, face.encoding) for face in self._known_faces]
        try:
            result = await self._hass.async_add_executor_job(
                self._engine.recognize,
                image_bytes,
                known,
                self._threshold,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Ошибка анализа кадра door=%s: %s", safe_door_ref(door_uid), err)
            return None
        except Exception:  # pragma: no cover
            _LOGGER.exception("Неожиданная ошибка recognition door=%s", safe_door_ref(door_uid))
            return None

        if result.faces_detected <= 0:
            self._streaks.pop(door_uid, None)
            return result

        if not result.matched or not result.matched_name:
            self._streaks.pop(door_uid, None)
            if self._should_emit(door_uid, DOOR_EVENT_UNKNOWN_PERSON):
                emit_door_event(
                    self._hass,
                    self._entry,
                    door_uid,
                    DOOR_EVENT_UNKNOWN_PERSON,
                    face_payload(
                        person=None,
                        person_entity_id=None,
                        distance=result.distance,
                        threshold=self._threshold,
                        faces_detected=result.faces_detected,
                        streak=0,
                        required_matches=self._required_matches,
                    ),
                )
                self._mark_event(door_uid, DOOR_EVENT_UNKNOWN_PERSON)
            return result

        matched_face = self._find_face(result.matched_name)
        if matched_face is None:
            _LOGGER.warning(
                "[FACE][MATCH_ORPHAN] door=%s identity_key_hash=%s",
                safe_door_ref(door_uid),
                hashlib.blake2b(result.matched_name.encode(), digest_size=5).hexdigest(),
            )
            self._streaks.pop(door_uid, None)
            return result

        display_name = self.display_name(matched_face)
        streak = self._advance_streak(door_uid, matched_face.identity_key)
        should_force_confirm_event = streak == self._required_matches
        if should_force_confirm_event or self._should_emit(
            door_uid, DOOR_EVENT_FACE_RECOGNIZED
        ):
            emit_door_event(
                self._hass,
                self._entry,
                door_uid,
                DOOR_EVENT_FACE_RECOGNIZED,
                face_payload(
                    person=display_name,
                    person_entity_id=matched_face.person_entity_id,
                    distance=result.distance,
                    threshold=self._threshold,
                    faces_detected=result.faces_detected,
                    streak=streak,
                    required_matches=self._required_matches,
                )
                | {
                    "confirmed": streak >= self._required_matches,
                    "auto_open_enabled": self._mode == RECOGNITION_MODE_AUTO_OPEN,
                },
            )
            self._mark_event(door_uid, DOOR_EVENT_FACE_RECOGNIZED)

        if self._mode != RECOGNITION_MODE_AUTO_OPEN:
            return result
        if streak < self._required_matches:
            _LOGGER.debug(
                "Ожидаем подтверждение лица door=%s streak=%s/%s",
                safe_door_ref(door_uid),
                streak,
                self._required_matches,
            )
            return result
        if result.faces_detected != 1:
            _LOGGER.warning(
                "Auto-open door=%s заблокирован: faces_detected=%s (требуется ровно одно лицо)",
                safe_door_ref(door_uid),
                result.faces_detected,
            )
            return result
        if not result.auto_open_safe:
            _LOGGER.warning(
                "Auto-open door=%s заблокирован portable-движком: кандидат недостаточно надёжен",
                safe_door_ref(door_uid),
            )
            return result
        if not callable(open_callback):
            _LOGGER.warning(
                "Auto-open door=%s невозможен: callback отсутствует",
                safe_door_ref(door_uid),
            )
            return result

        now = time.monotonic()
        last_open = self._door_open_cooldown.get(door_uid, 0.0)
        if now - last_open < self._open_cooldown:
            _LOGGER.debug(
                "Auto-open door=%s пропущен cooldown remaining=%.1fs",
                safe_door_ref(door_uid),
                self._open_cooldown - (now - last_open),
            )
            return result

        _LOGGER.info(
            "Auto-open подтверждён: door=%s streak=%s linked_person=%s",
            safe_door_ref(door_uid),
            streak,
            bool(matched_face.person_entity_id),
        )
        try:
            await open_callback()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Auto-open door=%s не выполнен: %s",
                safe_door_ref(door_uid),
                err,
            )
            return result
        except Exception:  # pragma: no cover - физическое действие не ломает camera entity
            _LOGGER.exception(
                "Auto-open door=%s завершился неожиданной ошибкой",
                safe_door_ref(door_uid),
            )
            return result
        self._door_open_cooldown[door_uid] = time.monotonic()
        self._streaks.pop(door_uid, None)
        return result

    async def async_stop(self) -> None:
        """Остановить изолированный recognition worker при выгрузке интеграции."""

        _LOGGER.debug("Останавливаем face manager entry_id=%s", self._entry.entry_id)
        await self._hass.async_add_executor_job(self._engine.close)

    def _advance_streak(self, door_uid: str, identity_key: str) -> int:
        current = self._streaks.get(door_uid)
        if current is None or current.identity_key != identity_key:
            current = _MatchStreak(identity_key=identity_key, count=1)
        else:
            current.count += 1
        self._streaks[door_uid] = current
        return current.count

    def _should_emit(self, door_uid: str, event_type: str) -> bool:
        last = self._event_cooldown.get((door_uid, event_type), 0.0)
        return time.monotonic() - last >= self._event_cooldown_seconds

    def _mark_event(self, door_uid: str, event_type: str) -> None:
        self._event_cooldown[(door_uid, event_type)] = time.monotonic()

    def _find_face(self, identifier: str) -> KnownFace | None:
        normalized = (identifier or "").strip()
        return next(
            (
                face
                for face in self._known_faces
                if self._face_matches_identifier(face, normalized)
            ),
            None,
        )

    @staticmethod
    def _face_matches_identifier(face: KnownFace, identifier: str) -> bool:
        return identifier in {
            face.identity_key,
            face.name,
            face.person_entity_id or "",
        }

    def _load_known_faces(self, stored: Iterable[dict[str, object]]) -> None:
        self._known_faces.clear()
        for item in stored or []:
            if not isinstance(item, dict):
                continue
            name = item.get(CONF_FACE_NAME)
            encoding = item.get(CONF_FACE_ENCODING)
            engine = item.get(CONF_FACE_ENGINE)
            person_entity_id = item.get(CONF_FACE_PERSON_ENTITY_ID)
            if not isinstance(name, str) or not isinstance(encoding, Iterable):
                continue
            if engine != FACE_ENGINE_PORTABLE_V1:
                previous_engine = engine or "legacy_dlib"
                _LOGGER.warning(
                    "Лицо '%s' пропущено: descriptor создан несовместимым движком (%s). "
                    "После обновления до portable engine его нужно добавить заново.",
                    name.strip() or "<без имени>",
                    previous_engine,
                )
                continue
            try:
                vector = [float(value) for value in encoding]
            except (TypeError, ValueError):
                continue
            normalized_person = (
                str(person_entity_id).strip()
                if isinstance(person_entity_id, str) and str(person_entity_id).startswith("person.")
                else None
            )
            if name.strip() and len(vector) == 128:
                self._known_faces.append(
                    KnownFace(
                        name=name.strip(),
                        encoding=vector,
                        engine=FACE_ENGINE_PORTABLE_V1,
                        person_entity_id=normalized_person,
                    )
                )
        _LOGGER.debug(
            "[FACE][LOAD] faces=%s linked_people=%s legacy_unlinked=%s",
            len(self._known_faces),
            sum(1 for face in self._known_faces if face.person_entity_id),
            sum(1 for face in self._known_faces if not face.person_entity_id),
        )

    async def _async_store_faces(self, *, ensure_safe_mode: bool = False) -> None:
        options = dict(self._entry.options)
        options[CONF_KNOWN_FACES] = [face.as_dict() for face in self._known_faces]
        if ensure_safe_mode and CONF_RECOGNITION_MODE not in options:
            options[CONF_RECOGNITION_MODE] = DEFAULT_RECOGNITION_MODE
        self._hass.config_entries.async_update_entry(self._entry, options=options)
        self.refresh_options()
        _LOGGER.debug(
            "[FACE][STORE] entry_id=%s faces=%s linked_people=%s",
            self._entry.entry_id,
            len(self._known_faces),
            sum(1 for face in self._known_faces if face.person_entity_id),
        )


def _clamp_float(value, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(minimum, min(maximum, parsed))


def _clamp_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(maximum, parsed))
