# Deploying Vezubunye

Same pattern as RIPS: backend on Railway, frontend on Vercel, MongoDB Atlas.

## 1. MongoDB Atlas

1. Create a database `vezubunye` in the existing Atlas cluster (or a new free cluster).
2. Create a database user; allow network access from anywhere (Railway IPs rotate).
3. Copy the connection string for `MONGO_URL`.

## 2. Backend → Railway

1. New Railway project → Deploy from GitHub repo → select `vezubunye`, root directory `backend/`.
   Railway picks up `backend/Dockerfile` via `railway.json`.
2. Variables:
   - `MONGO_URL` — Atlas connection string
   - `DB_NAME` — `vezubunye`
   - `JWT_SECRET` — long random string (`openssl rand -hex 32`)
   - `CORS_ORIGINS` — `https://vezubunye.com,https://www.vezubunye.com`
   - `CLOUDINARY_URL` — optional, for capture photos (falls back to MongoDB storage)
   - `WERNER_PASSWORD`, `PIERRE_PASSWORD`, `STEVEN_PASSWORD` — real passwords for seeding
3. After first deploy, run the seed once (Railway shell or locally pointed at Atlas):
   `python seed.py`
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
