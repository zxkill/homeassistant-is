"""DataUpdateCoordinator профиля и баланса Intersvyaz."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    IntersvyazApiClient,
    IntersvyazApiError,
    IntersvyazAuthError,
    IntersvyazNetworkError,
)
from .const import DEFAULT_UPDATE_INTERVAL_MINUTES

_LOGGER = logging.getLogger("custom_components.intersvyaz.coordinator")


class IntersvyazDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Регулярно обновляет только данные аккаунта, не credentials."""

    def __init__(self, hass: HomeAssistant, api_client: IntersvyazApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Intersvyaz account",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
        )
        self._api_client = api_client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._api_client.async_fetch_account_snapshot()
        except IntersvyazAuthError as err:
            raise ConfigEntryAuthFailed(
                "Требуется повторная авторизация Интерсвязи"
            ) from err
        except (IntersvyazNetworkError, IntersvyazApiError) as err:
            raise UpdateFailed(str(err)) from err
