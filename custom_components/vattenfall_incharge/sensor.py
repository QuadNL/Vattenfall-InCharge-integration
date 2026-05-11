"""Sensor platform for InCharge charging points."""

from __future__ import annotations

import hashlib
import json

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

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
                InChargeMyChargeThisYearEnergySensor(coordinator),
                InChargeMyChargeDurationSensor(coordinator),
                InChargeMyChargeAverageConsumption7dSensor(coordinator),
                InChargeMyChargeCurrentMonthCostSensor(coordinator),
                InChargeMyChargeLastMonthCostSensor(coordinator),
                InChargeMyChargeThisYearCostSensor(coordinator),
                InChargeMyChargeValidatedSessionsSensor(coordinator),
                InChargeMyChargeCancelledSessionsSensor(coordinator),
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

    _attr_has_entity_name = False
    icon = "mdi:ev-station"

    def __init__(self, coordinator, point_metadata: dict) -> None:
        super().__init__(coordinator, point_metadata)
        self.entity_id = f"sensor.{self._object_id}"

    @property
    def _station_name(self) -> str | None:
        return self.point_data.get("stationName") or self.point_metadata.get("stationName")

    @property
    def _point_name(self) -> str:
        data = self.point_data
        return (
            data.get("visualId")
            or data.get("displayName")
            or self.point_metadata.get("visualId")
            or self.point_metadata.get("displayName")
            or self._uuid
        )

    @property
    def _object_id(self) -> str:
        if self._station_name:
            return slugify(f"incharge_station_{self._station_name}_{self._point_name}_status")
        return slugify(f"incharge_station_{self._point_name}_status")

    @staticmethod
    def _friendly_status(raw_status: str | None) -> str | None:
        if not raw_status:
            return None
        return raw_status.replace("_", " ").title()

    @staticmethod
    def _normalized_price_components(price_components: dict) -> list[dict]:
        normalized = []
        for component in price_components.get("components") or []:
            if not isinstance(component, dict):
                continue
            normalized_component = {
                key: value for key, value in component.items() if key != "elements"
            }
            normalized_component["elements"] = [
                dict(element)
                for element in component.get("elements") or []
                if isinstance(element, dict)
            ]
            normalized.append(normalized_component)
        return normalized

    @staticmethod
    def _first_price_element(
        price_components: list[dict], component_type: str
    ) -> dict | None:
        for component in price_components:
            if str(component.get("type", "")).upper() != component_type:
                continue
            for element in component.get("elements") or []:
                if element.get("price") is not None:
                    return element
        return None

    def _price_attributes(self, price_components: dict) -> dict:
        normalized_components = self._normalized_price_components(price_components)
        component_types = [
            component.get("type")
            for component in normalized_components
            if component.get("type")
        ]
        unique_types = list(dict.fromkeys(component_types))

        kwh_element = self._first_price_element(normalized_components, "KWH")
        fixed_element = self._first_price_element(normalized_components, "FIXED")
        time_element = self._first_price_element(normalized_components, "TIME")

        price_type = None
        if len(unique_types) == 1:
            price_type = unique_types[0]
        elif len(unique_types) > 1:
            price_type = "MIXED"

        return {
            "price_currency": price_components.get("currency"),
            "price_type": price_type,
            "price_component_types": unique_types,
            "price_components": normalized_components,
            "price_per_kwh": (kwh_element or {}).get("price"),
            "price_start_fee": (fixed_element or {}).get("price"),
            "price_time_fee_per_minute": (time_element or {}).get("price"),
            "price_time_from": (time_element or kwh_element or {}).get("timeFrom"),
            "price_time_to": (time_element or kwh_element or {}).get("timeTo"),
        }

    @property
    def unique_id(self) -> str:
        return f"{self._uuid}_status"

    @property
    def suggested_object_id(self) -> str:
        return self._object_id

    @property
    def name(self) -> str:
        return f"{self._point_name} status"

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
        price_attributes = self._price_attributes(price_components)
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
            **price_attributes,
            "status_updated": data.get("statusUpdateTimestamp"),
            "opening_hours": data.get("openingHours"),
            "remote_payment_methods": data.get(
                "supportedRemoteChargingPaymentMethods"
            ),
        }


class InChargeMyChargeStatusSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Connection status sensor for the My InCharge account."""

    icon = "mdi:account-circle"

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_status"

    @property
    def name(self) -> str:
        return "My InCharge status"

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
            "account_number": profile.get("account_number"),
            "last_checked": self.mycharge_data.get("last_checked"),
            "account_hierarchy_count": account_count,
            "charging_history_error": self.mycharge_data.get("charging_history_error"),
            "cards_error": self.mycharge_data.get("cards_error"),
            "dashboard_error": self.mycharge_data.get("dashboard_error"),
            "token_refresh_error": self.mycharge_data.get("token_refresh_error"),
            "last_token_refresh": self.mycharge_data.get("last_token_refresh"),
            "reauth_required": self.mycharge_data.get("reauth_required"),
            "error": self.mycharge_data.get("error"),
        }


class InChargeMyChargeAccountSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Primary account identifier sensor for My InCharge."""

    icon = "mdi:card-account-details-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_account"

    @property
    def name(self) -> str:
        return "My InCharge account"

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
    """Base sensor for My InCharge charging-history totals."""

    @property
    def _history(self) -> dict:
        return self.mycharge_data.get("charging_history") or {}

    @property
    def available(self) -> bool:
        return bool(self._history)

    @property
    def extra_state_attributes(self) -> dict:
        attributes = {
            "period": self._history.get("period"),
            "period_start": self._history.get("period_start"),
            "period_end": self._history.get("period_end"),
            "account_number": self._history.get("account_number"),
            "source": "validated_sessions",
            "validated_session_count": (self._history.get("validated") or {}).get(
                "session_count"
            ),
            "error": self.mycharge_data.get("charging_history_error"),
        }
        if self._history.get("period_days") is not None:
            attributes["period_days"] = self._history.get("period_days")
        return attributes


class InChargeMyChargeEnergySensor(InChargeMyChargeChargingHistorySensor):
    """Total charging energy for the current calendar month."""

    icon = "mdi:lightning-bolt"
    native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _cost_key = "current_month"
    _sensor_suffix = "charging_energy_this_month"
    _sensor_name = "Charging energy this month"

    @property
    def device_class(self) -> None:
        return None

    @property
    def state_class(self) -> None:
        return None

    @property
    def _costs(self) -> dict:
        return ((self.mycharge_data.get("dashboard") or {}).get("costs") or {}).get(
            self._cost_key
        ) or {}

    @property
    def available(self) -> bool:
        return bool(self._costs) or super().available

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_{self._sensor_suffix}"

    @property
    def name(self) -> str:
        return self._sensor_name

    @property
    def native_value(self) -> float | None:
        if self._costs.get("consumption_kwh") is not None:
            return self._costs.get("consumption_kwh")
        return self._history.get("energy_kwh")

    @property
    def extra_state_attributes(self) -> dict:
        attributes = super().extra_state_attributes
        dashboard_errors = self.mycharge_data.get("dashboard_errors")
        if self._costs.get("consumption_kwh") is not None:
            attributes.update(
                {
                    "source": "dashboard_daily_summary",
                    "period": self._costs.get("period"),
                    "period_start": self._costs.get("period_start"),
                    "period_end": self._costs.get("period_end"),
                    "account_number": self._costs.get("account_number"),
                    "validated_session_count": self._costs.get("number_of_sessions"),
                    "total_cost_incl_vat": self._costs.get("cost"),
                    "duration_hours": self._costs.get("duration_hours"),
                    "duration_seconds": self._costs.get("duration_seconds"),
                    "active_days": self._costs.get("active_days"),
                    "latest_active_days": self._costs.get("latest_active_days"),
                    "error": (dashboard_errors or {}).get(f"costs_{self._cost_key}")
                    or self.mycharge_data.get("dashboard_error"),
                }
            )
        return attributes


class InChargeMyChargeThisYearEnergySensor(InChargeMyChargeEnergySensor):
    """Total charging energy for the current calendar year."""

    _cost_key = "this_year"
    _sensor_suffix = "charging_energy_this_year"
    _sensor_name = "Charging energy this year"


class InChargeMyChargeDurationSensor(InChargeMyChargeChargingHistorySensor):
    """Total charging duration for the current calendar month."""

    icon = "mdi:timer-outline"
    device_class = SensorDeviceClass.DURATION
    native_unit_of_measurement = UnitOfTime.HOURS
    state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_charging_duration_this_month"

    @property
    def name(self) -> str:
        return "Charging time this month"

    @property
    def native_value(self) -> float | None:
        if self._history.get("duration_hours") is not None:
            return self._history.get("duration_hours")
        current_month = (
            ((self.mycharge_data.get("dashboard") or {}).get("costs") or {}).get(
                "current_month"
            )
            or {}
        )
        if current_month.get("duration_hours") is not None:
            return current_month.get("duration_hours")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        attributes = super().extra_state_attributes
        if self._history.get("duration_hours") is not None:
            attributes["source"] = "validated_sessions"
        current_month = (
            ((self.mycharge_data.get("dashboard") or {}).get("costs") or {}).get(
                "current_month"
            )
            or {}
        )
        if current_month.get("duration_hours") is not None and self._history.get(
            "duration_hours"
        ) is None:
            dashboard_errors = self.mycharge_data.get("dashboard_errors")
            attributes.update(
                {
                    "source": "dashboard_daily_summary",
                    "period": current_month.get("period"),
                    "period_start": current_month.get("period_start"),
                    "period_end": current_month.get("period_end"),
                    "account_number": current_month.get("account_number"),
                    "validated_session_count": current_month.get("number_of_sessions"),
                    "total_cost_incl_vat": current_month.get("cost"),
                    "total_kwh": current_month.get("consumption_kwh"),
                    "duration_seconds": current_month.get("duration_seconds"),
                    "active_days": current_month.get("active_days"),
                    "latest_active_days": current_month.get("latest_active_days"),
                    "error": (dashboard_errors or {}).get("costs_current_month")
                    or self.mycharge_data.get("dashboard_error"),
                }
            )
        return attributes


class InChargeMyChargeAverageConsumptionSensor(
    InChargeMyChargeCoordinatorEntity, SensorEntity
):
    """Average kWh per My InCharge session for a fixed period."""

    icon = "mdi:chart-line"
    native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    state_class = SensorStateClass.MEASUREMENT
    _period_days = 0

    @property
    def device_class(self) -> None:
        return None

    @property
    def _dashboard(self) -> dict:
        return self.mycharge_data.get("dashboard") or {}

    @property
    def available(self) -> bool:
        return bool(self._dashboard)

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_average_consumption_per_session_{self._period_days}d"

    @property
    def name(self) -> str:
        return f"Average consumption per session last {self._period_days} days"

    @property
    def native_value(self) -> float | None:
        values = self._dashboard.get("average_consumption_per_session") or {}
        return values.get(str(self._period_days))

    @property
    def extra_state_attributes(self) -> dict:
        dashboard_errors = self.mycharge_data.get("dashboard_errors")
        return {
            "account_number": self._dashboard.get("account_number"),
            "period_days": self._period_days,
            "source": "dashboard_widget",
            "error": (dashboard_errors or {}).get(
                f"average_consumption_per_session_{self._period_days}d"
            )
            or self.mycharge_data.get("dashboard_error"),
        }


class InChargeMyChargeAverageConsumption7dSensor(
    InChargeMyChargeAverageConsumptionSensor
):
    """Average kWh per My InCharge session for the last seven days."""

    _period_days = 7


class InChargeMyChargeCostSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """My InCharge charging costs for a fixed period."""

    icon = "mdi:cash"
    device_class = SensorDeviceClass.MONETARY
    native_unit_of_measurement = "EUR"
    _cost_key = ""
    _sensor_suffix = ""
    _sensor_name = ""

    @property
    def state_class(self) -> SensorStateClass:
        return SensorStateClass.TOTAL

    @property
    def _costs(self) -> dict:
        return ((self.mycharge_data.get("dashboard") or {}).get("costs") or {}).get(
            self._cost_key
        ) or {}

    @property
    def available(self) -> bool:
        return bool(self._costs)

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_{self._sensor_suffix}"

    @property
    def name(self) -> str:
        return self._sensor_name

    @property
    def native_value(self) -> float | None:
        return self._costs.get("cost")

    @property
    def extra_state_attributes(self) -> dict:
        dashboard_errors = self.mycharge_data.get("dashboard_errors")
        return {
            "account_number": self._costs.get("account_number"),
            "period": self._costs.get("period"),
            "period_start": self._costs.get("period_start"),
            "period_end": self._costs.get("period_end"),
            "currency": self._costs.get("currency"),
            "consumption_kwh": self._costs.get("consumption_kwh"),
            "number_of_sessions": self._costs.get("number_of_sessions"),
            "active_days": self._costs.get("active_days"),
            "source_day_count": self._costs.get("source_day_count"),
            "latest_active_days": self._costs.get("latest_active_days"),
            "error": (dashboard_errors or {}).get(f"costs_{self._cost_key}")
            or self.mycharge_data.get("dashboard_error"),
        }


class InChargeMyChargeCurrentMonthCostSensor(InChargeMyChargeCostSensor):
    """My InCharge charging costs for the current calendar month."""

    _cost_key = "current_month"
    _sensor_suffix = "charging_costs_this_month"
    _sensor_name = "Charging costs this month"


class InChargeMyChargeLastMonthCostSensor(InChargeMyChargeCostSensor):
    """My InCharge charging costs for the previous calendar month."""

    _cost_key = "last_month"
    _sensor_suffix = "charging_costs_last_month"
    _sensor_name = "Charging costs last month"


class InChargeMyChargeThisYearCostSensor(InChargeMyChargeCostSensor):
    """My InCharge charging costs for the current calendar year."""

    _cost_key = "this_year"
    _sensor_suffix = "charging_costs_this_year"
    _sensor_name = "Charging costs this year"


class InChargeMyChargeSessionsSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Base sensor for a My InCharge charging-history status bucket."""

    icon = "mdi:history"
    state_class = SensorStateClass.MEASUREMENT
    _history_key = ""
    _sensor_suffix = ""
    _sensor_name = ""

    @property
    def _history(self) -> dict:
        return (self.mycharge_data.get("charging_history") or {}).get(
            self._history_key
        ) or {}

    @property
    def available(self) -> bool:
        return bool(self._history)

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_{self._sensor_suffix}"

    @property
    def name(self) -> str:
        return self._sensor_name

    @property
    def native_value(self) -> int | None:
        return self._history.get("session_count")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "account_number": self._history.get("account_number"),
            "period_days": self._history.get("period_days"),
            "period_start": self._history.get("period_start"),
            "period_end": self._history.get("period_end"),
            "total_kwh": self._history.get("total_kwh"),
            "total_duration_hours": self._history.get("total_duration_hours"),
            "total_duration_seconds": self._history.get("total_duration_seconds"),
            "total_cost_incl_vat": self._history.get("total_cost_incl_vat"),
            "latest_sessions": self._history.get("latest_sessions"),
            "error": self.mycharge_data.get("charging_history_error"),
        }


class InChargeMyChargeValidatedSessionsSensor(InChargeMyChargeSessionsSensor):
    """Validated charging sessions for the current calendar month."""

    _history_key = "validated"
    _sensor_suffix = "validated_sessions_this_month"
    _sensor_name = "Validated sessions this month"


class InChargeMyChargeCancelledSessionsSensor(InChargeMyChargeSessionsSensor):
    """Cancelled charging sessions for the current calendar month."""

    _history_key = "cancelled"
    _sensor_suffix = "cancelled_sessions_this_month"
    _sensor_name = "Cancelled sessions this month"


class InChargeMyChargeCardsSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Number of charging cards in the My InCharge account."""

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
        return "Charging cards"

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
            "usage_period_days": self._cards_data.get("usage_period_days"),
            "usage_summary": self._cards_data.get("usage_summary"),
            "usage_by_card": self._cards_data.get("usage_by_card"),
            "usage_error": self._cards_data.get("usage_error"),
            "error": self.mycharge_data.get("cards_error"),
        }
        if cards:
            attributes["cards"] = cards
        return attributes


class InChargeMyChargeCardSensor(InChargeMyChargeCoordinatorEntity, SensorEntity):
    """Sensor for one charging card returned by My InCharge."""

    icon = "mdi:card-account-details-outline"

    def __init__(self, coordinator, card: dict, index: int) -> None:
        super().__init__(coordinator)
        self._initial_card = card
        self._index = index
        self._card_key = self._build_card_key(card, index)

    @staticmethod
    def _card_value(card: dict, *keys: str):
        for key in keys:
            value = InChargeMyChargeCardSensor._nested_card_value(card, key)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _nested_card_value(card: dict, path: str):
        value: object = card
        for key in path.split("."):
            if not isinstance(value, dict):
                return None
            direct = value.get(key)
            if direct is None:
                lower_lookup = {
                    str(child_key).casefold(): child_value
                    for child_key, child_value in value.items()
                }
                direct = lower_lookup.get(key.casefold())
            value = direct
        return value

    @staticmethod
    def _format_card_status(value) -> str | None:
        if value in (None, ""):
            return None
        return str(value).replace("_", " ").title()

    @staticmethod
    def _build_card_key(card: dict, index: int) -> str:
        value = InChargeMyChargeCardSensor._card_value(
            card,
            "id",
            "tokenId",
            "details.uid",
            "details.rfid",
            "UID",
            "tokenUid",
            "cardId",
            "details.number",
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
            "details.name",
            "name",
            "Name",
            "displayName",
            "details.number",
            "cardNumber",
            "Number",
            "visualNumber",
            "printedNumber",
            "tokenId",
            "details.uid",
            "UID",
            "id",
        )
        return str(value) if value else fallback

    @staticmethod
    def _card_label(card: dict, fallback: str) -> str:
        assigned_name = InChargeMyChargeCardSensor._card_value(
            card,
            "customName",
            "details.name",
            "name",
            "Name",
            "displayName",
        )
        if assigned_name not in (None, ""):
            return str(assigned_name)
        card_number = InChargeMyChargeCardSensor._card_value(
            card,
            "details.number",
            "cardNumber",
            "Number",
        )
        if card_number not in (None, ""):
            return f"Charging card {card_number}"
        return f"Charging card {fallback}"

    @property
    def _cards(self) -> list:
        return ((self.mycharge_data.get("cards") or {}).get("cards")) or []

    @property
    def _card(self) -> dict:
        if self._index < len(self._cards) and isinstance(
            self._cards[self._index], dict
        ):
            return self._cards[self._index]
        return self._initial_card

    @property
    def unique_id(self) -> str:
        return f"{self._account_key}_charging_card_{self._card_key}"

    @property
    def name(self) -> str:
        return self._card_label(self._card, str(self._index + 1))

    @property
    def native_value(self) -> str | None:
        card = self._card
        value = self._card_value(
            card,
            "status.state",
            "state",
            "State",
            "tokenStatus",
            "cardStatus",
            "status.activation",
            "activation",
            "Activation",
        )
        if value not in (None, ""):
            return self._format_card_status(value)
        return "Available"

    @property
    def extra_state_attributes(self) -> dict:
        card = self._card
        usage = self._usage_for_card(card)
        return {
            "uid": self._card_value(card, "details.uid", "uid", "UID", "tokenUid"),
            "rfid": self._card_value(card, "details.rfid", "rfid"),
            "number": self._card_value(
                card, "details.number", "number", "Number", "cardNumber"
            ),
            "card_name": self._card_value(
                card, "details.name", "name", "Name", "customName"
            ),
            "card_type": self._card_value(card, "details.type", "type"),
            "ocpi_type": self._card_value(card, "details.ocpiType", "OCPI Type"),
            "activation": self._format_card_status(
                self._card_value(card, "status.activation", "activation", "Activation")
            ),
            "state": self._format_card_status(
                self._card_value(card, "status.state", "state", "State")
            ),
            "valid_from": self._card_value(
                card, "status.validFrom", "validFrom", "Valid From"
            ),
            "valid_to": self._card_value(card, "status.validTo", "validTo", "Valid To"),
            "emsp": self._card_value(card, "ownership.emsp"),
            "origin": self._card_value(card, "ownership.origin"),
            "effective_from": self._card_value(card, "ownership.effectiveFrom"),
            "usage_period_days": (self.mycharge_data.get("cards") or {}).get(
                "usage_period_days"
            ),
            "usage_consumption_kwh": (usage or {}).get("consumptionInKWH"),
            "usage_sessions": (usage or {}).get("numberOfSessions"),
            "usage_cost": (usage or {}).get("cost"),
            "usage_currency": (usage or {}).get("currency"),
            "card": card,
            "source_index": self._index,
        }

    def _usage_for_card(self, card: dict) -> dict | None:
        card_number = self._card_value(card, "details.number", "cardNumber", "Number")
        card_name = self._card_value(card, "details.name", "customName", "Name")
        for item in (self.mycharge_data.get("cards") or {}).get("usage_by_card") or []:
            if not isinstance(item, dict):
                continue
            if card_number and item.get("cardNumber") == card_number:
                return item
            if card_name and item.get("cardCustomName") == card_name:
                return item
        return None
