"""ReportLab PDFs: Daily Capture Sheet, Delivery Note, Invoice.

Brand: dark blue #022397 structural colour, Hakuna for headlines, Helvetica body
(Gotham/Montserrat substitute for print). Footer and terms blocks are anchored to
absolute vertical coordinates to prevent overflow.

PDFs are returned as bytes; the server stores them base64 in MongoDB and serves
them from /content endpoints (Cloudinary raw resources 401 on transformation).
"""
from __future__ import annotations

import os
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
DARK_BLUE = HexColor("#022397")
LIGHT_BLUE = HexColor("#00aeef")
YELLOW = HexColor("#dfbd25")
GREEN = HexColor("#41ad49")
GREY = HexColor("#666666")
LIGHT_GREY = HexColor("#e8eaf2")

LOGO_BLUE = os.path.join(ASSETS, "vezubunye_logo_darkblue_transparent.png")
LOGO_BLACK = os.path.join(ASSETS, "vezubunye_logo_black_transparent.png")

_fonts_registered = False


def _ensure_fonts():
    """Montserrat only (matches the de-brushed app). Falls back to Helvetica if missing."""
    global _fonts_registered
    if _fonts_registered:
        return
    reg = os.path.join(ASSETS, "Montserrat-Regular.ttf")
    bold = os.path.join(ASSETS, "Montserrat-Bold.ttf")
    if os.path.exists(reg):
        pdfmetrics.registerFont(TTFont("Montserrat", reg))
    if os.path.exists(bold):
        pdfmetrics.registerFont(TTFont("Montserrat-Bold", bold))
    _fonts_registered = True


def _font(bold: bool = False) -> str:
    _ensure_fonts()
    names = pdfmetrics.getRegisteredFontNames()
    if bold:
        return "Montserrat-Bold" if "Montserrat-Bold" in names else "Helvetica-Bold"
    return "Montserrat" if "Montserrat" in names else "Helvetica"


def _headline_font() -> str:
    return _font(bold=True)


W, H = A4  # 595 x 842 pt
MARGIN = 18 * mm
FOOTER_Y = 22 * mm  # absolute anchor: footer never moves, content never crosses it


def _header(c: Canvas, title: str, node_name: str, doc_no: str = "", date: str = ""):
    """Dark blue band with logo and document title."""
    band_h = 32 * mm
    c.setFillColor(DARK_BLUE)
    c.rect(0, H - band_h, W, band_h, fill=1, stroke=0)
    # logo: white-on-dark not available as print asset; use the blue logo on a white chip
    logo = LOGO_BLUE if os.path.exists(LOGO_BLUE) else None
    if logo:
        chip_w, chip_h = 34 * mm, 26 * mm
        c.setFillColor(white)
        c.roundRect(MARGIN, H - band_h + 3 * mm, chip_w, chip_h, 2 * mm, fill=1, stroke=0)
        c.drawImage(logo, MARGIN + 2 * mm, H - band_h + 4 * mm,
                    width=chip_w - 4 * mm, height=chip_h - 2 * mm,
                    preserveAspectRatio=True, mask="auto")
    c.setFillColor(white)
    c.setFont(_headline_font(), 22)
    c.drawRightString(W - MARGIN, H - 14 * mm, title)
    c.setFont(_font(), 9)
    c.drawRightString(W - MARGIN, H - 20 * mm, node_name)
    if doc_no:
        c.setFont(_font(bold=True), 10)
        c.drawRightString(W - MARGIN, H - 26 * mm, doc_no)
    if date:
        c.setFont(_font(), 9)
        c.drawRightString(W - MARGIN, H - 30 * mm, date)
    return H - band_h - 8 * mm  # content start y


def _footer(c: Canvas, note: str = ""):
    """Anchored at FOOTER_Y, absolute coordinates."""
    c.setStrokeColor(DARK_BLUE)
    c.setLineWidth(1.2)
    c.line(MARGIN, FOOTER_Y, W - MARGIN, FOOTER_Y)
    c.setFillColor(GREY)
    c.setFont(_font(), 7.5)
    c.drawString(MARGIN, FOOTER_Y - 4 * mm, "Vezubunye  •  Together, We Build")
    if note:
        c.drawRightString(W - MARGIN, FOOTER_Y - 4 * mm, note)


# ---------- Daily Capture Sheet ---------- #

HEADER_FILL = HexColor("#e8e8e8")   # light grey, prints clean in black & white
GRID = HexColor("#888888")


def _bw_header(c: Canvas, title: str, subtitle: str) -> float:
    """Print-friendly header: black logo + black title, a thin rule, no filled band."""
    logo = LOGO_BLACK if os.path.exists(LOGO_BLACK) else None
    if logo:
        lw = 28 * mm
        c.drawImage(logo, MARGIN, H - 4 * mm - lw / 1.37, width=lw, height=lw / 1.37,
                    preserveAspectRatio=True, mask="auto")
    c.setFillColor(black)
    c.setFont(_headline_font(), 22)
    c.drawRightString(W - MARGIN, H - 14 * mm, title)
    c.setFillColor(GREY)
    c.setFont(_font(), 10)
    c.drawRightString(W - MARGIN, H - 20 * mm, subtitle)
    c.setStrokeColor(black)
    c.setLineWidth(1)
    c.line(MARGIN, H - 27 * mm, W - MARGIN, H - 27 * mm)
    return H - 35 * mm


def _capture_table(c: Canvas, y: float, title: str, colour, headers: list[str],
                   fracs: list[float], rows: list[str], row_h: float = 7 * mm) -> float:
    """A titled table for the print-friendly sheet: black title, light-grey header band,
    thin grid. `colour` is accepted for call-site clarity but not used (black & white)."""
    full = W - 2 * MARGIN
    xs = [MARGIN]
    for f in fracs:
        xs.append(xs[-1] + full * f)
    c.setFillColor(black)
    c.setFont(_headline_font(), 12)
    c.drawString(MARGIN, y, title)
    y -= 4 * mm   # heading sits right above its table
    # header band: light grey fill, black text, black outline
    c.setFillColor(HEADER_FILL)
    c.rect(MARGIN, y - row_h, full, row_h, fill=1, stroke=0)
    c.setStrokeColor(black)
    c.setLineWidth(0.8)
    c.rect(MARGIN, y - row_h, full, row_h, fill=0)
    c.setFillColor(black)
    c.setFont(_font(bold=True), 8.5)
    for i, h in enumerate(headers):
        if i == 0:
            c.drawString(xs[0] + 2 * mm, y - row_h + 2.8 * mm, h)
        else:
            c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - row_h + 2.8 * mm, h)
    y -= row_h
    # rows (or two blank lines if no config rows yet)
    rows = rows or ["", ""]
    c.setFont(_font(), 9.5)
    for label in rows:
        c.setStrokeColor(GRID)
        c.setLineWidth(0.5)
        c.rect(MARGIN, y - row_h, full, row_h, fill=0)
        for x in xs[1:-1]:
            c.line(x, y - row_h, x, y)
        c.setFillColor(black)
        c.drawString(xs[0] + 2 * mm, y - row_h + 2.6 * mm, label)
        y -= row_h
    return y - 8 * mm   # clear space below the table before the next heading


def daily_capture_sheet(node: dict, cfg: dict) -> bytes:
    """Blank A4 sheet the on-site operator fills in by hand and prints daily.
    Print-friendly: black & white, no heavy colour fills. Movements only:
    powder, fittings, tanks moulded, booked, dispatched."""
    buf = BytesIO()
    c = Canvas(buf, pagesize=A4)
    c.setTitle("Vezubunye Daily Capture Sheet")
    c.setAuthor("Vezubunye")
    y = _bw_header(c, "Daily Capture Sheet", f"{node['name']} — {node['location']}")

    c.setFillColor(black)
    c.setFont(_font(bold=True), 10)
    c.drawString(MARGIN, y, "Date: ______ / ______ / ____________")
    y -= 9 * mm

    tanks = [t["name"] for t in cfg["tank_types"]]
    blank5 = [""] * 5   # powder grades vary day to day — leave open rows to write the grade in
    blank3 = [""] * 3

    y = _capture_table(c, y, "Powder", black,
                       ["Powder grade", "Received today (from Fenix)  kg", "Issued to production today  kg"],
                       [0.40, 0.30, 0.30], blank5)
    y = _capture_table(c, y, "Fittings", black,
                       ["Fitting", "Received today (from Fenix)  qty", "Issued to production today  qty"],
                       [0.40, 0.30, 0.30], blank3)
    y = _capture_table(c, y, "Tanks Produced Today", black,
                       ["Tank type", "Powder grade", "A Grade", "B Grade", "Reject"],
                       [0.26, 0.28, 0.155, 0.155, 0.15], tanks)
    y = _capture_table(c, y, "Tanks Dispatched Today", black,
                       ["Tank type", "A Grade", "B Grade", "Delivery note no."],
                       [0.30, 0.18, 0.18, 0.34], tanks)

    # notes line
    c.setFillColor(GREY)
    c.setFont(_font(), 9)
    c.drawString(MARGIN, y, "Notes: ______________________________________________________________________")

    # signature block anchored above the footer
    sig_y = FOOTER_Y + 12 * mm
    c.setFillColor(black)
    c.setFont(_font(), 10)
    c.drawString(MARGIN, sig_y, "Operator signature: _____________________________")
    c.drawRightString(W - MARGIN, sig_y, "Time: ______________")
    c.setFillColor(GREY)
    c.setFont(_font(), 7.5)
    c.drawString(MARGIN, sig_y - 5 * mm,
                 "Photograph this completed sheet and WhatsApp it before end of day. "
                 "Powder issued, less tanks moulded, must balance on the floor.")

    # print-friendly footer: black rule + grey text
    c.setStrokeColor(black)
    c.setLineWidth(0.8)
    c.line(MARGIN, FOOTER_Y, W - MARGIN, FOOTER_Y)
    c.setFillColor(GREY)
    c.setFont(_font(), 7.5)
    c.drawString(MARGIN, FOOTER_Y - 4 * mm, "Vezubunye  •  Together, We Build")
    c.showPage()
    c.save()
    return buf.getvalue()


# ---------- Delivery Note ---------- #

def delivery_note_pdf(node: dict, cfg: dict, dn: dict) -> bytes:
    buf = BytesIO()
    c = Canvas(buf, pagesize=A4)
    y = _header(c, "Delivery Note", f"{node['name']} — {node['location']}",
                dn["dn_number"], dn["date"])

    c.setFillColor(black)
    c.setFont(_font(bold=True), 10)
    c.drawString(MARGIN, y, "Deliver to:")
    c.setFont(_font(), 10)
    c.drawString(MARGIN + 25 * mm, y, dn["client_name"])
    y -= 5 * mm
    for line in (dn.get("client_details") or "").split("\n"):
        if line.strip():
            c.drawString(MARGIN + 25 * mm, y, line.strip())
            y -= 5 * mm
    y -= 6 * mm

    names = {t["code"]: t["name"] for t in cfg["tank_types"]}
    col_x = [MARGIN, MARGIN + 80 * mm, MARGIN + 120 * mm]
    row_h = 9 * mm
    c.setFillColor(DARK_BLUE)
    c.rect(MARGIN, y - row_h, W - 2 * MARGIN, row_h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(_font(bold=True), 9)
    c.drawString(col_x[0] + 3 * mm, y - row_h + 3 * mm, "Tank")
    c.drawString(col_x[1] + 3 * mm, y - row_h + 3 * mm, "Grade")
    c.drawString(col_x[2] + 3 * mm, y - row_h + 3 * mm, "Quantity")
    y -= row_h
    c.setFont(_font(), 10)
    for ln in dn["lines"]:
        c.setStrokeColor(LIGHT_GREY)
        c.setFillColor(black)
        c.rect(MARGIN, y - row_h, W - 2 * MARGIN, row_h, fill=0)
        c.drawString(col_x[0] + 3 * mm, y - row_h + 3 * mm, names.get(ln["tank_type"], ln["tank_type"]))
        c.drawString(col_x[1] + 3 * mm, y - row_h + 3 * mm, ln.get("grade", "A"))
        c.drawString(col_x[2] + 3 * mm, y - row_h + 3 * mm, str(ln["quantity"]))
        y -= row_h

    # signature block anchored absolute
    sig_y = FOOTER_Y + 22 * mm
    c.setFillColor(black)
    c.setFont(_font(), 10)
    c.drawString(MARGIN, sig_y, "Received by: _____________________________")
    c.drawString(MARGIN, sig_y - 8 * mm, "Signature:    _____________________________")
    c.drawRightString(W - MARGIN, sig_y, "Date: ______________")
    _footer(c, dn["dn_number"])
    c.showPage()
    c.save()
    return buf.getvalue()

