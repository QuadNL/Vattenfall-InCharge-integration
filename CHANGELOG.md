# Changelog

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
