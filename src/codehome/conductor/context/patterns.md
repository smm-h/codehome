# Coding Standards (Summary)

These are the project's coding standards. Ensure sub-agents follow them.

## TypeScript
- Strict mode. No `any`. No `@ts-ignore`.
- Named exports only -- no default exports.
- Use `@/` path alias for all imports. Never use relative imports (`../`).

## Components
- Under 200 lines. Split if larger.
- No inline event handlers passed to memoized children.
- No module-level mutable state outside React components -- use React Query instead.

## Data & State
- Server state: React Query (TanStack v5). No Zustand, Redux, or SWR.
- Local state: useState + Context API. No global state libraries.
- Never store PII in localStorage/sessionStorage. Use React Query for caching.

## Styling & UI
- Tailwind CSS only. No CSS modules, styled-components, or inline styles.
- Use shadcn/ui (Radix UI) primitives before building custom components.
- Forms: React Hook Form + Zod.

## Backend
- Edge Functions in Deno/TypeScript. No Express, no separate API server.
- RPC pattern: `supabase.rpc()` returns a `PostgrestBuilder` (lazy, `PromiseLike`). No `.catch()` or `.finally()` -- use `.then()` only. Must `await` or `.then()` to trigger the request.

## Database
- Never modify the DB directly or via Supabase Studio.
- Migrations are add-only: never modify existing migration files.
- Apply via `make migrate`.
- Supabase CLI version is pinned: always use `npx supabase@2.67.0`.

## Build Order
Bottom-up: migration -> Edge Function -> React component -> custom hook.

## Git
- Only commit changed files. No unrelated changes bundled.
- Format files after modifying them.
- Never commit local-only patches (`CLAUDE.md` with `--skip-worktree`).
