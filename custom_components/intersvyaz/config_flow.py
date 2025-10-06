"""Конфигурационный поток для интеграции Intersvyaz."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IntersvyazApiClient, IntersvyazApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_PHONE_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger("custom_components.intersvyaz.config_flow")


class IntersvyazConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Реализация мастера настройки интеграции Intersvyaz."""

    VERSION = 1

    def __init__(self) -> None:
        """Инициализировать состояние конфигурационного потока."""

        self._phone_number: Optional[str] = None
        self._api_client: Optional[IntersvyazApiClient] = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Первый шаг: запрос номера телефона пользователя."""

        errors: Dict[str, str] = {}

        if user_input is not None:
            phone_number = user_input[CONF_PHONE_NUMBER]
            _LOGGER.info("Получен номер телефона в мастере настройки: %s", phone_number)

            session = async_get_clientsession(self.hass)
            self._api_client = IntersvyazApiClient(session=session)

            try:
                await self._api_client.async_send_phone_number(phone_number)
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка при отправке номера телефона: %s", err)
                errors["base"] = "phone_submission_failed"
            else:
                self._phone_number = phone_number
                return await self.async_step_sms_code()

        data_schema = vol.Schema({vol.Required(CONF_PHONE_NUMBER): str})
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_sms_code(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Второй шаг: запрос кода из SMS."""

        errors: Dict[str, str] = {}

        if user_input is not None and self._api_client and self._phone_number:
            code = user_input["sms_code"]
            _LOGGER.info("Получен SMS-код в мастере настройки")
            try:
                token_info = await self._api_client.async_confirm_code(
                    phone_number=self._phone_number,
                    code=code,
                )
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка при подтверждении SMS-кода: %s", err)
                errors["base"] = "code_confirmation_failed"
            else:
                _LOGGER.info("Авторизация прошла успешно, создаем запись конфигурации")
                return self._create_entry(token_info)

        data_schema = vol.Schema({vol.Required("sms_code"): str})
        return self.async_show_form(
            step_id="sms_code",
            data_schema=data_schema,
            errors=errors,
        )

    @callback
    def _create_entry(self, token_info) -> FlowResult:
        """Создать запись конфигурации Home Assistant после успешной авторизации."""

        assert self._phone_number is not None
        assert token_info is not None

        data = {
            CONF_PHONE_NUMBER: self._phone_number,
            CONF_ACCESS_TOKEN: token_info.access_token,
            CONF_REFRESH_TOKEN: token_info.refresh_token,
            CONF_ACCESS_TOKEN_EXPIRES_AT: token_info.expires_at.isoformat(),
        }

        _LOGGER.debug("Создаем запись конфигурации с данными: %s", data)
        return self.async_create_entry(title=self._phone_number, data=data)
