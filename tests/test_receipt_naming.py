import io

import numpy as np
import pytest
from PIL import Image

import app.receipt_naming as receipt_naming
from app.ocr import OCRLine
from app.receipt_naming import (
    analyze_receipt_pdf,
    creditor_abbreviation,
    extract_receipt_naming,
    extract_receipt_text,
    format_received_date,
)
from app.scanner import build_pdf


def line(text: str, left: float, top: float, right: float, bottom: float, confidence: float = 0.98):
    return OCRLine(text, confidence, [[left, top], [right, top], [right, bottom], [left, bottom]])


def test_receipt_screenshot_builds_expected_filename():
    lines = [
        line("Nomor Penerimaan Barang", 40, 220, 260, 240),
        line("RC11117F012377353B", 40, 248, 245, 270),
        line("Nomor Faktur", 40, 286, 160, 306),
        line("2809018550", 40, 314, 150, 336),
        line("Kreditur", 500, 152, 575, 172),
        line("KIMIAFARMA TRADING & DISTRIBUTION,P", 500, 180, 850, 202),
        line("Tanggal Penerimaan Barang", 500, 220, 735, 240),
        line("01 Maret 2026", 500, 248, 635, 270),
        # Nearby unrelated fields must not be selected.
        line("Tanggal Faktur", 960, 220, 1080, 240),
        line("28 Februari 2026", 960, 248, 1125, 270),
    ]

    result = extract_receipt_naming(lines, (360, 1882, 3))

    assert result["creditor"] == "KTD"
    assert result["invoice_number"] == "2809018550"
    assert result["receipt_number"] == "RC11117F012377353B"
    assert result["received_date"] == "010326"
    assert result["suggested_filename"] == "KTD_2809018550_RC11117F012377353B_010326.pdf"
    assert result["missing_fields"] == []


def test_creditor_legal_suffixes_are_ignored():
    assert creditor_abbreviation("ENSEVAL PUTERA MEGATRADING, PT") == "EPM"
    assert creditor_abbreviation("PT ANUGRAH PHARMINDO LESTARI TBK") == "APL"
    assert creditor_abbreviation("ANUGERAH PHARMINDO LESTARI, PT") == "APL"


def test_received_date_supports_indonesian_and_numeric_formats():
    assert format_received_date("07 Januari 2026") == "070126"
    assert format_received_date("01/03/2026") == "010326"
    assert format_received_date("O2 Maret 2O26") == "020326"
    assert format_received_date("02Maret2026") == "020326"
    assert format_received_date("02 Maref 2026") == "020326"
    assert format_received_date("31 Februari 2026") == ""


def test_second_receipt_example_handles_ocr_date_errors():
    lines = [
        line("Nomor Penerimaan Barang", 60, 175, 265, 190),
        line("RC11117F0123794483", 60, 195, 245, 210),
        line("Nomor Faktur", 60, 222, 165, 237),
        line("1801289504", 60, 242, 155, 257),
        line("Kreditur", 510, 130, 575, 145),
        line("ANUGERAH PHARMINDO LESTARI, PT", 510, 150, 810, 165),
        line("Tanggal Penerimaan Barang", 510, 175, 720, 190),
        line("O2 Maret 2O26", 510, 195, 630, 210),
    ]
    result = extract_receipt_naming(lines, (263, 1843, 3))
    assert result["suggested_filename"] == "APL_1801289504_RC11117F0123794483_020326.pdf"


def test_alphanumeric_invoice_with_hyphen_is_preserved():
    lines = [
        line("Nomor Penerimaan Barang", 32, 52, 230, 67),
        line("RC11117F012393463E", 32, 72, 220, 87),
        line("Nomor Faktur", 32, 91, 135, 106),
        line("00203-C2929", 32, 111, 140, 126),
        line("Kreditur", 484, 14, 550, 29),
        line("MARGA NUSANTARA JAYA, PT", 484, 34, 710, 49),
        line("Tanggal Penerimaan Barang", 484, 52, 695, 67),
        line("05 Maret 2026", 484, 72, 605, 87),
    ]
    result = extract_receipt_naming(lines, (120, 1527, 3))
    assert result["invoice_number"] == "00203-C2929"
    assert result["suggested_filename"] == "MNJ_00203-C2929_RC11117F012393463E_050326.pdf"


def test_missing_receipt_does_not_steal_invoice_number_below_it():
    lines = [
        line("Nomor Penerimaan Barang", 40, 170, 260, 190),
        # RC value is deliberately absent.
        line("Nomor Faktur", 40, 220, 160, 240),
        line("1801289495", 40, 248, 150, 270),
        line("Kreditur", 500, 130, 575, 150),
        line("ANUGERAH PHARMINDO LESTARI, PT", 500, 158, 850, 180),
        line("Tanggal Penerimaan Barang", 500, 200, 735, 220),
        line("02 Maret 2026", 500, 228, 635, 250),
    ]

    result = extract_receipt_naming(lines, (583, 1911, 3))
    assert result["invoice_number"] == "1801289495"
    assert result["receipt_number"] == ""
    assert "receipt_number" in result["missing_fields"]
    assert result["suggested_filename"] == ""


def test_receipt_prefix_repairs_common_c_as_zero_ocr_error():
    lines = [
        line("Nomor Penerimaan Barang", 40, 170, 260, 190),
        line("RO11117F012378805C", 40, 198, 245, 220),
    ]
    result = extract_receipt_naming(lines, (583, 1911, 3))
    assert result["receipt_number"] == "RC11117F012378805C"


def test_pdf_text_layer_with_wrapped_labels_and_creditor():
    text = """
    Penerimaan Barang
    Nomor Surat Pemesanan
    POCA64496996
    Kreditur
    ANUGRAH ARGON
    MEDICA, PT
    PPn
    Exclude PPn
    Nomor Penerimaan
    Barang
    RC11117F012416050N
    Tanggal Penerimaan
    Barang
    09 Maret 2026
    Tanggal Faktur
    04 Maret 2026
    Nomor Faktur
    5823022374
    TOP ( dalam hari )
    60
    """
    result = extract_receipt_text(text)
    assert result["creditor"] == "AAM"
    assert result["invoice_number"] == "5823022374"
    assert result["receipt_number"] == "RC11117F012416050N"
    assert result["received_date"] == "090326"
    assert result["suggested_filename"] == "AAM_5823022374_RC11117F012416050N_090326.pdf"


def test_image_only_pdf_uses_embedded_image_ocr_fallback(monkeypatch):
    image = Image.new("RGB", (800, 500), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    pdf = build_pdf([buffer.getvalue()])
    expected = {
        "creditor": "AAM",
        "invoice_number": "5823022374",
        "receipt_number": "RC11117F012416050N",
        "received_date": "090326",
        "suggested_filename": "AAM_5823022374_RC11117F012416050N_090326.pdf",
        "confidence": 0.95,
        "missing_fields": [],
        "needs_review": False,
    }
    monkeypatch.setattr(receipt_naming, "analyze_receipt_screenshot", lambda image: expected)

    result = analyze_receipt_pdf(pdf)
    assert result["source"] == "pdf_ocr"
    assert result["suggested_filename"] == expected["suggested_filename"]


def test_pdf_table_before_header_does_not_turn_medicine_into_receipt():
    # Reproduce the extraction order of RC11117F012631861G.pdf without
    # committing the user's document or depending on its location.
    text = """
    Penerimaan Barang
    No. Nama Item NomorBatch Exp. Date JumlahDipesan
    Diskon Jumlah(Rp)1 2 3
    1 ATORVASTATIN CALC 20MG TAB@100
    GDX 57A0787 01-12-2027
    2 BISOPROLOL FUMARATE 2.5MG @100
    Nomor Surat Pemesanan
    PO12345678
    Kreditur
    ANUGRAH ARGON
    MEDICA, PT
    PPn
    Exclude PPn
    Nomor Penerimaan Barang
    RC11117F012631861G
    Tanggal Penerimaan Barang
    11 Mei 2026
    Nomor Faktur
    5826044475
    TOP ( dalam hari )
    60
    """
    result = extract_receipt_text(text)
    assert result["suggested_filename"] == "AAM_5826044475_RC11117F012631861G_110526.pdf"
    assert result["needs_review"] is False
    assert receipt_naming._label_kind("Penerimaan Barang") is None


@pytest.mark.parametrize("value", [
    "BISOPROLOL FUMARATE 2.5MG @100", "RCLOLFUMARATE25MG100",
    "RC11117F012631861", "RC11117F012631861G999", "abcRC11117F012631861G",
])
def test_invalid_receipts_require_review(value):
    result = extract_receipt_text(f"Nomor Penerimaan Barang\n{value}")
    assert result["receipt_number"] == ""
    assert result["needs_review"] is True


def test_unanchored_receipt_recovery_does_not_repair_ro_prefix():
    assert extract_receipt_text("RO11117F012631861G")["receipt_number"] == ""
    assert extract_receipt_text("RC11117F012631861G")["receipt_number"] == "RC11117F012631861G"


def test_receipt_ocr_repairs_only_numeric_positions():
    result = extract_receipt_text("Nomor Penerimaan Barang\nRO11I17FO12631861O")
    assert result["receipt_number"] == "RC11117F012631861O"


def screenshot_lines(invoice="58260044682", receipt="RC11117F012634030P", confidence=0.98):
    return [
        line("Penerimaan Barang", 860, 0, 1010, 16),
        line("Nomor Penerimaan Barang", 50, 156, 252, 172),
        line(receipt, 50, 174, 222, 190, confidence),
        line("Nomor Faktur", 50, 202, 154, 218),
        line(invoice, 50, 220, 160, 236, confidence),
        line("Kreditur", 500, 110, 560, 126),
        line("ANUGRAH ARGON MEDICA, PT", 500, 128, 734, 144),
        line("Tanggal Penerimaan Barang", 500, 156, 713, 172),
        line("11 Mei 2026", 500, 174, 596, 190),
    ]


def test_screenshot_layout_preserves_correct_identifiers():
    result = extract_receipt_naming(screenshot_lines(), (395, 1858, 3))
    assert result["suggested_filename"] == "AAM_58260044682_RC11117F012634030P_110526.pdf"
    assert result["needs_review"] is False


@pytest.mark.parametrize("invoice", ["5B260044682", "582G0044682", "582S0044682"])
def test_suspect_invoice_is_preserved_but_requires_review(invoice):
    result = extract_receipt_naming(screenshot_lines(invoice=invoice), (395, 1858, 3))
    assert result["invoice_number"] == invoice
    assert result["needs_review"] is True
    assert result["confidence"] < 0.8


def test_screenshot_rereads_suspect_invoice_without_guessing_digits(monkeypatch):
    calls = []

    def recognize(image):
        calls.append(image.shape)
        if len(calls) <= 2:
            return screenshot_lines(invoice="5B260044682")
        return [line("58260044682", 5, 10, 200, 35, 0.99)]

    monkeypatch.setattr(receipt_naming, "recognize", recognize)
    result = receipt_naming.analyze_receipt_screenshot(np.zeros((395, 1858, 3), dtype=np.uint8))
    assert len(calls) > 2
    assert result["invoice_number"] == "58260044682"
    # Conflicting readings remain visible as a request to check the result.
    assert result["needs_review"] is True


def test_high_confidence_disagreement_still_requires_review(monkeypatch):
    readings = iter([screenshot_lines(), screenshot_lines(invoice="58260044683")])

    def recognize(image):
        return next(readings, [line("58260044682", 5, 10, 200, 35, 0.99)])

    monkeypatch.setattr(receipt_naming, "recognize", recognize)
    result = receipt_naming.analyze_receipt_screenshot(np.zeros((395, 1858, 3), dtype=np.uint8))
    assert result["needs_review"] is True
    assert result["confidence"] < 0.8


def test_focused_receipt_retries_low_confidence_value(monkeypatch):
    calls = []

    def recognize(image):
        calls.append(image.shape)
        if len(calls) <= 2:
            return screenshot_lines(receipt="RC11117F012634030B", confidence=0.7)
        return [line("RC11117F012634030P", 5, 10, 200, 35, 0.99)]

    monkeypatch.setattr(receipt_naming, "recognize", recognize)
    result = receipt_naming.analyze_receipt_screenshot(np.zeros((395, 1858, 3), dtype=np.uint8))
    assert result["receipt_number"] == "RC11117F012634030P"
    assert result["needs_review"] is True


def test_focused_receipt_crop_stops_before_invoice(monkeypatch):
    crops = []
    monkeypatch.setattr(receipt_naming, "recognize", lambda image: crops.append(image.shape) or [])
    receipt_naming._recognize_focused_code(
        np.zeros((395, 1858, 3), dtype=np.uint8), screenshot_lines(), "receipt"
    )
    assert crops
    # Receipt label ends at y=172; invoice starts at y=202. Crop is enlarged 3x.
    assert crops[0][0] == (202 - 168) * 3
