"""The certificate PDF."""
import json

from flask import current_app
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

from ..core import pdfkit as K


def _qr(url, size=34 * mm):
    widget = QrCodeWidget(url)
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    return d


def render_certificate_pdf(cert, company):
    c = json.loads(cert["content_json"])
    url = "%s/verify/%s" % (current_app.config["PUBLIC_URL"], cert["number"])
    width = 182 * mm
    story = [
        K.P(c["title"], size=22, bold=True, color=K.NAVY, leading=26),
        Spacer(1, 2 * mm),
        K.P("Certificate number  %s" % cert["number"], size=12, bold=True, color=K.BLUE),
        K.P("Issued %s UTC" % cert["issued_at"], size=8.5, color=K.MUTED),
        Spacer(1, 5 * mm),
        K.P("This certifies that the storage device described below was sanitised and that the "
            "result was verified, as recorded by the ITAD erasure engine and signed on the "
            "processing station.", size=9.5, color=K.INK, leading=13),
        Spacer(1, 5 * mm),
    ]

    def section(title, rows):
        return KeepTogether([K.P(title.upper(), size=8, bold=True, color=K.BLUE), Spacer(1, 1 * mm),
                             K.kv_table(rows, width), Spacer(1, 4 * mm)])

    a, d, s, e = c["asset"], c["drive"], c["sanitisation"], c["erasure"]
    story += [
        section("Customer and job", [("Customer", c["customer"]), ("Job reference", c["job_ref"]),
                                     ("Processing location", c["location"])]),
        section("Device", [("Manufacturer", a["manufacturer"]), ("Model", a["model"]),
                           ("Serial number", a["serial"]), ("Asset ID", a["id"])]),
        section("Storage device", [("Drive", d["model"]), ("Drive serial", d["serial"]),
                                   ("Capacity", "%s GB" % d["capacity_gb"] if d["capacity_gb"] else ""),
                                   ("Interface path", d["path"])]),
        section("Sanitisation", [("Method", s["method"]), ("NIST SP 800-88 level", s["level"]),
                                 ("Standard reference", s["standard"]),
                                 ("Erasure date and time",
                                  "%s  to  %s" % (e["started"], e["finished"])),
                                 ("Erasure reference", e["id"])]),
        section("Verification", [("Verification status",
                                  "VERIFIED - %s" % e["status"] if e["verified"] else e["status"]),
                                 ("Technician", c["technician"]),
                                 ("Report signature", c["source_report"]["signature"].upper()
                                  + "  (key %s)" % c["source_report"]["key_id"]),
                                 ("Source report fingerprint", c["source_report"]["sha256"])]),
    ]

    qr = Table([[_qr(url),
                 [K.P("Verify this certificate", size=10, bold=True, color=K.NAVY),
                  K.P("Scan the code or open the address below. The page confirms the certificate "
                      "number is genuine and has not been altered or withdrawn.", size=8.5,
                      color=K.MUTED, leading=11),
                  Spacer(1, 2 * mm), K.P(url, size=8.5, bold=True, color=K.BLUE)]]],
               colWidths=[38 * mm, width - 38 * mm])
    qr.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("BOX", (0, 0), (-1, -1), 0.6, K.LINE),
                            ("BACKGROUND", (0, 0), (-1, -1), K.SOFT),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [qr, Spacer(1, 4 * mm)]
    co = c["company"]
    contact = "  |  ".join(x for x in (co["legal_name"] or co["name"], co["address"], co["email"],
                                       co["phone"], co["website"]) if x)
    story.append(K.P(contact, size=8, color=K.MUTED, leading=11))
    if company and company["certificate_footer"]:
        story += [Spacer(1, 2 * mm), K.P(company["certificate_footer"], size=8, color=K.MUTED, leading=11)]

    watermark = "REVOKED" if cert["revoked"] else ("DEMONSTRATION" if cert["is_demo"] else "")
    return K.build(story, "Certificate of Data Erasure", co["name"],
                   logo=K.logo_path(current_app.config["UPLOAD_DIR"], company), watermark=watermark,
                   footer_note="%s  |  %s%s" % (cert["number"], url,
                                                "  |  DEMONSTRATION RECORD - NOT A REAL ERASURE"
                                                if cert["is_demo"] else ""))
