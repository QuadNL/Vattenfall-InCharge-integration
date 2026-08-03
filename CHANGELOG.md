# Changelog

## v0.5.4-beta11

### Fixed

- Fixed `download_my_incharge_report` matching a stale, already-consumed FILE notification (returning HTTP 204 forever) instead of the fresh one for the current request, when multiple near-identical "sessions.csv" notifications with the same name/date labels exist. The notification lookup now only considers notifications created after the report was requested.

## v0.5.4-beta10

### Fixed

- Reverted the v0.5.4-beta9 change that re-polled the report export endpoint directly. Confirmed by testing that each `GET` on that endpoint starts a **new** report-generation job on Vattenfall's side rather than checking an existing one, which caused duplicate report-ready notifications. Back to the notification-based polling from beta8 (single export request, then polling `/live-notifications-v2/api/notifications` for readiness), which does not have this side effect.

### Known issue

- `download_my_incharge_report` can still time out ("Report file was not ready before the timeout") even though the report is generated almost instantly and visible in the My InCharge portal. The root cause (the notification-based readiness check not reliably matching) is still being investigated; a real fix needs a safe way to detect readiness without re-triggering report generation.

## v0.5.4-beta8

### Fixed

- Fixed `download_my_incharge_report` failing with "Report file was not ready before the timeout" for reports that take longer than 30 seconds for Vattenfall to generate. The polling window is now up to 120 seconds, with debug logging per attempt.

## v0.5.4-beta7

### Changed

- Clarified the "Add or update My InCharge account" instructions: the mobile-app login callback (`nl.nuon.laadpunten://login`) does not visibly navigate in a desktop browser, so the help text now explains using the browser history (Ctrl+H) to find and copy the callback URL, and warns that it expires quickly.

## v0.5.4-beta6

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
