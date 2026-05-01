"""Base entity helpers for InCharge."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class InChargeCoordinatorEntity(CoordinatorEntity):
    """Base entity for a charging point."""

    def __init__(self, coordinator, point_metadata: dict) -> None:
        super().__init__(coordinator)
        self.point_metadata = point_metadata
        self._uuid = str(point_metadata["uuid"]).lower()

    @property
    def point_data(self) -> dict:
        return (
            (self.coordinator.data.get("charging_points") or {}).get(
                self._uuid, self.point_metadata
            )
        )

    @staticmethod
    def _looks_like_address_name(name: str | None, address: str | None) -> bool:
        if not name:
            return False
        compact = name.strip()
        if not compact:
            return False
        if address and compact.casefold() == address.strip().casefold():
            return True
        if compact.startswith(("NL,", "DE,", "BE,", "FR,")):
            return True
        if "," in compact and re.search(r"\d", compact):
            return True
        return False

    @property
    def device_info(self) -> DeviceInfo:
        data = self.point_data
        station_uuid = data.get("stationUuid") or self.point_metadata.get("stationUuid")
        station_name = data.get("stationName") or self.point_metadata.get("stationName")
        address = ((data.get("location") or {}).get("address")) or (
            (self.point_metadata.get("location") or {}).get("address")
        )
        display_name = data.get("displayName") or self.point_metadata.get("displayName")
        visual_id = data.get("visualId") or self.point_metadata.get("visualId")
        if self._looks_like_address_name(station_name, address):
            preferred_name = display_name or visual_id or station_name or "InCharge Station"
        else:
            preferred_name = station_name or display_name or visual_id or "InCharge Station"
        return DeviceInfo(
            identifiers={(DOMAIN, str(station_uuid).lower())},
            name=preferred_name,
            manufacturer="Vattenfall InCharge",
            model="Public Charging Station",
        )


class InChargeMyChargeCoordinatorEntity(CoordinatorEntity):
    """Base entity for the My InCharge account device."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)

    @property
    def mycharge_data(self) -> dict:
        return self.coordinator.data.get("mycharge") or {}

    @property
    def mycharge_profile(self) -> dict:
        return self.mycharge_data.get("profile") or {}

    @property
    def _account_key(self) -> str:
        account_number = self.mycharge_profile.get("account_number")
        sub = self.mycharge_profile.get("sub")
        account_id = account_number or sub or "unknown"
        return f"mycharge_account:{str(account_id).lower()}"

    @property
    def device_info(self) -> DeviceInfo:
        profile = self.mycharge_profile
        account_number = profile.get("account_number")
        email = profile.get("email")
        name = "My InCharge Account"
        if account_number:
            name = f"My InCharge {account_number}"
        elif email:
            name = f"My InCharge {email}"
        return DeviceInfo(
            identifiers={(DOMAIN, self._account_key)},
            name=name,
            manufacturer="Vattenfall InCharge",
            model="My InCharge Account",
        )
