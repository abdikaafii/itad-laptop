"""Shared PDF building blocks (reportlab): colours, fonts, header band, tables, watermark, logo."""
import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Table, TableStyle

NAVY = colors.HexColor("#0B1530")
NAVY2 = colors.HexColor("#14275A")
BLUE = colors.HexColor("#2563EB")
CYAN = colors.HexColor("#22D3EE")
INK = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#D5DCE8")
SOFT = colors.HexColor("#F1F5FB")
GREEN = colors.HexColor("#15803D")
RED = colors.HexColor("#B91C1C")
AMBER = colors.HexColor("#B45309")

STATUS_COLORS = {"PASS": GREEN, "FAIL": RED, "WARN": AMBER, "NOT_TESTED": MUTED, "NOT_FITTED": MUTED}

_FONT, _FONT_B = "Helvetica", "Helvetica-Bold"
_registered = False


def fonts():
    """Use DejaVu (full Unicode, so customer names with accents print) if the system has it."""
    global _FONT, _FONT_B, _registered
    if not _registered:
        _registered = True
        for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
                     "/usr/share/fonts/TTF", "/Library/Fonts", "C:/Windows/Fonts"):
            reg, bold = os.path.join(base, "DejaVuSans.ttf"), os.path.join(base, "DejaVuSans-Bold.ttf")
            if os.path.exists(reg) and os.path.exists(bold):
                pdfmetrics.registerFont(TTFont("ITAD", reg))
                pdfmetrics.registerFont(TTFont("ITAD-Bold", bold))
                _FONT, _FONT_B = "ITAD", "ITAD-Bold"
                break
    return _FONT, _FONT_B


def style(name, size=9, bold=False, color=INK, leading=None, align=TA_LEFT):
    f, fb = fonts()
    return ParagraphStyle(name, fontName=fb if bold else f, fontSize=size, textColor=color,
                          leading=leading or size * 1.3, alignment=align)


def esc(text):
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def P(text, **kw):
    return Paragraph(esc(text), style("p%d" % id(kw), **kw))


def kv_table(rows, width, label_w=48 * mm, pad=4):
    """Two-column label/value table with light rules."""
    data = [[P(k, size=8.5, color=MUTED), P(v if v not in (None, "") else "-", size=9.5)]
            for k, v in rows]
    t = Table(data, colWidths=[label_w, width - label_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def data_table(header, rows, widths, status_col=None, font_size=8):
    """Generic grid with navy header; optional coloured status column."""
    head = [P(h, size=font_size, bold=True, color=colors.white) for h in header]
    body = []
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            if status_col is not None and i == status_col:
                key = str(c).replace(" ", "_").upper()
                cells.append(P(str(c).replace("_", " "), size=font_size, bold=True,
                               color=STATUS_COLORS.get(key, INK)))
            else:
                cells.append(P(c if c not in (None, "") else "-", size=font_size))
        body.append(cells)
    t = Table([head] + body, colWidths=widths, repeatRows=1)
    ts = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("GRID", (0, 0), (-1, -1), 0.3, LINE),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i in range(1, len(body) + 1):
        if i % 2 == 0:
            ts.append(("BACKGROUND", (0, i), (-1, i), SOFT))
    t.setStyle(TableStyle(ts))
    return t


def logo_path(upload_dir, company):
    if not company or not company["logo_file"]:
        return None
    path = os.path.join(upload_dir, "logos", os.path.basename(company["logo_file"]))
    return path if os.path.isfile(path) else None


def draw_page(canvas, doc, title, company_name, logo=None, watermark="", footer_note="", band=22 * mm):
    """Header band, footer, optional diagonal watermark. Used as onFirstPage/onLaterPages."""
    f, fb = fonts()
    w, h = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - band, w, band, stroke=0, fill=1)
    canvas.setFillColor(BLUE)
    canvas.rect(0, h - band - 1.2 * mm, w, 1.2 * mm, stroke=0, fill=1)
    x = 14 * mm
    if logo:
        try:
            canvas.drawImage(logo, x, h - band + 3 * mm, width=band * 1.6 - 6 * mm, height=band - 6 * mm,
                             preserveAspectRatio=True, mask="auto", anchor="w")
            x += band * 1.6
        except Exception:
            pass
    canvas.setFillColor(colors.white)
    canvas.setFont(fb, 13)
    canvas.drawString(x, h - band / 2 - 1 * mm, company_name[:60])
    canvas.setFont(f, 8.5)
    canvas.setFillColor(CYAN)
    canvas.drawRightString(w - 14 * mm, h - band / 2 - 1 * mm, title)
    canvas.setStrokeColor(LINE)
    canvas.line(14 * mm, 12 * mm, w - 14 * mm, 12 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(f, 7)
    canvas.drawString(14 * mm, 8 * mm, footer_note[:118])
    canvas.drawRightString(w - 14 * mm, 8 * mm, "Page %d" % doc.page)
    if watermark:
        canvas.setFillColor(colors.Color(0.75, 0.1, 0.1, alpha=0.13))
        canvas.setFont(fb, 74)
        canvas.translate(w / 2, h / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, watermark)
    canvas.restoreState()


def build(story, title, company_name, logo=None, watermark="", footer_note="", landscape_mode=False):
    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    size = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=32 * mm, bottomMargin=18 * mm, title=title, author=company_name)

    def page(canvas, d):
        draw_page(canvas, d, title, company_name, logo, watermark, footer_note)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
