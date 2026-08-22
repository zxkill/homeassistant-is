"""Доменные ошибки облачного API Intersvyaz."""


class IntersvyazApiError(Exception):
    """Базовая ошибка API Intersvyaz."""


class IntersvyazAuthError(IntersvyazApiError):
    """Credentials отсутствуют, отозваны или истекли."""


class IntersvyazNetworkError(IntersvyazApiError):
    """Временная сетевая ошибка, которую можно повторить позже."""
