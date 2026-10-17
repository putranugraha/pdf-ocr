import re

import cv2
import numpy as np
import pytest
import app.ocr as ocr
from app.ocr import OCRLine, estimate_content_crop, extract_record, extract_visit
from app.scanner import apply_filter, build_pdf, detect_document, perspective_crop, safe_filename


def test_visit_number_extraction_repairs_common_ocr_errors():
    lines = [OCRLine("No. Kunjungan", 0.98, [[0, 0], [10, 0], [10, 5], [0, 5]]),
             OCRLine("O226UO180926P000471", 0.91, [[20, 0], [80, 0], [80, 5], [20, 5]])]
    value, confidence = extract_visit(lines)
    assert value == "0226U0180926P000471"
    assert confidence == 0.91


def test_visit_number_uses_label_and_supports_y_format():
    lines = [
        OCRLine("No Kunjungan", 0.99, [[100, 100], [300, 100], [300, 140], [100, 140]]),
        OCRLine(": 220208010926Y003452", 0.98, [[340, 102], [750, 102], [750, 142], [340, 142]]),
        OCRLine("No Kartu BPJS", 0.99, [[100, 300], [350, 300], [350, 340], [100, 340]]),
        OCRLine("0002306550396", 0.99, [[390, 300], [700, 300], [700, 340], [390, 340]]),
    ]
    value, confidence = extract_visit(lines)
    assert value == "220208010926Y003452"
    assert confidence >= 0.98


def test_prescription_label_and_strict_pattern_fallback():
    prescription = [
        OCRLine("No Resep", 0.99, [[100, 100], [300, 100], [300, 140], [100, 140]]),
        OCRLine(": 022600040926Y000202", 0.98, [[340, 102], [760, 102], [760, 142], [340, 142]]),
        OCRLine("No Kartu", 0.99, [[100, 300], [300, 300], [300, 340], [100, 340]]),
        OCRLine("0001093692947", 1.0, [[340, 300], [650, 300], [650, 340], [340, 340]]),
    ]
    assert extract_visit(prescription)[0] == "022600040926Y000202"

    # Even without a readable label, the known 19-character pattern wins and
    # a 13-digit BPJS card number is ignored.
    without_label = prescription[1:]
    assert extract_visit(without_label)[0] == "022600040926Y000202"


def test_visit_fallback_joins_split_number_on_same_row():
    lines = [
        OCRLine("022600040926Y", 0.91, [[300, 100], [600, 100], [600, 140], [300, 140]]),
        OCRLine("000202", 0.93, [[610, 101], [750, 101], [750, 141], [610, 141]]),
        OCRLine("0001093692947", 0.99, [[300, 250], [650, 250], [650, 290], [300, 290]]),
    ]
    assert extract_visit(lines)[0] == "022600040926Y000202"


@pytest.mark.parametrize("code", [
    "220208010926Y00345", "220208010926Y0034529", "9220208010926Y003452",
    "0226U0180926P00047", "0226U0180926P0004719",
    "123456789012345678", "12345678901234567890",
])
@pytest.mark.parametrize("label", ["", "No. Resep: ", "No Kunjungan "])
def test_visit_rejects_wrong_length_without_taking_a_substring(code, label):
    assert ocr._clean_visit(label + code) == ""
    lines = [OCRLine(label + code, 0.99, [[0, 0], [500, 0], [500, 30], [0, 30]])]
    assert extract_visit(lines) == ("", 0.0)


@pytest.mark.parametrize("code", ["220208010926Y003452", "0226U0180926P000471", "12345678901234OILAB"])
def test_visit_exactly_19_characters_preserves_known_and_other_labeled_formats(code):
    assert ocr._clean_visit("Nomor Kunjungan: " + code) == code
    assert ocr._clean_visit("No. Resep: " + code.lower()) == code


def test_visit_wrong_length_requests_review_after_all_ocr_passes(monkeypatch):
    lines = [
        OCRLine("No Resep", 0.99, [[10, 40], [180, 40], [180, 70], [10, 70]]),
        OCRLine("220208010926Y0034529", 0.99, [[200, 40], [600, 40], [600, 70], [200, 70]]),
    ]
    monkeypatch.setattr(ocr, "recognize", lambda image: lines)
    result = ocr.analyze_visit_images([np.full((200, 800, 3), 255, dtype=np.uint8)])
    assert result["visit_number"] == ""
    assert result["visit_confidence"] == 0.0
    assert result["needs_review"] is True


def test_visit_focused_reread_recovers_a_complete_number(monkeypatch):
    first_pass = [
        OCRLine("No Resep", 0.99, [[10, 40], [180, 40], [180, 70], [10, 70]]),
        OCRLine("220208010926Y00345", 0.99, [[200, 40], [600, 40], [600, 70], [200, 70]]),
    ]
    focused = [OCRLine("220208010926Y003452", 0.96, [[10, 10], [400, 10], [400, 40], [10, 40]])]
    passes = iter([first_pass, focused, focused])
    monkeypatch.setattr(ocr, "recognize", lambda image: next(passes))
    result = ocr.analyze_visit_images([np.full((200, 800, 3), 255, dtype=np.uint8)])
    assert result["visit_number"] == "220208010926Y003452"
    assert result["needs_review"] is False


def test_record_prefers_large_text_near_top():
    lines = [
        OCRLine("503", 0.99, [[100, 500], [180, 500], [180, 520], [100, 520]]),
        OCRLine("697", 0.93, [[10, 10], [250, 10], [250, 160], [10, 160]]),
    ]
    assert extract_record(lines, (1000, 800, 3))[0] == "697"


def test_record_rejects_mixed_dates_and_small_document_numbers():
    lines = [
        OCRLine("22/9/2026", 0.99, [[100, 120], [280, 120], [280, 160], [100, 160]]),
        OCRLine("380.47y", 0.98, [[100, 200], [260, 200], [260, 245], [100, 245]]),
        OCRLine("3", 1.0, [[500, 300], [520, 300], [520, 320], [500, 320]]),
        OCRLine("Nama pasien", 0.99, [[100, 400], [350, 400], [350, 435], [100, 435]]),
    ]
    assert extract_record(lines, (1000, 800, 3)) == ("", 0.0)


def test_content_crop_wraps_text_with_margin():
    lines = [
        OCRLine("697", 0.99, [[100, 100], [200, 100], [200, 180], [100, 180]]),
        OCRLine("Nama pasien", 0.95, [[120, 300], [500, 300], [500, 340], [120, 340]]),
        OCRLine("Nomor lain", 0.95, [[130, 600], [650, 600], [650, 640], [130, 640]]),
    ]
    points = estimate_content_crop(lines, (1000, 800, 3))
    assert points is not None
    assert points[0][0] < 100 and points[0][1] < 100
    assert points[2][0] > 650 and points[2][1] > 640


def test_document_detection_and_crop():
    image = np.zeros((800, 600, 3), dtype=np.uint8)
    polygon = np.array([[90, 70], [530, 100], [510, 730], [70, 700]], dtype=np.int32)
    cv2.fillConvexPoly(image, polygon, (255, 255, 255))
    points, detected = detect_document(image)
    cropped = perspective_crop(image, points)
    assert detected
    assert cropped.shape[0] > 500
    assert cropped.shape[1] > 350


def test_filters_and_pdf():
    image = np.full((500, 350, 3), 220, dtype=np.uint8)
    cv2.putText(image, "697", (60, 240), cv2.FONT_HERSHEY_SIMPLEX, 3, (10, 10, 10), 7)
    color = apply_filter(image, "no_shadow_color")
    bw = apply_filter(image, "no_shadow_bw")
    ok1, page1 = cv2.imencode(".jpg", color)
    ok2, page2 = cv2.imencode(".jpg", bw)
    assert ok1 and ok2
    pdf = build_pdf([page1.tobytes(), page2.tobytes()])
    assert pdf.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) == 2


def test_safe_filename():
    assert safe_filename("697 0226U0180926P000471.pdf") == "697 0226U0180926P000471.pdf"
    assert safe_filename("../aneh nama?.pdf") == "aneh nama.pdf"

