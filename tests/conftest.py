"""Фикстуры для тестов интеграции Intersvyaz."""

from importlib.util import find_spec

# Плагин aiohttp полезен для тестов с временным веб-сервером, но в CI/локальных
# окружениях библиотека может отсутствовать. Используем find_spec, чтобы
# подключить плагин только при наличии зависимости и не падать с ImportError.
if find_spec("aiohttp") is not None:
    pytest_plugins = ("aiohttp.pytest_plugin",)
else:  # pragma: no cover - ветка выполняется при отсутствии aiohttp
    pytest_plugins: tuple[str, ...] = ()
