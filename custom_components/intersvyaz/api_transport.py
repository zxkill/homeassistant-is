"""Безопасный HTTP transport облачного API Intersvyaz."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .api_errors import IntersvyazApiError, IntersvyazAuthError, IntersvyazNetworkError
from .security import redact_error_text, redact_url, sanitize_request_context

_LOGGER = logging.getLogger("custom_components.intersvyaz.api_transport")


class IntersvyazHttpTransport:
    """HTTP-запросы, timeout и безопасный request context."""

    def __init__(self, session: ClientSession, timeout: int) -> None:
        self._session = session
        self._timeout = int(timeout)
        self.last_request_context: dict[str, Any] | None = None

    async def async_request(
        self,
        *,
        base_url: str,
        method: str,
        endpoint: str,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{base_url}{endpoint}"
        self.last_request_context = sanitize_request_context(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json or {},
                "params": params or {},
            }
        )
        _LOGGER.debug("API request=%s", self.last_request_context)
        safe_endpoint = redact_url(endpoint) or "<unknown>"
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
                        "API response status=%s endpoint=%s", response.status, safe_endpoint
                    )
                    return await self._async_handle_response(response)
        except (ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "Временная ошибка API: endpoint=%s error=%s",
                safe_endpoint,
                type(err).__name__,
            )
            raise IntersvyazNetworkError(
                "Ошибка сети при обращении к API Интерсвязи"
            ) from err

    async def _async_handle_response(self, response: ClientResponse) -> Any:
        text = await response.text()
        if response.status in (401, 403):
            _LOGGER.warning(
                "API отклонил авторизацию: status=%s request=%s",
                response.status,
                self.last_request_context,
            )
            raise IntersvyazAuthError("Требуется повторная авторизация Интерсвязи")
        if response.status >= 400:
            _LOGGER.warning(
                "API error status=%s body=%s request=%s",
                response.status,
                redact_error_text(text),
                self.last_request_context,
            )
            raise IntersvyazApiError(f"API Интерсвязи вернул ошибку {response.status}")
        if not text:
            return {}
        try:
            return await response.json()
        except (ValueError, TypeError) as err:
            _LOGGER.warning(
                "Некорректный JSON API: status=%s content_type=%s",
                response.status,
                response.headers.get("Content-Type"),
            )
            raise IntersvyazApiError("Ответ сервера не является корректным JSON") from err
