"""ChequeScript web app."""

from __future__ import annotations

import copy
import csv
import logging
import re
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import urlencode

import fitz
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.compose import compose
from app.extract import extract_document
from app.records import (
    MORE_FIELDS,
    PRIMARY_FIELDS,
    apply_form,
    divide_picture,
    public_error,
    store_reading,
    warnings_for,
)
from app.sample_pdf import write_sample
from app.store import (
    Missing,
    delete_job,
    effective_settings,
    job_dir,
    list_jobs,
    load_job,
    load_settings,
    now_stamp,
    save_job,
    save_settings,
    settings_hint,
)
from app.transcribe import ping_model, transcribe_image

log = logging.getLogger("chequescript")
BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals["PRIMARY_FIELDS"] = PRIMARY_FIELDS
templates.env.globals["MORE_FIELDS"] = MORE_FIELDS
templates.env.globals["STATUS_LABEL"] = {
    "review": "Needs review",
    "transcribing": "Reading handwriting",
    "built": "Print file ready",
    "error": "Needs attention",
}
templates.env.globals["PAGE_LABEL"] = {
    "typescript": "Original typed page",
    "mixed": "Typed page, picture pulled out",
    "picture": "Picture page",
    "blank": "Blank page, left out",
}

app = FastAPI(title="ChequeScript")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(job_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _locks[job_id] = lock
        return lock


def _shortdate(value: str) -> str:
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d %b %Y · %H:%M")


templates.env.filters["shortdate"] = _shortdate
templates.env.globals["range"] = range


def render(request: Request, name: str, status_code: int = 200, **context):
    saved = request.query_params.get("saved", "")
    notices = {"1": "Saved.", "settings": "Settings saved."}
    context.setdefault("error", request.query_params.get("error", ""))
    context.setdefault("notice", notices.get(saved, ""))
    context.setdefault("active", "")
    context["request"] = request
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def redirect(url: str, error: str = "", saved: str = "") -> RedirectResponse:
    params = {}
    if error:
        params["error"] = error[:300]
    if saved:
        params["saved"] = saved
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    return RedirectResponse(url, status_code=303)


def safe_filename(name: str) -> str:
    cleaned = Path(name or "").name
    cleaned = re.sub(r"[^A-Za-z0-9._ ()-]", "", cleaned).strip() or "document.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned[:120]


def download_stem(job: dict) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]", "", Path(job["filename"]).stem).strip() or "file"
    return f"ChequeScript - {stem[:80]}"


def _load(job_id: str) -> dict:
    try:
        return load_job(job_id)
    except Missing as exc:
        raise Missing() from exc


def _new_job(filename: str, pdf_path: Path, job_id: str) -> dict:
    extracted = extract_document(pdf_path, pdf_path.parent)
    job = {
        "id": job_id,
        "filename": filename,
        "created_at": now_stamp(),
        "status": "review",
        "error": "",
        "output": "",
        "printed": None,
        "overwrite": False,
        **extracted,
    }
    for cheque in job["cheques"]:
        cheque["warnings"] = warnings_for(cheque)
    save_job(job)
    return job


def create_job(filename: str, data: bytes) -> dict:
    job_id = uuid.uuid4().hex
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / "source.pdf"
    try:
        pdf_path.write_bytes(data)
        return _new_job(safe_filename(filename), pdf_path, job_id)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def create_sample_job() -> dict:
    job_id = uuid.uuid4().hex
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / "source.pdf"
    try:
        write_sample(pdf_path)
        return _new_job("Sample remittance and cheque.pdf", pdf_path, job_id)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _write_previews(pdf_path: Path, directory: Path) -> int:
    document = fitz.open(pdf_path)
    count = 0
    try:
        for index, page in enumerate(document):
            if index >= 6:
                break
            page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False).save(directory / f"preview_{index + 1}.png")
            count += 1
    finally:
        document.close()
    return count


def write_csv(path: Path, cheques: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "cheque_number",
                "date",
                "payee",
                "amount_numeric",
                "amount_words",
                "currency",
                "bank_name",
                "branch",
                "drawer",
                "memo",
                "signature_present",
                "micr",
                "notes",
                "source_page",
                "confidence",
                "warnings",
            ]
        )
        for cheque in cheques:
            fields = cheque["fields"]
            warnings = " | ".join(item["text"] for item in cheque.get("warnings", []) if item.get("level") == "warn")
            writer.writerow(
                [
                    fields.get("cheque_number", ""),
                    fields.get("date", ""),
                    fields.get("payee", ""),
                    fields.get("amount_numeric", ""),
                    fields.get("amount_words", ""),
                    fields.get("currency", ""),
                    fields.get("bank_name", ""),
                    fields.get("branch", ""),
                    fields.get("drawer", ""),
                    fields.get("memo", ""),
                    fields.get("signature_present", ""),
                    fields.get("micr", ""),
                    fields.get("notes", ""),
                    cheque.get("page", ""),
                    "" if cheque.get("confidence") is None else cheque.get("confidence"),
                    warnings,
                ]
            )


def run_transcription(job_id: str) -> None:
    try:
        with _lock(job_id):
            job = load_job(job_id)
            if job.get("status") != "transcribing":
                return
            settings = effective_settings()
            overwrite = bool(job.get("overwrite"))
            attempted = 0
            failed = 0
            queue = list(job["cheques"])
            seen: set[str] = set()
            while queue:
                cheque = queue.pop(0)
                if cheque["id"] in seen:
                    continue
                seen.add(cheque["id"])
                if cheque.get("source") in {"manual", "reviewed", "ai"} and not overwrite:
                    continue
                pieces = divide_picture(job, cheque, job_dir(job_id))
                queue = pieces + queue
                attempted += 1
                image = job_dir(job_id) / cheque["image"]
                try:
                    result = transcribe_image(image.read_bytes(), settings, hint=cheque.get("ocr_hint", ""))
                    store_reading(job, cheque, result)
                except Exception as exc:
                    log.exception("cheque read failed")
                    cheque["error"] = public_error(exc)
                    cheque["source"] = "error"
                    cheque["warnings"] = warnings_for(cheque)
                    failed += 1
                save_job(job)
            if attempted and failed == attempted:
                job["error"] = (
                    "The model could not read these pictures. Type the details yourself, or check Settings and try again."
                )
            else:
                job["error"] = ""
            job["status"] = "review"
            job["overwrite"] = False
            save_job(job)
    except Exception as exc:
        log.exception("transcription failed")
        try:
            job = load_job(job_id)
            job["status"] = "error"
            job["error"] = public_error(exc)
            save_job(job)
        except Exception:
            log.exception("could not store the transcription error")


@app.exception_handler(Missing)
async def missing(request: Request, exc: Missing):
    return render(request, "missing.html", status_code=404)


@app.get("/")
def home(request: Request):
    return render(request, "home.html", active="home", jobs=list_jobs())


@app.get("/settings")
def settings_page(request: Request):
    settings = effective_settings()
    return render(
        request,
        "settings.html",
        active="settings",
        base_url=settings["base_url"],
        model=settings["model"],
        hint=settings_hint(settings),
        has_key=bool(settings.get("api_key")),
    )


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    base_url = str(form.get("base_url") or "").strip().rstrip("/")
    model = str(form.get("model") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        return redirect("/settings", error="The model address must start with http:// or https://.")
    if not model:
        return redirect("/settings", error="Add a model name.")
    stored = load_settings()
    stored["base_url"] = base_url
    stored["model"] = model
    key = str(form.get("api_key") or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if key:
        stored["api_key"] = key
    save_settings(stored)
    return redirect("/settings", saved="settings")


@app.post("/settings/test")
async def settings_test(request: Request):
    payload = await request.json()
    settings = effective_settings()
    if str(payload.get("api_key") or "").strip():
        settings["api_key"] = str(payload.get("api_key")).strip()
    if str(payload.get("base_url") or "").strip():
        settings["base_url"] = str(payload.get("base_url")).strip().rstrip("/")
    if str(payload.get("model") or "").strip():
        settings["model"] = str(payload.get("model")).strip()
    if not settings.get("api_key"):
        return JSONResponse({"ok": False, "message": "Add an API key first."}, status_code=400)
    try:
        ping_model(settings)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": public_error(exc)}, status_code=400)
    return {"ok": True, "message": "Connected. The model answered. Handwriting needs a vision model such as gpt-4o."}


@app.post("/jobs")
async def upload(file: UploadFile = File(...)):
    name = file.filename or "document.pdf"
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        return redirect("/", error="That PDF is larger than 25 MB.")
    if not name.lower().endswith(".pdf") or not data.startswith(b"%PDF"):
        return redirect("/", error="Upload a PDF file.")
    try:
        job = create_job(name, data)
    except ValueError as exc:
        return redirect("/", error=str(exc))
    except Exception:
        log.exception("upload failed")
        return redirect("/", error="That PDF could not be read.")
    return redirect(f"/jobs/{job['id']}")


@app.post("/sample")
def sample():
    try:
        job = create_sample_job()
    except ValueError as exc:
        return redirect("/", error=str(exc))
    except Exception:
        log.exception("sample failed")
        return redirect("/", error="The sample file could not be prepared.")
    return redirect(f"/jobs/{job['id']}")


@app.get("/jobs/{job_id}")
def review(request: Request, job_id: str):
    try:
        job = _load(job_id)
    except Missing:
        return render(request, "missing.html", status_code=404)
    settings = effective_settings()
    return render(
        request,
        "review.html",
        active="home",
        job=job,
        has_key=bool(settings.get("api_key")),
    )


@app.post("/jobs/{job_id}/transcribe")
async def transcribe(job_id: str, request: Request):
    try:
        _load(job_id)
    except Missing:
        return render(request, "missing.html", status_code=404)
    form = await request.form()
    settings = effective_settings()
    if not settings.get("api_key"):
        return redirect(f"/jobs/{job_id}", error="Add a vision model in Settings before reading handwriting.")
    with _lock(job_id):
        job = load_job(job_id)
        if not job["cheques"]:
            return redirect(f"/jobs/{job_id}", error="This PDF has no pictures to read.")
        job["status"] = "transcribing"
        job["error"] = ""
        job["overwrite"] = form.get("overwrite") == "on"
        save_job(job)
    threading.Thread(target=run_transcription, args=(job_id,), daemon=True).start()
    return redirect(f"/jobs/{job_id}/processing")


@app.get("/jobs/{job_id}/processing")
def processing(request: Request, job_id: str):
    try:
        job = _load(job_id)
    except Missing:
        return render(request, "missing.html", status_code=404)
    return render(request, "processing.html", active="home", job=job)


@app.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    try:
        job = _load(job_id)
    except Missing:
        return JSONResponse({"status": "missing"}, status_code=404)
    return {"status": job.get("status", "review"), "error": job.get("error", "")}


@app.post("/jobs/{job_id}/save")
async def save(job_id: str, request: Request):
    form = await request.form()
    try:
        with _lock(job_id):
            job = _load(job_id)
            apply_form(job, form, mark_reviewed=True)
            if job.get("status") == "built":
                job["status"] = "review"
            save_job(job)
    except Missing:
        return render(request, "missing.html", status_code=404)
    return redirect(f"/jobs/{job_id}", saved="1")


@app.post("/jobs/{job_id}/build")
async def build(job_id: str, request: Request):
    form = await request.form()
    try:
        with _lock(job_id):
            job = _load(job_id)
            apply_form(job, form, mark_reviewed=True)
            save_job(job)
            source = job_dir(job_id) / "source.pdf"
            dest = job_dir(job_id) / "print.pdf"
            try:
                stats = compose(job, source, dest)
            except Exception:
                log.exception("compose failed")
                return redirect(f"/jobs/{job_id}", error="The print file could not be created.")
            printed = [copy.deepcopy(cheque) for cheque in job["cheques"] if cheque.get("role") == "cheque"]
            write_csv(job_dir(job_id) / "cheques.csv", printed)
            try:
                preview_count = _write_previews(dest, job_dir(job_id))
            except Exception:
                log.exception("preview pages failed")
                preview_count = 0
            job["printed"] = {
                "cheques": printed,
                "typed_pictures": [
                    {
                        "page": cheque["page"],
                        "text": str(cheque.get("typed_text", "")).strip()
                        or "Picture kept. No typed text was entered.",
                    }
                    for cheque in job["cheques"]
                    if cheque.get("role") == "typed"
                ],
                "copied_pages": stats["copied_pages"],
                "reflow_pages": stats["reflow_pages"],
                "page_count": stats["page_count"],
                "preview_count": preview_count,
                "total": stats["total"],
            }
            job["output"] = "print.pdf"
            job["status"] = "built"
            job["error"] = ""
            save_job(job)
    except Missing:
        return render(request, "missing.html", status_code=404)
    return redirect(f"/jobs/{job_id}/result")


@app.get("/jobs/{job_id}/result")
def result(request: Request, job_id: str):
    try:
        job = _load(job_id)
    except Missing:
        return render(request, "missing.html", status_code=404)
    if not job.get("output"):
        return redirect(f"/jobs/{job_id}", error="Create the print file first.")
    return render(request, "result.html", active="home", job=job)


@app.get("/jobs/{job_id}/preview")
def preview(job_id: str):
    return _pdf_response(job_id, inline=True)


@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    return _pdf_response(job_id, inline=False)


def _pdf_response(job_id: str, inline: bool):
    try:
        job = _load(job_id)
    except Missing:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    path = job_dir(job_id) / "print.pdf"
    if not path.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=download_stem(job) + ".pdf",
        content_disposition_type="inline" if inline else "attachment",
    )


@app.get("/jobs/{job_id}/csv")
def download_csv(job_id: str):
    try:
        job = _load(job_id)
    except Missing:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    path = job_dir(job_id) / "cheques.csv"
    if not path.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(
        path,
        media_type="text/csv",
        filename=download_stem(job) + ".csv",
        content_disposition_type="attachment",
    )


@app.get("/jobs/{job_id}/previews/{name}")
def preview_image(job_id: str, name: str):
    if not re.fullmatch(r"[1-6]\.png", name):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    try:
        directory = job_dir(job_id)
    except Missing:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    path = directory / f"preview_{name}"
    if not path.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@app.get("/jobs/{job_id}/images/{name}")
def image(job_id: str, name: str):
    if not re.fullmatch(r"cheque_[0-9]+\.png", name):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    try:
        directory = job_dir(job_id)
    except Missing:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    path = directory / name
    if not path.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@app.post("/jobs/{job_id}/delete")
def remove(job_id: str):
    try:
        delete_job(job_id)
    except Missing:
        return redirect("/")
    with _locks_guard:
        _locks.pop(job_id, None)
    return redirect("/")


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    if (
        request.url.path == "/"
        or request.url.path.startswith("/jobs")
        or request.url.path.startswith("/settings")
        or request.url.path.startswith("/static")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8741, reload=False)
