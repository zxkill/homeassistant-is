"""Config flow авторизации Intersvyaz."""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

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
    CONF_DOOR_ADDRESS,
    CONF_DOOR_ENTRANCE,
    CONF_DOOR_HAS_VIDEO,
    CONF_DOOR_IMAGE_URL,
    CONF_ENTRANCE_UID,
    CONF_MOBILE_ACCESS_BEGIN,
    CONF_MOBILE_ACCESS_END,
    CONF_MOBILE_TOKEN,
    CONF_PHONE_NUMBER,
    CONF_PROFILE_ID,
    CONF_RELAY_ID,
    CONF_RELAY_NUM,
    CONF_RELAY_PAYLOAD,
    CONF_USER_ID,
    DEFAULT_BUYER_ID,
    DOMAIN,
)
from .options_flow import IntersvyazOptionsFlow

_LOGGER = logging.getLogger("custom_components.intersvyaz.config_flow")


class IntersvyazConfigFlow(ConfigFlow, domain=DOMAIN):
    """Мастер подключения, reauth и reconfigure."""

    VERSION = 3

    def __init__(self) -> None:
        self._phone_number: str | None = None
        self._device_id: str | None = None
        self._api_client: IntersvyazApiClient | None = None
        self._confirm_message: str | None = None
        self._auth_id: str | None = None
        self._addresses: list[ConfirmAddress] = []
        self._mobile_token: MobileToken | None = None
        self._buyer_id = DEFAULT_BUYER_ID
        self._selected_relay: RelayInfo | None = None
        self._crm_token_payload: dict[str, Any] | None = None
        self._last_error: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Начать обычное подключение."""

        schema = vol.Schema(
            {
                vol.Required(CONF_PHONE_NUMBER): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEL)
                )
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = _normalize_phone(str(user_input.get(CONF_PHONE_NUMBER, "")))
            if not phone:
                errors["base"] = "invalid_phone"
            else:
                return await self._async_begin_confirmation(phone)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Home Assistant вызвал повторную авторизацию."""

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({}))
        entry = self._get_reauth_entry()
        phone = _normalize_phone(str(entry.data.get(CONF_PHONE_NUMBER, "")))
        if not phone:
            return self.async_abort(reason="reauth_missing_phone")
        self._device_id = str(entry.data.get(CONF_DEVICE_ID) or generate_device_id())
        return await self._async_begin_confirmation(phone)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Позволить повторно пройти авторизацию/выбор договора без удаления entry."""

        entry = self._get_reconfigure_entry()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PHONE_NUMBER,
                    default=str(entry.data.get(CONF_PHONE_NUMBER, "")),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEL))
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = _normalize_phone(str(user_input.get(CONF_PHONE_NUMBER, "")))
            if not phone:
                errors["base"] = "invalid_phone"
            else:
                self._device_id = str(entry.data.get(CONF_DEVICE_ID) or generate_device_id())
                return await self._async_begin_confirmation(phone)
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    async def _async_begin_confirmation(self, phone: str) -> ConfigFlowResult:
        self._phone_number = phone
        self._device_id = self._device_id or generate_device_id()
        self._api_client = IntersvyazApiClient(
            async_get_clientsession(self.hass),
            device_id=self._device_id,
        )
        try:
            context = await self._api_client.async_request_confirmation(phone)
        except IntersvyazApiError as err:
            _LOGGER.warning("Не удалось начать авторизацию: %s", err)
            self._last_error = str(err)
            if self.source == SOURCE_REAUTH:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=vol.Schema({}),
                    errors={"base": "phone_submission_failed"},
                )
            if self.source == SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=vol.Schema(
                        {
                            vol.Required(
                                CONF_PHONE_NUMBER,
                                default=str(entry.data.get(CONF_PHONE_NUMBER, phone)),
                            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEL))
                        }
                    ),
                    errors={"base": "phone_submission_failed"},
                )
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_PHONE_NUMBER, default=phone): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.TEL)
                        )
                    }
                ),
                errors={"base": "phone_submission_failed"},
            )
        self._confirm_message = _normalize_message(context.message)
        self._auth_id = context.auth_id
        self._last_error = None
        return await self.async_step_sms_code()

    async def async_step_sms_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Проверить код подтверждения."""

        schema = vol.Schema(
            {
                vol.Required("sms_code"): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                )
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            if not self._api_client or not self._phone_number:
                return self.async_abort(reason="auth_context_lost")
            try:
                result = await self._api_client.async_check_confirmation(
                    self._phone_number,
                    str(user_input.get("sms_code", "")).strip(),
                )
            except IntersvyazApiError as err:
                _LOGGER.warning("Код подтверждения отклонён: %s", err)
                self._last_error = str(err)
                errors["base"] = "code_confirmation_failed"
            else:
                if result.message and not result.addresses:
                    self._last_error = result.message
                    errors["base"] = "code_confirmation_failed"
                elif not result.addresses:
                    errors["base"] = "no_addresses"
                    self._last_error = "Не найдены договоры"
                else:
                    self._addresses = result.addresses
                    self._auth_id = result.auth_id or self._auth_id
                    if len(self._addresses) == 1:
                        return await self._async_select_account(self._addresses[0].user_id)
                    return await self.async_step_select_account()

        return self.async_show_form(
            step_id="sms_code",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "auth_message": self._confirm_message
                or "Введите код подтверждения, указанный оператором.",
                "error_message": self._last_error or "",
            },
        )

    async def async_step_select_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Выбрать договор, если их несколько."""

        options = {address.user_id: address.address for address in self._addresses}
        schema = vol.Schema({vol.Required(CONF_USER_ID): vol.In(options)})
        if user_input is not None:
            return await self._async_select_account(str(user_input[CONF_USER_ID]))
        return self.async_show_form(
            step_id="select_account",
            data_schema=schema,
            description_placeholders={
                "addresses": "\n".join(options.values()),
                "error_message": self._last_error or "",
            },
        )

    async def _async_select_account(self, user_id: str) -> ConfigFlowResult:
        if not self._api_client or not self._auth_id:
            return self.async_abort(reason="auth_context_lost")
        try:
            token = await self._api_client.async_get_mobile_token(self._auth_id, user_id)
        except IntersvyazApiError as err:
            self._last_error = str(err)
            return await self.async_step_select_account()

        self._mobile_token = token
        if token.unique_device_id:
            self._device_id = token.unique_device_id
        await self.async_set_unique_id(str(token.user_id), raise_on_progress=False)
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            self._abort_if_unique_id_mismatch(reason="wrong_account")
        else:
            self._abort_if_unique_id_configured()
        return await self._async_finalize_configuration()

    async def _async_finalize_configuration(self) -> ConfigFlowResult:
        assert self._api_client is not None
        assert self._mobile_token is not None

        try:
            relays = await self._api_client.async_get_relays()
        except IntersvyazApiError as err:
            self._last_error = str(err)
            return self.async_abort(reason="relay_fetch_failed")
        if not relays:
            return self.async_abort(reason="relay_not_found")

        relay = _select_preferred_relay(relays)
        if relay is None:
            return self.async_abort(reason="relay_not_found")
        mac = (relay.mac or (relay.opener.mac if relay.opener else None) or "").strip()
        if not _validate_mac(mac):
            return self.async_abort(reason="relay_data_invalid")

        self._selected_relay = relay
        self._buyer_id = _coerce_buyer_id(relay, self._mobile_token)
        try:
            self._api_client.set_buyer_id(self._buyer_id)
            crm = await self._api_client.async_authenticate_crm(self._buyer_id)
        except IntersvyazApiError as err:
            _LOGGER.warning("CRM авторизация не удалась: %s", err)
            return self.async_abort(reason="crm_auth_failed")
        self._crm_token_payload = dict(crm.raw)
        return self._finish_entry(mac)

    def _finish_entry(self, mac: str) -> ConfigFlowResult:
        assert self._phone_number is not None
        assert self._device_id is not None
        assert self._mobile_token is not None
        assert self._selected_relay is not None
        assert self._crm_token_payload is not None

        relay = self._selected_relay
        relay_num = relay.opener.relay_num if relay.opener and relay.opener.relay_num is not None else None
        if relay_num is None:
            try:
                relay_num = int(relay.porch_num) if relay.porch_num else 1
            except (TypeError, ValueError):
                relay_num = 1

        data: dict[str, Any] = {
            CONF_PHONE_NUMBER: self._phone_number,
            CONF_DEVICE_ID: self._device_id,
            CONF_USER_ID: self._mobile_token.user_id,
            CONF_PROFILE_ID: self._mobile_token.profile_id,
            CONF_MOBILE_TOKEN: dict(self._mobile_token.raw),
            CONF_MOBILE_ACCESS_BEGIN: _datetime_to_iso(self._mobile_token.access_begin),
            CONF_MOBILE_ACCESS_END: _datetime_to_iso(self._mobile_token.access_end),
            CONF_BUYER_ID: self._buyer_id,
            CONF_CRM_TOKEN: self._crm_token_payload,
            CONF_CRM_ACCESS_BEGIN: self._crm_token_payload.get("ACCESS_BEGIN"),
            CONF_CRM_ACCESS_END: self._crm_token_payload.get("ACCESS_END"),
            CONF_DOOR_MAC: mac.upper(),
            CONF_DOOR_ENTRANCE: int(relay_num),
            CONF_DOOR_ADDRESS: relay.address or None,
            CONF_DOOR_HAS_VIDEO: bool(relay.has_video),
            CONF_DOOR_IMAGE_URL: relay.image_url,
            CONF_RELAY_PAYLOAD: relay.to_dict(),
            CONF_ENTRANCE_UID: relay.entrance_uid,
        }
        if relay.relay_id:
            data[CONF_RELAY_ID] = relay.relay_id
        if relay.opener and relay.opener.relay_num is not None:
            data[CONF_RELAY_NUM] = relay.opener.relay_num

        _LOGGER.info(
            "Config flow завершён: source=%s primary_video=%s",
            self.source,
            relay.has_video,
        )

        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates=data,
            )
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=data,
            )
        return self.async_create_entry(title=self._phone_number, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> IntersvyazOptionsFlow:
        return IntersvyazOptionsFlow(config_entry)


def _normalize_phone(value: str) -> str:
    return re.sub(r"[^0-9+]", "", value.strip())


def _normalize_message(message: str | None) -> str | None:
    if not message:
        return None
    text = message.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return re.sub(r"<[^>]+>", "", unescape(text)).strip() or None


def _select_preferred_relay(relays: list[RelayInfo]) -> RelayInfo | None:
    if not relays:
        return None
    return next((relay for relay in relays if relay.is_main), relays[0])


def _validate_mac(value: str) -> bool:
    return bool(re.fullmatch(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$", value))


def _datetime_to_iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _coerce_buyer_id(relay: RelayInfo | None, token: MobileToken | None) -> int:
    candidates: list[Any] = []
    if relay and relay.relay_id:
        candidates.append(relay.relay_id)
    if token:
        candidates.append(token.profile_id)
    normalized = []
    for candidate in candidates:
        try:
            normalized.append(int(candidate))
        except (TypeError, ValueError):
            pass
    if any(value != DEFAULT_BUYER_ID for value in normalized):
        _LOGGER.debug(
            "API вернул buyer candidates, отличающиеся от совместимого значения; используем %s",
            DEFAULT_BUYER_ID,
        )
    return DEFAULT_BUYER_ID
