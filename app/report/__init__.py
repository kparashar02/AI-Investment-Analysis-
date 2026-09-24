"""Report rendering — Layer 4 output (PRD 13).

Turns a :class:`~app.models.report.ResearchReport` (fixed decision + generated
prose + verification result) into a shareable document. Markdown is the
dependency-free renderer; HTML/PDF and charts follow. Rendering never changes a
number — it only lays out what the decision and the narrative already contain.
"""

from app.report.html import render_html
from app.report.markdown import render_markdown
from app.report.pdf import PdfUnavailable, html_to_pdf, pdf_available

__all__ = ["render_markdown", "render_html", "html_to_pdf", "pdf_available", "PdfUnavailable"]
