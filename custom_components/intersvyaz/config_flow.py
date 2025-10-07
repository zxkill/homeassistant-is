"""Конфигурационный поток для интеграции Intersvyaz."""
from __future__ import annotations

import logging
import re
from html import unescape
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
        self._auth_message: Optional[str] = None
        self._auth_id: Optional[str] = None
        self._last_error_message: Optional[str] = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Первый шаг: запрос номера телефона пользователя."""

        errors: Dict[str, str] = {}

        if user_input is not None:
            phone_number = user_input[CONF_PHONE_NUMBER]
            _LOGGER.info("Получен номер телефона в мастере настройки: %s", phone_number)

            session = async_get_clientsession(self.hass)
            self._api_client = IntersvyazApiClient(session=session)

            try:
                auth_context = await self._api_client.async_send_phone_number(
                    phone_number
                )
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка при отправке номера телефона: %s", err)
                errors["base"] = "phone_submission_failed"
            else:
                self._phone_number = phone_number
                self._auth_message = self._extract_user_message(auth_context)
                self._auth_id = auth_context.get("authId") if isinstance(
                    auth_context, dict
                ) else None
                self._last_error_message = None
                if self._auth_id:
                    _LOGGER.debug("Получен идентификатор авторизации authId=%s", self._auth_id)
                if self._auth_message:
                    _LOGGER.info(
                        "Пользователь увидит подсказку: %s", self._auth_message
                    )
                confirm_type = (
                    auth_context.get("confirmType")
                    if isinstance(auth_context, dict)
                    else None
                )
                if confirm_type is not None:
                    _LOGGER.debug("Тип подтверждения от API: %s", confirm_type)
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
                    auth_id=self._auth_id,
                )
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка при подтверждении SMS-кода: %s", err)
                errors["base"] = "code_confirmation_failed"
                self._last_error_message = str(err)
            else:
                _LOGGER.info("Авторизация прошла успешно, создаем запись конфигурации")
                self._last_error_message = None
                return self._create_entry(token_info)

        data_schema = vol.Schema({vol.Required("sms_code"): str})
        description_placeholders = self._build_description_placeholders()
        return self.async_show_form(
            step_id="sms_code",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=description_placeholders,
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

    @staticmethod
    def _extract_user_message(auth_context: Optional[Dict[str, Any]]) -> Optional[str]:
        """Преобразовать HTML-сообщение от API в удобный для пользователя текст."""

        if not isinstance(auth_context, dict):
            _LOGGER.debug("Контекст авторизации отсутствует или некорректен: %s", auth_context)
            return None

        raw_message = auth_context.get("message")
        if not isinstance(raw_message, str):
            _LOGGER.debug("В ответе отсутствует строковое сообщение: %s", raw_message)
            return None

        # Преобразуем HTML-разметку в чистый текст: переносы строк заменяем на \n, остальные
        # теги удаляем, HTML-сущности декодируем.
        normalized = raw_message.replace("<br>", "\n").replace("<br/>", "\n").replace(
            "<br />", "\n"
        )
        normalized = unescape(normalized)
        normalized = re.sub(r"<[^>]+>", "", normalized)
        cleaned = normalized.strip()
        _LOGGER.debug("Нормализованное сообщение для пользователя: %s", cleaned)
        return cleaned or None

    def _build_description_placeholders(self) -> Dict[str, str]:
        """Сформировать плейсхолдеры описания с учетом ошибок и локали."""

        auth_message = self._auth_message or self._get_localized_default_auth_message()
        error_suffix = ""
        if self._last_error_message:
            error_suffix = self._format_error_suffix(self._last_error_message)
        placeholders = {
            "auth_message": auth_message,
            "error_message": error_suffix,
        }
        _LOGGER.debug(
            "Подготовлены плейсхолдеры формы подтверждения: %s", placeholders
        )
        return placeholders

    def _get_localized_default_auth_message(self) -> str:
        """Вернуть дефолтную инструкцию с учетом языка интерфейса."""

        language = (self.hass.config.language or "en").split("-")[0].lower()
        defaults = {
            "ru": "Введите код подтверждения, отправленный оператором.",
            "en": "Enter the confirmation code provided by the operator.",
        }
        default_message = defaults.get(language, defaults["en"])
        _LOGGER.debug(
            "Используется дефолтная подсказка для языка %s: %s",
            language,
            default_message,
        )
        return default_message

    def _format_error_suffix(self, message: str) -> str:
        """Добавить к описанию переводимое сообщение об ошибке."""

        language = (self.hass.config.language or "en").split("-")[0].lower()
        templates = {
            "ru": "\n\nОшибка: {message}",
            "en": "\n\nError: {message}",
        }
        template = templates.get(language, templates["en"])
        formatted = template.format(message=message)
        _LOGGER.debug("Сформирован текст ошибки для пользователя: %s", formatted)
        return formatted
