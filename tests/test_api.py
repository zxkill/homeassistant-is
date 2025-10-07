from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Dict
import sys
import types

import pytest

aiohttp = pytest.importorskip(
    "aiohttp", reason="Тесты API требуют aiohttp для имитации облака"
)
ClientSession = aiohttp.ClientSession
web = aiohttp.web

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
sanitize_request_context = API_MODULE._sanitize_request_context
mask_string = API_MODULE._mask_string


@pytest.fixture
async def api_server(aiohttp_server):
    """Развернуть временный сервер, имитирующий API Intersvyaz."""

    app = web.Application()
    state: Dict[str, Any] = {
        "door_open_calls": 0,
        "crm_auth_calls": 0,
        "last_confirm_payload": None,
        "last_get_token_payload": None,
        "relays_requested": 0,
    }

    async def handle_get_confirm(request: web.Request) -> web.Response:
        payload = await request.json()
        state["confirm_payload"] = payload
        state["confirm_headers"] = dict(request.headers)
        if payload.get("phone") != "9001112233":
            return web.json_response({"message": "invalid phone"}, status=400)
        return web.json_response(
            {
                "authType": 1,
                "message": "Сейчас на номер<br>+7 (900) 111-22-33 позвонят.<br>Введите код.",
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
                        "USER_ID": "1000001",
                        "ADDRESS": "Москва, ул. Ленина, д. 1",
                    },
                    {
                        "USER_ID": "1000002",
                        "ADDRESS": "Москва, ул. Ленина, д. 2",
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
                "USER_ID": 1000001,
                "PROFILE_ID": 2000001,
                "TOKEN": "primary-token",
                "ACCESS_BEGIN": "2025-10-07 11:24:23",
                "ACCESS_END": "2026-10-07 11:24:23",
                "PHONE": 9001112233,
                "UNIQUE_DEVICE_ID": "00000000-0000-0000-0000-000000000001",
            }
        )

    async def handle_user_info(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer primary-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(
            {
                "USER_ID": 1000001,
                "LOGIN": "IVANOVI",
                "ACCOUNT_NUM": 7000001,
                "profileName": "Иванов Иван Иванович",
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

    async def handle_relays(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer primary-token":
            return web.json_response({"error": "unauthorized"}, status=401)
        state["relays_requested"] += 1
        return web.json_response(
            [
                {
                    "ADDRESS": "Москва, ул. Ленина, д. 1, подъезд 1",
                    "RELAY_ID": "50001",
                    "STATUS_CODE": "0",
                    "BUILDING_ID": "300001",
                    "MAC_ADDR": "08:13:CD:00:0D:7F",
                    "STATUS_TEXT": "OK",
                    "IS_MAIN": "1",
                    "HAS_VIDEO": "1",
                    "ENTRANCE_UID": "11111111-2222-3333-4444-555555555555",
                    "PORCH_NUM": "1",
                    "RELAY_TYPE": "Главный вход",
                    "SMART_INTERCOM": "0",
                    "NUM_BUILDING": "1",
                    "IMAGE_URL": "https://td-snapshots.is74.ru/mock.jpg",
                    "LINKS": {"open": "https://td-crm.is74.ru/api/open/08:13:CD:00:0D:7F/1"},
                    "OPENER": {
                        "type": "crm",
                        "relay_id": 50001,
                        "relay_num": 1,
                        "mac": "08:13:CD:00:0D:7F",
                    },
                }
            ]
        )

    async def handle_token_info(request: web.Request) -> web.Response:
        return web.json_response({"TOKEN": "primary-token"})

    async def handle_crm_auth(request: web.Request) -> web.Response:
        state["crm_auth_calls"] += 1
        payload = await request.json()
        # CRM сервис ожидает увидеть мобильный токен как в теле запроса,
        # так и в заголовке Authorization, повторяя реальное API.
        if payload.get("token") != "primary-token":
            return web.json_response({"message": "bad token"}, status=401)
        auth_header = request.headers.get("Authorization")
        if auth_header != "Bearer primary-token":
            return web.json_response({"message": "missing bearer"}, status=401)
        return web.json_response(
            {
                "USER_ID": 3000001,
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
    app.router.add_get("/domofon/relays", handle_relays)
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

        confirm = await client.async_request_confirmation("9001112233")
        assert confirm.auth_id == "auth-123"
        assert "TEST-DEVICE" in api_server.state["confirm_headers"].get("X-Device-Id", "")

        check_result = await client.async_check_confirmation("9001112233", "1234")
        assert len(check_result.addresses) == 2

        token = await client.async_get_mobile_token(check_result.auth_id, check_result.addresses[0].user_id)
        assert token.token == "primary-token"
        assert token.user_id == 1000001
        assert token.profile_id == 2000001

        user_info = await client.async_get_user_info()
        assert user_info["LOGIN"] == "IVANOVI"

        balance = await client.async_get_balance()
        assert balance["balance"] == "-338.84"

        crm_token = await client.async_authenticate_crm(1)
        assert crm_token.token == "crm-token"

        await client.async_open_door("08:53:CD:00:83:4E", 1)
        assert api_server.state["door_open_calls"] == 1

        snapshot = await client.async_fetch_account_snapshot()
        assert snapshot["user"]["USER_ID"] == 1000001
        assert snapshot["balance"]["blocked"]["text"] == "К оплате"

        relays = await client.async_get_relays()
        assert len(relays) == 1
        relay = relays[0]
        assert relay.mac == "08:13:CD:00:0D:7F"
        assert relay.opener and relay.opener.relay_num == 1
        assert relay.to_dict()["RELAY_ID"] == "50001"
        assert api_server.state["relays_requested"] == 1


def test_mask_string_behaviour() -> None:
    """Строки корректно маскируются для логов, сохраняя подсказку."""

    assert mask_string("1234567890", keep_ends=True) == "12***90"
    assert mask_string("abcd", keep_ends=False) == "***"
    assert mask_string("", keep_ends=True) == "***"


def test_sanitize_request_context_masks_sensitive_data() -> None:
    """Контекст запроса не содержит токены и полные телефоны после маскировки."""

    context = {
        "method": "POST",
        "url": "https://example/api",
        "headers": {
            "Authorization": "Bearer secret-token",
            "X-Device-Id": "ABCDEF123456",
        },
        "json": {
            "token": "secret-token",
            "phone": "+79001234567",
        },
        "params": {"confirmCode": "1234"},
    }
    sanitized = sanitize_request_context(context)
    assert sanitized["headers"]["Authorization"].startswith("Bearer ")
    assert sanitized["headers"]["Authorization"].endswith("***")
    assert sanitized["headers"]["X-Device-Id"].startswith("AB")
    assert sanitized["headers"]["X-Device-Id"].endswith("56")
    assert sanitized["json"]["token"] == "***"
    assert sanitized["json"]["phone"].startswith("+7")
    assert sanitized["json"]["phone"].endswith("67")
    assert sanitized["params"]["confirmCode"] == "***"


@pytest.mark.asyncio
async def test_open_door_triggers_crm_auth(api_server) -> None:
    """Проверить, что при отсутствии CRM токена выполняется авторизация."""

    base_url = str(api_server.make_url(""))
    async with ClientSession() as session:
        client = IntersvyazApiClient(session=session, api_base_url=base_url, crm_base_url=base_url)
        await client.async_request_confirmation("9001112233")
        check_result = await client.async_check_confirmation("9001112233", "1234")
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
