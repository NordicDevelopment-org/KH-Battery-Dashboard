"""PDF certificate generation (reportlab).

unit_certificate()  - one battery, with discharge curves
batch_certificate() - one customer job/lot, every PASS battery in a table
"""

from datetime import datetime
from io import BytesIO
from typing import Optional

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .config import BenchConfig

INK = colors.HexColor("#1b2430")
MUTED = colors.HexColor("#5b6675")
ACCENT = colors.HexColor("#0b5cad")
RULE = colors.HexColor("#c9d1db")
PASS_GREEN = colors.HexColor("#1e7b34")
FAIL_RED = colors.HexColor("#b3261e")
ZEBRA = colors.HexColor("#f3f6f9")

ss = getSampleStyleSheet()
S = {
    "company": ParagraphStyle("company", parent=ss["Normal"], fontName="Helvetica-Bold",
                              fontSize=16, textColor=INK, leading=19),
    "addr": ParagraphStyle("addr", parent=ss["Normal"], fontSize=8, textColor=MUTED,
                           alignment=TA_RIGHT, leading=10),
    "title": ParagraphStyle("title", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=18, textColor=ACCENT, leading=22, spaceBefore=6),
    "sub": ParagraphStyle("sub", parent=ss["Normal"], fontSize=10.5, textColor=MUTED, leading=13),
    "h": ParagraphStyle("h", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=10,
                        textColor=INK, spaceBefore=10, spaceAfter=4),
    "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, textColor=INK, leading=13),
    "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=7.5, textColor=MUTED, leading=9.5),
    "cell": ParagraphStyle("cell", parent=ss["Normal"], fontSize=8, textColor=INK, leading=10),
}


def _fmt_dt(iso: Optional[str]) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")


def _f(v, nd=2, unit="") -> str:
    return "-" if v is None else f"{v:.{nd}f}{unit}"


def _header(cfg: BenchConfig, cert_no: str, issued: str) -> list:
    c = cfg.company
    addr = "<br/>".join(x for x in [c.address.replace("\n", "<br/>"), c.phone] if x)
    top = Table([[Paragraph(c.name, S["company"]), Paragraph(addr, S["addr"])]],
                colWidths=[4 * inch, 3.5 * inch])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LINEBELOW", (0, 0), (-1, 0), 1.5, ACCENT),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    meta = Table([["Certificate No.", cert_no], ["Date Issued", issued]],
                 colWidths=[1.1 * inch, 1.6 * inch])
    meta.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica", 8),
                              ("FONT", (1, 0), (1, -1), "Helvetica-Bold", 9),
                              ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                              ("BOX", (0, 0), (-1, -1), 0.75, RULE),
                              ("INNERGRID", (0, 0), (-1, -1), 0.5, RULE)]))
    titles = [Paragraph(c.cert_title, S["title"]), Paragraph(c.cert_subtitle, S["sub"])]
    row = Table([[titles, meta]], colWidths=[4.8 * inch, 2.7 * inch])
    row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return [top, Spacer(1, 4), row, Spacer(1, 10)]


def _kv_grid(pairs: list[tuple[str, str]], cols: int = 2) -> Table:
    rows, row = [], []
    for k, v in pairs:
        row += [k, Paragraph(str(v) if v not in (None, "") else "-", S["cell"])]
        if len(row) == cols * 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row + [""] * (cols * 2 - len(row)))
    w = 7.5 * inch / cols
    t = Table(rows, colWidths=[w * 0.38, w * 0.62] * cols)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8),
                           *[("TEXTCOLOR", (c * 2, 0), (c * 2, -1), MUTED) for c in range(cols)],
                           *[("FONT", (c * 2, 0), (c * 2, -1), "Helvetica", 8) for c in range(cols)],
                           ("BOX", (0, 0), (-1, -1), 0.75, RULE),
                           ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _signatures(operator: str = "") -> KeepTogether:
    line = "_" * 34
    t = Table([
        ["Tested by", "Signature", "Date"],
        [operator or line, line, "_" * 16],
        ["Approved by (QA)", "Signature", "Date"],
        [line, line, "_" * 16],
    ], colWidths=[2.6 * inch, 2.9 * inch, 2.0 * inch], rowHeights=[12, 26, 22, 26])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica", 7.5),
                           ("FONT", (0, 2), (-1, 2), "Helvetica", 7.5),
                           ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                           ("TEXTCOLOR", (0, 2), (-1, 2), MUTED),
                           ("FONT", (0, 1), (-1, 1), "Helvetica", 9),
                           ("FONT", (0, 3), (-1, 3), "Helvetica", 9),
                           ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([Paragraph("Authorization", S["h"]), t])


def _footer_cb(cfg: BenchConfig, cert_no: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.5 * inch, 0.4 * inch,
                          f"{cfg.company.name}  |  {cert_no}  |  {cfg.company.cert_footer}".rstrip(" |"))
        canvas.drawRightString(8.0 * inch, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _doc(buf: BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(buf, pagesize=letter, title=title, author="KH Battery Dashboard",
                             # frames pad 6pt; offset so content is exactly 7.5in wide
                             leftMargin=0.5 * inch - 6, rightMargin=0.5 * inch - 6,
                             topMargin=0.5 * inch, bottomMargin=0.7 * inch)


def _statement(cfg: BenchConfig, target_soc: float) -> Paragraph:
    return Paragraph(cfg.company.cert_statement.format(target_soc=f"{target_soc:g}"), S["body"])


def _chart(samples: list[dict], key: str, label: str, color, w: float, h: float) -> Drawing:
    d = Drawing(w, h)
    pts = [(s["t_s"] / 60.0, s[key]) for s in samples if s[key] is not None]
    if len(pts) < 2:
        d.add(String(w / 2, h / 2, "no data", textAnchor="middle", fontSize=8, fillColor=MUTED))
        return d
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 36, 22, w - 46, h - 40
    lp.data = [pts]
    lp.lines[0].strokeColor = color
    lp.lines[0].strokeWidth = 1.4
    for ax in (lp.xValueAxis, lp.yValueAxis):
        ax.labels.fontSize = 7
        ax.labels.fillColor = MUTED
        ax.strokeColor = RULE
    lp.xValueAxis.valueMin = 0
    lp.yValueAxis.gridStrokeColor = colors.HexColor("#e6eaef")
    lp.yValueAxis.visibleGrid = True
    d.add(lp)
    d.add(String(36, h - 10, label, fontSize=8, fontName="Helvetica-Bold", fillColor=INK))
    d.add(String(w - 10, 4, "minutes", fontSize=7, fillColor=MUTED, textAnchor="end"))
    return d


def unit_certificate(cfg: BenchConfig, run: dict, samples: list[dict],
                     job: Optional[dict] = None) -> bytes:
    buf = BytesIO()
    cert_no = run.get("cert_no") or f"DRAFT-RUN{run['id']}"
    doc = _doc(buf, f"SOC Certificate {run['serial']}")
    passed = run.get("result") == "PASS"
    story = _header(cfg, cert_no, _fmt_dt(run.get("ended_at")))

    if not passed:
        story += [Paragraph(f"<font color='#b3261e'><b>NOT CERTIFIED - result: "
                            f"{run.get('result') or run.get('status')}. "
                            f"{run.get('fail_reason') or ''}</b></font>", S["body"]), Spacer(1, 6)]

    story += [_kv_grid([
        ("Customer", job["customer"] if job else ""),
        ("Serial Number", f"<b>{run['serial']}</b>"),
        ("PO Number", job["po_number"] if job else ""),
        ("Model", run.get("model") or run["profile"]),
        ("Lot / Batch", job["lot"] if job else ""),
        ("Chemistry / Config", f"LiFePO4, {run['cells_series']}S, "
                               f"{run['cells_series'] * 3.2:.1f} V nominal"),
    ]), Spacer(1, 10), _statement(cfg, run["target_soc"])]

    result_color = "#1e7b34" if passed else "#b3261e"
    story += [Paragraph("Test Results", S["h"]), _kv_grid([
        ("Rated Capacity", _f(run["rated_ah"], 1, " Ah")),
        ("Result", f"<font color='{result_color}'><b>{run.get('result') or '-'}</b></font>"),
        ("Starting SOC (assumed)", _f(run["start_soc"], 0, " %")),
        ("Final SOC", f"<b>{_f(run.get('final_soc'), 1, ' %')}</b>"),
        ("Target SOC", f"&le; {_f(run['target_soc'], 0, ' %')}"),
        ("Rested OCV", _f(run.get("ocv_v"), 2, " V")),
        ("Capacity Removed", _f(run.get("ah_removed"), 2, " Ah")),
        ("Energy Removed", _f(run.get("wh_removed"), 1, " Wh")),
        ("Starting Voltage", _f(run.get("start_v"), 2, " V")),
        ("Max Temperature", _f(run.get("max_temp_c"), 1, " &deg;C")),
        ("Discharge Start", _fmt_dt(run.get("started_at"))),
        ("Discharge End", _fmt_dt(run.get("discharge_end_at"))),
    ])]

    if samples:
        w = 3.7 * inch
        charts = Table([[
            _chart(samples, "voltage", "Battery Voltage (V)", ACCENT, w, 1.9 * inch),
            _chart(samples, "soc", "State of Charge (%)", PASS_GREEN, w, 1.9 * inch),
        ]], colWidths=[3.75 * inch] * 2)
        charts.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += [Paragraph("Discharge Record", S["h"]), charts]

    story += [Paragraph("Method &amp; Equipment", S["h"]), Paragraph(
        f"Constant-current discharge at {_f(run['discharge_a'], 1, ' A')} on bench channel "
        f"{run['channel']}. Amp-hours integrated from measured current; SOC = start SOC - "
        f"Ah removed / rated Ah. Load switched off at target and battery rested before "
        f"open-circuit voltage was recorded. Instrument: {run.get('instrument') or '-'}.",
        S["small"]), Spacer(1, 6), _signatures(run.get("operator") or "")]

    doc.build(story, onFirstPage=_footer_cb(cfg, cert_no), onLaterPages=_footer_cb(cfg, cert_no))
    return buf.getvalue()


def batch_certificate(cfg: BenchConfig, job: dict, runs: list[dict], cert_no: str) -> bytes:
    """Certificate for a whole customer job. Only PASS runs are certified."""
    buf = BytesIO()
    doc = _doc(buf, f"SOC Certificate {job['customer']} {job.get('lot') or ''}")
    # latest PASS per serial
    latest: dict[str, dict] = {}
    for r in sorted(runs, key=lambda r: r["id"]):
        if r.get("result") == "PASS":
            latest[r["serial"]] = r
    passed = sorted(latest.values(), key=lambda r: r["serial"])
    target = max((r["target_soc"] for r in passed), default=30)
    models = sorted({r.get("model") or r["profile"] for r in passed})
    dates = [r["ended_at"] for r in passed if r.get("ended_at")]

    story = _header(cfg, cert_no, datetime.now().strftime("%Y-%m-%d %H:%M"))
    story += [_kv_grid([
        ("Customer", job["customer"]),
        ("Quantity Certified", f"<b>{len(passed)}</b>"),
        ("PO Number", job.get("po_number")),
        ("Model(s)", ", ".join(models)),
        ("Lot / Batch", job.get("lot")),
        ("Test Dates", f"{_fmt_dt(min(dates))[:10]} to {_fmt_dt(max(dates))[:10]}" if dates else "-"),
    ]), Spacer(1, 10), _statement(cfg, target), Paragraph("Certified Units", S["h"])]

    head = ["#", "Serial Number", "Model", "Rated\nAh", "Start\nV", "Ah\nRemoved",
            "Final\nSOC %", "Rested\nOCV V", "Tested", "Result"]
    rows = [head] + [[
        str(n), r["serial"], Paragraph(r.get("model") or r["profile"], S["cell"]),
        _f(r["rated_ah"], 0), _f(r.get("start_v")), _f(r.get("ah_removed")),
        _f(r.get("final_soc"), 1), _f(r.get("ocv_v")), _fmt_dt(r.get("ended_at")), "PASS",
    ] for n, r in enumerate(passed, 1)]
    t = Table(rows, repeatRows=1, hAlign="LEFT",
              colWidths=[w * inch for w in (0.3, 1.35, 1.35, 0.5, 0.55, 0.65, 0.6, 0.6, 1.05, 0.55)])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ("TEXTCOLOR", (-1, 1), (-1, -1), PASS_GREEN),
        ("FONT", (-1, 1), (-1, -1), "Helvetica-Bold", 8),
        ("ALIGN", (3, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.75, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
    ]))
    story += [t]
    if not passed:
        story += [Paragraph("<font color='#b3261e'><b>No passing units in this job.</b></font>",
                            S["body"])]
    excluded = sorted({r["serial"] for r in runs if r["serial"] not in latest})
    if excluded:
        story += [Spacer(1, 4), Paragraph(
            f"<b>Excluded (did not pass, not certified):</b> {', '.join(excluded)}", S["small"])]

    story += [Paragraph("Method", S["h"]), Paragraph(
        "Each battery was discharged at constant current from a fully charged state on a "
        "programmable electronic load. Amp-hours removed were integrated from measured current; "
        "the load was switched off when the calculated SOC reached the target, and the battery "
        "was rested before open-circuit voltage was recorded. Individual discharge records are "
        "retained and available on request.", S["small"]),
        Spacer(1, 6), _signatures(", ".join(sorted({r["operator"] for r in passed if r.get("operator")})))]

    doc.build(story, onFirstPage=_footer_cb(cfg, cert_no), onLaterPages=_footer_cb(cfg, cert_no))
    return buf.getvalue()
