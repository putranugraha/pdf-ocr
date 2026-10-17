from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .ocr import (
    analyze_name_images,
    analyze_visit_images,
    engine_status,
    estimate_content_crop,
    extract_record,
    recognize,
)
from .pdf_merge import merge_two_pdfs
from .receipt_naming import analyze_receipt_pdf, analyze_receipt_screenshot
from .scanner import (
    ImageError,
    apply_filter,
    build_pdf,
    decode_image,
    detect_document,
    encode_jpeg,
    normalized_points,
    parse_corners,
    perspective_crop,
    safe_filename,
)
from .tracking import ScanTracker, TrackingError

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR.parent / "data"

app = FastAPI(title="ScanNama PDF", version="0.1.0")
scan_tracker = ScanTracker(DATA_DIR / "scan_history.sqlite3")

DOWNLOAD_TTL_SECONDS = 5 * 60
MAX_PENDING_DOWNLOADS = 20
_pending_downloads: dict[str, tuple[float, str, bytes]] = {}
_download_lock = threading.Lock()


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'"
    if request.url.path == "/merge" or request.url.path.startswith("/vendor/pdfjs/"):
        # The local PDF preview uses a worker, embedded fonts and image decoders.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; "
            "script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
            "font-src 'self' blob: data:; connect-src 'self'"
        )
    response.headers["Permissions-Policy"] = "web-share=(self)"
    return response


async def _read_image(upload: UploadFile):
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(415, "File harus berupa gambar.")
    try:
        return decode_image(await upload.read())
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/health")
def health():
    ready, error = engine_status()
    return {"status": "ok", "ocr_ready": ready, "ocr_error": error}


@app.get("/merge", include_in_schema=False)
def merge_page():
    return FileResponse(STATIC_DIR / "merge.html")


@app.post("/api/detect")
async def detect(file: UploadFile = File(...)):
    image = await _read_image(file)
    points, detected = detect_document(image)
    height, width = image.shape[:2]
    return {
        "width": width,
        "height": height,
        "corners": normalized_points(points, width, height),
        "detected": detected,
    }


@app.post("/api/process")
async def process_page(
    file: UploadFile = File(...),
    corners: str = Form(...),
    filter_mode: str = Form("no_shadow_color"),
):
    image = await _read_image(file)
    height, width = image.shape[:2]
    try:
        points = parse_corners(corners, width, height)
        cropped = perspective_crop(image, points)
        processed = apply_filter(cropped, filter_mode)
        data = encode_jpeg(processed)
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(data, media_type="image/jpeg")


@app.post("/api/auto-scan")
async def auto_scan(
    file: UploadFile = File(...),
    filter_mode: str = Form("no_shadow_color"),
):
    """Detect, crop, enhance, and read the large record number in one request."""
    image = await _read_image(file)
    height, width = image.shape[:2]
    lines = []
    try:
        lines = recognize(image)
    except RuntimeError:
        # Scanning must remain usable even if the optional OCR engine fails.
        pass

    points, edge_detected = detect_document(image)
    strategy = "edge" if edge_detected else "fallback"
    if not edge_detected and lines:
        content_points = estimate_content_crop(lines, image.shape)
        if content_points is not None:
            points = content_points
            strategy = "content"

    try:
        processed = apply_filter(perspective_crop(image, points), filter_mode)
        data = encode_jpeg(processed)
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    record_number, record_confidence = extract_record(lines, image.shape) if lines else ("", 0.0)
    headers = {
        "X-Crop-Strategy": strategy,
        "X-Crop-Corners": json.dumps(normalized_points(points, width, height), separators=(",", ":")),
        "X-Record-Number": record_number,
        "X-Record-Confidence": str(round(record_confidence, 3)),
    }
    return Response(data, media_type="image/jpeg", headers=headers)


@app.post("/api/ocr-name")
async def ocr_name(
    record_images: list[UploadFile] = File(...),
    visit_images: list[UploadFile] = File(...),
):
    try:
        records = [await _read_image(item) for item in record_images[:10]]
        visits = [await _read_image(item) for item in visit_images[:10]]
        return analyze_name_images(records, visits)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/ocr-visit")
async def ocr_visit(visit_images: list[UploadFile] = File(...)):
    try:
        visits = [await _read_image(item) for item in visit_images[:10]]
        return analyze_visit_images(visits)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/ocr-receipt-name")
async def ocr_receipt_name(screenshot: UploadFile = File(...)):
    """Build the warehouse receipt filename from a Penerimaan Barang screenshot."""
    image = await _read_image(screenshot)
    try:
        return analyze_receipt_screenshot(image)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pdf-receipt-name")
async def pdf_receipt_name(secondary_pdf: UploadFile = File(...)):
    """Read naming fields directly from the second receipt PDF."""
    if not (secondary_pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "File penamaan harus berupa PDF.")
    try:
        return analyze_receipt_pdf(await secondary_pdf.read())
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pdf")
async def create_pdf(
    pages: list[UploadFile] = File(...),
    filename: str = Form(...),
):
    if len(pages) > 100:
        raise HTTPException(400, "Maksimal 100 halaman per PDF.")
    try:
        safe_name = safe_filename(filename)
        page_data = [await page.read() for page in pages]
        pdf = build_pdf(page_data)
    except (ImageError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}"'}
    return Response(pdf, media_type="application/pdf", headers=headers)


def _store_download(filename: str, pdf: bytes) -> str:
    now = time.monotonic()
    with _download_lock:
        expired = [token for token, item in _pending_downloads.items() if item[0] <= now]
        for token in expired:
            _pending_downloads.pop(token, None)
        if len(_pending_downloads) >= MAX_PENDING_DOWNLOADS:
            oldest = min(_pending_downloads, key=lambda token: _pending_downloads[token][0])
            _pending_downloads.pop(oldest, None)
        token = secrets.token_urlsafe(24)
        _pending_downloads[token] = (now + DOWNLOAD_TTL_SECONDS, filename, pdf)
    return token


@app.post("/api/pdf-job")
async def create_pdf_job(
    pages: list[UploadFile] = File(...),
    filename: str = Form(...),
):
    """Prepare a same-origin download URL so mobile browsers keep the filename."""
    if len(pages) > 100:
        raise HTTPException(400, "Maksimal 100 halaman per PDF.")
    try:
        safe_name = safe_filename(filename)
        page_data = [await page.read() for page in pages]
        pdf = build_pdf(page_data)
    except (ImageError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    token = _store_download(safe_name, pdf)
    encoded_name = quote(safe_name, safe=".-_")
    return {
        "filename": safe_name,
        "download_url": f"/api/download/{token}/{encoded_name}",
        "expires_in": DOWNLOAD_TTL_SECONDS,
    }


@app.get("/api/download/{token}/{requested_filename}")
def download_pdf(token: str, requested_filename: str):
    now = time.monotonic()
    with _download_lock:
        item = _pending_downloads.pop(token, None)
    if item is None or item[0] <= now:
        raise HTTPException(404, "Link unduhan sudah kedaluwarsa. Buat PDF kembali.")
    _, filename, pdf = item
    if requested_filename != filename:
        raise HTTPException(404, "Nama file unduhan tidak cocok.")
    encoded_name = quote(filename, safe=".-_")
    headers = {
        "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_name}"
    }
    return Response(pdf, media_type="application/pdf", headers=headers)


@app.post("/api/merge-pdf")
async def merge_pdf_files(
    primary_pdf: UploadFile = File(...),
    secondary_pdf: UploadFile = File(...),
    output_filename: str | None = Form(None),
    rotate_primary_pages: str = Form("[]", max_length=5000),
):
    """Append PDF 2 to PDF 1, with PDF 1's name as the editable default."""
    for position, upload in enumerate((primary_pdf, secondary_pdf), start=1):
        if not (upload.filename or "").lower().endswith(".pdf"):
            raise HTTPException(415, f"File ke-{position} harus berupa PDF.")

    original_name = (primary_pdf.filename or "gabungan.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    try:
        filename = safe_filename(original_name if output_filename is None else output_filename)
        try:
            rotation_pages = json.loads(rotate_primary_pages)
        except json.JSONDecodeError as exc:
            raise ImageError("Pilihan halaman untuk rotasi tidak valid.") from exc
        if not isinstance(rotation_pages, list):
            raise ImageError("Pilihan rotasi harus berupa daftar nomor halaman PDF pertama.")
        merged = merge_two_pdfs(
            await primary_pdf.read(), await secondary_pdf.read(), rotate_primary_pages=rotation_pages
        )
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    encoded_name = quote(filename, safe=".-_")
    headers = {
        "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_name}",
        "X-Download-Filename": encoded_name,
    }
    return Response(merged, media_type="application/pdf", headers=headers)


@app.get("/api/tracking/summary")
def tracking_summary(period: str | None = None):
    """Return checklist progress without modifying scan or PDF state."""
    try:
        return scan_tracker.summary(period)
    except TrackingError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/tracking/check")
def tracking_check(record_number: str, visit_number: str = ""):
    """Check the active month for an already shared record or exact pair."""
    try:
        return scan_tracker.check(record_number, visit_number)
    except TrackingError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/tracking/complete")
def tracking_complete(
    record_number: str = Form(...),
    visit_number: str = Form(...),
    filename: str = Form(...),
):
    """Mark a scan complete only after the browser reports a successful Share."""
    try:
        return scan_tracker.complete(record_number, visit_number, filename)
    except TrackingError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/tracking/record/{record_number}")
def tracking_remove_record(record_number: str, period: str | None = None):
    """Undo a checklist mark while keeping other periods untouched."""
    try:
        removed = scan_tracker.remove_record(record_number, period)
        return {"removed": removed, "record_number": record_number, "period": period}
    except TrackingError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/tracking/period")
def tracking_set_period(
    period: str = Form(...),
    target_count: int = Form(1000),
):
    """Start or reactivate a month; earlier months remain archived."""
    try:
        return scan_tracker.set_active_period(period, target_count)
    except TrackingError as exc:
        raise HTTPException(400, str(exc)) from exc


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
