# Dashboard UI/UX Revamp

Complete overhaul of the codehome dashboard frontend (SvelteKit app at `/home/m/Work/super/dashboard`). This covers navigation, theming, component architecture, page restructuring, and code deletion.

## Context

The current dashboard has 17+ flat tabs, no visual hierarchy, cluttered pages, mobile code that doesn't work well, offline infrastructure for a localhost tool, and a one-size-fits-all design. The revamp introduces grouped navigation, multiple themes, SDUI-configurable home page, a new DataGrid primitive, and removes dead weight.

The dashboard serves three top-level concerns:
- **Conductor**: AI agent orchestration (chat, agents, inbox) -- a core CodeHome subsystem
- **Supervisor**: branch/git/service management -- the biggest plugin
- **Plugins**: all other plugins shown via a rich table

Admin/system functionality is accessed via gear icon, not a nav group.

---

## Phase 1: Navigation Architecture

### 1.1 Nav Layout Interface

Define a contract/interface that all nav layout variants implement. The interface must support:
- Rendering 3 top-level groups (Conductor, Supervisor, Plugins)
- Rendering sub-pages within the active group
- Highlighting the current page
- Integrating with the top bar (user info, gear icon, connection dot)
- Responsive behavior (collapse gracefully at narrower widths)

### 1.2 Four Nav Layout Variants

Implement four interchangeable nav layouts, all conforming to the interface:

1. **SecondRow**: Group labels in top row, active group's pages as tabs in a second row below
2. **Dropdown**: Group labels in top row, clicking opens a dropdown menu of pages
3. **InlineSwap**: Single row, active group's page tabs shown inline next to group labels
4. **FlatTabs**: Current behavior (all tabs in one row, no grouping) preserved as legacy option

User selects their preferred layout from Settings. Stored in preferences.

### 1.3 Top Bar Redesign

The top bar must contain:
- Left: nav layout (groups + pages per the selected variant)
- Right: connection status dot (green=connected, red=disconnected), current branch name, user name, gear icon (links to /admin)

Remove from top bar: language toggle (move to Settings).

### 1.4 Group Contents

- **Conductor group**: Chat, Agents, Inbox/Tasks (all top-level routes, not branch-scoped)
- **Supervisor group**: Branches (matrix/overview), Branch detail (git, services, terminal, tests, pipeline, review), Compare
- **Plugins group**: single page showing a DataGrid table of all installed plugins

### 1.5 Conductor Route Migration

Move conductor pages from `/branch/:repo/:branch/chat|agents|inbox` to top-level routes `/conductor/chat`, `/conductor/agents`, `/conductor/inbox`. Branch context is selected inside the conductor UI (dropdown or picker within the page), not via URL structure.

### 1.6 Admin Page

New `/admin` route (gear icon in top bar) with sub-tabs:
- Health (system status, diagnostics, metrics)
- Errors (filterable error log, currently /errors)
- Users (user management, currently in /system)
- Settings (appearance, features, connections -- currently /settings)
- Docs (documentation viewer, currently /docs)

Remove standalone /settings, /system, /errors, /docs pages. Redirect old URLs to /admin sub-tabs.

---

## Phase 2: Theming System

### 2.1 Three Visual Themes

Build three complete themes, each with its own accent color, spacing, border style, and typographic hierarchy:

1. **Minimal** (Linear-inspired): generous whitespace, subtle borders, muted accent (e.g., soft indigo), smooth transitions, content-focused, almost no chrome
2. **Dense** (Vercel-inspired): compact spacing, monospace accents, high-contrast dark mode, technical feel, information-rich
3. **Familiar** (GitHub-inspired): traditional spacing, clear headers/dividers, standard component patterns, readable at any density

Each theme bundles its own accent color (no separate accent picker). Theme selection in Settings (under /admin).

### 2.2 Theme Implementation

- Each theme is a complete CSS variable set (colors, spacing, radii, fonts, shadows, transitions)
- Themes stored as named objects; switching applies the full variable set to `:root`
- Dark/light mode remains orthogonal to theme choice (each theme defines both dark and light variants)
- Remove the 10-color accent swatch picker and the metrics visualization picker from settings

### 2.3 Design Tokens

Tighten the token system:
- Fewer font sizes with clearer hierarchy (title, heading, body, caption, mono)
- Consistent spacing scale (4px base)
- Border radius tied to theme (minimal=larger radii, dense=smaller, familiar=medium)
- Shadow levels (none, sm, md) instead of ad-hoc box-shadows

---

## Phase 3: Home Page (SDUI-Configurable)

### 3.1 Home Page as SDUI Layout

The home page (`/`) renders an SDUI tree fetched from the server. The server provides multiple home page layout presets the user can shuffle between.

### 3.2 Home Page Presets

Build at least 3 initial presets:
- **Activity**: recent events (commits, agent outputs, service changes, failing checks), quick-launch buttons
- **Status Board**: active branch status, services health grid, CI status, open PRs
- **Compact**: condensed single-column with key metrics and links

### 3.3 Preset Shuffling

User can cycle through presets via a control on the home page itself (e.g., a prev/next or dropdown). Selection persists in preferences.

### 3.4 Contextual Empty States

When any section of the home page (or any other page) has no data, show a contextual hint explaining what the feature does and how to set it up. Replace blank/empty renders throughout the app.

---

## Phase 4: DataGrid Primitive

### 4.1 New SDUI Primitive

Add `DataGrid` as a new SDUI primitive alongside the existing `Table`. The existing Table remains for simple static data.

### 4.2 DataGrid Features

- Sortable columns (click header to sort asc/desc)
- Filterable (per-column filters or global search)
- Column reorder via drag-and-drop (edit mode)
- Column show/hide toggle (edit mode)
- Pagination or virtual scroll for large datasets
- Row selection (single/multi)
- Cell rendering customization (badges, status dots, links, actions)
- Persisted column configuration per-user (order, visibility, widths)

### 4.3 Extract from Matrix

The current branch matrix table implements some of these features ad-hoc. Extract and generalize into the DataGrid primitive. The matrix page then becomes a consumer of DataGrid.

### 4.4 Plugins Table

The Plugins nav group page uses DataGrid to show all installed plugins with columns:
- Name, Description, Version, Creator, Size
- Enabled status (toggle)
- Current activity (what it's doing right now)
- UI provided (dashboard route if any)
- Settings/config link
- Update available
- Install date

---

## Phase 5: Component Improvements

### 5.1 Service Cards Redesign

Replace current dense service cards with compact rows:
- Default: name + status badge + last health check time
- Click expands accordion in-place showing: logs, metrics, dependency graph, start/stop/restart controls
- No navigation away from the page

### 5.2 Plugin Page Chrome

Plugin pages (rendered via SDUI) get two modes, toggleable by the user:
- **Chrome mode** (default): plugin header with name, icon, back button. Clear boundary between native and plugin content.
- **Seamless mode**: no chrome, plugin page looks identical to native pages. Toggle via setting or per-visit control.

### 5.3 Loading States

Replace all "Loading..." text throughout the app with shimmer skeleton screens that mirror the shape of the content being loaded (different skeleton shapes for different page types).

### 5.4 Error Toasts

Redesign error toasts:
- Show friendly human-readable message (map known error codes to plain language)
- Optional "Details" expand showing the raw technical error
- Don't show raw API error strings by default

### 5.5 Enhanced Cmd+K

Expand the command palette to be a unified search across:
- Pages (navigate to any page by name)
- Branches (fuzzy match branch names, jump to branch)
- Services (find a service, see status, jump to it)
- Files (search file paths in repos)
- Docs (search documentation content)
- Actions (existing: new branch, refresh, toggle theme, push, etc.)

No additional keyboard shortcuts beyond Cmd+K.

---

## Phase 6: Deletions

### 6.1 Delete Onboarding System

Remove entirely:
- `RoleSelector.svelte` component
- `OnboardingTour.svelte` component
- Tour data/definitions (TOURS array)
- Onboarding state API calls (`/api/preferences/onboarding`)
- Onboarding logic in RootLayout (phase tracking, role storage)

Replace with: contextual hints system (phase 3.4).

### 6.2 Delete Offline Infrastructure

Remove entirely:
- `offlineQueue.svelte.ts` store (queue, replay, localStorage persistence)
- `isQueueable()` function and all references
- Offline-related logic in `api.ts` (network error queueing branch)
- `OfflineBanner.svelte` component (replaced by top bar dot + toast)
- `connectivity.svelte.ts` store (replace with a minimal SSE connection state tracker)

### 6.3 Delete Mobile Code

Remove entirely:
- `MobileNav.svelte` component
- `PullToRefresh.svelte` component
- Swipe navigation handlers in BranchLayout
- All `@media (max-width: 768px)` responsive breakpoints
- `isMobile` detection logic and conditional rendering
- Bottom bar styles and layout

### 6.4 Delete Settings Bloat

Remove:
- Metrics visualization picker (ChartJS/Sparkline/Canvas radio) -- keep one renderer (ChartJS), delete others and the setting
- Accent color swatch picker (10 colors) -- accent is now theme-bundled
- Language toggle from top bar (keep in admin settings)

### 6.5 Remove Standalone Pages

Delete as standalone routes (content moves to /admin sub-tabs):
- `/settings` route and Settings.svelte page
- `/system` route and System.svelte page
- `/errors` route and Errors.svelte page
- `/docs` route and Docs.svelte page

Keep redirects from old URLs to new /admin sub-tab equivalents during transition.

---

## Phase 7: Connection Status & Notifications

### 7.1 Top Bar Connection Dot

Persistent small dot in the top bar:
- Green: SSE connected
- Red: SSE disconnected
- No text, no animation beyond color change

### 7.2 Toast Notifications for State Changes

- On disconnect: corner toast "Server disconnected" (auto-dismiss after 5s)
- On reconnect: corner toast "Back online" (auto-dismiss after 3s)
- No layout-shifting banner. No persistent text.

### 7.3 Minimal Connectivity Store

Replace the full connectivity store with a minimal reactive boolean (`sseConnected`) derived from the SSE store's connection state. No browser online/offline event tracking, no ping polling.

---

## Phase 8: Auth Improvements

### 8.1 Token Expiry

Change JWT expiry from 24 hours to 30 days. Update `authenticate_user()` and any token generation code.

### 8.2 Seamless Boot

Ensure the login page never renders (not even for one frame) when a valid token exists. The auth check must resolve before any route rendering decision is made. Current fix (Login.svelte onMount re-check) is a workaround; the proper fix is gating all rendering on auth resolution in the root layout.

---

## Phase 9: Miscellaneous

### 9.1 i18n

Keep EN/IT system as-is. Remove language toggle from top bar; accessible only from /admin Settings sub-tab. Architecture remains extensible for future languages.

### 9.2 Compare Page

Stays as a standalone page under the Supervisor group. No changes to its functionality.

### 9.3 Demo Player / Narration

Keep in codebase. No changes.

### 9.4 Feature Flag Hot-Reload

When a feature flag is toggled in admin settings, the change should take effect immediately (re-render affected routes, show/hide tabs) without a full page reload or "Restart Required" banner. Remove the restart banner.

---

## Files/Directories Affected

**Dashboard frontend** (`/home/m/Work/super/dashboard/`):
- `src/layouts/RootLayout.svelte` -- complete rewrite (nav system, top bar, remove mobile/onboarding)
- `src/layouts/BranchLayout.svelte` -- remove swipe/mobile, adjust for new nav
- `src/App.svelte` -- adjust SSE/plugin init for new conductor routing
- `src/pages/Login.svelte` -- simplify (remove redundant onMount hack)
- `src/pages/Home.svelte` -- rewrite as SDUI-configurable page
- `src/pages/Settings.svelte` -- DELETE (moves to /admin)
- `src/pages/System.svelte` -- DELETE (moves to /admin)
- `src/pages/Errors.svelte` -- DELETE (moves to /admin)
- `src/pages/Docs.svelte` -- DELETE (moves to /admin)
- `src/pages/Admin.svelte` -- NEW (consolidated admin with sub-tabs)
- `src/pages/PluginsTable.svelte` -- NEW (DataGrid-based plugin browser)
- `src/lib/router/routes.ts` -- restructure for new groups, conductor top-level, admin
- `src/lib/stores/auth.svelte.ts` -- no major changes
- `src/lib/stores/connectivity.svelte.ts` -- simplify to minimal boolean
- `src/lib/stores/offlineQueue.svelte.ts` -- DELETE
- `src/lib/stores/preferences.svelte.ts` -- add nav layout preference, home preset
- `src/lib/components/MobileNav.svelte` -- DELETE
- `src/lib/components/PullToRefresh.svelte` -- DELETE
- `src/lib/components/OfflineBanner.svelte` -- DELETE
- `src/lib/components/OnboardingTour.svelte` -- DELETE
- `src/lib/components/RoleSelector.svelte` -- DELETE
- `src/lib/components/UpdateBanner.svelte` -- redesign (non-disruptive)
- `src/lib/components/CommandPalette.svelte` -- expand to unified search
- `src/lib/components/DataGrid.svelte` -- NEW
- `src/lib/components/NavSecondRow.svelte` -- NEW
- `src/lib/components/NavDropdown.svelte` -- NEW
- `src/lib/components/NavInlineSwap.svelte` -- NEW
- `src/lib/components/NavFlatTabs.svelte` -- NEW (extracted from current)
- `src/lib/components/ConnectionDot.svelte` -- NEW
- `src/lib/components/ContextualHint.svelte` -- NEW
- `src/lib/components/SkeletonLoader.svelte` -- NEW
- `src/lib/themes/` -- NEW directory (minimal.ts, dense.ts, familiar.ts)
- `src/lib/nav/` -- NEW directory (interface + 4 implementations)
- `src/lib/api.ts` -- remove offline queue integration
- `DESIGN_TOKENS.md` -- rewrite for new theme system

**Backend** (`/home/m/Work/super/src/codehome/`):
- `serve/routers/auth.py` -- 30-day token expiry
- `serve/routers/conductor.py` -- no route changes needed (already top-level API)
- `serve/sdui/primitives.py` -- add DataGrid primitive schema
- `serve/routers/plugins.py` -- ensure plugin table data (columns) is served
- `serve/routers/home.py` -- NEW (serve SDUI home page presets)
- `features.py` -- hot-reload support (SSE event on flag change)

**CodeHome core** (`/home/m/Projects/codehome/`):
- `src/codehome/serve/sdui/primitives.py` -- add DataGrid primitive
- `src/codehome/serve/auth_deps.py` -- already has token file fallback (done)
- `src/codehome/serve/routers/auth.py` -- 30-day expiry, cookie bootstrap (partially done)
- `src/codehome/commands/setup.py` -- non-interactive flags (done)

---

## Effort Estimate

| Phase | Scope | Relative Effort |
|-------|-------|-----------------|
| 1. Navigation | Architecture, 4 variants, routing | High |
| 2. Theming | 3 themes, token system, dark/light per theme | High |
| 3. Home Page | SDUI presets, shuffling, empty states | Medium |
| 4. DataGrid | New primitive, extraction, plugins table | High |
| 5. Components | Service cards, loading, errors, Cmd+K, plugin chrome | Medium |
| 6. Deletions | Remove onboarding, offline, mobile, settings bloat | Low |
| 7. Connection | Dot, toasts, minimal store | Low |
| 8. Auth | Token expiry, seamless boot | Low (partially done) |
| 9. Misc | i18n, feature hot-reload, compare | Low |

Recommended order: 6 (deletions first to reduce noise) -> 1 (navigation) -> 2 (theming) -> 4 (DataGrid) -> 3 (home) -> 5 (components) -> 7 (connection) -> 8 (auth) -> 9 (misc).
