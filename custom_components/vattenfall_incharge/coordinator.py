"""Data update coordinator for InCharge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import InChargeClient
from .const import (
    CONF_CHARGING_POINTS,
    CONF_MYCHARGE,
    DATA_SKIP_RELOAD_ONCE,
    DOMAIN,
    MYCHARGE_POLL_MINUTES,
    NOTIFICATION_MYCHARGE_AUTH,
    PUBLIC_STATION_POLL_MINUTES,
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
        self._last_mycharge_update: datetime | None = None
        self._force_mycharge_update = False
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=PUBLIC_STATION_POLL_MINUTES),
        )

    def force_mycharge_update(self) -> None:
        """Force the next refresh cycle to update My InCharge data."""
        self._force_mycharge_update = True

    def _update_mycharge_auth_notification(self, mycharge_data: dict | None) -> None:
        """Show or clear a Home Assistant notification for My InCharge reauth."""
        if not mycharge_data:
            return
        if mycharge_data.get("reauth_required"):
            _LOGGER.warning(
                "My InCharge re-authentication required — token refresh failed or token expired. "
                "Go to integration settings → 'Add or update My InCharge account' to reconnect."
            )
            async_create(
                self.hass,
                (
                    "The Vattenfall InCharge integration needs you to reconnect "
                    "your My InCharge account. Open the integration settings and "
                    "choose 'Add or update My InCharge account'."
                ),
                title="Vattenfall InCharge authentication required",
                notification_id=NOTIFICATION_MYCHARGE_AUTH,
            )
            return
        if mycharge_data.get("connected"):
            async_dismiss(self.hass, NOTIFICATION_MYCHARGE_AUTH)

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
            now = datetime.now(UTC)
            previous_data = (self.data or {}).get("mycharge") if self.data else None
            tokens = self.client.mycharge_auth.get("tokens", {})
            seconds_left = self.client.mycharge_token_seconds_left(tokens)
            tokens_need_refresh = self.client.mycharge_tokens_need_refresh(tokens)

            reasons: list[str] = []
            if self._force_mycharge_update:
                reasons.append("forced")
            if previous_data is None:
                reasons.append("no previous data")
            if self._last_mycharge_update is None:
                reasons.append("never updated")
            elif now - self._last_mycharge_update >= timedelta(minutes=MYCHARGE_POLL_MINUTES):
                elapsed = int((now - self._last_mycharge_update).total_seconds() / 60)
                reasons.append(f"poll interval exceeded ({elapsed}m >= {MYCHARGE_POLL_MINUTES}m)")
            if tokens_need_refresh:
                reasons.append(
                    f"token expires in {seconds_left}s (refresh window: 1800s)"
                    if seconds_left is not None
                    else "token expiry unknown"
                )

            mycharge_due = bool(reasons)

            if not mycharge_due:
                mycharge_data = previous_data
                self._update_mycharge_auth_notification(mycharge_data)
                return {
                    "charging_points": charging_point_data,
                    "mycharge": mycharge_data,
                }

            _LOGGER.debug(
                "My InCharge update triggered: %s (token expires in %ss)",
                ", ".join(reasons),
                seconds_left,
            )

            try:
                mycharge_data = await self.client.async_get_mycharge_overview()
                self._last_mycharge_update = now
                self._force_mycharge_update = False
                self._update_mycharge_auth_notification(mycharge_data)
                if self.client.mycharge_auth != self.entry.options.get(CONF_MYCHARGE):
                    old_rt = (self.entry.options.get(CONF_MYCHARGE) or {}).get(
                        "tokens", {}
                    ).get("refresh_token")
                    new_rt = self.client.mycharge_auth.get("tokens", {}).get("refresh_token")
                    _LOGGER.info(
                        "Persisting updated My InCharge tokens to config entry "
                        "(refresh_token %s)",
                        "rotated to new value" if old_rt != new_rt else "unchanged",
                    )
                    entry_data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
                    if entry_data is not None:
                        entry_data[DATA_SKIP_RELOAD_ONCE] = True
                    self.hass.config_entries.async_update_entry(
                        self.entry,
                        options={
                            **self.entry.options,
                            CONF_MYCHARGE: self.client.mycharge_auth,
                        },
                    )
            except Exception as err:  # pragma: no cover - keep station polling alive
                _LOGGER.exception("Failed to update My InCharge account data: %s", err)
                mycharge_data = {
                    "connected": False,
                    "status": "Error",
                    "profile": (self.entry.options.get(CONF_MYCHARGE) or {}).get(
                        "profile", {}
                    ),
                    "error": "Failed to update My InCharge account data",
                }
                self._last_mycharge_update = now
                self._force_mycharge_update = False
                self._update_mycharge_auth_notification(mycharge_data)

        return {
            "charging_points": charging_point_data,
            "mycharge": mycharge_data,
        }
