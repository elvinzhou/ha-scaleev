"""Config, options and reauth flows."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ScaleEVAuthError, ScaleEVClient, ScaleEVError, parse_link
from .const import (
    CONF_API_BASE,
    CONF_CABLE_SENSOR,
    CONF_CHARGE_ZONE,
    CONF_CONNECTOR_ID,
    CONF_DRIVER_ID,
    CONF_EMAIL,
    CONF_LINK,
    CONF_LOCATION_ID,
    CONF_LOCATION_LAT,
    CONF_LOCATION_LON,
    CONF_LOCATION_NAME,
    CONF_NOTIFY_SERVICE,
    CONF_PASSWORD,
    CONF_PAY_TIMEOUT,
    CONF_RADIUS,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    CONF_TARGET_KWH,
    CONF_TENANT_SLUG,
    CONF_VEHICLE_TRACKER,
    DEFAULT_PAY_TIMEOUT,
    DEFAULT_RADIUS,
    DOMAIN,
)


def _settings_schema(hass) -> vol.Schema:
    services = sorted(
        f"notify.{name}"
        for name in hass.services.async_services_for_domain("notify")
        if name not in ("send_message", "persistent_notification")
    )
    # Newer integrations provide notify entities instead of their own action.
    services += sorted(e for e in hass.states.async_entity_ids("notify") if e not in services)
    return vol.Schema(
        {
            vol.Required(CONF_TARGET_KWH): NumberSelector(
                NumberSelectorConfig(min=1, max=250, step=0.5, unit_of_measurement="kWh", mode=NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_CABLE_SENSOR): EntitySelector(EntitySelectorConfig(domain="binary_sensor")),
            vol.Required(CONF_VEHICLE_TRACKER): EntitySelector(EntitySelectorConfig(domain="device_tracker")),
            vol.Required(CONF_RADIUS, default=DEFAULT_RADIUS): NumberSelector(
                NumberSelectorConfig(min=25, max=2000, step=25, unit_of_measurement="m", mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_CHARGE_ZONE): EntitySelector(EntitySelectorConfig(domain="zone")),
            vol.Required(CONF_NOTIFY_SERVICE): SelectSelector(
                SelectSelectorConfig(options=services, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_PAY_TIMEOUT, default=DEFAULT_PAY_TIMEOUT): NumberSelector(
                NumberSelectorConfig(min=1, max=60, unit_of_measurement="min", mode=NumberSelectorMode.BOX)
            ),
        }
    )


def _options(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        **user_input,
        CONF_PAY_TIMEOUT: float(user_input[CONF_PAY_TIMEOUT]),
        CONF_RADIUS: float(user_input[CONF_RADIUS]),
    }


def _settings_errors(user_input: dict[str, Any], has_coordinates: bool) -> dict[str, str]:
    """A zone is required when ScaleEV gave us no coordinates for the location."""
    if not has_coordinates and not user_input.get(CONF_CHARGE_ZONE):
        return {CONF_CHARGE_ZONE: "zone_required"}
    return {}


class ScaleEVConfigFlow(ConfigFlow, domain=DOMAIN):
    """Link -> login -> location -> station -> settings."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_base = ""
        self._slug = ""
        self._network = ""
        self._email = ""
        self._password = ""
        self._driver_id = ""
        self._client: ScaleEVClient | None = None
        self._locations: list[dict[str, Any]] = []
        self._location: dict[str, Any] = {}
        self._stations: list[dict[str, Any]] = []
        self._station: dict[str, Any] = {}
        self._connector_id = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return ScaleEVOptionsFlow()

    # -- 1. link -----------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                domain, guess = parse_link(user_input[CONF_LINK])
            except ValueError:
                errors[CONF_LINK] = "invalid_link"
            else:
                api_base = (user_input.get(CONF_API_BASE) or guess or "").rstrip("/")
                if not api_base:
                    errors[CONF_API_BASE] = "api_base_required"
                else:
                    client = ScaleEVClient(async_get_clientsession(self.hass), api_base)
                    try:
                        config = await client.async_tenant_config(domain)
                    except ScaleEVError:
                        errors["base"] = "cannot_connect"
                    else:
                        self._api_base = api_base
                        self._slug = config["slug"]
                        self._network = config.get("appName") or config["slug"]
                        return await self.async_step_login()
        schema = vol.Schema(
            {
                vol.Required(CONF_LINK): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Optional(CONF_API_BASE): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors,
            description_placeholders={"example": "https://yournetwork.scaleev.scaletech.xyz/map"},
        )

    # -- 2. login ----------------------------------------------------------
    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = ScaleEVClient(
                async_get_clientsession(self.hass), self._api_base, self._slug,
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD],
            )
            try:
                login = await client.async_login()
                self._locations = await client.async_locations()
            except ScaleEVAuthError:
                errors["base"] = "invalid_auth"
            except ScaleEVError:
                errors["base"] = "cannot_connect"
            else:
                self._client = client
                self._email = user_input[CONF_EMAIL]
                self._password = user_input[CONF_PASSWORD]
                self._driver_id = login["driver"]["id"]
                return await self.async_step_location()
        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(
            step_id="login", data_schema=schema, errors=errors, description_placeholders={"network": self._network}
        )

    # -- 3. location -------------------------------------------------------
    async def async_step_location(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self._locations:
            return self.async_abort(reason="no_locations")
        if user_input is None and len(self._locations) == 1:
            user_input = {CONF_LOCATION_ID: self._locations[0]["id"]}
        if user_input is not None:
            self._location = next(l for l in self._locations if l["id"] == user_input[CONF_LOCATION_ID])
            try:
                self._stations = sorted(
                    await self._client.async_stations(self._location["id"]), key=lambda s: s.get("name", "")
                )
            except ScaleEVError:
                return self.async_abort(reason="cannot_connect")
            if not self._stations:
                return self.async_abort(reason="no_stations")
            return await self.async_step_station()
        options = [
            SelectOptionDict(value=l["id"], label=f"{l['name']} - {l.get('address', '')}") for l in self._locations
        ]
        schema = vol.Schema({vol.Required(CONF_LOCATION_ID): SelectSelector(SelectSelectorConfig(options=options))})
        return self.async_show_form(step_id="location", data_schema=schema)

    # -- 4. station (and connector) ---------------------------------------
    async def async_step_station(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None and len(self._stations) == 1:
            user_input = {CONF_STATION_ID: self._stations[0]["id"]}
        if user_input is not None:
            self._station = next(s for s in self._stations if s["id"] == user_input[CONF_STATION_ID])
            connectors = self._station.get("connectors") or []
            if not connectors:
                return self.async_abort(reason="no_connectors")
            if len(connectors) == 1:
                self._connector_id = connectors[0]["connectorId"]
                return await self._finish_station()
            return await self.async_step_connector()
        options = [
            SelectOptionDict(
                value=s["id"], label=f"{s['name']} - {s.get('type', '')} {s.get('maxPower', '?')} kW ({s.get('status', '?')})"
            )
            for s in self._stations
        ]
        schema = vol.Schema({vol.Required(CONF_STATION_ID): SelectSelector(SelectSelectorConfig(options=options))})
        return self.async_show_form(step_id="station", data_schema=schema)

    async def async_step_connector(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._connector_id = int(user_input[CONF_CONNECTOR_ID])
            return await self._finish_station()
        options = [
            SelectOptionDict(value=str(c["connectorId"]), label=f"#{c['connectorId']} {c.get('type', '')} {c.get('maxPower', '?')} kW")
            for c in self._station["connectors"]
        ]
        schema = vol.Schema({vol.Required(CONF_CONNECTOR_ID): SelectSelector(SelectSelectorConfig(options=options))})
        return self.async_show_form(step_id="connector", data_schema=schema)

    async def _finish_station(self) -> ConfigFlowResult:
        await self.async_set_unique_id(f"{self._driver_id}_{self._station['id']}")
        self._abort_if_unique_id_configured()
        return await self.async_step_settings()

    # -- 5. settings -------------------------------------------------------
    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        has_coordinates = self._location.get("latitude") is not None and self._location.get("longitude") is not None
        if user_input is not None:
            errors = _settings_errors(user_input, has_coordinates)
        if user_input is not None and not errors:
            return self.async_create_entry(
                title=f"{self._station['name']} ({self._location['name']})",
                data={
                    CONF_API_BASE: self._api_base,
                    CONF_TENANT_SLUG: self._slug,
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_DRIVER_ID: self._driver_id,
                    CONF_LOCATION_ID: self._location["id"],
                    CONF_LOCATION_NAME: self._location["name"],
                    CONF_LOCATION_LAT: self._location.get("latitude"),
                    CONF_LOCATION_LON: self._location.get("longitude"),
                    CONF_STATION_ID: self._station["id"],
                    CONF_STATION_NAME: self._station["name"],
                    CONF_CONNECTOR_ID: self._connector_id,
                },
                options=_options(user_input),
            )
        schema = _settings_schema(self.hass)
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="settings", data_schema=schema, errors=errors,
            description_placeholders={"location": self._location["name"]},
        )

    # -- reauth ------------------------------------------------------------
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client = ScaleEVClient(
                async_get_clientsession(self.hass), entry.data[CONF_API_BASE], entry.data[CONF_TENANT_SLUG],
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD],
            )
            try:
                await client.async_login()
            except ScaleEVAuthError:
                errors["base"] = "invalid_auth"
            except ScaleEVError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        schema = vol.Schema(
            {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
        )


class ScaleEVOptionsFlow(OptionsFlow):
    """Edit target energy, entities, notify action and payment timeout."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        data = self.config_entry.data
        has_coordinates = data.get(CONF_LOCATION_LAT) is not None and data.get(CONF_LOCATION_LON) is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _settings_errors(user_input, has_coordinates)
            if not errors:
                return self.async_create_entry(data=_options(user_input))
        current = user_input if user_input is not None else self.config_entry.options
        schema = self.add_suggested_values_to_schema(_settings_schema(self.hass), current)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
