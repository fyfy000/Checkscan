"""A typed remittance advice with a picture of a handwritten cheque."""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

CALIBRI = Path(r"C:\Windows\Fonts\calibri.ttf")
CALIBRI_BOLD = Path(r"C:\Windows\Fonts\calibrib.ttf")
GEORGIA = Path(r"C:\Windows\Fonts\georgia.ttf")
CONSOLAS = Path(r"C:\Windows\Fonts\consola.ttf")
SCRIPT = Path(r"C:\Windows\Fonts\segoesc.ttf")
PRINT = Path(r"C:\Windows\Fonts\segoepr.ttf")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _script(base: Image.Image, text: str, font: ImageFont.FreeTypeFont, xy: tuple[int, int], angle: float = 0) -> None:
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    layer = Image.new("RGBA", (right - left + 24, bottom - top + 24), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((12 - left, 12 - top), text, font=font, fill=(27, 43, 64, 255))
    if angle:
        layer = layer.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    base.alpha_composite(layer, dest=xy)


def draw_cheque(path: Path) -> None:
    width, height = 1800, 780
    image = Image.new("RGBA", (width, height), "#f4efe2")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 16, width - 16, height - 16), radius=18, outline="#1e4d3c", width=5)
    draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=12, outline="#1e4d3c", width=1)
    draw.ellipse((58, 52, 138, 132), outline="#1e4d3c", width=3)
    draw.text((74, 68), "HT", font=_font(CALIBRI_BOLD if CALIBRI_BOLD.is_file() else CALIBRI, 32), fill="#1e4d3c")
    draw.text((160, 52), "Harbour Trust", font=_font(GEORGIA, 52), fill="#1e4d3c")
    draw.text((162, 118), "King Street branch", font=_font(CALIBRI, 24), fill="#5e6a62")
    draw.text((1468, 64), "No.", font=_font(CALIBRI, 24), fill="#5e6a62")
    draw.text((1530, 56), "1042", font=_font(CONSOLAS, 36), fill="#1e4d3c")

    draw.text((1188, 176), "Date", font=_font(CALIBRI, 24), fill="#5e6a62")
    draw.line((1288, 214, 1710, 214), fill="#8aa097", width=2)
    _script(image, "3 April 2026", _font(PRINT, 40), (1288, 158), -1)

    draw.text((58, 286), "Pay to the order of", font=_font(CALIBRI, 24), fill="#5e6a62")
    draw.line((330, 334, 1340, 334), fill="#8aa097", width=2)
    _script(image, "Harbour & Co.", _font(SCRIPT, 48), (348, 262), 1)

    draw.rounded_rectangle((1400, 258, 1724, 348), radius=8, outline="#1e4d3c", width=3)
    _script(image, "$1,200.00", _font(PRINT, 42), (1424, 268), 0)

    draw.line((58, 468, 1540, 468), fill="#8aa097", width=2)
    draw.text((1560, 428), "Dollars", font=_font(CALIBRI, 24), fill="#5e6a62")
    _script(image, "One thousand two hundred dollars only", _font(SCRIPT, 36), (64, 400), -0.6)

    draw.text((58, 530), "Memo", font=_font(CALIBRI, 24), fill="#5e6a62")
    draw.line((150, 572, 900, 572), fill="#8aa097", width=2)
    _script(image, "INV-4481 and INV-4502", _font(SCRIPT, 32), (164, 508), 1)

    draw.line((1120, 660, 1710, 660), fill="#8aa097", width=2)
    _script(image, "A. Chen", _font(SCRIPT, 46), (1240, 575), -5)
    draw.text((1120, 672), "Authorized signature", font=_font(CALIBRI, 20), fill="#7d8a84")
    draw.text((58, 704), "Sample for reading practice  ·  not a negotiable cheque", font=_font(CALIBRI, 20), fill="#8d735c")
    image.convert("RGB").save(path, "PNG")


def write_sample(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        cheque_path = Path(temporary) / "cheque.png"
        draw_cheque(cheque_path)
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        sans, bold, mono = "helv", "hebo", "cour"
        page.draw_rect(fitz.Rect(0, 0, 612, 96), color=None, fill=(0.12, 0.30, 0.24))
        page.insert_text((40, 46), "HARBOUR & CO.", fontname=bold, fontsize=20, color=(1, 1, 1))
        page.insert_text((40, 72), "REMITTANCE ADVICE", fontname=sans, fontsize=12, color=(0.86, 0.93, 0.88))
        page.insert_text((360, 46), "Account AC-20418", fontname=sans, fontsize=11, color=(1, 1, 1))
        page.insert_text((360, 68), "3 April 2026", fontname=sans, fontsize=11, color=(1, 1, 1))

        body = (0.12, 0.10, 0.08)
        muted = (0.35, 0.32, 0.28)
        page.insert_text((40, 140), "Invoices settled by the enclosed cheque.", fontname=sans, fontsize=12, color=body)
        headers = ((40, "Invoice"), (130, "Date"), (250, "Description"), (470, "Amount"))
        for x, label in headers:
            page.insert_text((x, 178), label, fontname=sans, fontsize=9, color=muted)
        page.draw_line(fitz.Point(40, 186), fitz.Point(560, 186), color=(0.7, 0.64, 0.56), width=0.6)
        rows = (
            (210, "INV-4481", "12 Mar 2026", "Workshop supplies", "1,240.00"),
            (234, "INV-4502", "28 Mar 2026", "Delivery adjustment", "-40.00"),
        )
        for y, invoice, date, description, amount in rows:
            page.insert_text((40, y), invoice, fontname=mono, fontsize=11, color=body)
            page.insert_text((130, y), date, fontname=mono, fontsize=11, color=body)
            page.insert_text((250, y), description, fontname=mono, fontsize=11, color=body)
            page.insert_text((470, y), amount, fontname=mono, fontsize=11, color=body)
        page.draw_line(fitz.Point(40, 258), fitz.Point(560, 258), color=(0.7, 0.64, 0.56), width=0.6)
        page.insert_text((250, 286), "Amount due", fontname=bold, fontsize=12, color=body)
        page.insert_text((470, 286), "1,200.00", fontname=bold, fontsize=12, color=body)
        page.insert_text(
            (40, 340),
            "Please retain this advice with the cheque copy.",
            fontname=sans,
            fontsize=11,
            color=body,
        )
        cheque_page = document.new_page(width=792, height=343)
        cheque_page.insert_image(cheque_page.rect, filename=str(cheque_path))
        document.save(path)
        document.close()
