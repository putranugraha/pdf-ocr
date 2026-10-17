from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from .scanner import _normalize_lighting
from .visit_numbers import is_valid_visit_number


@dataclass
class OCRLine:
    text: str
    confidence: float
    box: list[list[float]]


_engine = None
_engine_error: str | None = None
_lock = threading.Lock()


def engine_status() -> tuple[bool, str | None]:
    _get_engine()
    return _engine is not None, _engine_error


def _get_engine():
    global _engine, _engine_error
    if _engine is not None or _engine_error is not None:
        return _engine
    with _lock:
        if _engine is not None or _engine_error is not None:
            return _engine
        try:
            from rapidocr import RapidOCR

            _engine = RapidOCR()
        except Exception as exc:  # pragma: no cover - environment-dependent
            _engine_error = f"OCR belum siap: {exc}"
    return _engine


def _box_to_list(box) -> list[list[float]]:
    array = np.asarray(box, dtype=float).reshape(-1, 2)
    return [[float(x), float(y)] for x, y in array]


def recognize(image: np.ndarray) -> list[OCRLine]:
    engine = _get_engine()
    if engine is None:
        raise RuntimeError(_engine_error or "Mesin OCR tidak tersedia.")

    result = engine(image)
    lines: list[OCRLine] = []
    # RapidOCR 3.x returns an object; older compatible releases returned a tuple.
    if hasattr(result, "txts"):
        boxes = result.boxes if result.boxes is not None else []
        texts = result.txts if result.txts is not None else []
        scores = result.scores if result.scores is not None else []
        for box, text, score in zip(boxes, texts, scores):
            lines.append(OCRLine(str(text), float(score), _box_to_list(box)))
    elif isinstance(result, tuple) and result and result[0]:
        for item in result[0]:
            box, text, score = item
            lines.append(OCRLine(str(text), float(score), _box_to_list(box)))
    return lines


def _ocr_variants(image: np.ndarray) -> list[OCRLine]:
    lines = recognize(image)
    if lines:
        return lines
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return recognize(_normalize_lighting(gray))


def _clean_visit(text: str, allow_generic: bool = True) -> str:
    text = re.sub(r"[^A-Z0-9]", "", text.upper())
    # A label can share an OCR box with the number. Remove only that explicit
    # prefix; never extract a 19-character substring from a longer identifier.
    text = re.sub(r"^(?:N[O0](?:M[O0]R)?)?(?:KUNJUNGAN|RESEP)", "", text)
    if not is_valid_visit_number(text):
        return ""
    fix = str.maketrans({"O": "0", "I": "1", "L": "1"})

    # Known format A: 4 digits + U + 7 digits + P + 6 digits.
    match = re.fullmatch(r"([0-9OIL]{4})[U0]([0-9OIL]{7})P([0-9OIL]{6})", text)
    if match:
        return f"{match.group(1).translate(fix)}U{match.group(2).translate(fix)}P{match.group(3).translate(fix)}"

    # Known format B, used by visit and prescription forms:
    # 12 digits + Y + 6 digits. V is accepted as a common OCR error for Y.
    match = re.fullmatch(r"([0-9OIL]{12})[YV]([0-9OIL]{6})", text)
    if match:
        return f"{match.group(1).translate(fix)}Y{match.group(2).translate(fix)}"

    if not allow_generic:
        return ""

    # Other label-anchored formats still require exactly 19 characters.
    # Keep letters intact when their positions are not defined by a known format.
    digit_count = sum(character.isdigit() for character in text)
    letter_count = sum(character.isalpha() for character in text)
    if digit_count < 10 or digit_count / len(text) < 0.72 or letter_count > 5:
        return ""
    return text


def _line_bounds(line: OCRLine) -> tuple[float, float, float, float]:
    box = np.asarray(line.box, dtype=float)
    return box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max()


def _visit_anchors(lines: list[OCRLine]) -> list[OCRLine]:
    anchors = []
    for line in lines:
        compact = re.sub(r"[^A-Z0-9]", "", line.text.upper())
        normalized = compact.replace("1", "I")
        if "KUNJUNGAN" in normalized or "RESEP" in normalized:
            anchors.append(line)
    return anchors


def _unanchored_visit_candidates(
    lines: list[OCRLine], *, allow_generic: bool = False
) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    for line in lines:
        value = _clean_visit(line.text, allow_generic=allow_generic)
        if value:
            candidates.append((value, line.confidence))

    # A long identifier is sometimes split into two adjacent OCR boxes. Join
    # text boxes that share a row, then apply the same strict pattern check.
    sorted_lines = sorted(lines, key=lambda line: (_line_bounds(line)[1], _line_bounds(line)[0]))
    rows: list[list[OCRLine]] = []
    for line in sorted_lines:
        _, top, _, bottom = _line_bounds(line)
        center = (top + bottom) / 2
        height = max(1.0, bottom - top)
        matching_row = None
        for row in rows:
            _, row_top, _, row_bottom = _line_bounds(row[0])
            row_center = (row_top + row_bottom) / 2
            row_height = max(1.0, row_bottom - row_top)
            if abs(center - row_center) <= max(height, row_height) * 0.7:
                matching_row = row
                break
        if matching_row is None:
            rows.append([line])
        else:
            matching_row.append(line)
    for row in rows:
        if len(row) < 2:
            continue
        row.sort(key=lambda line: _line_bounds(line)[0])
        joined = "".join(line.text for line in row)
        value = _clean_visit(joined, allow_generic=allow_generic)
        if value:
            candidates.append((value, min(line.confidence for line in row)))
    return candidates


def extract_visit(lines: list[OCRLine]) -> tuple[str, float]:
    candidates: list[tuple[str, float]] = []
    anchors = _visit_anchors(lines)

    # Highest priority: a number embedded in the label or positioned on the
    # same row immediately to its right.
    for anchor in anchors:
        embedded = _clean_visit(anchor.text, allow_generic=True)
        if embedded:
            candidates.append((embedded, anchor.confidence))
        a_left, a_top, a_right, a_bottom = _line_bounds(anchor)
        a_height = max(1.0, a_bottom - a_top)
        a_center = (a_top + a_bottom) / 2
        for line in lines:
            if line is anchor:
                continue
            left, top, _, bottom = _line_bounds(line)
            center = (top + bottom) / 2
            same_row = abs(center - a_center) <= max(a_height, bottom - top) * 1.15
            is_right = left >= a_left and left >= a_right - a_height
            if same_row and is_right:
                value = _clean_visit(line.text, allow_generic=True)
                if value:
                    candidates.append((value, line.confidence))

    # Fallback keeps working for a tightly cropped photo where the label is
    # outside the frame, while still requiring a long mostly-numeric token.
    fallback = _unanchored_visit_candidates(lines, allow_generic=bool(anchors))
    if anchors:
        fallback = [(value, confidence * 0.85) for value, confidence in fallback]
    candidates.extend(fallback)
    return max(candidates, key=lambda item: item[1], default=("", 0.0))


def recognize_visit(image: np.ndarray) -> tuple[str, float]:
    """Read a visit number with an anchor-guided, focused second OCR pass."""
    lines = recognize(image)
    candidates = [extract_visit(lines)]
    height, width = image.shape[:2]

    for anchor in _visit_anchors(lines):
        _, top, right, bottom = _line_bounds(anchor)
        line_height = max(12.0, bottom - top)
        x1 = max(0, int(right - line_height * 0.5))
        x2 = width
        y1 = max(0, int(top - line_height * 1.2))
        y2 = min(height, int(bottom + line_height * 1.2))
        if x2 - x1 < 100 or y2 - y1 < 30:
            continue
        focus = image[y1:y2, x1:x2]

        # Original-color and normalized passes help with shadows and curved paper.
        focus_variants = [focus]
        gray = cv2.cvtColor(focus, cv2.COLOR_BGR2GRAY)
        normalized = _normalize_lighting(gray)
        focus_variants.append(normalized)
        for variant in focus_variants:
            focused_lines = recognize(variant)
            candidates.extend(_unanchored_visit_candidates(focused_lines, allow_generic=True))

    return max(candidates, key=lambda item: item[1], default=("", 0.0))


def extract_record(lines: list[OCRLine], image_shape: tuple[int, ...]) -> tuple[str, float]:
    height, width = image_shape[:2]
    ranked: list[tuple[float, str, float]] = []
    text_heights = []
    for line in lines:
        if not line.text.strip():
            continue
        box = np.asarray(line.box, dtype=float)
        text_heights.append(float(box[:, 1].max() - box[:, 1].min()))
    typical_height = float(np.median(text_heights)) if text_heights else 0.0
    minimum_large_height = max(height * 0.018, typical_height * 1.35)

    for line in lines:
        # Do not turn dates, stamps, phone fragments, or mixed words into a
        # patient number. The numbered card should contain an isolated token.
        token = re.fullmatch(r"[\s:;#.,-]*(\d{1,6})[\s:;#.,-]*", line.text)
        if not token:
            continue
        value = token.group(1)
        box = np.asarray(line.box, dtype=float)
        absolute_height = float(box[:, 1].max() - box[:, 1].min())
        box_height = absolute_height / max(height, 1)
        box_width = float(box[:, 0].max() - box[:, 0].min()) / max(width, 1)
        top = float(box[:, 1].min()) / max(height, 1)
        if absolute_height < minimum_large_height or top > 0.50:
            continue
        # Size and top position matter more than raw OCR confidence. This keeps
        # a small date/year from beating the large numbered card.
        rank = (box_height * 20) + (box_width * 2) + max(0, 0.50 - top) * 2 + line.confidence * 0.5
        ranked.append((rank, value, line.confidence))
    if not ranked:
        return "", 0.0
    _, value, confidence = max(ranked)
    return value, confidence


def estimate_content_crop(lines: list[OCRLine], image_shape: tuple[int, ...]) -> np.ndarray | None:
    """Estimate a safe outer crop when overlapping papers have no single contour."""
    height, width = image_shape[:2]
    usable = [line for line in lines if line.confidence >= 0.55 and len(line.text.strip()) >= 1]
    if len(usable) < 3:
        return None
    points = np.concatenate([np.asarray(line.box, dtype=np.float32) for line in usable])
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    if (right - left) < width * 0.15 or (bottom - top) < height * 0.10:
        return None

    # Leave enough paper margin around the detected writing. This works for a
    # single sheet as well as a bundle of overlapping prescriptions/receipts.
    pad_x = max(width * 0.06, (right - left) * 0.06)
    pad_y = max(height * 0.06, (bottom - top) * 0.06)
    left = max(0.0, left - pad_x)
    top = max(0.0, top - pad_y)
    right = min(float(width - 1), right + pad_x)
    bottom = min(float(height - 1), bottom + pad_y)
    return np.asarray(
        [[left, top], [right, top], [right, bottom], [left, bottom]],
        dtype=np.float32,
    )


def analyze_name_images(record_images: list[np.ndarray], visit_images: list[np.ndarray]) -> dict:
    record_candidates: list[tuple[str, float]] = []
    visit_candidates: list[tuple[str, float]] = []

    for image in record_images:
        lines = _ocr_variants(image)
        record_candidates.append(extract_record(lines, image.shape))
    for image in visit_images:
        lines = _ocr_variants(image)
        visit_candidates.append(extract_visit(lines))

    record = max(record_candidates, key=lambda item: item[1], default=("", 0.0))
    visit = max(visit_candidates, key=lambda item: item[1], default=("", 0.0))
    return {
        "record_number": record[0],
        "visit_number": visit[0],
        "record_confidence": round(record[1], 3),
        "visit_confidence": round(visit[1], 3),
        "needs_review": not record[0] or not visit[0] or record[1] < 0.75 or visit[1] < 0.75,
    }


def analyze_visit_images(visit_images: list[np.ndarray]) -> dict:
    candidates: list[tuple[str, float]] = []
    for image in visit_images:
        candidates.append(recognize_visit(image))
    visit = max(candidates, key=lambda item: item[1], default=("", 0.0))
    return {
        "visit_number": visit[0],
        "visit_confidence": round(visit[1], 3),
        "needs_review": not visit[0] or visit[1] < 0.75,
    }
