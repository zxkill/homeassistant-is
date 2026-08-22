"""Утилиты безопасного логирования и диагностики Intersvyaz."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "**REDACTED**"

_FULL_SECRET_KEYS = {
    "token",
    "mobile_token",
    "crm_token",
    "authorization",
    "confirmcode",
    "confirm_code",
    "sms_code",
    "code",
    "password",
    "authid",
    "auth_id",
    "face_encoding",
}

_PERSONAL_KEYS = {
    "phone",
    "phone_number",
    "deviceid",
    "device_id",
    "x-device-id",
    "unique_device_id",
    "address",
    "door_address",
    "mac",
    "mac_addr",
    "door_mac",
    "image_url",
    "door_image_url",
    "open_link",
    "door_open_link",
}

_PARTIAL_KEYS = {
    "user_id",
    "profile_id",
    "account",
    "account_num",
}


def safe_door_ref(door_uid: str | None) -> str:
    """Вернуть стабильную нераскрывающую ссылку на домофон для журналов.

    Runtime UID может содержать MAC-адрес для обратной совместимости entity IDs.
    В логах такой UID не выводим: короткий SHA-256 позволяет сопоставлять
    последовательность событий одного домофона без раскрытия идентификатора.
    """

    if not door_uid:
        return "door:unknown"
    digest = hashlib.sha256(str(door_uid).encode("utf-8")).hexdigest()[:10]
    return f"door:{digest}"


def mask_text(value: str, *, keep_ends: bool = False) -> str:
    """Замаскировать строку, при необходимости оставив края."""

    if not value:
        return REDACTED
    if not keep_ends or len(value) < 7:
        return REDACTED
    return f"{value[:2]}***{value[-2:]}"


def redact_value(key: str, value: Any) -> Any:
    """Рекурсивно очистить значение для безопасного вывода в лог/diagnostics."""

    normalized = str(key).lower().replace("-", "_")
    if normalized in {item.replace("-", "_") for item in _FULL_SECRET_KEYS}:
        return REDACTED
    if normalized.endswith("token") or normalized.endswith("_token"):
        return REDACTED
    if normalized in {item.replace("-", "_") for item in _PERSONAL_KEYS}:
        return REDACTED
    if normalized in _PARTIAL_KEYS:
        if value is None:
            return None
        return mask_text(str(value), keep_ends=True)

    if isinstance(value, Mapping):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [redact_value(key, item) for item in value]
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, set):
        return [redact_value(key, item) for item in sorted(value, key=str)]
    return value


def redact_mapping(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Вернуть безопасную копию словаря."""

    if not data:
        return {}
    return {str(key): redact_value(str(key), value) for key, value in data.items()}


def redact_url(url: str | None) -> str | None:
    """Скрыть host/query чувствительной временной ссылки, сохранив только путь."""

    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED

    # Для отладки полезна форма endpoint, но path тоже может содержать MAC,
    # account id, UUID или временную подпись. Динамические сегменты маскируем.
    path_segments = []
    for segment in parts.path.split("/"):
        dynamic = bool(
            re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", segment)
            or re.fullmatch(r"[0-9a-fA-F-]{20,}", segment)
            or re.fullmatch(r"\d{5,}", segment)
            or len(segment) > 40
        )
        path_segments.append(REDACTED if dynamic else segment)
    safe_path = "/".join(path_segments)
    safe_query = ""
    if parts.query:
        safe_query = urlencode([(key, REDACTED) for key, _ in parse_qsl(parts.query)])
    return urlunsplit((parts.scheme, REDACTED if parts.netloc else "", safe_path, safe_query, ""))


def sanitize_request_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Очистить HTTP request context перед сохранением и логированием."""

    sanitized = deepcopy(dict(context))
    if "url" in sanitized:
        sanitized["url"] = redact_url(str(sanitized["url"]))
    for section in ("headers", "json", "params"):
        value = sanitized.get(section)
        if isinstance(value, Mapping):
            sanitized[section] = redact_mapping(value)
    return sanitized


def safe_payload_summary(payload: Any) -> Any:
    """Сформировать короткий безопасный summary ответа API."""

    if isinstance(payload, Mapping):
        # Значения ответа принципиально не логируем: новые поля API могут неожиданно
        # содержать ФИО, адрес, телефон или credentials, которых sanitizer ещё не знает.
        return {
            "type": "dict",
            "keys": sorted(str(key) for key in payload.keys()),
            "count": len(payload),
        }
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return {"type": "list", "count": len(payload)}
    if isinstance(payload, str):
        return payload[:200]
    return payload


def redact_error_text(text: str, *, max_length: int = 500) -> str:
    """Безопасно подготовить серверную ошибку для журнала.

    Сначала пытаемся разобрать JSON и применить тот же рекурсивный sanitizer,
    который используется для diagnostics/request context. Для произвольного
    текста дополнительно скрываем bearer/JWT и телефоноподобные значения.
    """

    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        result = raw
    else:
        if isinstance(parsed, Mapping):
            result = json.dumps(redact_mapping(parsed), ensure_ascii=False)
        elif isinstance(parsed, list):
            result = json.dumps(
                [redact_value("item", item) for item in parsed],
                ensure_ascii=False,
            )
        else:
            result = str(parsed)

    result = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]+",
        f"Bearer {REDACTED}",
        result,
    )
    # JWT-подобные строки без префикса Bearer.
    result = re.sub(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b",
        REDACTED,
        result,
    )
    # Телефоноподобные последовательности 10–15 цифр с необязательным плюсом.
    result = re.sub(r"(?<!\d)\+?\d{10,15}(?!\d)", REDACTED, result)
    return result[:max_length]
