"""Isolated dlib ResNet face-recognition engine."""
from __future__ import annotations

import base64
import importlib.util
import logging
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from homeassistant.exceptions import HomeAssistantError

from ..const import FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX
from .engine import FaceRecognitionResult
from .engine import PortableFaceRecognitionEngine as _BaseWorkerEngine

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")


class DlibFaceRecognitionEngine(_BaseWorkerEngine):
    """Use dlib only inside a child process so native faults cannot crash HA."""

    engine_id = "dlib_resnet_v1"

    def __init__(
        self,
        model_dir: Path,
        *,
        timeout_seconds: float = 45.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self._model_dir = Path(model_dir)

    @property
    def available(self) -> bool:
        """Check Python modules without importing native dlib into Home Assistant."""

        if self._fatal_error is not None:
            return False
        return all(
            importlib.util.find_spec(module_name) is not None
            for module_name in ("dlib", "PIL", "numpy")
        )

    @property
    def models_available(self) -> bool:
        return all(
            (self._model_dir / filename).is_file()
            for filename in (
                "shape_predictor_5_face_landmarks.dat",
                "dlib_face_recognition_resnet_model_v1.dat",
            )
        )

    def recognize(
        self,
        image_bytes: bytes,
        known_faces: Sequence[tuple[str, Sequence[float]]],
        threshold: float,
    ) -> FaceRecognitionResult:
        """Recognize with a separate stricter ceiling for automatic opening."""

        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        response = self._rpc(
            {
                "command": "recognize",
                "image": base64.b64encode(image_bytes).decode("ascii"),
                "known_faces": [
                    {
                        "name": str(name),
                        "encoding": [float(value) for value in encoding],
                    }
                    for name, encoding in known_faces
                ],
                "threshold": float(threshold),
                "auto_open_threshold": FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX,
            }
        )

        try:
            faces_detected = max(int(response.get("faces_detected", 0)), 0)
        except (TypeError, ValueError):
            faces_detected = 0

        matched_name = response.get("matched_name")
        if not isinstance(matched_name, str) or not matched_name:
            matched_name = None

        distance_raw = response.get("distance")
        try:
            distance = float(distance_raw) if distance_raw is not None else None
        except (TypeError, ValueError):
            distance = None

        _LOGGER.info(
            "[FACE][COMPARE] engine=%s known_templates=%s faces=%s distance=%s "
            "threshold=%.3f auto_open_threshold=%.3f matched=%s "
            "detector_score=%s face_side=%s auto_open_safe=%s",
            self.engine_id,
            len(known_faces),
            faces_detected,
            f"{distance:.4f}" if distance is not None else "none",
            float(threshold),
            FACE_AUTO_OPEN_DISTANCE_THRESHOLD_MAX,
            bool(matched_name),
            response.get("best_detector_score", "none"),
            response.get("best_face_side", 0),
            bool(response.get("auto_open_safe", False)),
        )

        return FaceRecognitionResult(
            faces_detected=faces_detected,
            matched_name=matched_name,
            distance=distance,
            auto_open_safe=bool(response.get("auto_open_safe", False)),
        )

    def _ensure_worker_locked(self) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        if process is not None:
            self._process = None

        if not self.models_available:
            raise HomeAssistantError(
                "Модели dlib ResNet ещё не подготовлены. "
                "Повторите операцию после автоматической загрузки моделей."
            )

        worker_path = Path(__file__).with_name("dlib_worker.py")
        if not worker_path.is_file():
            raise HomeAssistantError(
                "Файл dlib recognition worker отсутствует в установленной интеграции"
            )

        _LOGGER.info(
            "[FACE][WORKER_START] engine=%s python=%s model_dir=%s",
            self.engine_id,
            sys.executable,
            self._model_dir.name,
        )
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    str(worker_path),
                    str(self._model_dir),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                close_fds=True,
            )
        except OSError as err:
            raise HomeAssistantError(
                f"Не удалось запустить dlib recognition worker: {err}"
            ) from err

        self._process = process
        return process


# Keep the old public names so the rest of the integration and third-party
# automations do not need to know which implementation is active.
PortableFaceRecognitionEngine = DlibFaceRecognitionEngine
OpenCvFaceRecognitionEngine = DlibFaceRecognitionEngine

__all__ = [
    "FaceRecognitionResult",
    "DlibFaceRecognitionEngine",
    "PortableFaceRecognitionEngine",
    "OpenCvFaceRecognitionEngine",
]
