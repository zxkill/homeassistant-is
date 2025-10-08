"""Константы для интеграции Intersvyaz."""

# Домены и ключи данных Home Assistant
DOMAIN = "intersvyaz"
DATA_API_CLIENT = "api_client"
DATA_CONFIG = "config"
DATA_COORDINATOR = "coordinator"
DATA_OPEN_DOOR = "open_door"
DATA_DOOR_OPENERS = "door_openers"

# Базовые URL для API Intersvyaz (можно заменить при реальной интеграции)
DEFAULT_API_BASE_URL = "https://api.is74.ru"
DEFAULT_CRM_BASE_URL = "https://td-crm.is74.ru"

# Конечные точки основного API
SEND_PHONE_ENDPOINT = "/mobile/auth/get-confirm"
CHECK_CONFIRM_ENDPOINT = "/mobile/auth/check-confirm"
GET_TOKEN_ENDPOINT = "/mobile/auth/get-token"
USER_INFO_ENDPOINT = "/user/user"
BALANCE_ENDPOINT = "/user/balance"
TOKEN_INFO_ENDPOINT = "/token/info"
RELAYS_ENDPOINT = "/domofon/relays"

# Конечные точки CRM
CRM_AUTH_ENDPOINT = "/api/auth-lk"
# Шаблон конечной точки открытия домофона.
CRM_OPEN_DOOR_ENDPOINT_TEMPLATE = "/api/open/{mac}/{door_id}"

# Значения по умолчанию для технических параметров авторизации
DEFAULT_APP_VERSION = "2.11.0"
DEFAULT_PLATFORM = "iOS"
DEFAULT_API_SOURCE = "com.intersvyaz.lk"
DEFAULT_BUYER_ID = 1

# Интервалы и таймауты
DEFAULT_TIMEOUT = 30
TOKEN_EXPIRATION_MARGIN = 60
DEFAULT_UPDATE_INTERVAL_MINUTES = 10

# Названия сервисов
SERVICE_OPEN_DOOR = "open_door"

# Ключи данных для конфигурации и состояний токенов
CONF_PHONE_NUMBER = "phone_number"
CONF_DEVICE_ID = "device_id"
CONF_USER_ID = "user_id"
CONF_PROFILE_ID = "profile_id"
CONF_MOBILE_TOKEN = "mobile_token"
CONF_MOBILE_ACCESS_BEGIN = "mobile_access_begin"
CONF_MOBILE_ACCESS_END = "mobile_access_end"
CONF_CRM_TOKEN = "crm_token"
CONF_CRM_ACCESS_BEGIN = "crm_access_begin"
CONF_CRM_ACCESS_END = "crm_access_end"
CONF_BUYER_ID = "buyer_id"
CONF_DOOR_MAC = "door_mac"
CONF_DOOR_ENTRANCE = "door_entrance"
CONF_RELAY_ID = "relay_id"
CONF_RELAY_NUM = "relay_num"
CONF_RELAY_PAYLOAD = "relay_payload"
CONF_DOOR_ADDRESS = "door_address"
CONF_DOOR_HAS_VIDEO = "door_has_video"
CONF_DOOR_IMAGE_URL = "door_image_url"
CONF_ENTRANCE_UID = "entrance_uid"

# Заголовки
HEADER_AUTHORIZATION = "Authorization"

# Прочие константы
LOGGER_NAME = "custom_components.intersvyaz"

