"""Константы интеграции Intersvyaz."""
from __future__ import annotations

DOMAIN = "intersvyaz"
LOGGER_NAME = "custom_components.intersvyaz"

# API endpoints
DEFAULT_API_BASE_URL = "https://api.is74.ru"
DEFAULT_CRM_BASE_URL = "https://td-crm.is74.ru"
DEFAULT_CAMERAS_BASE_URL = "https://cams.is74.ru"
YARD_WITH_GROUP_ENDPOINT = "/api/yard-with-group"
SEND_PHONE_ENDPOINT = "/mobile/auth/get-confirm"
CHECK_CONFIRM_ENDPOINT = "/mobile/auth/check-confirm"
GET_TOKEN_ENDPOINT = "/mobile/auth/get-token"
USER_INFO_ENDPOINT = "/user/user"
BALANCE_ENDPOINT = "/user/balance"
TOKEN_INFO_ENDPOINT = "/token/info"
RELAYS_ENDPOINT = "/domofon/relays"
CRM_AUTH_ENDPOINT = "/api/auth-lk"
CRM_OPEN_DOOR_ENDPOINT_TEMPLATE = "/api/open/{mac}/{door_id}"

# Mobile client compatibility
DEFAULT_APP_VERSION = "2.11.0"
DEFAULT_PLATFORM = "iOS"
DEFAULT_API_SOURCE = "com.intersvyaz.lk"
DEFAULT_BUYER_ID = 1
DEFAULT_USER_AGENT = "20250909164306"
YARD_APP_VERSION = "2.19.0"
YARD_USER_AGENT = "20260707121700"
HEADER_AUTHORIZATION = "Authorization"

# Timings
DEFAULT_TIMEOUT = 30
TOKEN_EXPIRATION_MARGIN = 60
DEFAULT_UPDATE_INTERVAL_MINUTES = 10
DOOR_LINK_REFRESH_INTERVAL_HOURS = 6
YARD_CAMERA_REFRESH_INTERVAL_HOURS = 6
YARD_STREAM_PROBE_TIMEOUT_SECONDS = 8
YARD_STREAM_PROBE_CACHE_SECONDS = 30
CAMERA_FRAME_INTERVAL_SECONDS = 2
SNAPSHOT_CACHE_TTL_SECONDS = 1.0
SNAPSHOT_MAX_BYTES = 12 * 1024 * 1024
FACE_RECOGNITION_DISTANCE_THRESHOLD = 0.30
FACE_RECOGNITION_COOLDOWN_SECONDS = 30
FACE_EVENT_COOLDOWN_SECONDS = 10
FACE_REQUIRED_MATCHES_DEFAULT = 3
FACE_REQUIRED_MATCHES_MIN = 1
FACE_REQUIRED_MATCHES_MAX = 5

# Config entry data
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
CONF_DOOR_OPEN_LINK = "door_open_link"

# Recognition options
CONF_KNOWN_FACES = "known_faces"
CONF_FACE_NAME = "face_name"
CONF_FACE_PERSON_ENTITY_ID = "face_person_entity_id"
CONF_FACE_ENCODING = "face_encoding"
CONF_FACE_ENGINE = "face_engine"
FACE_ENGINE_PORTABLE_V1 = "portable_face_v1"
# Старый ID сохраняем только для корректной миграции/диагностики descriptors 2.0.5-2.0.6.
CONF_FACE_IMAGE = "face_image"
CONF_BACKGROUND_CAMERAS = "background_cameras"
CONF_RECOGNITION_MODE = "recognition_mode"
CONF_RECOGNITION_THRESHOLD = "recognition_threshold"
CONF_RECOGNITION_REQUIRED_MATCHES = "recognition_required_matches"
CONF_AUTO_OPEN_COOLDOWN_SECONDS = "auto_open_cooldown_seconds"
CONF_FACE_EVENT_COOLDOWN_SECONDS = "face_event_cooldown_seconds"

RECOGNITION_MODE_OFF = "off"
RECOGNITION_MODE_OBSERVE = "observe"
RECOGNITION_MODE_AUTO_OPEN = "auto_open"
RECOGNITION_MODES = (
    RECOGNITION_MODE_OFF,
    RECOGNITION_MODE_OBSERVE,
    RECOGNITION_MODE_AUTO_OPEN,
)
DEFAULT_RECOGNITION_MODE = RECOGNITION_MODE_OBSERVE

# Services/actions
SERVICE_OPEN_DOOR = "open_door"
SERVICE_ADD_KNOWN_FACE = "add_known_face"
SERVICE_REMOVE_KNOWN_FACE = "remove_known_face"

# Event bus compatibility names
EVENT_FACE_RECOGNIZED = "intersvyaz_face_recognized"
EVENT_UNKNOWN_PERSON = "intersvyaz_unknown_person"
EVENT_DOOR_OPENED = "intersvyaz_door_opened"
EVENT_DOOR_OPEN_FAILED = "intersvyaz_door_open_failed"

# Event entity types
DOOR_EVENT_FACE_RECOGNIZED = "face_recognized"
DOOR_EVENT_UNKNOWN_PERSON = "unknown_person"
DOOR_EVENT_OPENED = "door_opened"
DOOR_EVENT_OPEN_FAILED = "door_open_failed"
DOOR_EVENT_TYPES = (
    DOOR_EVENT_FACE_RECOGNIZED,
    DOOR_EVENT_UNKNOWN_PERSON,
    DOOR_EVENT_OPENED,
    DOOR_EVENT_OPEN_FAILED,
)

# Dispatcher
SIGNAL_DOOR_EVENT = f"{DOMAIN}_door_event"

# Legacy hass.data keys kept only for compatibility with older tests/installations.
# New 2.0 code stores per-entry runtime state in ConfigEntry.runtime_data.
DATA_API_CLIENT = "api_client"
DATA_CONFIG = "config"
DATA_COORDINATOR = "coordinator"
DATA_OPEN_DOOR = "open_door"
DATA_DOOR_OPENERS = "door_openers"
DATA_DOOR_REFRESH_UNSUB = "door_refresh_unsub"
DATA_FACE_MANAGER = "face_manager"
DATA_BACKGROUND_PROCESSOR = "background_processor"
DATA_SNAPSHOT_MANAGER = "snapshot_manager"
