"""Тесты для клиента API Intersvyaz."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Dict

import sys
import types

import pytest
from aiohttp import ClientSession, web

# Загружаем модули интеграции напрямую по путям, чтобы не требовать установку Home Assistant в окружении тестов.
PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz"


def _load_module(module_name: str, relative_path: str):
    """Вспомогательная функция для загрузки модулей интеграции."""

    spec = spec_from_file_location(module_name, PACKAGE_ROOT / relative_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module

sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
intersvyaz_module = types.ModuleType("custom_components.intersvyaz")
intersvyaz_module.__path__ = [str(PACKAGE_ROOT)]  # type: ignore[attr-defined]
sys.modules["custom_components.intersvyaz"] = intersvyaz_module

CONST_MODULE = _load_module("custom_components.intersvyaz.const", "const.py")
API_MODULE = _load_module("custom_components.intersvyaz.api", "api.py")

IntersvyazApiClient = API_MODULE.IntersvyazApiClient
IntersvyazApiError = API_MODULE.IntersvyazApiError
TokenInfo = API_MODULE.TokenInfo
DEFAULT_DEVICE_ID = CONST_MODULE.DEFAULT_DEVICE_ID


@pytest.fixture
async def api_server(aiohttp_server):
    """Создать тестовый сервер, имитирующий API Intersvyaz."""

    app = web.Application()
    state: Dict[str, Any] = {
        "door_open_requests": 0,
        "last_authorization_header": None,
        "last_phone_payload": None,
        "last_phone_headers": None,
        "last_confirm_payload": None,
    }

    async def handle_phone(request: web.Request) -> web.Response:
        payload = await request.json()
        state["last_phone_payload"] = payload
        state["last_phone_headers"] = dict(request.headers)
        if payload.get("phone") == "+70000000000" and payload.get("deviceId") == DEFAULT_DEVICE_ID:
            return web.json_response(
                {
                    "authType": 1,
                    "timeoutMins": None,
                    "message": "Сейчас на номер<br>+7 (908) 048-57-43 позвонят.",
                    "timeoutMinsDefault": 1,
                    "authId": "auth-id-123",
                    "confirmType": 1,
                }
            )
        return web.json_response({"status": "error"}, status=400)

    async def handle_code(request: web.Request) -> web.Response:
        payload = await request.json()
        state["last_confirm_payload"] = payload
        if payload.get("confirmCode") == "0000":
            return web.json_response(
                {"message": "Неверный код подтверждения"}
            )
        if payload.get("confirmCode") != "1234":
            return web.json_response(
                {"message": "Неверный код подтверждения"}, status=400
            )
        if payload.get("authId") != "auth-id-123":
            return web.json_response({"error": "invalid_auth"}, status=400)
        return web.json_response(
            {
                "access_token": "initial-access",
                "refresh_token": "initial-refresh",
                "expires_in": 3600,
            }
        )

    async def handle_refresh(request: web.Request) -> web.Response:
        payload = await request.json()
        if payload.get("refresh_token") != "initial-refresh":
            return web.json_response({"error": "invalid_refresh"}, status=401)
        return web.json_response(
            {
                "access_token": "refreshed-access",
                "refresh_token": "refreshed-refresh",
                "expires_in": 7200,
            }
        )

    async def handle_open(request: web.Request) -> web.Response:
        state["door_open_requests"] += 1
        state["last_authorization_header"] = request.headers.get("Authorization")
        return web.json_response({"opened": True})

    app.router.add_post("/mobile/auth/get-confirm", handle_phone)
    app.router.add_post("/mobile/auth/check-confirm", handle_code)
    app.router.add_post("/auth/refresh", handle_refresh)
    app.router.add_get("/door/open", handle_open)

    server = await aiohttp_server(app)
    server.state = state
    return server


@pytest.mark.asyncio
async def test_full_authorization_flow(api_server) -> None:
    """Проверить полный сценарий авторизации и открытия двери."""

    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=str(api_server.make_url("")))

        auth_context = await client.async_send_phone_number("+70000000000")
        assert auth_context["authId"] == "auth-id-123"
        assert client.last_auth_context == auth_context
        assert api_server.state["last_phone_payload"]["checkSkipAuth"] == 1
        assert (
            api_server.state["last_phone_headers"].get("X-Device-Id")
            == DEFAULT_DEVICE_ID
        )

        token_info = await client.async_confirm_code(
            "+70000000000",
            "1234",
            auth_id=auth_context["authId"],
        )
        assert isinstance(token_info, TokenInfo)
        assert token_info.access_token == "initial-access"
        assert api_server.state["last_confirm_payload"]["confirmCode"] == "1234"

        # Форсируем устаревание токена
        client.set_token_info(
            TokenInfo(
                access_token="expired",
                refresh_token="initial-refresh",
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )

        await client.async_open_door()
        assert api_server.state["door_open_requests"] == 1
        assert api_server.state["last_authorization_header"] == "Bearer refreshed-access"


@pytest.mark.asyncio
async def test_refresh_token_error(api_server) -> None:
    """Убедиться, что ошибки обновления токена корректно пробрасываются."""

    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=str(api_server.make_url("")))
        client.set_token_info(
            TokenInfo(
                access_token="any",
                refresh_token="wrong-refresh",
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )

        with pytest.raises(IntersvyazApiError):
            await client.async_open_door()


@pytest.mark.asyncio
async def test_request_unexpected_status(api_server) -> None:
    """Проверить обработку неожиданных статусов HTTP."""

    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=str(api_server.make_url("")))
        with pytest.raises(IntersvyazApiError):
            await client.async_send_phone_number("+79999999999")


@pytest.mark.asyncio
async def test_non_json_response(aiohttp_server) -> None:
    """Проверить реакцию клиента на не-JSON ответ."""

    async def handle_plain(request: web.Request) -> web.Response:
        return web.Response(text="not-json")

    app = web.Application()
    app.router.add_get("/door/open", handle_plain)
    server = await aiohttp_server(app)

    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=str(server.make_url("")))
        client.set_token_info(
            TokenInfo(
                access_token="token",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )

        with pytest.raises(IntersvyazApiError):
            await client.async_open_door()


@pytest.mark.asyncio
async def test_confirm_code_with_message_error(api_server) -> None:
    """Убедиться, что текст ошибки из API пробрасывается пользователю."""

    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=str(api_server.make_url("")))

        await client.async_send_phone_number("+70000000000")

        with pytest.raises(IntersvyazApiError) as err:
            await client.async_confirm_code(
                phone_number="+70000000000",
                code="0000",
                auth_id="auth-id-123",
            )

        assert "Неверный код подтверждения" in str(err.value)


def test_token_info_is_expired_property() -> None:
    """Проверить вычисление свойства is_expired."""

    token = TokenInfo(
        access_token="token",
        refresh_token="refresh",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    assert token.is_expired

    token = TokenInfo(
        access_token="token",
        refresh_token="refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert not token.is_expired
