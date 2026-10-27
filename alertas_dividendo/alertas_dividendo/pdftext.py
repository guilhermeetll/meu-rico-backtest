"""Texto de um PDF: pdftotext (poppler) e, se faltar, pypdf."""

from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger(__name__)


def _pdftotext(data: bytes, args: list[str]) -> str:
    completed = subprocess.run(
        ["pdftotext", *args, "-", "-"],
        input=data,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", errors="ignore")


def _pypdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    import io

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        log.warning("pypdf não abriu o PDF", exc_info=True)
        return ""
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            log.warning("pypdf falhou numa página", exc_info=True)
    return "\n".join(parts)


def pdf_variants(data: bytes) -> list[str]:
    """Textos do mesmo PDF. O modo padrão vem primeiro; a tabela usa as linhas dele."""
    chunks: list[str] = []
    if shutil.which("pdftotext"):
        for args in ([], ["-raw"], ["-layout"]):
            text = _pdftotext(data, args)
            if text.strip():
                chunks.append(text)
    else:
        log.warning("pdftotext não está instalado; usando pypdf")
        fallback = _pypdf(data)
        if fallback.strip():
            chunks.append(fallback)
    return chunks
