"""Runtime selection between dlib ResNet and portable fallback."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from homeassistant.exceptions import HomeAssistantError

from .dlib_engine import DlibFaceRecognitionEngine
from .engine import FaceRecognitionResult
from .engine_v2 import PortableFaceRecognitionEngine as PortableFallbackEngine

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")


class RecognitionBackendSwitched(HomeAssistantError):
    """Raised when dlib crashed and the current request must be retried."""


class FaceRecognitionBackendRouter:
    """Prefer dlib, but permanently fall back for this HA run after native failure."""

    def __init__(self, model_dir: Path) -> None:
        self._dlib = DlibFaceRecognitionEngine(model_dir)
        self._portable = PortableFallbackEngine()
        self._active = self._dlib
        self._fallback_reason: str | None = None
        self._dlib_probe_completed = False

    @property
    def engine_id(self) -> str:
        return self._active.engine_id

    @property
    def backend_name(self) -> str:
        return "dlib_resnet" if self._active is self._dlib else "portable_multicrop"

    @property
    def available(self) -> bool:
        return self._active.available

    @property
    def using_dlib(self) -> bool:
        return self._active is self._dlib

    @property
    def dlib_available(self) -> bool:
        return self._dlib.available

    @property
    def using_portable(self) -> bool:
        return self._active is self._portable

    @property
    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    @property
    def models_available(self) -> bool:
        return bool(getattr(self._dlib, "models_available", False))

    def probe_dlib(self) -> bool:
        """Run a real detector + ResNet self-test once per HA process."""

        if not self.using_dlib:
            return False
        if self._dlib_probe_completed:
            return True

        try:
            self._dlib.probe()
        except HomeAssistantError as err:
            if self._dlib.fatal_error:
                self.activate_portable(str(err))
                return False
            raise

        self._dlib_probe_completed = True
        _LOGGER.info(
            "[FACE][BACKEND_PROBE_OK] backend=dlib_resnet engine=%s",
            self._dlib.engine_id,
        )
        return True

    def extract_single_encoding(self, image_bytes: bytes) -> list[float]:
        """Enroll using current backend; retry automatically after fatal dlib crash."""

        if self.using_dlib:
            try:
                return self._dlib.extract_single_encoding(image_bytes)
            except HomeAssistantError as err:
                if self._dlib.fatal_error:
                    self.activate_portable(str(err))
                    _LOGGER.warning(
                        "[FACE][ENROLL_RETRY] backend=portable_multicrop "
                        "reason=dlib_native_failure"
                    )
                    return self._portable.extract_single_encoding(image_bytes)
                raise

        return self._portable.extract_single_encoding(image_bytes)

    def recognize(
        self,
        image_bytes: bytes,
        known_faces: Sequence[tuple[str, Sequence[float]]],
        threshold: float,
    ) -> FaceRecognitionResult:
        """Recognize using current backend.

        If dlib dies natively, switch backend and tell the manager to rebuild the
        known-face list for the portable descriptor format before retrying.
        """

        if self.using_dlib:
            try:
                return self._dlib.recognize(
                    image_bytes,
                    known_faces,
                    threshold,
                )
            except HomeAssistantError as err:
                if self._dlib.fatal_error:
                    self.activate_portable(str(err))
                    raise RecognitionBackendSwitched(
                        "dlib backend аварийно завершился; "
                        "запрос будет повторён через portable fallback"
                    ) from err
                raise

        return self._portable.recognize(
            image_bytes,
            known_faces,
            threshold,
        )

    def close(self) -> None:
        self._dlib.close()
        self._portable.close()

    def activate_portable(self, reason: str) -> None:
        if self.using_portable:
            return

        self._fallback_reason = reason
        self._active = self._portable
        _LOGGER.error(
            "[FACE][BACKEND_FALLBACK] from=dlib_resnet to=portable_multicrop "
            "reason=native_cpu_incompatibility details=%s",
            reason,
        )


__all__ = [
    "FaceRecognitionBackendRouter",
    "RecognitionBackendSwitched",
]
