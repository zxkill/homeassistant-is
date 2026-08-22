"""Адаптер portable face worker v2 с расширенной безопасной диагностикой.

Descriptor format и engine_id намеренно остаются ``portable_face_v1``:
существующие сохранённые лица продолжают работать без миграции и повторной
загрузки фотографий. Меняется только стратегия сравнения runtime-кадра:
worker_v2 проверяет несколько близких вариантов crop лица.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from homeassistant.exceptions import HomeAssistantError

from .engine import FaceRecognitionResult
from .engine import PortableFaceRecognitionEngine as _BasePortableFaceRecognitionEngine

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")


class PortableFaceRecognitionEngine(_BasePortableFaceRecognitionEngine):
    """Portable engine v2, совместимый с descriptor portable_face_v1."""

    # Не меняем ID: формат 128-мерного descriptor остался тем же.
    engine_id = "portable_face_v1"

    def recognize(
        self,
        image_bytes: bytes,
        known_faces: Sequence[tuple[str, Sequence[float]]],
        threshold: float,
    ) -> FaceRecognitionResult:
        """Распознать лица и записать полезную диагностику результата сравнения."""

        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        response = self._rpc(
            {
                "command": "recognize",
                "image": __import__("base64").b64encode(image_bytes).decode("ascii"),
                "known_faces": [
                    {"name": str(name), "encoding": [float(value) for value in encoding]}
                    for name, encoding in known_faces
                ],
                "threshold": float(threshold),
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

        try:
            variants_tested = max(int(response.get("variants_tested", 0)), 0)
        except (TypeError, ValueError):
            variants_tested = 0

        _LOGGER.info(
            "[FACE][COMPARE] known=%s faces=%s variants=%s distance=%s threshold=%.3f "
            "matched=%s auto_open_safe=%s",
            len(known_faces),
            faces_detected,
            variants_tested,
            f"{distance:.4f}" if distance is not None else "none",
            float(threshold),
            bool(matched_name),
            bool(response.get("auto_open_safe", False)),
        )

        return FaceRecognitionResult(
            faces_detected=faces_detected,
            matched_name=matched_name,
            distance=distance,
            auto_open_safe=bool(response.get("auto_open_safe", False)),
        )

    def _ensure_worker_locked(self) -> subprocess.Popen[str]:
        """Запустить worker v2 с теми же изоляцией и протоколом, что у базового engine."""

        process = self._process
        if process is not None and process.poll() is None:
            return process

        if process is not None:
            self._process = None

        worker_path = Path(__file__).with_name("worker_v2.py")
        if not worker_path.is_file():
            raise HomeAssistantError(
                "Файл recognition worker v2 отсутствует в установленной интеграции"
            )

        _LOGGER.info(
            "[FACE][WORKER_START] version=multicrop_v2 python=%s engine=%s",
            sys.executable,
            self.engine_id,
        )
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", str(worker_path)],
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
                f"Не удалось запустить локальный recognition worker v2: {err}"
            ) from err

        self._process = process
        return process


# Совместимость со старым публичным именем интеграции.
OpenCvFaceRecognitionEngine = PortableFaceRecognitionEngine

__all__ = [
    "FaceRecognitionResult",
    "PortableFaceRecognitionEngine",
    "OpenCvFaceRecognitionEngine",
]
