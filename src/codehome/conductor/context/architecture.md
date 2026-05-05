# Project Architecture (Summary)

Veliu Bag is a luxury fashion and electronics trade-in platform. Users trade in items, receive AI-powered valuations, and items go to remarketer auctions.

## System Shape

5 independent React (Vite) frontends sharing a single Supabase backend. No REST API server -- all server-side logic lives in Deno Edge Functions invoked via `supabase.functions.invoke()`.

## Frontends

| App | Purpose | Auth |
|-----|---------|------|
| bag.veliu.com | End-user trade-in platform | Phone OTP (Twilio) |
| orders.bag.veliu.com | Internal order/customer management | Email/password (admin) |
| backoffice.veliu.com | Drops marketplace admin | Email/password (domain whitelist) |
| drops.veliu.com | Consumer timed-drop marketplace | Phone OTP |
| veliu-mobile | Capacitor-wrapped PWA (iOS/Android) | Phone OTP |

Each frontend is a standalone Vite project with its own `package.json`. They share no code -- only the Supabase backend.

## Backend

- **Database**: PostgreSQL via Supabase with RLS on all tables.
- **Edge Functions**: Deno/TypeScript, invoked via `supabase.functions.invoke()`. Categories include: trade-in processing, valuation, auction management, notifications, payments, chat.
- **Chat backend**: Separate Python repo (veliu-chatbot). SSE streaming, card-based UI. Frontend components in `bag.veliu.com/src/components/chat/`.

## Trade-In Flow (Summary)

1. User submits item (photos + details)
2. AI valuation generates price estimate
3. User accepts or declines
4. If accepted: shipping label generated
5. Item received and inspected
6. Final valuation (may differ from estimate)
7. User confirms or disputes
8. Item listed in remarketer auction
9. Auction completes
10. User paid out
11. Counteroffers possible at steps 3 and 7

## Key Infrastructure

- CI runs `tsc --noEmit` + `vite build` on all apps per push.
- Routing: React Router v6.
- Mobile: Capacitor wrapping the bag.veliu.com PWA.
