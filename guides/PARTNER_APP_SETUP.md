# Partner app: setup, onboarding and decisions (Brief 2)

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Backend (one service) | Railway `vezubunye-api` | `/api/*` internal, `/api/p/*` partner, `/api/d/*` driver |
| Internal app | Vercel `vezubunye` (`frontend/`) | vezubunye.com |
| Partner app | Vercel `vezubunye-partner` (`partner/`) | one subdomain per node, e.g. makx.vezubunye.com |

The partner app calls the API on its own origin: `partner/vercel.json` rewrites `/api/*` to
`https://api.vezubunye.com/api/*`. Share links, pay links and driver links therefore all live
on the node's own address.

## New environment variables (Railway)

| Variable | Purpose |
|---|---|
| `PARTNER_JWT_SECRET` | Signs partner tokens, driver links, share links and the Xero OAuth state. Must differ from `JWT_SECRET`. |
| `SECRETS_ENC_KEY` | Encrypts Yoco keys, Yoco webhook secrets and Xero tokens at rest. Any long random string. Changing it strands stored secrets. |
| `PUBLIC_API_URL` | `https://api.vezubunye.com`. Yoco webhooks are registered against it. |
| `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET` | The new Vezubunye Xero app. |
| `XERO_REDIRECT_URI` | `https://api.vezubunye.com/api/p/xero/callback` |
| `XERO_SCOPES` | Optional override. Default: `openid profile email offline_access accounting.settings.read accounting.contacts accounting.invoices accounting.payments accounting.attachments`. Confirm the granular names when creating the Xero app. |
| `ANTHROPIC_API_KEY` | Bill document reading. Optional: bills capture by hand without it. |
| `BILLS_EXTRACT_MODEL` | Optional. Defaults to `claude-opus-5-5`. |
| `PARTNER_ORIGIN_REGEX` | Optional CORS override. Default `https://[a-z0-9-]+\.vezubunye\.com`. |
| `NIGHTLY_HOUR_SAST` / `DISABLE_SCHEDULER` | Nightly sweeps run in-process at 02:00 SAST. |

## Adding a node's partner app (Makulu: done in code, DNS and Vercel still to do)

1. Internal app, Admin, Partner apps: set the address (slug), document prefix, company details,
   commercial model, colour stock. Create the first partner admin login.
2. Vercel, project `vezubunye-partner`: add the domain `<slug>.vezubunye.com`.
3. clusterdns.co.za: add `CNAME <slug> -> cname.vercel-dns.com`. No wildcard: vezubunye.com
   is not on Vercel nameservers.
4. The partner admin signs in, checks Settings (products, colours, prices, charges, drivers),
   connects Yoco and Xero, and adds their staff.
5. When the node captures in its own app, tick "Partner app live": the internal capture and
   new-delivery screens go read-only for that node. Admin corrections keep working.

## Makulu onboarding status

Seeded on first boot (`migrate_brief2.py`): node `makulu`, slug `makx`, prefix `MX`,
`powder_sale`, colour stock on, four tank types, ten colours, retail and trade price lists
effective 2026-10-01, delivery charges, drivers Jimmy and Tapiwa L, company and bank details,
brand assets.

Still open (needs Werner):
- Burnt Orange and Bronze Olive have no RIPS powder product. They are listed as on request and
  cannot be captured until Fenix adds a product (or they are dropped).
- First users, Yoco secret key, terms customers.
- Brand files: the logos in `backend/assets/brands/makx/` are SVG traces of the supplied logo
  images (the supplied files could not be attached to the repo). `logo_dark.svg` (gradient
  icon, white wordmark) stands in for the white-on-dark variant still to come; replace both
  when the SVGs arrive (Settings, Branding in the app, or the files plus a redeploy for new
  nodes). No Adobe Fonts kit is configured, so text falls back to the system sans.
- Black grades seeded: 5840, 6840 and 2840 Black UV RotoPowder (in RIPS stock). Add others in
  Admin if Makulu draws a different grade.

## Yoco: which API and why

Checkout API (`POST https://payments.yoco.com/api/checkouts`, secret key, `payment.succeeded`
webhook, Standard Webhooks signature). Yoco does not publish how long a checkout's
`redirectUrl` stays valid, so the customer is never sent one. They get the node's own stable
link `https://<slug>.vezubunye.com/pay/<token>`; opening it creates a checkout for the
invoice's balance at that moment and redirects. Days-old links keep working and a part payment
by another method changes what the next tap charges. The Payment Links API was not used: it
uses separate scoped OAuth tokens and its expiry and webhook format are not documented.
Before go-live, confirm against developer.yoco.com with a test key: webhook registration,
the signature headers and `payload.metadata` on `payment.succeeded`.

## Migration on deploy (writes to the live database)

On startup the backend runs `migrate_brief2.run()` (idempotent):
- every Brief 1 delivery note without an order becomes a legacy order and a legacy invoice;
  distinct client names become customers on terms; matched payments become allocations;
- Makulu is seeded if absent.
The existing invoice index is rebuilt with a partial filter (legacy invoices carry no number).
Take an Atlas snapshot before the first deploy of this branch.
