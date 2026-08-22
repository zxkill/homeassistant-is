"""Асинхронный клиент облачных API Intersvyaz.

В 2.0 это высокоуровневый клиент; модели, ошибки и HTTP transport вынесены в отдельные небольшие модули.
Ни один bearer/token/номер телефона/временная ссылка не должен попадать в лог
в открытом виде.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import parse_qsl, urlparse

from aiohttp import ClientSession

from .const import (
    BALANCE_ENDPOINT,
    CHECK_CONFIRM_ENDPOINT,
    CRM_AUTH_ENDPOINT,
    CRM_OPEN_DOOR_ENDPOINT_TEMPLATE,
    DEFAULT_API_BASE_URL,
    DEFAULT_API_SOURCE,
    DEFAULT_APP_VERSION,
    DEFAULT_BUYER_ID,
    DEFAULT_CRM_BASE_URL,
    DEFAULT_CAMERAS_BASE_URL,
    DEFAULT_PLATFORM,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    YARD_APP_VERSION,
    YARD_USER_AGENT,
    YARD_WITH_GROUP_ENDPOINT,
    GET_TOKEN_ENDPOINT,
    HEADER_AUTHORIZATION,
    RELAYS_ENDPOINT,
    SEND_PHONE_ENDPOINT,
    TOKEN_INFO_ENDPOINT,
    USER_INFO_ENDPOINT,
)
from .security import safe_payload_summary

_LOGGER = logging.getLogger("custom_components.intersvyaz.api")


from .api_errors import IntersvyazApiError, IntersvyazAuthError, IntersvyazNetworkError
from .api_models import (
    CheckConfirmResult,
    ConfirmAddress,
    ConfirmContext,
    CrmToken,
    MobileToken,
    RelayInfo,
    RelayOpener,
    generate_device_id,
    optional_int,
    parse_crm_token,
    parse_mobile_token,
    parse_relay_info,
    safe_str,
)
from .api_transport import IntersvyazHttpTransport
from .yard_models import YardGroupInfo, parse_yard_groups

class IntersvyazApiClient:
    """Высокоуровневый API-клиент Интерсвязи."""

    def __init__(
        self,
        session: ClientSession,
        *,
        api_base_url: str = DEFAULT_API_BASE_URL,
        crm_base_url: str = DEFAULT_CRM_BASE_URL,
        cameras_base_url: str = DEFAULT_CAMERAS_BASE_URL,
        request_timeout: int = DEFAULT_TIMEOUT,
        device_id: str | None = None,
        app_version: str = DEFAULT_APP_VERSION,
        platform: str = DEFAULT_PLATFORM,
        api_source: str = DEFAULT_API_SOURCE,
        buyer_id: int = DEFAULT_BUYER_ID,
        accept_language: str = "ru-RU",
        user_agent: str | None = None,
    ) -> None:
        self._transport = IntersvyazHttpTransport(session, request_timeout)
        self._api_base_url = api_base_url.rstrip("/")
        self._crm_base_url = crm_base_url.rstrip("/")
        self._cameras_base_url = cameras_base_url.rstrip("/")
        self._device_id = device_id or generate_device_id()
        self._app_version = app_version
        self._platform = platform
        self._api_source = api_source
        self._buyer_id = int(buyer_id)
        self._accept_language = accept_language
        self._user_agent = user_agent or DEFAULT_USER_AGENT
        self._mobile_token: MobileToken | None = None
        self._crm_token: CrmToken | None = None

        _LOGGER.debug(
            "Создан API client: api=%s crm=%s cameras=%s buyer_id=%s device_id=<redacted>",
            self._api_base_url,
            self._crm_base_url,
            self._cameras_base_url,
            self._buyer_id,
        )

    @property
    def mobile_token(self) -> MobileToken | None:
        return self._mobile_token

    @property
    def crm_token(self) -> CrmToken | None:
        return self._crm_token

    @property
    def buyer_id(self) -> int:
        return self._buyer_id

    @property
    def device_id(self) -> str:
        return self._device_id

    async def async_request_confirmation(self, phone_number: str) -> ConfirmContext:
        """Запросить звонок/СМС подтверждения."""

        payload = {
            "deviceId": self._device_id,
            "phone": phone_number,
            "checkSkipAuth": 1,
        }
        _LOGGER.info("Запрашиваем подтверждение номера телефона")
        response = await self._request_mobile(
            "POST", SEND_PHONE_ENDPOINT, json=payload, accept_version="v2"
        )
        _LOGGER.debug("get-confirm response=%s", safe_payload_summary(response))
        return ConfirmContext(
            auth_id=safe_str(response, "authId"),
            message=safe_str(response, "message"),
            timeout_mins=optional_int(response.get("timeoutMins")),
            timeout_default=optional_int(response.get("timeoutMinsDefault")),
            confirm_type=optional_int(response.get("confirmType")),
        )

    async def async_check_confirmation(
        self, phone_number: str, confirm_code: str
    ) -> CheckConfirmResult:
        """Проверить код и вернуть договоры."""

        payload = {"confirmCode": confirm_code, "phone": phone_number}
        _LOGGER.info("Проверяем код подтверждения")
        response = await self._request_mobile(
            "POST", CHECK_CONFIRM_ENDPOINT, json=payload, accept_version="v2"
        )
        addresses: list[ConfirmAddress] = []
        raw_addresses = response.get("addresses") if isinstance(response, dict) else None
        if isinstance(raw_addresses, list):
            for item in raw_addresses:
                if not isinstance(item, dict):
                    continue
                user_id = item.get("USER_ID")
                address = item.get("ADDRESS")
                if user_id is not None and address:
                    addresses.append(ConfirmAddress(str(user_id), str(address)))
        _LOGGER.debug(
            "check-confirm: addresses=%s message_present=%s",
            len(addresses),
            bool(response.get("message")) if isinstance(response, dict) else False,
        )
        return CheckConfirmResult(
            auth_id=safe_str(response, "authId"),
            addresses=addresses,
            message=safe_str(response, "message"),
        )

    async def async_get_mobile_token(self, auth_id: str, user_id: str) -> MobileToken:
        """Получить мобильный токен после выбора договора."""

        _LOGGER.info("Запрашиваем мобильный токен для выбранного договора")
        response = await self._request_mobile(
            "POST",
            GET_TOKEN_ENDPOINT,
            json={"authId": auth_id, "userId": user_id},
            accept_version="v2",
        )
        token = parse_mobile_token(response)
        self._mobile_token = token
        _LOGGER.info(
            "Мобильная авторизация успешна: expires=%s",
            token.access_end,
        )
        return token

    async def async_check_token(self) -> dict[str, Any]:
        """Получить диагностическую информацию о мобильном токене."""

        self._ensure_mobile_token()
        response = await self._request_mobile(
            "GET",
            TOKEN_INFO_ENDPOINT,
            headers=self._build_mobile_headers(accept_version="v2", include_bearer=True),
            accept_version="v2",
        )
        _LOGGER.debug("token-info=%s", safe_payload_summary(response))
        return response

    async def async_authenticate_crm(self, buyer_id: int | None = None) -> CrmToken:
        """Получить CRM JWT через мобильный токен."""

        self._ensure_mobile_token()
        effective_buyer_id = int(buyer_id if buyer_id is not None else self._buyer_id)
        _LOGGER.info("Запрашиваем CRM токен buyer_id=%s", effective_buyer_id)
        response = await self._request_crm(
            "POST",
            CRM_AUTH_ENDPOINT,
            json={"token": self._mobile_token.token, "buyerId": effective_buyer_id},
            use_mobile_token=True,
        )
        token = parse_crm_token(response)
        self._crm_token = token
        _LOGGER.info("CRM авторизация успешна: expires=%s", token.access_end)
        return token

    async def async_get_relays(
        self,
        *,
        pagination: int = 1,
        page_size: int = 30,
        main_first: int = 1,
        include_main: bool = True,
        include_shared: bool = True,
    ) -> list[RelayInfo]:
        """Получить основные и расшаренные домофоны без дублей."""

        if not include_main and not include_shared:
            return []
        self._ensure_mobile_token()
        headers = self._build_mobile_headers(accept_version="v2", include_bearer=True)
        if self._mobile_token.profile_id:
            headers["X-api-profile-id"] = str(self._mobile_token.profile_id)

        result: list[RelayInfo] = []
        seen: set[tuple[Any, ...]] = set()

        async def collect(is_shared: int, label: str) -> None:
            try:
                batch = await self._async_fetch_relays_batch(
                    headers=headers,
                    pagination=pagination,
                    page_size=page_size,
                    main_first=main_first,
                    is_shared=is_shared,
                    label=label,
                )
            except IntersvyazAuthError:
                raise
            except IntersvyazApiError as err:
                _LOGGER.warning("Не удалось получить %s домофоны: %s", label, err)
                return
            for relay in batch:
                key = (
                    (relay.entrance_uid or "").lower(),
                    (relay.mac or "").upper(),
                    relay.opener.relay_id if relay.opener else None,
                    relay.opener.relay_num if relay.opener else None,
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append(relay)

        if include_main:
            await collect(0, "основные")
        if include_shared:
            await collect(1, "расшаренные")

        _LOGGER.info(
            "Получены домофоны: total=%s main_requested=%s shared_requested=%s",
            len(result),
            include_main,
            include_shared,
        )
        return result

    async def _async_fetch_relays_batch(
        self,
        *,
        headers: dict[str, str],
        pagination: int,
        page_size: int,
        main_first: int,
        is_shared: int,
        label: str,
    ) -> list[RelayInfo]:
        params = {
            "pagination": pagination,
            "pageSize": page_size,
            "mainFirst": main_first,
            "isShared": is_shared,
        }
        _LOGGER.debug("Запрашиваем домофоны type=%s", label)
        response = await self._request_mobile(
            "GET",
            RELAYS_ENDPOINT,
            headers=headers,
            params=params,
            accept_version="v2",
        )
        if not isinstance(response, list):
            raise IntersvyazApiError("API вернул неожиданный формат списка домофонов")
        return [parse_relay_info(item) for item in response if isinstance(item, dict)]

    async def async_get_yard_groups(self) -> list[YardGroupInfo]:
        """Получить все группы и камеры, доступные аккаунту в «Умном дворе»."""

        self._ensure_mobile_token()
        headers = self._build_yard_headers()
        _LOGGER.info("[YARD_CAMERAS][FETCH_BEGIN]")
        response = await self._request(
            base_url=self._cameras_base_url,
            method="GET",
            endpoint=YARD_WITH_GROUP_ENDPOINT,
            headers=headers,
        )
        if not isinstance(response, list):
            raise IntersvyazApiError("API камер вернул неожиданный формат")
        groups = parse_yard_groups(response)
        camera_count = sum(len(group.cameras) for group in groups)
        live_count = sum(
            1
            for group in groups
            for camera in group.cameras
            if camera.live_access
        )
        _LOGGER.info(
            "[YARD_CAMERAS][FETCH_OK] groups=%s cameras=%s live=%s",
            len(groups),
            camera_count,
            live_count,
        )
        return groups

    async def async_open_door(
        self,
        mac: str | None = None,
        door_id: int | None = None,
        *,
        open_link: str | None = None,
    ) -> None:
        """Открыть домофон по ссылке API либо по MAC/relay."""

        if open_link:
            await self._async_open_door_by_link(open_link)
            return
        if not mac:
            raise IntersvyazApiError("Не удалось определить MAC-адрес домофона")
        if door_id is None:
            raise IntersvyazApiError("Не удалось определить номер реле домофона")

        await self._ensure_crm_token()
        endpoint = CRM_OPEN_DOOR_ENDPOINT_TEMPLATE.format(mac=mac, door_id=door_id)
        _LOGGER.info("Отправляем команду открытия door_id=%s", door_id)
        await self._request_crm(
            "GET",
            endpoint,
            headers=self._build_crm_headers(include_crm_bearer=True),
            use_crm_token=True,
        )

    async def _async_open_door_by_link(self, open_link: str) -> None:
        normalized = (open_link or "").strip()
        if not normalized:
            raise IntersvyazApiError("API не вернул ссылку открытия домофона")
        await self._ensure_crm_token()
        parsed = urlparse(normalized)
        if parsed.scheme and parsed.netloc:
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            endpoint = parsed.path or "/"
            params = dict(parse_qsl(parsed.query)) if parsed.query else None
        else:
            base_url = self._crm_base_url
            endpoint = normalized if normalized.startswith("/") else f"/{normalized}"
            params = None
        _LOGGER.info("Отправляем команду открытия по временной ссылке")
        await self._request(
            base_url=base_url,
            method="GET",
            endpoint=endpoint,
            headers=self._build_crm_headers(include_crm_bearer=True),
            params=params,
        )

    async def async_get_user_info(self) -> dict[str, Any]:
        """Получить профиль абонента."""

        self._ensure_mobile_token()
        response = await self._request_mobile(
            "GET",
            USER_INFO_ENDPOINT,
            headers=self._build_mobile_headers(accept_version="v3", include_bearer=True),
            accept_version="v3",
        )
        _LOGGER.debug("Профиль обновлён: %s", safe_payload_summary(response))
        return response

    async def async_get_balance(self) -> dict[str, Any]:
        """Получить баланс договора."""

        self._ensure_mobile_token()
        headers = self._build_mobile_headers(accept_version="v2", include_bearer=True)
        if self._mobile_token.profile_id:
            headers["X-api-profile-id"] = str(self._mobile_token.profile_id)
        headers["X-Api-User-Id"] = str(self._mobile_token.user_id)
        response = await self._request_mobile(
            "GET", BALANCE_ENDPOINT, headers=headers, accept_version="v2"
        )
        _LOGGER.debug("Баланс обновлён: %s", safe_payload_summary(response))
        return response

    async def async_fetch_account_snapshot(self) -> dict[str, Any]:
        """Получить только публичные данные coordinator, без токенов."""

        user_task = asyncio.create_task(self.async_get_user_info())
        balance_task = asyncio.create_task(self.async_get_balance())
        try:
            user_info, balance = await asyncio.gather(user_task, balance_task)
        except Exception:
            user_task.cancel()
            balance_task.cancel()
            raise
        return {"user": user_info, "balance": balance}

    def set_mobile_token(self, token_payload: dict[str, Any]) -> None:
        self._mobile_token = parse_mobile_token(token_payload)
        _LOGGER.debug("Мобильный токен восстановлен из ConfigEntry")

    def set_crm_token(self, token_payload: dict[str, Any]) -> None:
        self._crm_token = parse_crm_token(token_payload)
        _LOGGER.debug("CRM токен восстановлен из ConfigEntry")

    def set_buyer_id(self, buyer_id: int) -> None:
        self._buyer_id = int(buyer_id)
        _LOGGER.debug("buyer_id обновлён: %s", self._buyer_id)

    def _build_mobile_headers(
        self, *, accept_version: str, include_bearer: bool = False
    ) -> dict[str, str]:
        headers = {
            "Accept": f"application/json; version={accept_version}",
            "App-Version": self._app_version,
            "X-App-Version": self._app_version,
            "X-Api-Source": self._api_source,
            "X-Source": self._api_source,
            "Platform": self._platform,
            "User-Agent": self._user_agent,
            "X-Device-Id": self._device_id,
            "Accept-Language": self._accept_language,
            "Content-Type": "application/json",
        }
        if self._mobile_token:
            headers["X-Api-User-Id"] = str(self._mobile_token.user_id)
        if include_bearer and self._mobile_token:
            headers[HEADER_AUTHORIZATION] = f"Bearer {self._mobile_token.token}"
        return headers

    def _build_yard_headers(self) -> dict[str, str]:
        """Заголовки cams.is74.ru по формату официального мобильного клиента."""

        self._ensure_mobile_token()
        assert self._mobile_token is not None
        return {
            "Accept": "application/json",
            "App-Version": YARD_APP_VERSION,
            "X-App-Version": YARD_APP_VERSION,
            "X-Api-Source": self._api_source,
            "X-Source": self._api_source,
            "Platform": self._platform,
            "User-Agent": YARD_USER_AGENT,
            "X-Device-Id": self._device_id,
            "X-api-profile-id": str(self._mobile_token.profile_id),
            "X-Api-User-Id": str(self._mobile_token.user_id),
            "Accept-Language": self._accept_language,
            "Content-Type": "application/json",
            HEADER_AUTHORIZATION: f"Bearer {self._mobile_token.token}",
        }

    def _build_crm_headers(
        self, *, include_crm_bearer: bool, include_mobile_bearer: bool = False
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "App-Version": self._app_version,
            "X-App-Version": self._app_version,
            "X-Api-Source": self._api_source,
            "X-Source": self._api_source,
            "Platform": self._platform,
            "X-api-profile-id": str(self._mobile_token.profile_id)
            if self._mobile_token and self._mobile_token.profile_id
            else "",
            "User-Agent": self._user_agent,
            "X-Device-Id": self._device_id,
            "Accept-Language": self._accept_language,
            "Content-Type": "application/json",
        }
        if include_mobile_bearer and self._mobile_token:
            headers[HEADER_AUTHORIZATION] = f"Bearer {self._mobile_token.token}"
        if include_crm_bearer and self._crm_token:
            headers[HEADER_AUTHORIZATION] = f"Bearer {self._crm_token.token}"
        return headers

    async def _request_mobile(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        accept_version: str = "v2",
    ) -> Any:
        merged = self._build_mobile_headers(accept_version=accept_version)
        if headers:
            merged.update(headers)
        return await self._request(
            base_url=self._api_base_url,
            method=method,
            endpoint=endpoint,
            headers=merged,
            json=json,
            params=params,
        )

    async def _request_crm(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_crm_token: bool = False,
        use_mobile_token: bool = False,
    ) -> Any:
        merged = self._build_crm_headers(
            include_crm_bearer=use_crm_token,
            include_mobile_bearer=use_mobile_token,
        )
        if headers:
            merged.update(headers)
        return await self._request(
            base_url=self._crm_base_url,
            method=method,
            endpoint=endpoint,
            headers=merged,
            json=json,
        )

    async def _request(
        self,
        *,
        base_url: str,
        method: str,
        endpoint: str,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Передать запрос выделенному безопасному transport-слою."""

        return await self._transport.async_request(
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            headers=headers,
            json=json,
            params=params,
        )

    def _ensure_mobile_token(self) -> None:
        if self._mobile_token is None:
            raise IntersvyazAuthError("Отсутствует мобильный токен")
        if self._mobile_token.is_expired:
            raise IntersvyazAuthError("Срок действия мобильного токена истёк")

    async def _ensure_crm_token(self) -> None:
        self._ensure_mobile_token()
        if self._crm_token and not self._crm_token.is_expired:
            return
        _LOGGER.info("CRM токен отсутствует/истёк; выполняем безопасное обновление")
        await self.async_authenticate_crm(self._buyer_id)


__all__ = [
    "CheckConfirmResult",
    "ConfirmAddress",
    "ConfirmContext",
    "CrmToken",
    "IntersvyazApiClient",
    "IntersvyazApiError",
    "IntersvyazAuthError",
    "IntersvyazNetworkError",
    "MobileToken",
    "RelayInfo",
    "RelayOpener",
    "generate_device_id",
]
