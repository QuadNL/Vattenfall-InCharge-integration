"""The InCharge integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import InChargeApiError, InChargeClient
from .const import (
    CONF_APK_CRC,
    CONF_APK_SHA1,
    CONF_CHARGING_POINTS,
    CONF_DEVICE_ID,
    CONF_MYCHARGE,
    CONF_MYCHARGE_PROFILE,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_SKIP_RELOAD_ONCE,
    DOMAIN,
    PLATFORMS,
    SERVICE_REFRESH_MYCHARGE_TOKENS,
    CONF_X_TOKEN,
)
from .coordinator import InChargeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH_MYCHARGE_TOKENS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
    }
)


def _mycharge_account_key(entry: ConfigEntry) -> str | None:
    mycharge = entry.options.get(CONF_MYCHARGE) or {}
    profile = mycharge.get(CONF_MYCHARGE_PROFILE) or {}
    account_number = profile.get("account_number")
    sub = profile.get("sub")
    account_id = account_number or sub
    if not account_id:
        return None
    return f"mycharge_account:{str(account_id).lower()}"


def _configured_station_ids(entry: ConfigEntry) -> set[str]:
    return {
        str(point.get("stationUuid", "")).lower()
        for point in entry.options.get(CONF_CHARGING_POINTS, [])
        if point.get("stationUuid")
    }


def _configured_entity_unique_ids(entry: ConfigEntry) -> set[str]:
    unique_ids = {
        f"{str(point['uuid']).lower()}_status"
        for point in entry.options.get(CONF_CHARGING_POINTS, [])
        if point.get("uuid")
    }
    mycharge_key = _mycharge_account_key(entry)
    if mycharge_key:
        unique_ids.update(
            {
                f"{mycharge_key}_status",
                f"{mycharge_key}_account",
                f"{mycharge_key}_charging_energy_30d",
                f"{mycharge_key}_charging_duration_30d",
                f"{mycharge_key}_average_consumption_per_session_7d",
                f"{mycharge_key}_charging_costs_current_month",
                f"{mycharge_key}_charging_costs_last_month",
                f"{mycharge_key}_charging_costs_this_year",
                f"{mycharge_key}_validated_sessions_30d",
                f"{mycharge_key}_cancelled_sessions_30d",
                f"{mycharge_key}_charging_cards",
            }
        )
    return unique_ids


def _configured_device_ids(entry: ConfigEntry) -> set[str]:
    valid = _configured_station_ids(entry)
    mycharge_key = _mycharge_account_key(entry)
    if mycharge_key:
        valid.add(mycharge_key)
    return valid


async def async_cleanup_orphan_registrations(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove stale entity and device registry entries after config changes."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    valid_unique_ids = _configured_entity_unique_ids(entry)
    valid_device_ids = _configured_device_ids(entry)

    for entity_entry in list(er.async_entries_for_config_entry(entity_registry, entry.entry_id)):
        if entity_entry.domain != "sensor":
            continue
        if entity_entry.unique_id in valid_unique_ids:
            continue
        if mycharge_key := _mycharge_account_key(entry):
            if entity_entry.unique_id.startswith(f"{mycharge_key}_charging_card_"):
                continue
        _LOGGER.info("Removing stale entity registry entry %s", entity_entry.entity_id)
        entity_registry.async_remove(entity_entry.entity_id)

    for device_entry in list(dr.async_entries_for_config_entry(device_registry, entry.entry_id)):
        device_ids = {
            identifier[1].lower()
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN
        }
        if not device_ids:
            continue
        if device_ids & valid_device_ids:
            continue
        _LOGGER.info("Removing stale device registry entry %s", device_entry.id)
        device_registry.async_remove_device(device_entry.id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up InCharge from a config entry."""
    await async_setup_services(hass)

    client = InChargeClient(
        hass,
        device_id=entry.data[CONF_DEVICE_ID],
        x_token=entry.data[CONF_X_TOKEN],
        mycharge_auth=entry.options.get(CONF_MYCHARGE),
        apk_sha1=entry.data[CONF_APK_SHA1],
        apk_crc=entry.data[CONF_APK_CRC],
    )
    coordinator = InChargeDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
    }

    await async_cleanup_orphan_registrations(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_MYCHARGE_TOKENS):
        return

    async def refresh_mycharge_tokens(call: ServiceCall) -> None:
        entry_id = call.data.get("entry_id")
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if not entry_id or entry.entry_id == entry_id
        ]
        if entry_id and not entries:
            raise HomeAssistantError(f"No Vattenfall InCharge entry found for {entry_id}")

        refreshed = 0
        for entry in entries:
            entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not entry_data:
                continue
            client: InChargeClient = entry_data[DATA_CLIENT]
            coordinator: InChargeDataUpdateCoordinator = entry_data[DATA_COORDINATOR]
            if not client.mycharge_auth:
                continue

            try:
                await client.async_refresh_mycharge_tokens(source="manual_service")
            except InChargeApiError as err:
                _LOGGER.warning("Manual My InCharge token refresh failed: %s", err)
                raise HomeAssistantError(
                    "My InCharge token refresh failed. Reconnect the account from "
                    "Configure > Add or update My InCharge account, then run this test again."
                ) from err
            entry_data[DATA_SKIP_RELOAD_ONCE] = True
            hass.config_entries.async_update_entry(
                entry,
                options={**entry.options, CONF_MYCHARGE: client.mycharge_auth},
            )
            coordinator.force_mycharge_update()
            await coordinator.async_request_refresh()
            refreshed += 1

        if refreshed == 0:
            raise HomeAssistantError("No configured My InCharge account found to refresh")

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_MYCHARGE_TOKENS,
        refresh_mycharge_tokens,
        schema=SERVICE_REFRESH_MYCHARGE_TOKENS_SCHEMA,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an InCharge config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry on update."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_data and entry_data.pop(DATA_SKIP_RELOAD_ONCE, False):
        _LOGGER.debug("Skipping reload after internal My InCharge token update")
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a station device and its charging points from this config entry."""
    station_ids = {
        identifier[1].lower()
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
    }
    station_ids = {
        identifier
        for identifier in station_ids
        if not identifier.startswith("mycharge_account:")
    }
    if not station_ids:
        _LOGGER.warning(
            "Device removal requested without matching %s identifiers: %s",
            DOMAIN,
            device_entry.identifiers,
        )
        return False

    charging_points = entry.options.get(CONF_CHARGING_POINTS, [])
    filtered_points = [
        point
        for point in charging_points
        if str(point.get("stationUuid", "")).lower() not in station_ids
    ]
    if len(filtered_points) == len(charging_points):
        _LOGGER.warning(
            "No charging points matched station ids %s during device removal",
            station_ids,
        )
        return False

    _LOGGER.info(
        "Removing InCharge station device %s with station ids %s",
        device_entry.id,
        sorted(station_ids),
    )
    new_options = {**entry.options, CONF_CHARGING_POINTS: filtered_points}
    hass.config_entries.async_update_entry(entry, options=new_options)
    return True
