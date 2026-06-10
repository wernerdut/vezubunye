# Deploying Vezubunye

Same pattern as RIPS: backend on Railway, frontend on Vercel, MongoDB Atlas.

## 1. MongoDB Atlas — DONE

The `vezubunye` database is live on the existing `werner-labs` Atlas cluster
(same cluster as RIPS `fenix_forecast`, isolated by database name). User
`wernerdut_db_user` and open network access were already provisioned. The
database was created and seeded (GoGreen node, config, three users) on
2026-06-10 with generated passwords.

The connection string and app passwords are in `backend/.env` and
`backend/CREDENTIALS_LOCAL.txt` (both gitignored — never commit them).
For Railway, reuse that same `MONGO_URL` with `DB_NAME=vezubunye`.

## 2. Backend → Railway — DONE

Deployed and live: **https://vezubunye-api-production.up.railway.app**

- Railway project `vezubunye`, service `vezubunye-api` (Dockerfile build).
- Variables set: `MONGO_URL`, `DB_NAME=vezubunye`, `JWT_SECRET`, `TOKEN_HOURS=72`,
  `CORS_ORIGINS` (the live domains + the Vercel URL). `CLOUDINARY_URL` is optional
  (capture photos fall back to MongoDB storage); add it later if wanted.
- `.dockerignore` keeps `.env`, `.venv`, tests, and credentials out of the image.
- Database already seeded — no seed step on deploy. `seed.py` is idempotent if re-run.
- Health verified: `/api/health` → `{"status":"ok"}`, live login against Atlas works.

Optional `api.vezubunye.com`: add it in the Railway dashboard (Service → Settings →
Networking → Custom Domain — the CLI can't add custom domains). Then add the CNAME it
shows, switch `VITE_API_URL` to `https://api.vezubunye.com`, and redeploy the frontend.
Not required: the site already works against the `.up.railway.app` URL above.

## 3. Frontend → Vercel — DONE

Deployed and live: **https://vezubunye.vercel.app**

- Vercel project `vezubunye` (Vite preset), team "Werner's projects".
- `VITE_API_URL` (Production) = `https://vezubunye-api-production.up.railway.app`,
  baked into the production bundle.
- Custom domains `vezubunye.com` and `www.vezubunye.com` attached to the project,
  awaiting DNS (below).
- Verified: site is public, serves the app, and a login from the live origin returns
  a token with the correct `Access-Control-Allow-Origin` header.

## 4. vezubunye.com DNS — the only step left

The domain's nameservers are at clusterdns.co.za. At that DNS provider, add:

| Type | Name | Value |
|---|---|---|
| A | @ (vezubunye.com) | `76.76.21.21` |
| A | www | `76.76.21.21` |

Vercel issues TLS automatically once these propagate (minutes to a few hours), and
Vercel verifies and emails on completion. Until then the app is fully usable at
https://vezubunye.vercel.app. (CNAME `www → cname.vercel-dns.com` also works in place
of the www A record.)

## 5. Smoke test (works now, before DNS)

1. https://vezubunye-api-production.up.railway.app/api/health → `{"status":"ok"}`
2. Log in at https://vezubunye.vercel.app as each role (logins in `backend/CREDENTIALS_LOCAL.txt`).
3. Download the blank Daily Capture Sheet PDF.
4. Key a test capture with an intentional 1 kg powder gap → confirm the flag raises, then resolve it with a note.
5. Delete the test data from Atlas or keep it as the audit trail of go-live testing.
