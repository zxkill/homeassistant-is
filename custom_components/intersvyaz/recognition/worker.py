"""Изолированный dlib worker для интеграции Intersvyaz.

Этот файл запускается отдельным Python-процессом. Никогда не импортируйте его
из процесса Home Assistant: именно здесь находятся native imports dlib/numpy.
Протокол: одна JSON-строка на запрос и одна JSON-строка на ответ.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# ВАЖНО: native imports должны оставаться только в worker-процессе.
import dlib  # type: ignore
import numpy as np
from PIL import Image


_detector = None
_predictor = None
_encoder = None


def _resolve_models_directory() -> Path:
    """Найти модели без импорта устаревшего pkg_resources."""

    spec = importlib.util.find_spec("face_recognition_models")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("Пакет face-recognition-models не найден")
    return Path(next(iter(spec.submodule_search_locations))) / "models"


def _ensure_models() -> None:
    """Лениво инициализировать модели один раз на жизнь worker."""

    global _detector, _predictor, _encoder
    if _detector is not None:
        return

    models_dir = _resolve_models_directory()
    predictor_path = models_dir / "shape_predictor_5_face_landmarks.dat"
    recognition_path = models_dir / "dlib_face_recognition_resnet_model_v1.dat"
    if not predictor_path.is_file() or not recognition_path.is_file():
        raise RuntimeError(
            "Пакет face-recognition-models установлен, но файлы моделей не найдены"
        )

    _detector = dlib.get_frontal_face_detector()
    _predictor = dlib.shape_predictor(str(predictor_path))
    _encoder = dlib.face_recognition_model_v1(str(recognition_path))


def _decode_image(encoded: object) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Изображение не передано в recognition worker")
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as err:
        raise ValueError("Некорректное base64-изображение") from err


def _extract_encodings(image_bytes: bytes) -> list[list[float]]:
    """Найти лица и получить 128-мерные descriptors."""

    _ensure_models()
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_array = np.asarray(image.convert("RGB"))
    except Exception as err:
        raise ValueError(f"Не удалось загрузить изображение: {err}") from err

    try:
        faces = _detector(image_array, 1)
        result: list[list[float]] = []
        for face in faces:
            shape = _predictor(image_array, face)
            descriptor = _encoder.compute_face_descriptor(image_array, shape, 1)
            result.append([float(value) for value in descriptor])
        return result
    except Exception as err:
        raise RuntimeError(f"Ошибка распознавания лица: {err}") from err


def _euclidean_distance(left: Iterable[float], right: Iterable[float]) -> float:
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


def _extract_single_encoding(request: dict[str, Any]) -> dict[str, object]:
    encodings = _extract_encodings(_decode_image(request.get("image")))
    if not encodings:
        raise ValueError("На изображении не найдено лиц")
    if len(encodings) > 1:
        raise ValueError(
            "На изображении найдено несколько лиц. "
            "Загрузите фотографию только одного человека"
        )
    return {"encoding": encodings[0]}


def _recognize(request: dict[str, Any]) -> dict[str, object]:
    encodings = _extract_encodings(_decode_image(request.get("image")))
    if not encodings:
        return {"faces_detected": 0, "matched_name": None, "distance": None}

    known_raw = request.get("known_faces")
    known_faces: list[tuple[str, Sequence[float]]] = []
    if isinstance(known_raw, list):
        for item in known_raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            encoding = item.get("encoding")
            if not isinstance(name, str) or not isinstance(encoding, list):
                continue
            if len(encoding) != 128:
                continue
            known_faces.append((name, encoding))

    if not known_faces:
        return {
            "faces_detected": len(encodings),
            "matched_name": None,
            "distance": None,
        }

    try:
        threshold = float(request.get("threshold", 0.60))
    except (TypeError, ValueError):
        threshold = 0.60

    best_name: str | None = None
    best_distance: float | None = None
    for candidate in encodings:
        for name, known in known_faces:
            distance = _euclidean_distance(known, candidate)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_name = name

    if best_distance is None or best_distance > threshold:
        return {
            "faces_detected": len(encodings),
            "matched_name": None,
            "distance": best_distance,
        }

    return {
        "faces_detected": len(encodings),
        "matched_name": best_name,
        "distance": best_distance,
    }


def _handle(request: dict[str, Any]) -> dict[str, object]:
    command = request.get("command")
    if command == "extract_single_encoding":
        return _extract_single_encoding(request)
    if command == "recognize":
        return _recognize(request)
    raise ValueError(f"Неизвестная команда recognition worker: {command}")


def main() -> int:
    """Основной JSONL-цикл worker."""

    for raw_line in sys.stdin:
        request_id: object = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("Запрос worker должен быть JSON-объектом")
            request_id = request.get("request_id")

            if request.get("command") == "shutdown":
                response = {"ok": True, "request_id": request_id}
                print(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    flush=True,
                )
                return 0

            payload = _handle(request)
            response = {"ok": True, "request_id": request_id, **payload}
        except (ValueError, RuntimeError) as err:
            response = {
                "ok": False,
                "request_id": request_id,
                "error_code": "recognition_error",
                "error": str(err),
            }
        except Exception as err:
            # Не отправляем traceback через протокол: Home Assistant получает
            # безопасную ошибку, а worker остаётся жив, если это возможно.
            response = {
                "ok": False,
                "request_id": request_id,
                "error_code": "unexpected_error",
                "error": f"Неожиданная ошибка recognition worker: {type(err).__name__}: {err}",
            }

        print(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
