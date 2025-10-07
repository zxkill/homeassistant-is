"""Константы для интеграции Intersvyaz."""

# Домены и ключи данных Home Assistant
DOMAIN = "intersvyaz"
DATA_API_CLIENT = "api_client"
DATA_CONFIG = "config"

# Базовые URL для API Intersvyaz (можно заменить при реальной интеграции)
DEFAULT_API_BASE_URL = "https://api.is74.ru"
SEND_PHONE_ENDPOINT = "/mobile/auth/get-confirm"

#{
    #'checkSkipAuth': 1,
    #'phone': '9080485745',
    #'deviceId': '60113CFC-044B-435C-9679-BB89A2EE3DBA'
#}

CONFIRM_CODE_ENDPOINT = "/auth/code"
REFRESH_TOKEN_ENDPOINT = "/auth/refresh"
OPEN_DOOR_ENDPOINT = "/door/open"

# Интервалы и таймауты
DEFAULT_TIMEOUT = 30
TOKEN_EXPIRATION_MARGIN = 60

# Названия сервисов
SERVICE_OPEN_DOOR = "open_door"

# Ключи данных для конфигурации и состояний токенов
CONF_PHONE_NUMBER = "phone_number"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN_EXPIRES_AT = "access_token_expires_at"

# Заголовки
HEADER_AUTHORIZATION = "Authorization"

# Прочие константы
LOGGER_NAME = "custom_components.intersvyaz"
