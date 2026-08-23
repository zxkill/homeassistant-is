"""Child process for dlib HOG + 5-point alignment + ResNet embeddings."""
from __future__ import annotations

import base64
from io import BytesIO
import json
import math
from pathlib import Path
import sys
from typing import Any

import dlib
import numpy as np
from PIL import Image, ImageOps

_ENGINE_ID = "dlib_resnet_v1"
_DESCRIPTOR_SIZE = 128
_MAX_IMAGE_SIDE = 900
_MIN_ENROLL_FACE_SIDE = 60
_MIN_AUTO_OPEN_FACE_SIDE = 55

_MODEL_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path()
_LANDMARK_MODEL = _MODEL_DIR / "shape_predictor_5_face_landmarks.dat"
_RECOGNITION_MODEL = _MODEL_DIR / "dlib_face_recognition_resnet_model_v1.dat"

if not _LANDMARK_MODEL.is_file() or not _RECOGNITION_MODEL.is_file():
    raise RuntimeError("Не найдены локальные модели dlib face recognition")

_detector = dlib.get_frontal_face_detector()
_shape_predictor = dlib.shape_predictor(str(_LANDMARK_MODEL))
_face_encoder = dlib.face_recognition_model_v1(str(_RECOGNITION_MODEL))


def _decode_image(encoded: object) -> np.ndarray:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Изображение не передано")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as err:
        raise ValueError("Некорректное base64-изображение") from err
    if not raw:
        raise ValueError("Изображение пустое")

    try:
        with Image.open(BytesIO(raw)) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            if max(source.size) > _MAX_IMAGE_SIDE:
                source.thumbnail(
                    (_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE),
                    Image.Resampling.LANCZOS,
                )
            image = np.asarray(source, dtype=np.uint8)
    except Exception as err:
        raise ValueError("Не удалось декодировать изображение") from err

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Ожидалось RGB-изображение")
    return np.ascontiguousarray(image)


def _detect_faces(image: np.ndarray) -> list[tuple[Any, float]]:
    rectangles, scores, _ = _detector.run(image, 1, 0.0)
    return [(rect, float(score)) for rect, score in zip(rectangles, scores)]


def _face_side(rect: Any) -> int:
    return max(0, min(int(rect.width()), int(rect.height())))


def _descriptor(
    image: np.ndarray,
    rect: Any,
    *,
    num_jitters: int,
) -> list[float]:
    shape = _shape_predictor(image, rect)
    vector = _face_encoder.compute_face_descriptor(
        image,
        shape,
        max(int(num_jitters), 0),
        0.25,
    )
    result = [float(value) for value in vector]
    if len(result) != _DESCRIPTOR_SIZE or not all(math.isfinite(v) for v in result):
        raise ValueError("dlib вернул повреждённый face descriptor")
    return result


def _parse_known_faces(raw: object) -> list[tuple[str, np.ndarray]]:
    if not isinstance(raw, list):
        return []
    result: list[tuple[str, np.ndarray]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        encoding = item.get("encoding")
        if not isinstance(name, str) or not name or not isinstance(encoding, list):
            continue
        if len(encoding) != _DESCRIPTOR_SIZE:
            continue
        try:
            vector = np.asarray([float(value) for value in encoding], dtype=np.float64)
        except (TypeError, ValueError):
            continue
        if vector.shape != (_DESCRIPTOR_SIZE,) or not np.isfinite(vector).all():
            continue
        result.append((name, vector))
    return result


def _extract_single_encoding(request: dict[str, Any]) -> dict[str, object]:
    image = _decode_image(request.get("image"))
    faces = _detect_faces(image)
    if not faces:
        raise ValueError("На фотографии не найдено лицо")
    if len(faces) != 1:
        raise ValueError(
            f"Для эталона требуется ровно одно лицо; найдено: {len(faces)}"
        )

    rect, score = faces[0]
    side = _face_side(rect)
    if side < _MIN_ENROLL_FACE_SIDE:
        raise ValueError(
            "Лицо на фотографии слишком маленькое. "
            "Используйте более крупный и чёткий снимок."
        )

    encoding = _descriptor(image, rect, num_jitters=2)
    return {
        "encoding": encoding,
        "engine": _ENGINE_ID,
        "faces_detected": 1,
        "detector_score": score,
        "face_side": side,
    }


def _recognize(request: dict[str, Any]) -> dict[str, object]:
    image = _decode_image(request.get("image"))
    faces = _detect_faces(image)
    known_faces = _parse_known_faces(request.get("known_faces"))

    try:
        threshold = float(request.get("threshold", 0.52))
    except (TypeError, ValueError):
        threshold = 0.52
    threshold = max(0.25, min(0.70, threshold))

    try:
        auto_open_threshold = float(request.get("auto_open_threshold", 0.50))
    except (TypeError, ValueError):
        auto_open_threshold = 0.50
    auto_open_threshold = max(0.25, min(threshold, auto_open_threshold))

    if not faces:
        return {
            "faces_detected": 0,
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
            "best_detector_score": None,
            "best_face_side": 0,
        }

    if not known_faces:
        return {
            "faces_detected": len(faces),
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
            "best_detector_score": None,
            "best_face_side": 0,
        }

    best_name: str | None = None
    best_distance: float | None = None
    best_detector_score: float | None = None
    best_face_side = 0

    for rect, detector_score in faces:
        candidate = np.asarray(
            _descriptor(image, rect, num_jitters=1),
            dtype=np.float64,
        )
        for name, known in known_faces:
            distance = float(np.linalg.norm(known - candidate))
            if best_distance is None or distance < best_distance:
                best_name = name
                best_distance = distance
                best_detector_score = detector_score
                best_face_side = _face_side(rect)

    matched = (
        best_name is not None
        and best_distance is not None
        and best_distance <= threshold
    )
    if not matched:
        best_name = None

    auto_open_safe = bool(
        matched
        and best_distance is not None
        and best_distance <= auto_open_threshold
        and len(faces) == 1
        and best_face_side >= _MIN_AUTO_OPEN_FACE_SIDE
        and (best_detector_score or 0.0) >= 0.0
    )

    return {
        "faces_detected": len(faces),
        "matched_name": best_name,
        "distance": best_distance,
        "engine": _ENGINE_ID,
        "auto_open_safe": auto_open_safe,
        "best_detector_score": best_detector_score,
        "best_face_side": best_face_side,
    }


def _healthcheck() -> dict[str, object]:
    return {
        "engine": _ENGINE_ID,
        "descriptor_size": _DESCRIPTOR_SIZE,
        "detector": "dlib_hog_frontal",
        "landmarks": "dlib_5_point",
        "recognizer": "dlib_resnet_29",
        "dlib_version": getattr(dlib, "__version__", "unknown"),
        "models_ready": True,
    }


def _handle(request: dict[str, Any]) -> dict[str, object]:
    command = request.get("command")
    if command == "extract_single_encoding":
        return _extract_single_encoding(request)
    if command == "recognize":
        return _recognize(request)
    if command == "healthcheck":
        return _healthcheck()
    raise ValueError(f"Неизвестная команда worker: {command}")


def main() -> int:
    for raw_line in sys.stdin:
        request_id: object = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("Запрос worker должен быть JSON-объектом")
            request_id = request.get("request_id")

            if request.get("command") == "shutdown":
                print(
                    json.dumps(
                        {"ok": True, "request_id": request_id},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                return 0

            payload = _handle(request)
            response = {"ok": True, "request_id": request_id, **payload}
        except Exception as err:
            response = {
                "ok": False,
                "request_id": request_id,
                "error_code": err.__class__.__name__,
                "error": str(err),
            }

        print(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
