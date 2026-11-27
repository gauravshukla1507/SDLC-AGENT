"""Requirement documents in, plain text out.

Teams write requirements in Markdown, plain text, Word or PDF. The agents need the text, including headings
and tables (acceptance criteria usually live in tables), so each format is converted to Markdown-like text
without extra dependencies where possible:

  .md / .markdown / .txt   read as-is
  .docx                    paragraphs, heading levels, bullet lists and tables from word/document.xml
  .pdf                     `pdftotext -layout` if installed, else the `pypdf` package if installed
"""
from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED = (".md", ".markdown", ".txt", ".docx", ".pdf")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(Exception):
    pass


def read_requirements(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise DocumentError(f"Requirements document not found: {p}")
    ext = p.suffix.lower()
    if ext in (".md", ".markdown", ".txt"):
        text = p.read_text(encoding="utf-8", errors="replace")
    elif ext == ".docx":
        text = _docx_to_text(p)
    elif ext == ".pdf":
        text = _pdf_to_text(p)
    else:
        raise DocumentError(f"Unsupported document type '{ext}'. Use one of: {', '.join(SUPPORTED)}")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 40:
        raise DocumentError(f"{p.name} contains almost no text ({len(text)} characters). "
                            "If it is a scanned PDF, export it as .docx or .md first.")
    return text


def title_of(text: str, fallback: str = "Delivery") -> str:
    """First heading or meaningful line, used as the project title."""
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip().strip("*").strip()
        if line and not re.match(r"^(request|feature request|document id|version|\|)", line, re.I):
            return re.sub(r"^(business requirements document|brd|requirements?)\s*[:\-–]\s*", "", line, flags=re.I)[:90]
    return fallback


# ------------------------------------------------------------------ Word
def _text_of(node) -> str:
    parts = []
    for el in node.iter():
        if el.tag == W + "t" and el.text:
            parts.append(el.text)
        elif el.tag == W + "tab":
            parts.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _docx_to_text(p: Path) -> str:
    try:
        with zipfile.ZipFile(p) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DocumentError(f"{p.name} is not a valid .docx file") from exc
    body = root.find(W + "body")
    out: list[str] = []
    for block in list(body if body is not None else []):
        if block.tag == W + "p":
            text = _text_of(block)
            if not text:
                continue
            style = block.find(f"{W}pPr/{W}pStyle")
            style_name = style.get(W + "val", "") if style is not None else ""
            heading = re.match(r"(?i)heading\s*(\d)", style_name)
            numbered = block.find(f"{W}pPr/{W}numPr") is not None
            if re.match(r"(?i)^title$", style_name):
                out.append(f"\n# {text}\n")
            elif heading:                                   # Word Heading 1 sits under the document title
                out.append(f"\n{'#' * min(int(heading.group(1)) + 1, 6)} {text}\n")
            elif numbered or re.match(r"(?i)list", style_name):
                out.append(f"- {text}")
            else:
                out.append(text + "\n")
        elif block.tag == W + "tbl":
            rows = []
            for tr in block.iter(W + "tr"):
                cells = [_text_of(tc).replace("\n", " ").replace("|", "/") for tc in tr.iter(W + "tc")]
                if cells:
                    rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                out.append("")
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("|" + "---|" * width)
                out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ PDF
def _pdf_to_text(p: Path) -> str:
    if shutil.which("pdftotext"):
        proc = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError as exc:
        raise DocumentError("Reading PDF needs `pdftotext` (poppler) or `pip install pypdf`. "
                            "Alternatively save the document as .docx or .md.") from exc
    return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages)
