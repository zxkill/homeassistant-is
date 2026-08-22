"""Модели API камер двора Intersvyaz (cams.is74.ru)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class YardCameraInfo:
    """Одна камера, доступная аккаунту в разделе «Умный двор»."""

    camera_id: int | None
    uuid: str
    name: str
    address: str
    porch: str | None
    live_access: bool
    archive_access: bool
    movement_access: bool
    snapshot_url: str | None
    snapshot_lossy_url: str | None
    hls_url: str | None
    low_latency_hls_url: str | None
    archive_hls_url: str | None
    latitude: float | None
    longitude: float | None


@dataclass(slots=True)
class YardGroupInfo:
    """Группа камер одного двора/дома."""

    group_id: str
    name: str
    cameras: list[YardCameraInfo]


def parse_yard_groups(payload: Any) -> list[YardGroupInfo]:
    """Разобрать `/api/yard-with-group`, игнорируя неизвестные поля API."""

    if not isinstance(payload, list):
        return []

    groups: list[YardGroupInfo] = []
    for raw_group in payload:
        if not isinstance(raw_group, dict):
            continue
        cameras: list[YardCameraInfo] = []
        raw_cameras = raw_group.get("cameras")
        if isinstance(raw_cameras, list):
            for raw_camera in raw_cameras:
                camera = _parse_camera(raw_camera)
                if camera is not None:
                    cameras.append(camera)
        groups.append(
            YardGroupInfo(
                group_id=_text(raw_group.get("id")) or "unknown",
                name=_text(raw_group.get("groupName")) or "Intersvyaz yard",
                cameras=cameras,
            )
        )
    return groups


def _parse_camera(payload: Any) -> YardCameraInfo | None:
    if not isinstance(payload, dict):
        return None
    uuid = _text(payload.get("UUID"))
    if not uuid:
        return None

    access = payload.get("ACCESS") if isinstance(payload.get("ACCESS"), dict) else {}
    media = payload.get("MEDIA") if isinstance(payload.get("MEDIA"), dict) else {}
    snapshot = media.get("SNAPSHOT") if isinstance(media.get("SNAPSHOT"), dict) else {}
    snapshot_live = snapshot.get("LIVE") if isinstance(snapshot.get("LIVE"), dict) else {}
    hls = media.get("HLS") if isinstance(media.get("HLS"), dict) else {}
    hls_live = hls.get("LIVE") if isinstance(hls.get("LIVE"), dict) else {}
    position = payload.get("POSITION") if isinstance(payload.get("POSITION"), dict) else {}
    coordinates = payload.get("COORDINATES") if isinstance(payload.get("COORDINATES"), dict) else {}

    return YardCameraInfo(
        camera_id=_int(payload.get("ID")),
        uuid=uuid,
        name=_text(payload.get("NAME")) or "Intersvyaz camera",
        address=_text(payload.get("ADDRESS")) or "",
        porch=_text(payload.get("PORCH")),
        live_access=_access_status(access, "LIVE"),
        archive_access=_access_status(access, "ARCHIVE"),
        movement_access=_access_status(access, "MOVEMENT"),
        snapshot_url=_text(snapshot_live.get("MAIN")),
        snapshot_lossy_url=_text(snapshot_live.get("LOSSY")),
        hls_url=_text(hls_live.get("MAIN")),
        low_latency_hls_url=_text(hls_live.get("LOW_LATENCY")),
        archive_hls_url=_text(hls.get("ARCHIVE")),
        latitude=_float(position.get("LATITUDE") or coordinates.get("LATITUDE")),
        longitude=_float(position.get("LONGITUDE") or coordinates.get("LONGITUDE")),
    )


def _access_status(access: dict[str, Any], key: str) -> bool:
    item = access.get(key)
    if not isinstance(item, dict):
        return False
    return bool(item.get("STATUS"))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
