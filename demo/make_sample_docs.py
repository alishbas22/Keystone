"""Generate the mini sample dataset (KAN-35, minimal version) into demo/data/.

Fictional company: Brightline Furnishings Ltd (office furniture). Its biggest customer is
Harbor Hotels Group. Planted contradiction: the March 2025 account plan says Sara Khan manages
the Harbor Hotels account; the September 2026 memo says Bilal Ahmed does.

File names start with the document date (YYYY-MM-DD); ingestion uses it as "observed at".

    uv run python demo/make_sample_docs.py
"""

from pathlib import Path

from docx import Document as WordDocument
from fpdf import FPDF
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches, Pt

OUT = Path(__file__).parent / "data"


def company_overview_pdf(path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Brightline Furnishings Ltd - Company Overview", new_x="LMARGIN", new_y="NEXT")
    sections = {
        "About us": (
            "Brightline Furnishings Ltd designs and sells office furniture to businesses. "
            "The company is headquartered in Manchester, United Kingdom. "
            "This overview was written in January 2025."
        ),
        "Leadership": (
            "Amina Yusuf is the Chief Executive Officer of Brightline Furnishings. "
            "Hina Raza is the Chief Financial Officer and head of the Finance department. "
            "Omar Siddiqui is the Head of Sales and leads the Sales team."
        ),
        "Teams": (
            "The Sales team and the Finance department are units of Brightline Furnishings. "
            "Sara Khan and Bilal Ahmed are Account Managers in the Sales team; both report to "
            "Omar Siddiqui. Usman Tariq is a Financial Analyst in the Finance department and "
            "reports to Hina Raza."
        ),
    }
    for heading, text in sections.items():
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, text)
    pdf.output(str(path))


def account_plan_docx(path: Path) -> None:
    doc = WordDocument()
    doc.add_heading("Account Plan: Harbor Hotels Group", level=1)
    doc.add_paragraph("Prepared by the Sales team, 10 March 2025.")
    doc.add_heading("Overview", level=2)
    doc.add_paragraph(
        "Harbor Hotels Group is Brightline Furnishings' biggest customer, with annual "
        "purchases of about GBP 480,000. Harbor Hotels Group operates 40 hotels across the UK."
    )
    doc.add_heading("Account team", level=2)
    doc.add_paragraph(
        "Sara Khan is the account manager for Harbor Hotels Group. She manages the account "
        "and is the main contact for all orders. The customer's main contact is Daniel Price, "
        "Procurement Director at Harbor Hotels Group."
    )
    doc.add_heading("Products", level=2)
    doc.add_paragraph(
        "Harbor Hotels Group buys the Ergo Desk Chair (SKU BF-CH-100) and the Oak Standing "
        "Desk (SKU BF-DK-220)."
    )
    doc.save(str(path))


def customer_report_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Top customers Q2 2025"
    rows = [
        ("Customer", "Annual revenue (GBP)", "Account manager", "Industry"),
        ("Harbor Hotels Group", 480000, "Sara Khan", "Hospitality"),
        ("Crestview Bank", 210000, "Bilal Ahmed", "Banking"),
        ("Greenleaf Clinics", 95000, "Bilal Ahmed", "Healthcare"),
    ]
    for row in rows:
        ws.append(row)
    wb.save(str(path))


def finance_review_pptx(path: Path) -> None:
    prs = Presentation()
    slides = [
        ("Q3 2025 Finance Review", "Brightline Furnishings Ltd. Prepared by Hina Raza, CFO."),
        (
            "Invoices",
            "Invoice INV-2025-031 was issued by Brightline Furnishings to Harbor Hotels Group "
            "for order PO-1042. Total GBP 120,000, due 1 October 2025.\n"
            "Invoice INV-2025-034 to Crestview Bank, total GBP 45,000, was paid in full.",
        ),
        (
            "Order PO-1042",
            "Order PO-1042 was placed by Harbor Hotels Group on 12 August 2025 and contains "
            "200 Ergo Desk Chairs and 50 Oak Standing Desks.",
        ),
    ]
    for title, body in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
        assert slide.shapes.title is not None
        slide.shapes.title.text = title
        box = slide.shapes.add_textbox(Inches(0.7), Inches(1.8), Inches(8.6), Inches(4))
        box.text_frame.word_wrap = True
        box.text_frame.text = body
        for paragraph in box.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(18)
    prs.save(str(path))


def reassignment_memo_docx(path: Path) -> None:
    doc = WordDocument()
    doc.add_heading("Memo: Account manager changes", level=1)
    doc.add_paragraph("From: Omar Siddiqui, Head of Sales. Date: 15 September 2026.")
    doc.add_heading("Harbor Hotels Group", level=2)
    doc.add_paragraph(
        "Effective 1 September 2026, Bilal Ahmed is the account manager for Harbor Hotels "
        "Group and manages the account. Please send all Harbor Hotels orders to Bilal."
    )
    doc.add_heading("Retail Partners team", level=2)
    doc.add_paragraph("Sara Khan now leads the new Retail Partners team, a unit of the Sales team.")
    doc.save(str(path))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    company_overview_pdf(OUT / "2025-01-15_company_overview.pdf")
    account_plan_docx(OUT / "2025-03-10_harbor_hotels_account_plan.docx")
    customer_report_xlsx(OUT / "2025-06-30_q2_customer_report.xlsx")
    finance_review_pptx(OUT / "2025-09-02_q3_finance_review.pptx")
    reassignment_memo_docx(OUT / "2026-09-15_account_reassignment_memo.docx")
    for path in sorted(OUT.iterdir()):
        print(path.name)


if __name__ == "__main__":
    main()
