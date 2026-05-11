"""Public InCharge API client."""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import logging
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    APP_ACCEPT,
    MOBILE_APIM_KEY,
    MOBILE_BASE_URL,
    MYCHARGE_AUTHORIZE_URL,
    MYCHARGE_CLIENT_ID,
    MYCHARGE_REDIRECT_URI,
    MYCHARGE_SCOPE,
    MYCHARGE_SERVICE_PROVIDER,
    MYCHARGE_TENANT_DOMAIN,
    MYCHARGE_TOKEN_URL,
    PORTAL_APIM_KEY,
    PORTAL_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)
MYCHARGE_HISTORY_STATUSES = {
    "validated": "ACCEPTED,RATED,VALIDATED",
    "cancelled": "CANCELLED",
}


class InChargeApiError(Exception):
    """Raised when the InCharge API call fails."""


class InChargeClient:
    """Small public API client for InCharge charging points."""

    def __init__(
        self,
        hass,
        *,
        device_id: str | None = None,
        x_token: str | None = None,
        mycharge_auth: dict[str, Any] | None = None,
        apk_sha1: str,
        apk_crc: int,
    ) -> None:
        self.hass = hass
        self._session = async_get_clientsession(hass)
        self.device_id = device_id
        self.x_token = x_token
        self.mycharge_auth = mycharge_auth or {}
        self.apk_sha1 = apk_sha1
        self.apk_crc = apk_crc

    def _headers(self, include_token: bool = True) -> dict[str, str]:
        headers = {
            "Accept": APP_ACCEPT,
            "Content-Type": "application/json",
            "User-Agent": "Android",
            "Ocp-Apim-Subscription-Key": MOBILE_APIM_KEY,
            "Apk-CRC": str(self.apk_crc),
            "Apk-SHA1": self.apk_sha1,
            "Device-Id": self.device_id or "",
        }
        if include_token and self.x_token:
            headers["X-Token"] = self.x_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        include_token: bool = True,
        json_body: Any | None = None,
        retry_unauthorized: bool = False,
    ) -> Any:
        url = f"{MOBILE_BASE_URL}{path}"

        for attempt in range(3 if retry_unauthorized else 1):
            async with self._session.request(
                method,
                url,
                headers=self._headers(include_token=include_token),
                json=json_body,
                timeout=30,
            ) as response:
                text = await response.text()
                if response.status == 401 and retry_unauthorized and attempt < 2:
                    await asyncio.sleep(1)
                    continue
                if response.status >= 400:
                    raise InChargeApiError(
                        f"{method} {path} failed with {response.status}: {text}"
                    )
                if not text:
                    return None
                return await response.json()

        raise InChargeApiError(f"{method} {path} failed after retry")

    async def _portal_request(
        self,
        method: str,
        path: str,
        *,
        bearer_token: str,
        accept: str = "application/json, text/plain, */*",
        form_body: dict[str, str] | None = None,
    ) -> Any:
        url = f"{PORTAL_BASE_URL}{path}"
        headers = {
            "Accept": accept,
            "Origin": "https://myincharge.vattenfall.com",
            "Referer": "https://myincharge.vattenfall.com/",
        }
        data = None
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
            headers["Ocp-Apim-Subscription-Key"] = PORTAL_APIM_KEY
        if form_body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = form_body

        async with self._session.request(
            method,
            url,
            headers=headers,
            data=data,
            timeout=30,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise InChargeApiError(
                    f"{method} {path} failed with {response.status}: {text}"
                )
            if not text:
                return None
            if "json" in response.headers.get("content-type", ""):
                return json.loads(text)
            return text

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    @classmethod
    def create_mycharge_pkce_pair(cls) -> tuple[str, str]:
        verifier = cls._b64url(secrets.token_bytes(64))
        challenge = cls._b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return verifier, challenge

    @staticmethod
    def build_mycharge_authorize_url(state: str, code_challenge: str) -> str:
        from urllib.parse import urlencode

        query = {
            "client_id": MYCHARGE_CLIENT_ID,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "forceAuth": "false",
            "passiveAuth": "false",
            "redirect_uri": MYCHARGE_REDIRECT_URI,
            "response_mode": "query",
            "response_type": "code",
            "scope": MYCHARGE_SCOPE,
            "state": state,
            "tenantDomain": MYCHARGE_TENANT_DOMAIN,
            "relyingParty": MYCHARGE_CLIENT_ID,
            "type": "oidc",
            "sp": MYCHARGE_SERVICE_PROVIDER,
            "isSaaSApp": "false",
            "authenticators": "BasicAuthenticator:LOCAL",
        }
        return MYCHARGE_AUTHORIZE_URL + "?" + urlencode(query)

    @staticmethod
    def normalize_mycharge_callback_input(callback_input: str) -> str:
        text = callback_input.strip()
        match = re.search(r"(https://myincharge\.vattenfall\.com\?authType=customer[^'\"]+)", text)
        if match:
            return match.group(1)
        return text

    @staticmethod
    def extract_mycharge_code_and_state(callback_url: str) -> tuple[str, str]:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(InChargeClient.normalize_mycharge_callback_input(callback_url))
        params = parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        state = params.get("state", [""])[0]
        if not code:
            raise InChargeApiError("No code parameter found in the callback URL.")
        if not state:
            raise InChargeApiError("No state parameter found in the callback URL.")
        return code, state

    @staticmethod
    def decode_jwt_payload(token: str) -> dict[str, Any]:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))

    @staticmethod
    def mycharge_account_number_from_tokens(tokens: dict[str, Any]) -> str | None:
        payload = InChargeClient.decode_jwt_payload(str(tokens.get("id_token", "")))
        roles = payload.get("roles", [])
        if not isinstance(roles, list):
            return None
        for role in roles:
            match = re.search(r"_(\d+)$", str(role))
            if match:
                return match.group(1)
        return None

    @staticmethod
    def mycharge_token_seconds_left(tokens: dict[str, Any]) -> int | None:
        payload = InChargeClient.decode_jwt_payload(str(tokens.get("id_token", "")))
        exp = payload.get("exp")
        if not isinstance(exp, int):
            return None
        return exp - int(time.time())

    @classmethod
    def mycharge_tokens_need_refresh(
        cls, tokens: dict[str, Any], *, refresh_window: int = 1800
    ) -> bool:
        seconds_left = cls.mycharge_token_seconds_left(tokens)
        return seconds_left is None or seconds_left < refresh_window

    @classmethod
    def build_mycharge_profile(cls, tokens: dict[str, Any]) -> dict[str, Any]:
        payload = cls.decode_jwt_payload(str(tokens.get("id_token", "")))
        return {
            "sub": payload.get("sub"),
            "email": payload.get("email"),
            "username": payload.get("username"),
            "given_name": payload.get("given_name"),
            "customer_id": payload.get("customerid"),
            "tenant_domain": payload.get("tenant_domain"),
            "account_number": cls.mycharge_account_number_from_tokens(tokens),
            "roles": payload.get("roles", []),
            "scope": payload.get("scope", []),
            "expires_at": payload.get("exp"),
        }

    async def async_exchange_mycharge_code(
        self, code: str, code_verifier: str
    ) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": MYCHARGE_CLIENT_ID,
            "code": code,
            "redirect_uri": MYCHARGE_REDIRECT_URI,
            "code_verifier": code_verifier,
        }
        async with self._session.post(
            MYCHARGE_TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://myincharge.vattenfall.com",
                "Referer": "https://myincharge.vattenfall.com/",
            },
            data=payload,
            timeout=30,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise InChargeApiError(
                    f"POST token exchange failed with {response.status}: {text}"
                )
            return json.loads(text)

    async def async_refresh_mycharge_tokens(self, *, source: str = "automatic") -> dict[str, Any]:
        refresh_token = self.mycharge_auth.get("tokens", {}).get("refresh_token")
        if not refresh_token:
            raise InChargeApiError("No My InCharge refresh_token available.")
        previous_seconds_left = self.mycharge_token_seconds_left(
            self.mycharge_auth.get("tokens", {})
        )
        payload = {
            "grant_type": "refresh_token",
            "client_id": MYCHARGE_CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": MYCHARGE_SCOPE,
            "redirect_uri": MYCHARGE_REDIRECT_URI,
        }
        async with self._session.post(
            MYCHARGE_TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://myincharge.vattenfall.com",
                "Referer": "https://myincharge.vattenfall.com/",
            },
            data=payload,
            timeout=30,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise InChargeApiError(
                    f"POST refresh failed with {response.status}: {text}"
                )
            refreshed = json.loads(text)
        merged_tokens = {**self.mycharge_auth.get("tokens", {}), **refreshed}
        self.mycharge_auth = {
            **self.mycharge_auth,
            "tokens": merged_tokens,
            "profile": self.build_mycharge_profile(merged_tokens),
            "last_token_refresh": {
                "source": source,
                "refreshed_at": datetime.now(UTC).isoformat(),
                "previous_seconds_left": previous_seconds_left,
                "new_seconds_left": self.mycharge_token_seconds_left(merged_tokens),
                "expires_at": self.build_mycharge_profile(merged_tokens).get("expires_at"),
            },
        }
        profile = self.mycharge_auth.get("profile") or {}
        _LOGGER.info(
            "My InCharge token refreshed successfully for account %s; token expires in %s seconds; source=%s",
            profile.get("account_number") or "unknown",
            self.mycharge_token_seconds_left(merged_tokens),
            source,
        )
        return self.mycharge_auth

    async def async_get_mycharge_account_hierarchy(self) -> Any:
        id_token = str(self.mycharge_auth.get("tokens", {}).get("id_token", ""))
        if not id_token:
            raise InChargeApiError("No My InCharge id_token available.")
        return await self._portal_request(
            "GET",
            "/account-management/accounts/hierarchy",
            bearer_token=id_token,
        )

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip().replace(",", ".")
            try:
                return float(normalized)
            except ValueError:
                return None
        return None

    @classmethod
    def _energy_value_kwh(cls, key: str, value: Any) -> float | None:
        lowered = key.casefold()
        number = cls._as_float(value)
        if number is None:
            return None
        if "kwh" in lowered:
            return number
        if "wh" in lowered and "kwh" not in lowered:
            return number / 1000
        if "energy" in lowered or "consumption" in lowered:
            return number
        return None

    @classmethod
    def _duration_value_hours(cls, key: str, value: Any) -> float | None:
        lowered = key.casefold()
        number = cls._as_float(value)
        if number is not None:
            if "duration" not in lowered and "chargingtime" not in lowered:
                return None
            if "millisecond" in lowered or lowered.endswith("ms"):
                return number / 3_600_000
            if "second" in lowered or lowered.endswith("sec") or lowered.endswith("s"):
                return number / 3600
            if "minute" in lowered or lowered.endswith("min"):
                return number / 60
            return number

        if isinstance(value, str):
            match = re.fullmatch(
                r"PT(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
                value.strip(),
            )
            if match:
                hours = float(match.group("hours") or 0)
                minutes = float(match.group("minutes") or 0)
                seconds = float(match.group("seconds") or 0)
                return hours + minutes / 60 + seconds / 3600
        return None

    @classmethod
    def _summarize_charging_history_json(cls, payload: Any) -> dict[str, Any]:
        energy_kwh = 0.0
        duration_hours = 0.0
        energy_matches = 0
        duration_matches = 0
        item_count = 0
        top_level_keys: list[str] = []

        if isinstance(payload, dict):
            top_level_keys = sorted(str(key) for key in payload.keys())
        elif isinstance(payload, list):
            item_count = len(payload)

        def walk(value: Any, parent_key: str = "") -> None:
            nonlocal energy_kwh, duration_hours, energy_matches, duration_matches, item_count
            if isinstance(value, dict):
                if parent_key:
                    item_count += 1
                for child_key, child_value in value.items():
                    key = str(child_key)
                    energy = cls._energy_value_kwh(key, child_value)
                    if energy is not None:
                        energy_kwh += energy
                        energy_matches += 1
                    duration = cls._duration_value_hours(key, child_value)
                    if duration is not None:
                        duration_hours += duration
                        duration_matches += 1
                    if isinstance(child_value, (dict, list)):
                        walk(child_value, key)
            elif isinstance(value, list):
                item_count += len(value)
                for item in value:
                    walk(item, parent_key)

        walk(payload)
        return {
            "energy_kwh": round(energy_kwh, 3),
            "duration_hours": round(duration_hours, 3),
            "energy_field_matches": energy_matches,
            "duration_field_matches": duration_matches,
            "source_item_count": item_count,
            "source_top_level_keys": top_level_keys,
        }

    @classmethod
    def _summarize_charging_history_csv(cls, csv_text: str) -> dict[str, Any]:
        reader = csv.DictReader(io.StringIO(csv_text))
        total_kwh = 0.0
        total_seconds = 0.0
        row_count = 0
        fieldnames = list(reader.fieldnames or [])

        for row in reader:
            row_count += 1
            total_kwh += cls._as_float(row.get("Charged (kWh)")) or 0.0
            total_seconds += cls._as_float(row.get("Duration in seconds")) or 0.0

        return {
            "energy_kwh": round(total_kwh, 3),
            "duration_hours": round(total_seconds / 3600, 3),
            "source_row_count": row_count,
            "source_fieldnames": fieldnames,
        }

    @staticmethod
    def _first_value(item: dict[str, Any], *keys: str) -> Any:
        lower_lookup = {str(key).casefold(): value for key, value in item.items()}
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
            value = lower_lookup.get(key.casefold())
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _first_float(cls, item: dict[str, Any], *keys: str) -> float | None:
        return cls._as_float(cls._first_value(item, *keys))

    @staticmethod
    def _format_history_time(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _format_history_end_time(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S")

    def _mycharge_history_period(self, period_days: int) -> tuple[datetime, datetime]:
        now = dt_util.now()
        start = (now - timedelta(days=period_days)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end = (now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return start, end

    @staticmethod
    def _start_of_month(value: datetime) -> datetime:
        return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @classmethod
    def _previous_month_period(cls, value: datetime) -> tuple[datetime, datetime]:
        current_month = cls._start_of_month(value)
        last_month_end = current_month - timedelta(seconds=1)
        return cls._start_of_month(last_month_end), last_month_end

    @classmethod
    def _mycharge_cost_periods(cls) -> dict[str, tuple[datetime, datetime]]:
        now = dt_util.now()
        current_month_start = cls._start_of_month(now)
        next_day_start = (now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        last_month_start, last_month_end = cls._previous_month_period(now)
        year_start = now.replace(
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return {
            "current_month": (current_month_start, next_day_start - timedelta(seconds=1)),
            "last_month": (last_month_start, last_month_end),
            "this_year": (year_start, next_day_start - timedelta(seconds=1)),
        }

    @classmethod
    def _summarize_charging_costs(
        cls,
        payload: Any,
        *,
        account_number: str,
        period_key: str,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, Any]:
        series: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for bucket in payload:
                if isinstance(bucket, dict) and isinstance(bucket.get("series"), list):
                    series.extend(
                        item for item in bucket["series"] if isinstance(item, dict)
                    )
                elif isinstance(bucket, dict):
                    series.append(bucket)
        elif isinstance(payload, dict) and isinstance(payload.get("series"), list):
            series.extend(item for item in payload["series"] if isinstance(item, dict))

        total_cost = 0.0
        total_kwh = 0.0
        session_count = 0
        active_days = 0
        latest_days: list[dict[str, Any]] = []
        for item in series:
            cost = cls._as_float(item.get("cost")) or 0.0
            consumption = cls._as_float(item.get("consumption")) or 0.0
            sessions = int(cls._as_float(item.get("numberOfSessions")) or 0)
            total_cost += cost
            total_kwh += consumption
            session_count += sessions
            if cost or consumption or sessions:
                active_days += 1
                latest_days.append(
                    {
                        "date": cls._history_bucket_date(item),
                        "cost": cost,
                        "consumption_kwh": consumption,
                        "number_of_sessions": sessions,
                    }
                )

        return {
            "account_number": account_number,
            "period": period_key,
            "period_start": cls._format_history_time(period_start),
            "period_end": cls._format_history_end_time(period_end),
            "cost": round(total_cost, 2),
            "currency": "EUR",
            "consumption_kwh": round(total_kwh, 3),
            "number_of_sessions": session_count,
            "active_days": active_days,
            "source_day_count": len(series),
            "latest_active_days": latest_days[-5:],
        }

    @staticmethod
    def _history_bucket_date(item: dict[str, Any]) -> str | None:
        if isinstance(item.get("date"), list):
            return "-".join(str(part) for part in item["date"])
        if item.get("year") and item.get("month") and item.get("day"):
            return f"{item['year']}-{item['month']}-{item['day']}"
        return None

    @classmethod
    def _billing_price_variants(cls, item: dict[str, Any]) -> list[dict[str, Any]]:
        billing_details = item.get("billingDetails")
        if not isinstance(billing_details, dict):
            return []
        variants = billing_details.get("priceVariants")
        if not isinstance(variants, list):
            return []
        return [
            {
                "time_from": variant.get("timeFrom"),
                "time_to": variant.get("timeTo"),
                "price_per_kwh_excl_vat": cls._as_float(
                    variant.get("billingPricePerKWH")
                ),
                "kwh_cost_excl_vat": cls._as_float(variant.get("kwhCostExclVat")),
                "consumption_kwh": cls._as_float(variant.get("consumptionInKWH")),
            }
            for variant in variants
            if isinstance(variant, dict)
        ]

    @classmethod
    def _summarize_charging_history_page(
        cls,
        payload: Any,
        *,
        status_key: str,
        period_days: int,
        period_start: datetime,
        period_end: datetime,
        account_number: str,
    ) -> dict[str, Any]:
        content = []
        if isinstance(payload, dict) and isinstance(payload.get("content"), list):
            content = payload["content"]

        total_kwh = 0.0
        total_seconds = 0.0
        total_cost_incl_vat = 0.0
        cost_matches = 0
        latest_sessions: list[dict[str, Any]] = []

        for item in content:
            if not isinstance(item, dict):
                continue
            charged_kwh = cls._first_float(
                item,
                "chargedKwh",
                "chargedKWh",
                "chargedInKwh",
                "chargedInKWh",
                "consumptionInKWH",
                "consumptionInKwh",
                "consumption",
                "charged",
                "energyKwh",
                "Geladen (kWh)",
                "Charged (kWh)",
            )
            duration_seconds = cls._first_float(
                item,
                "durationInSeconds",
                "durationSeconds",
                "Duur in seconden",
                "Duration in seconds",
            )
            cost_incl_vat = cls._first_float(
                item,
                "totalCostInclVat",
                "totalCostInclVAT",
                "totalCostIncludingVat",
                "invoiceCostInclVat",
                "Totale kosten (incl. Btw)",
                "Total Cost (Incl. VAT)",
            )
            if charged_kwh is not None:
                total_kwh += charged_kwh
            if duration_seconds is not None:
                total_seconds += duration_seconds
            if cost_incl_vat is not None:
                total_cost_incl_vat += cost_incl_vat
                cost_matches += 1

            if len(latest_sessions) < 5:
                price_variants = cls._billing_price_variants(item)
                latest_sessions.append(
                    {
                        "transaction_id": cls._first_value(item, "transactionId"),
                        "card": cls._first_value(item, "cardNumber", "Laadpas"),
                        "custom_name": cls._first_value(
                            item, "customName", "Aangepaste naam"
                        ),
                        "station_name": cls._first_value(item, "stationName"),
                        "point_name": cls._first_value(item, "pointName"),
                        "charging_station": cls._first_value(
                            item, "chargingStation", "stationName", "Laadpaal"
                        ),
                        "address": cls._first_value(item, "address", "adres"),
                        "postalcode": cls._first_value(item, "zipCode", "postcode"),
                        "city": cls._first_value(item, "city", "stad"),
                        "station_operator": cls._first_value(item, "stationOperator"),
                        "cpo_name": cls._first_value(item, "cpoName"),
                        "cpo_type": cls._first_value(item, "cpoType"),
                        "start_time": cls._first_value(
                            item, "startTimeWithTimezone", "startTime", "Starttijd"
                        ),
                        "end_time": cls._first_value(
                            item, "endTimeWithTimezone", "endTime", "Eindtijd"
                        ),
                        "duration": cls._first_value(item, "duration", "Duur"),
                        "duration_seconds": duration_seconds,
                        "charged_kwh": charged_kwh,
                        "price_per_kwh_excl_vat": (
                            price_variants[0].get("price_per_kwh_excl_vat")
                            if price_variants
                            else None
                        ),
                        "price_per_minute_excl_vat": cls._first_float(
                            item,
                            "pricePerMinuteExclVat",
                            "Prijs per minuut (excl. btw)",
                        ),
                        "minute_price_duration": cls._first_float(
                            item, "minutePriceDuration", "Minuut-prijsduur"
                        ),
                        "start_fee_excl_vat": cls._first_float(
                            item,
                            "priceForStartingFeeExclVat",
                            "Starttarief (excl. btw)",
                        ),
                        "total_cost_excl_vat": cls._first_float(
                            item,
                            "totalCostExclVat",
                            "Totale kosten (excl. btw)",
                        ),
                        "total_cost_incl_vat": cost_incl_vat,
                        "invoice_cost_incl_vat": cls._first_float(
                            item, "invoiceCostInclVat", "Factuurkosten (incl. Btw)"
                        ),
                        "currency": cls._first_value(item, "currency", "Currency"),
                        "status": cls._first_value(item, "status"),
                        "price_variants": price_variants,
                    }
                )

        if isinstance(payload, dict):
            count = payload.get("totalElements")
            if count is None:
                count = payload.get("numberOfElements")
        else:
            count = None
        if count is None:
            count = len(content)

        return {
            "account_number": account_number,
            "status_key": status_key,
            "period_days": period_days,
            "period_start": cls._format_history_time(period_start),
            "period_end": cls._format_history_time(period_end),
            "session_count": count,
            "page_count": len(content),
            "total_kwh": round(total_kwh, 3),
            "total_duration_hours": round(total_seconds / 3600, 3),
            "total_duration_seconds": round(total_seconds),
            "total_cost_incl_vat": round(total_cost_incl_vat, 2)
            if cost_matches
            else None,
            "latest_sessions": latest_sessions,
        }

    async def async_get_mycharge_charging_history_summary(
        self,
        *,
        period_days: int = 30,
    ) -> dict[str, Any]:
        id_token = str(self.mycharge_auth.get("tokens", {}).get("id_token", ""))
        if not id_token:
            raise InChargeApiError("No My InCharge id_token available.")

        profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
            self.mycharge_auth.get("tokens", {})
        )
        account_number = profile.get("account_number")
        if not account_number:
            raise InChargeApiError("No My InCharge account number available.")

        start, end = self._mycharge_history_period(period_days)
        history: dict[str, Any] = {}
        for status_key, status_value in MYCHARGE_HISTORY_STATUSES.items():
            query = urlencode(
                {
                    "startTime": self._format_history_time(start),
                    "endTime": self._format_history_time(end),
                    "status": status_value,
                    "page": "0",
                    "size": "30",
                    "sort": "cardNumber,endTime",
                    "selectedAccount": account_number,
                }
            )
            payload = await self._portal_request(
                "GET",
                f"/usage-data-pcu-readmodel/api/pcu-charging-history?{query}",
                bearer_token=id_token,
                accept="application/vnd.vattenfall.charging-history+json",
            )
            history[status_key] = self._summarize_charging_history_page(
                payload,
                status_key=status_key,
                period_days=period_days,
                period_start=start,
                period_end=end,
                account_number=account_number,
            )

        validated = history["validated"]
        return {
            "energy_kwh": validated["total_kwh"],
            "duration_hours": validated["total_duration_hours"],
            "period_days": period_days,
            "period_start": self._format_history_time(start),
            "period_end": self._format_history_time(end),
            "account_number": account_number,
            "validated": validated,
            "cancelled": history["cancelled"],
        }

    async def async_get_mycharge_charging_history_export_summary(
        self,
        *,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Fetch charging history CSV export totals.

        Kept for later report download work; the dashboard sensors use the JSON
        history endpoint because it is what the portal uses for screen data.
        """
        id_token = str(self.mycharge_auth.get("tokens", {}).get("id_token", ""))
        if not id_token:
            raise InChargeApiError("No My InCharge id_token available.")
        profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
            self.mycharge_auth.get("tokens", {})
        )
        account_number = profile.get("account_number")
        if not account_number:
            raise InChargeApiError("No My InCharge account number available.")
        start, end = self._mycharge_history_period(period_days)
        query = urlencode(
            {
                "startTime": self._format_history_time(start),
                "endTime": self._format_history_time(end),
                "selectedAccount": account_number,
            }
        )
        history_path = f"/usage-data-pcu-readmodel/api/pcu-charging-history?{query}"
        errors: list[str] = []

        try:
            csv_text = await self._portal_request(
                "GET",
                history_path,
                bearer_token=id_token,
                accept="text/vnd.vattenfall.charging-history-v2+csv",
            )
            if isinstance(csv_text, str):
                summary = {
                    **self._summarize_charging_history_csv(csv_text),
                    "source_format": "csv",
                }
            else:
                summary = {
                    **self._summarize_charging_history_json(csv_text),
                    "source_format": "csv_json_fallback",
                }
        except InChargeApiError as err:
            errors.append(f"CSV export failed: {err}")
            try:
                payload = await self._portal_request(
                    "GET",
                    history_path,
                    bearer_token=id_token,
                    accept="application/vnd.vattenfall.daily-charging-summary+json",
                )
                summary = {
                    **self._summarize_charging_history_json(payload),
                    "source_format": "json",
                    "source_warnings": errors,
                }
            except InChargeApiError as json_err:
                errors.append(f"JSON summary failed: {json_err}")
                raise InChargeApiError("; ".join(errors)) from json_err
        return {
            **summary,
            "period_days": period_days,
            "period_start": self._format_history_time(start),
            "period_end": self._format_history_time(end),
            "account_number": account_number,
        }

    async def async_get_mycharge_dashboard_widgets(self) -> dict[str, Any]:
        id_token = str(self.mycharge_auth.get("tokens", {}).get("id_token", ""))
        if not id_token:
            raise InChargeApiError("No My InCharge id_token available.")

        profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
            self.mycharge_auth.get("tokens", {})
        )
        account_number = profile.get("account_number")
        if not account_number:
            raise InChargeApiError("No My InCharge account number available.")

        errors: dict[str, str] = {}
        common_query = urlencode({"period": "30", "selectedAccount": account_number})
        total_hours = None
        try:
            total_hours = await self._portal_request(
                "GET",
                f"/usage-data-pcu-readmodel/api/cards/charging-history/total-hours?{common_query}",
                bearer_token=id_token,
            )
        except InChargeApiError as err:
            errors["total_hours_30d"] = str(err)
        average_consumption: dict[str, Any] = {}
        query = urlencode({"period": "7", "selectedAccount": account_number})
        try:
            average_consumption["7"] = await self._portal_request(
                "GET",
                "/usage-data-pcu-readmodel/api/cards/charging-history/"
                f"average-consumption-per-session?{query}",
                bearer_token=id_token,
            )
        except InChargeApiError as err:
            errors["average_consumption_per_session_7d"] = str(err)

        costs: dict[str, Any] = {}
        periods = self._mycharge_cost_periods()
        periods["last_30_days"] = self._mycharge_history_period(30)
        for period_key, (start, end) in periods.items():
            query = urlencode(
                {
                    "startTime": self._format_history_time(start),
                    "endTime": self._format_history_end_time(end),
                    "selectedAccount": account_number,
                }
            )
            try:
                payload = await self._portal_request(
                    "GET",
                    f"/usage-data-pcu-readmodel/api/pcu-charging-history?{query}",
                    bearer_token=id_token,
                    accept="application/vnd.vattenfall.charging-summary-v2+json",
                )
                costs[period_key] = self._summarize_charging_costs(
                    payload,
                    account_number=account_number,
                    period_key=period_key,
                    period_start=start,
                    period_end=end,
                )
            except InChargeApiError as err:
                errors[f"costs_{period_key}"] = str(err)

        return {
            "account_number": account_number,
            "total_hours_30d": self._as_float(
                total_hours.get("totalHoursCharged")
                if isinstance(total_hours, dict)
                else total_hours
            ),
            "average_consumption_per_session": {
                period: self._as_float(
                    value.get("averageConsumptionPerSession")
                    if isinstance(value, dict)
                    else value
                )
                for period, value in average_consumption.items()
            },
            "costs": costs,
            "last_30_days": costs.get("last_30_days"),
            "errors": errors,
        }

    async def async_get_mycharge_cards_overview(self) -> dict[str, Any]:
        id_token = str(self.mycharge_auth.get("tokens", {}).get("id_token", ""))
        if not id_token:
            raise InChargeApiError("No My InCharge id_token available.")

        profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
            self.mycharge_auth.get("tokens", {})
        )
        account_number = profile.get("account_number")
        if not account_number:
            raise InChargeApiError("No My InCharge account number available.")

        common_query = urlencode({"selectedAccount": account_number})
        tokens_query = urlencode(
            {
                "page": "0",
                "size": "100",
                "selectedAccount": account_number,
            }
        )
        privileges = await self._portal_request(
            "GET",
            f"/emsp-tokens/pub/privileges?{common_query}",
            bearer_token=id_token,
        )
        pending_assignments = await self._portal_request(
            "GET",
            f"/emsp-tokens/pub/pendingAssignments?{common_query}",
            bearer_token=id_token,
        )
        tokens = await self._portal_request(
            "GET",
            f"/emsp-tokens/pub/tokens?{tokens_query}",
            bearer_token=id_token,
        )

        token_items = []
        if isinstance(tokens, dict) and isinstance(tokens.get("content"), list):
            token_items = tokens["content"]

        usage_summary = None
        usage_by_card: list[dict[str, Any]] = []
        usage_error = None
        start, end = self._mycharge_history_period(30)
        usage_query = urlencode(
            {
                "startTime": self._format_history_time(start),
                "endTime": self._format_history_time(end),
                "selectedAccount": account_number,
            }
        )
        try:
            usage_summary = await self._portal_request(
                "GET",
                f"/usage-data-pcu-readmodel/api/pcu-charging-history?{usage_query}",
                bearer_token=id_token,
                accept="application/vnd.vattenfall.cards-summary+json",
            )
            history_query = urlencode(
                {
                    "startTime": self._format_history_time(start),
                    "endTime": self._format_history_time(end),
                    "size": "20",
                    "page": "0",
                    "selectedAccount": account_number,
                }
            )
            usage_history = await self._portal_request(
                "GET",
                f"/usage-data-pcu-readmodel/api/pcu-charging-history?{history_query}",
                bearer_token=id_token,
                accept="application/vnd.vattenfall.cards-history+json",
            )
            if isinstance(usage_history, dict) and isinstance(
                usage_history.get("content"), list
            ):
                usage_by_card = usage_history["content"]
        except InChargeApiError as err:
            usage_error = str(err)

        return {
            "account_number": account_number,
            "card_count": len(token_items),
            "cards": token_items,
            "usage_period_days": 30,
            "usage_summary": usage_summary,
            "usage_by_card": usage_by_card,
            "usage_error": usage_error,
            "page": {
                "number": tokens.get("number"),
                "size": tokens.get("size"),
                "total_elements": tokens.get("totalElements"),
                "total_pages": tokens.get("totalPages"),
                "empty": tokens.get("empty"),
            }
            if isinstance(tokens, dict)
            else None,
            "pending_assignments": pending_assignments,
            "privileges": privileges,
        }

    async def async_get_mycharge_overview(self) -> dict[str, Any]:
        tokens = self.mycharge_auth.get("tokens", {})
        if tokens.get("refresh_token") and self.mycharge_tokens_need_refresh(tokens):
            try:
                await self.async_refresh_mycharge_tokens()
            except InChargeApiError as err:
                _LOGGER.warning("My InCharge token refresh failed: %s", err)
                profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
                    self.mycharge_auth.get("tokens", {})
                )
                return {
                    "connected": False,
                    "status": "Re-authentication required",
                    "profile": profile,
                    "error": (
                        "My InCharge token refresh failed. Reconnect the account from "
                        "Configure > Add or update My InCharge account."
                    ),
                    "token_refresh_error": str(err),
                    "reauth_required": True,
                    "last_checked": datetime.now(UTC).isoformat(),
                    "token_seconds_left": self.mycharge_token_seconds_left(
                        self.mycharge_auth.get("tokens", {})
                    ),
                }
        profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
            self.mycharge_auth.get("tokens", {})
        )
        try:
            hierarchy = await self.async_get_mycharge_account_hierarchy()
        except InChargeApiError as err:
            token_seconds_left = self.mycharge_token_seconds_left(
                self.mycharge_auth.get("tokens", {})
            )
            reauth_required = token_seconds_left is not None and token_seconds_left <= 0
            if "401" in str(err):
                reauth_required = True
            return {
                "connected": False,
                "status": "Re-authentication required" if reauth_required else "Error",
                "profile": profile,
                "error": str(err),
                "reauth_required": reauth_required,
                "last_checked": datetime.now(UTC).isoformat(),
                "token_seconds_left": token_seconds_left,
            }
        charging_history = None
        charging_history_error = None
        try:
            charging_history = await self.async_get_mycharge_charging_history_summary()
        except InChargeApiError as err:
            charging_history_error = str(err)
        cards = None
        cards_error = None
        try:
            cards = await self.async_get_mycharge_cards_overview()
        except InChargeApiError as err:
            cards_error = str(err)
        dashboard = None
        dashboard_error = None
        try:
            dashboard = await self.async_get_mycharge_dashboard_widgets()
            if dashboard and dashboard.get("errors"):
                dashboard_error = "; ".join(
                    f"{key}: {value}" for key, value in dashboard["errors"].items()
                )
        except InChargeApiError as err:
            dashboard_error = str(err)
        return {
            "connected": True,
            "status": "Connected",
            "profile": profile,
            "account_hierarchy": hierarchy,
            "charging_history": charging_history,
            "charging_history_error": charging_history_error,
            "cards": cards,
            "cards_error": cards_error,
            "dashboard": dashboard,
            "dashboard_error": dashboard_error,
            "dashboard_errors": (dashboard or {}).get("errors"),
            "last_checked": datetime.now(UTC).isoformat(),
            "token_seconds_left": self.mycharge_token_seconds_left(
                self.mycharge_auth.get("tokens", {})
            ),
            "last_token_refresh": self.mycharge_auth.get("last_token_refresh"),
        }

    async def async_bootstrap_device(
        self,
        *,
        push_token: str | None = None,
    ) -> tuple[str, str]:
        self.device_id = str(uuid.uuid4())
        payload = {
            "brand": "nuon",
            "deviceId": self.device_id,
            "language": "EN",
            "locale": "en_US",
            "osVersion": "37",
            "pushToken": push_token or f"home-assistant-{uuid.uuid4()}",
            "userAgent": "android",
            "versionCode": 40505180,
            "versionName": "4.8.7",
        }
        response = await self._request(
            "PUT",
            "device",
            include_token=False,
            json_body=payload,
        )
        self.x_token = response["xToken"]
        return self.device_id, self.x_token

    async def async_search_charging_points(
        self,
        search_term: str,
        *,
        latitude: float = 37.4219983,
        longitude: float = -122.084,
        page_number: int = 0,
        page_size: int = 40,
    ) -> list[dict[str, Any]]:
        tried: set[str] = set()
        for candidate in self._search_variants(search_term):
            normalized = candidate.strip()
            if not normalized or normalized in tried:
                continue
            tried.add(normalized)
            payload = {
                "coordinates": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "pagination": {
                    "pageNumber": page_number,
                    "pageSize": page_size,
                },
                "search": normalized,
            }
            results = await self._request(
                "POST",
                "api/charging-points/charging_point/search",
                json_body=payload,
                retry_unauthorized=True,
            )
            if results:
                if normalized != search_term:
                    _LOGGER.debug(
                        "InCharge search fallback matched '%s' using '%s'",
                        search_term,
                        normalized,
                    )
                return results
        return []

    @staticmethod
    def _search_variants(search_term: str) -> list[str]:
        value = search_term.strip()
        variants: list[str] = [value]

        if "*" in value:
            parts = [part for part in value.split("*") if part]
            if len(parts) >= 2:
                variants.append(parts[-1])
            if len(parts) >= 3:
                variants.append("*".join(parts[-2:]))
            if len(parts) >= 4:
                variants.append(parts[-2])

        digit_groups = re.findall(r"\d{4,}", value)
        for group in digit_groups:
            variants.append(group)

        return variants

    async def async_get_neighbours(
        self, charging_point_uuid: str
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            f"api/charging-points/charging_point/{charging_point_uuid}/neighbours",
            retry_unauthorized=True,
        )

    async def async_get_charging_points(
        self, uuids: list[str]
    ) -> list[dict[str, Any]]:
        payload = {"uuids": [item.upper() for item in uuids]}
        return await self._request(
            "POST",
            "api/charging-points/charging_points",
            json_body=payload,
            retry_unauthorized=True,
        )

    @staticmethod
    def _score_search_result(
        result: dict[str, Any],
        search_term: str,
    ) -> tuple[int, str, str]:
        """Prefer the most exact search result."""
        needle = search_term.strip().casefold()
        display_name = str(result.get("displayName") or "").casefold()
        visual_id = str(result.get("visualId") or "").casefold()
        evse_id = str(result.get("evseId") or "").casefold()
        station_name = str(result.get("stationName") or "").casefold()

        if visual_id == needle:
            rank = 0
        elif display_name == needle:
            rank = 1
        elif evse_id == needle:
            rank = 2
        elif station_name == needle:
            rank = 3
        elif visual_id.startswith(needle):
            rank = 4
        elif display_name.startswith(needle):
            rank = 5
        elif needle in evse_id:
            rank = 6
        elif needle in visual_id:
            rank = 7
        elif needle in display_name:
            rank = 8
        elif needle in station_name:
            rank = 9
        else:
            rank = 10

        return (
            rank,
            str(result.get("stationUuid") or ""),
            str(result.get("uuid") or ""),
        )

    def _choose_best_search_result(
        self,
        search_results: list[dict[str, Any]],
        search_term: str,
    ) -> dict[str, Any] | None:
        if not search_results:
            return None
        ordered = sorted(
            search_results,
            key=lambda item: self._score_search_result(item, search_term),
        )
        return ordered[0]

    async def async_collect_station_points(
        self, search_term: str
    ) -> list[dict[str, Any]]:
        search_results = await self.async_search_charging_points(search_term)
        if not search_results:
            return []

        anchor = self._choose_best_search_result(search_results, search_term)
        if anchor is None:
            return []

        seen: set[str] = set()
        all_points: list[dict[str, Any]] = []

        for point in await self.async_get_neighbours(anchor["uuid"]):
            point_uuid = str(point["uuid"]).lower()
            if point_uuid in seen:
                continue
            seen.add(point_uuid)
            all_points.append(point)

        return all_points
