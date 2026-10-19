import time
from urllib.parse import urlparse

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.compose import compose
from app.extract import extract_document
from app.main import app
from app.records import warnings_for, words_to_amount
from app.sample_pdf import write_sample
from app.store import load_settings, save_settings

client = TestClient(app)


def cheque_form(**overrides):
    data = {
        "c1__role": "cheque",
        "c1__cheque_number": "1042",
        "c1__date": "3 April 2026",
        "c1__payee": "Harbour & Co.",
        "c1__amount_numeric": "$1,200.00",
        "c1__amount_words": "One thousand two hundred dollars only",
        "c1__currency": "CAD",
        "c1__bank_name": "Harbour Trust",
        "c1__drawer": "A. Chen",
        "c1__memo": "INV-4481 and INV-4502",
        "c1__signature_present": "yes",
        "c1__branch": "King Street",
        "c1__micr": "",
        "c1__notes": "",
        "c1__typed_text": "",
    }
    data.update(overrides)
    return data


def job_path(response):
    assert response.status_code == 303
    return urlparse(response.headers["location"]).path


def pdf_text(content: bytes) -> str:
    document = fitz.open(stream=content, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in document)
    finally:
        document.close()


def test_amount_words():
    assert words_to_amount("One thousand two hundred dollars only") == 1200
    assert words_to_amount("One thousand two hundred forty dollars and 50/100") == 1240.50
    assert words_to_amount("twelve hundred dollars") == 1200
    assert words_to_amount("two hundred and five dollars") == 205


def test_home_and_settings_pages():
    home = client.get("/")
    assert home.status_code == 200
    assert "Handwritten cheques, set in type." in home.text
    assert "Drop a customer PDF" in home.text
    assert "Try the sample PDF" not in home.text
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "Model settings" in settings.text


def test_settings_keep_the_key_out_of_the_page():
    save_settings(
        {"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-local-test-9f3c"}
    )
    page = client.get("/settings")
    assert page.status_code == 200
    assert "sk-local-test-9f3c" not in page.text
    assert "9f3c" in page.text
    updated = client.post(
        "/settings",
        data={"base_url": "https://example.test/v1", "model": "local-vision", "api_key": ""},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    stored = load_settings()
    assert stored["api_key"] == "sk-local-test-9f3c"
    assert stored["model"] == "local-vision"
    assert stored["base_url"] == "https://example.test/v1"
    rejected = client.post(
        "/settings",
        data={"base_url": "javascript:alert(1)", "model": "x", "api_key": ""},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert "http" in rejected.headers["location"]
    save_settings({"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": ""})
    check = client.post("/settings/test", json={"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": ""})
    assert check.status_code == 400
    assert check.json()["ok"] is False


def test_rejects_a_file_that_is_not_a_pdf():
    response = client.post(
        "/jobs",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" in response.headers["location"]
    broken = client.post(
        "/jobs",
        files={"file": ("x.pdf", b"%PDF-not-really", "application/pdf")},
        follow_redirects=False,
    )
    assert broken.status_code == 303
    page = client.get(broken.headers["location"])
    assert page.status_code == 200
    assert "could not" in page.text.lower() or "pdf" in page.text.lower()


def test_sample_splits_typed_pages_from_the_cheque_picture(tmp_path):
    pdf = tmp_path / "sample.pdf"
    write_sample(pdf)
    info = extract_document(pdf, tmp_path)
    assert info["pages"][0]["role"] == "typescript"
    assert info["pages"][1]["role"] == "picture"
    assert len(info["cheques"]) == 1
    joined = info["typescript"][0]["text"]
    assert "REMITTANCE ADVICE" in joined
    assert "Workshop supplies" in joined
    assert "A. Chen" not in joined
    assert "One thousand" not in joined
    assert (tmp_path / info["cheques"][0]["image"]).is_file()


def test_crucial_pages_and_print_file():
    created = client.post("/sample", follow_redirects=False)
    path = job_path(created)
    review = client.get(path)
    assert review.status_code == 200
    assert "REMITTANCE ADVICE" in review.text
    assert "Workshop supplies" in review.text
    assert review.text.count("What should this picture become?") == 1
    assert ">Save</button>" not in review.text
    assert "Nothing typed yet." in review.text
    processing = client.get(path + "/processing")
    assert processing.status_code == 200
    assert "No reading in progress" in processing.text
    image = client.get(path + "/images/cheque_1.png")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    blocked = client.get(path + "/images/job.json")
    assert blocked.status_code == 404

    mismatch = client.post(path + "/save", data=cheque_form(c1__amount_words="One hundred dollars only"), follow_redirects=False)
    saved = client.get(job_path(mismatch))
    assert "do not agree" in saved.text
    assert "100.00" in saved.text

    built = client.post(path + "/build", data=cheque_form(), follow_redirects=False)
    result_path = job_path(built)
    result = client.get(result_path)
    assert result.status_code == 200
    assert "Print file ready" in result.text
    assert "Harbour Trust" in result.text
    assert "The figure and the words agree." in result.text
    assert "REMITTANCE ADVICE" in result.text
    download = client.get(path + "/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    text = pdf_text(download.content)
    assert "REMITTANCE ADVICE" in text
    assert "Workshop supplies" in text
    assert "CHEQUE PRINT RECORD" in text
    assert "Harbour Trust" in text
    assert "A. Chen" in text
    assert "The figure and the words agree." in text
    assert "Not a negotiable cheque." in text
    preview = client.get(path + "/preview")
    assert preview.status_code == 200
    assert "inline" in preview.headers["content-disposition"]
    table = client.get(path + "/csv")
    assert table.status_code == 200
    assert "Harbour Trust" in table.text
    missing = client.get("/jobs/not-a-real-file")
    assert missing.status_code == 404
    assert "not on this computer" in missing.text


def test_model_read_fills_the_cheque(monkeypatch):
    def fake_read(image_bytes, settings, hint=""):
        assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        return {
            "is_cheque": True,
            "cheque_number": "1042",
            "date": "3 April 2026",
            "payee": "Harbour & Co.",
            "amount_numeric": "$1,200.00",
            "amount_words": "One thousand two hundred dollars only",
            "currency": "CAD",
            "bank_name": "Harbour Trust",
            "branch": "King Street",
            "drawer": "A. Chen",
            "memo": "INV-4481 and INV-4502",
            "signature_present": "yes",
            "confidence": 0.91,
            "illegible": [],
            "notes": "",
        }

    monkeypatch.setattr("app.main.transcribe_image", fake_read)
    save_settings({"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-local-test-9f3c"})
    path = job_path(client.post("/sample", follow_redirects=False))
    job_id = path.rsplit("/", 1)[-1]
    started = client.post(path + "/transcribe", data={}, follow_redirects=False)
    assert job_path(started).endswith("/processing")
    deadline = time.time() + 5
    status = {}
    while time.time() < deadline:
        status = client.get(path + "/status").json()
        if status.get("status") != "transcribing":
            break
        time.sleep(0.05)
    assert status.get("status") == "review"
    page = client.get(path)
    assert 'value="Harbour &amp; Co."' in page.text or "Harbour &amp; Co." in page.text
    assert "Harbour Trust" in page.text
    assert "Read by the model" in page.text
    assert "91% confidence" in page.text
    assert "The figure and the words agree." in page.text

    def other_read(image_bytes, settings, hint=""):
        return {"is_cheque": True, "payee": "Someone Else", "amount_numeric": "$5.00", "amount_words": "Five dollars only", "confidence": 0.4}

    monkeypatch.setattr("app.main.transcribe_image", other_read)
    client.post(path + "/transcribe", data={}, follow_redirects=False)
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get(path + "/status").json()
        if status.get("status") != "transcribing":
            break
        time.sleep(0.05)
    kept = client.get(path)
    assert "Harbour Trust" in kept.text
    assert "Someone Else" not in kept.text

    client.post(path + "/transcribe", data={"overwrite": "on"}, follow_redirects=False)
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get(path + "/status").json()
        if status.get("status") != "transcribing":
            break
        time.sleep(0.05)
    replaced = client.get(path)
    assert "Someone Else" in replaced.text
    assert job_id


def test_typed_picture_and_omitted_cheque():
    path = job_path(client.post("/sample", follow_redirects=False))
    omitted = client.post(path + "/build", data=cheque_form(**{"c1__role": "omit"}), follow_redirects=False)
    assert omitted.status_code == 303
    text = pdf_text(client.get(path + "/download").content)
    assert "REMITTANCE ADVICE" in text
    assert "CHEQUE PRINT RECORD" not in text

    path = job_path(client.post("/sample", follow_redirects=False))
    typed = client.post(
        path + "/build",
        data=cheque_form(**{"c1__role": "typed", "c1__typed_text": "Scanned letter about the enclosed payment."}),
        follow_redirects=False,
    )
    assert typed.status_code == 303
    text = pdf_text(client.get(path + "/download").content)
    assert "Scanned letter about the enclosed payment." in text
    assert "CHEQUE PRINT RECORD" not in text

    path = job_path(client.post("/sample", follow_redirects=False))
    blank = client.post(
        path + "/build",
        data=cheque_form(**{"c1__role": "typed", "c1__typed_text": ""}),
        follow_redirects=False,
    )
    assert blank.status_code == 303
    text = pdf_text(client.get(path + "/download").content)
    assert "REMITTANCE ADVICE" in text
    assert "TYPED PAGE" in text
    assert "CHEQUE PRINT RECORD" not in text


def _framed(draw, box):
    draw.rectangle(box, outline=(30, 77, 60), width=6)
    draw.line((box[0] + 24, box[1] + 70, box[2] - 24, box[1] + 70), fill=(30, 77, 60), width=3)


def test_two_cheques_in_one_bitmap_become_two_pictures(tmp_path):
    image_path = tmp_path / "pair.png"
    image = Image.new("RGB", (900, 980), "white")
    draw = ImageDraw.Draw(image)
    _framed(draw, (40, 30, 860, 400))
    _framed(draw, (40, 560, 860, 940))
    image.save(image_path)
    pdf = tmp_path / "pair.pdf"
    document = fitz.open()
    page = document.new_page(width=450, height=490)
    page.insert_image(page.rect, filename=str(image_path))
    document.save(pdf)
    document.close()

    info = extract_document(pdf, tmp_path)
    assert len(info["cheques"]) == 2
    assert [item["kind"] for item in info["flow"]] == ["image", "image"]


def test_one_framed_cheque_is_not_split(tmp_path):
    image_path = tmp_path / "one.png"
    image = Image.new("RGB", (900, 420), "white")
    draw = ImageDraw.Draw(image)
    _framed(draw, (30, 24, 870, 390))
    image.save(image_path)
    from app.splitimg import split_boxes

    assert split_boxes(image) == []


def test_model_keeps_a_second_cheque_from_the_same_picture(monkeypatch):
    def fake_read(image_bytes, settings, hint=""):
        return {
            "cheques": [
                {
                    "is_cheque": True,
                    "cheque_number": "1042",
                    "payee": "Harbour & Co.",
                    "amount_numeric": "$1,200.00",
                    "amount_words": "One thousand two hundred dollars only",
                    "confidence": 0.9,
                },
                {
                    "is_cheque": True,
                    "cheque_number": "1044",
                    "payee": "City Water Dept.",
                    "amount_numeric": "$46.20",
                    "amount_words": "Forty six dollars and 20/100",
                    "confidence": 0.8,
                },
            ]
        }

    monkeypatch.setattr("app.main.transcribe_image", fake_read)
    save_settings({"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-local-test-9f3c"})
    path = job_path(client.post("/sample", follow_redirects=False))
    started = client.post(path + "/transcribe", data={}, follow_redirects=False)
    assert job_path(started).endswith("/processing")
    deadline = time.time() + 5
    status = {}
    while time.time() < deadline:
        status = client.get(path + "/status").json()
        if status.get("status") != "transcribing":
            break
        time.sleep(0.05)
    assert status.get("status") == "review"
    page = client.get(path)
    assert "City Water Dept." in page.text
    assert "Another cheque was on this same picture." in page.text
    assert page.text.count("What should this picture become?") == 2
    built = client.post(path + "/build", data=cheque_form(), follow_redirects=False)
    assert built.status_code == 303
    text = pdf_text(client.get(path + "/download").content)
    assert text.count("CHEQUE PRINT RECORD") == 2
    assert "City Water Dept." in text
    save_settings({"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": ""})


def test_three_small_cheques_on_one_page_are_all_kept(tmp_path):
    """Cheques placed small on one sheet must each become their own picture.

    Matches a real packet where three photos sat on a blank page and two of
    them covered less than a tenth of the sheet.
    """
    pdf = tmp_path / "123.pdf"
    document = fitz.open()
    page = document.new_page(width=595.3, height=841.9)
    boxes = (
        fitz.Rect(90.0, 109.8, 311.4, 205.8),
        fitz.Rect(90.0, 216.6, 392.0, 347.4),
        fitz.Rect(90.0, 356.4, 504.5, 536.0),
    )
    for index, box in enumerate(boxes, start=1):
        image = tmp_path / f"cheque-{index}.png"
        Image.new("RGB", (1800, 780), (240, 220, 180)).save(image)
        page.insert_image(box, filename=str(image))
    document.save(pdf)
    document.close()

    info = extract_document(pdf, tmp_path)
    assert len(info["cheques"]) == 3
    assert [item["page"] for item in info["cheques"]] == [1, 1, 1]
    assert info["pages"][0]["role"] == "picture"


def test_reading_page_has_no_way_back_while_the_model_runs():
    from app.store import load_job, save_job

    created = client.post("/sample", follow_redirects=False)
    path = job_path(created)
    job_id = path.rsplit("/", 1)[-1]
    job = load_job(job_id)
    job["status"] = "transcribing"
    save_job(job)
    page = client.get(path + "/processing")
    assert page.status_code == 200
    assert "Reading the handwriting" in page.text
    assert "Back to the file" not in page.text


def test_mixed_page_keeps_the_typed_words(tmp_path):
    pdf = tmp_path / "mixed.pdf"
    image = tmp_path / "pic.png"
    Image.new("RGB", (800, 320), "#f4e6c8").save(image)
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    sentence = "REMITTANCE ADVICE stays on the typed page. " + " ".join(["Workshop"] * 30)
    page.insert_text((72, 80), sentence, fontsize=11)
    page.insert_image(fitz.Rect(72, 220, 472, 370), filename=str(image))
    document.save(pdf)
    document.close()

    info = extract_document(pdf, tmp_path)
    assert info["pages"][0]["role"] == "mixed"
    assert info["cheques"]
    assert any(item["kind"] == "reflow" for item in info["flow"])
    assert not any(item["kind"] == "copy" for item in info["flow"])
    cheque = info["cheques"][0]
    cheque["fields"].update(
        {
            "cheque_number": "88",
            "date": "1 May 2026",
            "payee": "Northwind Supplies",
            "amount_numeric": "$80.00",
            "amount_words": "Eighty dollars only",
            "bank_name": "North Bank",
            "drawer": "J. Patel",
        }
    )
    cheque["warnings"] = warnings_for(cheque)
    out = tmp_path / "out.pdf"
    compose({"filename": "mixed.pdf", "cheques": info["cheques"], "flow": info["flow"]}, pdf, out)
    text = pdf_text(out.read_bytes())
    assert "REMITTANCE ADVICE" in text
    assert "CHEQUE PRINT RECORD" in text
    assert "Northwind Supplies" in text


def test_delete_removes_the_file_from_the_list():
    path = job_path(client.post("/sample", follow_redirects=False))
    removed = client.post(path + "/delete", follow_redirects=False)
    assert removed.status_code == 303
    missing = client.get(path)
    assert missing.status_code == 404


def test_read_without_a_key_stays_on_the_review_page():
    save_settings({"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": ""})
    path = job_path(client.post("/sample", follow_redirects=False))
    response = client.post(path + "/transcribe", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert urlparse(response.headers["location"]).path == path
    page = client.get(response.headers["location"])
    assert "Add a vision model" in page.text
