"""Локальное распознавание лиц без внешних сервисов.

Используется совместимый с dlib бинарный пакет ``dlib-bin`` и публичные
модели ``face-recognition-models``. Модели загружаются лениво при первом
использовании, чтобы обычная работа домофона не тратила память зря.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import math
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, Sequence

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")

try:
    import dlib  # type: ignore
    import numpy as np
    from PIL import Image
except ImportError as err:  # pragma: no cover - Home Assistant ставит requirements до загрузки
    dlib = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = err
else:
    _IMPORT_ERROR = None


@dataclass(frozen=True)
class FaceRecognitionResult:
    """Результат анализа одного кадра."""

    faces_detected: int
    matched_name: str | None = None
    distance: float | None = None

    @property
    def matched(self) -> bool:
        """Есть ли совпадение с известным лицом."""

        return self.matched_name is not None


class DlibFaceRecognitionEngine:
    """Небольшая обёртка над dlib для регистрации и сравнения лиц."""

    def __init__(self) -> None:
        self._detector = None
        self._predictor = None
        self._encoder = None

    @property
    def available(self) -> bool:
        """Готовы ли Python-зависимости движка."""

        return (
            _IMPORT_ERROR is None
            and importlib.util.find_spec("face_recognition_models") is not None
        )

    def extract_single_encoding(self, image_bytes: bytes) -> list[float]:
        """Получить вектор ровно одного лица из фотографии."""

        encodings = self.extract_encodings(image_bytes)
        if not encodings:
            raise HomeAssistantError("На изображении не найдено лиц")
        if len(encodings) > 1:
            raise HomeAssistantError(
                "На изображении найдено несколько лиц. Загрузите фотографию только одного человека"
            )
        return encodings[0]

    def recognize(
        self,
        image_bytes: bytes,
        known_faces: Sequence[tuple[str, Sequence[float]]],
        threshold: float,
    ) -> FaceRecognitionResult:
        """Найти лучшее совпадение среди известных лиц."""

        encodings = self.extract_encodings(image_bytes)
        if not encodings:
            return FaceRecognitionResult(faces_detected=0)
        if not known_faces:
            return FaceRecognitionResult(faces_detected=len(encodings))

        best_name: str | None = None
        best_distance: float | None = None

        for candidate_encoding in encodings:
            for name, known_encoding in known_faces:
                distance = self._euclidean_distance(known_encoding, candidate_encoding)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_name = name

        if best_distance is None or best_distance > float(threshold):
            return FaceRecognitionResult(
                faces_detected=len(encodings),
                distance=best_distance,
            )

        return FaceRecognitionResult(
            faces_detected=len(encodings),
            matched_name=best_name,
            distance=best_distance,
        )

    def extract_encodings(self, image_bytes: bytes) -> list[list[float]]:
        """Найти лица в кадре и вернуть dlib-дескрипторы."""

        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")
        self._ensure_ready()

        try:
            assert Image is not None
            assert np is not None
            with Image.open(io.BytesIO(image_bytes)) as image:
                rgb = image.convert("RGB")
                image_array = np.asarray(rgb)
        except Exception as err:
            raise HomeAssistantError(f"Не удалось загрузить изображение: {err}") from err

        try:
            faces = self._detector(image_array, 1)
            result: list[list[float]] = []
            for face in faces:
                shape = self._predictor(image_array, face)
                descriptor = self._encoder.compute_face_descriptor(image_array, shape, 1)
                result.append([float(value) for value in descriptor])
        except Exception as err:
            raise HomeAssistantError(f"Ошибка распознавания лица: {err}") from err

        _LOGGER.debug("На изображении найдено лиц: %s", len(result))
        return result

    def _ensure_ready(self) -> None:
        """Лениво загрузить модели dlib."""

        if _IMPORT_ERROR is not None:
            raise HomeAssistantError(
                "Не удалось загрузить локальный движок распознавания лиц: "
                f"{_IMPORT_ERROR}"
            ) from _IMPORT_ERROR

        if self._detector is not None:
            return

        try:
            assert dlib is not None
            models_dir = self._resolve_models_directory()
            predictor_path = models_dir / "shape_predictor_5_face_landmarks.dat"
            recognition_path = models_dir / "dlib_face_recognition_resnet_model_v1.dat"
            if not predictor_path.is_file() or not recognition_path.is_file():
                raise FileNotFoundError(
                    "Пакет face-recognition-models установлен, но файлы моделей не найдены"
                )
            self._detector = dlib.get_frontal_face_detector()
            self._predictor = dlib.shape_predictor(str(predictor_path))
            self._encoder = dlib.face_recognition_model_v1(str(recognition_path))
        except Exception as err:
            raise HomeAssistantError(
                f"Не удалось инициализировать модели распознавания лиц: {err}"
            ) from err

        _LOGGER.info("Локальный движок распознавания лиц Intersvyaz инициализирован")

    @staticmethod
    def _resolve_models_directory() -> Path:
        """Найти каталог моделей без импорта устаревшего pkg_resources."""

        spec = importlib.util.find_spec("face_recognition_models")
        if spec is None or not spec.submodule_search_locations:
            raise HomeAssistantError(
                "Пакет face-recognition-models не найден в окружении Home Assistant"
            )
        package_dir = Path(next(iter(spec.submodule_search_locations)))
        return package_dir / "models"

    @staticmethod
    def _euclidean_distance(left: Iterable[float], right: Iterable[float]) -> float:
        """Рассчитать евклидово расстояние между двумя дескрипторами."""

        left_values = [float(value) for value in left]
        right_values = [float(value) for value in right]
        if len(left_values) != len(right_values):
            return float("inf")
        return math.sqrt(
            sum(
                (left_value - right_value) ** 2
                for left_value, right_value in zip(left_values, right_values)
            )
        )
