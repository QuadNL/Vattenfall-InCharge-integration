# Vattenfall InCharge

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/QuadNL/Vattenfall-InCharge-integration.svg)](https://github.com/QuadNL/Vattenfall-InCharge-integration/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Home Assistant custom integration for Vattenfall InCharge public charging stations and My InCharge account data.

## Features

- Add public charging stations by charging point name, for example `AB1234` or `XY6789`
- Poll charging point status, connector details, pricing, location and availability
- Add and remove configured charging stations from the integration settings
- Connect a My InCharge account with the Vattenfall login and OTP flow
- Expose basic My InCharge account status and account hierarchy data
- Expose My InCharge charging energy and charging time totals
- Expose My InCharge dashboard widgets such as average kWh per session and charging costs
- Expose My InCharge charging-history counts for validated and cancelled sessions
- Expose My InCharge charging-card counts, pending assignments and per-card sensors when cards are present

Vattenfall InCharge stations are fully supported. Other charging networks exposed through the same app API are best effort and may return less consistent names or grouping.

## Installation

Add this repository to your HACS with the following button:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=QuadNL&repository=Vattenfall-InCharge-integration&category=integration)

Install this integration with the following button:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=vattenfall_incharge)

### HACS custom repository

1. Open HACS.
2. Add this repository as a custom repository.
3. Select category `Integration`.
4. Install `Vattenfall InCharge`.
5. Restart Home Assistant.

### Manual installation

Copy this folder into your Home Assistant config directory:

```text
custom_components/vattenfall_incharge
```

Then restart Home Assistant.

## Setup

1. Go to `Settings` -> `Devices & services`.
2. Select `Add integration`.
3. Search for `Vattenfall InCharge`.
4. Complete the setup flow.
5. Add your first charging point by entering the visible charging point name from the Vattenfall InCharge app.

The public charging station flow does not require a Vattenfall account login. The integration creates a local anonymous InCharge device session and stores the device credentials in Home Assistant config entry storage.

## Configuration

Open the integration settings to manage:

- `Add charging point`
- `Remove charging point`
- `Add or update My InCharge account`

The integration polls public charging stations every 5 minutes. My InCharge account data is refreshed every 15 minutes, or sooner when a token refresh is needed.

## My InCharge account

The My InCharge account flow uses the browser-based Vattenfall login and OTP flow, followed by the mobile-app OAuth callback (Vattenfall stopped issuing usable refresh tokens to the web-portal OAuth client, so the integration links accounts through the same client the official mobile app uses).

Because the final step redirects to a `nl.nuon.laadpunten://login` link that desktop browsers cannot open, the setup flow asks you to copy that link manually:

1. Use a private/incognito browser window and open DevTools (F12) with the Network tab open before you start.
2. Complete the Vattenfall login and OTP steps as shown in the Home Assistant setup flow.
3. Right after entering the OTP code, filter the Network tab on `authorize?` and open the last matching request.
4. Copy the `location` response header value — it starts with `nl.nuon.laadpunten://login?code=...`.
5. Paste that whole link into the Home Assistant setup flow. This link expires quickly (well under a minute), so copy and paste it right away.

After login, Home Assistant stores the returned tokens in the config entry storage. The integration refreshes tokens automatically when they are close to expiry and writes refreshed tokens back to Home Assistant storage. Use the `vattenfall_incharge.refresh_my_incharge_tokens` service to manually trigger a refresh for testing.

Current My InCharge entities:

- `My InCharge status`
- `My InCharge account`
- `Charging energy this month`
- `Charging energy this year`
- `Charging time this month`
- `Average consumption per session last 7 days`
- `Charging costs this month`
- `Charging costs last month`
- `Charging costs this year`
- `Validated sessions this month`
- `Cancelled sessions this month`
- `Charging cards`
- one `My InCharge card ...` sensor per returned charging card

## Entities

Each configured charging point exposes a status sensor with useful attributes, including:

- status
- station and charging point identifiers
- EVSE ID
- address and coordinates
- connector type and max power
- price per kWh
- opening hours
- remote payment method support

## Development notes

The integration domain is currently:

```text
vattenfall_incharge
```

The visible integration name is:

```text
Vattenfall InCharge
```

This domain is intentionally broad enough for both public charging stations and My InCharge account features.
