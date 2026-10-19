# ChequeScript

A local tool for customer PDFs that mix typed pages with photos of handwritten cheques. Typed pages stay as they are. Each cheque photo is read by a vision model into a record you can check, and both go into a new PDF.

The cheque record is for the accounting file. The footer says it is not a negotiable cheque.

## What it does

- Upload one PDF. Typed pages are copied. Cheque photos on the same page are split apart.
- When one picture contains several cheques and a blank gap separates them, the picture is cut. If the model still finds more than one cheque, each one becomes its own record.
- Check the amount, payee, date, and the other fields. Mark a picture as an ordinary typed page, or leave it out.
- Create a new PDF and a CSV. The original typed pages come first. Cheque print records follow.
- A cheque image is sent to the model in Settings only when you click Read handwriting.

## Install

Python 3.11 or newer on Windows.

Double-click `start.bat`. The first run creates a virtual environment, installs the dependencies, and opens http://127.0.0.1:8741 .

Or start it yourself:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8741
```

In Settings, enter the model address, model name, and API key. The program listens only on this computer. The key is stored in `data/settings.json` on this computer, and that directory is not part of the repository.

## Tests

```bat
.venv\Scripts\python.exe -m pytest tests -q
```
