"""PDF export (PRD 13.3).

WeasyPrint renders the self-contained HTML (inline SVG charts included) to a
print-ready PDF — where its native libraries (Pango/Cairo/GObject) are present,
which is the norm on Linux/CI but often not on a bare Windows box. So the import
is lazy and failures are turned into a clear, actionable message rather than an
opaque stack trace: the HTML is already print-ready, and any browser's
"Print → Save as PDF" produces the same document.
"""

from __future__ import annotations

from pathlib import Path


class PdfUnavailable(RuntimeError):
    """WeasyPrint (or its native libraries) is not available on this machine."""


_GUIDANCE = (
    "PDF export needs WeasyPrint and its native libraries (Pango, Cairo, "
    "GObject), which are not available here. The generated HTML report is "
    "already print-ready — open it in a browser and choose 'Print → Save as "
    "PDF'. To enable direct PDF export, install the GTK/Pango runtime per "
    "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
)


def pdf_available() -> bool:
    """Whether WeasyPrint can actually be used on this machine."""
    try:
        import weasyprint  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001 — a missing native lib raises OSError, not ImportError
        return False
    return True


def html_to_pdf(html: str, out_path: str | Path) -> Path:
    """Render an HTML string to a PDF file. Raises :class:`PdfUnavailable` with
    guidance if WeasyPrint cannot run here."""
    try:
        from weasyprint import HTML  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise PdfUnavailable(_GUIDANCE) from exc
    path = Path(out_path)
    HTML(string=html).write_pdf(str(path))
    return path
