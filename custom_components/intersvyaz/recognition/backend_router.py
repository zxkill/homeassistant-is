"""Runtime selection between remote, dlib ResNet and portable fallback."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .dlib_engine import DlibFaceRecognitionEngine
from .engine import FaceRecognitionResult
from .engine_v2 import PortableFaceRecognitionEngine as PortableFallbackEngine
from .remote_engine import (
    RemoteFaceRecognitionEngine,
    RemoteRecognitionAuthError,
    RemoteRecognitionUnavailable,
)

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")

_REMOTE_COOLDOWN_INITIAL_SECONDS = 15.0
_REMOTE_COOLDOWN_MAX_SECONDS = 300.0
_REMOTE_AUTH_COOLDOWN_SECONDS = 120.0


class RecognitionBackendSwitched(HomeAssistantError):
    """Raised when the active backend failed and the current request must be retried."""


class FaceRecognitionBackendRouter:
    """Prefer remote (if configured), then dlib, falling back to portable.

    Two very different failure modes are handled differently on purpose:

    * dlib -> portable is a *permanent* switch for the remainder of this HA
      run. A native SIGILL/SIGSEGV means the CPU/environment is genuinely
      incompatible with dlib; retrying costs nothing to try but never helps.
    * remote -> {dlib, portable} is *transient*. A self-hosted container can
      restart in seconds; a network blip can resolve on its own. So remote
      failures use an expiring cooldown and are retried automatically,
      instead of disabling remote for the rest of the run.
    """

    def __init__(self, hass: HomeAssistant, model_dir: Path) -> None:
        self._dlib = DlibFaceRecognitionEngine(model_dir)
        self._portable = PortableFallbackEngine()
        self._remote = RemoteFaceRecognitionEngine(hass)
        self._active = self._dlib
        self._fallback_reason: str | None = None
        self._dlib_probe_completed = False
        self._dlib_permanently_disabled = False
        self._remote_cooldown_until = 0.0
        self._remote_cooldown_seconds = _REMOTE_COOLDOWN_INITIAL_SECONDS

    def configure_remote(
        self,
        url: str | None,
        api_key: str | None,
        timeout_seconds: float,
    ) -> None:
        """Update remote connection settings; safe to call at any time."""

        self._remote.configure(url, api_key, timeout_seconds)
        if self._remote.configured:
            # Give remote an immediate chance on the very next request,
            # regardless of any earlier cooldown/backoff.
            self._remote_cooldown_until = 0.0
            self._remote_cooldown_seconds = _REMOTE_COOLDOWN_INITIAL_SECONDS

    def refresh_active_backend(self) -> None:
        """Re-evaluate which backend should be preferred right now.

        Call before inspecting using_remote/using_dlib/backend_name so a
        remote cooldown that has expired is picked back up automatically.
        """

        self._maybe_reactivate_remote()

    @property
    def engine_id(self) -> str:
        return self._active.engine_id

    @property
    def backend_name(self) -> str:
        if self._active is self._remote:
            return "remote_dlib"
        return "dlib_resnet" if self._active is self._dlib else "portable_multicrop"

    @property
    def available(self) -> bool:
        return self._active.available

    @property
    def using_dlib(self) -> bool:
        return self._active is self._dlib

    @property
    def using_remote(self) -> bool:
        return self._active is self._remote

    @property
    def using_portable(self) -> bool:
        return self._active is self._portable

    @property
    def remote_configured(self) -> bool:
        return self._remote.configured

    @property
    def dlib_available(self) -> bool:
        return self._dlib.available

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
        """Enroll using the current backend; cascade through fallbacks inline."""

        self._maybe_reactivate_remote()

        if self.using_remote:
            try:
                return self._remote.extract_single_encoding(image_bytes)
            except (RemoteRecognitionUnavailable, RemoteRecognitionAuthError) as err:
                self._enter_remote_cooldown(err)
                _LOGGER.warning(
                    "[FACE][ENROLL_RETRY] backend=%s reason=remote_unavailable",
                    self.backend_name,
                )
                # self._active now points at dlib or portable; fall through.

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
        """Recognize using the current backend.

        If the active backend fails, switch backend and tell the manager to
        rebuild the known-face list for the new engine's descriptor space
        before retrying.
        """

        self._maybe_reactivate_remote()

        if self.using_remote:
            try:
                return self._remote.recognize(image_bytes, known_faces, threshold)
            except (RemoteRecognitionUnavailable, RemoteRecognitionAuthError) as err:
                self._enter_remote_cooldown(err)
                raise RecognitionBackendSwitched(
                    "remote backend недоступен; запрос будет повторён через "
                    f"{self.backend_name}"
                ) from err

        if self.using_dlib:
            try:
                return self._dlib.recognize(image_bytes, known_faces, threshold)
            except HomeAssistantError as err:
                if self._dlib.fatal_error:
                    self.activate_portable(str(err))
                    raise RecognitionBackendSwitched(
                        "dlib backend аварийно завершился; "
                        "запрос будет повторён через portable fallback"
                    ) from err
                raise

        return self._portable.recognize(image_bytes, known_faces, threshold)

    def close(self) -> None:
        self._dlib.close()
        self._portable.close()
        self._remote.close()

    def activate_portable(self, reason: str) -> None:
        if self.using_portable:
            return

        self._fallback_reason = reason
        self._active = self._portable
        self._dlib_permanently_disabled = True
        _LOGGER.error(
            "[FACE][BACKEND_FALLBACK] from=dlib_resnet to=portable_multicrop "
            "reason=native_cpu_incompatibility details=%s",
            reason,
        )

    def _maybe_reactivate_remote(self) -> None:
        """Switch back to remote once it's configured and its cooldown has expired."""

        if self.using_remote or not self._remote.configured:
            return
        if time.monotonic() < self._remote_cooldown_until:
            return

        previous = self.backend_name
        self._active = self._remote
        _LOGGER.info(
            "[FACE][REMOTE_RETRY] engine=%s previous_backend=%s",
            self._remote.engine_id,
            previous,
        )

    def _enter_remote_cooldown(self, err: Exception) -> None:
        is_auth_error = isinstance(err, RemoteRecognitionAuthError)
        now = time.monotonic()

        if is_auth_error:
            cooldown = _REMOTE_AUTH_COOLDOWN_SECONDS
        else:
            cooldown = self._remote_cooldown_seconds
            self._remote_cooldown_seconds = min(
                self._remote_cooldown_seconds * 2, _REMOTE_COOLDOWN_MAX_SECONDS
            )
        self._remote_cooldown_until = now + cooldown

        self._active = self._portable if self._dlib_permanently_disabled else self._dlib

        _LOGGER.error(
            "[FACE][%s] from=remote_dlib to=%s reason=%s retry_after=%.0fs details=%s",
            "REMOTE_AUTH_FAILED" if is_auth_error else "REMOTE_FALLBACK",
            self.backend_name,
            "remote_auth_failure" if is_auth_error else "remote_unavailable",
            cooldown,
            err,
        )


__all__ = [
    "FaceRecognitionBackendRouter",
    "RecognitionBackendSwitched",
]
