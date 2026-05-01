"""Sensor platform for InCharge charging points."""

from __future__ import annotations

import hashlib
import json

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_CHARGING_POINTS, CONF_MYCHARGE, DATA_COORDINATOR, DOMAIN
from .entity import InChargeCoordinatorEntity, InChargeMyChargeCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    charging_points = entry.options.get(CONF_CHARGING_POINTS, [])
    entities = [
        InChargeStatusSensor(coordinator, point) for point in charging_points
    ]
    if entry.options.get(CONF_MYCHARGE):
        mycharge_data = coordinator.data.get("mycharge") or {}
        cards = ((mycharge_data.get("cards") or {}).get("cards")) or []
        entities.extend(
            [
                InChargeMyChargeStatusSensor(coordinator),
                InChargeMyChargeAccountSensor(coordinator),
                InChargeMyChargeEnergySensor(coordinator),
                InChargeMyChargeDurationSensor(coordinator),
                InChargeMyChargeCardsSensor(coordinator),
            ]
        )
        entities.extend(
            InChargeMyChargeCardSensor(coordinator, card, index)
            for index, card in enumerate(cards)
            if isinstance(card, dict)
        )
    async_add_entities(entities)


class InChargeStatusSensor(InChargeCoordinatorEntity, SensorEntity):
    """Main status sensor for an InCharge charging point."""

    icon = "mdi:ev-station"

    @staticmethod
    def _friendly_status(raw_status: str | None) -> str | None:
        if not raw_status:
            return None
        return raw_status.replace("_", " ").title()

    @property
    def unique_id(self) -> str:
        return f"{self._uuid}_status"

    @property
    def name(self) -> str:
        return f"{self.point_data.get('displayName', self.point_metadata.get('displayName', self._uuid))} status"

    @property
    def native_value(self) -> str | None:
        return self._friendly_status(self.point_data.get("status"))

    @property
    def extra_state_attributes(self) -> dict:
        data = self.point_data
        location = data.get("location") or {}
        connectors = data.get("connectors") or []
        first_connector = connectors[0] if connectors else {}
        price_components = data.get("priceComponents") or {}
        first_component = (price_components.get("components") or [{}])[0]
        first_element = (first_component.get("elements") or [{}])[0]
        latitude = (location.get("coordinates") or {}).get("latitude")
        longitude = (location.get("coordinates") or {}).get("longitude")
        maps_url = None
        if latitude is not None and longitude is not None:
            maps_url = f"https://www.google.com/maps?q={latitude},{longitude}"

        return {
            "station_name": data.get("stationName"),
            "station_uuid": data.get("stationUuid"),
            "charging_point_uuid": data.get("uuid"),
            "charging_point_id": data.get("id"),
            "visual_id": data.get("visualId"),
            "evse_id": data.get("evseId"),
            "translated": self._friendly_status(data.get("status")),
            "raw": data.get("status"),
            "address": location.get("address"),
            "city": location.get("city"),
            "postalcode": location.get("postalcode"),
            "country": location.get("country"),
            "latitude": latitude,
            "longitude": longitude,
            "google_maps_url": maps_url,
            "operator": data.get("operator"),
            "category": data.get("category"),
            "max_power_w": first_connector.get("power"),
            "connector_type": first_connector.get("pluginType")
            or first_connector.get("type"),
            "connector_id": first_connector.get("connectorId"),
            "price_currency": price_components.get("currency"),
            "price_type": first_component.get("type"),
            "price_per_kwh": first_element.get("price"),
            "price_time_from": first_element.get("timeFrom"),
            "price_time_to": first_element.get("timeTo"),
            "status_updated": data.get("statusUpdateTimestamp"),
            "opening_hours": data.get("openingHours"),
            "remote_payment_methods": data.get(
                "supportedRemoteChargingPaymentMethods"
            ),
        }


class InChargeMyChargeStatusSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Connection status sensor for the MyCharge account."""

    icon = "mdi:account-circle"

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_status"

    @property
    def name(self) -> str:
        return "MyCharge status"

    @property
    def native_value(self) -> str | None:
        return self.mycharge_data.get("status")

    @property
    def extra_state_attributes(self) -> dict:
        profile = self.mycharge_profile
        hierarchy = self.mycharge_data.get("account_hierarchy")
        account_count = None
        if isinstance(hierarchy, list):
            account_count = len(hierarchy)
        elif isinstance(hierarchy, dict):
            accounts = hierarchy.get("accounts")
            if isinstance(accounts, list):
                account_count = len(accounts)
        return {
            "connected": self.mycharge_data.get("connected"),
            "email": profile.get("email"),
            "username": profile.get("username"),
            "given_name": profile.get("given_name"),
            "customer_id": profile.get("customer_id"),
            "tenant_domain": profile.get("tenant_domain"),
            "account_number": profile.get("account_number"),
            "token_expires_at": profile.get("expires_at"),
            "token_seconds_left": self.mycharge_data.get("token_seconds_left"),
            "last_checked": self.mycharge_data.get("last_checked"),
            "account_hierarchy_count": account_count,
            "charging_history_error": self.mycharge_data.get("charging_history_error"),
            "cards_error": self.mycharge_data.get("cards_error"),
            "error": self.mycharge_data.get("error"),
        }


class InChargeMyChargeAccountSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Primary account identifier sensor for MyCharge."""

    icon = "mdi:card-account-details-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_account"

    @property
    def name(self) -> str:
        return "MyCharge account"

    @property
    def native_value(self) -> str | None:
        profile = self.mycharge_profile
        return profile.get("account_number") or profile.get("email") or profile.get("sub")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "roles": self.mycharge_profile.get("roles"),
            "scope": self.mycharge_profile.get("scope"),
            "account_hierarchy": self.mycharge_data.get("account_hierarchy"),
        }


class InChargeMyChargeChargingHistorySensor(
    InChargeMyChargeCoordinatorEntity, SensorEntity
):
    """Base sensor for MyCharge charging-history totals."""

    @property
    def _history(self) -> dict:
        return self.mycharge_data.get("charging_history") or {}

    @property
    def available(self) -> bool:
        return bool(self._history)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "period_days": self._history.get("period_days"),
            "period_start": self._history.get("period_start"),
            "period_end": self._history.get("period_end"),
            "account_number": self._history.get("account_number"),
            "source_format": self._history.get("source_format"),
            "source_warnings": self._history.get("source_warnings"),
            "source_row_count": self._history.get("source_row_count"),
            "source_fieldnames": self._history.get("source_fieldnames"),
            "source_item_count": self._history.get("source_item_count"),
            "energy_field_matches": self._history.get("energy_field_matches"),
            "duration_field_matches": self._history.get("duration_field_matches"),
            "source_top_level_keys": self._history.get("source_top_level_keys"),
            "error": self.mycharge_data.get("charging_history_error"),
        }


class InChargeMyChargeEnergySensor(InChargeMyChargeChargingHistorySensor):
    """Total MyCharge charging energy for the recent period."""

    icon = "mdi:lightning-bolt"
    device_class = SensorDeviceClass.ENERGY
    native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_charging_energy_30d"

    @property
    def name(self) -> str:
        return "MyCharge charging energy last 30 days"

    @property
    def native_value(self) -> float | None:
        return self._history.get("energy_kwh")


class InChargeMyChargeDurationSensor(InChargeMyChargeChargingHistorySensor):
    """Total MyCharge charging duration for the recent period."""

    icon = "mdi:timer-outline"
    device_class = SensorDeviceClass.DURATION
    native_unit_of_measurement = UnitOfTime.HOURS
    state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_charging_duration_30d"

    @property
    def name(self) -> str:
        return "MyCharge charging time last 30 days"

    @property
    def native_value(self) -> float | None:
        return self._history.get("duration_hours")


class InChargeMyChargeCardsSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Number of charging cards in the MyCharge account."""

    icon = "mdi:card-multiple-outline"
    state_class = SensorStateClass.MEASUREMENT

    @property
    def _cards_data(self) -> dict:
        return self.mycharge_data.get("cards") or {}

    @property
    def available(self) -> bool:
        return bool(self._cards_data)

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_charging_cards"

    @property
    def name(self) -> str:
        return "MyCharge charging cards"

    @property
    def native_value(self) -> int | None:
        return self._cards_data.get("card_count")

    @property
    def extra_state_attributes(self) -> dict:
        pending_assignments = self._cards_data.get("pending_assignments") or {}
        cards = self._cards_data.get("cards") or []
        attributes = {
            "account_number": self._cards_data.get("account_number"),
            "pending_assignments": pending_assignments.get("pendingAssignments"),
            "ongoing_assignments": pending_assignments.get("ongoingAssignments"),
            "error": self.mycharge_data.get("cards_error"),
        }
        if cards:
            attributes["cards"] = cards
        return attributes


class InChargeMyChargeCardSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Sensor for one charging card returned by MyCharge."""

    icon = "mdi:card-account-details-outline"

    def __init__(self, coordinator, card: dict, index: int) -> None:
        super().__init__(coordinator)
        self._initial_card = card
        self._index = index
        self._card_key = self._build_card_key(card, index)

    @staticmethod
    def _card_value(card: dict, *keys: str):
        lower_lookup = {str(key).casefold(): value for key, value in card.items()}
        for key in keys:
            value = card.get(key)
            if value:
                return value
            value = lower_lookup.get(key.casefold())
            if value:
                return value
        return None

    @staticmethod
    def _build_card_key(card: dict, index: int) -> str:
        value = InChargeMyChargeCardSensor._card_value(
            card,
            "id",
            "tokenId",
            "uid",
            "UID",
            "tokenUid",
            "cardId",
            "cardNumber",
            "Number",
            "visualNumber",
            "printedNumber",
        )
        if value:
            return str(value).casefold().replace(" ", "_")
        raw = json.dumps(card, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12] or str(index)

    @staticmethod
    def _display_card(card: dict, fallback: str) -> str:
        value = InChargeMyChargeCardSensor._card_value(
            card,
            "customName",
            "name",
            "Name",
            "displayName",
            "cardNumber",
            "Number",
            "visualNumber",
            "printedNumber",
            "tokenId",
            "uid",
            "UID",
            "id",
        )
        return str(value) if value else fallback

    @property
    def _cards(self) -> list:
        return ((self.mycharge_data.get("cards") or {}).get("cards")) or []

    @property
    def _card(self) -> dict:
        if self._index < len(self._cards) and isinstance(self._cards[self._index], dict):
            return self._cards[self._index]
        return self._initial_card

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_charging_card_{self._card_key}"

    @property
    def name(self) -> str:
        return f"MyCharge card {self._display_card(self._card, str(self._index + 1))}"

    @property
    def native_value(self) -> str | None:
        card = self._card
        value = self._card_value(card, "status", "state", "State", "tokenStatus", "cardStatus")
        if value:
            return str(value).replace("_", " ").title()
        return "Available"

    @property
    def extra_state_attributes(self) -> dict:
        card = self._card
        return {
            "uid": self._card_value(card, "uid", "UID", "tokenUid"),
            "number": self._card_value(card, "number", "Number", "cardNumber"),
            "card_name": self._card_value(card, "name", "Name", "customName"),
            "activation": self._card_value(card, "activation", "Activation"),
            "state": self._card_value(card, "state", "State"),
            "valid_from": self._card_value(card, "validFrom", "Valid From"),
            "valid_to": self._card_value(card, "validTo", "Valid To"),
            "card": card,
            "source_index": self._index,
        }
