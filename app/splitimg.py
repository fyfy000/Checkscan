"""Split one bitmap into separate cheques when a blank gutter divides them."""

from __future__ import annotations

from PIL import Image

_INK = 226


def split_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Return crop boxes when one picture holds two or more cheques.

    A gutter counts only when a band is blank from edge to edge. A line of
    writing or a cheque border keeps the picture together. One cheque comes
    back as an empty list.
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 140 or height < 140:
        return []
    scale = 400 / width if width > 400 else 1.0
    small_w = max(1, int(round(width * scale)))
    small_h = max(1, int(round(height * scale)))
    small = rgb.resize((small_w, small_h), Image.Resampling.BOX)
    raw = small.tobytes()

    def blank_row(y: int) -> bool:
        start = y * small_w * 3
        row = raw[start : start + small_w * 3]
        return all(channel >= _INK for channel in row)

    def blank_col(x: int) -> bool:
        for y in range(small_h):
            i = (y * small_w + x) * 3
            if raw[i] < _INK or raw[i + 1] < _INK or raw[i + 2] < _INK:
                return False
        return True

    horizontal = _intervals([blank_row(y) for y in range(small_h)])
    vertical = _intervals([blank_col(x) for x in range(small_w)])
    if len(horizontal) >= 2 and len(horizontal) >= len(vertical):
        spans = horizontal
        axis = "y"
    elif len(vertical) >= 2:
        spans = vertical
        axis = "x"
    else:
        return []
    if len(spans) > 4:
        return []

    boxes = []
    for start, end in spans:
        if axis == "y":
            box = (0, int(start / scale), width, min(height, int(round(end / scale))))
        else:
            box = (int(start / scale), 0, min(width, int(round(end / scale))), height)
        trimmed = _trim(rgb, box)
        if trimmed is None:
            return []
        boxes.append(trimmed)
    if len(boxes) < 2:
        return []
    if _overlaps(boxes):
        return []
    return boxes


def _intervals(blank: list[bool]) -> list[tuple[int, int]]:
    length = len(blank)
    min_band = max(8, int(length * 0.04))
    edge = max(4, int(length * 0.06))
    min_piece = max(min_band, int(length * 0.18))
    cuts: list[tuple[int, int]] = []
    index = 0
    while index < length:
        if not blank[index]:
            index += 1
            continue
        start = index
        while index < length and blank[index]:
            index += 1
        if index - start >= min_band and start >= edge and index <= length - edge:
            cuts.append((start, index))
    if not cuts:
        return []
    points = [0]
    for start, end in cuts:
        points.extend((start, end))
    points.append(length)
    spans = []
    for index in range(0, len(points), 2):
        start, end = points[index], points[index + 1]
        if end - start >= min_piece:
            spans.append((start, end))
    return spans


def _trim(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = box
    crop = image.crop((x0, y0, x1, y1))
    width, height = crop.size
    raw = crop.tobytes()
    min_x, min_y, max_x, max_y = width, height, 0, 0
    found = False
    for y in range(height):
        row = raw[y * width * 3 : (y + 1) * width * 3]
        for x in range(width):
            i = x * 3
            if row[i] < _INK or row[i + 1] < _INK or row[i + 2] < _INK:
                found = True
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x + 1 > max_x:
                    max_x = x + 1
                if y + 1 > max_y:
                    max_y = y + 1
    if not found:
        return None
    pad = 10
    left = max(0, x0 + min_x - pad)
    top = max(0, y0 + min_y - pad)
    right = min(image.size[0], x0 + max_x + pad)
    bottom = min(image.size[1], y0 + max_y + pad)
    if right - left < 80 or bottom - top < 50:
        return None
    area = image.size[0] * image.size[1]
    if (right - left) * (bottom - top) < area * 0.08:
        return None
    return (left, top, right, bottom)


def _overlaps(boxes: list[tuple[int, int, int, int]]) -> bool:
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            width = min(first[2], second[2]) - max(first[0], second[0])
            height = min(first[3], second[3]) - max(first[1], second[1])
            if width > 0 and height > 0:
                shared = width * height
                smaller = min(
                    (first[2] - first[0]) * (first[3] - first[1]),
                    (second[2] - second[0]) * (second[3] - second[1]),
                )
                if smaller and shared / smaller > 0.25:
                    return True
    return False
