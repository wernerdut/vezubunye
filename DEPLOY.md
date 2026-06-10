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

## 2. Backend → Railway

1. New Railway project → Deploy from GitHub repo → select `vezubunye`, root directory `backend/`.
   Railway picks up `backend/Dockerfile` via `railway.json`.
2. Variables (copy `MONGO_URL` and `JWT_SECRET` from `backend/.env`):
   - `MONGO_URL` — the Atlas connection string from `backend/.env`
   - `DB_NAME` — `vezubunye`
   - `JWT_SECRET` — reuse the one in `backend/.env` (or set a new one; existing sessions reset)
   - `CORS_ORIGINS` — `https://vezubunye.com,https://www.vezubunye.com`
   - `CLOUDINARY_URL` — optional, for capture photos (falls back to MongoDB storage)
3. The database is already seeded, so no seed step is needed on deploy. `seed.py`
   is idempotent if you ever do re-run it (it skips anything that already exists,
   so it will not overwrite the existing passwords).
4. Custom domain: in Railway → Settings → Domains, add `api.vezubunye.com`.

## 3. Frontend → Vercel

1. New Vercel project → import the `vezubunye` repo, root directory `frontend/`.
   Framework preset: Vite. Build `npm run build`, output `dist`.
2. Environment variable:
   - `VITE_API_URL` — `https://api.vezubunye.com`
3. Domains: add `vezubunye.com` and `www.vezubunye.com` to the project.

## 4. vezubunye.com DNS

At the domain registrar, add:

| Type | Name | Value |
|---|---|---|
| A | @ | `76.76.21.21` (Vercel) |
| CNAME | www | `cname.vercel-dns.com` |
| CNAME | api | the `*.up.railway.app` target Railway shows for the custom domain |

Vercel and Railway both issue TLS automatically once DNS propagates.

## 5. Smoke test after deploy

1. `https://api.vezubunye.com/api/health` → `{"status":"ok"}`
2. Log in at `https://vezubunye.com` as each role.
3. Download the blank Daily Capture Sheet PDF.
4. Key a test capture with an intentional 1 kg powder gap → confirm the flag raises, then resolve it with a note.
5. Delete the test data from Atlas or keep it as the audit trail of go-live testing.
