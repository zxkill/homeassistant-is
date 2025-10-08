"""Высокоуровневый клиент для облачных API Intersvyaz."""
from __future__ import annotations

import asyncio
import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aiohttp import ClientError, ClientResponse, ClientSession

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
    DEFAULT_PLATFORM,
    DEFAULT_TIMEOUT,
    HEADER_AUTHORIZATION,
    SEND_PHONE_ENDPOINT,
    TOKEN_EXPIRATION_MARGIN,
    TOKEN_INFO_ENDPOINT,
    USER_INFO_ENDPOINT,
    GET_TOKEN_ENDPOINT,
    RELAYS_ENDPOINT,
)

_LOGGER = logging.getLogger("custom_components.intersvyaz.api")

# Набор ключей, которые необходимо маскировать полностью при логировании.
_FULL_MASK_KEYS = {
    "token",
    "confirmcode",
    "code",
    "password",
    "authid",
}

# Набор ключей, которые удобнее показывать частично (например, телефон или device_id).
_PARTIAL_MASK_KEYS = {
    "phone",
    "x-device-id",
    "unique_device_id",
}


@dataclass
class ConfirmContext:
    """Контекст подтверждения, возвращаемый запросом `get-confirm`."""

    auth_id: Optional[str]
    message: Optional[str]
    timeout_mins: Optional[int]
    timeout_default: Optional[int]
    confirm_type: Optional[int]


@dataclass
class ConfirmAddress:
    """Описание адреса из ответа `check-confirm`."""

    user_id: str
    address: str


@dataclass
class CheckConfirmResult:
    """Результат проверки кода подтверждения."""

    auth_id: Optional[str]
    addresses: List[ConfirmAddress]
    message: Optional[str]


@dataclass
class MobileToken:
    """Токен основного мобильного API."""

    token: str
    user_id: int
    profile_id: int
    access_begin: Optional[datetime]
    access_end: Optional[datetime]
    phone: Optional[str]
    unique_device_id: Optional[str]
    raw: Dict[str, Any]

    @property
    def is_expired(self) -> bool:
        """Проверить, истёк ли срок действия токена мобильного API."""

        if not self.access_end:
            return False
        # Добавляем запас по времени, чтобы исключить гонки.
        safe_end = self.access_end - timedelta(seconds=TOKEN_EXPIRATION_MARGIN)
        return datetime.now(timezone.utc) >= safe_end


@dataclass
class CrmToken:
    """Токен вспомогательной CRM-системы."""

    token: str
    user_id: Optional[int]
    access_begin: Optional[datetime]
    access_end: Optional[datetime]
    raw: Dict[str, Any]

    @property
    def is_expired(self) -> bool:
        """Проверить, истёк ли CRM-токен."""

        if not self.access_end:
            return False
        safe_end = self.access_end - timedelta(seconds=TOKEN_EXPIRATION_MARGIN)
        return datetime.now(timezone.utc) >= safe_end


class IntersvyazApiError(Exception):
    """Базовое исключение клиента Intersvyaz."""


@dataclass
class RelayOpener:
    """Описание CRM-параметров, необходимых для открытия домофона."""

    relay_id: Optional[int]
    relay_num: Optional[int]
    mac: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        """Вернуть словарь для сохранения в конфигурации."""

        return {
            "relay_id": self.relay_id,
            "relay_num": self.relay_num,
            "mac": self.mac,
        }


@dataclass
class RelayInfo:
    """Структурированное описание реле домофона."""

    address: str
    relay_id: Optional[str]
    status_code: Optional[str]
    building_id: Optional[str]
    mac: Optional[str]
    status_text: Optional[str]
    is_main: bool
    has_video: bool
    entrance_uid: Optional[str]
    porch_num: Optional[str]
    relay_type: Optional[str]
    relay_descr: Optional[str]
    smart_intercom: Optional[bool]
    num_building: Optional[str]
    letter_building: Optional[str]
    image_url: Optional[str]
    open_link: Optional[str]
    opener: Optional[RelayOpener]
    raw: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Вернуть объединённые данные домофона."""

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


def _mask_string(value: str, *, keep_ends: bool) -> str:
    """Скрыть часть строкового значения, сохранив подсказку для отладки."""

    if not value:
        return "***"
    if not keep_ends or len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def _sanitize_value(key: str, value: Any) -> Any:
    """Рекурсивно маскировать конфиденциальные данные в структуре запроса."""

    lowered_key = key.lower()
    if isinstance(value, dict):
        return {k: _sanitize_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(key, item) for item in value]
    if lowered_key in _FULL_MASK_KEYS:
        if isinstance(value, str):
            return _mask_string(value, keep_ends=False)
        return "***"
    if lowered_key in _PARTIAL_MASK_KEYS:
        if isinstance(value, str):
            return _mask_string(value, keep_ends=True)
        if isinstance(value, (int, float)):
            # Для числовых значений телефонов возвращаем последние цифры.
            stringified = str(value)
            return _mask_string(stringified, keep_ends=True)
        return "***"
    if lowered_key == "authorization" and isinstance(value, str):
        # Авторизационный заголовок имеет формат "Bearer <токен>",
        # поэтому маскируем только секретную часть.
        parts = value.split(" ", 1)
        if len(parts) == 2:
            return f"{parts[0]} {_mask_string(parts[1], keep_ends=False)}"
        return _mask_string(value, keep_ends=False)
    return value


def _sanitize_request_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Создать безопасную копию контекста запроса для логирования."""

    sanitized = deepcopy(context)
    if "headers" in sanitized and isinstance(sanitized["headers"], dict):
        sanitized["headers"] = {
            key: _sanitize_value(key, value)
            for key, value in sanitized["headers"].items()
        }
    if "json" in sanitized and isinstance(sanitized["json"], dict):
        sanitized["json"] = {
            key: _sanitize_value(key, value)
            for key, value in sanitized["json"].items()
        }
    if "params" in sanitized and isinstance(sanitized["params"], dict):
        sanitized["params"] = {
            key: _sanitize_value(key, value)
            for key, value in sanitized["params"].items()
        }
    return sanitized


def generate_device_id() -> str:
    """Сгенерировать псевдо-уникальный идентификатор устройства."""

    # Генерируем UUID4, приводим к верхнему регистру и сохраняем формат с дефисами.
    return str(uuid.uuid4()).upper()


class IntersvyazApiClient:
    """Асинхронный клиент, который инкапсулирует всю сетевую работу."""

    def __init__(
        self,
        session: ClientSession,
        *,
        api_base_url: str = DEFAULT_API_BASE_URL,
        crm_base_url: str = DEFAULT_CRM_BASE_URL,
        request_timeout: int = DEFAULT_TIMEOUT,
        device_id: Optional[str] = None,
        app_version: str = DEFAULT_APP_VERSION,
        platform: str = DEFAULT_PLATFORM,
        api_source: str = DEFAULT_API_SOURCE,
        buyer_id: int = DEFAULT_BUYER_ID,
        accept_language: str = "ru-RU",
        user_agent: Optional[str] = None,
    ) -> None:
        """Инициализировать клиента с типичными параметрами мобильного приложения."""

        self._session = session
        self._api_base_url = api_base_url.rstrip("/")
        self._crm_base_url = crm_base_url.rstrip("/")
        self._timeout = request_timeout
        self._device_id = device_id or generate_device_id()
        self._app_version = app_version
        self._platform = platform
        self._api_source = api_source
        self._buyer_id = buyer_id
        self._accept_language = accept_language
        self._user_agent = user_agent or "20250909164306"

        # Состояние текущей авторизации.
        self._mobile_token: Optional[MobileToken] = None
        self._crm_token: Optional[CrmToken] = None
        # Сохраняем последний отправленный запрос, чтобы вывести его при ошибке.
        self._last_request_context: Optional[Dict[str, Any]] = None

        _LOGGER.debug(
            "Создан клиент Intersvyaz: api_base_url=%s, crm_base_url=%s, device_id=%s",
            self._api_base_url,
            self._crm_base_url,
            self._device_id,
        )

    @property
    def mobile_token(self) -> Optional[MobileToken]:
        """Текущий мобильный токен (если имеется)."""

        return self._mobile_token

    @property
    def crm_token(self) -> Optional[CrmToken]:
        """Текущий токен CRM системы."""

        return self._crm_token

    @property
    def buyer_id(self) -> int:
        """Возвращает актуальный buyer_id."""

        return self._buyer_id

    # ---------------------------------------------------------------------
    # Публичные методы для сценария авторизации
    # ---------------------------------------------------------------------

    async def async_request_confirmation(self, phone_number: str) -> ConfirmContext:
        """Отправить номер телефона и получить инструкции по подтверждению."""

        payload = {
            "deviceId": self._device_id,
            "phone": phone_number,
            "checkSkipAuth": 1,
        }
        _LOGGER.info("Отправляем запрос подтверждения на номер %s", phone_number)
        response = await self._request_mobile(
            "POST",
            SEND_PHONE_ENDPOINT,
            json=payload,
            accept_version="v2",
        )
        _LOGGER.debug("Ответ на запрос подтверждения: %s", response)
        return ConfirmContext(
            auth_id=_safe_get(response, "authId"),
            message=_safe_get(response, "message"),
            timeout_mins=_safe_get(response, "timeoutMins"),
            timeout_default=_safe_get(response, "timeoutMinsDefault"),
            confirm_type=_safe_get(response, "confirmType"),
        )

    async def async_check_confirmation(
        self, phone_number: str, confirm_code: str
    ) -> CheckConfirmResult:
        """Проверить код подтверждения и получить список адресов пользователя."""

        payload = {
            "confirmCode": confirm_code,
            "phone": phone_number,
        }
        _LOGGER.info(
            "Отправляем код подтверждения для номера %s", phone_number
        )
        response = await self._request_mobile(
            "POST",
            CHECK_CONFIRM_ENDPOINT,
            json=payload,
            accept_version="v2",
        )
        _LOGGER.debug("Ответ на проверку кода подтверждения: %s", response)

        addresses_payload = response.get("addresses") if isinstance(response, dict) else None
        addresses: List[ConfirmAddress] = []
        if isinstance(addresses_payload, list):
            for item in addresses_payload:
                user_id = _safe_get(item, "USER_ID")
                address = _safe_get(item, "ADDRESS")
                if user_id and address:
                    addresses.append(ConfirmAddress(str(user_id), str(address)))

        return CheckConfirmResult(
            auth_id=_safe_get(response, "authId"),
            addresses=addresses,
            message=_safe_get(response, "message"),
        )

    async def async_get_mobile_token(
        self, auth_id: str, user_id: str
    ) -> MobileToken:
        """Получить токен мобильного API после успешного выбора договора."""

        payload = {
            "authId": auth_id,
            "userId": user_id,
        }
        _LOGGER.info("Запрашиваем мобильный токен для user_id=%s", user_id)
        response = await self._request_mobile(
            "POST",
            GET_TOKEN_ENDPOINT,
            json=payload,
            accept_version="v2",
        )
        _LOGGER.debug("Ответ на получение токена: %s", response)

        token = self._parse_mobile_token(response)
        self._mobile_token = token
        return token

    async def async_check_token(self) -> Dict[str, Any]:
        """Получить диагностическую информацию по мобильному токену."""

        self._ensure_mobile_token()
        headers = self._build_mobile_headers(accept_version="v2")
        headers[HEADER_AUTHORIZATION] = f"Bearer {self._mobile_token.token}"
        response = await self._request_mobile(
            "GET",
            TOKEN_INFO_ENDPOINT,
            headers=headers,
            accept_version="v2",
        )
        _LOGGER.debug("Ответ на проверку токена: %s", response)
        return response

    async def async_authenticate_crm(self, buyer_id: Optional[int] = None) -> CrmToken:
        """Авторизоваться во второй системе (CRM) с помощью токена мобильного API."""

        self._ensure_mobile_token()
        payload = {
            "token": self._mobile_token.token,
            "buyerId": buyer_id or self._buyer_id,
        }
        _LOGGER.info("Запрашиваем CRM токен для buyer_id=%s", payload["buyerId"])
        response = await self._request_crm(
            "POST",
            CRM_AUTH_ENDPOINT,
            json=payload,
            use_mobile_token=True,
        )
        _LOGGER.debug("Ответ CRM авторизации: %s", response)
        token = self._parse_crm_token(response)
        self._crm_token = token
        return token

    async def async_get_relays(
        self,
        *,
        pagination: int = 1,
        page_size: int = 30,
        main_first: int = 1,
        include_main: bool = True,
        include_shared: bool = True,
    ) -> List[RelayInfo]:
        """Получить перечень домофонов, доступных пользователю."""

        # Домофоны делятся на «основные» (isShared=0) и «расшаренные» (isShared=1).
        # Чтобы пользователь увидел полный список адресов, необходимо запросить
        # обе категории по отдельности и объединить результат вручную.
        if not include_main and not include_shared:
            _LOGGER.warning(
                "Переданы флаги include_main=%s и include_shared=%s, перечень домофонов "
                "будет пустым.",
                include_main,
                include_shared,
            )
            return []

        self._ensure_mobile_token()
        headers = self._build_mobile_headers(accept_version="v2", include_bearer=True)
        if self._mobile_token and self._mobile_token.profile_id:
            headers["X-api-profile-id"] = str(self._mobile_token.profile_id)

        batches: List[RelayInfo] = []
        seen_relays: set[tuple[Any, ...]] = set()

        async def _collect_batch(is_shared: int, label: str) -> None:
            """Выполнить запрос конкретной категории домофонов и накопить результат."""

            try:
                batch = await self._async_fetch_relays_batch(
                    headers=headers,
                    pagination=pagination,
                    page_size=page_size,
                    main_first=main_first,
                    is_shared=is_shared,
                    label=label,
                )
            except IntersvyazApiError as err:
                _LOGGER.warning(
                    "Не удалось получить %s домофоны: %s",
                    label,
                    err,
                )
                return

            for relay in batch:
                dedupe_key = (
                    (relay.entrance_uid or "").lower(),
                    (relay.mac or "").upper(),
                    getattr(getattr(relay, "opener", None), "relay_id", None),
                    getattr(getattr(relay, "opener", None), "relay_num", None),
                )
                if dedupe_key in seen_relays:
                    _LOGGER.debug(
                        "Пропускаем дублирующийся домофон is_shared=%s: %s",
                        is_shared,
                        relay.to_dict(),
                    )
                    continue
                seen_relays.add(dedupe_key)
                batches.append(relay)

        if include_main:
            await _collect_batch(0, "основные")
        if include_shared:
            await _collect_batch(1, "расшаренные")

        _LOGGER.info(
            "Собрано %s уникальных домофонов (main=%s, shared=%s)",
            len(batches),
            include_main,
            include_shared,
        )
        return batches

    async def _async_fetch_relays_batch(
        self,
        *,
        headers: Dict[str, str],
        pagination: int,
        page_size: int,
        main_first: int,
        is_shared: int,
        label: str,
    ) -> List[RelayInfo]:
        """Запросить и разобрать список домофонов конкретного типа."""

        params = {
            "pagination": pagination,
            "pageSize": page_size,
            "mainFirst": main_first,
            "isShared": is_shared,
        }
        _LOGGER.info(
            "Запрашиваем %s домофоны: pagination=%s pageSize=%s isShared=%s",
            label,
            pagination,
            page_size,
            is_shared,
        )
        response = await self._request_mobile(
            "GET",
            RELAYS_ENDPOINT,
            headers=headers,
            params=params,
            accept_version="v2",
        )
        if not isinstance(response, list):
            _LOGGER.error(
                "Ответ на список %s домофонов имеет неверный формат: %s",
                label,
                response,
            )
            raise IntersvyazApiError(
                "API вернуло неожиданный ответ при получении списка домофонов"
            )

        relays: List[RelayInfo] = []
        for item in response:
            if not isinstance(item, dict):
                _LOGGER.debug(
                    "Пропускаем некорректный элемент в списке %s домофонов: %s",
                    label,
                    item,
                )
                continue
            relays.append(self._parse_relay_info(item))

        _LOGGER.debug(
            "Получено %s домофонов категории %s",
            len(relays),
            label,
        )
        return relays

    async def async_open_door(self, mac: str, door_id: int) -> None:
        """Открыть домофон с указанным MAC-адресом."""

        await self._ensure_crm_token()
        assert self._crm_token is not None
        endpoint = CRM_OPEN_DOOR_ENDPOINT_TEMPLATE.format(mac=mac, door_id=door_id)
        headers = self._build_crm_headers(include_crm_bearer=True)
        _LOGGER.info(
            "Отправляем команду на открытие домофона mac=%s door_id=%s", mac, door_id
        )
        await self._request_crm(
            "GET",
            endpoint,
            headers=headers,
            use_crm_token=True,
        )
        _LOGGER.info(
            "CRM подтвердила открытие домофона mac=%s door_id=%s (ожидается статус 204)",
            mac,
            door_id,
        )

    # ------------------------------------------------------------------
    # Методы для получения информации о пользователе и балансе
    # ------------------------------------------------------------------

    async def async_get_user_info(self) -> Dict[str, Any]:
        """Получить данные профиля пользователя."""

        self._ensure_mobile_token()
        headers = self._build_mobile_headers(accept_version="v3")
        headers[HEADER_AUTHORIZATION] = f"Bearer {self._mobile_token.token}"
        response = await self._request_mobile(
            "GET",
            USER_INFO_ENDPOINT,
            headers=headers,
            accept_version="v3",
        )
        _LOGGER.debug("Данные профиля пользователя: %s", response)
        return response

    async def async_get_balance(self) -> Dict[str, Any]:
        """Получить информацию о балансе договора."""

        self._ensure_mobile_token()
        headers = self._build_mobile_headers(accept_version="v2")
        headers[HEADER_AUTHORIZATION] = f"Bearer {self._mobile_token.token}"
        if self._mobile_token.profile_id:
            headers["X-api-profile-id"] = str(self._mobile_token.profile_id)
        headers["X-Api-User-Id"] = str(self._mobile_token.user_id)
        response = await self._request_mobile(
            "GET",
            BALANCE_ENDPOINT,
            headers=headers,
            accept_version="v2",
        )
        _LOGGER.debug("Данные по балансу: %s", response)
        return response

    async def async_fetch_account_snapshot(self) -> Dict[str, Any]:
        """Комплексно получить профиль и баланс для координирующей сущности."""

        _LOGGER.debug("Запрашиваем снимок данных аккаунта")
        user_task = asyncio.create_task(self.async_get_user_info())
        balance_task = asyncio.create_task(self.async_get_balance())
        try:
            user_info, balance = await asyncio.gather(user_task, balance_task)
        except Exception:
            # В случае ошибки обязательно отменяем вторую задачу и пробрасываем исключение.
            user_task.cancel()
            balance_task.cancel()
            _LOGGER.exception("Не удалось обновить комплексные данные аккаунта")
            raise
        snapshot = {
            "user": user_info,
            "balance": balance,
            "mobile_token": self._mobile_token.raw if self._mobile_token else None,
            "crm_token": self._crm_token.raw if self._crm_token else None,
        }
        _LOGGER.debug("Комплексные данные аккаунта: %s", snapshot)
        return snapshot

    # ------------------------------------------------------------------
    # Методы настройки из конфигурации Home Assistant
    # ------------------------------------------------------------------

    def set_mobile_token(self, token_payload: Dict[str, Any]) -> None:
        """Восстановить мобильный токен из сохранённых данных конфигурации."""

        token = self._parse_mobile_token(token_payload)
        self._mobile_token = token
        _LOGGER.debug("Мобильный токен восстановлен из конфигурации")

    def set_crm_token(self, token_payload: Dict[str, Any]) -> None:
        """Восстановить CRM-токен из сохранённых данных."""

        token = self._parse_crm_token(token_payload)
        self._crm_token = token
        _LOGGER.debug("CRM токен восстановлен из конфигурации")

    def set_buyer_id(self, buyer_id: int) -> None:
        """Обновить идентификатор покупателя для CRM запросов."""

        self._buyer_id = buyer_id
        _LOGGER.info(
            "CRM идентификатор покупателя обновлён на %s для последующих запросов",
            buyer_id,
        )

    # ------------------------------------------------------------------
    # Внутренние утилиты
    # ------------------------------------------------------------------

    def _build_mobile_headers(
        self,
        *,
        accept_version: str,
        include_bearer: bool = False,
    ) -> Dict[str, str]:
        """Сформировать заголовки, соответствующие мобильному приложению."""

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

    def _build_crm_headers(
        self, *, include_crm_bearer: bool, include_mobile_bearer: bool = False
    ) -> Dict[str, str]:
        """Сформировать набор заголовков для CRM запросов."""

        headers = {
            "Accept": "application/json",
            "App-Version": self._app_version,
            "X-App-Version": self._app_version,
            "X-Api-Source": self._api_source,
            "X-Source": self._api_source,
            "Platform": self._platform,
            "X-api-profile-id": (
                str(self._mobile_token.profile_id)
                if self._mobile_token and self._mobile_token.profile_id
                else ""
            ),
            "User-Agent": self._user_agent,
            "X-Device-Id": self._device_id,
            "Accept-Language": self._accept_language,
            "Content-Type": "application/json",
        }
        # Для первичной авторизации CRM сервер ожидает увидеть актуальный
        # мобильный токен в заголовке Authorization. Это поведение было
        # обнаружено при анализе сетевого трафика официального приложения,
        # поэтому даём возможность явно добавить соответствующий заголовок.
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
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        accept_version: str = "v2",
    ) -> Dict[str, Any]:
        """Выполнить HTTP-запрос к основному API."""

        merged_headers = self._build_mobile_headers(accept_version=accept_version)
        if headers:
            merged_headers.update(headers)
        return await self._request(
            base_url=self._api_base_url,
            method=method,
            endpoint=endpoint,
            headers=merged_headers,
            json=json,
            params=params,
        )

    async def _request_crm(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_crm_token: bool = False,
        use_mobile_token: bool = False,
    ) -> Dict[str, Any]:
        """Выполнить HTTP-запрос к CRM-системе."""

        # По умолчанию CRM использует отдельный JWT. Однако для первичного
        # обмена необходимо передать мобильный токен, поэтому параметры
        # ``use_crm_token`` и ``use_mobile_token`` позволяют гибко управлять
        # тем, какой заголовок Authorization будет сформирован.

        merged_headers = self._build_crm_headers(
            include_crm_bearer=use_crm_token,
            include_mobile_bearer=use_mobile_token,
        )
        if headers:
            merged_headers.update(headers)
        return await self._request(
            base_url=self._crm_base_url,
            method=method,
            endpoint=endpoint,
            headers=merged_headers,
            json=json,
        )

    async def _request(
        self,
        *,
        base_url: str,
        method: str,
        endpoint: str,
        headers: Dict[str, str],
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Универсальная обёртка над HTTP-запросом."""

        url = f"{base_url}{endpoint}"
        request_context = {
            "method": method,
            "url": url,
            "headers": headers,
            "json": json or {},
            "params": params or {},
        }
        self._last_request_context = _sanitize_request_context(request_context)
        _LOGGER.debug(
            "Готовим запрос к API: %s",
            self._last_request_context,
        )
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                ) as response:
                    _LOGGER.debug(
                        "Получен ответ %s %s", response.status, response.reason
                    )
                    return await self._handle_response(response)
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.exception("Ошибка при обращении к API Intersvyaz: %s", err)
            raise IntersvyazApiError("Ошибка сети при обращении к API Intersvyaz")

    async def _handle_response(self, response: ClientResponse) -> Dict[str, Any]:
        """Проверить статус ответа и преобразовать тело в JSON."""

        text = await response.text()
        if response.status >= 400:
            _LOGGER.error(
                "Сервер вернул ошибку %s: %s. Контекст запроса: %s",
                response.status,
                text,
                self._last_request_context,
            )
            raise IntersvyazApiError(
                f"API вернуло ошибку {response.status}: {text}"
            )
        if not text:
            return {}
        try:
            data = await response.json()
        except ValueError as err:
            _LOGGER.exception("Не удалось декодировать JSON: %s", err)
            raise IntersvyazApiError("Ответ сервера не является корректным JSON")
        return data

    def _parse_mobile_token(self, payload: Dict[str, Any]) -> MobileToken:
        """Преобразовать словарь API к структуре MobileToken."""

        token = str(payload.get("TOKEN"))
        if not token:
            raise IntersvyazApiError("В ответе отсутствует мобильный токен")
        user_id = _safe_int(payload, "USER_ID")
        profile_id = _safe_int(payload, "PROFILE_ID")
        access_begin = _parse_datetime(payload.get("ACCESS_BEGIN"))
        access_end = _parse_datetime(payload.get("ACCESS_END"))
        phone = payload.get("PHONE")
        unique_device_id = payload.get("UNIQUE_DEVICE_ID")
        mobile_token = MobileToken(
            token=token,
            user_id=user_id,
            profile_id=profile_id,
            access_begin=access_begin,
            access_end=access_end,
            phone=str(phone) if phone is not None else None,
            unique_device_id=unique_device_id,
            raw=dict(payload),
        )
        _LOGGER.debug(
            "Получен мобильный токен: user_id=%s profile_id=%s access_end=%s",
            user_id,
            profile_id,
            access_end,
        )
        return mobile_token

    def _parse_crm_token(self, payload: Dict[str, Any]) -> CrmToken:
        """Преобразовать ответ CRM авторизации."""

        token = str(payload.get("TOKEN"))
        if not token:
            raise IntersvyazApiError("В ответе отсутствует CRM токен")
        user_id = _safe_int(payload, "USER_ID") if "USER_ID" in payload else None
        access_begin = _parse_datetime(payload.get("ACCESS_BEGIN"))
        access_end = _parse_datetime(payload.get("ACCESS_END"))
        crm_token = CrmToken(
            token=token,
            user_id=user_id,
            access_begin=access_begin,
            access_end=access_end,
            raw=dict(payload),
        )
        _LOGGER.debug(
            "Получен CRM токен: user_id=%s access_end=%s",
            user_id,
            access_end,
        )
        return crm_token

    def _parse_relay_info(self, payload: Dict[str, Any]) -> RelayInfo:
        """Преобразовать словарь API в структуру RelayInfo."""

        def _as_bool(value: Any) -> bool:
            """Нормализовать различные представления булевых значений."""

            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value == "1" or value.lower() in {"true", "yes"}
            return False

        opener_payload = payload.get("OPENER")
        opener: Optional[RelayOpener] = None
        if isinstance(opener_payload, dict):
            relay_id = opener_payload.get("relay_id") or opener_payload.get("relayId")
            relay_num = opener_payload.get("relay_num") or opener_payload.get("relayNum")
            relay_id_int: Optional[int]
            relay_num_int: Optional[int]
            try:
                relay_id_int = int(relay_id) if relay_id is not None else None
            except (TypeError, ValueError):
                relay_id_int = None
            try:
                relay_num_int = int(relay_num) if relay_num is not None else None
            except (TypeError, ValueError):
                relay_num_int = None
            opener = RelayOpener(
                relay_id=relay_id_int,
                relay_num=relay_num_int,
                mac=opener_payload.get("mac"),
            )

        relay_info = RelayInfo(
            address=str(payload.get("ADDRESS") or ""),
            relay_id=str(payload.get("RELAY_ID")) if payload.get("RELAY_ID") else None,
            status_code=str(payload.get("STATUS_CODE"))
            if payload.get("STATUS_CODE")
            else None,
            building_id=str(payload.get("BUILDING_ID"))
            if payload.get("BUILDING_ID")
            else None,
            mac=payload.get("MAC_ADDR") or payload.get("mac"),
            status_text=payload.get("STATUS_TEXT"),
            is_main=_as_bool(payload.get("IS_MAIN")),
            has_video=_as_bool(payload.get("HAS_VIDEO")),
            entrance_uid=payload.get("ENTRANCE_UID"),
            porch_num=str(payload.get("PORCH_NUM")) if payload.get("PORCH_NUM") else None,
            relay_type=payload.get("RELAY_TYPE"),
            relay_descr=payload.get("RELAY_DESCR"),
            smart_intercom=_as_bool(payload.get("SMART_INTERCOM")),
            num_building=str(payload.get("NUM_BUILDING"))
            if payload.get("NUM_BUILDING")
            else None,
            letter_building=payload.get("LETTER_BUILDING"),
            image_url=payload.get("IMAGE_URL"),
            open_link=_safe_get(payload.get("LINKS"), "open"),
            opener=opener,
            raw=dict(payload),
        )
        _LOGGER.debug("Распарсено реле домофона: %s", relay_info)
        return relay_info

    def _ensure_mobile_token(self) -> None:
        """Убедиться, что мобильный токен установлен."""

        if not self._mobile_token:
            raise IntersvyazApiError(
                "Отсутствует мобильный токен. Повторите авторизацию через конфигурацию."
            )
        if self._mobile_token.is_expired:
            raise IntersvyazApiError(
                "Срок действия мобильного токена истёк. Повторите авторизацию."
            )

    async def _ensure_crm_token(self) -> None:
        """Убедиться, что CRM-токен валиден, при необходимости переавторизоваться."""

        if self._crm_token and not self._crm_token.is_expired:
            return
        _LOGGER.debug("CRM токен отсутствует или истёк, требуется переавторизация")
        await self.async_authenticate_crm()


def _safe_get(payload: Optional[Dict[str, Any]], key: str) -> Optional[Any]:
    """Безопасно получить значение из словаря."""

    if not isinstance(payload, dict):
        return None
    return payload.get(key)


def _safe_int(payload: Dict[str, Any], key: str) -> int:
    """Сконвертировать значение из словаря в целое число."""

    value = payload.get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise IntersvyazApiError(f"Поле {key} отсутствует или не является числом") from None


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Преобразовать строку формата `%Y-%m-%d %H:%M:%S` в datetime с UTC."""

    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        _LOGGER.warning("Не удалось распарсить дату %s", value)
        return None
    return parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "ConfirmContext",
    "ConfirmAddress",
    "CheckConfirmResult",
    "MobileToken",
    "CrmToken",
    "RelayInfo",
    "RelayOpener",
    "IntersvyazApiClient",
    "IntersvyazApiError",
    "generate_device_id",
]
