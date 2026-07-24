"""Turn a resume file (PDF / DOCX / TXT / MD) into plain text."""

from __future__ import annotations

from pathlib import Path


def extract_text(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Resume not found: {p}")

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _pdf(p)
    if suffix in {".docx"}:
        return _docx(p)
    if suffix in {".txt", ".md", ".text"}:
        return p.read_text(errors="ignore")
    # Best effort: try to read as text.
    try:
        return p.read_text(errors="ignore")
    except Exception as e:
        raise ValueError(f"Unsupported resume format: {suffix}") from e


def _pdf(p: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(p))
    parts = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(parts).strip()
    if not text:
        raise ValueError(
            "Could not extract text from PDF (it may be a scanned image). "
            "Please provide a text-based resume or paste the text manually."
        )
    return text


def _docx(p: Path) -> str:
    import docx

    doc = docx.Document(str(p))
    return "\n".join(para.text for para in doc.paragraphs).strip()
