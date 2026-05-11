"""Constants for the InCharge integration."""

DOMAIN = "vattenfall_incharge"
DEFAULT_NAME = "Vattenfall InCharge"
PUBLIC_STATION_POLL_MINUTES = 5
MYCHARGE_POLL_MINUTES = 15

# Public mobile app compatibility values. These are not user secrets; the
# Vattenfall backend expects requests that look like the official mobile app.
MOBILE_APP_SHA1 = "aa08ea4e0a721e5a5f8d81f1e7c7fbd87f8d3a5f"
MOBILE_APP_CRC = 0
APP_ACCEPT = "application/vnd.emobilitymobile.v16+json"
MOBILE_BASE_URL = "https://businessspecificapimanglobal.azure-api.net/emobility/"
MOBILE_APIM_KEY = "12c7d772faa84b92a8f13a22d7bd8638"

# Public My InCharge portal OAuth/API compatibility values.
MYCHARGE_AUTHORIZE_URL = "https://accounts.vattenfall.com/iamng/emob/oauth2/authorize"
MYCHARGE_TOKEN_URL = "https://accounts.vattenfall.com/iamng/emob/oauth2/token"
MYCHARGE_CLIENT_ID = "Ac5BFlCwsq4AgqvwaqBYv5uVLpJV"
MYCHARGE_REDIRECT_URI = "https://myincharge.vattenfall.com?authType=customer"
MYCHARGE_SCOPE = "openid profile email offline_access api"
MYCHARGE_TENANT_DOMAIN = "int.incharge"
MYCHARGE_SERVICE_PROVIDER = "ICSP"
PORTAL_APIM_KEY = "7685786eb9544d97923b0f01ac1b45d8"
PORTAL_BASE_URL = "https://businessspecificapimanglobal.azure-api.net"

CONF_APK_CRC = "apk_crc"
CONF_APK_SHA1 = "apk_sha1"
CONF_CHARGING_POINTS = "charging_points"
CONF_DEVICE_ID = "device_id"
CONF_MYCHARGE = "mycharge"
CONF_MYCHARGE_PROFILE = "profile"
CONF_MYCHARGE_TOKENS = "tokens"
CONF_SEARCH_TERM = "search_term"
CONF_X_TOKEN = "x_token"

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_SKIP_RELOAD_ONCE = "skip_reload_once"

SERVICE_REFRESH_MYCHARGE_TOKENS = "refresh_my_incharge_tokens"
SERVICE_DOWNLOAD_MYCHARGE_REPORT = "download_my_incharge_report"
NOTIFICATION_MYCHARGE_AUTH = f"{DOMAIN}_my_incharge_authentication_required"

PLATFORMS = ["sensor"]
