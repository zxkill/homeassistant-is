from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Dict
import sys
import types

import pytest
from aiohttp import ClientSession, web

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "intersvyaz"


def _load_module(module_name: str, relative_path: str):
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

API_MODULE = _load_module("custom_components.intersvyaz.api", "api.py")
IntersvyazApiClient = API_MODULE.IntersvyazApiClient
IntersvyazApiError = API_MODULE.IntersvyazApiError


@pytest.fixture
async def api_server(aiohttp_server):
    """Развернуть временный сервер, имитирующий API Intersvyaz."""

    app = web.Application()
    state: Dict[str, Any] = {
        "door_open_calls": 0,
        "crm_auth_calls": 0,
        "last_confirm_payload": None,
        "last_get_token_payload": None,
    }

    async def handle_get_confirm(request: web.Request) -> web.Response:
        payload = await request.json()
        state["confirm_payload"] = payload
        state["confirm_headers"] = dict(request.headers)
        if payload.get("phone") != "9080485744":
            return web.json_response({"message": "invalid phone"}, status=400)
        return web.json_response(
            {
                "authType": 1,
                "message": "Сейчас на номер<br>+7 (908) 048-57-43 позвонят.<br>Введите код.",
                "authId": "auth-123",
                "confirmType": 1,
            }
        )

    async def handle_check_confirm(request: web.Request) -> web.Response:
        payload = await request.json()
        state["last_confirm_payload"] = payload
        if payload.get("confirmCode") != "1234":
            return web.json_response({"message": "Неверный код подтверждения"})
        return web.json_response(
            {
                "authId": "auth-123",
                "addresses": [
                    {
                        "USER_ID": "1157556",
                        "ADDRESS": "Магнитогорск, ул. Строителей, д. 49",
                    },
                    {
                        "USER_ID": "1155801",
                        "ADDRESS": "Магнитогорск, пр-кт. Карла Маркса, д. 142",
                    },
                ],
            }
        )

    async def handle_get_token(request: web.Request) -> web.Response:
        payload = await request.json()
        state["last_get_token_payload"] = payload
        if payload.get("authId") != "auth-123":
            return web.json_response({"message": "invalid auth"}, status=401)
        return web.json_response(
            {
                "USER_ID": 1157556,
                "PROFILE_ID": 666213,
                "TOKEN": "primary-token",
                "ACCESS_BEGIN": "2025-10-07 11:24:23",
                "ACCESS_END": "2026-10-07 11:24:23",
                "PHONE": 9085867416,
                "UNIQUE_DEVICE_ID": "60113CFC-044B-435C-9679-BB89A2EE3DBA",
            }
        )

    async def handle_user_info(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer primary-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(
            {
                "USER_ID": 1157556,
                "LOGIN": "STADNIKS",
                "ACCOUNT_NUM": 9144937,
                "profileName": "Сергей Викторович Стадник",
                "roleName": "Владелец договора",
                "firm": {"NAME": "АО \"Интерсвязь\""},
            }
        )

    async def handle_balance(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer primary-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(
            {
                "balance": "-338.84",
                "blocked": {"text": "К оплате", "pay": "646"},
            }
        )

    async def handle_token_info(request: web.Request) -> web.Response:
        return web.json_response({"TOKEN": "primary-token"})

    async def handle_crm_auth(request: web.Request) -> web.Response:
        state["crm_auth_calls"] += 1
        payload = await request.json()
        if payload.get("token") != "primary-token":
            return web.json_response({"message": "bad token"}, status=401)
        return web.json_response(
            {
                "USER_ID": 635292,
                "TOKEN": "crm-token",
                "ACCESS_BEGIN": "2025-10-07 06:24:24",
                "ACCESS_END": "2026-01-07 06:24:24",
            }
        )

    async def handle_open(request: web.Request) -> web.Response:
        state["door_open_calls"] += 1
        if request.headers.get("Authorization") != "Bearer crm-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.Response(status=204)

    app.router.add_post("/mobile/auth/get-confirm", handle_get_confirm)
    app.router.add_post("/mobile/auth/check-confirm", handle_check_confirm)
    app.router.add_post("/mobile/auth/get-token", handle_get_token)
    app.router.add_get("/user/user", handle_user_info)
    app.router.add_get("/user/balance", handle_balance)
    app.router.add_get("/token/info", handle_token_info)
    app.router.add_post("/api/auth-lk", handle_crm_auth)
    app.router.add_get(r"/api/open/{mac}/{door_id}", handle_open)

    server = await aiohttp_server(app)
    server.state = state
    return server


@pytest.mark.asyncio
async def test_full_authorization_flow(api_server) -> None:
    """Проверить полный сценарий авторизации и получения данных."""

    base_url = str(api_server.make_url(""))
    async with ClientSession() as session:
        client = IntersvyazApiClient(
            session=session,
            api_base_url=base_url,
            crm_base_url=base_url,
            device_id="TEST-DEVICE",
        )

        confirm = await client.async_request_confirmation("9080485744")
        assert confirm.auth_id == "auth-123"
        assert "TEST-DEVICE" in api_server.state["confirm_headers"].get("X-Device-Id", "")

        check_result = await client.async_check_confirmation("9080485744", "1234")
        assert len(check_result.addresses) == 2

        token = await client.async_get_mobile_token(check_result.auth_id, check_result.addresses[0].user_id)
        assert token.token == "primary-token"
        assert token.user_id == 1157556
        assert token.profile_id == 666213

        user_info = await client.async_get_user_info()
        assert user_info["LOGIN"] == "STADNIKS"

        balance = await client.async_get_balance()
        assert balance["balance"] == "-338.84"

        crm_token = await client.async_authenticate_crm(1)
        assert crm_token.token == "crm-token"

        await client.async_open_door("08:53:CD:00:83:4E", 1)
        assert api_server.state["door_open_calls"] == 1

        snapshot = await client.async_fetch_account_snapshot()
        assert snapshot["user"]["USER_ID"] == 1157556
        assert snapshot["balance"]["blocked"]["text"] == "К оплате"


@pytest.mark.asyncio
async def test_open_door_triggers_crm_auth(api_server) -> None:
    """Проверить, что при отсутствии CRM токена выполняется авторизация."""

    base_url = str(api_server.make_url(""))
    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=base_url, crm_base_url=base_url)
        await client.async_request_confirmation("9080485744")
        check_result = await client.async_check_confirmation("9080485744", "1234")
        await client.async_get_mobile_token(check_result.auth_id, check_result.addresses[0].user_id)

        await client.async_open_door("08:53:CD:00:83:4E", 1)
        assert api_server.state["crm_auth_calls"] == 1
        assert api_server.state["door_open_calls"] == 1


@pytest.mark.asyncio
async def test_missing_mobile_token_raises(api_server) -> None:
    """Убедиться, что запросы без токена завершаются ошибкой."""

    base_url = str(api_server.make_url(""))
    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=base_url, crm_base_url=base_url)
        with pytest.raises(IntersvyazApiError):
            await client.async_get_user_info()


@pytest.mark.asyncio
async def test_request_confirmation_error(api_server) -> None:
    """Обработать ошибку подтверждения номера телефона."""

    base_url = str(api_server.make_url(""))
    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=base_url, crm_base_url=base_url)
        with pytest.raises(IntersvyazApiError):
            await client.async_request_confirmation("0000000000")
