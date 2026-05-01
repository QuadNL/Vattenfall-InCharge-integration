"""Config flow for the Vattenfall InCharge integration."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import voluptuous as vol
from aiohttp import ClientError

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import InChargeApiError, InChargeClient
from .const import (
    CONF_APK_CRC,
    CONF_APK_SHA1,
    CONF_CHARGING_POINTS,
    CONF_DEVICE_ID,
    CONF_MYCHARGE,
    CONF_POLL_MINUTES,
    CONF_SEARCH_TERM,
    CONF_X_TOKEN,
    DEFAULT_NAME,
    DEFAULT_POLL_MINUTES,
    DOMAIN,
    MOBILE_APP_CRC,
    MOBILE_APP_SHA1,
)

_LOGGER = logging.getLogger(__name__)
CONF_REMOVE_STATION_UUIDS = "remove_station_uuids"
CONF_CALLBACK_URL = "callback_url"


class VattenfallInChargePublicStationsConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._name = DEFAULT_NAME
        self._poll_minutes = DEFAULT_POLL_MINUTES
        self._device_id: str | None = None
        self._x_token: str | None = None
        self._charging_points: list[dict[str, Any]] = []
        self._mycharge_auth: dict[str, Any] | None = None
        self._mycharge_state: str | None = None
        self._mycharge_code_verifier: str | None = None
        self._mycharge_authorize_url: str = ""

    def _create_config_entry(self) -> config_entries.FlowResult:
        options: dict[str, Any] = {
            CONF_CHARGING_POINTS: self._charging_points,
            CONF_POLL_MINUTES: self._poll_minutes,
        }
        if self._mycharge_auth is not None:
            options[CONF_MYCHARGE] = self._mycharge_auth

        return self.async_create_entry(
            title=self._name,
            data={
                CONF_DEVICE_ID: self._device_id,
                CONF_X_TOKEN: self._x_token,
                CONF_APK_SHA1: MOBILE_APP_SHA1,
                CONF_APK_CRC: MOBILE_APP_CRC,
            },
            options=options,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            client = InChargeClient(
                self.hass,
                apk_sha1=MOBILE_APP_SHA1,
                apk_crc=MOBILE_APP_CRC,
            )
            try:
                device_id, x_token = await client.async_bootstrap_device()
            except (InChargeApiError, ClientError, TimeoutError):
                _LOGGER.exception("Failed to bootstrap Vattenfall InCharge device")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                self._name = user_input["name"]
                self._poll_minutes = user_input[CONF_POLL_MINUTES]
                self._device_id = device_id
                self._x_token = x_token
                return await self.async_step_add_first_station()

        schema = vol.Schema(
            {
                vol.Required("name", default=DEFAULT_NAME): str,
                vol.Required(
                    CONF_POLL_MINUTES, default=DEFAULT_POLL_MINUTES
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=120,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_add_first_station(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            search_term = str(user_input.get(CONF_SEARCH_TERM, "")).strip()
            if not search_term:
                return await self.async_step_add_first_mycharge_account()

            client = InChargeClient(
                self.hass,
                device_id=self._device_id,
                x_token=self._x_token,
                apk_sha1=MOBILE_APP_SHA1,
                apk_crc=MOBILE_APP_CRC,
            )
            try:
                found_points = await client.async_collect_station_points(
                    search_term
                )
            except InChargeApiError:
                _LOGGER.exception("Failed to search Vattenfall InCharge stations")
                errors["base"] = "cannot_connect"
            else:
                if not found_points:
                    errors["base"] = "charging_station_not_found"
                else:
                    self._charging_points = found_points
                    return await self.async_step_add_first_mycharge_account()

        schema = vol.Schema(
            {
                vol.Optional(CONF_SEARCH_TERM): str,
            }
        )
        return self.async_show_form(
            step_id="add_first_station",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_add_first_mycharge_account(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        client = InChargeClient(
            self.hass,
            device_id=self._device_id,
            x_token=self._x_token,
            apk_sha1=MOBILE_APP_SHA1,
            apk_crc=MOBILE_APP_CRC,
        )

        if self._mycharge_state is None or self._mycharge_code_verifier is None:
            self._mycharge_state = secrets.token_hex(16)
            self._mycharge_code_verifier, code_challenge = (
                client.create_mycharge_pkce_pair()
            )
            self._mycharge_authorize_url = client.build_mycharge_authorize_url(
                self._mycharge_state,
                code_challenge,
            )

        if user_input is not None:
            callback_url = str(user_input.get(CONF_CALLBACK_URL, "")).strip()
            if not callback_url:
                return self._create_config_entry()

            try:
                code, returned_state = client.extract_mycharge_code_and_state(
                    callback_url
                )
                if returned_state != self._mycharge_state:
                    errors["base"] = "invalid_auth_state"
                else:
                    tokens = await client.async_exchange_mycharge_code(
                        code, self._mycharge_code_verifier
                    )
                    self._mycharge_auth = {
                        "tokens": tokens,
                        "profile": client.build_mycharge_profile(tokens),
                    }
                    return self._create_config_entry()
            except InChargeApiError:
                _LOGGER.exception("Failed to connect My InCharge account")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="add_first_mycharge_account",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CALLBACK_URL): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "auth_url": self._mycharge_authorize_url,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return VattenfallInChargePublicStationsOptionsFlow(config_entry)


class VattenfallInChargePublicStationsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._mycharge_state: str | None = None
        self._mycharge_code_verifier: str | None = None

    def _merged_options(self, **updates: Any) -> dict[str, Any]:
        return {**self._config_entry.options, **updates}

    def _station_choices(self) -> list[selector.SelectOptionDict]:
        stations: dict[str, dict[str, Any]] = {}
        for point in self._config_entry.options.get(CONF_CHARGING_POINTS, []):
            station_uuid = str(point.get("stationUuid", "")).lower()
            if not station_uuid or station_uuid in stations:
                continue
            stations[station_uuid] = point

        choices: list[selector.SelectOptionDict] = []
        for station_uuid, point in sorted(
            stations.items(),
            key=lambda item: (
                str(item[1].get("stationName") or item[1].get("displayName") or "")
            ).casefold(),
        ):
            station_name = point.get("stationName") or point.get("displayName") or station_uuid
            address = (point.get("location") or {}).get("address")
            label = station_name if not address else f"{station_name} - {address}"
            choices.append(
                selector.SelectOptionDict(
                    value=station_uuid,
                    label=label,
                )
            )
        return choices

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_charging_point",
                "remove_charging_points",
                "mycharge_account",
                "advanced",
            ],
        )

    async def async_step_add_charging_point(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            current_points = self._config_entry.options.get(CONF_CHARGING_POINTS, [])
            merged = {str(point["uuid"]).lower(): point for point in current_points}
            client = InChargeClient(
                self.hass,
                device_id=self._config_entry.data[CONF_DEVICE_ID],
                x_token=self._config_entry.data[CONF_X_TOKEN],
                apk_sha1=self._config_entry.data[CONF_APK_SHA1],
                apk_crc=self._config_entry.data[CONF_APK_CRC],
            )
            search_term = str(user_input[CONF_SEARCH_TERM]).strip()
            try:
                found_points = await client.async_collect_station_points(search_term)
            except InChargeApiError:
                _LOGGER.exception("Failed to search Vattenfall InCharge stations")
                errors["base"] = "cannot_connect"
            else:
                if not found_points:
                    errors["base"] = "charging_station_not_found"
                else:
                    for point in found_points:
                        merged[str(point["uuid"]).lower()] = point

            if not errors:
                return self.async_create_entry(
                    title="",
                    data=self._merged_options(
                        **{CONF_CHARGING_POINTS: list(merged.values())}
                    ),
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SEARCH_TERM): str,
            }
        )
        return self.async_show_form(
            step_id="add_charging_point", data_schema=schema, errors=errors
        )

    async def async_step_remove_charging_points(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            remove_station_uuids = {
                str(value).lower()
                for value in user_input.get(CONF_REMOVE_STATION_UUIDS, [])
            }
            filtered_points = [
                point
                for point in self._config_entry.options.get(CONF_CHARGING_POINTS, [])
                if str(point.get("stationUuid", "")).lower() not in remove_station_uuids
            ]
            return self.async_create_entry(
                title="",
                data=self._merged_options(**{CONF_CHARGING_POINTS: filtered_points}),
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_REMOVE_STATION_UUIDS,
                    default=[],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._station_choices(),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="remove_charging_points",
            data_schema=schema,
        )

    async def async_step_mycharge_account(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        client = InChargeClient(
            self.hass,
            device_id=self._config_entry.data[CONF_DEVICE_ID],
            x_token=self._config_entry.data[CONF_X_TOKEN],
            apk_sha1=self._config_entry.data[CONF_APK_SHA1],
            apk_crc=self._config_entry.data[CONF_APK_CRC],
        )

        if self._mycharge_state is None or self._mycharge_code_verifier is None:
            self._mycharge_state = secrets.token_hex(16)
            self._mycharge_code_verifier, code_challenge = client.create_mycharge_pkce_pair()
            self._mycharge_authorize_url = client.build_mycharge_authorize_url(
                self._mycharge_state,
                code_challenge,
            )

        if user_input is not None:
            callback_url = str(user_input[CONF_CALLBACK_URL]).strip()
            try:
                code, returned_state = client.extract_mycharge_code_and_state(callback_url)
                if returned_state != self._mycharge_state:
                    errors["base"] = "invalid_auth_state"
                else:
                    tokens = await client.async_exchange_mycharge_code(
                        code, self._mycharge_code_verifier
                    )
                    mycharge_auth = {
                        "tokens": tokens,
                        "profile": client.build_mycharge_profile(tokens),
                    }
                    return self.async_create_entry(
                        title="",
                        data=self._merged_options(**{CONF_MYCHARGE: mycharge_auth}),
                    )
            except InChargeApiError:
                _LOGGER.exception("Failed to connect My InCharge account")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="mycharge_account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CALLBACK_URL): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "auth_url": getattr(self, "_mycharge_authorize_url", ""),
            },
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=self._merged_options(
                    **{CONF_POLL_MINUTES: user_input[CONF_POLL_MINUTES]}
                ),
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_MINUTES,
                    default=self._config_entry.options.get(
                        CONF_POLL_MINUTES, DEFAULT_POLL_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=120,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="advanced",
            data_schema=schema,
        )
