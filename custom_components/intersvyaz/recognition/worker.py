"""Изолированный переносимый worker распознавания лиц Intersvyaz.

Worker использует только Pillow (уже входит в Home Assistant Core) и NumPy.
OpenCV/dlib/onnxruntime здесь намеренно отсутствуют: официальные контейнеры
Home Assistant основаны на Alpine/musl, а многие компьютерно-зрительные
пакеты публикуют только glibc/manylinux wheels и начинают собираться из
исходников прямо внутри Home Assistant.

Алгоритм лёгкий и консервативный:
* цветовая сегментация кожи ищет правдоподобные области лица;
* нормализованный квадратный crop строит 128-мерный mirror-invariant descriptor;
* descriptor сочетает низкочастотную яркость и карту градиентов;
* для auto-open пригодны только кандидаты, прошедшие цветовую проверку лица.

Это локальный lightweight recognizer, а не security-grade biometric system и
не содержит liveness detection. Auto-open остаётся явным opt-in режимом.
Протокол: одна JSON-строка на запрос и одна JSON-строка на ответ.
"""
from __future__ import annotations

import base64
import io
import json
import math
import sys
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageOps, __version__ as PILLOW_VERSION

_ENGINE_ID = "portable_face_v1"
_DESCRIPTOR_SIZE = 128
_FACE_SIZE = 96
_MAX_DETECTION_SIDE = 640
_BLOCK = 4
_DISTANCE_SCALE = 3.0


@dataclass(frozen=True, slots=True)
class _Candidate:
    box: tuple[int, int, int, int]
    score: float
    skin_density: float
    source: str


def _decode_image(encoded: object) -> Image.Image:
    """Декодировать JPEG/PNG/WebP через Pillow с EXIF orientation."""

    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Изображение не передано в recognition worker")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as err:
        raise ValueError("Некорректное base64-изображение") from err
    if not raw:
        raise ValueError("Передано пустое изображение")

    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except Exception as err:
        raise ValueError(f"Не удалось декодировать изображение: {err}") from err

    if image.width < 32 or image.height < 32:
        raise ValueError("Изображение слишком маленькое для распознавания")
    return image


def _resize_for_detection(image: Image.Image) -> tuple[Image.Image, float]:
    """Уменьшить большой кадр, вернув scale относительно исходника."""

    largest = max(image.size)
    if largest <= _MAX_DETECTION_SIDE:
        return image, 1.0
    scale = _MAX_DETECTION_SIDE / float(largest)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.BILINEAR,
    )
    return resized, scale


def _skin_mask(image: Image.Image) -> np.ndarray:
    """Консервативная RGB-маска кожи, отсекающая насыщенную одежду/фон."""

    array = np.asarray(image, dtype=np.float32) / 255.0
    red = array[:, :, 0]
    green = array[:, :, 1]
    blue = array[:, :, 2]
    maximum = array.max(axis=2)
    minimum = array.min(axis=2)
    saturation = np.zeros_like(maximum)
    nonzero = maximum > 1e-6
    saturation[nonzero] = (
        (maximum[nonzero] - minimum[nonzero]) / maximum[nonzero]
    )

    # Классическое RGB skin rule + ограничение насыщенности. Нам важнее
    # пропустить сомнительный кадр, чем принять яркую одежду за лицо.
    return (
        (red > 95.0 / 255.0)
        & (green > 40.0 / 255.0)
        & (blue > 20.0 / 255.0)
        & ((maximum - minimum) > 15.0 / 255.0)
        & (np.abs(red - green) > 15.0 / 255.0)
        & (red > green)
        & (red > blue)
        & (saturation > 0.06)
        & (saturation < 0.58)
    )


def _neighbor_sum(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask.astype(np.uint8), 1)
    result = np.zeros(mask.shape, dtype=np.uint8)
    for dy in range(3):
        for dx in range(3):
            result += padded[
                dy : dy + mask.shape[0],
                dx : dx + mask.shape[1],
            ]
    return result


def _connected_components(mask: np.ndarray) -> list[tuple[int, int, int, int, float, int]]:
    """Найти связные skin-компоненты на уменьшенной block-grid маске."""

    height, width = mask.shape
    usable_h = (height // _BLOCK) * _BLOCK
    usable_w = (width // _BLOCK) * _BLOCK
    if usable_h < _BLOCK or usable_w < _BLOCK:
        return []

    coarse = mask[:usable_h, :usable_w].reshape(
        usable_h // _BLOCK,
        _BLOCK,
        usable_w // _BLOCK,
        _BLOCK,
    ).mean(axis=(1, 3))
    active = coarse > 0.25
    # Два дешёвых прохода закрывают небольшие дырки в маске лица.
    active = _neighbor_sum(active) >= 2
    active = _neighbor_sum(active) >= 3

    rows, cols = active.shape
    seen = np.zeros_like(active, dtype=bool)
    components: list[tuple[int, int, int, int, float, int]] = []

    for row in range(rows):
        for col in range(cols):
            if not active[row, col] or seen[row, col]:
                continue
            stack = [(row, col)]
            seen[row, col] = True
            points: list[tuple[int, int]] = []
            while stack:
                current_row, current_col = stack.pop()
                points.append((current_row, current_col))
                for delta_row, delta_col in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    next_row = current_row + delta_row
                    next_col = current_col + delta_col
                    if (
                        0 <= next_row < rows
                        and 0 <= next_col < cols
                        and active[next_row, next_col]
                        and not seen[next_row, next_col]
                    ):
                        seen[next_row, next_col] = True
                        stack.append((next_row, next_col))

            if len(points) < 4:
                continue
            point_rows = [point[0] for point in points]
            point_cols = [point[1] for point in points]
            left = min(point_cols) * _BLOCK
            top = min(point_rows) * _BLOCK
            right = min(width, (max(point_cols) + 1) * _BLOCK)
            bottom = min(height, (max(point_rows) + 1) * _BLOCK)
            region = mask[top:bottom, left:right]
            density = float(region.mean()) if region.size else 0.0
            components.append(
                (left, top, right, bottom, density, len(points))
            )

    return components


def _iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx0, ly0, lx1, ly1 = left
    rx0, ry0, rx1, ry1 = right
    ix0, iy0 = max(lx0, rx0), max(ly0, ry0)
    ix1, iy1 = min(lx1, rx1), min(ly1, ry1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = float((ix1 - ix0) * (iy1 - iy0))
    union = float((lx1 - lx0) * (ly1 - ly0) + (rx1 - rx0) * (ry1 - ry0)) - intersection
    return intersection / union if union > 0 else 0.0


def _detect_candidates(image: Image.Image) -> list[_Candidate]:
    """Найти консервативные face-like skin компоненты."""

    work, scale = _resize_for_detection(image)
    mask = _skin_mask(work)
    width, height = work.size
    total_area = float(width * height)
    candidates: list[_Candidate] = []

    for left, top, right, bottom, density, _cells in _connected_components(mask):
        box_width = right - left
        box_height = bottom - top
        if box_width < max(28, int(width * 0.05)):
            continue
        if box_height < max(34, int(height * 0.06)):
            continue

        aspect = box_width / max(float(box_height), 1.0)
        area_ratio = (box_width * box_height) / total_area
        if not 0.52 <= aspect <= 1.35:
            continue
        if not 0.006 <= area_ratio <= 0.30:
            continue
        if density < 0.24:
            continue

        # Компонент, зажатый сразу в два края картинки, чаще является фоном/одеждой.
        edge_hits = sum(
            (
                left <= 4,
                top <= 4,
                right >= width - 4,
                bottom >= height - 4,
            )
        )
        if edge_hits >= 2:
            continue

        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        center_distance = math.hypot(
            (center_x - width / 2.0) / max(width / 2.0, 1.0),
            (center_y - height * 0.42) / max(height * 0.58, 1.0),
        )
        shape_score = 1.0 - min(abs(aspect - 0.82) / 0.82, 1.0)
        size_score = min(area_ratio / 0.05, 1.0)
        score = (
            2.0 * density
            + 0.45 * shape_score
            + 0.25 * size_score
            - 0.25 * center_distance
        )

        if scale != 1.0:
            inverse = 1.0 / scale
            box = tuple(
                int(round(value * inverse))
                for value in (left, top, right, bottom)
            )
        else:
            box = (left, top, right, bottom)
        candidates.append(
            _Candidate(
                box=box,
                score=float(score),
                skin_density=density,
                source="skin",
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    selected: list[_Candidate] = []
    for candidate in candidates:
        if any(_iou(candidate.box, existing.box) > 0.35 for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= 4:
            break
    return selected


def _normalized_crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Сделать квадратный grayscale crop лица фиксированного размера."""

    left, top, right, bottom = box
    box_width = max(right - left, 1)
    box_height = max(bottom - top, 1)
    side = max(box_width, box_height) * 1.18
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0 - 0.02 * box_height

    crop_left = max(0, int(round(center_x - side / 2.0)))
    crop_top = max(0, int(round(center_y - side / 2.0)))
    crop_right = min(image.width, int(round(center_x + side / 2.0)))
    crop_bottom = min(image.height, int(round(center_y + side / 2.0)))

    crop = image.crop((crop_left, crop_top, crop_right, crop_bottom)).convert("L")
    crop = ImageOps.pad(
        crop,
        (_FACE_SIZE, _FACE_SIZE),
        method=Image.Resampling.LANCZOS,
        color=0,
        centering=(0.5, 0.5),
    )
    return ImageOps.equalize(crop)


def _mirror_features(matrix: np.ndarray) -> np.ndarray:
    """Сделать признаки устойчивыми к зеркальному отражению камеры."""

    left = matrix[:, : matrix.shape[1] // 2]
    right = np.fliplr(matrix[:, matrix.shape[1] // 2 :])
    average = (left + right) / 2.0
    difference = np.abs(left - right)
    return np.concatenate((average.ravel(), difference.ravel()))


def _descriptor(face: Image.Image) -> list[float]:
    """Построить 128-мерный portable descriptor лица."""

    gray = np.asarray(face, dtype=np.float32) / 255.0

    raw = np.asarray(
        face.resize((8, 8), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    raw = (raw - float(raw.mean())) / (float(raw.std()) + 1e-6)

    gradient_x = np.zeros_like(gray)
    gradient_y = np.zeros_like(gray)
    gradient_x[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gradient_y[1:-1, :] = gray[2:, :] - gray[:-2, :]
    magnitude = np.hypot(gradient_x, gradient_y)
    maximum = float(magnitude.max())
    if maximum > 1e-9:
        magnitude /= maximum
    magnitude_image = Image.fromarray(
        np.uint8(np.clip(magnitude * 255.0, 0.0, 255.0)),
        mode="L",
    )
    gradient = np.asarray(
        magnitude_image.resize((8, 8), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    gradient = (
        gradient - float(gradient.mean())
    ) / (float(gradient.std()) + 1e-6)

    vector = np.concatenate((_mirror_features(raw), _mirror_features(gradient)))
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-9:
        raise ValueError("Фотография лица не содержит достаточно деталей")
    vector /= norm
    if vector.size != _DESCRIPTOR_SIZE:
        raise RuntimeError(
            f"Некорректный размер descriptor: {vector.size}"
        )
    return [float(value) for value in vector]


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine distance, масштабированная в привычный диапазон 0..1."""

    if len(left) != _DESCRIPTOR_SIZE or len(right) != _DESCRIPTOR_SIZE:
        return 1.0
    left_array = np.asarray(left, dtype=np.float64)
    right_array = np.asarray(right, dtype=np.float64)
    denominator = float(np.linalg.norm(left_array) * np.linalg.norm(right_array))
    if denominator <= 1e-12:
        return 1.0
    cosine = float(np.dot(left_array, right_array) / denominator)
    raw = max(0.0, min(1.0, (1.0 - cosine) / 2.0))
    return max(0.0, min(1.0, raw * _DISTANCE_SCALE))


def _extract_single_encoding(request: dict[str, Any]) -> dict[str, object]:
    image = _decode_image(request.get("image"))
    candidates = _detect_candidates(image)
    if not candidates:
        raise ValueError(
            "На изображении не найдено лицо. Используйте цветную, хорошо освещённую "
            "фотографию анфас, где лицо занимает заметную часть кадра"
        )

    best = candidates[0]
    # Если два кандидата почти равнозначны, не угадываем, кого регистрировать.
    if len(candidates) > 1 and candidates[1].score >= best.score * 0.88:
        raise ValueError(
            "На изображении найдено несколько возможных лиц. "
            "Загрузите фотографию только одного человека"
        )

    encoding = _descriptor(_normalized_crop(image, best.box))
    return {
        "encoding": encoding,
        "engine": _ENGINE_ID,
        "face_source": best.source,
        "skin_density": round(best.skin_density, 4),
    }


def _parse_known_faces(raw: object) -> list[tuple[str, Sequence[float]]]:
    result: list[tuple[str, Sequence[float]]] = []
    if not isinstance(raw, list):
        return result
    for item in raw:
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
        result.append((name, normalized))
    return result


def _recognize(request: dict[str, Any]) -> dict[str, object]:
    image = _decode_image(request.get("image"))
    candidates = _detect_candidates(image)
    known_faces = _parse_known_faces(request.get("known_faces"))

    if not candidates:
        return {
            "faces_detected": 0,
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
        }

    if not known_faces:
        return {
            "faces_detected": len(candidates),
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
        }

    try:
        threshold = float(request.get("threshold", 0.30))
    except (TypeError, ValueError):
        threshold = 0.30
    threshold = max(0.10, min(0.55, threshold))

    best_name: str | None = None
    best_distance: float | None = None
    best_candidate: _Candidate | None = None
    for candidate in candidates:
        candidate_encoding = _descriptor(_normalized_crop(image, candidate.box))
        for name, known_encoding in known_faces:
            current_distance = _distance(known_encoding, candidate_encoding)
            if best_distance is None or current_distance < best_distance:
                best_distance = current_distance
                best_name = name
                best_candidate = candidate

    if best_distance is None or best_distance > threshold:
        return {
            "faces_detected": len(candidates),
            "matched_name": None,
            "distance": best_distance,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
        }

    auto_open_safe = bool(
        best_candidate is not None
        and best_candidate.source == "skin"
        and best_candidate.skin_density >= 0.28
        and len(candidates) == 1
    )
    return {
        "faces_detected": len(candidates),
        "matched_name": best_name,
        "distance": best_distance,
        "engine": _ENGINE_ID,
        "auto_open_safe": auto_open_safe,
    }


def _handle(request: dict[str, Any]) -> dict[str, object]:
    command = request.get("command")
    if command == "extract_single_encoding":
        return _extract_single_encoding(request)
    if command == "recognize":
        return _recognize(request)
    if command == "healthcheck":
        return {
            "engine": _ENGINE_ID,
            "descriptor_size": _DESCRIPTOR_SIZE,
            "pillow_version": PILLOW_VERSION,
            "numpy_version": str(np.__version__),
            "detector": "portable_skin_components_v1",
        }
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
