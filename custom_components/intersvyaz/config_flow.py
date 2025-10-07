"""Конфигурационный поток интеграции Intersvyaz."""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, List, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ConfirmAddress,
    IntersvyazApiClient,
    IntersvyazApiError,
    MobileToken,
    generate_device_id,
)
from .const import (
    CONF_BUYER_ID,
    CONF_CRM_ACCESS_BEGIN,
    CONF_CRM_ACCESS_END,
    CONF_CRM_TOKEN,
    CONF_DEVICE_ID,
    CONF_DOOR_ENTRANCE,
    CONF_DOOR_MAC,
    CONF_MOBILE_ACCESS_BEGIN,
    CONF_MOBILE_ACCESS_END,
    CONF_MOBILE_TOKEN,
    CONF_PHONE_NUMBER,
    CONF_PROFILE_ID,
    CONF_USER_ID,
    DEFAULT_BUYER_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(f"{DOMAIN}.config_flow")

PHONE_SCHEMA = vol.Schema({vol.Required(CONF_PHONE_NUMBER): str})
CODE_SCHEMA = vol.Schema({vol.Required("sms_code"): str})


class IntersvyazConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Пошаговый мастер настройки интеграции."""

    VERSION = 2

    def __init__(self) -> None:
        """Инициализировать переменные состояния мастера."""

        self._phone_number: Optional[str] = None
        self._device_id: Optional[str] = None
        self._api_client: Optional[IntersvyazApiClient] = None
        self._confirm_message: Optional[str] = None
        self._auth_id: Optional[str] = None
        self._addresses: List[ConfirmAddress] = []
        self._mobile_token: Optional[MobileToken] = None
        self._buyer_id: int = DEFAULT_BUYER_ID
        self._door_mac: Optional[str] = None
        self._door_entrance: Optional[int] = None
        self._crm_token_payload: Optional[Dict[str, Any]] = None
        self._last_error_message: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Запросить номер телефона пользователя."""

        errors: Dict[str, str] = {}

        if user_input is not None:
            phone_number = user_input[CONF_PHONE_NUMBER]
            self._phone_number = phone_number
            await self.async_set_unique_id(phone_number, raise_on_progress=False)
            self._device_id = self._device_id or generate_device_id()
            session = async_get_clientsession(self.hass)
            self._api_client = IntersvyazApiClient(
                session=session,
                device_id=self._device_id,
            )
            try:
                context = await self._api_client.async_request_confirmation(phone_number)
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка при отправке номера телефона: %s", err)
                errors["base"] = "phone_submission_failed"
                self._last_error_message = str(err)
            else:
                self._confirm_message = _normalize_message(context.message)
                self._auth_id = context.auth_id or self._auth_id
                self._last_error_message = None
                return await self.async_step_sms_code()

        return self.async_show_form(
            step_id="user", data_schema=PHONE_SCHEMA, errors=errors
        )

    async def async_step_sms_code(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Получить код подтверждения и список договоров."""

        errors: Dict[str, str] = {}

        if user_input is not None and self._api_client and self._phone_number:
            code = user_input["sms_code"]
            try:
                result = await self._api_client.async_check_confirmation(
                    self._phone_number, code
                )
            except IntersvyazApiError as err:
                _LOGGER.error("Ошибка при проверке кода подтверждения: %s", err)
                errors["base"] = "code_confirmation_failed"
                self._last_error_message = str(err)
            else:
                if result.message and not result.addresses:
                    _LOGGER.warning("API вернуло сообщение об ошибке: %s", result.message)
                    errors["base"] = "code_confirmation_failed"
                    self._last_error_message = result.message
                elif not result.addresses:
                    _LOGGER.error("API не вернуло ни одного адреса для выбора")
                    errors["base"] = "no_addresses"
                    self._last_error_message = "Не найдены договоры для указанного номера"
                else:
                    self._addresses = result.addresses
                    self._auth_id = result.auth_id or self._auth_id
                    self._last_error_message = None
                    if len(self._addresses) == 1:
                        single_address = self._addresses[0]
                        return await self._handle_address_selection(single_address.user_id)
                    return await self.async_step_select_account()

        placeholders = self._build_description_placeholders()
        return self.async_show_form(
            step_id="sms_code",
            data_schema=CODE_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_select_account(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Дать пользователю выбрать конкретный договор из списка адресов."""

        if user_input is not None:
            selected_user_id = user_input["user_id"]
            return await self._handle_address_selection(selected_user_id)

        return self._show_select_account_form()

    async def async_step_account_options(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Собрать параметры домофона и выполнить вторичную авторизацию."""

        errors: Dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_DOOR_MAC].strip()
            if not _validate_mac(mac):
                errors[CONF_DOOR_MAC] = "invalid_mac"
            else:
                entrance = int(user_input[CONF_DOOR_ENTRANCE])
                buyer_id = int(user_input[CONF_BUYER_ID])
                self._door_mac = mac.upper()
                self._door_entrance = entrance
                self._buyer_id = buyer_id
                try:
                    assert self._api_client is not None
                    self._api_client.set_buyer_id(buyer_id)
                    crm_token = await self._api_client.async_authenticate_crm(buyer_id)
                except IntersvyazApiError as err:
                    _LOGGER.error("Ошибка при авторизации во второй системе: %s", err)
                    errors["base"] = "crm_auth_failed"
                    self._last_error_message = str(err)
                else:
                    self._crm_token_payload = crm_token.raw
                    self._last_error_message = None
                    return self._create_entry()

        schema = vol.Schema(
            {
                vol.Required(CONF_DOOR_MAC, default=self._door_mac or ""):
                    vol.All(str, vol.Length(min=11)),
                vol.Required(CONF_DOOR_ENTRANCE, default=self._door_entrance or 1):
                    vol.All(int, vol.Range(min=0)),
                vol.Required(CONF_BUYER_ID, default=self._buyer_id):
                    vol.All(int, vol.Range(min=1)),
            }
        )
        return self.async_show_form(
            step_id="account_options",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._build_options_placeholders(),
        )

    async def _handle_address_selection(self, user_id: str) -> FlowResult:
        """Получить мобильный токен после выбора договора."""

        if not self._api_client or not self._auth_id:
            raise IntersvyazApiError("Контекст авторизации утерян, начните заново")
        try:
            token = await self._api_client.async_get_mobile_token(self._auth_id, user_id)
        except IntersvyazApiError as err:
            _LOGGER.error("Ошибка при получении токена: %s", err)
            self._last_error_message = str(err)
            return self._show_select_account_form(errors={"base": "token_request_failed"})
        self._mobile_token = token
        if token.unique_device_id:
            self._device_id = token.unique_device_id
        await self.async_set_unique_id(str(token.user_id), raise_on_progress=False)
        self._last_error_message = None
        return await self.async_step_account_options()

    @callback
    def _create_entry(self) -> FlowResult:
        """Формирование итоговой записи конфигурации."""

        assert self._mobile_token is not None
        assert self._phone_number is not None
        assert self._device_id is not None
        assert self._door_mac is not None
        assert self._door_entrance is not None
        assert self._crm_token_payload is not None

        crm_access_begin = self._crm_token_payload.get("ACCESS_BEGIN")
        crm_access_end = self._crm_token_payload.get("ACCESS_END")
        data = {
            CONF_PHONE_NUMBER: self._phone_number,
            CONF_DEVICE_ID: self._device_id,
            CONF_USER_ID: self._mobile_token.user_id,
            CONF_PROFILE_ID: self._mobile_token.profile_id,
            CONF_MOBILE_TOKEN: self._mobile_token.raw,
            CONF_MOBILE_ACCESS_BEGIN: _datetime_to_iso(self._mobile_token.access_begin),
            CONF_MOBILE_ACCESS_END: _datetime_to_iso(self._mobile_token.access_end),
            CONF_DOOR_MAC: self._door_mac,
            CONF_DOOR_ENTRANCE: self._door_entrance,
            CONF_BUYER_ID: self._buyer_id,
            CONF_CRM_TOKEN: self._crm_token_payload,
            CONF_CRM_ACCESS_BEGIN: crm_access_begin,
            CONF_CRM_ACCESS_END: crm_access_end,
        }
        _LOGGER.debug("Создаём конфигурацию с данными: %s", data)
        return self.async_create_entry(title=self._phone_number, data=data)

    def _build_description_placeholders(self) -> Dict[str, str]:
        """Сформировать текст подсказки для шага с кодом подтверждения."""

        message = self._confirm_message or self._default_auth_message()
        if self._last_error_message:
            message = f"{message}\n\n{self._last_error_message}"
        return {"auth_message": message}

    def _build_options_placeholders(self) -> Dict[str, str]:
        """Подсказки для шага выбора домофона."""

        placeholders: Dict[str, str] = {}
        if self._addresses:
            addresses = "\n".join(address.address for address in self._addresses)
            placeholders["addresses"] = addresses
        if self._last_error_message:
            placeholders["error_message"] = self._last_error_message
        return placeholders

    def _show_select_account_form(
        self, errors: Optional[Dict[str, str]] = None
    ) -> FlowResult:
        """Отобразить форму выбора договора с подсказками."""

        options = {address.user_id: address.address for address in self._addresses}
        schema = vol.Schema({vol.Required("user_id"): vol.In(options)})
        placeholders = {"addresses": "\n".join(options.values())}
        if self._last_error_message:
            placeholders["error_message"] = self._last_error_message
        return self.async_show_form(
            step_id="select_account",
            data_schema=schema,
            errors=errors or {},
            description_placeholders=placeholders,
        )

    def _default_auth_message(self) -> str:
        """Возвращает дефолтную подсказку с учётом языка интерфейса."""

        language = (self.hass.config.language or "ru").split("-")[0].lower()
        defaults = {
            "ru": "Введите код подтверждения, указанный оператором.",
            "en": "Enter the confirmation code provided by the operator.",
        }
        return defaults.get(language, defaults["en"])


def _normalize_message(message: Optional[str]) -> Optional[str]:
    """Очистить HTML сообщение и привести к многострочному виду."""

    if not message:
        return None
    normalized = message.replace("<br>", "\n").replace("<br/>", "\n").replace(
        "<br />", "\n"
    )
    normalized = unescape(normalized)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    return normalized.strip() or None


def _validate_mac(value: str) -> bool:
    """Проверить, что MAC-адрес соответствует формату XX:XX:XX:XX:XX:XX."""

    return bool(re.fullmatch(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$", value))


def _datetime_to_iso(value) -> Optional[str]:
    """Преобразовать datetime в ISO-формат."""

    if value is None:
        return None
    return value.isoformat()

