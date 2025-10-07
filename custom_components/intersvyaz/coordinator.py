"""Координатор обновления данных интеграции Intersvyaz."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IntersvyazApiClient, IntersvyazApiError
from .const import DEFAULT_UPDATE_INTERVAL_MINUTES, LOGGER_NAME

_LOGGER = logging.getLogger(f"{LOGGER_NAME}.coordinator")


class IntersvyazDataUpdateCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Координатор, отвечающий за обновление пользовательских данных."""

    def __init__(self, hass: HomeAssistant, api_client: IntersvyazApiClient) -> None:
        """Сохранить ссылку на клиента API и настроить интервал обновления."""

        super().__init__(
            hass,
            _LOGGER,
            name="Intersvyaz data",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES),
        )
        self._api_client = api_client

    async def _async_update_data(self) -> Dict[str, Any]:
        """Получить актуальную информацию о пользователе и балансе."""

        try:
            return await self._api_client.async_fetch_account_snapshot()
        except IntersvyazApiError as err:
            # Преобразуем доменный эксепшен в UpdateFailed для Home Assistant.
            raise UpdateFailed(str(err)) from err
