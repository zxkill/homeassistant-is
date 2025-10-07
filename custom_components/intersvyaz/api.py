"""Клиент для работы с API Intersvyaz."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    CONFIRM_CODE_ENDPOINT,
    DEFAULT_API_BASE_URL,
    DEFAULT_APP_VERSION,
    DEFAULT_DEVICE_ID,
    DEFAULT_TIMEOUT,
    HEADER_AUTHORIZATION,
    OPEN_DOOR_ENDPOINT,
    REFRESH_TOKEN_ENDPOINT,
    SEND_PHONE_ENDPOINT,
    TOKEN_EXPIRATION_MARGIN,
)

_LOGGER = logging.getLogger("custom_components.intersvyaz.api")


@dataclass
class TokenInfo:
    """Модель данных с информацией по токенам авторизации."""

    access_token: str
    refresh_token: str
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        """Проверить, истек ли срок жизни access-токена."""

        # Используем запас времени, чтобы избежать гонок в реальном коде
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(
            seconds=TOKEN_EXPIRATION_MARGIN
        )


class IntersvyazApiError(Exception):
    """Базовое исключение клиента Intersvyaz."""


class IntersvyazApiClient:
    """Асинхронный клиент для работы с облачными API Intersvyaz."""

    def __init__(
        self,
        session: ClientSession,
        api_base_url: str = DEFAULT_API_BASE_URL,
        request_timeout: int = DEFAULT_TIMEOUT,
        device_id: str = DEFAULT_DEVICE_ID,
        app_version: str = DEFAULT_APP_VERSION,
        platform: str = "iOS",
        api_source: str = "com.intersvyaz.lk",
        api_user_id: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """Инициализировать клиента.

        Args:
            session: Экземпляр `aiohttp.ClientSession`, созданный Home Assistant.
            api_base_url: Базовый URL облачного API Intersvyaz.
            request_timeout: Таймаут ожидания ответа от сервера в секундах.
        """

        self._session = session
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout = request_timeout
        self._token_info: Optional[TokenInfo] = None
        self._last_auth_context: Optional[Dict[str, Any]] = None
        self._device_id = device_id
        self._app_version = app_version
        self._platform = platform
        self._api_source = api_source
        self._api_user_id = api_user_id
        self._user_agent = user_agent or f"HA-Intersvyaz/{app_version}"
        self._base_headers = self._build_default_headers()
        _LOGGER.debug(
            "Инициализирован клиент Intersvyaz с базовым URL %s", self._api_base_url
        )

    @property
    def token_info(self) -> Optional[TokenInfo]:
        """Вернуть текущую информацию о токенах."""

        return self._token_info

    @property
    def last_auth_context(self) -> Optional[Dict[str, Any]]:
        """Вернуть данные последней отправки номера телефона (authId, сообщение и т.д.)."""

        return self._last_auth_context

    def set_token_info(self, token_info: TokenInfo) -> None:
        """Сохранить актуальные токены в клиенте."""

        _LOGGER.debug(
            "Обновление токенов авторизации: expires_at=%s",
            token_info.expires_at.isoformat(),
        )
        self._token_info = token_info

    async def async_send_phone_number(self, phone_number: str) -> Dict[str, Any]:
        """Отправить номер телефона для начала процедуры авторизации."""

        payload = {
            "phone": phone_number,
            "deviceId": self._device_id,
            "checkSkipAuth": 1,
        }
        # Фиксируем полезную нагрузку, максимально приближенную к мобильному приложению,
        # чтобы бэкенд распознал устройство и отправил корректный тип подтверждения.
        _LOGGER.info("Отправка номера телефона %s для авторизации", phone_number)
        response = await self._request("POST", SEND_PHONE_ENDPOINT, json=payload)
        _LOGGER.debug("Ответ на отправку номера телефона: %s", response)
        self._last_auth_context = response
        return response

    async def async_confirm_code(
        self, phone_number: str, code: str, auth_id: Optional[str] = None
    ) -> TokenInfo:
        """Подтвердить SMS-код и получить токены."""

        payload: Dict[str, Any] = {
            "phone": phone_number,
            "confirmCode": code,
        }
        # Если сервер вернул authId на первом шаге, обязательно передаем его обратно.
        if auth_id:
            payload["authId"] = auth_id
        _LOGGER.info("Подтверждение кода для телефона %s", phone_number)
        response = await self._request("POST", CONFIRM_CODE_ENDPOINT, json=payload)
        _LOGGER.debug("Ответ на подтверждение кода: %s", response)

        # Если сервер вернул сообщение об ошибке без токенов, прерываем сценарий и
        # пробрасываем исключение с человеческо-читаемым текстом.
        api_message = self._extract_api_message(response)
        if api_message and not self._contains_token_payload(response):
            _LOGGER.warning(
                "Сервер сообщил об ошибке при подтверждении кода: %s", api_message
            )
            raise IntersvyazApiError(api_message)

        token_info = self._parse_token_response(response)
        self.set_token_info(token_info)
        return token_info

    async def async_refresh_token(self) -> TokenInfo:
        """Обновить токены авторизации с использованием refresh-токена."""

        if not self._token_info:
            raise IntersvyazApiError(
                "Невозможно обновить токен: отсутствует сохраненная информация"
            )

        payload = {"refresh_token": self._token_info.refresh_token}
        _LOGGER.info("Обновление токена авторизации")
        response = await self._request("POST", REFRESH_TOKEN_ENDPOINT, json=payload)
        _LOGGER.debug("Ответ на обновление токена: %s", response)

        token_info = self._parse_token_response(response)
        self.set_token_info(token_info)
        return token_info

    async def async_open_door(self) -> None:
        """Открыть дверь домофона через облачный API."""

        if not self._token_info:
            raise IntersvyazApiError(
                "Невозможно открыть дверь: отсутствует access-токен"
            )

        if self._token_info.is_expired:
            _LOGGER.warning(
                "Access-токен истек или скоро истечет, необходимо обновление"
            )
            await self.async_refresh_token()

        headers = {HEADER_AUTHORIZATION: f"Bearer {self._token_info.access_token}"}
        _LOGGER.info("Отправка запроса на открытие домофона")
        await self._request("GET", OPEN_DOOR_ENDPOINT, headers=headers)

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Выполнить HTTP-запрос к API Intersvyaz."""

        url = f"{self._api_base_url}{endpoint}"
        merged_headers = {**self._base_headers, **(headers or {})}
        _LOGGER.debug(
            "Подготовка запроса %s %s с телом %s и заголовками %s",
            method,
            url,
            json,
            merged_headers,
        )
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method,
                    url,
                    json=json,
                    headers=merged_headers,
                ) as response:
                    _LOGGER.debug(
                        "Получен ответ %s %s", response.status, response.reason
                    )
                    data = await self._handle_response(response)
                    _LOGGER.debug("Тело ответа: %s", data)
                    return data
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.exception("Ошибка при обращении к API Intersvyaz: %s", err)
            raise IntersvyazApiError("Ошибка сети при обращении к API Intersvyaz")

    async def _handle_response(self, response: ClientResponse) -> Dict[str, Any]:
        """Проверить статус ответа и вернуть JSON."""

        if response.status >= 400:
            text = await response.text()
            parsed_message: Optional[str] = None
            try:
                error_payload = json.loads(text)
            except ValueError:
                error_payload = None
            if isinstance(error_payload, dict):
                parsed_message = self._extract_api_message(error_payload)
            _LOGGER.error(
                "Сервер вернул ошибку %s: %s", response.status, text
            )
            raise IntersvyazApiError(
                parsed_message or f"API вернуло ошибку {response.status}: {text}"
            )

        try:
            return await response.json()
        except ValueError as err:
            _LOGGER.exception("Не удалось преобразовать ответ в JSON: %s", err)
            raise IntersvyazApiError("Ответ сервера не является корректным JSON")

    def _build_default_headers(self) -> Dict[str, str]:
        """Сформировать набор стандартных заголовков для запросов к API."""

        headers = {
            "Accept": "application/json; version=v2",
            "App-Version": self._app_version,
            "X-App-Version": self._app_version,
            "X-Api-Source": self._api_source,
            "X-Source": self._api_source,
            "Platform": self._platform,
            "User-Agent": self._user_agent,
            "X-Device-Id": self._device_id,
            "Content-Type": "application/json",
        }
        if self._api_user_id:
            headers["X-Api-User-Id"] = self._api_user_id
        return headers

    def _parse_token_response(self, data: Dict[str, Any]) -> TokenInfo:
        """Преобразовать ответ API с токенами в объект TokenInfo."""

        try:
            access_token = data["access_token"]
            refresh_token = data["refresh_token"]
            expires_in = int(data["expires_in"])
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.exception("Некорректный формат ответа при получении токенов: %s", err)
            raise IntersvyazApiError("Некорректный формат ответа при получении токенов")

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        _LOGGER.debug(
            "Получены токены: expires_in=%s секунд, expires_at=%s",
            expires_in,
            expires_at.isoformat(),
        )
        return TokenInfo(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    @staticmethod
    def _extract_api_message(payload: Optional[Dict[str, Any]]) -> Optional[str]:
        """Получить текстовое сообщение об ошибке из ответа API."""

        if not isinstance(payload, dict):
            return None

        message = payload.get("message")
        if isinstance(message, str):
            cleaned = message.strip()
            return cleaned or None
        return None

    @staticmethod
    def _contains_token_payload(payload: Optional[Dict[str, Any]]) -> bool:
        """Проверить, содержит ли ответ обязательные поля для токенов."""

        if not isinstance(payload, dict):
            return False
        required_keys = {"access_token", "refresh_token", "expires_in"}
        return required_keys.issubset(payload.keys())
