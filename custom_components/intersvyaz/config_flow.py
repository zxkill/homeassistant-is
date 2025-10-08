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
    RelayInfo,
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
    CONF_DOOR_ADDRESS,
    CONF_DOOR_HAS_VIDEO,
    CONF_DOOR_IMAGE_URL,
    CONF_MOBILE_ACCESS_BEGIN,
    CONF_MOBILE_ACCESS_END,
    CONF_MOBILE_TOKEN,
    CONF_PHONE_NUMBER,
    CONF_PROFILE_ID,
    CONF_USER_ID,
    CONF_RELAY_ID,
    CONF_RELAY_NUM,
    CONF_RELAY_PAYLOAD,
    CONF_ENTRANCE_UID,
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
        self._door_address: Optional[str] = None
        self._door_has_video: Optional[bool] = None
        self._door_image_url: Optional[str] = None
        self._entrance_uid: Optional[str] = None
        self._relay_payload: Optional[Dict[str, Any]] = None
        self._selected_relay: Optional[RelayInfo] = None
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
        return await self._finalize_configuration()

    async def _finalize_configuration(self) -> FlowResult:
        """Получить сведения о домофоне и выполнить CRM авторизацию."""

        assert self._api_client is not None
        assert self._mobile_token is not None

        try:
            relays = await self._api_client.async_get_relays()
        except IntersvyazApiError as err:
            _LOGGER.error("Не удалось получить список домофонов: %s", err)
            self._last_error_message = str(err)
            return self._show_select_account_form(errors={"base": "relay_fetch_failed"})

        if not relays:
            _LOGGER.error("API не вернуло ни одного домофона для пользователя")
            self._last_error_message = None
            return self._show_select_account_form(errors={"base": "relay_not_found"})

        relay = _select_preferred_relay(relays)
        if not relay:
            _LOGGER.error("Не удалось выбрать подходящий домофон из списка")
            self._last_error_message = None
            return self._show_select_account_form(errors={"base": "relay_not_found"})

        # Сервер может вернуть MAC либо в основном блоке, либо вложенным в `OPENER`.
        mac = (relay.mac or (relay.opener.mac if relay.opener else None) or "").strip()
        if not _validate_mac(mac):
            _LOGGER.error("Получен некорректный MAC-адрес домофона: %s", mac)
            self._last_error_message = None
            return self._show_select_account_form(errors={"base": "relay_data_invalid"})

        # Для CRM используется номер реле, однако в отдельных ответах он совпадает с номером подъезда.
        relay_num = (
            relay.opener.relay_num if relay.opener and relay.opener.relay_num is not None else None
        )
        if relay_num is None and relay.porch_num:
            try:
                relay_num = int(relay.porch_num)
            except ValueError:
                relay_num = None
        if relay_num is None:
            relay_num = 1

        self._door_mac = mac.upper()
        self._door_entrance = int(relay_num)
        self._door_address = relay.address or None
        self._door_has_video = relay.has_video
        self._door_image_url = relay.image_url
        self._entrance_uid = relay.entrance_uid
        self._relay_payload = relay.to_dict()
        self._selected_relay = relay

        # В некоторых городах `RELAY_ID` совпадает с buyerId. Чтобы исключить ошибки 401,
        # приводим все идентификаторы к int и подставляем профайл либо дефолт как запасной.
        self._buyer_id = _coerce_buyer_id(relay, self._mobile_token)

        _LOGGER.info(
            "Выбран домофон %s (mac=%s, relay_num=%s, buyer_id=%s)",
            relay.address,
            self._door_mac,
            relay_num,
            self._buyer_id,
        )

        try:
            self._api_client.set_buyer_id(self._buyer_id)
            crm_token = await self._api_client.async_authenticate_crm(self._buyer_id)
        except IntersvyazApiError as err:
            _LOGGER.error("Ошибка при авторизации во второй системе: %s", err)
            self._last_error_message = str(err)
            return self._show_select_account_form(errors={"base": "crm_auth_failed"})

        self._crm_token_payload = crm_token.raw
        self._last_error_message = None
        return self._create_entry()

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
        if self._selected_relay and self._selected_relay.relay_id:
            data[CONF_RELAY_ID] = self._selected_relay.relay_id
        if self._selected_relay and self._selected_relay.opener:
            if self._selected_relay.opener.relay_num is not None:
                data[CONF_RELAY_NUM] = self._selected_relay.opener.relay_num
        if self._relay_payload:
            data[CONF_RELAY_PAYLOAD] = self._relay_payload
        if self._door_address:
            data[CONF_DOOR_ADDRESS] = self._door_address
        if self._door_has_video is not None:
            data[CONF_DOOR_HAS_VIDEO] = self._door_has_video
        if self._door_image_url:
            data[CONF_DOOR_IMAGE_URL] = self._door_image_url
        if self._entrance_uid:
            data[CONF_ENTRANCE_UID] = self._entrance_uid
        _LOGGER.debug("Создаём конфигурацию с данными: %s", data)
        return self.async_create_entry(title=self._phone_number, data=data)

    def _build_description_placeholders(self) -> Dict[str, str]:
        """Сформировать текст подсказки для шага с кодом подтверждения."""

        message = self._confirm_message or self._default_auth_message()
        if self._last_error_message:
            message = f"{message}\n\n{self._last_error_message}"
        return {"auth_message": message}

    def _show_select_account_form(
        self, errors: Optional[Dict[str, str]] = None
    ) -> FlowResult:
        """Отобразить форму выбора договора с подсказками."""

        options = {address.user_id: address.address for address in self._addresses}
        schema = vol.Schema({vol.Required("user_id"): vol.In(options)})
        placeholders = {"addresses": "\n".join(options.values())}
        # Даже при отсутствии ошибок Home Assistant должен получить плейсхолдер
        # `error_message`, иначе перевод рухнет с KeyError. Поэтому всегда
        # передаём строку, дополняя её текстом ошибки и отступами только при
        # необходимости, чтобы описание оставалось читабельным.
        if self._last_error_message:
            placeholders["error_message"] = f"\n\n{self._last_error_message}"
        else:
            placeholders["error_message"] = ""
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


def _select_preferred_relay(relays: List[RelayInfo]) -> Optional[RelayInfo]:
    """Выбрать домофон, который будет использован по умолчанию."""

    if not relays:
        return None
    for relay in relays:
        if relay.is_main:
            return relay
    return relays[0]


def _validate_mac(value: str) -> bool:
    """Проверить, что MAC-адрес соответствует формату XX:XX:XX:XX:XX:XX."""

    return bool(re.fullmatch(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$", value))


def _datetime_to_iso(value) -> Optional[str]:
    """Преобразовать datetime в ISO-формат."""

    if value is None:
        return None
    return value.isoformat()


def _coerce_buyer_id(relay: Optional[RelayInfo], token: Optional[MobileToken]) -> int:
    """Форсировать buyerId=1 для CRM, логируя отличия кандидатов."""

    candidates: List[Any] = []
    if relay and relay.relay_id:
        candidates.append(relay.relay_id)
    if token and token.profile_id is not None:
        candidates.append(token.profile_id)

    normalized_candidates: List[int] = []
    for candidate in candidates:
        try:
            # CRM ожидает единицу, но мы собираем числовые значения для диагностики.
            normalized_candidates.append(int(candidate))
        except (TypeError, ValueError):
            _LOGGER.debug(
                "Не удалось преобразовать candidate=%s в buyer_id, пропускаем", candidate
            )

    if normalized_candidates and any(
        candidate != DEFAULT_BUYER_ID for candidate in normalized_candidates
    ):
        # CRM возвращает 401 при любых значениях, кроме 1, поэтому принудительно подменяем.
        _LOGGER.warning(
            "CRM ожидает buyer_id=%s, но API предложило %s — используем значение по умолчанию",
            DEFAULT_BUYER_ID,
            normalized_candidates,
        )
    else:
        _LOGGER.debug(
            "CRM использует buyer_id=%s, кандидаты=%s",
            DEFAULT_BUYER_ID,
            normalized_candidates or candidates,
        )

    return DEFAULT_BUYER_ID

