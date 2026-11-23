"""One-off removals of a whole sale, asked for by Werner, run once on startup.

delete_sale removes everything that belongs to one sale so the partner can capture it again from
scratch: the proforma (quote), the order, its invoices, delivery notes and their stock
movements, payments made against them, pay links, share links, POD files records and flags.
The customer stays. Nothing is lost: every removed document is copied into deleted_backup
first, under one key, and the key makes the run idempotent.

It refuses (changes nothing, logs why) when anything has reached Xero, when cash from it has
been banked, or when a payment also pays something outside this sale.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import audit
import corrections
import db

SYSTEM = {"email": "system:cleanup", "role": "system"}

# Werner, 9 Nov 2026: "delete it completely so he can start it from scratch" (Empire, MX-INV-0008)
REQUESTS = [{"key": "delete-sale-makx-MX-INV-0008", "slug": "makx", "invoice": "MX-INV-0008",
             "customer_contains": "empire"}]


def backups():
    return db.db()["deleted_backup"]


async def delete_sale(key: str, slug: str, invoice_number: str, customer_contains: str = "") -> dict:
    if await backups().find_one({"_id": key}):
        return {"skipped": "already done"}
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {"skipped": f"no node {slug}"}
    nid = node["node_id"]
    inv = await db.invoices().find_one({"node_id": nid, "display_number": invoice_number})
    if not inv:
        return {"skipped": f"{invoice_number} not found"}
    order = await db.orders().find_one({"_id": inv.get("order_id")}) if inv.get("order_id") else None
    if not order:
        return {"refused": f"{invoice_number} has no order"}
    name = (order.get("customer_name") or inv.get("customer_name") or "").lower()
    if customer_contains and customer_contains.lower() not in name:
        return {"refused": f"customer is {name!r}, expected {customer_contains!r}"}

    oid = order["_id"]
    quote = await db.quotes().find_one({"_id": order["quote_id"]}) if order.get("quote_id") else None
    invs = [i async for i in db.invoices().find({"order_id": oid})]
    inv_ids = {i["_id"] for i in invs}
    dns = [d async for d in db.delivery_notes().find({"order_id": oid})]
    dn_ids = {d["_id"] for d in dns}
    qid = quote["_id"] if quote else None
    pay_or = [{"allocations.invoice_id": {"$in": list(inv_ids)}}, {"dn_id": {"$in": list(dn_ids)}}]
    if qid:
        pay_or.append({"quote_id": qid})
    pays = [p async for p in db.payments().find({"node_id": nid, "$or": pay_or})]

    # refuse what cannot be undone here
    for i in invs:
        if (i.get("xero") or {}).get("invoice_id") or (i.get("xero") or {}).get("status") == "posted":
            return {"refused": f"{i.get('display_number')} is in Xero"}
    for p in pays:
        if p.get("posted_to_xero") or p.get("xero_payment_id"):
            return {"refused": f"payment {p['_id']} is in Xero"}
        if p.get("deposit_id"):
            return {"refused": f"cash payment {p['_id']} is already banked"}
        if any(a["invoice_id"] not in inv_ids for a in p.get("allocations") or []):
            return {"refused": f"payment {p['_id']} also pays another invoice"}

    rids = list(inv_ids | ({qid} if qid else set()))
    pl_or = [{"invoice_id": {"$in": list(inv_ids)}}, {"dn_id": {"$in": list(dn_ids)}}] + ([{"quote_id": qid}] if qid else [])
    flag_or = [{"references.order_id": oid}, {"references.dn_id": {"$in": list(dn_ids)}},
               {"references.invoice_id": {"$in": list(inv_ids)}}] + ([{"references.quote_id": qid}] if qid else [])
    found = {
        "orders": [order],
        "quotes": [quote] if quote else [],
        "invoices": invs,
        "delivery_notes": dns,
        "finished_goods_ledger": [f async for f in db.finished_goods().find(
            {"node_id": nid, "reference": {"$in": list(dn_ids)}})],
        "payments": pays,
        "pay_links": [pl async for pl in db.pay_links().find({"node_id": nid, "$or": pl_or})],
        "short_links": [s async for s in db.short_links().find({"rid": {"$in": rids}})],
        "pod_files": [f async for f in db.pod_files().find({"dn_id": {"$in": list(dn_ids)}})],
        "delivery_documents": [d async for d in db.delivery_documents().find({"delivery_id": {"$in": list(dn_ids)}})],
        "flags": [f async for f in db.flags().find({"node_id": nid, "$or": flag_or})],
    }
    colls = {"orders": db.orders(), "quotes": db.quotes(), "invoices": db.invoices(),
             "delivery_notes": db.delivery_notes(), "finished_goods_ledger": db.finished_goods(),
             "payments": db.payments(), "pay_links": db.pay_links(), "short_links": db.short_links(),
             "pod_files": db.pod_files(), "delivery_documents": db.delivery_documents(), "flags": db.flags()}
    summary = {k: len(v) for k, v in found.items() if v}
    summary["paid"] = round(sum(p["amount"] for p in pays), 2)
    summary["payment_methods"] = sorted({p.get("method", "") for p in pays})
    summary["numbers"] = [x for x in [quote and quote.get("quote_number"), order.get("order_number")]
                          + [i.get("display_number") for i in invs] + [d.get("dn_number") for d in dns] if x]
    async with corrections.txn() as s:
        await backups().insert_one({"_id": key, "node_id": nid, "at": datetime.utcnow(), "summary": summary,
                                    "docs": found}, session=s)
        for name_, docs in found.items():
            if docs:
                await colls[name_].delete_many({"_id": {"$in": [d["_id"] for d in docs]}}, session=s)
        await audit.log(SYSTEM, nid, "delete_sale", "orders", oid, before={"summary": summary}, session=s)
    return {"deleted": summary}


async def charges_onto_invoices(slug: str = "makx") -> dict:
    """Werner, 11 Nov 2026: terms invoices made before charges were included get their left-off
    charges added (MX-ORD-0022's R200 onto MX-INV-0026). Paid or Xero invoices are skipped."""
    import commerce
    import recon
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    cfg = await recon.get_config(node["node_id"])
    done, skipped = [], []
    async for o in db.orders().find({"node_id": node["node_id"], "payment_basis": "terms", "status": {"$ne": "cancelled"}}):
        if not await commerce.uninvoiced_charges(o, cfg):
            continue
        if not await db.invoices().find_one({"order_id": o["_id"], **corrections.ACTIVE}):
            continue          # nothing invoiced yet: the first invoice will carry them
        try:
            inv = await commerce.add_charges_to_invoice(node, cfg, o, SYSTEM)
            await commerce.refresh_order_status(o["_id"])
            done.append(f"{o.get('order_number')}: {inv.get('display_number')} now {inv['total']}")
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{o.get('order_number')}: {getattr(e, 'detail', e)}")
    return {"added": done, "skipped": skipped}


async def cash_was_eft(slug: str = "makx") -> dict:
    """Werner, 13 Nov 2026: with no EFT option in Record payment, Steven captured EFTs as cash,
    so cash on hand showed money he never held. Cash he recorded himself (not driver collections),
    not banked and not in Xero, becomes EFT waiting to be matched in Xero."""
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    done = []
    async for p in db.payments().find({"node_id": node["node_id"], "method": "cash", "deposit_id": None,
                                       "dn_id": None, "xero_payment_id": None, **corrections.ACTIVE}):
        h = p.get("holder") or {}
        if h.get("type") == "driver" or p.get("posted_to_xero") or p.get("xero_payment_ids"):
            continue
        ref = (p.get("reference") or "").strip()
        upd = {"method": "eft", "holder": None, "xero_status": "match_in_xero", "was_cash": True,
               "reference": ref or "EFT (captured as cash)"}
        await db.payments().update_one({"_id": p["_id"]}, {"$set": upd})
        await audit.log(SYSTEM, node["node_id"], "update", "payments", p["_id"],
                        before={"method": "cash", "holder": p.get("holder")},
                        after={**upd, "reason": "EFT captured as cash before EFT was offered"})
        done.append(f"{p.get('date')} R{p['amount']:,.2f} {ref}".strip())
    return {"changed": done}


async def cod_invoices_ahead(slug: str = "makx") -> list:
    """Read only: pay on delivery invoices made before delivery (before 13 Nov 2026), unpaid and
    not delivered yet. Listed so Werner can decide what to do with them; nothing is changed."""
    node = await db.nodes().find_one({"slug": slug})
    out = []
    if not node:
        return out
    async for o in db.orders().find({"node_id": node["node_id"], "pay_on_delivery": True, "status": "open"}):
        if await db.delivery_notes().find_one({"order_id": o["_id"], "delivery_status": "delivered", **corrections.ACTIVE}):
            continue
        async for i in db.invoices().find({"order_id": o["_id"], **corrections.ACTIVE}):
            if (i.get("amount_paid") or 0) > 0.004:
                continue
            x = i.get("xero") or {}
            out.append(f"{i.get('display_number')} {o.get('order_number')} {o.get('customer_name')} "
                       f"R{i['total']:,.2f} xero={'yes' if x.get('invoice_id') else 'no'}")
    return out


async def xero_accounts(slug: str = "makx", sales: str = "200", delivery: str = "210") -> dict:
    """Pierre, 13 Nov 2026: tanks to account 200 (Sales), delivery fees to 210 (Shipping), tax
    Standard Rate Sales 15% (the default code OUTPUT is South Africa's archived 14%). Sets the
    Xero settings and resends the lines of invoices already in Xero. Accounts and the tax rate
    are checked against the organisation's own lists first; nothing is guessed."""
    import xero_mt
    node = await db.nodes().find_one({"slug": slug})
    c = await xero_mt.connection(node["node_id"]) if node else None
    if not c or c.get("status") != "connected":
        return {"skipped": "Xero not connected"}
    await xero_mt.sync_reference(node["node_id"])
    c = await xero_mt.connection(node["node_id"])
    ref = c.get("reference") or {}
    codes = {a["code"]: a["name"] for a in ref.get("accounts", [])}
    missing = [x for x in (sales, delivery) if x not in codes]
    if missing:
        return {"refused": f"account {', '.join(missing)} not in Xero"}
    rates = [t for t in ref.get("tax_rates", []) if abs(t["rate"] - 15) < 0.01
             and t["name"].strip().lower() == "standard rate sales"]      # not "... - Capital Goods"
    if len(rates) != 1:
        return {"refused": f"15% standard sales rate not found once: {[t['name'] for t in rates]}"}
    cfg = await db.node_config().find_one({"node_id": node["node_id"]})
    s = {**xero_mt.default_settings(), **(c.get("settings") or {})}
    before = {k: s.get(k) for k in ("sales_accounts", "charge_accounts", "tax_type")}
    s["sales_accounts"] = {"water": sales, "septic": sales}
    s["charge_accounts"] = {ch["code"]: delivery for ch in cfg.get("charges", [])}
    s["tax_type"] = rates[0]["tax_type"]
    await xero_mt.conns().update_one({"node_id": node["node_id"]}, {"$set": {"settings": s}})
    await audit.log(SYSTEM, node["node_id"], "update", "xero_settings", None, before=before,
                    after={k: s[k] for k in before})
    lines = await xero_mt.update_all_lines(node["node_id"])
    return {"sales": f"{sales} {codes[sales]}", "delivery": f"{delivery} {codes[delivery]}",
            "tax": f"{rates[0]['tax_type']} {rates[0]['name']}", "charges": list(s["charge_accounts"]), **lines}


async def terms_fix(slug: str = "makx") -> dict:
    """16 Nov 2026: payment terms saved as text ("7") made invoicing fail with a server error, so
    a terms delivery marked delivered got no invoice (MX-DN-0028). Terms become numbers, and terms
    deliveries delivered in the last 3 days without an invoice are invoiced now, as they should
    have been, dated today."""
    import commerce
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    fixed = 0
    async for c in db.customers().find({"node_id": node["node_id"], "payment_terms_days": {"$type": "string"}}):
        await db.customers().update_one({"_id": c["_id"]}, {"$set": {
            "payment_terms_days": commerce.terms_days(c["payment_terms_days"])}})
        fixed += 1
    invoiced = []
    since = datetime.utcnow() - timedelta(days=3)
    async for dn in db.delivery_notes().find({"node_id": node["node_id"], "delivery_status": "delivered",
                                              "delivered_at": {"$gte": since}, **corrections.ACTIVE}):
        try:
            inv = await commerce.invoice_on_delivery(dn, SYSTEM)
        except Exception as e:  # noqa: BLE001
            invoiced.append(f"{dn.get('dn_number')}: failed {getattr(e, 'detail', e)}")
            continue
        if inv:
            invoiced.append(f"{dn.get('dn_number')}: {inv.get('display_number')} R{inv['total']:,.2f}")
            try:
                import xero_mt
                await xero_mt.post_invoice(inv["_id"])
            except Exception as e:  # noqa: BLE001
                print(f"[xero] invoice post deferred: {e}")
    return {"customers_fixed": fixed, "invoiced": invoiced}


async def overpaid_report(slug: str = "makx") -> list:
    """Read only (16 Nov 2026): invoices showing more paid than their total, with every payment
    on them, to see which were added twice by the Xero pull. Nothing is changed."""
    node = await db.nodes().find_one({"slug": slug})
    out = []
    if not node:
        return out
    async for i in db.invoices().find({"node_id": node["node_id"], **corrections.ACTIVE}).sort("display_number", 1):
        if (i.get("amount_paid") or 0) <= (i.get("total") or 0) + 0.05:
            continue
        pays = []
        async for p in db.payments().find({"allocations.invoice_id": i["_id"]}).sort("created_at", 1):
            alloc = sum(a["amount"] for a in p.get("allocations") or [] if a["invoice_id"] == i["_id"])
            pays.append(f"{p['_id'][:6]} {p.get('date')} R{p['amount']:.2f} alloc {alloc:.2f} {p.get('method')}"
                        f"{' was_cash' if p.get('was_cash') else ''} by {p.get('captured_by')} made {str(p.get('created_at'))[:16]}"
                        f" ref {p.get('reference')!r} xpid {str(p.get('xero_payment_id'))[:8]}"
                        f" xpids {[str(v)[:8] for v in (p.get('xero_payment_ids') or {}).values()]}"
                        f" more {p.get('xero_more')} mismatch {p.get('xero_amount_mismatch')}"
                        f" {p.get('xero_status')}{' VOID' if p.get('void') else ''}")
        out.append({f"{i.get('display_number')} {i.get('customer_name')} total {i['total']:.2f} paid {i['amount_paid']:.2f}": pays})
    return out


async def xero_doubles(slug: str = "makx") -> dict:
    """16 Nov 2026 (Steven: "On payments I now get a overpaid tab. Friday it was all correct"):
    the Xero pull of 15 Nov added payments Pierre had matched in Xero as new EFT although Steven
    or Yoco had already captured the same money here, so five invoices showed paid twice. On an
    overpaid invoice, an EFT the pull added that belongs to a payment captured here is voided and
    its Xero payment is linked to that captured payment instead. Anything else is left and listed."""
    import commerce
    import xero_mt
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    nid = node["node_id"]
    fixed, left, relinked = [], [], []
    reason = "Counted twice: already captured in MakX, matched in Xero"
    # the first run (inside startup) was stopped part way: bring every invoice's paid figure up
    # to date, and link what it voided but had not linked yet
    async for i in db.invoices().find({"node_id": nid, **corrections.ACTIVE}):
        await commerce.refresh_invoice(i["_id"])
    async for v in db.payments().find({"node_id": nid, "void.reason": reason, "xero_payment_id": {"$ne": None}}):
        pid, iid = v["xero_payment_id"], v["allocations"][0]["invoice_id"]
        if await db.payments().find_one({"node_id": nid, **corrections.ACTIVE, "$or": [
                {f"xero_payment_ids.{iid}": pid}, {f"xero_more.{iid}.{pid}": {"$exists": True}}]}):
            continue
        if await xero_mt._link_yoco(nid, iid, {"PaymentID": pid, "Amount": v["amount"]}):
            relinked.append(f"R{v['amount']:,.2f}")
    async for p in db.payments().find({"node_id": nid, "captured_by": xero_mt.SYSTEM["email"], "method": "eft",
                                       "xero_payment_id": {"$ne": None}, **corrections.ACTIVE}).sort("created_at", 1):
        allocs = p.get("allocations") or []
        if len(allocs) != 1:
            continue
        inv = await db.invoices().find_one({"_id": allocs[0]["invoice_id"]})
        if not inv or (inv.get("amount_paid") or 0) <= inv["total"] + commerce.PAY_TOL:
            continue
        label = f"{inv.get('display_number')} {inv.get('customer_name')} R{p['amount']:,.2f}"
        xp = {"PaymentID": p["xero_payment_id"], "Amount": p["amount"]}
        without = round(inv["amount_paid"] - allocs[0]["amount"], 2)
        if not await xero_mt._link_target(nid, inv["_id"], p["amount"], without):
            left.append(label)
            continue
        async with corrections.txn() as s:
            await corrections.mark_void("payments", p["_id"], SYSTEM, reason, None, s)
        await commerce.refresh_invoice(inv["_id"])
        await xero_mt._link_yoco(nid, inv["_id"], xp)
        if inv.get("order_id"):
            await commerce.refresh_order_status(inv["order_id"])
        after = await db.invoices().find_one({"_id": inv["_id"]})
        fixed.append(f"{label}: now paid R{after['amount_paid']:,.2f} of R{after['total']:,.2f} ({after['status']})")
    async for v in db.payments().find({"node_id": nid, "void.reason": reason}):
        await db.flags().update_many(
            {"node_id": nid, "type": "over_paid", "status": "open", "references.payment_id": v["_id"]},
            {"$set": {"status": "resolved", "resolved_by": SYSTEM["email"], "resolved_at": datetime.utcnow(),
                      "resolution_note": "Xero pull counted a captured payment twice; the second one is voided."}})
    if relinked:
        return {"fixed": fixed, "left": left, "relinked": relinked}
    return {"fixed": fixed, "left": left}


# Werner, 18 Nov 2026: "yes on all" (remove one Yoco duplicate and Steven's manual payment)
YOCO_DOUBLE = {"key": "yoco-double-makx-MX-INV-0049", "yoco_id": "p_aPgrM90Bbaj6tk01HpWtlVEb",
               "customer_id": "98a56520a8d94e45a5ca15b3b5ffe7d2", "hand_amount": 3700.0,
               "hand_from": datetime(2026, 11, 18, 7, 31, 0), "hand_to": datetime(2026, 11, 18, 7, 32, 0)}


async def yoco_double(yoco_id: str, customer_id: str, hand_amount: float, hand_from: datetime,
                      hand_to: datetime, slug: str = "makx") -> dict:
    """18 Nov 2026 (Steven: "So double payment please correct"). Yoco sent the paid notice for
    MX-INV-0049 (Pure Source, R3,700) twice, 38 ms apart, and both were recorded (yoco._claim now
    prevents that). Steven's screen still showed unpaid, so four minutes later he captured the
    same R3,700 by hand on the Payments screen. The first Yoco payment stays; the second one and
    the hand capture are voided. The hand capture is voided only when exactly one payment fits
    (this customer, this amount, captured by a person in that minute), it has not been banked
    and it has not been matched in Xero. Anything else is left and listed. Times are UTC."""
    import commerce
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    nid = node["node_id"]

    def show(p: dict) -> str:
        return (f"{p['_id'][:6]} {p.get('date')} R{p['amount']:.2f} {p.get('method')}"
                f"{'/' + p['source'] if p.get('source') else ''} by {p.get('captured_by')}"
                f" made {str(p.get('created_at'))[:19]} ref {p.get('reference')!r}"
                f" alloc {[(a['invoice_id'][:6], a['amount']) for a in p.get('allocations') or []]}"
                f" unallocated {p.get('unallocated')}")

    twice = [p async for p in db.payments().find({"node_id": nid, "external_id": yoco_id,
                                                  **corrections.ACTIVE}).sort("created_at", 1)]
    hand = [p async for p in db.payments().find({"node_id": nid, "customer_id": customer_id, "external_id": None,
                                                 "created_at": {"$gte": hand_from, "$lt": hand_to},
                                                 **corrections.ACTIVE})
            if abs(p["amount"] - hand_amount) < 0.005
            and p.get("captured_by") not in (SYSTEM["email"], "yoco:webhook", "system:xero")]
    out: dict = {"yoco_found": [show(p) for p in twice], "hand_found": [show(p) for p in hand],
                 "voided": [], "left": [], "invoices": []}
    to_void = [(p, "Counted twice: Yoco sent the paid notice twice") for p in twice[1:]]
    if twice and len(hand) == 1 and not hand[0].get("deposit_id") and not hand[0].get("xero_payment_id"):
        to_void.append((hand[0], "Captured by hand for money Yoco had already recorded"))
    else:
        out["left"] = [show(p) for p in hand]
    touched = {a["invoice_id"] for p in twice for a in p.get("allocations") or []}
    gone = []
    for p, reason in to_void:
        async with corrections.txn() as s:
            await corrections.mark_void("payments", p["_id"], SYSTEM, reason, None, s)
        gone.append(p["_id"])
        touched |= {a["invoice_id"] for a in p.get("allocations") or []}
        out["voided"].append(f"{show(p)}: {reason}")
    done = {"status": "resolved", "resolved_by": SYSTEM["email"], "resolved_at": datetime.utcnow(),
            "resolution_note": "Yoco sent the paid notice twice and the same money was also captured by hand; "
                               "the extra payments are voided."}
    for iid in sorted(touched):
        await commerce.refresh_invoice(iid)
        inv = await db.invoices().find_one({"_id": iid})
        if inv.get("order_id"):
            await commerce.refresh_order_status(inv["order_id"])
        out["invoices"].append(f"{inv.get('display_number')} {inv.get('customer_name')}: now paid "
                               f"R{inv['amount_paid']:,.2f} of R{inv['total']:,.2f} ({inv['status']})")
        if inv["amount_paid"] <= inv["total"] + commerce.PAY_TOL:
            await db.flags().update_many({"node_id": nid, "type": "over_paid", "status": "open",
                                          "references.invoice_id": iid}, {"$set": done})
    if gone:
        await db.flags().update_many({"node_id": nid, "type": {"$in": ["over_paid", "payment_unallocated"]},
                                      "status": "open", "references.payment_id": {"$in": gone}}, {"$set": done})
    out["customer_payments_now"] = [show(p) async for p in db.payments().find(
        {"node_id": nid, "customer_id": customer_id, **corrections.ACTIVE}).sort("created_at", 1)]
    return out


async def run() -> list:
    out = [{r["key"]: await delete_sale(r["key"], r["slug"], r["invoice"], r.get("customer_contains", ""))}
           for r in REQUESTS]
    out.append({"charges_onto_invoices": await charges_onto_invoices()})
    out.append({"cod_invoices_ahead": await cod_invoices_ahead()})
    try:
        out.append({"terms_fix": await terms_fix()})
    except Exception as e:  # noqa: BLE001
        out.append({"terms_fix": f"failed: {e}"})
    try:    # unpaid proformas older than 14 days: cancelled now, then nightly (Werner, 13 Nov 2026)
        import commerce
        mk = await db.nodes().find_one({"slug": "makx"})
        if mk:
            out.append({"proformas_cancelled": await commerce.sweep_proformas(mk["node_id"])})
    except Exception as e:  # noqa: BLE001
        out.append({"proformas_cancelled": f"failed: {e}"})
    if not await backups().find_one({"_id": "cash-was-eft-makx"}):
        r = await cash_was_eft()
        await backups().insert_one({"_id": "cash-was-eft-makx", "at": datetime.utcnow(), "summary": r, "docs": {}})
        out.append({"cash_was_eft": r})
    return out


async def not_in_xero(slug: str = "makx") -> list:
    """Read only (16 Nov 2026, Pierre: today's invoices are not in Xero): every invoice that has
    not reached Xero, with the reason Xero gave. Nothing is changed."""
    node = await db.nodes().find_one({"slug": slug})
    out = []
    if not node:
        return out
    async for i in db.invoices().find({"node_id": node["node_id"], "legacy": {"$ne": True},
                                       "xero.status": {"$ne": "posted"}, **corrections.ACTIVE}).sort("display_number", 1):
        x = i.get("xero") or {}
        out.append(f"{i.get('display_number')} {i.get('date')} {i.get('customer_name')} R{i.get('total', 0):,.2f} "
                   f"made {str(i.get('created_at'))[:16]}: {x.get('status')} {str(x.get('error'))[:300]}")
    return out


async def xero_look(slug: str = "makx") -> dict:
    """Read only (16 Nov 2026): what Xero itself says about the invoices of the last 4 days
    (MakX has them all as sent, Pierre does not see them), and how invoices are sent."""
    import xero_mt
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    nid = node["node_id"]
    c = await xero_mt.connection(nid) or {}
    since = (datetime.utcnow() - timedelta(days=4)).date().isoformat()
    ours = {}
    async for i in db.invoices().find({"node_id": nid, "date": {"$gte": since}, "xero.invoice_id": {"$exists": True},
                                       **corrections.ACTIVE}):
        ours[i["xero"]["invoice_id"]] = i
    rows = []
    if ours:
        body = await xero_mt.request(nid, "GET", "Invoices", params={"IDs": ",".join(ours)})
        got = {x["InvoiceID"]: x for x in (body or {}).get("Invoices") or []}
        for xid, i in sorted(ours.items(), key=lambda kv: kv[1].get("display_number") or ""):
            x = got.get(xid)
            rows.append(f"{i.get('display_number')} sent {str(i['xero'].get('posted_at'))[:16]}: "
                        + (f"Xero {x.get('Status')} no {x.get('InvoiceNumber')} total {x.get('Total')}" if x else "NOT FOUND in Xero"))
    return {"org": c.get("org_name"), "send_as": (c.get("settings") or {}).get("post_status"), "invoices": rows}


async def main_sites_look(slug: str = "makx", dn_number: str = "MX-DN-0038") -> dict:
    """Read only, counts and yes/no only (no names or addresses): how many delivery addresses
    have no address text, how many of those are a customer's Main with an address typed on the
    customer, and what the note Steven sent on 23 Nov 2026 holds. Changes nothing."""
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {"skipped": f"no node {slug}"}
    nid = node["node_id"]
    out = {"customers": 0, "sites": 0, "no_text": 0, "no_text_by_label": {}, "main_no_text": 0,
           "main_no_text_with_customer_address": 0, "no_text_pinned": 0, "no_text_place_link": 0}
    async for c in db.customers().find({"node_id": nid}, {"sites": 1, "billing_address": 1, "billing_city": 1}):
        out["customers"] += 1
        has_addr = bool(str(c.get("billing_address") or "").strip() or str(c.get("billing_city") or "").strip())
        for st in c.get("sites") or []:
            out["sites"] += 1
            if st.get("address") or st.get("town"):
                continue
            out["no_text"] += 1
            label = (st.get("label") or "").strip().lower()[:12]
            key = label if label in ("main", "site", "delivery") else "other"
            out["no_text_by_label"][key] = out["no_text_by_label"].get(key, 0) + 1
            out["no_text_pinned"] += st.get("lat") is not None
            out["no_text_place_link"] += "/place/" in str(st.get("resolved_link") or st.get("maps_link") or "")
            if label == "main":
                out["main_no_text"] += 1
                out["main_no_text_with_customer_address"] += has_addr
    dn = await db.delivery_notes().find_one({"node_id": nid, "dn_number": dn_number}, {"content_b64": 0})
    if dn:
        st = dn.get("site") or {}
        c = await db.customers().find_one({"_id": dn.get("customer_id")}) or {}
        out["note"] = {"number": dn_number, "site_fields": sorted(k for k, v in st.items() if v not in ("", None, False)),
                       "customer_has_address": bool(str(c.get("billing_address") or "").strip()),
                       "customer_sites": len(c.get("sites") or []),
                       "place_link": "/place/" in str(st.get("resolved_link") or st.get("maps_link") or "")}
    return out


async def background():
    """Slow one-offs (calls to Xero per invoice, voids with their sweeps) run after startup, so
    the health check is not held up: doing them inside startup failed a deploy on 13 Nov 2026
    and again on 16 Nov 2026."""
    try:
        print(f"cleanup background: {{'main_sites_look': {await main_sites_look()}}}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"cleanup background: main_sites_look failed: {str(getattr(e, 'message', e))[:300]}", flush=True)
    key = "xero-accounts-makx-v2"
    try:
        if not await backups().find_one({"_id": key}):
            r = await xero_accounts()
            if r.get("sales"):
                await backups().insert_one({"_id": key, "at": datetime.utcnow(), "summary": r, "docs": {}})
            print(f"cleanup background: {{'xero_accounts': {r}}}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"cleanup background: xero_accounts failed: {str(getattr(e, 'message', e))[:300]}", flush=True)
    try:
        if not await backups().find_one({"_id": YOCO_DOUBLE["key"]}):
            r = await yoco_double(**{k: v for k, v in YOCO_DOUBLE.items() if k != "key"})
            await backups().insert_one({"_id": YOCO_DOUBLE["key"], "at": datetime.utcnow(), "summary": r, "docs": {}})
            print(f"cleanup background: {{'yoco_double': {r}}}", flush=True)
            print(f"cleanup background: {{'overpaid_now': {await overpaid_report()}}}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"cleanup background: yoco_double failed: {str(getattr(e, 'message', e))[:300]}", flush=True)
