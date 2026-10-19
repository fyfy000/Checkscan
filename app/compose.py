"""Build a print file: original typed pages, then a typewritten cheque record."""

from __future__ import annotations

from pathlib import Path

import fitz

from app.records import PRIMARY_FIELDS, cheque_total, format_amount, parse_amount

LETTER = fitz.paper_rect("letter")
INK = (0.12, 0.10, 0.08)
MUTED = (0.38, 0.34, 0.30)
GREEN = (0.11, 0.29, 0.22)
RULE = (0.62, 0.55, 0.46)
PAPER = (0.976, 0.965, 0.94)
WARN = (0.55, 0.24, 0.1)

class Fonts:
    """Base-14 fonts so the print file stays real, copyable type."""

    def apply(self, page: fitz.Page) -> "Fonts":
        self.sans = "helv"
        self.bold = "hebo"
        self.mono = "cour"
        return self


def clean(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch in "\n\t" or ord(ch) >= 32).strip()


def wrap(text: str, width: int) -> list[str]:
    flat = " ".join(clean(text).replace("\n", " ").split())
    if not flat:
        return ["—"]
    lines: list[str] = []
    current = ""
    for word in flat.split(" "):
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _footer(page: fitz.Page, fonts: Fonts) -> None:
    page.insert_text(
        (54, 770),
        "Transcript for the accounting file. Not a negotiable cheque.",
        fontname=fonts.sans,
        fontsize=8,
        color=MUTED,
    )


def _reflow_lines(text: str) -> list[str]:
    raw = clean(text)
    if not raw:
        return ["No typed text was found on this page."]
    lines: list[str] = []
    for paragraph in raw.splitlines():
        if not paragraph.strip():
            lines.append("")
        else:
            lines.extend(wrap(paragraph, 88))
    return lines or ["No typed text was found on this page."]


def _write_reflow(out: fitz.Document, title: str, subtitle: str, text: str, fonts_factory: Fonts) -> int:
    lines = _reflow_lines(text)
    start = out.page_count + 1
    index = 0
    first = True
    while index < len(lines):
        page = out.new_page(width=LETTER.width, height=LETTER.height)
        fonts = fonts_factory.apply(page)
        if first:
            page.insert_text((54, 64), title, fontname=fonts.bold, fontsize=16, color=INK)
            page.insert_text((54, 84), subtitle, fontname=fonts.sans, fontsize=9, color=MUTED)
            cursor = 116
            first = False
        else:
            page.insert_text((54, 56), title + "  ·  continued", fontname=fonts.sans, fontsize=10, color=MUTED)
            cursor = 84
        while index < len(lines) and cursor < 748:
            if lines[index]:
                page.insert_text((54, cursor), lines[index], fontname=fonts.sans, fontsize=11, color=INK)
            cursor += 16
            index += 1
        _footer(page, fonts)
    return start


def _cheque_rows(cheque: dict) -> list[tuple[str, str, bool]]:
    fields = cheque["fields"]
    rows = []
    for key, label in PRIMARY_FIELDS:
        rows.append((label, fields.get(key, ""), key == "amount_numeric"))
    signature = fields.get("signature_present", "unknown")
    signature_text = {"yes": "Present", "no": "Not seen", "unknown": "Not confirmed"}.get(signature, "Not confirmed")
    rows.append(("Signature", signature_text, False))
    return rows


def _write_typed_picture(
    out: fitz.Document, cheque: dict, filename: str, image_path: Path | None, fonts_factory: Fonts
) -> int:
    """Keep a picture the user marked as a typed page, even with the text box empty."""
    page = out.new_page(width=LETTER.width, height=LETTER.height)
    fonts = fonts_factory.apply(page)
    page.insert_text((54, 58), "TYPED PAGE", fontname=fonts.bold, fontsize=18, color=INK)
    page.insert_text(
        (54, 78),
        f"Picture kept from {filename}  ·  page {cheque['page']}",
        fontname=fonts.sans,
        fontsize=9,
        color=MUTED,
    )
    page.insert_text(
        (54, 104),
        "No typed text was entered, so the picture is included as it appears.",
        fontname=fonts.sans,
        fontsize=11,
        color=INK,
    )
    if image_path and image_path.is_file():
        page.insert_image(fitz.Rect(54, 124, 558, 740), filename=str(image_path), keep_proportion=True)
    _footer(page, fonts)
    return out.page_count


def _write_cheque(out: fitz.Document, cheque: dict, filename: str, image_path: Path | None, fonts_factory: Fonts) -> int:
    page = out.new_page(width=LETTER.width, height=LETTER.height)
    fonts = fonts_factory.apply(page)
    page.insert_text((54, 58), "CHEQUE PRINT RECORD", fontname=fonts.bold, fontsize=18, color=INK)
    page.insert_text(
        (54, 78),
        f"Set in type from the handwritten cheque  ·  {filename}  ·  page {cheque['page']}",
        fontname=fonts.sans,
        fontsize=9,
        color=MUTED,
    )

    rows = _cheque_rows(cheque)
    laid = []
    y = 128
    for label, value, emphasis in rows:
        lines = wrap(value, 38 if not emphasis else 28)
        height = max(26, 16 * len(lines) + 8)
        laid.append((y, label, lines, emphasis, height))
        y += height
    box = fitz.Rect(50, 100, 562, y + 8)
    page.draw_rect(box, color=GREEN, width=1.1)
    page.draw_rect(fitz.Rect(box.x0 - 4, box.y0 - 4, box.x1 + 4, box.y1 + 4), color=GREEN, width=0.4)
    page.draw_rect(fitz.Rect(box.x0, box.y0, box.x0 + 5, box.y1), color=None, fill=GREEN)

    for top, label, lines, emphasis, height in laid:
        page.insert_text((68, top + 12), label.upper(), fontname=fonts.sans, fontsize=8, color=MUTED)
        if emphasis:
            page.draw_rect(fitz.Rect(230, top - 2, 520, top + height - 8), color=None, fill=(0.90, 0.94, 0.91))
        for index, line in enumerate(lines):
            page.insert_text(
                (236, top + 14 + index * 16),
                line,
                fontname=fonts.mono,
                fontsize=13 if emphasis else 12,
                color=INK,
            )

    cursor = box.y1 + 22
    extra = []
    branch = clean(cheque["fields"].get("branch", ""))
    micr = clean(cheque["fields"].get("micr", ""))
    notes = clean(cheque["fields"].get("notes", ""))
    if branch:
        extra.append("Branch  " + branch)
    if micr:
        extra.append("Account line  " + micr)
    if notes:
        extra.append("Note  " + notes)
    for line in extra:
        for part in wrap(line, 78):
            if cursor > 640:
                break
            page.insert_text((54, cursor), part, fontname=fonts.sans, fontsize=10, color=INK)
            cursor += 14
        cursor += 4

    warnings = [item["text"] for item in cheque.get("warnings", []) if item.get("level") == "warn"]
    if any(item.get("text") == "Nothing typed yet." for item in cheque.get("warnings", [])):
        page.insert_text((54, cursor + 4), "No cheque details were entered.", fontname=fonts.sans, fontsize=10, color=WARN)
        cursor += 20
    if warnings:
        page.insert_text((54, cursor + 4), "CHECK BEFORE POSTING", fontname=fonts.bold, fontsize=9, color=WARN)
        cursor += 20
        for warning in warnings:
            for part in wrap(warning, 78):
                page.insert_text((54, cursor), part, fontname=fonts.sans, fontsize=10, color=WARN)
                cursor += 14
            cursor += 2
    elif clean(cheque["fields"].get("amount_numeric", "")) and clean(cheque["fields"].get("amount_words", "")):
        page.insert_text((54, cursor + 4), "The figure and the words agree.", fontname=fonts.sans, fontsize=10, color=GREEN)
        cursor += 20

    if image_path and image_path.is_file() and cursor < 700:
        caption_y = cursor + 8
        page.insert_text((54, caption_y), "Source image, reduced", fontname=fonts.sans, fontsize=8, color=MUTED)
        available = 748 - (caption_y + 8)
        if available > 70:
            rect = fitz.Rect(54, caption_y + 8, 558, caption_y + 8 + available)
            page.insert_image(rect, filename=str(image_path), keep_proportion=True)

    _footer(page, fonts)
    return out.page_count


def _write_summary(out: fitz.Document, cheques: list[dict], filename: str, fonts_factory: Fonts) -> int:
    start = out.page_count + 1
    rows = list(cheques)
    total = cheque_total(cheques)
    first = True
    while rows or first:
        page = out.new_page(width=LETTER.width, height=LETTER.height)
        fonts = fonts_factory.apply(page)
        page.insert_text((54, 58), "CHEQUE SUMMARY", fontname=fonts.bold, fontsize=18, color=INK)
        page.insert_text(
            (54, 78),
            f"Set in type from handwriting  ·  {filename}",
            fontname=fonts.sans,
            fontsize=9,
            color=MUTED,
        )
        y = 112
        if first:
            page.insert_text(
                (54, y),
                "Original typed pages come first. Each cheque then has its own print record.",
                fontname=fonts.sans,
                fontsize=10,
                color=INK,
            )
            y = 140
            first = False
        for x, label in ((54, "NO."), (130, "DATE"), (250, "PAYEE"), (450, "AMOUNT")):
            page.insert_text((x, y), label, fontname=fonts.sans, fontsize=8, color=MUTED)
        y += 8
        page.draw_line(fitz.Point(54, y), fitz.Point(558, y), color=RULE, width=0.6)
        y += 18
        while rows and y < 700:
            cheque = rows.pop(0)
            fields = cheque["fields"]
            number = clean(fields.get("cheque_number", "")) or "—"
            date = clean(fields.get("date", "")) or "—"
            payee = wrap(fields.get("payee", ""), 26)[0]
            amount = clean(fields.get("amount_numeric", "")) or "—"
            starred = any(item.get("level") == "warn" for item in cheque.get("warnings", []))
            page.insert_text((54, y), number[:14], fontname=fonts.mono, fontsize=10, color=INK)
            page.insert_text((130, y), date[:16], fontname=fonts.mono, fontsize=10, color=INK)
            page.insert_text((250, y), payee, fontname=fonts.mono, fontsize=10, color=INK)
            page.insert_text((450, y), (amount + (" *" if starred else ""))[:18], fontname=fonts.mono, fontsize=10, color=INK)
            y += 20
        if not rows and total:
            y += 8
            page.draw_line(fitz.Point(400, y), fitz.Point(558, y), color=RULE, width=0.6)
            page.insert_text((400, y + 18), "Total  " + total, fontname=fonts.bold, fontsize=12, color=INK)
        if any(any(item.get("level") == "warn" for item in cheque.get("warnings", [])) for cheque in cheques):
            page.insert_text((54, 748), "* Compare this line with the cheque before posting.", fontname=fonts.sans, fontsize=8, color=WARN)
        _footer(page, fonts)
        if not rows:
            break
    return start


def _note_page(out: fitz.Document, text: str, fonts_factory: Fonts) -> None:
    page = out.new_page(width=LETTER.width, height=LETTER.height)
    fonts = fonts_factory.apply(page)
    page.insert_text((54, 72), "CHEQUESCRIPT", fontname=fonts.bold, fontsize=16, color=INK)
    page.insert_textbox(fitz.Rect(54, 100, 540, 300), text, fontname=fonts.sans, fontsize=12, color=INK)
    _footer(page, fonts)


def compose(job: dict, source_pdf: Path, dest_pdf: Path) -> dict:
    source = fitz.open(source_pdf)
    out = fitz.open()
    fonts = Fonts()
    by_id = {cheque["id"]: cheque for cheque in job["cheques"]}
    deferred: list[dict] = []
    copied = 0
    reflowed = 0
    toc = []
    typed_marked = False

    try:
        for item in job.get("flow", []):
            kind = item.get("kind")
            if kind == "copy":
                index = int(item["page_index"])
                if index < 0 or index >= source.page_count:
                    continue
                if not typed_marked:
                    toc.append([1, "Original typed pages", out.page_count + 1])
                    typed_marked = True
                out.insert_pdf(source, from_page=index, to_page=index)
                copied += 1
            elif kind == "reflow":
                if not typed_marked:
                    toc.append([1, "Original typed pages", out.page_count + 1])
                    typed_marked = True
                _write_reflow(
                    out,
                    f"TYPED TEXT FROM PAGE {item['page']}",
                    job["filename"],
                    item.get("text", ""),
                    fonts,
                )
                reflowed += 1
            elif kind == "image":
                cheque = by_id.get(item.get("cheque_id"))
                if not cheque:
                    continue
                if cheque.get("role") == "typed":
                    if not typed_marked:
                        toc.append([1, "Original typed pages", out.page_count + 1])
                        typed_marked = True
                    typed_text = clean(cheque.get("typed_text", ""))
                    if typed_text:
                        _write_reflow(
                            out,
                            f"TYPED TEXT FROM PAGE {cheque['page']}",
                            "Read from a picture in " + job["filename"],
                            typed_text,
                            fonts,
                        )
                    else:
                        image = source_pdf.parent / cheque["image"]
                        _write_typed_picture(out, cheque, job["filename"], image, fonts)
                    reflowed += 1
                elif cheque.get("role") == "cheque":
                    deferred.append(cheque)

        included = deferred
        if len(included) >= 2:
            summary_at = _write_summary(out, included, job["filename"], fonts)
            toc.append([1, "Cheque summary", summary_at])
        for cheque in included:
            image = source_pdf.parent / cheque["image"]
            page_at = _write_cheque(out, cheque, job["filename"], image, fonts)
            number = clean(cheque["fields"].get("cheque_number", ""))
            label = f"Cheque {number}" if number else f"Cheque on page {cheque['page']}"
            toc.append([1, label, page_at])

        if out.page_count == 0:
            _note_page(out, "Nothing was selected for this print file.", fonts)

        if toc:
            out.set_toc(toc)
        out.set_metadata(
            {
                "title": f"ChequeScript print file — {job['filename']}",
                "author": "ChequeScript",
                "subject": "Typed pages plus cheque print records",
            }
        )
        out.save(dest_pdf, garbage=4, deflate=True)
        page_count = out.page_count
    finally:
        source.close()
        out.close()

    amounts = []
    for cheque in included:
        parsed = parse_amount(cheque["fields"].get("amount_numeric", ""))
        if parsed is not None:
            amounts.append(parsed)
    return {
        "copied_pages": copied,
        "reflow_pages": reflowed,
        "cheque_count": len(included),
        "page_count": page_count,
        "total": cheque_total(included),
        "amount_sum": round(sum(amounts), 2) if amounts else None,
    }
