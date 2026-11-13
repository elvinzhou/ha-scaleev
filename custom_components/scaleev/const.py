"""Constants for the ScaleEV integration."""

DOMAIN = "scaleev"

DEFAULT_API_BASE = "https://api-scaleev.demo.scaletech.xyz"
NETWORK_HOST_SUFFIX = ".scaleev.scaletech.xyz"

CONF_LINK = "link"
CONF_API_BASE = "api_base"
CONF_TENANT_SLUG = "tenant_slug"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DRIVER_ID = "driver_id"
CONF_LOCATION_ID = "location_id"
CONF_LOCATION_NAME = "location_name"
CONF_LOCATION_LAT = "location_latitude"
CONF_LOCATION_LON = "location_longitude"
CONF_STATION_ID = "station_id"
CONF_STATION_NAME = "station_name"
CONF_CONNECTOR_ID = "connector_id"

CONF_TARGET_KWH = "target_kwh"
CONF_CABLE_SENSOR = "cable_sensor"
CONF_VEHICLE_TRACKER = "vehicle_tracker"
CONF_CHARGE_ZONE = "charge_zone"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_PAY_TIMEOUT = "pay_timeout"
CONF_RADIUS = "radius"

DEFAULT_PAY_TIMEOUT = 10  # minutes
DEFAULT_RADIUS = 150  # metres around the location's coordinates
POLL_SECONDS = 10  # while waiting for payment authorization
NO_PAYMENT_WAIT = 120  # seconds to wait for confirmation when no payment is needed
PAID_STATUSES = ("authorized", "captured")
UNPAID_STATUS = "pending"

# States of the "Start status" sensor: why the last attempt did or did not start.
STATUS_IDLE = "idle"
STATUS_AUTO_OFF = "auto_start_off"
STATUS_AWAY = "away"
STATUS_ALREADY_CHARGING = "already_charging"
STATUS_STARTING = "starting"
STATUS_AWAITING_PAYMENT = "awaiting_payment"
STATUS_STARTED = "started"
STATUS_PAYMENT_TIMEOUT = "payment_timeout"
STATUS_NOT_CONFIRMED = "not_confirmed"
STATUS_FAILED = "failed"
STATUSES = [
    STATUS_IDLE, STATUS_AUTO_OFF, STATUS_AWAY, STATUS_ALREADY_CHARGING, STATUS_STARTING,
    STATUS_AWAITING_PAYMENT, STATUS_STARTED, STATUS_PAYMENT_TIMEOUT, STATUS_NOT_CONFIRMED, STATUS_FAILED,
]

NOTIFICATION_TAG = "scaleev_pay"
