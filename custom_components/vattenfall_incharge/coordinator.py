"""Data update coordinator for InCharge."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import InChargeClient
from .const import (
    CONF_CHARGING_POINTS,
    CONF_MYCHARGE,
    CONF_POLL_MINUTES,
    DEFAULT_POLL_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class InChargeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate charging-point updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: InChargeClient,
    ) -> None:
        self.entry = entry
        self.client = client
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES)
            ),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        charging_points = self.entry.options.get(CONF_CHARGING_POINTS, [])
        uuids = [item["uuid"] for item in charging_points if item.get("uuid")]
        charging_point_data: dict[str, dict[str, Any]] = {}
        if not uuids:
            details = []
        else:
            details = await self.client.async_get_charging_points(uuids)
            charging_point_data = {str(item["uuid"]).lower(): item for item in details}

        mycharge_data = None
        if self.entry.options.get(CONF_MYCHARGE):
            try:
                mycharge_data = await self.client.async_get_mycharge_overview()
                if self.client.mycharge_auth != self.entry.options.get(CONF_MYCHARGE):
                    self.hass.config_entries.async_update_entry(
                        self.entry,
                        options={
                            **self.entry.options,
                            CONF_MYCHARGE: self.client.mycharge_auth,
                        },
                    )
            except Exception:  # pragma: no cover - keep station polling alive
                _LOGGER.exception("Failed to update My InCharge account data")
                mycharge_data = {
                    "connected": False,
                    "status": "Error",
                    "profile": (self.entry.options.get(CONF_MYCHARGE) or {}).get(
                        "profile", {}
                    ),
                    "error": "Failed to update My InCharge account data",
                }

        return {
            "charging_points": charging_point_data,
            "mycharge": mycharge_data,
        }
