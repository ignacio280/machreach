"""PDF builders for extraction tests.

Two kinds of PDF matter to MachReach and they are indistinguishable by size or
page count: one carries a text layer, the other is photographs of pages. Only
the first can become a quiz, so tests need to build both on purpose.
"""
import io


def scanned_pdf(pages: int = 1) -> bytes:
    """A PDF whose pages hold no text — what a phone scan of notes produces."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    payload = io.BytesIO()
    writer.write(payload)
    return payload.getvalue()


def text_pdf(pages: list[str]) -> bytes:
    """A PDF carrying a real text layer, one drawn string per page.

    Hand-built because the repo has no PDF writer that can draw text: pypdf
    only assembles pages, and adding a renderer for one test is not worth it.
    """
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[{}]/Count {}>>".format(
            " ".join(f"{4 + i * 2} 0 R" for i in range(len(pages))), len(pages)
        ).encode(),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    for i, body in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({body}) Tj ET".encode()
        objects.append(
            (
                "<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                f"/Contents {5 + i * 2} 0 R/Resources<</Font<</F1 3 0 R>>>>>>"
            ).encode()
        )
        objects.append(
            b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream"
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + payload + b"\nendobj\n")
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    return out.getvalue()


# Long enough to clear the "is this a scan?" and "is there enough to build
# questions from?" floors, so tests exercise the happy path rather than them.
STUDY_MATERIAL = (
    "The Carnot cycle sets the upper bound on the efficiency of any heat engine "
    "working between two reservoirs, and depends only on their temperatures. "
    "Entropy never decreases in an isolated system, which is the second law. "
    "A reversible process is the idealisation every real engine falls short of."
)
