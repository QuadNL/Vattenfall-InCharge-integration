# Vattenfall InCharge

Home Assistant custom integration for Vattenfall InCharge public charging stations and My InCharge account data.

## Features

- Add public charging stations by charging point name, for example `AB1234` or `XY6789`
- Poll charging point status, connector details, pricing, location and availability
- Add and remove configured charging stations from the integration settings
- Connect a My InCharge account with the Vattenfall login and OTP flow
- Expose basic My InCharge account status and account hierarchy data
- Expose My InCharge charging energy and charging time totals for the last 30 days
- Expose My InCharge dashboard widgets such as average kWh per session and charging costs
- Expose My InCharge charging-history counts for validated, in-review and cancelled sessions
- Expose My InCharge charging-card counts, pending assignments and per-card sensors when cards are present

Vattenfall InCharge stations are fully supported. Other charging networks exposed through the same app API are best effort and may return less consistent names or grouping.

## Installation

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

The My InCharge account flow uses the browser-based Vattenfall login and OTP flow.

After login, Home Assistant stores the returned tokens in the config entry storage. The integration refreshes tokens when they are close to expiry and writes refreshed tokens back to Home Assistant storage.

Current My InCharge entities:

- `My InCharge status`
- `My InCharge account`
- `My InCharge charging energy last 30 days`
- `My InCharge charging time last 30 days`
- `My InCharge average consumption per session last 7 days`
- `My InCharge charging costs current month`
- `My InCharge charging costs last month`
- `My InCharge charging costs this year`
- `My InCharge validated sessions last 30 days`
- `My InCharge sessions in review last 30 days`
- `My InCharge cancelled sessions last 30 days`
- `My InCharge charging cards`
- one `My InCharge card ...` sensor per returned charging card

Future My InCharge features may include report download support.

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
