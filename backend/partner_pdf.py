"""Partner-branded PDFs: quote, tax invoice, delivery note, customer statement, daily capture
sheet. Everything comes from the node's branding and company details; no platform name or
mark appears on any of them (tested).

Layout follows pdf_gen: content never crosses the absolute footer anchor; long tables page.
"""
from __future__ import annotations

from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

import maps

W, H = A4
MARGIN = 16 * mm
FOOTER_Y = 20 * mm
GREY = HexColor("#5f6b70")
LINE = HexColor("#dfe4e6")
F = "Helvetica"
FB = "Helvetica-Bold"


def money(x: float | None) -> str:
    return f"R{(x or 0):,.2f}"


def _brand(node: dict) -> dict:
    b = node.get("branding") or {}
    c = b.get("colors") or {}
    return {"name": b.get("display_name") or node.get("name", ""),
            "primary": HexColor(c.get("primary") or "#333333"),
            "accent": HexColor(c.get("accent") or c.get("primary") or "#333333"),
            "dark": HexColor(c.get("dark") or "#111111"),
            "logo": node.get("_logo_png")}


def _company(node: dict) -> dict:
    return node.get("company") or {}


class Doc:
    """A paged canvas with the partner header and footer on every page."""

    def __init__(self, node: dict, title: str, number: str = "", date: str = "", extra: list[str] | None = None):
        self.buf = BytesIO()
        self.c = Canvas(self.buf, pagesize=A4)
        self.node, self.title, self.number, self.date, self.extra = node, title, number, date, extra or []
        self.b = _brand(node)
        self.c.setTitle(f"{title} {number}".strip())
        self.c.setAuthor(self.b["name"])
        self.y = self._header()

    def _header(self) -> float:
        c, b = self.c, self.b
        top = H - 12 * mm
        if b["logo"]:
            try:
                img = ImageReader(BytesIO(b["logo"]))
                iw, ih = img.getSize()
                lh = 24 * mm
                lw = min(60 * mm, lh * iw / ih)
                c.drawImage(img, MARGIN, top - lh, width=lw, height=lw * ih / iw, mask="auto",
                            preserveAspectRatio=True)
            except Exception:  # noqa: BLE001
                self._name_text(top)
        else:
            self._name_text(top)
        c.setFillColor(b["dark"])
        c.setFont(FB, 20)
        c.drawRightString(W - MARGIN, top - 7 * mm, self.title)
        c.setFont(FB, 10)
        yy = top - 13 * mm
        if self.number:
            c.drawRightString(W - MARGIN, yy, self.number)
            yy -= 4.5 * mm
        c.setFont(F, 9)
        for t in ([self.date] if self.date else []) + self.extra:
            c.drawRightString(W - MARGIN, yy, t)
            yy -= 4.2 * mm
        rule = min(top - 28 * mm, yy - 2 * mm)
        c.setStrokeColor(b["primary"])
        c.setLineWidth(2)
        c.line(MARGIN, rule, W - MARGIN, rule)
        self._footer()
        return rule - 8 * mm

    def _name_text(self, top: float):
        self.c.setFillColor(self.b["dark"])
        self.c.setFont(FB, 16)
        self.c.drawString(MARGIN, top - 10 * mm, self.b["name"])

    def _footer(self):
        c, co = self.c, _company(self.node)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(MARGIN, FOOTER_Y, W - MARGIN, FOOTER_Y)
        c.setFillColor(GREY)
        c.setFont(F, 7)
        l1 = "  ·  ".join(x for x in (co.get("legal_name"), f"Reg. No. {co['reg_no']}" if co.get("reg_no") else "",
                                      f"VAT {co['vat_number']}" if co.get("vat_number") else "") if x)
        l2 = "  ·  ".join(x for x in (co.get("address"), co.get("phone"), co.get("whatsapp") and f"WhatsApp {co['whatsapp']}",
                                      co.get("email"), co.get("website")) if x)
        c.drawString(MARGIN, FOOTER_Y - 4 * mm, l1)
        c.drawString(MARGIN, FOOTER_Y - 7.5 * mm, l2)
        c.drawRightString(W - MARGIN, FOOTER_Y - 4 * mm, f"Page {c.getPageNumber()}")

    def need(self, h: float):
        if self.y - h < FOOTER_Y + 8 * mm:
            self.c.showPage()
            self.y = self._header()

    def text(self, s: str, size: float = 9.5, bold: bool = False, x: float | None = None,
             color=None, gap: float = 4.8 * mm):
        self.need(gap)
        self.c.setFillColor(color or black)
        self.c.setFont(FB if bold else F, size)
        self.c.drawString(x if x is not None else MARGIN, self.y, s)
        self.y -= gap

    def block(self, label: str, lines: list[str], x: float, y: float, width: float = 80 * mm) -> float:
        c = self.c
        c.setFillColor(GREY)
        c.setFont(FB, 7.5)
        c.drawString(x, y, label.upper())
        y -= 4.5 * mm
        c.setFillColor(black)
        c.setFont(F, 9.5)
        for ln in lines:
            for part in _wrap(ln, 52):
                c.drawString(x, y, part)
                y -= 4.4 * mm
        return y

    def table(self, cols: list[tuple[str, float, str]], rows: list[list[str]], swatches: list | None = None):
        """cols: (header, width fraction, align l|r|c)."""
        full = W - 2 * MARGIN
        xs = [MARGIN]
        for _, f, _ in cols:
            xs.append(xs[-1] + full * f)
        rh = 7.5 * mm

        def head():
            self.need(rh * 2)
            c = self.c
            c.setFillColor(self.b["dark"])
            c.rect(MARGIN, self.y - rh, full, rh, fill=1, stroke=0)
            c.setFillColor(white)
            c.setFont(FB, 8.5)
            for i, (h, _, al) in enumerate(cols):
                self._cell(h, xs[i], xs[i + 1], self.y - rh + 2.6 * mm, al)
            self.y -= rh

        head()
        for ri, row in enumerate(rows):
            if self.y - rh < FOOTER_Y + 8 * mm:
                self.c.showPage()
                self.y = self._header()
                head()
            c = self.c
            c.setStrokeColor(LINE)
            c.setLineWidth(0.5)
            c.line(MARGIN, self.y - rh, MARGIN + full, self.y - rh)
            c.setFillColor(black)
            c.setFont(F, 9)
            for i, (_, _, al) in enumerate(cols):
                x0 = xs[i]
                if i == 0 and swatches and swatches[ri]:
                    c.setFillColor(HexColor(swatches[ri]))
                    c.rect(x0 + 2 * mm, self.y - rh + 2.2 * mm, 3 * mm, 3 * mm, fill=1, stroke=0)
                    c.setFillColor(black)
                    x0 += 4 * mm
                self._cell(row[i], x0, xs[i + 1], self.y - rh + 2.6 * mm, al)
            self.y -= rh
        self.y -= 3 * mm

    def _cell(self, s: str, x0: float, x1: float, y: float, al: str):
        if al == "r":
            self.c.drawRightString(x1 - 2 * mm, y, s)
        elif al == "c":
            self.c.drawCentredString((x0 + x1) / 2, y, s)
        else:
            self.c.drawString(x0 + 2 * mm, y, s)

    def totals(self, rows: list[tuple[str, str, bool]]):
        self.need(len(rows) * 5.5 * mm + 2 * mm)
        x_label, x_val = W - MARGIN - 70 * mm, W - MARGIN - 2 * mm
        for label, val, bold in rows:
            self.c.setFillColor(black)
            self.c.setFont(FB if bold else F, 10.5 if bold else 9.5)
            self.c.drawString(x_label, self.y, label)
            self.c.drawRightString(x_val, self.y, val)
            self.y -= 5.5 * mm

    def qr(self, data: str, x: float, y: float, size: float = 30 * mm):
        w = QrCodeWidget(data)
        b = w.getBounds()
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
        d.add(w)
        renderPDF.draw(d, self.c, x, y)

    def done(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def _wrap(s: str, n: int) -> list[str]:
    out = []
    for para in (s or "").split("\n"):
        line = ""
        for word in para.split():
            if len(line) + len(word) + 1 > n and line:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return [x for x in out if x is not None]


def _names(cfg: dict):
    tanks = {t["code"]: t for t in cfg["tank_types"]}
    cols = {c["code"]: c for c in cfg.get("colours", []) or []}
    return tanks, cols


def _line_desc(ln: dict, cfg: dict, with_dims: bool = False) -> str:
    tanks, cols = _names(cfg)
    if ln.get("kind") == "charge":
        return ln.get("name") or ln.get("code", "")
    t = tanks.get(ln["tank_type"], {"name": ln["tank_type"]})
    s = t["name"]
    col = cols.get(ln.get("colour") or "")
    if col:
        s += f", {col['name']}"
    if ln.get("grade", "A") == "B":
        s += " (B grade)"
    if with_dims and t.get("diameter_mm"):
        s += f"  ·  Ø{t['diameter_mm']} × {t['height_mm']} mm"
    if ln.get("made_to_order"):
        s += "  ·  made to order"
    return s


def _customer_lines(customer: dict | None, name: str) -> list[str]:
    c = customer or {}
    out = [name]
    for k in ("contact_person", "billing_address", "phone", "email"):
        if c.get(k):
            out.append(c[k])
    if c.get("vat_number"):
        out.append(f"VAT {c['vat_number']}")
    return out


def _bank_lines(node: dict, reference: str) -> list[str]:
    bank = (_company(node).get("bank") or {})
    if not bank:
        return []
    return [f"{bank.get('bank', '')}  ·  {bank.get('account_name', '')}",
            f"Account {bank.get('account_number', '')}  ·  Branch {bank.get('branch_code', '')}"
            + (f"  ·  SWIFT {bank['swift']}" if bank.get("swift") else ""),
            f"Reference: {reference}"]


def _priced_rows(doc: dict, cfg: dict, dims: bool):
    rows, sw = [], []
    cols = _names(cfg)[1]
    for ln in doc["lines"]:
        qty = ln.get("qty", ln.get("quantity", 0))
        rows.append([_line_desc(ln, cfg, dims), f"{qty:g}", money(ln.get("unit_incl") if ln.get("price_includes_vat") else ln.get("unit_ex")),
                     money(ln.get("line_ex")), money(ln.get("line_total"))])
        sw.append((cols.get(ln.get("colour") or "") or {}).get("swatch_hex") if ln.get("kind", "tank") == "tank" else None)
    return rows, sw


PRICE_COLS = [("Description", 0.46, "l"), ("Qty", 0.08, "r"), ("Unit", 0.15, "r"),
              ("Excl VAT", 0.15, "r"), ("Total", 0.16, "r")]


def quote(node: dict, cfg: dict, q: dict, customer: dict | None, pay_link: str | None = None) -> bytes:
    rev = f"Revision {q['rev']}" if q.get("rev", 1) > 1 else ""
    proforma = bool(cfg.get("proforma"))
    d = Doc(node, "Pro forma invoice" if proforma else "Quotation", q["quote_number"], q["date"],
            ([] if proforma else [f"Valid until {q['valid_until']}"]) + ([rev] if rev else []))
    y0 = d.y
    yl = d.block("Bill to" if proforma else "Quote for", _customer_lines(customer, q["customer_name"]), MARGIN, y0)
    site = q.get("site") or {}
    yr = d.block("Delivery site", [x for x in (site.get("label"), site.get("address"), site.get("town")) if x], W / 2, y0) if site else y0
    d.y = min(yl, yr) - 4 * mm
    rows, sw = _priced_rows(q, cfg, True)
    d.table(PRICE_COLS, rows, sw)
    d.totals([("Subtotal excl VAT", money(q["subtotal"]), False),
              (f"VAT {q.get('vat_rate', 15):g}%", money(q["vat"]), False),
              ("Total incl VAT", money(q["total"]), True)])
    d.y -= 4 * mm
    d.text("Payment options", 10, True, color=_brand(node)["dark"])
    if pay_link:
        d.text(f"Pay by card: {pay_link}", 9)
    if q.get("pay_on_delivery_offered"):
        d.text("Pay on delivery: cash or card at the site.", 9)
    bank = _bank_lines(node, q["quote_number"])
    if bank:
        d.text(f"EFT, using {q['quote_number']} as the reference:" if proforma else "EFT on acceptance:", 9)
        for ln in bank:
            d.text(ln, 9, x=MARGIN + 4 * mm)
    if q.get("notes"):
        d.y -= 2 * mm
        d.text("Notes", 10, True)
        for part in _wrap(q["notes"], 95):
            d.text(part, 9)
    return d.done()


def invoice(node: dict, cfg: dict, inv: dict, customer: dict | None, pay_link: str | None = None) -> bytes:
    d = Doc(node, "Tax Invoice", inv["display_number"], inv["date"],
            [f"Due {inv['due_date']}"] + ([f"Order {inv['order_number']}"] if inv.get("order_number") else [])
            + ([f"Delivery {inv['dn_number']}"] if inv.get("dn_number") else []))
    y0 = d.y
    yl = d.block("Bill to", _customer_lines(customer, inv["customer_name"]), MARGIN, y0)
    co = _company(node)
    yr = d.block("From", [x for x in (co.get("legal_name"), co.get("reg_no") and f"Reg. No. {co['reg_no']}",
                                      co.get("vat_number") and f"VAT No. {co['vat_number']}", co.get("address")) if x],
                 W / 2, y0)
    d.y = min(yl, yr) - 4 * mm
    rows, sw = _priced_rows(inv, cfg, False)
    d.table(PRICE_COLS, rows, sw)
    d.totals([("Subtotal excl VAT", money(inv["subtotal"]), False),
              (f"VAT {inv.get('vat_rate', 15):g}%", money(inv["vat"]), False),
              ("Total incl VAT", money(inv["total"]), True),
              ("Paid", money(inv.get("amount_paid")), False),
              ("Amount due", money(inv.get("balance", inv["total"])), True)])
    # payment block directly under the totals
    d.y -= 3 * mm
    d.need(36 * mm)
    top = d.y
    d.text("How to pay", 10, True, color=_brand(node)["dark"])
    d.text(f"Amount due: {money(inv.get('balance', inv['total']))}", 9.5, True)
    if pay_link:
        d.text(f"Pay by card: {pay_link}", 9)
    for ln in _bank_lines(node, inv["display_number"]):
        d.text(ln, 9)
    if pay_link:
        d.qr(pay_link, W - MARGIN - 28 * mm, top - 30 * mm, 28 * mm)
    if inv.get("void"):
        d.c.saveState()
        d.c.setFillColor(HexColor("#d2232a"))
        d.c.setFont(FB, 60)
        d.c.translate(W / 2, H / 2)
        d.c.rotate(30)
        d.c.drawCentredString(0, 0, "VOID")
        d.c.restoreState()
    return d.done()


def delivery_note(node: dict, cfg: dict, dn: dict, ctx: dict | None = None) -> bytes:
    """No prices. Order number, site, contact, directions, a QR of the map link, the amount to
    collect on pay-on-delivery notes, and part-delivery numbering."""
    ctx = ctx or {}
    extra = []
    if dn.get("order_number"):
        extra.append(f"Order {dn['order_number']}")
    d = Doc(node, "Delivery Note", dn["dn_number"], dn.get("planned_date") or dn["date"], extra)
    if ctx.get("part_index"):
        d.text(f"Part delivery {ctx['part_index']} of order {dn.get('order_number')}"
               + (f"  ·  {ctx['to_come']} still to come" if ctx.get("to_come") else ""), 10, True,
               color=_brand(node)["primary"])
    site = dn.get("site") or {}
    y0 = d.y
    yl = d.block("Deliver to", [dn["client_name"]] + [x for x in (site.get("label"), site.get("address"), site.get("town")) if x]
                 + ([dn["client_details"]] if dn.get("client_details") and not site else []), MARGIN, y0)
    contact = [x for x in (site.get("contact_name"), site.get("contact_phone")) if x]
    yr = d.block("Site contact", [" ".join(contact)] if contact else ["—"], W / 2, y0)
    if site.get("directions"):
        yr = d.block("Directions", [site["directions"]], W / 2, yr - 2 * mm)
    link = maps.nav_link(site)
    if link:
        d.qr(link, W - MARGIN - 26 * mm, y0 - 30 * mm, 26 * mm)
        d.c.setFont(F, 7)
        d.c.setFillColor(GREY)
        d.c.drawCentredString(W - MARGIN - 13 * mm, y0 - 33 * mm, "Scan to navigate")
    d.y = min(yl, yr, y0 - 36 * mm if link else yl) - 4 * mm
    if dn.get("driver"):
        d.text(f"Driver: {dn['driver'].get('name', '')} {dn['driver'].get('phone', '')}"
               + (f"  ·  Vehicle {dn['vehicle_reg']}" if dn.get("vehicle_reg") else ""), 9)
    rows, sw = [], []
    cols = _names(cfg)[1]
    for ln in dn["lines"]:
        rows.append([_line_desc(ln, cfg, True), ln.get("grade", "A"), str(ln["quantity"])])
        sw.append((cols.get(ln.get("colour") or "") or {}).get("swatch_hex"))
    d.table([("Tank", 0.70, "l"), ("Grade", 0.12, "c"), ("Quantity", 0.18, "r")], rows, sw)
    if dn.get("collect_amount"):
        d.y -= 2 * mm
        d.text(f"Collect {money(dn['collect_amount'])} on delivery", 13, True, color=HexColor("#b4371b"))
    sig = FOOTER_Y + 22 * mm
    if d.y < sig + 8 * mm:
        d.c.showPage()
        d.y = d._header()
    c = d.c
    c.setFillColor(black)
    c.setFont(F, 10)
    c.drawString(MARGIN, sig, "Received by: _____________________________")
    c.drawString(MARGIN, sig - 8 * mm, "Signature:    _____________________________")
    c.drawRightString(W - MARGIN, sig, "Date: ______________")
    return d.done()


def statement(node: dict, cfg: dict, customer: dict, st: dict) -> bytes:
    d = Doc(node, "Statement", customer["name"], st["date"], [f"{st['from']} to {st['to']}"])
    d.y = d.block("Customer", _customer_lines(customer, customer["name"]), MARGIN, d.y) - 4 * mm
    d.text(f"Opening balance {money(st['opening'])}", 9.5, True)
    rows = [[r["date"], r["type"], r["ref"], money(r["debit"]) if r["debit"] else "",
             money(r["credit"]) if r["credit"] else "", money(r["balance"])] for r in st["rows"]]
    d.table([("Date", 0.14, "l"), ("Type", 0.14, "l"), ("Reference", 0.24, "l"), ("Invoiced", 0.16, "r"),
             ("Paid", 0.16, "r"), ("Balance", 0.16, "r")], rows)
    d.totals([("Closing balance", money(st["closing"]), True)])
    a = st["aging"]
    d.y -= 3 * mm
    d.table([("Current", 0.2, "r"), ("30 days", 0.2, "r"), ("60 days", 0.2, "r"), ("90+ days", 0.2, "r"), ("Total due", 0.2, "r")],
            [[money(a["current"]), money(a["d30"]), money(a["d60"]), money(a["d90"]), money(st["closing"])]])
    for ln in _bank_lines(node, customer["name"]):
        d.text(ln, 9)
    return d.done()


def capture_sheet(node: dict, cfg: dict) -> bytes:
    """The daily capture sheet with the node's own branding (print-friendly)."""
    d = Doc(node, "Daily Capture Sheet", "", "", [])
    d.text("Date: ______ / ______ / ____________", 10, True, gap=8 * mm)
    tanks, cols = _names(cfg)
    d.text("Powder", 11, True)
    d.table([("Powder grade", 0.4, "l"), ("Received today kg", 0.3, "c"), ("Issued to production kg", 0.3, "c")],
            [[""] * 3 for _ in range(5)])
    d.text("Tanks produced today", 11, True)
    d.table([("Tank type", 0.26, "l"), ("Colour", 0.28, "l"), ("A grade", 0.155, "c"), ("B grade", 0.155, "c"),
             ("Reject", 0.15, "c")], [[t["name"], "", "", "", ""] for t in cfg["tank_types"] if t.get("active", True)]
            + [[""] * 5 for _ in range(3)])
    d.text("Notes: ____________________________________________________________________________", 9)
    d.y -= 6 * mm
    d.text("Operator signature: _____________________________        Time: ______________", 10)
    return d.done()
