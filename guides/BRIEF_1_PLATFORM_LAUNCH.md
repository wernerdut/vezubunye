# Vezubunye — Brief 1: Platform Launch (GoGreen Node)

Build the Vezubunye platform with one live node: GoGreen Roto Moulding, Queenstown. Domain: vezubunye.com.

## 1. What this is

Vezubunye runs a network of BEE tank-manufacturing nodes. Each node is a partner factory: sales and logistics by the partner, machine and all material by Fenix Rising. The app is internal and Fenix-controlled. Its purpose is control: prove there is no leakage anywhere on the chain from powder received to cash in the bank, and prove every rand owed to Fenix lands in the account.

Launch scope is one node page running the full powder-to-cash chain with manual capture and audit oversight. The platform structure (node identifier on every record, per-node configuration) is built in from the first table so node two is a configuration entry, not a rebuild. No network features at launch except one headline number: total kilograms through all nodes.

## 2. Stack

- Backend: FastAPI + MongoDB Atlas, deployed on Railway
- Frontend: React + TypeScript + Vite, deployed on Vercel
- PDF generation: ReportLab (delivery notes, invoices, daily capture sheet). Footer and terms blocks anchored to absolute vertical coordinates.
- Auth: simple email/password with role claims. Three users at launch.
- Repo: `vezubunye`, separate from RIPS. No integration with RIPS at launch.

## 3. Data model

`node_id` is a required field on every collection without exception. Indexed everywhere.

Collections: nodes, node_config, powder_ledger, production_runs, finished_goods_ledger, scrap_log, daily_captures, delivery_notes, invoices, payments, flags, physical_counts, users (+ audit_log, counters).

GoGreen launch config:
- 2500L: R1,620 ex VAT ex-works, 36 kg
- 5000L: R3,450 ex VAT ex-works, 75 kg
- Material cost: R20/kg (admin-only)
- Powder variance tolerance: ZERO. Every kilogram drawn must be accounted for by a moulded tank (any grade). Scrap is handled by grading tanks, not by tolerating powder variance.

Grades: A (full-price sellable), B (moulded but defective, sellable at reduced price), Reject (moulded but not sellable). Rejects never enter finished goods; they exit via scrap_log. Every moulded tank consumes its full weight in powder regardless of grade.

## 4. The chain and its reconciliations

powder in → powder drawn → tanks produced → finished goods → delivered (delivery note) → invoiced → paid into account → ex-works value drawn to Fenix.

Hard rules, enforced in logic and surfaced as flags:
1. Powder drawn (kg) = total tanks moulded (A + B + Reject) × tank weight, exactly. Zero tolerance.
2. Opening finished goods + produced − delivered = closing, per tank type per grade.
3. Every delivery requires a delivery note, and every delivery note must link to an invoice.
4. Every invoice must match to a payment. Unpaid past terms, short-paid, or over-paid: flag.
5. On payment match: Fenix draws ex-works value (full for A-grade, b_grade_exworks_pct × ex-works for B-grade), balance is the partner's.
6. Each tank type and grade reconciles separately. Nothing auto-clears.
7. Physical counts reconcile to system at intervals; variance flags.

Fenix cost (R20/kg) and margin never render for any role except admin.

## 5. Daily capture workflow

1. Printable Daily Capture Sheet (PDF, A4) per node; on-site operator fills it in by hand. Operator is not a system user.
2. One WhatsApp photo per day. Steven uploads it and keys the numbers into a single capture form that writes powder_ledger, production_runs, and finished_goods in one transaction.
3. Reconciliation rules 1 and 2 evaluate immediately on save and raise flags.
4. Pierre: reconciliation dashboard per node, day-by-day status, open flags, unmatched payments, unpaid invoices. Resolution requires a note.

Separation of duties: operations cannot resolve flags or match payments; audit cannot edit captures without an audit trail. Every write is audit-logged.

## 6. Documents

- Daily Capture Sheet (blank, printable, per node)
- Delivery notes (PDF), invoices (PDF). Numbering sequential per node with prefix (GG-INV-0001, GG-DN-0001).

## 7. Screens (launch)

Login · Node page (9 tabs) · Reconciliation dashboard (audit home) · Admin (config, users, nodes) · Network header with total kg on every page.

Do not build: comparison dashboards, partner portal, onboarding flows, second-node UI.

## 8. Brand

Colours: Dark Blue #022397 (primary), Light Blue #00aeef (accents), Yellow #dfbd25 (network kg headline), Red #d2232a (flags/errors), Green #41ad49 (clear/reconciled), Orange #e36837 (warnings).
Typography: Hakuna for headlines only; Montserrat as web body (Gotham substitute). Logo white on dark blue for app header/login; black or dark blue on white for PDFs. Tagline: "We Build Together!"

## 9. Roles at launch

Roles: admin | audit | operations.

Werner: admin. Pierre: audit. Steven: operations. Operator: not a user.

Stakeholder (not a system user): Charel Kerschbaumer, CTO and build owner.

Separation of duties: operations cannot resolve flags or match payments; audit cannot edit captures without an audit trail. The reconciliation dashboard is the audit home screen.

## 10. Reporting

Daily: tanks produced, powder in/drawn, finished goods, deliveries, invoices, payments, recon status.
Monthly: kg through plant, tanks by type, ex-works value, invoiced, cash received, outstanding.
Network: total kilograms (the headline).

## 11. Open items (Werner to confirm)

1. **b_grade_exworks_pct**: what does Fenix draw on a B-grade sale? (a) full ex-works, (b) fixed percentage, (c) proportional to discount. Built as a configurable percentage in node_config; **currently set to 100% (full ex-works)** pending Werner's call.
2. Reject handling: launch assumption is rejects exit the system entirely, no reclaim into the powder ledger. Confirm return/regrind/dump.

## 12. Build order

1. Data model, auth, node config, audit log ✓
2. Daily capture sheet PDF + capture form + ledgers + recon rules 1–2 ✓
3. Delivery notes and invoices (PDFs, numbering) ✓
4. Payments, matching, split, flags for rules 3–5 ✓
5. Reconciliation dashboard and flag resolution ✓
6. Physical counts ✓
7. Reporting views and the network kg headline ✓
8. Brand pass ✓
