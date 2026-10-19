"""Cheque fields, amount checks, and the records the print file is built from."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image

from app.config import MAX_PICTURES
from app.splitimg import split_boxes

PRIMARY_FIELDS = [
    ("cheque_number", "Cheque number"),
    ("date", "Date"),
    ("payee", "Pay to the order of"),
    ("amount_numeric", "Amount"),
    ("amount_words", "Amount in words"),
    ("currency", "Currency"),
    ("bank_name", "Bank"),
    ("drawer", "Drawer"),
    ("memo", "Memo"),
]

MORE_FIELDS = [
    ("branch", "Branch"),
    ("micr", "Account / MICR line"),
]

TEXT_FIELDS = PRIMARY_FIELDS + MORE_FIELDS
ROLES = ("cheque", "typed", "omit")
SIGNATURES = ("unknown", "yes", "no")

ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def empty_fields() -> dict[str, str]:
    fields = {key: "" for key, _label in TEXT_FIELDS}
    fields["signature_present"] = "unknown"
    fields["notes"] = ""
    return fields


def new_cheque(page: int, image: str, number: int) -> dict[str, Any]:
    return {
        "id": f"c{number}",
        "page": page,
        "image": image,
        "role": "cheque",
        "typed_text": "",
        "ocr_hint": "",
        "fields": empty_fields(),
        "confidence": None,
        "illegible": [],
        "warnings": [],
        "source": "pending",
        "error": "",
        "shared": False,
    }


def parse_amount(text: str) -> float | None:
    """Read a figure such as '$1,200.00' or 'CAD 1200'."""
    if not text or not text.strip():
        return None
    match = re.search(r"(\d{1,3}(?:[ ,]\d{3})+|\d+)(?:[.](\d{1,2}))?", text)
    if not match:
        return None
    whole = match.group(1).replace(" ", "").replace(",", "")
    fraction = match.group(2) or "0"
    if len(fraction) == 1:
        fraction += "0"
    try:
        return int(whole) + int(fraction[:2]) / 100
    except ValueError:
        return None


def words_to_int(phrase: str) -> int | None:
    words = [word for word in re.split(r"\s+", phrase.lower().replace("-", " ").strip()) if word]
    if not words:
        return None
    total = 0
    current = 0
    for word in words:
        if word in ONES:
            current += ONES[word]
        elif word in TENS:
            current += TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word in {"thousand", "million"}:
            scale = 1000 if word == "thousand" else 1_000_000
            total += (current or 1) * scale
            current = 0
        else:
            return None
    return total + current


def words_to_amount(text: str) -> float | None:
    """Read cheque words such as 'one thousand two hundred dollars and 50/100'."""
    if not text or not text.strip():
        return None
    cleaned = text.lower().replace("\n", " ")
    cents: int | None = None
    fraction = re.search(r"(\d{1,2})\s*/\s*100", cleaned)
    if fraction:
        cents = int(fraction.group(1))
        cleaned = cleaned[: fraction.start()] + " " + cleaned[fraction.end() :]
    cent_words = re.search(r"\band\s+([a-z\s-]+?)\s+cents?\b", cleaned)
    if cent_words and cents is None:
        parsed_cents = words_to_int(cent_words.group(1))
        if parsed_cents is None or parsed_cents > 99:
            return None
        cents = parsed_cents
        cleaned = cleaned[: cent_words.start()] + " " + cleaned[cent_words.end() :]
    for filler in ("dollars", "dollar", "only", "exactly"):
        cleaned = re.sub(rf"\b{filler}\b", " ", cleaned)
    cleaned = re.sub(r"\band\b", " ", cleaned)
    cleaned = re.sub(r"[^a-z\s-]", " ", cleaned)
    if not cleaned.strip():
        if cents is None:
            return None
        return round(cents / 100, 2)
    dollars = words_to_int(cleaned)
    if dollars is None:
        return None
    return round(dollars + (cents or 0) / 100, 2)


def format_amount(value: float) -> str:
    return f"{value:,.2f}"


def _has_details(fields: dict[str, str]) -> bool:
    keys = [key for key, _label in TEXT_FIELDS] + ["notes"]
    return any(str(fields.get(key, "")).strip() for key in keys)


def warnings_for(cheque: dict[str, Any]) -> list[dict[str, str]]:
    if cheque.get("role") != "cheque":
        return []
    fields = cheque["fields"]
    if not _has_details(fields):
        return [{"level": "quiet", "text": "Nothing typed yet."}]

    warnings: list[dict[str, str]] = []
    if not fields.get("payee", "").strip():
        warnings.append({"level": "warn", "text": "Payee is empty."})
    if not fields.get("date", "").strip():
        warnings.append({"level": "warn", "text": "Date is empty."})
    if not fields.get("amount_numeric", "").strip():
        warnings.append({"level": "warn", "text": "Amount is empty."})

    numeric = parse_amount(fields.get("amount_numeric", ""))
    words = words_to_amount(fields.get("amount_words", ""))
    if numeric is not None and words is not None and round(numeric, 2) != round(words, 2):
        warnings.append(
            {
                "level": "warn",
                "text": (
                    f"The figure {fields.get('amount_numeric', '').strip()} and the words do not agree. "
                    f"The words come to {format_amount(words)}."
                ),
            }
        )
    elif numeric is not None and words is not None:
        warnings.append({"level": "good", "text": "The figure and the words agree."})
    elif numeric is not None and fields.get("amount_words", "").strip() and words is None:
        if re.search(r"\d|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand", fields["amount_words"].lower()):
            warnings.append(
                {
                    "level": "warn",
                    "text": "The amount in words could not be matched to the figure. Compare the two lines.",
                }
            )

    illegible = [str(item).strip() for item in cheque.get("illegible") or [] if str(item).strip()]
    if illegible:
        warnings.append({"level": "warn", "text": "Hard to read: " + ", ".join(illegible[:8]) + "."})

    confidence = cheque.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < 0.55:
        warnings.append({"level": "warn", "text": "The model was unsure about this cheque. Compare it with the image."})
    return warnings


def cheque_total(cheques: list[dict[str, Any]]) -> str:
    """Sum figures when every included cheque has an amount and one currency."""
    amounts: list[float] = []
    currencies: set[str] = set()
    for cheque in cheques:
        amount = parse_amount(cheque["fields"].get("amount_numeric", ""))
        if amount is None:
            return ""
        amounts.append(amount)
        currencies.add(cheque["fields"].get("currency", "").strip().upper())
    if not amounts:
        return ""
    named = currencies - {""}
    if len(named) > 1:
        return ""
    total = format_amount(sum(amounts))
    prefix = next(iter(named)) + " " if len(named) == 1 else ""
    return prefix + total


def _clip(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def as_confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1 and number <= 100:
        number = number / 100
    if number < 0 or number > 1:
        return None
    return round(number, 2)


def as_cheque_list(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One model answer may describe every cheque visible in the picture."""
    many = result.get("cheques")
    if isinstance(many, list):
        items = [item for item in many if isinstance(item, dict)]
        if items:
            return items[:8]
    return [result]


def _detail_keys() -> tuple[str, ...]:
    return tuple(key for key, _label in TEXT_FIELDS[:9])


def _reading_useful(item: dict[str, Any]) -> bool:
    if item.get("is_cheque") is False:
        return bool(str(item.get("typed_text") or "").strip())
    return any(str(item.get(key) or "").strip() for key in _detail_keys())


def _same_reading(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = [
        key
        for key in ("cheque_number", "payee", "amount_numeric")
        if str(left.get(key) or "").strip() and str(right.get(key) or "").strip()
    ]
    return len(keys) >= 2 and all(str(left[key]).strip().lower() == str(right[key]).strip().lower() for key in keys)


def _next_image_name(directory: Path) -> str | None:
    used = set()
    for file in directory.glob("cheque_*.png"):
        match = re.fullmatch(r"cheque_(\d+)\.png", file.name)
        if match:
            used.add(int(match.group(1)))
    number = 1
    while number in used:
        number += 1
    if number > MAX_PICTURES:
        return None
    return f"cheque_{number}.png"


def insert_cheques(job: dict[str, Any], after_id: str, extras: list[dict]) -> None:
    if not extras:
        return
    cheques = job["cheques"]
    index = next(i for i, cheque in enumerate(cheques) if cheque["id"] == after_id)
    cheques[index + 1 : index + 1] = extras
    flow = job.setdefault("flow", [])
    flow_at = next((i for i, item in enumerate(flow) if item.get("cheque_id") == after_id), None)
    if flow_at is None:
        for extra in extras:
            flow.append({"kind": "image", "cheque_id": extra["id"]})
        return
    for offset, extra in enumerate(extras, start=1):
        flow.insert(flow_at + offset, {"kind": "image", "cheque_id": extra["id"]})


def divide_picture(job: dict[str, Any], cheque: dict[str, Any], directory: Path) -> list[dict]:
    """Cut a bitmap into separate pictures when a blank gutter divides cheques."""
    path = directory / cheque["image"]
    if not path.is_file():
        return []
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    boxes = split_boxes(image)
    if len(boxes) < 2:
        return []
    if len(job["cheques"]) + len(boxes) - 1 > MAX_PICTURES:
        return []
    crops = [image.crop(box) for box in boxes]
    crops[0].save(path, "PNG")
    extras = []
    for crop in crops[1:]:
        name = _next_image_name(directory)
        if name is None:
            break
        crop.save(directory / name, "PNG")
        extra = new_cheque(cheque["page"], name, _spare_number(job, extras))
        extra["ocr_hint"] = cheque.get("ocr_hint", "")
        extras.append(extra)
    if not extras:
        return []
    insert_cheques(job, cheque["id"], extras)
    return extras


def store_reading(job: dict[str, Any], cheque: dict[str, Any], result: dict[str, Any]) -> None:
    """Keep every cheque the model found in this picture, not only the first."""
    items = [item for item in as_cheque_list(result) if _reading_useful(item)]
    if not items:
        items = as_cheque_list(result)[:1]
    apply_ai(cheque, items[0])
    created: list[dict] = []
    seen = [items[0]]
    for item in items[1:]:
        if any(_same_reading(item, earlier) for earlier in seen):
            continue
        if len(job["cheques"]) + len(created) >= MAX_PICTURES:
            break
        extra = new_cheque(cheque["page"], cheque["image"], _spare_number(job, created))
        extra["ocr_hint"] = cheque.get("ocr_hint", "")
        extra["shared"] = True
        apply_ai(extra, item)
        created.append(extra)
        seen.append(item)
    insert_cheques(job, cheque["id"], created)


def _spare_number(job: dict[str, Any], pending: list[dict]) -> int:
    used = set()
    for cheque in list(job["cheques"]) + pending:
        match = re.fullmatch(r"c(\d+)", str(cheque.get("id", "")))
        if match:
            used.add(int(match.group(1)))
    number = 1
    while number in used:
        number += 1
    return number


def apply_ai(cheque: dict[str, Any], result: dict[str, Any]) -> None:
    is_cheque = bool(result.get("is_cheque"))
    cheque["confidence"] = as_confidence(result.get("confidence"))
    illegible = result.get("illegible") or []
    if not isinstance(illegible, list):
        illegible = []
    cheque["illegible"] = [_clip(item, 40) for item in illegible if _clip(item, 40)][:12]
    cheque["error"] = ""
    cheque["source"] = "ai"
    if is_cheque:
        cheque["role"] = "cheque"
        cheque["typed_text"] = ""
        for key, _label in TEXT_FIELDS:
            if key in result and result[key] is not None:
                cheque["fields"][key] = _clip(result[key], 500)
        signature = str(result.get("signature_present", "")).strip().lower()
        if signature in {"yes", "true", "present", "signed"}:
            cheque["fields"]["signature_present"] = "yes"
        elif signature in {"no", "false", "absent", "unsigned"}:
            cheque["fields"]["signature_present"] = "no"
        else:
            cheque["fields"]["signature_present"] = "unknown"
        cheque["fields"]["notes"] = _clip(result.get("notes", ""), 1000)
    else:
        typed = _clip(result.get("typed_text", ""), 20000)
        cheque["typed_text"] = typed
        cheque["role"] = "typed" if typed else "omit"
        cheque["fields"] = empty_fields()
        if result.get("notes"):
            cheque["fields"]["notes"] = _clip(result.get("notes"), 1000)
    cheque["warnings"] = warnings_for(cheque)


def apply_form(job: dict[str, Any], form: Any, mark_reviewed: bool = False) -> None:
    for cheque in job["cheques"]:
        prefix = cheque["id"] + "__"
        role = str(form.get(prefix + "role") or cheque["role"])
        cheque["role"] = role if role in ROLES else "cheque"
        for key, _label in TEXT_FIELDS:
            if prefix + key in form:
                cheque["fields"][key] = _clip(form.get(prefix + key), 500)
        if prefix + "signature_present" in form:
            signature = str(form.get(prefix + "signature_present") or "")
            cheque["fields"]["signature_present"] = signature if signature in SIGNATURES else "unknown"
        if prefix + "notes" in form:
            cheque["fields"]["notes"] = _clip(form.get(prefix + "notes"), 1000)
        if prefix + "typed_text" in form:
            cheque["typed_text"] = _clip(form.get(prefix + "typed_text"), 20000)
        filled = _has_details(cheque["fields"]) or bool(cheque.get("typed_text", "").strip())
        if cheque["source"] == "pending" and filled:
            cheque["source"] = "manual"
        elif mark_reviewed and cheque["source"] == "ai" and filled:
            cheque["source"] = "reviewed"
        cheque["warnings"] = warnings_for(cheque)


def public_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-…", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer …", text, flags=re.I)
    return text[:400]
