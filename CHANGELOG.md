# Changelog

## v0.5.4-beta5

### Fixed

- Fixed My InCharge sessions becoming permanently unrecoverable after ~2 hours. Vattenfall stopped issuing usable `refresh_token`s to the web-portal OAuth client; My InCharge account linking now uses the mobile-app OAuth client instead, whose `refresh_token` grant keeps working when requests present mobile-app-like headers (`User-Agent`, `Apk-CRC`, `Apk-SHA1`).
- The "Add or update My InCharge account" login link now points to Vattenfall's mobile-app login flow (`nl.nuon.laadpunten://login` callback) instead of the web-portal flow.

### Changed

- Added detailed debug/info logging for My InCharge token exchange, refresh and device-session linking, so request/response activity can be traced in the Home Assistant logs.

### Known limitations

- Charging card visibility (`Charging cards` sensor) may follow whichever device most recently logged into the account (Home Assistant, the mobile app, etc.) — this is Vattenfall backend behavior, not a bug in the integration.

## v0.5.1

### Changed

- Updated the GitHub release workflow so an existing release is updated instead of failing with `Release.tag_name already exists`.
- Released the report download changes through the normal tag-based GitHub Actions release flow.

## v0.5.0

### Added

- Added My InCharge report downloads for charging history.
- Added CSV and XLSX report formats.
- Added support for preset report periods such as this month, last month, last 30 days, this year and custom date ranges.
- Added Home Assistant notifications with a direct download link when a report is ready.
- Added a 30-second per-account delay for report download requests to avoid sending repeated report-generation requests to Vattenfall.

### Changed

- Report download links now use an opaque download ID instead of exposing account numbers, dates or filenames in the URL.
- Downloaded reports are stored outside `/config/www` and served through the integration download endpoint.
