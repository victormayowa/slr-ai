"""The house style for every Word document OmniReview produces: Times New Roman, one-and-a-half spacing, black text.

Journals and institutions ask for manuscripts in this shape, and a protocol, checklist, or response letter that
matches it can be sent on without reformatting. Every document is built through `styled_document()`, and the
manuscript, which Pandoc writes rather than python-docx, is given `reference_docx()` so it comes out the same.
"""

import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as WordDocument
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

FONT_NAME = "Times New Roman"
LINE_SPACING = 1.5
BODY_SIZE = Pt(12)
BLACK = RGBColor(0x00, 0x00, 0x00)

_reference: Path | None = None


def _set_font(style: Any) -> None:
    """Put one style in the house font and colour. Sizes stay as they are, so headings keep their hierarchy."""
    style.font.name = FONT_NAME
    style.font.color.rgb = BLACK
    # python-docx sets only the Latin font; Word needs the other three, or it substitutes another face.
    run_properties = style.element.get_or_add_rPr()
    fonts = run_properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = run_properties.makeelement(qn("w:rFonts"), {})
        run_properties.append(fonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attribute), FONT_NAME)


def apply_house_style(document: WordDocument) -> WordDocument:
    """Set the font, colour, and spacing on every paragraph style the document defines."""
    for style in document.styles:
        if style.type != WD_STYLE_TYPE.PARAGRAPH:
            continue
        _set_font(style)
        style.paragraph_format.line_spacing = LINE_SPACING
    document.styles["Normal"].font.size = BODY_SIZE
    return document


def styled_document() -> WordDocument:
    """A new Word document in the house style. Used instead of python-docx's Document()."""
    return apply_house_style(Document())


def to_bytes(document: WordDocument) -> bytes:
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def reference_docx() -> Path:
    """A Word file in the house style for Pandoc's --reference-doc, so the manuscript matches the other documents.

    Pandoc takes the styles from this file, not its content. It is built once and kept for the life of the process.
    """
    global _reference
    if _reference is not None and _reference.exists():
        return _reference
    document = styled_document()
    # Pandoc looks up the styles it uses by name, so the file has to define them; writing one of each does that.
    for level in range(1, 7):
        document.add_heading(f"Heading {level}", level=level)
    document.add_paragraph("Body text")
    document.add_paragraph("Bullet", style="List Bullet")
    document.add_paragraph("Number", style="List Number")
    folder = Path(tempfile.mkdtemp(prefix="omnireview-docx-style-"))
    path = folder / "reference.docx"
    path.write_bytes(to_bytes(document))
    _reference = path
    return path
