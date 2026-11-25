"""File types Keystone can parse. Kept free of heavy imports so connectors can use it cheaply."""

# Extension -> Docling InputFormat name (see keystone.ingest.parse).
FORMAT_NAMES = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".xlsx": "XLSX",
    ".pptx": "PPTX",
    ".eml": "EMAIL",
    ".msg": "EMAIL",
    ".md": "MD",
    ".txt": "MD",
    ".html": "HTML",
    ".htm": "HTML",
    ".csv": "CSV",
}
SUPPORTED_SUFFIXES = sorted(FORMAT_NAMES)
