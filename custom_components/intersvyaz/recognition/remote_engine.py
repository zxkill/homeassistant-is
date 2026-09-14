"""HTTP client for an external face-recognition encoder service.

Talks to a face_recognize_service-compatible container (see
C:\\storm\\ha_addons\\face_recognize_service) over REST instead of spawning a
local dlib subprocess. That service only detects faces and returns 128-d
dlib ResNet descriptors; comparing descriptors against known faces and
applying thresholds stays here, exactly like the local dlib/portable
engines, so Home Assistant remains the single source of truth for who is
"known".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from aiohttp import ClientError, ClientTimeout, FormData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX
from .engine import FaceRecognitionResult

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")

_ENROLL_JITTERS = 2
_RECOGNIZE_JITTERS = 1
_EXTRA_CALL_TIMEOUT_MARGIN_SECONDS = 5.0


class RemoteRecognitionUnavailable(HomeAssistantError):
    """Remote service unreachable, timed out, or returned a server error.

    Treated by the backend router as a transient condition (unlike a native
    SIGILL): worth a cooldown before retrying, but never a permanent switch.
    """


class RemoteRecognitionAuthError(HomeAssistantError):
    """Remote service rejected the configured API key."""


class RemoteFaceRecognitionEngine:
    """Client for a remote, encoder-only face_recognize_service container."""

    engine_id = "remote_dlib_resnet_v1"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._url: str | None = None
        self._api_key: str | None = None
        self._timeout_seconds: float = 15.0

    def configure(
        self,
        url: str | None,
        api_key: str | None,
        timeout_seconds: float,
    ) -> None:
        """Update connection settings; safe to call any time (e.g. on options save)."""

        self._url = (url or "").strip().rstrip("/") or None
        self._api_key = (api_key or "").strip() or None
        try:
            self._timeout_seconds = max(float(timeout_seconds), 3.0)
        except (TypeError, ValueError):
            self._timeout_seconds = 15.0

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def available(self) -> bool:
        return self._url is not None

    @property
    def fatal_error(self) -> str | None:
        # A remote failure never permanently disables this engine on its own;
        # FaceRecognitionBackendRouter owns the transient cooldown instead.
        return None

    def probe(self) -> None:
        """Real connectivity check used by the router before trusting remote."""

        status, payload = self._call_get("/health")
        self._raise_for_status(status)
        if payload.get("status") != "ok":
            raise RemoteRecognitionUnavailable(
                "Удалённый сервис распознавания вернул неожиданный ответ /health"
            )
        _LOGGER.info(
            "[FACE][REMOTE_PROBE_OK] engine=%s remote_engine=%s",
            self.engine_id,
            payload.get("engine", "unknown"),
        )

    def extract_single_encoding(self, image_bytes: bytes) -> list[float]:
        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        status, payload = self._call(
            "/v1/encode/single",
            image_bytes,
            {"num_jitters": str(_ENROLL_JITTERS)},
        )

        if status == 422:
            code = _error_code(payload)
            if code == "no_face":
                raise HomeAssistantError("На фотографии не найдено лицо")
            if code == "multiple_faces":
                faces = _error_detail(payload).get("faces_detected", "?")
                raise HomeAssistantError(
                    f"Для эталона требуется ровно одно лицо; найдено: {faces}"
                )
            raise HomeAssistantError("Удалённый сервис отклонил изображение")

        self._raise_for_status(status)

        encoding = payload.get("encoding")
        if not isinstance(encoding, list) or len(encoding) != 128:
            raise HomeAssistantError(
                "Удалённый сервис распознавания вернул некорректный descriptor лица"
            )
        try:
            return [float(value) for value in encoding]
        except (TypeError, ValueError) as err:
            raise HomeAssistantError(
                "Удалённый сервис распознавания вернул повреждённый descriptor лица"
            ) from err

    def recognize(
        self,
        image_bytes: bytes,
        known_faces: Sequence[tuple[str, Sequence[float]]],
        threshold: float,
    ) -> FaceRecognitionResult:
        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        status, payload = self._call(
            "/v1/encode/multi",
            image_bytes,
            {"num_jitters": str(_RECOGNIZE_JITTERS)},
        )
        self._raise_for_status(status)

        faces = payload.get("faces")
        if not isinstance(faces, list):
            faces = []

        best_name, best_distance = _best_match(faces, known_faces)
        matched = (
            best_name is not None
            and best_distance is not None
            and best_distance <= threshold
        )
        auto_open_safe = (
            matched
            and len(faces) == 1
            and best_distance is not None
            and best_distance <= FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX
        )

        return FaceRecognitionResult(
            faces_detected=len(faces),
            matched_name=best_name if matched else None,
            distance=best_distance,
            auto_open_safe=bool(auto_open_safe),
        )

    def close(self) -> None:
        # The aiohttp session is shared/owned by Home Assistant; nothing to close.
        return None

    # -- transport -----------------------------------------------------

    def _call(
        self,
        path: str,
        image_bytes: bytes,
        extra_fields: dict[str, str],
    ) -> tuple[int, dict]:
        """Blocking call from an executor thread; bridges into the HA event loop."""

        return asyncio.run_coroutine_threadsafe(
            self._async_post(path, image_bytes, extra_fields),
            self._hass.loop,
        ).result(timeout=self._timeout_seconds + _EXTRA_CALL_TIMEOUT_MARGIN_SECONDS)

    def _call_get(self, path: str) -> tuple[int, dict]:
        return asyncio.run_coroutine_threadsafe(
            self._async_get(path),
            self._hass.loop,
        ).result(timeout=self._timeout_seconds + _EXTRA_CALL_TIMEOUT_MARGIN_SECONDS)

    async def _async_post(
        self,
        path: str,
        image_bytes: bytes,
        extra_fields: dict[str, str],
    ) -> tuple[int, dict]:
        if self._url is None:
            raise RemoteRecognitionUnavailable("Удалённый сервис распознавания не настроен")

        form = FormData()
        form.add_field(
            "image",
            image_bytes,
            filename="frame.jpg",
            content_type="application/octet-stream",
        )
        for key, value in extra_fields.items():
            form.add_field(key, value)

        try:
            session = async_get_clientsession(self._hass)
            async with session.post(
                f"{self._url}{path}",
                data=form,
                headers=self._headers(),
                timeout=ClientTimeout(total=self._timeout_seconds),
            ) as response:
                return response.status, await _safe_json(response)
        except asyncio.TimeoutError as err:
            raise RemoteRecognitionUnavailable(
                f"Удалённый сервис распознавания не ответил за {self._timeout_seconds:.0f}с"
            ) from err
        except ClientError as err:
            raise RemoteRecognitionUnavailable(
                f"Удалённый сервис распознавания недоступен: {err}"
            ) from err

    async def _async_get(self, path: str) -> tuple[int, dict]:
        if self._url is None:
            raise RemoteRecognitionUnavailable("Удалённый сервис распознавания не настроен")

        try:
            session = async_get_clientsession(self._hass)
            async with session.get(
                f"{self._url}{path}",
                headers=self._headers(),
                timeout=ClientTimeout(total=self._timeout_seconds),
            ) as response:
                return response.status, await _safe_json(response)
        except asyncio.TimeoutError as err:
            raise RemoteRecognitionUnavailable(
                f"Удалённый сервис распознавания не ответил за {self._timeout_seconds:.0f}с"
            ) from err
        except ClientError as err:
            raise RemoteRecognitionUnavailable(
                f"Удалённый сервис распознавания недоступен: {err}"
            ) from err

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _raise_for_status(self, status: int) -> None:
        if status in (401, 403):
            raise RemoteRecognitionAuthError(
                "Удалённый сервис распознавания отклонил API-ключ"
            )
        if status >= 500 or status == 0:
            raise RemoteRecognitionUnavailable(
                f"Удалённый сервис распознавания вернул ошибку (HTTP {status})"
            )
        if status >= 400:
            raise HomeAssistantError(
                f"Удалённый сервис распознавания отклонил запрос (HTTP {status})"
            )


def _best_match(
    faces: list,
    known_faces: Sequence[tuple[str, Sequence[float]]],
) -> tuple[str | None, float | None]:
    """Nearest-neighbour match across all detected faces vs. all known templates."""

    if not faces or not known_faces:
        return None, None

    import numpy as np  # local import: keep numpy out of the HA event-loop startup path

    best_name: str | None = None
    best_distance: float | None = None
    for face in faces:
        if not isinstance(face, dict):
            continue
        encoding = face.get("encoding")
        if not isinstance(encoding, list) or len(encoding) != 128:
            continue
        candidate = np.asarray(encoding, dtype=float)

        for name, known_encoding in known_faces:
            if len(known_encoding) != 128:
                continue
            distance = float(np.linalg.norm(candidate - np.asarray(known_encoding, dtype=float)))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_name = str(name)

    return best_name, best_distance


async def _safe_json(response) -> dict:
    try:
        data = await response.json(content_type=None)
    except Exception:  # noqa: BLE001 - malformed/empty body is not fatal here
        return {}
    return data if isinstance(data, dict) else {}


def _error_detail(payload: dict) -> dict:
    detail = payload.get("detail")
    return detail if isinstance(detail, dict) else {}


def _error_code(payload: dict) -> str | None:
    code = _error_detail(payload).get("error_code")
    return str(code) if code else None


__all__ = [
    "RemoteFaceRecognitionEngine",
    "RemoteRecognitionAuthError",
    "RemoteRecognitionUnavailable",
]
