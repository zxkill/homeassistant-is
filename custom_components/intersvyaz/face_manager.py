"""Нейросетевое распознавание лиц и безопасное автооткрытие домофона."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
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
    FACE_ENGINE_DLIB_RESNET_V1,
    FACE_ENGINE_PORTABLE_V1,
    FACE_EVENT_COOLDOWN_SECONDS,
    FACE_RECOGNITION_COOLDOWN_SECONDS,
    FACE_RECOGNITION_DISTANCE_THRESHOLD,
    FACE_REQUIRED_MATCHES_DEFAULT,
    FACE_REQUIRED_MATCHES_MAX,
    FACE_REQUIRED_MATCHES_MIN,
    FACE_TEMPLATES_PER_PERSON_MAX,
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
from .recognition import DlibFaceRecognitionEngine, FaceRecognitionResult
from .recognition.model_manager import FaceModelManager
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.face_manager")
_LEGACY_PORTABLE_DEFAULT_THRESHOLD = 0.30


@dataclass(slots=True)
class _MatchStreak:
    identity_key: str
    count: int


class FaceRecognitionManager:
    """Хранит нейросетевые эталоны и принимает решение об автооткрытии."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        *,
        engine: DlibFaceRecognitionEngine | None = None,
    ) -> None:
        self._hass = hass
        self._entry = entry

        if engine is None:
            model_dir = Path(
                hass.config.path(".storage", "intersvyaz_face_models")
            )
            self._model_manager: FaceModelManager | None = FaceModelManager(
                hass,
                model_dir,
            )
            self._engine = DlibFaceRecognitionEngine(model_dir)
        else:
            self._model_manager = None
            self._engine = engine

        self._known_faces: list[KnownFace] = []
        self._last_frame_hash: dict[str, bytes] = {}
        self._door_open_cooldown: dict[str, float] = {}
        self._event_cooldown: dict[tuple[str, str], float] = {}
        self._streaks: dict[str, _MatchStreak] = {}
        self._legacy_descriptors_skipped = False
        self._reset_legacy_threshold_on_store = False

        self._mode = DEFAULT_RECOGNITION_MODE
        self._threshold = FACE_RECOGNITION_DISTANCE_THRESHOLD
        self._required_matches = FACE_REQUIRED_MATCHES_DEFAULT
        self._open_cooldown = FACE_RECOGNITION_COOLDOWN_SECONDS
        self._event_cooldown_seconds = FACE_EVENT_COOLDOWN_SECONDS

        self._load_known_faces(entry.options.get(CONF_KNOWN_FACES, []))
        self.refresh_options()

        _LOGGER.info(
            "[FACE][READY] entry_id=%s people=%s templates=%s linked_people=%s "
            "mode=%s threshold=%.2f required=%s engine=%s dependencies=%s",
            entry.entry_id,
            len({face.identity_key for face in self._known_faces}),
            len(self._known_faces),
            len(
                {
                    face.person_entity_id
                    for face in self._known_faces
                    if face.person_entity_id
                }
            ),
            self._mode,
            self._threshold,
            self._required_matches,
            self._engine.engine_id,
            self._engine.available,
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
        options = self._entry.options
        mode = str(options.get(CONF_RECOGNITION_MODE, DEFAULT_RECOGNITION_MODE))
        self._mode = mode if mode in RECOGNITION_MODES else DEFAULT_RECOGNITION_MODE

        threshold_value = options.get(
            CONF_RECOGNITION_THRESHOLD,
            FACE_RECOGNITION_DISTANCE_THRESHOLD,
        )
        try:
            raw_threshold = float(threshold_value)
        except (TypeError, ValueError):
            raw_threshold = FACE_RECOGNITION_DISTANCE_THRESHOLD

        # The 2.0.x portable descriptor used a completely different distance scale.
        # Do not silently carry its default 0.30 into dlib ResNet.
        if (
            self._legacy_descriptors_skipped
            and abs(raw_threshold - _LEGACY_PORTABLE_DEFAULT_THRESHOLD) < 0.0001
        ):
            raw_threshold = FACE_RECOGNITION_DISTANCE_THRESHOLD
            self._reset_legacy_threshold_on_store = True

        self._threshold = _clamp_float(
            raw_threshold,
            default=FACE_RECOGNITION_DISTANCE_THRESHOLD,
            minimum=0.25,
            maximum=0.70,
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
            "[FACE][OPTIONS] mode=%s threshold=%.2f required=%s "
            "open_cooldown=%ss event_cooldown=%ss legacy_threshold_reset=%s",
            self._mode,
            self._threshold,
            self._required_matches,
            self._open_cooldown,
            self._event_cooldown_seconds,
            self._reset_legacy_threshold_on_store,
        )

    def list_known_faces(self) -> list[KnownFace]:
        return list(self._known_faces)

    def list_known_face_names(self) -> list[str]:
        """Return one display name per identity, not one row per template."""

        seen: set[str] = set()
        result: list[str] = []
        for face in self._known_faces:
            if face.identity_key in seen:
                continue
            seen.add(face.identity_key)
            result.append(self.display_name(face))
        return result

    def list_unlinked_faces(self) -> list[KnownFace]:
        result: list[KnownFace] = []
        seen: set[str] = set()
        for face in self._known_faces:
            if face.person_entity_id or face.identity_key in seen:
                continue
            seen.add(face.identity_key)
            result.append(face)
        return result

    def display_name(self, face: KnownFace) -> str:
        return face_display_name(self._hass, face)

    def face_choices(self, *, only_unlinked: bool = False) -> dict[str, str]:
        faces = self.list_unlinked_faces() if only_unlinked else self._known_faces
        counts: dict[str, int] = {}
        for face in self._known_faces:
            counts[face.identity_key] = counts.get(face.identity_key, 0) + 1

        choices: dict[str, str] = {}
        for face in faces:
            if face.identity_key in choices:
                continue
            label = self.display_name(face)
            if face.person_entity_id:
                label = f"{label} ({face.person_entity_id})"
            template_count = counts.get(face.identity_key, 1)
            if template_count > 1:
                label = f"{label} · {template_count} templates"
            choices[face.identity_key] = label
        return choices

    async def async_add_known_face(
        self,
        name: str,
        image_bytes: bytes,
        *,
        person_entity_id: str | None = None,
    ) -> None:
        """Add one ResNet template; up to five may belong to the same Person."""

        normalized_person = normalize_person_entity_id(self._hass, person_entity_id)
        normalized_name = resolve_person_name(
            self._hass,
            normalized_person,
            fallback=name,
        )
        if not normalized_name:
            raise HomeAssistantError("Имя не может быть пустым")
        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        await self._async_prepare_engine()
        if not self.library_available:
            raise HomeAssistantError(
                "dlib ResNet недоступен в текущем окружении. "
                "Проверьте установку dlib-bin и журнал Home Assistant."
            )

        _LOGGER.info(
            "[FACE][ENROLL_BEGIN] entry_id=%s image_bytes=%s "
            "linked_to_person=%s engine=%s",
            self._entry.entry_id,
            len(image_bytes),
            bool(normalized_person),
            self._engine.engine_id,
        )
        force_observe_after_enroll = (
            self._legacy_descriptors_skipped or not self._known_faces
        )
        encoding = await self._hass.async_add_executor_job(
            self._engine.extract_single_encoding,
            image_bytes,
        )

        # Drop old unlinked aliases with the same friendly name when a real Person
        # is selected, but preserve existing dlib templates for that Person.
        if normalized_person:
            self._known_faces = [
                face
                for face in self._known_faces
                if not (
                    not face.person_entity_id
                    and self.display_name(face).casefold()
                    == normalized_name.casefold()
                )
            ]

        new_face = KnownFace(
            name=normalized_name,
            encoding=encoding,
            engine=self._engine.engine_id,
            person_entity_id=normalized_person,
        )
        self._known_faces.append(new_face)
        self._trim_templates(new_face.identity_key)

        await self._async_store_faces(
            ensure_safe_mode=True,
            force_observe=force_observe_after_enroll,
        )
        templates = sum(
            1
            for face in self._known_faces
            if face.identity_key == new_face.identity_key
        )
        _LOGGER.info(
            "[FACE][ENROLL_OK] descriptor=%s identity_templates=%s/%s "
            "total_templates=%s linked_to_person=%s",
            len(encoding),
            templates,
            FACE_TEMPLATES_PER_PERSON_MAX,
            len(self._known_faces),
            bool(normalized_person),
        )

    async def async_link_known_face(
        self,
        identity_key: str,
        person_entity_id: str,
    ) -> None:
        face = self._find_face(identity_key)
        if face is None:
            raise HomeAssistantError("Выбранное лицо не найдено")
        normalized_person = normalize_person_entity_id(self._hass, person_entity_id)
        if not normalized_person:
            raise HomeAssistantError("Не выбран человек Home Assistant")

        old_key = face.identity_key
        new_name = resolve_person_name(
            self._hass,
            normalized_person,
            fallback=face.name,
        )

        for item in self._known_faces:
            if item.identity_key == old_key:
                item.person_entity_id = normalized_person
                item.name = new_name

        new_key = f"person:{normalized_person}"
        self._trim_templates(new_key)
        await self._async_store_faces()
        _LOGGER.info(
            "[FACE][LINK_OK] entry_id=%s linked_to_person=true templates=%s",
            self._entry.entry_id,
            sum(1 for item in self._known_faces if item.identity_key == new_key),
        )

    async def async_remove_known_face(self, identifier: str) -> None:
        normalized = (identifier or "").strip()
        before = len(self._known_faces)
        self._known_faces = [
            face
            for face in self._known_faces
            if not self._face_matches_identifier(face, normalized)
        ]
        removed = before - len(self._known_faces)
        if removed <= 0:
            raise HomeAssistantError(f"Лицо '{normalized}' не найдено")
        await self._async_store_faces()
        _LOGGER.info(
            "[FACE][REMOVE_OK] removed_templates=%s total_templates=%s",
            removed,
            len(self._known_faces),
        )

    async def async_process_image(
        self,
        door_uid: str,
        image_bytes: bytes,
        open_callback: Callable[[], Awaitable[None]] | None,
    ) -> FaceRecognitionResult | None:
        if self._mode == RECOGNITION_MODE_OFF:
            return None
        if not self._known_faces or not image_bytes:
            return None

        try:
            await self._async_prepare_engine()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "[FACE][PREPARE_FAILED] door=%s error=%s",
                safe_door_ref(door_uid),
                err,
            )
            return None

        if not self.library_available:
            return None

        digest = hashlib.blake2b(image_bytes, digest_size=12).digest()
        if self._last_frame_hash.get(door_uid) == digest:
            _LOGGER.debug(
                "[FACE][FRAME_SKIP] door=%s reason=identical",
                safe_door_ref(door_uid),
            )
            return None
        self._last_frame_hash[door_uid] = digest

        known = [
            (face.identity_key, face.encoding)
            for face in self._known_faces
            if face.engine == FACE_ENGINE_DLIB_RESNET_V1
            and len(face.encoding) == 128
        ]
        try:
            result = await self._hass.async_add_executor_job(
                self._engine.recognize,
                image_bytes,
                known,
                self._threshold,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "[FACE][ANALYZE_FAILED] door=%s error=%s",
                safe_door_ref(door_uid),
                err,
            )
            return None
        except Exception:  # pragma: no cover
            _LOGGER.exception(
                "[FACE][ANALYZE_UNEXPECTED] door=%s",
                safe_door_ref(door_uid),
            )
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
                hashlib.blake2b(
                    result.matched_name.encode(),
                    digest_size=5,
                ).hexdigest(),
            )
            self._streaks.pop(door_uid, None)
            return result

        display_name = self.display_name(matched_face)
        streak = self._advance_streak(door_uid, matched_face.identity_key)
        should_force_confirm_event = streak == self._required_matches
        if should_force_confirm_event or self._should_emit(
            door_uid,
            DOOR_EVENT_FACE_RECOGNIZED,
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
                "[FACE][AUTO_OPEN_WAIT] door=%s streak=%s/%s",
                safe_door_ref(door_uid),
                streak,
                self._required_matches,
            )
            return result
        if result.faces_detected != 1:
            _LOGGER.warning(
                "[FACE][AUTO_OPEN_BLOCK] door=%s reason=faces_detected value=%s",
                safe_door_ref(door_uid),
                result.faces_detected,
            )
            return result
        if not result.auto_open_safe:
            _LOGGER.warning(
                "[FACE][AUTO_OPEN_BLOCK] door=%s reason=dlib_safety distance=%s",
                safe_door_ref(door_uid),
                f"{result.distance:.4f}" if result.distance is not None else "none",
            )
            return result
        if not callable(open_callback):
            _LOGGER.warning(
                "[FACE][AUTO_OPEN_BLOCK] door=%s reason=no_callback",
                safe_door_ref(door_uid),
            )
            return result

        now = time.monotonic()
        last_open = self._door_open_cooldown.get(door_uid, 0.0)
        if now - last_open < self._open_cooldown:
            _LOGGER.debug(
                "[FACE][AUTO_OPEN_COOLDOWN] door=%s remaining=%.1fs",
                safe_door_ref(door_uid),
                self._open_cooldown - (now - last_open),
            )
            return result

        _LOGGER.info(
            "[FACE][AUTO_OPEN_CONFIRMED] door=%s streak=%s linked_person=%s",
            safe_door_ref(door_uid),
            streak,
            bool(matched_face.person_entity_id),
        )
        try:
            await open_callback()
        except HomeAssistantError as err:
            _LOGGER.warning(
                "[FACE][AUTO_OPEN_FAILED] door=%s error=%s",
                safe_door_ref(door_uid),
                err,
            )
            return result
        except Exception:  # pragma: no cover
            _LOGGER.exception(
                "[FACE][AUTO_OPEN_UNEXPECTED] door=%s",
                safe_door_ref(door_uid),
            )
            return result

        self._door_open_cooldown[door_uid] = time.monotonic()
        self._streaks.pop(door_uid, None)
        return result

    async def async_stop(self) -> None:
        _LOGGER.debug(
            "[FACE][STOP] entry_id=%s engine=%s",
            self._entry.entry_id,
            self._engine.engine_id,
        )
        await self._hass.async_add_executor_job(self._engine.close)

    async def _async_prepare_engine(self) -> None:
        if not self._engine.available:
            raise HomeAssistantError(
                "dlib-bin не установлен или несовместим с текущей платформой"
            )
        if self._model_manager is not None:
            await self._model_manager.async_ensure_models()
        if not bool(getattr(self._engine, "models_available", True)):
            raise HomeAssistantError("Модели dlib ResNet не удалось подготовить")

    def _trim_templates(self, identity_key: str) -> None:
        templates = [
            face
            for face in self._known_faces
            if face.identity_key == identity_key
        ]
        if len(templates) <= FACE_TEMPLATES_PER_PERSON_MAX:
            return

        keep_ids = {
            id(face)
            for face in templates[-FACE_TEMPLATES_PER_PERSON_MAX:]
        }
        before = len(self._known_faces)
        self._known_faces = [
            face
            for face in self._known_faces
            if face.identity_key != identity_key or id(face) in keep_ids
        ]
        _LOGGER.info(
            "[FACE][TEMPLATE_TRIM] identity_hash=%s removed=%s kept=%s",
            hashlib.blake2b(
                identity_key.encode(),
                digest_size=5,
            ).hexdigest(),
            before - len(self._known_faces),
            FACE_TEMPLATES_PER_PERSON_MAX,
        )

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

            if engine != FACE_ENGINE_DLIB_RESNET_V1:
                self._legacy_descriptors_skipped = True
                previous_engine = engine or "legacy_dlib"
                _LOGGER.warning(
                    "[FACE][MIGRATION_SKIP] name_present=%s old_engine=%s "
                    "new_engine=%s action=re_enroll_required",
                    bool(name.strip()),
                    previous_engine,
                    FACE_ENGINE_DLIB_RESNET_V1,
                )
                continue

            try:
                vector = [float(value) for value in encoding]
            except (TypeError, ValueError):
                continue

            normalized_person = (
                str(person_entity_id).strip()
                if isinstance(person_entity_id, str)
                and str(person_entity_id).startswith("person.")
                else None
            )
            if name.strip() and len(vector) == 128:
                self._known_faces.append(
                    KnownFace(
                        name=name.strip(),
                        encoding=vector,
                        engine=FACE_ENGINE_DLIB_RESNET_V1,
                        person_entity_id=normalized_person,
                    )
                )

        for identity_key in {
            face.identity_key for face in self._known_faces
        }:
            self._trim_templates(identity_key)

        _LOGGER.info(
            "[FACE][LOAD] people=%s templates=%s linked_people=%s "
            "legacy_skipped=%s engine=%s",
            len({face.identity_key for face in self._known_faces}),
            len(self._known_faces),
            len(
                {
                    face.person_entity_id
                    for face in self._known_faces
                    if face.person_entity_id
                }
            ),
            self._legacy_descriptors_skipped,
            FACE_ENGINE_DLIB_RESNET_V1,
        )

    async def _async_store_faces(
        self,
        *,
        ensure_safe_mode: bool = False,
        force_observe: bool = False,
    ) -> None:
        options = dict(self._entry.options)
        options[CONF_KNOWN_FACES] = [
            face.as_dict() for face in self._known_faces
        ]
        if ensure_safe_mode and CONF_RECOGNITION_MODE not in options:
            options[CONF_RECOGNITION_MODE] = DEFAULT_RECOGNITION_MODE

        if (
            force_observe
            and options.get(CONF_RECOGNITION_MODE) == RECOGNITION_MODE_AUTO_OPEN
        ):
            options[CONF_RECOGNITION_MODE] = DEFAULT_RECOGNITION_MODE
            _LOGGER.warning(
                "[FACE][MIGRATION_AUTO_OPEN_DISABLED] entry_id=%s "
                "reason=new_recognition_engine_requires_observation",
                self._entry.entry_id,
            )

        if self._reset_legacy_threshold_on_store:
            options[CONF_RECOGNITION_THRESHOLD] = (
                FACE_RECOGNITION_DISTANCE_THRESHOLD
            )
            self._reset_legacy_threshold_on_store = False
            _LOGGER.info(
                "[FACE][MIGRATION_THRESHOLD] old=%.2f new=%.2f engine=%s",
                _LEGACY_PORTABLE_DEFAULT_THRESHOLD,
                FACE_RECOGNITION_DISTANCE_THRESHOLD,
                FACE_ENGINE_DLIB_RESNET_V1,
            )

        self._hass.config_entries.async_update_entry(
            self._entry,
            options=options,
        )
        self._legacy_descriptors_skipped = False
        self.refresh_options()
        _LOGGER.debug(
            "[FACE][STORE] entry_id=%s people=%s templates=%s linked_people=%s",
            self._entry.entry_id,
            len({face.identity_key for face in self._known_faces}),
            len(self._known_faces),
            len(
                {
                    face.person_entity_id
                    for face in self._known_faces
                    if face.person_entity_id
                }
            ),
        )


def _clamp_float(
    value,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(minimum, min(maximum, parsed))


def _clamp_int(
    value,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(maximum, parsed))
