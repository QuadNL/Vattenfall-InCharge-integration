"""Public InCharge API client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientError

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    APP_ACCEPT,
    DEFAULT_PUSH_TOKEN,
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
        cls, tokens: dict[str, Any], *, refresh_window: int = 600
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

    async def async_refresh_mycharge_tokens(self) -> dict[str, Any]:
        refresh_token = self.mycharge_auth.get("tokens", {}).get("refresh_token")
        if not refresh_token:
            raise InChargeApiError("No MyCharge refresh_token available.")
        payload = {
            "grant_type": "refresh_token",
            "client_id": MYCHARGE_CLIENT_ID,
            "refresh_token": refresh_token,
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
        }
        return self.mycharge_auth

    async def async_get_mycharge_account_hierarchy(self) -> Any:
        id_token = str(self.mycharge_auth.get("tokens", {}).get("id_token", ""))
        if not id_token:
            raise InChargeApiError("No MyCharge id_token available.")
        return await self._portal_request(
            "GET",
            "/account-management/accounts/hierarchy",
            bearer_token=id_token,
        )

    async def async_get_mycharge_overview(self) -> dict[str, Any]:
        tokens = self.mycharge_auth.get("tokens", {})
        if tokens.get("refresh_token") and self.mycharge_tokens_need_refresh(tokens):
            try:
                await self.async_refresh_mycharge_tokens()
            except InChargeApiError:
                _LOGGER.debug("MyCharge token refresh failed; falling back to stored tokens")
        profile = self.mycharge_auth.get("profile") or self.build_mycharge_profile(
            self.mycharge_auth.get("tokens", {})
        )
        try:
            hierarchy = await self.async_get_mycharge_account_hierarchy()
        except InChargeApiError as err:
            return {
                "connected": False,
                "status": "Error",
                "profile": profile,
                "error": str(err),
                "last_checked": datetime.now(UTC).isoformat(),
                "token_seconds_left": self.mycharge_token_seconds_left(
                    self.mycharge_auth.get("tokens", {})
                ),
            }
        return {
            "connected": True,
            "status": "Connected",
            "profile": profile,
            "account_hierarchy": hierarchy,
            "last_checked": datetime.now(UTC).isoformat(),
            "token_seconds_left": self.mycharge_token_seconds_left(
                self.mycharge_auth.get("tokens", {})
            ),
        }

    async def async_bootstrap_device(
        self,
        *,
        push_token: str = DEFAULT_PUSH_TOKEN,
    ) -> tuple[str, str]:
        self.device_id = str(uuid.uuid4())
        payload = {
            "brand": "nuon",
            "deviceId": self.device_id,
            "language": "EN",
            "locale": "en_US",
            "osVersion": "37",
            "pushToken": push_token,
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
