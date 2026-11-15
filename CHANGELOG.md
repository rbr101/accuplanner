# Changelog

## [1.18.0] - 2026-11-15

### Added
- **MIT license.**
- **Home Assistant Marketplace ready:** GitHub Actions run the HACS/Marketplace validation, `hassfest` and the tests on every push.
- **Tests:** planner tests (prices of 3–4 November 2026) and Home Assistant tests for the config flow, entities, setting switches, reaction speed, stored prices and migration.
- README: installation via the **Marketplace** (Home Assistant 2026.11+), with HACS for older versions.

### Changed
- `frontend` is now an *after* dependency; the dashboard cards are registered whenever the frontend is loaded (as before).

### Fixed
- Discharge settings description: placeholder in quotes rejected by `hassfest`.

## [1.17.0] - 2026-11-03

### Changed
- **More accurate actual savings:** every kWh delivered by the battery is valued at the purchase price of the quarter it was delivered in (split over quarters), minus the stored energy cost.
- SOC drops while the battery holds (0 W), charges or waits for solar are standby consumption and no longer count as savings.

### Added
- *Total savings* attributes `saved_per_kwh` and `last_discharge`.

## [1.16.0] - 2026-11-03

### Added
- Purchase and sell prices are stored locally (also across restarts) and updated with every new reading. When the price sensor is unavailable the stored prices are used; the charge plan shows a ⚠️ row and the Mode sensor has `price_source: stored`.
- The last known battery capacity is used when the capacity sensor is briefly unavailable.

## [1.15.0] - 2026-11-03

### Added
- Option **Sell price based on replacement value** (default on): sell price = (value of the energy in the battery + wear cost) × (1 + profit %), per quarter. Off = based on the stored energy cost as before. Per-quarter values in `series.sell`.

### Changed
- Setting switches update the plan immediately.

## [1.14.0] - 2026-11-03

### Changed
- The discharge price is based on the replacement value only (what recharging later costs), not on what the current content cost.

### Removed
- Option *Use stored energy cost as discharge floor* (migrated away automatically).
