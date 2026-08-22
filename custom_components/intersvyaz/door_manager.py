"""Обнаружение, обновление и управление физическими домофонами Intersvyaz."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .api import IntersvyazApiClient, IntersvyazApiError, IntersvyazAuthError, RelayInfo
from .const import (
    CONF_CRM_TOKEN,
    CONF_DOOR_ADDRESS,
    CONF_DOOR_ENTRANCE,
    CONF_DOOR_HAS_VIDEO,
    CONF_DOOR_IMAGE_URL,
    CONF_DOOR_MAC,
    CONF_DOOR_OPEN_LINK,
    CONF_MOBILE_TOKEN,
    CONF_RELAY_ID,
    CONF_RELAY_NUM,
    DOOR_EVENT_OPENED,
    DOOR_EVENT_OPEN_FAILED,
    DOOR_LINK_REFRESH_INTERVAL_HOURS,
)
from .events import emit_door_event
from .models import DoorRuntime
from .runtime import IntersvyazConfigEntry
from .security import safe_door_ref

_LOGGER = logging.getLogger("custom_components.intersvyaz.door_manager")


class DoorManager:
    """Поддерживает актуальный список домофонов и временных ссылок."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IntersvyazConfigEntry,
        api: IntersvyazApiClient,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._api = api
        self._doors: list[DoorRuntime] = []
        self._refresh_unsub = None
        self._reload_scheduled = False

    @property
    def doors(self) -> list[DoorRuntime]:
        return self._doors

    @property
    def default_door(self) -> DoorRuntime | None:
        if not self._doors:
            return None
        return next((door for door in self._doors if door.is_main), self._doors[0])

    async def async_setup(self) -> None:
        """Первично получить домофоны либо восстановить fallback."""

        try:
            relays = await self._api.async_get_relays()
        except IntersvyazAuthError:
            raise
        except IntersvyazApiError as err:
            _LOGGER.warning(
                "Не удалось получить список домофонов при setup; используем fallback: %s",
                err,
            )
            relays = []

        doors = self._build_doors(relays)
        if not doors:
            fallback = self._build_fallback_door()
            if fallback:
                doors = [fallback]
        self._doors = doors
        self._attach_callbacks()
        self._sync_primary_to_entry()
        _LOGGER.info(
            "DoorManager готов: entry_id=%s doors=%s video=%s",
            self._entry.entry_id,
            len(self._doors),
            sum(1 for door in self._doors if door.has_video),
        )

    def start_periodic_refresh(self) -> None:
        """Запустить обновление временных ссылок."""

        if self._refresh_unsub:
            return
        self._refresh_unsub = async_track_time_interval(
            self._hass,
            self._async_scheduled_refresh,
            timedelta(hours=DOOR_LINK_REFRESH_INTERVAL_HOURS),
        )
        _LOGGER.info(
            "Плановое обновление домофонов: entry_id=%s interval=%sh",
            self._entry.entry_id,
            DOOR_LINK_REFRESH_INTERVAL_HOURS,
        )

    def stop(self) -> None:
        if self._refresh_unsub:
            self._refresh_unsub()
            self._refresh_unsub = None

    async def _async_scheduled_refresh(self, _now=None) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> bool:
        """Обновить реле/URL; вернуть True, если данные получены."""

        try:
            relays = await self._api.async_get_relays()
        except IntersvyazAuthError as err:
            _LOGGER.warning("Авторизация истекла при обновлении домофонов")
            self._entry.async_start_reauth(self._hass)
            return False
        except IntersvyazApiError as err:
            _LOGGER.warning("Не удалось обновить домофоны: %s", err)
            return False

        fresh = self._build_doors(relays)
        old_by_uid = {door.uid: door for door in self._doors}
        fresh_by_uid = {door.uid: door for door in fresh}

        if set(old_by_uid) != set(fresh_by_uid):
            _LOGGER.info(
                "Состав домофонов изменился: old=%s new=%s; планируем reload entry",
                len(old_by_uid),
                len(fresh_by_uid),
            )
            self._doors = fresh
            self._attach_callbacks()
            self._sync_primary_to_entry()
            if not self._reload_scheduled:
                self._reload_scheduled = True
                self._hass.async_create_task(
                    self._hass.config_entries.async_reload(self._entry.entry_id)
                )
            return True

        for uid, current in old_by_uid.items():
            current.update_from(fresh_by_uid[uid])
        self._attach_callbacks()
        self._sync_primary_to_entry()

        # URL картинки мог измениться — старый кеш больше нельзя использовать.
        try:
            self._entry.runtime_data.snapshot_manager.invalidate()
        except (AttributeError, RuntimeError):
            pass

        _LOGGER.debug(
            "Домофоны обновлены без изменения состава: entry_id=%s count=%s",
            self._entry.entry_id,
            len(self._doors),
        )
        return True

    def get(self, door_uid: str) -> DoorRuntime | None:
        return next((door for door in self._doors if door.uid == door_uid), None)

    def _attach_callbacks(self) -> None:
        for door in self._doors:
            door.callback = self._make_open_callback(door)

    def _make_open_callback(self, door: DoorRuntime):
        async def _async_open() -> None:
            _LOGGER.info(
                "Открытие домофона: entry_id=%s door=%s door_id=%s",
                self._entry.entry_id,
                safe_door_ref(door.uid),
                door.door_id,
            )
            try:
                await self._api.async_open_door(
                    door.mac,
                    door.door_id,
                    open_link=door.open_link,
                )
                await self.async_persist_tokens()
            except IntersvyazAuthError as err:
                emit_door_event(
                    self._hass,
                    self._entry,
                    door.uid,
                    DOOR_EVENT_OPEN_FAILED,
                    {"reason": "reauth_required"},
                )
                self._entry.async_start_reauth(self._hass)
                raise HomeAssistantError("Требуется повторная авторизация Интерсвязи") from err
            except IntersvyazApiError as err:
                emit_door_event(
                    self._hass,
                    self._entry,
                    door.uid,
                    DOOR_EVENT_OPEN_FAILED,
                    {"reason": "api_error"},
                )
                raise HomeAssistantError(f"Не удалось открыть домофон: {err}") from err
            except Exception as err:  # pragma: no cover - страховка физического действия
                emit_door_event(
                    self._hass,
                    self._entry,
                    door.uid,
                    DOOR_EVENT_OPEN_FAILED,
                    {"reason": "unexpected_error"},
                )
                _LOGGER.exception(
                    "Неожиданная ошибка открытия door=%s", safe_door_ref(door.uid)
                )
                raise HomeAssistantError("Не удалось открыть домофон") from err

            emit_door_event(
                self._hass,
                self._entry,
                door.uid,
                DOOR_EVENT_OPENED,
                {"source": "integration"},
            )

        return _async_open

    async def async_persist_tokens(self) -> None:
        """Сохранить автоматически обновлённый CRM токен без лишних логов секретов."""

        data = dict(self._entry.data)
        if self._api.mobile_token:
            data[CONF_MOBILE_TOKEN] = dict(self._api.mobile_token.raw)
        if self._api.crm_token:
            data[CONF_CRM_TOKEN] = dict(self._api.crm_token.raw)
        if data != self._entry.data:
            self._hass.config_entries.async_update_entry(self._entry, data=data)
            _LOGGER.debug("Обновлённые токены сохранены в ConfigEntry")

    def _build_doors(self, relays: list[RelayInfo]) -> list[DoorRuntime]:
        result: list[DoorRuntime] = []
        seen: set[str] = set()
        for index, relay in enumerate(
            sorted(
                relays,
                key=lambda item: (not item.is_main, (item.address or "").lower()),
            ),
            start=1,
        ):
            door = self._build_door(relay, index)
            if door is None or door.uid in seen:
                continue
            seen.add(door.uid)
            result.append(door)
        return result

    def _build_door(self, relay: RelayInfo, index: int) -> DoorRuntime | None:
        opener = relay.opener
        mac = (relay.mac or (opener.mac if opener else None) or "").strip().upper()
        if not mac:
            _LOGGER.debug("Пропущен домофон без MAC: index=%s", index)
            return None

        door_id = opener.relay_num if opener and opener.relay_num is not None else None
        if door_id is None and relay.porch_num:
            try:
                door_id = int(relay.porch_num)
            except (TypeError, ValueError):
                door_id = None
        door_id = int(door_id if door_id is not None else 1)
        uid = f"{self._entry.entry_id}_door_{mac.replace(':', '').lower()}_{door_id}"

        return DoorRuntime(
            uid=uid,
            mac=mac,
            door_id=door_id,
            address=(relay.address or f"Домофон №{index}").strip(),
            is_main=bool(relay.is_main),
            is_shared=not bool(relay.is_main),
            relay_id=opener.relay_id if opener else relay.relay_id,
            relay_num=opener.relay_num if opener else None,
            porch_num=relay.porch_num,
            open_link=(relay.open_link or "").strip() or None,
            image_url=(relay.image_url or "").strip() or None,
            has_video=bool(relay.has_video),
            status_code=relay.status_code,
            status_text=relay.status_text,
            entrance_uid=relay.entrance_uid,
            metadata={
                "smart_intercom": relay.smart_intercom,
                "relay_type": relay.relay_type,
                "relay_descr": relay.relay_descr,
            },
        )

    def _build_fallback_door(self) -> DoorRuntime | None:
        data = self._entry.data
        mac = str(data.get(CONF_DOOR_MAC, "") or "").strip().upper()
        if not mac:
            return None
        door_id = int(data.get(CONF_RELAY_NUM, data.get(CONF_DOOR_ENTRANCE, 1)) or 1)
        uid = f"{self._entry.entry_id}_door_{mac.replace(':', '').lower()}_{door_id}"
        return DoorRuntime(
            uid=uid,
            mac=mac,
            door_id=door_id,
            address=str(data.get(CONF_DOOR_ADDRESS) or "Домофон"),
            is_main=True,
            is_shared=False,
            relay_id=data.get(CONF_RELAY_ID),
            relay_num=data.get(CONF_RELAY_NUM),
            porch_num=data.get(CONF_DOOR_ENTRANCE),
            open_link=data.get(CONF_DOOR_OPEN_LINK),
            image_url=data.get(CONF_DOOR_IMAGE_URL),
            has_video=bool(data.get(CONF_DOOR_HAS_VIDEO)),
        )

    def _sync_primary_to_entry(self) -> None:
        primary = self.default_door
        if primary is None:
            return
        data = dict(self._entry.data)
        updates: dict[str, Any] = {
            CONF_DOOR_MAC: primary.mac,
            CONF_RELAY_NUM: primary.door_id,
            CONF_DOOR_ADDRESS: primary.address,
            CONF_DOOR_ENTRANCE: primary.porch_num,
            CONF_RELAY_ID: primary.relay_id,
            CONF_DOOR_HAS_VIDEO: primary.has_video,
            CONF_DOOR_IMAGE_URL: primary.image_url,
            CONF_DOOR_OPEN_LINK: primary.open_link,
        }
        changed = False
        for key, value in updates.items():
            if data.get(key) != value:
                data[key] = value
                changed = True
        if changed:
            self._hass.config_entries.async_update_entry(self._entry, data=data)
            _LOGGER.debug("Fallback-данные основного домофона синхронизированы")
