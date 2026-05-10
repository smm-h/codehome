# Changelog

## 0.1.2

Internal improvements.

## 0.1.1

### Added

- Dashboard shell with auth, navigation, plugin mounting, SDUI, themes, and i18n
- Notification dispatcher with channel protocol and registry
- Background task registry for plugin-contributed recurring tasks
- Generic server ops module (codehome.serve.system)

### Changed

- Renamed .supervisor/ state dir to .codehome/
- Extended JWT expiry from 24h to 30 days
- Auto-set session cookie when authenticated via CLI token file
- Paths module walks up from CWD to find project root after repo split

### Fixed

- SDK raises clear error when plugin symbols accessed without plugin installed

## 0.1.0

Initial release as `superv`.

### Added

- Multi-repo worktree manager with full branch lifecycle (create, select, finalize, rename, list)
- Git operations layer (diff, changes, rebase, push, compare, history) with worktree-aware context
- Plugin system with 16 plugins: deploy, design, extensions, hubspot, incantino, inspect, linear, lisa, publisher, reveng, review, supabase, tdd, team, telemac, tests
- Dev server with Svelte 5 dashboard (Granian ASGI, HMR proxy, SSE events)
- Check system with precommit and gate groups, plus repo/branch extension hooks
- Per-branch service management for Docker, Supabase, and Vite
- Linear integration for issue tracking linked to branch lifecycle
- App Store Connect and Google Play publishing pipeline (builds, submissions, crash reporting, vitals)
- Remote Mac iOS build pipeline via SSH (telemac)
- DOM inspection and Playwright-based declarative testing
- SDUI spec tooling for validation, generation, and coverage analysis (incantino)
- Reverse engineering pipeline for converting React components to SDUI YAML specs
