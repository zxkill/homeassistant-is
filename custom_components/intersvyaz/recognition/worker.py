"""Изолированный OpenCV worker для интеграции Intersvyaz.

Этот файл запускается отдельным Python-процессом. Никогда не импортируйте его
из процесса Home Assistant: именно здесь находятся native imports cv2/numpy.

Распознавание deliberately использует консервативный классический pipeline:
Haar face detector + нормализованный 128-мерный LBP descriptor. Он заметно
легче dlib/нейросетевых движков и не требует отдельного model package.
Протокол: одна JSON-строка на запрос и одна JSON-строка на ответ.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Sequence

# ВАЖНО: native imports должны оставаться только в worker-процессе.
import cv2  # type: ignore
import numpy as np

_ENGINE_ID = "opencv_lbp_v1"
_FACE_SIZE = 128
_GRID = 4
_BINS = 8
_DESCRIPTOR_SIZE = _GRID * _GRID * _BINS
_DISTANCE_SCALE = 3.0
_detector = None


def _configure_runtime() -> None:
    """Сделать native runtime предсказуемым и малоресурсным."""

    try:
        cv2.setNumThreads(1)
    except Exception:
        pass
    try:
        cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass


def _ensure_detector():
    """Лениво создать встроенный Haar detector OpenCV."""

    global _detector
    if _detector is not None:
        return _detector

    cascade_root = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if not cascade_root:
        raise RuntimeError("OpenCV не содержит путь к встроенным Haar-моделям")
    cascade_path = Path(cascade_root) / "haarcascade_frontalface_default.xml"
    if not cascade_path.is_file():
        raise RuntimeError("Встроенная Haar-модель лица OpenCV не найдена")

    detector = cv2.CascadeClassifier(str(cascade_path))
    if detector.empty():
        raise RuntimeError("Не удалось загрузить Haar-модель лица OpenCV")
    _detector = detector
    return detector


def _decode_image(encoded: object) -> np.ndarray:
    """Декодировать JPEG/PNG в grayscale без Pillow."""

    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Изображение не передано в recognition worker")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as err:
        raise ValueError("Некорректное base64-изображение") from err
    if not raw:
        raise ValueError("Передано пустое изображение")

    image_array = np.frombuffer(raw, dtype=np.uint8)
    gray = cv2.imdecode(image_array, cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.size == 0:
        raise ValueError("Не удалось декодировать изображение")
    return gray


def _detect_face_crops(gray: np.ndarray) -> list[np.ndarray]:
    """Найти лица и вернуть нормализованные grayscale crops."""

    detector = _ensure_detector()
    work = gray
    scale_back = 1.0
    height, width = work.shape[:2]
    largest = max(height, width)
    if largest > 1600:
        scale = 1600.0 / float(largest)
        work = cv2.resize(
            work,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        scale_back = 1.0 / scale

    equalized = cv2.equalizeHist(work)
    min_face = max(48, int(min(equalized.shape[:2]) * 0.08))
    faces = detector.detectMultiScale(
        equalized,
        scaleFactor=1.10,
        minNeighbors=5,
        minSize=(min_face, min_face),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )

    crops: list[np.ndarray] = []
    full_h, full_w = gray.shape[:2]
    for x, y, w, h in faces:
        x = int(round(float(x) * scale_back))
        y = int(round(float(y) * scale_back))
        w = int(round(float(w) * scale_back))
        h = int(round(float(h) * scale_back))

        margin_x = int(w * 0.12)
        margin_y = int(h * 0.15)
        left = max(0, x - margin_x)
        top = max(0, y - margin_y)
        right = min(full_w, x + w + margin_x)
        bottom = min(full_h, y + h + margin_y)
        crop = gray[top:bottom, left:right]
        if crop.size == 0:
            continue
        crop = cv2.resize(
            crop,
            (_FACE_SIZE, _FACE_SIZE),
            interpolation=cv2.INTER_AREA,
        )
        crop = cv2.equalizeHist(crop)
        crops.append(crop)

    return crops


def _lbp_descriptor(face: np.ndarray) -> list[float]:
    """Построить компактный 128-мерный spatial LBP descriptor."""

    if face.shape != (_FACE_SIZE, _FACE_SIZE):
        raise ValueError("Некорректный размер нормализованного лица")

    center = face[1:-1, 1:-1]
    lbp = np.zeros(center.shape, dtype=np.uint8)
    neighbors = (
        face[:-2, :-2],
        face[:-2, 1:-1],
        face[:-2, 2:],
        face[1:-1, 2:],
        face[2:, 2:],
        face[2:, 1:-1],
        face[2:, :-2],
        face[1:-1, :-2],
    )
    for bit, neighbor in enumerate(neighbors):
        lbp |= ((neighbor >= center).astype(np.uint8) << bit)

    quantized = (lbp >> 5).astype(np.uint8)  # 256 patterns -> 8 stable bins
    height, width = quantized.shape
    descriptor: list[float] = []
    for grid_y in range(_GRID):
        y0 = grid_y * height // _GRID
        y1 = (grid_y + 1) * height // _GRID
        for grid_x in range(_GRID):
            x0 = grid_x * width // _GRID
            x1 = (grid_x + 1) * width // _GRID
            cell = quantized[y0:y1, x0:x1]
            hist = np.bincount(cell.ravel(), minlength=_BINS).astype(np.float64)
            total = float(hist.sum())
            if total > 0:
                hist /= total
            # Hellinger transform немного уменьшает влияние освещения/контраста.
            hist = np.sqrt(hist)
            norm = float(np.linalg.norm(hist))
            if norm > 0:
                hist /= norm
            descriptor.extend(float(value) for value in hist)

    if len(descriptor) != _DESCRIPTOR_SIZE:
        raise RuntimeError(
            f"Некорректный размер LBP descriptor: {len(descriptor)}"
        )
    return descriptor


def _extract_encodings(image_bytes_b64: object) -> list[list[float]]:
    gray = _decode_image(image_bytes_b64)
    crops = _detect_face_crops(gray)
    return [_lbp_descriptor(crop) for crop in crops]


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Вернуть нормализованную chi-square distance в диапазоне 0..1."""

    if len(left) != _DESCRIPTOR_SIZE or len(right) != _DESCRIPTOR_SIZE:
        return 1.0
    left_arr = np.asarray(left, dtype=np.float64)
    right_arr = np.asarray(right, dtype=np.float64)
    denominator = left_arr + right_arr + 1e-12
    raw = 0.5 * float(np.sum(((left_arr - right_arr) ** 2) / denominator))
    raw /= float(_GRID * _GRID)
    return max(0.0, min(1.0, raw * _DISTANCE_SCALE))


def _extract_single_encoding(request: dict[str, Any]) -> dict[str, object]:
    encodings = _extract_encodings(request.get("image"))
    if not encodings:
        raise ValueError(
            "На изображении не найдено лицо. Используйте хорошо освещённую фотографию анфас"
        )
    if len(encodings) > 1:
        raise ValueError(
            "На изображении найдено несколько лиц. "
            "Загрузите фотографию только одного человека"
        )
    return {"encoding": encodings[0], "engine": _ENGINE_ID}


def _recognize(request: dict[str, Any]) -> dict[str, object]:
    encodings = _extract_encodings(request.get("image"))
    if not encodings:
        return {
            "faces_detected": 0,
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
        }

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
            if len(encoding) != _DESCRIPTOR_SIZE:
                continue
            try:
                normalized = [float(value) for value in encoding]
            except (TypeError, ValueError):
                continue
            known_faces.append((name, normalized))

    if not known_faces:
        return {
            "faces_detected": len(encodings),
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
        }

    try:
        threshold = float(request.get("threshold", 0.60))
    except (TypeError, ValueError):
        threshold = 0.60
    threshold = max(0.05, min(0.95, threshold))

    best_name: str | None = None
    best_distance: float | None = None
    for candidate in encodings:
        for name, known in known_faces:
            distance = _distance(known, candidate)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_name = name

    if best_distance is None or best_distance > threshold:
        return {
            "faces_detected": len(encodings),
            "matched_name": None,
            "distance": best_distance,
            "engine": _ENGINE_ID,
        }

    return {
        "faces_detected": len(encodings),
        "matched_name": best_name,
        "distance": best_distance,
        "engine": _ENGINE_ID,
    }


def _handle(request: dict[str, Any]) -> dict[str, object]:
    command = request.get("command")
    if command == "extract_single_encoding":
        return _extract_single_encoding(request)
    if command == "recognize":
        return _recognize(request)
    if command == "healthcheck":
        _ensure_detector()
        return {
            "engine": _ENGINE_ID,
            "opencv_version": str(getattr(cv2, "__version__", "unknown")),
            "descriptor_size": _DESCRIPTOR_SIZE,
        }
    raise ValueError(f"Неизвестная команда recognition worker: {command}")


def main() -> int:
    """Основной JSONL-цикл worker."""

    _configure_runtime()
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
