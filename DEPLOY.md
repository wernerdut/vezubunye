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

Custom domain `api.vezubunye.com` is added to the service (same `api.<domain>`
pattern as RIPS `api.risingpowder.co.za`). It needs the `api` CNAME below. The
`.up.railway.app` URL keeps working too.

## 3. Frontend → Vercel — DONE

Deployed and live: **https://vezubunye.vercel.app**

- Vercel project `vezubunye` (Vite preset), team "Werner's projects".
- `VITE_API_URL` (Production) = `https://api.vezubunye.com` — baked into the bundle.
  (Until the `api` CNAME is added, the deployed site can reach the API only after
  DNS lands; the backend itself is already up on the `.up.railway.app` URL.)
- Custom domains `vezubunye.com` and `www.vezubunye.com` attached to the project.
- Verified: site is public and serves the app.

## 4. vezubunye.com DNS — the only step left

The domain's nameservers are at clusterdns.co.za. Add all three records there:

| Type | Name | Value |
|---|---|---|
| A | @ (vezubunye.com) | `76.76.21.21` |
| A | www | `76.76.21.21` |
| CNAME | api | `ngbduovf.up.railway.app` |

Vercel (site) and Railway (api) both issue TLS automatically once the records
propagate (minutes to a few hours) and email on completion. When all three are in,
the whole thing is live on vezubunye.com. (CNAME `www → cname.vercel-dns.com` also
works in place of the www A record.)

## 5. Smoke test

1. https://vezubunye-api-production.up.railway.app/api/health → `{"status":"ok"}`
   (after DNS: https://api.vezubunye.com/api/health).
2. Log in at https://vezubunye.com (or https://vezubunye.vercel.app once the `api`
   CNAME is live) as each role — logins in `backend/CREDENTIALS_LOCAL.txt`.
3. Download the blank Daily Capture Sheet PDF.
4. Key a test capture with an intentional 1 kg powder gap → confirm the flag raises, then resolve it with a note.
5. Delete the test data from Atlas or keep it as the audit trail of go-live testing.
