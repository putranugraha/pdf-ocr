import io
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app.main import app
from app.scanner import build_pdf


def make_pdf(page_count: int, width: int = 120) -> bytes:
    pages = []
    for index in range(page_count):
        image = np.full((160, width, 3), 245, dtype=np.uint8)
        cv2.putText(image, str(index + 1), (35, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        pages.append(encoded.tobytes())
    return build_pdf(pages)


def test_merge_appends_second_pdf_and_keeps_first_filename():
    client = TestClient(app)
    response = client.post(
        "/api/merge-pdf",
        files={
            "primary_pdf": ("683 220207020926Y000758.pdf", make_pdf(2, 120), "application/pdf"),
            "secondary_pdf": ("lampiran.pdf", make_pdf(3, 80), "application/pdf"),
        },
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert len(pages) == 5
    assert [float(page.mediabox.width) for page in pages[:2]] == [57.6, 57.6]
    assert [float(page.mediabox.width) for page in pages[2:]] == [38.4, 38.4, 38.4]
    assert [page.rotation for page in pages] == [0, 0, 0, 0, 0]
    assert 'filename="683 220207020926Y000758.pdf"' in response.headers["content-disposition"]


def test_merge_rejects_non_pdf_upload():
    client = TestClient(app)
    response = client.post(
        "/api/merge-pdf",
        files={
            "primary_pdf": ("utama.pdf", make_pdf(1), "application/pdf"),
            "secondary_pdf": ("catatan.txt", b"bukan pdf", "text/plain"),
        },
    )
    assert response.status_code == 415


def test_merge_accepts_confirmed_manual_filename():
    client = TestClient(app)
    response = client.post(
        "/api/merge-pdf",
        files={
            "primary_pdf": ("nama awal.pdf", make_pdf(1), "application/pdf"),
            "secondary_pdf": ("lampiran.pdf", make_pdf(1), "application/pdf"),
        },
        data={"output_filename": "nama hasil konfirmasi"},
    )

    assert response.status_code == 200
    assert 'filename="nama hasil konfirmasi.pdf"' in response.headers["content-disposition"]
    assert response.headers["x-download-filename"] == "nama%20hasil%20konfirmasi.pdf"


def rotated_pdf(rotations):
    reader = PdfReader(io.BytesIO(make_pdf(len(rotations))))
    writer = PdfWriter()
    for page, angle in zip(reader.pages, rotations):
        writer.add_page(page).rotate(angle)
    output = io.BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def test_merge_rotates_only_selected_primary_pages_and_preserves_content():
    primary = rotated_pdf([90, 0, 180])
    secondary = rotated_pdf([270, 180])
    response = TestClient(app).post(
        "/api/merge-pdf",
        files={
            "primary_pdf": ("utama.pdf", primary, "application/pdf"),
            "secondary_pdf": ("lampiran.pdf", secondary, "application/pdf"),
        },
        data={"rotate_primary_pages": "[1, 3, 3]", "output_filename": "hasil.pdf"},
    )
    assert response.status_code == 200
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert [page.rotation for page in pages] == [270, 0, 0, 270, 180]
    original_pages = list(PdfReader(io.BytesIO(primary)).pages) + list(PdfReader(io.BytesIO(secondary)).pages)
    for original, result in zip(original_pages, pages):
        assert original.get_contents().get_data() == result.get_contents().get_data()
        assert original.images[0].data == result.images[0].data
        assert original.mediabox == result.mediabox
    assert [page.rotation for page in PdfReader(io.BytesIO(primary)).pages] == [90, 0, 180]


@pytest.mark.parametrize("selection", [
    "oops", "null", "{}", '"1"', "[0]", "[-1]", "[3]", "[1.5]", "[true]", '["1"]',
    json.dumps([1] * 501),
])
def test_merge_rejects_invalid_rotation_selection(selection):
    response = TestClient(app).post(
        "/api/merge-pdf",
        files={
            "primary_pdf": ("utama.pdf", make_pdf(2), "application/pdf"),
            "secondary_pdf": ("lampiran.pdf", make_pdf(1), "application/pdf"),
        },
        data={"rotate_primary_pages": selection},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_merge_preview_assets_and_scoped_security_policy():
    client = TestClient(app)
    page = client.get("/merge")
    assert page.status_code == 200
    assert 'id="primaryPreview"' in page.text
    assert 'id="secondaryPreview"' in page.text
    assert 'type="module"' in page.text
    assert "worker-src 'self'" in page.headers["content-security-policy"]
    assert "wasm-unsafe-eval" not in client.get("/").headers["content-security-policy"]
    for asset in ("/pdf-preview.js", "/vendor/pdfjs/pdf.min.mjs", "/vendor/pdfjs/pdf.worker.min.mjs"):
        response = client.get(asset)
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]
