# Vezubunye

Internal, Fenix-controlled platform for the Vezubunye network of BEE tank-manufacturing nodes.
One live node at launch: **GoGreen Roto Moulding, Queenstown**.

Purpose: prove there is no leakage anywhere on the chain from powder received to cash in the bank,
and prove every rand owed to Fenix lands in the account.

## The chain

powder in → powder drawn → tanks produced (A / B / Reject) → finished goods → delivered (DN) → invoiced → paid → ex-works value drawn to Fenix

Hard rules (zero tolerance, no flag auto-clears):
1. Powder drawn must exactly equal tanks moulded × tank weight. Any gap flags.
2. Finished goods reconcile per tank type per grade.
3. No tank leaves without a delivery note **and** an invoice.
4. Every invoice matches a payment; unpaid/short/over flags.
5. On payment match, the Fenix ex-works / partner balance split is computed and recorded.

## Stack

- `backend/` — FastAPI + MongoDB Atlas (motor), ReportLab PDFs, JWT auth. Deployed on Railway.
- `frontend/` — React + TypeScript + Vite + Tailwind. Deployed on Vercel.
- `guides/` — specs and design docs.

## Local dev

```bash
# backend (no Mongo needed: in-memory mock + seeded data)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt mongomock-motor
.venv/bin/python run_dev.py          # http://localhost:8000

# backend against real Mongo
cp .env.example .env                 # fill in MONGO_URL etc.
.venv/bin/python seed.py             # GoGreen node, config, three users
.venv/bin/uvicorn server:app --reload

# frontend
cd frontend
npm install
npm run dev                          # http://localhost:5173 (proxies /api to :8000)

# tests
cd backend && .venv/bin/python -m pytest test_chain.py -v
```

## Roles at launch

| User | Role | Can |
|---|---|---|
| Werner | admin | everything, incl. config, costs, margins, audit |
| Pierre | controller | reconciliation, payment matching, flag resolution |
| Steven | capturer | daily captures, delivery notes, invoices |

The on-site operator is not a user: paper sheet + one WhatsApp photo per day.

Seed passwords default to `changeme-*` — set `WERNER_PASSWORD` / `PIERRE_PASSWORD` / `STEVEN_PASSWORD`
in the environment before running `seed.py` in production.

## Deployment

See [DEPLOY.md](DEPLOY.md) for Railway, Vercel, and vezubunye.com DNS setup.

## Adding node two

Node two is a configuration entry, not a rebuild: create the node (admin), add its `node_config`
document, grant users `node_access`. `node_id` is on every record and indexed everywhere.
