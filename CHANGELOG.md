# Changelog

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
