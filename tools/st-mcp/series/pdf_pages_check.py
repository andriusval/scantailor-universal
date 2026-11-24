# Compare the page count of each OCR PDF with the output page count of the matching book project.
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from st_mcp.project import Project  # noqa: E402

PROJECT = "projektas.ScanTailor"


def key(name: str) -> str:
    # The PDF names and the folder names use different punctuation, so only the letters and digits must match.
    s = unicodedata.normalize("NFC", name).casefold()
    return " ".join(re.findall(r"\w+", s))


def pdf_pages(path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(path).pages)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare PDF page counts with the output pages of the book projects.")
    ap.add_argument("pdf_dir", help="folder with '<Author> - <Title> (<year>).pdf' files")
    ap.add_argument("series", help="folder with one '<Author>. <Title> (<year>)' folder per book")
    ap.add_argument("--all", action="store_true", help="also print the books that match")
    args = ap.parse_args(argv)
    pdfs = {key(p.stem): p for p in sorted(Path(args.pdf_dir).glob("*.pdf"))}
    books = {key(d.name): d for d in sorted(Path(args.series).iterdir()) if (d / PROJECT).exists()}
    bad = ok = 0
    for k in sorted(pdfs.keys() & books.keys()):
        book = books[k]
        p = Project(book / PROJECT)
        # A page without its TIFF in out/ makes a short PDF too, so the script also counts the TIFF files.
        tifs = sum((p.output_dir / f"{p.output_name(pg.id)}.tif").exists() for pg in p.pages)
        try:
            n = pdf_pages(pdfs[k])
        except Exception as e:
            bad += 1
            print(f"ERROR   {book.name}: cannot read PDF: {e}")
            continue
        same = n == len(p.pages) == tifs
        ok += same
        bad += not same
        if not same or args.all:
            print(f"{'OK' if same else 'DIFF':7} {book.name}: pdf {n}, project {len(p.pages)}, out tif {tifs}")
    for k in sorted(pdfs.keys() - books.keys()):
        print(f"NO BOOK {pdfs[k].name}")
    for k in sorted(books.keys() - pdfs.keys()):
        print(f"NO PDF  {books[k].name}")
    missing = len(pdfs.keys() ^ books.keys())
    print(f"{ok} match, {bad} differ, {missing} without a pair")
    return 1 if bad or missing else 0


if __name__ == "__main__":
    sys.exit(main())
