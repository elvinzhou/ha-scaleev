"""Session and connector sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy

from .const import STATUSES
from .entity import ScaleEVCoordinatorEntity, ScaleEVEntity


async def async_setup_entry(hass, entry, async_add_entities):
    m = entry.runtime_data
    async_add_entities(
        [ConnectorStatus(m, "connector_status"), SessionStatus(m, "session_status"),
         SessionEnergy(m, "session_energy"), SessionCost(m, "session_cost"),
         StartStatus(m, "start_status")]
    )


class _Base(ScaleEVCoordinatorEntity, SensorEntity):
    @property
    def _session(self):
        return (self.coordinator.data or {}).get("session")


class ConnectorStatus(_Base):
    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("connector_status")


class SessionStatus(_Base):
    @property
    def native_value(self):
        return self._session.get("status") if self._session else "idle"


class SessionEnergy(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self):
        return self._session.get("energyDeliveredKwh") if self._session else None


class SessionCost(_Base):
    _attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_unit_of_measurement(self):
        return (self._session or {}).get("currency") or "USD"

    @property
    def native_value(self):
        return self._session.get("totalCost") if self._session else None


class StartStatus(ScaleEVEntity, SensorEntity):
    """Why the last start attempt did, or did not, start charging."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.async_add_status_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.manager.status

    @property
    def extra_state_attributes(self):
        return {"detail": self.manager.status_detail}
