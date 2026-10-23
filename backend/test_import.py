"""Cin7 customer import: refs kept, phones E.164, trade only where Cin7 priced them as trade,
re-runs update instead of duplicating, hand-keyed customers merged on phone.

Run: .venv/bin/python -m pytest test_import.py -v
"""
import csv
import os

import pytest

CUST_COLS = ("makx_ref,name,customer_type,type_reason,contact_name,whatsapp_mobile,other_phone,email,vat_number,"
             "billing_line1,billing_line2,billing_city,billing_province,billing_postcode,billing_country,"
             "billing_address_quality,billing_address_source,delivery_addresses,usually_collects,orders,first_order,"
             "last_order,total_invoiced_incl_vat_zar,unpaid_invoices_zar,unpaid_invoices,unpaid_note,marketing_consent,"
             "cin7_payment_terms,cin7_price_tier,merged_from_cin7,cin7_ids,cin7_names_merged,shares_phone_or_email_with").split(",")
ADDR_COLS = ("makx_ref,customer_name,address_type,is_default,line1,line2,city,province,postcode,country,"
             "address_quality,source,pin_lat,pin_lng").split(",")


def write(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


@pytest.fixture
async def files(tmp_path):
    from mongomock_motor import AsyncMongoMockClient
    import db
    db._client = AsyncMongoMockClient()
    os.environ["DISABLE_SCHEDULER"] = "1"
    import seed
    import migrate_brief2
    await seed.seed()
    await migrate_brief2.run()
    cust, addr = tmp_path / "c.csv", tmp_path / "a.csv"
    write(cust, CUST_COLS, [
        {"makx_ref": "MKC-0001", "name": "Test Plumbing CC", "customer_type": "Trade", "type_reason": "Cin7 wholesale price list",
         "whatsapp_mobile": "+27821110001", "cin7_ids": "id-1 id-1b", "cin7_payment_terms": "30 days", "orders": "3",
         "last_order": "2026-10-01", "billing_line1": "1 Pipe St", "billing_city": "Hout Bay"},
        {"makx_ref": "MKC-0002", "name": "Looks Trade Pty", "customer_type": "Trade", "type_reason": "trade business name, check",
         "email": "a@b.co.za", "cin7_ids": "id-2"},
        {"makx_ref": "MKC-0003", "name": "Jane Doe", "customer_type": "Retail", "whatsapp_mobile": "+27825550101", "cin7_ids": "id-3"},
    ])
    write(addr, ADDR_COLS, [
        {"makx_ref": "MKC-0001", "address_type": "delivery", "is_default": "no", "line1": "Site B", "city": "Noordhoek"},
        {"makx_ref": "MKC-0001", "address_type": "delivery", "is_default": "yes", "line1": "Site A", "city": "Hout Bay",
         "pin_lat": "-34.03", "pin_lng": "18.35"},
    ])
    return str(cust), str(addr)


async def test_import(files):
    import db
    import import_cin7
    import commerce
    # hand-keyed before the import, same phone as MKC-0003
    await commerce.create_customer("makulu", {"name": "Jane", "phone": "082 555 0101"}, {"email": "s@x", "role": "partner_admin"}, True)
    early = await commerce.create_customer("makulu", {"name": "Walk In"}, {"email": "s@x", "role": "partner_admin"}, True)
    assert early["ref"] == "MKC-0002"                                    # would clash with Cin7's MKC-0002
    dry = await import_cin7.run(*files, apply=False)
    assert dry["new"] == 2 and len(dry["merged_with_existing"]) == 1
    assert await db.customers().count_documents({"node_id": "makulu", "cin7_ids": {"$exists": True}}) == 0
    out = await import_cin7.run(*files, apply=True)
    assert out["trade"] == 1 and out["trade_to_check"] == 1
    t = await db.customers().find_one({"ref": "MKC-0001"})
    assert t["price_tier"] == "trade" and t["payment_basis"] == "upfront" and t["cin7"]["payment_terms"] == "30 days"
    assert [s["address"] for s in t["sites"]] == ["Site A", "Site B"] and t["sites"][0]["pinned"]
    assert t["cin7_ids"] == ["id-1", "id-1b"] and t["last_order_date"] == "2026-10-01"
    assert (await db.customers().find_one({"ref": "MKC-0002"}))["price_tier"] == "retail"
    jane = await db.customers().find_one({"ref": "MKC-0003"})
    assert jane["name"] == "Jane" and jane["cin7_ids"] == ["id-3"]       # merged, name as keyed
    assert (await db.customers().find_one({"_id": early["_id"]}))["ref"] == "MKC-0004"
    again = await import_cin7.run(*files, apply=True)
    assert again["new"] == 0 and again["updated"] == 3
    assert await db.customers().count_documents({"node_id": "makulu"}) == 4
    assert (await db.customers().find_one({"_id": early["_id"]}))["ref"] == "MKC-0004"     # kept on re-run
    n = await commerce.create_customer("makulu", {"name": "Next One"}, {"email": "s@x", "role": "partner_admin"}, True)
    assert n["ref"] == "MKC-0005"
