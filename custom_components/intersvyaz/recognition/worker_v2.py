"""Improved portable recognition worker for real doorphone camera frames.

This module reuses the proven detector/descriptor from ``worker.py`` but makes
matching more tolerant to imperfect face crops. A doorphone snapshot rarely
frames a face exactly like an enrollment photo: the head may be slightly higher,
lower, closer or farther from the camera. Instead of weakening the identity
threshold, we compare several nearby crops and keep the best distance.

The strict threshold and auto-open safety rules remain unchanged.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import worker as legacy

_ENGINE_ID = legacy._ENGINE_ID


def _variant_boxes(
    box: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
) -> list[tuple[int, int, int, int]]:
    """Return small crop variations around one detector box.

    Variants intentionally stay conservative. They compensate detector jitter
    without searching arbitrary parts of the frame, which would increase false
    matches.
    """

    left, top, right, bottom = box
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0

    variants: list[tuple[float, float]] = [
        (1.00, 0.00),
        (1.12, 0.00),
        (1.24, 0.00),
        (0.92, 0.00),
        (1.12, -0.08),
        (1.12, 0.08),
    ]
    result: list[tuple[int, int, int, int]] = []

    for scale, vertical_shift in variants:
        new_width = width * scale
        new_height = height * scale
        shifted_y = center_y + height * vertical_shift
        candidate = (
            max(0, int(round(center_x - new_width / 2.0))),
            max(0, int(round(shifted_y - new_height / 2.0))),
            min(image_width, int(round(center_x + new_width / 2.0))),
            min(image_height, int(round(shifted_y + new_height / 2.0))),
        )
        if candidate[2] - candidate[0] < 16 or candidate[3] - candidate[1] < 16:
            continue
        if candidate not in result:
            result.append(candidate)

    return result or [box]


def _recognize(request: dict[str, Any]) -> dict[str, object]:
    """Recognize using several nearby crops while preserving strict threshold."""

    image = legacy._decode_image(request.get("image"))
    candidates = legacy._detect_candidates(image)
    known_faces = legacy._parse_known_faces(request.get("known_faces"))

    if not candidates:
        return {
            "faces_detected": 0,
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
            "variants_tested": 0,
        }

    if not known_faces:
        return {
            "faces_detected": len(candidates),
            "matched_name": None,
            "distance": None,
            "engine": _ENGINE_ID,
            "auto_open_safe": False,
            "variants_tested": 0,
        }

    try:
        threshold = float(request.get("threshold", 0.30))
    except (TypeError, ValueError):
        threshold = 0.30
    threshold = max(0.10, min(0.55, threshold))

    best_name: str | None = None
    best_distance: float | None = None
    best_candidate = None
    variants_tested = 0

    for candidate in candidates:
        for variant_box in _variant_boxes(candidate.box, image.width, image.height):
            variants_tested += 1
            candidate_encoding = legacy._descriptor(
                legacy._normalized_crop(image, variant_box)
            )
            for name, known_encoding in known_faces:
                current_distance = legacy._distance(known_encoding, candidate_encoding)
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
            "variants_tested": variants_tested,
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
        "variants_tested": variants_tested,
    }


def _handle(request: dict[str, Any]) -> dict[str, object]:
    command = request.get("command")
    if command == "recognize":
        return _recognize(request)
    if command == "healthcheck":
        payload = legacy._handle(request)
        payload["matcher"] = "portable_multicrop_v2"
        payload["crop_variants"] = 6
        return payload
    return legacy._handle(request)


def main() -> int:
    """Run the same JSONL protocol as the original isolated worker."""

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
