"""Высокоуровневый клиент для облачных API Intersvyaz."""
from __future__ import annotations

import asyncio
import logging
import uuid
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
)

_LOGGER = logging.getLogger("custom_components.intersvyaz.api")


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
        )
        _LOGGER.debug("Ответ CRM авторизации: %s", response)
        token = self._parse_crm_token(response)
        self._crm_token = token
        return token

    async def async_open_door(self, mac: str, door_id: int) -> None:
        """Открыть домофон с указанным MAC-адресом."""

        await self._ensure_crm_token()
        assert self._crm_token is not None
        endpoint = CRM_OPEN_DOOR_ENDPOINT_TEMPLATE.format(mac=mac, door_id=door_id)
        headers = self._build_crm_headers(include_bearer=True)
        _LOGGER.info(
            "Отправляем команду на открытие домофона mac=%s door_id=%s", mac, door_id
        )
        await self._request_crm("GET", endpoint, headers=headers)

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

    def _build_crm_headers(self, *, include_bearer: bool) -> Dict[str, str]:
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
        if include_bearer and self._crm_token:
            headers[HEADER_AUTHORIZATION] = f"Bearer {self._crm_token.token}"
        return headers

    async def _request_mobile(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[Dict[str, Any]] = None,
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
        )

    async def _request_crm(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Выполнить HTTP-запрос к CRM-системе."""

        merged_headers = self._build_crm_headers(include_bearer=False)
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
    ) -> Dict[str, Any]:
        """Универсальная обёртка над HTTP-запросом."""

        url = f"{base_url}{endpoint}"
        _LOGGER.debug(
            "Запрос %s %s: headers=%s json=%s", method, url, headers, json
        )
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method,
                    url,
                    json=json,
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
                "Сервер вернул ошибку %s: %s", response.status, text
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
    "IntersvyazApiClient",
    "IntersvyazApiError",
    "generate_device_id",
]
