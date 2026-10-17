from fastapi.testclient import TestClient

from app.main import app


def test_pdf_receipt_endpoint_rejects_wrong_extension():
    client = TestClient(app)
    response = client.post(
        "/api/pdf-receipt-name",
        files={"secondary_pdf": ("receipt.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 415


def test_pdf_receipt_endpoint_rejects_corrupt_pdf():
    client = TestClient(app)
    response = client.post(
        "/api/pdf-receipt-name",
        files={"secondary_pdf": ("receipt.pdf", b"not a real pdf", "application/pdf")},
    )
    assert response.status_code == 400
