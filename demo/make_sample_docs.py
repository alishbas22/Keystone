"""Generate the sample company dataset (KAN-35) into demo/data/<Business function>/.

Fictional company: Brightline Furnishings Ltd (office furniture, Manchester). 30 documents in
PDF, Word, Excel, PowerPoint, email, Markdown, CSV and text, filed by business function (the
folder connector tags them from the folder name). File names start with the document date.

Planted contradictions (expected outcomes in demo/ground_truth.yaml):
  1. time            Sara Khan (2025 plan) vs Bilal Ahmed (2026 memo) manage Harbor Hotels Group
  2. time/opposites  Harbor Hotels likes (2025) vs dislikes (2026) the Ergo Desk Chair
  3. authority       same day: shipping contract (DHL Freight) vs email (UPS) carry SHP-0077
  4. corroboration   same day: two emails (Bilal) vs one email (Sara) manage Greenleaf Clinics
  5. disputed        same day: Crestview Bank's parent is Northstar Holdings vs Meridian Group

    uv run python demo/make_sample_docs.py
"""

import csv
import shutil
import sqlite3
from email.message import EmailMessage
from pathlib import Path

from docx import Document as WordDocument
from fpdf import FPDF
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches, Pt

OUT = Path(__file__).parent / "data"
COMPANY = "Brightline Furnishings Ltd"


# --- Format helpers -------------------------------------------------------------------------------


def pdf(path: Path, title: str, sections: dict[str, str]) -> None:
    doc = FPDF()
    doc.add_page()
    doc.set_font("Helvetica", "B", 16)
    doc.multi_cell(0, 9, title)
    for heading, text in sections.items():
        doc.ln(3)
        doc.set_font("Helvetica", "B", 13)
        doc.cell(0, 8, heading, new_x="LMARGIN", new_y="NEXT")
        doc.set_font("Helvetica", "", 11)
        doc.multi_cell(0, 6, text)
    doc.output(str(path))


def docx(path: Path, title: str, intro: str, sections: dict[str, str]) -> None:
    doc = WordDocument()
    doc.add_heading(title, level=1)
    if intro:
        doc.add_paragraph(intro)
    for heading, text in sections.items():
        doc.add_heading(heading, level=2)
        doc.add_paragraph(text)
    doc.save(str(path))


def xlsx(path: Path, sheet: str, rows: list[tuple[object, ...]]) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sheet
    for row in rows:
        ws.append(row)
    wb.save(str(path))


def pptx(path: Path, slides: list[tuple[str, str]]) -> None:
    prs = Presentation()
    for title, body in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        assert slide.shapes.title is not None
        slide.shapes.title.text = title
        box = slide.shapes.add_textbox(Inches(0.7), Inches(1.8), Inches(8.6), Inches(4))
        box.text_frame.word_wrap = True
        box.text_frame.text = body
        for paragraph in box.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(18)
    prs.save(str(path))


def eml(path: Path, sender: str, to: str, date: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Date"], msg["Subject"] = sender, to, date, subject
    msg.set_content(body)
    path.write_bytes(bytes(msg))


def text(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def table(path: Path, rows: list[tuple[object, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


# --- The documents ----------------------------------------------------------------------------------


def hr(d: Path) -> None:
    pdf(d / "2025-01-15_company_overview.pdf", f"{COMPANY} - Company Overview", {
        "About us": f"{COMPANY} designs and sells office furniture to businesses. The company is "
        "headquartered in Manchester, United Kingdom. This overview was written in January 2025.",
        "Leadership": "Amina Yusuf is the Chief Executive Officer of Brightline Furnishings. Hina Raza "
        "is the Chief Financial Officer and head of the Finance department. Omar Siddiqui is the Head "
        "of Sales and leads the Sales team.",
        "Teams": "The Sales team and the Finance department are units of Brightline Furnishings. Sara "
        "Khan and Bilal Ahmed are Account Managers in the Sales team; both report to Omar Siddiqui. "
        "Usman Tariq is a Financial Analyst in the Finance department and reports to Hina Raza.",
    })  # fmt: skip
    text(
        d / "2025-11-20_hr_org_update.md",
        """
# HR update: Finance department

## New hire
Leila Shah joined Brightline Furnishings on 17 November 2025 as a Credit Controller in the Finance
department. Leila Shah reports to Hina Raza.

## Reporting lines
Usman Tariq continues to report to Hina Raza.
""",
    )
    eml(d / "2026-09-20_sales_team_update.eml", "Omar Siddiqui <omar@brightline.example>",
        "all-staff@brightline.example", "Sun, 20 Sep 2026 10:00:00 +0100", "Sales team update",
        "Hello all,\n\nSara Khan now leads the new Retail Partners team. The Retail Partners team is "
        "a unit of the Sales team. Sara Khan reports to Omar Siddiqui.\n\nOmar")  # fmt: skip
    text(
        d / "2026-03-15_team_chat_export.txt",
        """
Team chat export, 15 March 2026
Omar Siddiqui: Great work on the Crestview Bank renewal, Bilal.
Bilal Ahmed: Thanks! Crestview Bank renewed for another year.
Hina Raza: Finance will issue the renewal invoice next week.
""",
    )


def sales(d: Path) -> None:
    docx(d / "2025-03-10_harbor_hotels_account_plan.docx", "Account Plan: Harbor Hotels Group",
         "Prepared by the Sales team, 10 March 2025.", {
        "Overview": "Harbor Hotels Group is Brightline Furnishings' biggest customer, with annual "
        "purchases of about GBP 480,000. Harbor Hotels Group operates 40 hotels across the UK.",
        "Account team": "Sara Khan is the account manager for Harbor Hotels Group. She manages the "
        "account and is the main contact for all orders. The customer's main contact is Daniel Price, "
        "Procurement Director at Harbor Hotels Group.",
        "Products": "Harbor Hotels Group buys the Ergo Desk Chair (SKU BF-CH-100) and the Oak "
        "Standing Desk (SKU BF-DK-220).",
    })  # fmt: skip
    xlsx(d / "2025-06-30_q2_customer_report.xlsx", "Top customers Q2 2025", [
        ("Customer", "Annual revenue (GBP)", "Account manager", "Industry"),
        ("Harbor Hotels Group", 480000, "Sara Khan", "Hospitality"),
        ("Crestview Bank", 210000, "Bilal Ahmed", "Banking"),
        ("Greenleaf Clinics", 95000, "Bilal Ahmed", "Healthcare"),
    ])  # fmt: skip
    docx(d / "2026-09-15_account_reassignment_memo.docx", "Memo: Account manager changes",
         "From: Omar Siddiqui, Head of Sales. Date: 15 September 2026.", {
        "Harbor Hotels Group": "Effective 1 September 2026, Bilal Ahmed is the account manager for "
        "Harbor Hotels Group and manages the account. Please send all Harbor Hotels orders to Bilal.",
        "Retail Partners team": "Sara Khan now leads the new Retail Partners team, a unit of the Sales team.",
    })  # fmt: skip
    eml(d / "2025-04-02_harbor_feedback.eml", "Daniel Price <daniel.price@harborhotels.example>",
        "sara.khan@brightline.example", "Wed, 2 Apr 2025 09:15:00 +0100", "Feedback on the chairs",
        "Hi Sara,\n\nHarbor Hotels Group likes the Ergo Desk Chair very much. Our staff find it "
        "comfortable and we plan to order more.\n\nDaniel Price\nProcurement Director, Harbor Hotels Group")  # fmt: skip
    eml(d / "2026-05-20_harbor_complaint.eml", "Daniel Price <daniel.price@harborhotels.example>",
        "support@brightline.example", "Wed, 20 May 2026 16:40:00 +0100", "Complaint: Ergo Desk Chair",
        "Hello,\n\nAfter repeated broken armrests, Harbor Hotels Group now dislikes the Ergo Desk "
        "Chair and will not order it again until the quality problem is fixed.\n\nDaniel Price")  # fmt: skip
    for suffix, manager, writer in [("a", "Bilal Ahmed", "Omar Siddiqui"), ("b", "Bilal Ahmed", "Hina Raza"),
                                    ("note", "Sara Khan", "Usman Tariq")]:  # fmt: skip
        name = "greenleaf_note" if suffix == "note" else f"greenleaf_handover_{suffix}"
        eml(d / f"2026-04-10_{name}.eml", f"{writer} <{writer.split()[0].lower()}@brightline.example>",
            "sales@brightline.example", "Fri, 10 Apr 2026 11:00:00 +0100", "Greenleaf Clinics account",
            f"Hi team,\n\n{manager} is the account manager for Greenleaf Clinics and manages the account.\n\n{writer}")  # fmt: skip
    docx(d / "2025-08-12_purchase_order_po_1042.docx", "Purchase Order PO-1042",
         "Order date: 12 August 2025.", {
        "Buyer and seller": "Order PO-1042 was placed by Harbor Hotels Group and is sold by Brightline Furnishings.",
        "Items": "Order PO-1042 contains 200 Ergo Desk Chairs and 50 Oak Standing Desks. Total GBP 120,000.",
    })  # fmt: skip
    xlsx(d / "2026-06-30_h1_2026_sales_report.xlsx", "H1 2026 customers", [
        ("Customer", "H1 2026 revenue (GBP)", "Segment"),
        ("Harbor Hotels Group", 230000, "Hospitality chains"),
        ("Crestview Bank", 120000, "Financial services"),
        ("Greenleaf Clinics", 60000, "Healthcare"),
        ("Pinecrest Schools", 40000, "Education"),
    ])  # fmt: skip
    pdf(d / "2026-08-01_greenleaf_supply_contract.pdf", "Supply Contract: Greenleaf Clinics", {
        "Parties": "This supply agreement is between Brightline Furnishings Ltd and Greenleaf Clinics. "
        "Greenleaf Clinics is a customer of Brightline Furnishings from 1 August 2026.",
        "Products": "Brightline Furnishings supplies the Oak Standing Desk to Greenleaf Clinics.",
    })  # fmt: skip
    text(
        d / "2025-12-05_customer_segments.md",
        """
# Customer segments 2026

## Hospitality chains
Harbor Hotels Group is in the Hospitality chains customer segment.

## Healthcare
Greenleaf Clinics is in the Healthcare customer segment.

## Financial services
Crestview Bank is in the Financial services customer segment.
""",
    )


def finance(d: Path) -> None:
    pptx(d / "2025-09-02_q3_finance_review.pptx", [
        ("Q3 2025 Finance Review", "Brightline Furnishings Ltd. Prepared by Hina Raza, CFO."),
        ("Invoices", "Invoice INV-2025-031 was issued by Brightline Furnishings to Harbor Hotels Group "
         "for order PO-1042. Total GBP 120,000, due 1 October 2025.\nInvoice INV-2025-034 to "
         "Crestview Bank, total GBP 45,000, was paid in full."),
        ("Order PO-1042", "Order PO-1042 was placed by Harbor Hotels Group on 12 August 2025 and "
         "contains 200 Ergo Desk Chairs and 50 Oak Standing Desks."),
    ])  # fmt: skip
    for suffix, parent, writer in [
        ("a", "Northstar Holdings", "Hina Raza"),
        ("b", "Meridian Group", "Usman Tariq"),
    ]:
        eml(d / f"2026-02-14_crestview_parent_{suffix}.eml",
            f"{writer} <{writer.split()[0].lower()}@brightline.example>", "finance@brightline.example",
            "Sat, 14 Feb 2026 12:00:00 +0000", "Crestview Bank ownership",
            f"Hi,\n\nFor our credit checks: Crestview Bank is a subsidiary of {parent}.\n\n{writer}")  # fmt: skip
    text(
        d / "2026-01-08_expense_policy.md",
        """
# Expense policy 2026

## Approvals
Hina Raza, Chief Financial Officer, approves all expenses above GBP 5,000.

## Travel
Staff book travel through the Finance department.
""",
    )
    pdf(d / "2026-10-01_invoice_inv_2026_118.pdf", "Invoice INV-2026-118", {
        "Details": "Invoice INV-2026-118 was issued by Brightline Furnishings to Greenleaf Clinics on "
        "1 October 2026. Total GBP 18,500, payment due 31 October 2026.",
    })  # fmt: skip
    pptx(d / "2026-11-02_q3_2026_finance_review.pptx", [
        ("Q3 2026 Finance Review", "Brightline Furnishings Ltd. Prepared by Hina Raza, CFO."),
        ("Invoices", "Invoice INV-2026-118 to Greenleaf Clinics is due on 31 October 2026.\n"
         "Leila Shah, Credit Controller, follows up on overdue invoices."),
    ])  # fmt: skip
    docx(d / "2026-07-15_board_minutes.docx", "Board meeting minutes, 15 July 2026", "", {
        "Attendees": "Amina Yusuf (CEO), Hina Raza (CFO) and Omar Siddiqui (Head of Sales) attended the board meeting.",
        "Customers": "Pinecrest Schools became a customer of Brightline Furnishings in June 2026.",
    })  # fmt: skip


def operations(d: Path) -> None:
    pdf(d / "2026-03-02_shipping_agreement_dhl.pdf", "Signed shipping agreement", {
        "Agreement": "This shipping agreement, signed on 2 March 2026, covers shipment SHP-0077 "
        "for order PO-1042. Shipment SHP-0077 is carried by DHL Freight.",
    })  # fmt: skip
    eml(d / "2026-03-02_shipment_update.eml", "Warehouse <warehouse@brightline.example>",
        "sales@brightline.example", "Mon, 2 Mar 2026 15:30:00 +0000", "Shipment SHP-0077",
        "Hi,\n\nQuick update: shipment SHP-0077 is carried by UPS.\n\nWarehouse team")  # fmt: skip
    text(
        d / "2025-05-12_product_catalogue.md",
        """
# Product catalogue 2025

## Ergo Desk Chair
The Ergo Desk Chair has SKU BF-CH-100 and is in the Furniture category.

## Oak Standing Desk
The Oak Standing Desk line comes in two sizes. The Oak Standing Desk 140 cm has SKU BF-DK-220 and
the Oak Standing Desk 120 cm has SKU BF-DK-210. Both are variants of the Oak Standing Desk line.
""",
    )
    table(d / "2025-07-01_supplier_list.csv", [
        ("supplier", "supplies product", "country"),
        ("Nordwood Timber", "Oak Standing Desk", "Sweden"),
        ("Ergonomix", "Ergo Desk Chair", "Germany"),
    ])  # fmt: skip
    pptx(d / "2025-10-03_store_openings.pptx", [
        ("Store openings 2025", "Brightline Furnishings opens its first showrooms."),
        ("Stores", "Store MAN-01 (Brightline Manchester Store) is located in Manchester and operated "
         "by Brightline Furnishings.\nStore LDN-02 (Brightline London Store) is located in London "
         "and operated by Brightline Furnishings."),
    ])  # fmt: skip
    pdf(d / "2025-02-01_manchester_office_lease.pdf", "Office lease agreement", {
        "Premises": "Brightline Furnishings Ltd leases its head office at 12 Mill Street, Manchester, "
        "United Kingdom, from 1 February 2025.",
    })  # fmt: skip
    docx(d / "2026-04-22_supplier_review.docx", "Supplier review 2026", "Prepared by Operations.", {
        "Nordwood Timber": "Nordwood Timber is a supplier of Brightline Furnishings and supplies the Oak Standing Desk.",
        "Ergonomix": "Ergonomix supplies the Ergo Desk Chair. Brightline Furnishings dislikes Ergonomix's "
        "recent delivery delays.",
    })  # fmt: skip


SALES = Path(__file__).parent / "sales"


def sales_database() -> Path:
    """Build the SQLite copy of the sales database from schema.sql."""
    path = SALES / "sales.db"
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript((SALES / "schema.sql").read_text(encoding="utf-8"))
    return path


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for function, build in [
        ("HR", hr),
        ("Sales", sales),
        ("Finance", finance),
        ("Operations", operations),
    ]:
        folder = OUT / function
        folder.mkdir(parents=True)
        build(folder)
    files = sorted(OUT.rglob("*.*"))
    for path in files:
        print(path.relative_to(OUT))
    print(f"{len(files)} documents")
    print(f"sales database: {sales_database().relative_to(Path(__file__).parent)}")


if __name__ == "__main__":
    main()
