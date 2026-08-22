"""Модели и безопасный парсинг ответов API Intersvyaz."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .api_errors import IntersvyazApiError, IntersvyazAuthError
from .const import TOKEN_EXPIRATION_MARGIN

_LOGGER = logging.getLogger("custom_components.intersvyaz.api_models")


@dataclass(slots=True)
class ConfirmContext:
    auth_id: str | None
    message: str | None
    timeout_mins: int | None
    timeout_default: int | None
    confirm_type: int | None


@dataclass(slots=True)
class ConfirmAddress:
    user_id: str
    address: str


@dataclass(slots=True)
class CheckConfirmResult:
    auth_id: str | None
    addresses: list[ConfirmAddress]
    message: str | None


@dataclass(slots=True)
class MobileToken:
    token: str
    user_id: int
    profile_id: int
    access_begin: datetime | None
    access_end: datetime | None
    phone: str | None
    unique_device_id: str | None
    raw: dict[str, Any]

    @property
    def is_expired(self) -> bool:
        if not self.access_end:
            return False
        return datetime.now(timezone.utc) >= self.access_end - timedelta(
            seconds=TOKEN_EXPIRATION_MARGIN
        )


@dataclass(slots=True)
class CrmToken:
    token: str
    user_id: int | None
    access_begin: datetime | None
    access_end: datetime | None
    raw: dict[str, Any]

    @property
    def is_expired(self) -> bool:
        if not self.access_end:
            return False
        return datetime.now(timezone.utc) >= self.access_end - timedelta(
            seconds=TOKEN_EXPIRATION_MARGIN
        )


@dataclass(slots=True)
class RelayOpener:
    relay_id: int | None
    relay_num: int | None
    mac: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"relay_id": self.relay_id, "relay_num": self.relay_num, "mac": self.mac}


@dataclass(slots=True)
class RelayInfo:
    address: str
    relay_id: str | None
    status_code: str | None
    building_id: str | None
    mac: str | None
    status_text: str | None
    is_main: bool
    has_video: bool
    entrance_uid: str | None
    porch_num: str | None
    relay_type: str | None
    relay_descr: str | None
    smart_intercom: bool | None
    num_building: str | None
    letter_building: str | None
    image_url: str | None
    open_link: str | None
    opener: RelayOpener | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        normalized = dict(self.raw)
        normalized.update(
            {
                "ADDRESS": self.address,
                "RELAY_ID": self.relay_id,
                "STATUS_CODE": self.status_code,
                "BUILDING_ID": self.building_id,
                "MAC_ADDR": self.mac,
                "STATUS_TEXT": self.status_text,
                "IS_MAIN": "1" if self.is_main else "0",
                "HAS_VIDEO": "1" if self.has_video else "0",
                "ENTRANCE_UID": self.entrance_uid,
                "PORCH_NUM": self.porch_num,
                "RELAY_TYPE": self.relay_type,
                "RELAY_DESCR": self.relay_descr,
                "SMART_INTERCOM": "1" if self.smart_intercom else "0",
                "NUM_BUILDING": self.num_building,
                "LETTER_BUILDING": self.letter_building,
                "IMAGE_URL": self.image_url,
                "OPEN_LINK": self.open_link,
                "OPENER": self.opener.to_dict() if self.opener else None,
            }
        )
        return normalized


def generate_device_id() -> str:
    return str(uuid.uuid4()).upper()


def safe_str(payload: Any, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    return optional_str(payload.get(key))


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def required_int(payload: dict[str, Any], key: str) -> int:
    value = optional_int(payload.get(key))
    if value is None:
        raise IntersvyazApiError(f"Поле {key} отсутствует или не является числом")
    return value


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    _LOGGER.debug("Не удалось распарсить дату API")
    return None


def parse_mobile_token(payload: dict[str, Any]) -> MobileToken:
    raw_token = payload.get("TOKEN")
    if not isinstance(raw_token, str) or not raw_token.strip():
        raise IntersvyazAuthError("В ответе отсутствует мобильный токен")
    return MobileToken(
        token=raw_token.strip(),
        user_id=required_int(payload, "USER_ID"),
        profile_id=required_int(payload, "PROFILE_ID"),
        access_begin=parse_datetime(payload.get("ACCESS_BEGIN")),
        access_end=parse_datetime(payload.get("ACCESS_END")),
        phone=str(payload["PHONE"]) if payload.get("PHONE") is not None else None,
        unique_device_id=str(payload["UNIQUE_DEVICE_ID"])
        if payload.get("UNIQUE_DEVICE_ID")
        else None,
        raw=dict(payload),
    )


def parse_crm_token(payload: dict[str, Any]) -> CrmToken:
    raw_token = payload.get("TOKEN")
    if not isinstance(raw_token, str) or not raw_token.strip():
        raise IntersvyazAuthError("В ответе отсутствует CRM токен")
    return CrmToken(
        token=raw_token.strip(),
        user_id=required_int(payload, "USER_ID") if "USER_ID" in payload else None,
        access_begin=parse_datetime(payload.get("ACCESS_BEGIN")),
        access_end=parse_datetime(payload.get("ACCESS_END")),
        raw=dict(payload),
    )


def parse_relay_info(payload: dict[str, Any]) -> RelayInfo:
    opener_payload = payload.get("OPENER")
    opener: RelayOpener | None = None
    if isinstance(opener_payload, dict):
        opener = RelayOpener(
            relay_id=optional_int(opener_payload.get("relay_id") or opener_payload.get("relayId")),
            relay_num=optional_int(opener_payload.get("relay_num") or opener_payload.get("relayNum")),
            mac=optional_str(opener_payload.get("mac")),
        )
    links = payload.get("LINKS")
    open_link = links.get("open") if isinstance(links, dict) else None
    relay = RelayInfo(
        address=str(payload.get("ADDRESS") or ""),
        relay_id=optional_str(payload.get("RELAY_ID")),
        status_code=optional_str(payload.get("STATUS_CODE")),
        building_id=optional_str(payload.get("BUILDING_ID")),
        mac=optional_str(payload.get("MAC_ADDR") or payload.get("mac")),
        status_text=optional_str(payload.get("STATUS_TEXT")),
        is_main=as_bool(payload.get("IS_MAIN")),
        has_video=as_bool(payload.get("HAS_VIDEO")),
        entrance_uid=optional_str(payload.get("ENTRANCE_UID")),
        porch_num=optional_str(payload.get("PORCH_NUM")),
        relay_type=optional_str(payload.get("RELAY_TYPE")),
        relay_descr=optional_str(payload.get("RELAY_DESCR")),
        smart_intercom=as_bool(payload.get("SMART_INTERCOM")),
        num_building=optional_str(payload.get("NUM_BUILDING")),
        letter_building=optional_str(payload.get("LETTER_BUILDING")),
        image_url=optional_str(payload.get("IMAGE_URL")),
        open_link=optional_str(open_link),
        opener=opener,
        raw=dict(payload),
    )
    _LOGGER.debug(
        "Распарсен домофон: main=%s video=%s status=%s address=<redacted>",
        relay.is_main,
        relay.has_video,
        relay.status_code,
    )
    return relay
