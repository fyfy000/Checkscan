"""Split a customer PDF into typed pages and cheque pictures."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image

from app.config import MAX_PAGES, MAX_PICTURES
from app.records import new_cheque
from app.splitimg import split_boxes


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u202f", " ").replace("\x00", "")
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = "\n".join(lines).strip()
    if len(cleaned) > 200_000:
        cleaned = cleaned[:200_000] + "\n…"
    return cleaned


def reading_order(page: fitz.Page) -> str:
    """Pull typed text in visual rows, so a table stays on one line per row."""
    try:
        data = page.get_text("dict") or {}
    except Exception:
        return normalize_text(page.get_text("text") or "")
    found = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                piece = str(span.get("text", "")).strip()
                if piece:
                    parts.append((float(span["bbox"][0]), piece))
            if not parts:
                continue
            parts.sort()
            bbox = line.get("bbox") or [0, 0, 0, 0]
            found.append((float(bbox[1]), float(bbox[0]), " ".join(piece for _, piece in parts)))
    if not found:
        return ""
    found.sort(key=lambda item: (round(item[0], 1), item[1]))
    rows: list[str] = []
    bucket: list[tuple[float, str]] = []
    current_y = None
    for y, x, text in found:
        if current_y is None or abs(y - current_y) <= 3.5:
            bucket.append((x, text))
            if current_y is None:
                current_y = y
        else:
            bucket.sort()
            rows.append("   ".join(piece for _, piece in bucket))
            bucket = [(x, text)]
            current_y = y
    if bucket:
        bucket.sort()
        rows.append("   ".join(piece for _, piece in bucket))
    return normalize_text("\n".join(rows))


def _image_infos(page: fitz.Page) -> list[dict]:
    try:
        infos = page.get_image_info(xrefs=True) or []
    except Exception:
        infos = []
    if infos:
        return infos
    fallback = []
    try:
        images = page.get_images(full=True) or []
    except Exception:
        images = []
    for image in images:
        xref = image[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        for rect in rects:
            fallback.append({"xref": xref, "bbox": rect})
    return fallback


def _keep_picture(coverage: float, bbox: fitz.Rect, info: dict) -> bool:
    """Keep every real cheque photo, including ones placed small on the page.

    A logo or rule is dropped. A wide photo of a cheque is kept even when it
    covers only a few percent of the sheet, because several cheques often
    share one page.
    """
    if bbox.width < 36 or bbox.height < 18:
        return False
    pixels_w = int(info.get("width") or 0)
    pixels_h = int(info.get("height") or 0)
    pixels = pixels_w * pixels_h
    long_side = max(pixels_w, pixels_h)
    short_side = min(pixels_w, pixels_h) or 1
    cheque_shaped = long_side >= 360 and pixels >= 30_000 and 1.35 <= (long_side / short_side) <= 4.5
    if cheque_shaped and coverage >= 0.012:
        return True
    if coverage < 0.025:
        return False
    if pixels and pixels < 12_000 and coverage < 0.12:
        return False
    return True


def _measured_images(page: fitz.Page) -> list[dict]:
    page_area = abs(page.rect.width * page.rect.height) or 1
    found = []
    seen = set()
    for info in _image_infos(page):
        xref = int(info.get("xref") or 0)
        bbox = fitz.Rect(info.get("bbox") or page.rect) & page.rect
        if bbox.is_empty or bbox.width < 4 or bbox.height < 4:
            continue
        coverage = abs(bbox.width * bbox.height) / page_area
        if not _keep_picture(coverage, bbox, info):
            continue
        key = (xref, round(bbox.x0, 1), round(bbox.y0, 1), round(bbox.x1, 1), round(bbox.y1, 1))
        if key in seen:
            continue
        seen.add(key)
        found.append({"xref": xref, "bbox": bbox, "coverage": coverage})
    found.sort(key=lambda item: item["coverage"], reverse=True)
    return found


def _store_pixmap(image_dir: Path, serial: int, page_no: int, pixmap: fitz.Pixmap, hint: str = "") -> tuple[int, list[dict]]:
    """Save a page picture, splitting it when two cheques share the bitmap."""
    serial, name = _next_picture(image_dir, serial)
    path = image_dir / name
    pixmap.save(path)
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    boxes = split_boxes(image)
    if len(boxes) >= 2 and serial + len(boxes) - 1 <= MAX_PICTURES:
        crops = [image.crop(box) for box in boxes]
        crops[0].save(path, "PNG")
        names = [name]
        for crop in crops[1:]:
            serial, extra = _next_picture(image_dir, serial)
            crop.save(image_dir / extra, "PNG")
            names.append(extra)
    else:
        names = [name]
    made = []
    for piece in names:
        number = int(piece.removeprefix("cheque_").removesuffix(".png"))
        cheque = new_cheque(page_no, piece, number)
        if hint:
            cheque["ocr_hint"] = hint
        made.append(cheque)
    return serial, made


def _next_picture(image_dir: Path, serial: int) -> tuple[int, str]:
    serial += 1
    if serial > MAX_PICTURES:
        raise ValueError(
            f"This PDF contains more than {MAX_PICTURES} pictures. Split it so each upload stays readable."
        )
    return serial, f"cheque_{serial}.png"


def extract_document(pdf_path: Path, image_dir: Path) -> dict:
    try:
        document = fitz.open(pdf_path)
    except Exception as exc:
        raise ValueError("That file could not be opened as a PDF.") from exc

    if document.needs_pass:
        document.close()
        raise ValueError("This PDF is password protected. Remove the password and upload it again.")
    if document.page_count == 0:
        document.close()
        raise ValueError("This PDF has no pages.")
    if document.page_count > MAX_PAGES:
        document.close()
        raise ValueError(f"This PDF has more than {MAX_PAGES} pages. Split it and upload the part you need.")

    flow: list[dict] = []
    typescript: list[dict] = []
    cheques: list[dict] = []
    pages: list[dict] = []
    serial = 0

    try:
        for index, page in enumerate(document):
            text = reading_order(page)
            words = text.split()
            found = _measured_images(page)
            max_coverage = found[0]["coverage"] if found else 0
            try:
                raw_images = bool(page.get_images())
            except Exception:
                raw_images = False
            drawn = False
            if max_coverage < 0.10 and len(words) < 8 and not raw_images:
                try:
                    drawn = bool(page.get_drawings())
                except Exception:
                    drawn = False

            separate = [item for item in found if item["coverage"] < 0.55]
            scan = (
                (max_coverage >= 0.55 and len(separate) <= 1)
                or (len(words) < 12 and max_coverage >= 0.20 and len(separate) <= 1)
                or (len(words) == 0 and raw_images and not found)
                or (len(words) < 8 and drawn and not raw_images and not found)
            )
            page_no = index + 1

            if scan:
                hint = text[:4000] if len(words) >= 8 else ""
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                serial, made = _store_pixmap(image_dir, serial, page_no, pixmap, hint)
                for cheque in made:
                    cheques.append(cheque)
                    flow.append({"kind": "image", "cheque_id": cheque["id"]})
                pages.append({"page": page_no, "role": "picture"})
                continue

            insets = [item for item in found if item["coverage"] < 0.55]
            insets.sort(key=lambda item: (round(item["bbox"].y0, 1), round(item["bbox"].x0, 1)))
            if text and insets:
                typescript.append({"page": page_no, "text": text})
                flow.append({"kind": "reflow", "page": page_no, "text": text})
                pages.append({"page": page_no, "role": "mixed"})
            elif text:
                typescript.append({"page": page_no, "text": text})
                flow.append({"kind": "copy", "page_index": index})
                pages.append({"page": page_no, "role": "typescript"})
            elif insets:
                pages.append({"page": page_no, "role": "picture"})
            else:
                pages.append({"page": page_no, "role": "blank"})

            for item in insets:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=item["bbox"], alpha=False)
                serial, made = _store_pixmap(image_dir, serial, page_no, pixmap)
                for cheque in made:
                    cheques.append(cheque)
                    flow.append({"kind": "image", "cheque_id": cheque["id"]})
    finally:
        document.close()

    return {"flow": flow, "typescript": typescript, "cheques": cheques, "pages": pages}
