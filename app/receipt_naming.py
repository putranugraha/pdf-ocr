from __future__ import annotations

import io
import re
from datetime import date
from difflib import SequenceMatcher

import cv2
import numpy as np
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .ocr import OCRLine, recognize
from .scanner import ImageError


FIELD_LABELS = {
    "creditor": "KREDITUR",
    "invoice": "NOMORFAKTUR",
    "receipt": "NOMORPENERIMAANBARANG",
    "received_date": "TANGGALPENERIMAANBARANG",
}

MONTHS = {
    "JANUARI": 1,
    "JANUARY": 1,
    "FEBRUARI": 2,
    "PEBRUARI": 2,
    "FEBRUARY": 2,
    "MARET": 3,
    "MARCH": 3,
    "APRIL": 4,
    "MEI": 5,
    "MAY": 5,
    "JUNI": 6,
    "JUNE": 6,
    "JULI": 7,
    "JULY": 7,
    "AGUSTUS": 8,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OKTOBER": 10,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DESEMBER": 12,
    "DECEMBER": 12,
}

LEGAL_WORDS = {"PT", "P", "CV", "TBK", "PERSERO", "DAN", "AND"}
MAX_RECEIPT_PDF_BYTES = 100 * 1024 * 1024
MAX_RECEIPT_PDF_PAGES = 20

PDF_BOUNDARY_LABELS = (
    "NOMORSURATPEMESANAN",
    "KREDITUR",
    "PPN",
    "JUMLAHPPN",
    "NOMORPENERIMAANBARANG",
    "TANGGALPENERIMAANBARANG",
    "TANGGALFAKTUR",
    "TANGGALJATUHTEMPO",
    "NOMORFAKTUR",
    "TOPDALAMHARI",
    "TANGGALTERIMAFISIKFAKTUR",
    "NAMAITEM",
)


def _bounds(line: OCRLine) -> tuple[float, float, float, float]:
    box = np.asarray(line.box, dtype=float)
    return box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max()


def _label_text(text: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    return compact.translate(str.maketrans({"0": "O", "1": "I"}))


def _label_kind(text: str) -> str | None:
    compact = _label_text(text)
    if not compact:
        return None

    # Prefer explicit keywords so the two Penerimaan Barang labels cannot be
    # confused with each other.
    if "PENERIMAANBARANG" in compact:
        if "TANGGAL" in compact:
            return "received_date"
        if "NOMOR" in compact:
            return "receipt"
    if "FAKTUR" in compact and "NOMOR" in compact:
        return "invoice"
    if "KREDITUR" in compact:
        return "creditor"

    matches = [
        (SequenceMatcher(None, compact, expected).ratio(), field)
        for field, expected in FIELD_LABELS.items()
        if (field == "creditor"
            or (field in {"receipt", "invoice"} and "NOMOR" in compact)
            or (field == "received_date" and "TANGGAL" in compact))
    ]
    score, field = max(matches, default=(0.0, ""))
    return field if score >= 0.74 else None


def _inline_value(text: str) -> str:
    if ":" not in text:
        return ""
    return text.split(":", 1)[1].strip()


def _value_below(
    anchor: OCRLine,
    lines: list[OCRLine],
    image_shape: tuple[int, ...],
) -> OCRLine | None:
    height, width = image_shape[:2]
    a_left, a_top, a_right, a_bottom = _bounds(anchor)
    max_x_distance = max(width * 0.13, (a_right - a_left) * 0.8)
    max_y_distance = max(height * 0.18, (a_bottom - a_top) * 4)
    candidates: list[tuple[float, OCRLine]] = []

    # Values belong to the current label only until the next recognized label
    # in the same column. This prevents a missing RC row from falling through
    # to the invoice value below it.
    next_label_top = float("inf")
    for line in lines:
        if line is anchor or _label_kind(line.text) is None:
            continue
        left, top, _, _ = _bounds(line)
        if top > a_bottom and abs(left - a_left) <= max_x_distance:
            next_label_top = min(next_label_top, top)

    for line in lines:
        if line is anchor or _label_kind(line.text) is not None:
            continue
        left, top, _, bottom = _bounds(line)
        if top < a_bottom - max(2.0, (bottom - top) * 0.2):
            continue
        y_gap = max(0.0, top - a_bottom)
        x_gap = abs(left - a_left)
        if top >= next_label_top or y_gap > max_y_distance or x_gap > max_x_distance:
            continue
        candidates.append((y_gap + x_gap * 0.35, line))

    return min(candidates, key=lambda item: item[0], default=(0.0, None))[1]


def creditor_abbreviation(value: str) -> str:
    words = re.findall(r"[A-Z0-9]+", value.upper())
    meaningful = [word for word in words if word not in LEGAL_WORDS]
    if not meaningful:
        return ""
    if len(meaningful) == 1 and 2 <= len(meaningful[0]) <= 6:
        return meaningful[0]
    return "".join(word[0] for word in meaningful if word)[:10]


def _clean_code(value: str, *, numeric: bool = False) -> str:
    code = re.sub(r"[^A-Z0-9]", "", value.upper())
    if numeric and code:
        digit_like = sum(character.isdigit() or character in "OIL" for character in code)
        if digit_like / len(code) >= 0.85:
            code = code.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    return code


def _clean_invoice(value: str) -> str:
    code = value.upper().replace("–", "-").replace("—", "-")
    code = re.sub(r"\s+", "", code)
    code = re.sub(r"[^A-Z0-9/-]", "", code).strip("-/")
    if code.startswith("RC") or not re.fullmatch(r"[A-Z0-9]+(?:[-/][A-Z0-9]+)*", code):
        return ""
    digits = sum(character.isdigit() for character in code)
    if not 5 <= len(code) <= 30 or digits < 4:
        return ""

    # Repair O/I/L only for an otherwise numeric invoice. Alphanumeric invoice
    # letters, such as C in 00203-C2929, must remain untouched.
    if re.fullmatch(r"[0-9OIL/-]+", code):
        code = code.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    return code


def _clean_receipt(value: str, *, allow_ocr: bool = True) -> str:
    code = _clean_code(value)
    # Known receipt format: RC + 5 digits + letter + 9 digits + check character.
    # Match the whole value so medicine names and truncated codes cannot pass.
    prefix = r"R[C0O]" if allow_ocr else "RC"
    digit = r"[0-9OIL]" if allow_ocr else r"[0-9]"
    match = re.fullmatch(prefix + f"({digit}{{5}})([A-Z])({digit}{{9}})([A-Z0-9])", code)
    if not match:
        return ""
    fix = str.maketrans({"O": "0", "I": "1", "L": "1"})
    return "RC" + match[1].translate(fix) + match[2] + match[3].translate(fix) + match[4]


def _invoice_needs_review(code: str) -> bool:
    # Preserve legitimate alphanumeric invoices; never guess B/G/S as digits.
    # An otherwise numeric value containing these lookalikes needs a reread.
    letters = {character for character in code if character.isalpha()}
    return bool(
        letters and letters <= set("BGSZQ") and code.isalnum()
        and sum(character.isdigit() for character in code) / len(code) >= 0.8
    )


def format_received_date(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.upper()).strip()
    digit_fix = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5"})

    numeric = re.search(
        r"([0-9OQILZS]{1,2})\s*[./-]\s*([0-9OQILZS]{1,2})\s*[./-]\s*([0-9OQILZS]{2,4})",
        normalized,
    )
    if numeric:
        day, month, year = (int(part.translate(digit_fix)) for part in numeric.groups())
    else:
        # OCR commonly returns O2/2O26 or removes spaces around the month.
        written = re.search(
            r"([0-9OQILZS]{1,2})\s*([A-Z]{3,12})\s*([0-9OQILZS]{2,4})",
            normalized,
        )
        if not written:
            return ""
        day = int(written.group(1).translate(digit_fix))
        month_text = written.group(2)
        month = MONTHS.get(month_text, 0)
        if not month:
            month_matches = [
                (SequenceMatcher(None, month_text, candidate).ratio(), number)
                for candidate, number in MONTHS.items()
            ]
            month_score, month = max(month_matches, default=(0.0, 0))
            if month_score < 0.67:
                return ""
        year = int(written.group(3).translate(digit_fix))
    if year < 100:
        year += 2000
    try:
        parsed = date(year, month, day)
    except ValueError:
        return ""
    return parsed.strftime("%d%m%y")


def _pdf_lines(text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    merged: list[str] = []
    index = 0
    while index < len(lines):
        current = lines[index]
        if index + 1 < len(lines):
            combined = f"{current} {lines[index + 1]}"
            compact = re.sub(r"[^A-Z0-9]", "", combined.upper())
            if compact in PDF_BOUNDARY_LABELS:
                merged.append(combined)
                index += 2
                continue
        merged.append(current)
        index += 1
    return merged


def _is_pdf_boundary(text: str) -> bool:
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    return compact in PDF_BOUNDARY_LABELS


def _clean_text_field(field: str, value: str) -> str:
    if field == "creditor":
        return creditor_abbreviation(value)
    if field == "invoice":
        return _clean_invoice(value)
    if field == "receipt":
        return _clean_receipt(value)
    if field == "received_date":
        return format_received_date(value)
    return ""


def extract_receipt_text(text: str) -> dict[str, object]:
    """Extract receipt naming fields from a PDF's selectable text layer."""
    lines = _pdf_lines(text)
    values: dict[str, str] = {}
    for index, line in enumerate(lines):
        field = _label_kind(line)
        if field is None or field in values:
            continue

        inline = _inline_value(line)
        chunks: list[str] = [inline] if inline else []
        if not chunks:
            for candidate in lines[index + 1 : index + 6]:
                if _is_pdf_boundary(candidate) or _label_kind(candidate) is not None:
                    break
                chunks.append(candidate)

        # Multi-line company names need all chunks, while identifiers and
        # dates are normally contained in the first valid chunk.
        candidates = [" ".join(chunks)] if field == "creditor" else chunks + [" ".join(chunks)]
        for candidate in candidates:
            cleaned = _clean_text_field(field, candidate)
            if cleaned:
                values[field] = cleaned
                break

    # A receipt number is distinctive enough to recover safely even if its
    # wrapped label was extracted in an unusual order.
    if "receipt" not in values:
        for line in lines:
            receipt = _clean_receipt(line, allow_ocr=False)
            if receipt:
                values["receipt"] = receipt
                break

    fields = {
        "creditor": values.get("creditor", ""),
        "invoice_number": values.get("invoice", ""),
        "receipt_number": values.get("receipt", ""),
        "received_date": values.get("received_date", ""),
    }
    confidences = {field: (1.0 if value else 0.0) for field, value in fields.items()}
    return _build_result(fields, confidences)


def _build_result(fields: dict[str, str], field_confidences: dict[str, float]) -> dict[str, object]:
    field_confidences = dict(field_confidences)
    if _invoice_needs_review(fields.get("invoice_number", "")):
        field_confidences["invoice_number"] = min(field_confidences.get("invoice_number", 0.0), 0.79)
    missing_fields = [field for field, value in fields.items() if not value]
    filename = ""
    if not missing_fields:
        filename = (
            f"{fields['creditor']}_{fields['invoice_number']}_"
            f"{fields['receipt_number']}_{fields['received_date']}.pdf"
        )

    confidence = min(field_confidences.values(), default=0.0) if not missing_fields else 0.0
    return {
        **fields,
        "suggested_filename": filename,
        "confidence": round(confidence, 3),
        "missing_fields": missing_fields,
        "needs_review": bool(missing_fields) or confidence < 0.8,
        "_field_confidences": field_confidences,
    }


def extract_receipt_naming(
    lines: list[OCRLine], image_shape: tuple[int, ...]
) -> dict[str, object]:
    raw_values: dict[str, tuple[str, float]] = {}
    for line in lines:
        field = _label_kind(line.text)
        if field is None:
            continue
        inline = _inline_value(line.text)
        if inline:
            raw_values[field] = (inline, line.confidence)
            continue
        value_line = _value_below(line, lines, image_shape)
        if value_line is not None:
            raw_values[field] = (value_line.text.strip(), value_line.confidence)

    creditor_raw, creditor_confidence = raw_values.get("creditor", ("", 0.0))
    invoice_raw, invoice_confidence = raw_values.get("invoice", ("", 0.0))
    receipt_raw, receipt_confidence = raw_values.get("receipt", ("", 0.0))
    date_raw, date_confidence = raw_values.get("received_date", ("", 0.0))

    creditor = creditor_abbreviation(creditor_raw)
    invoice = _clean_invoice(invoice_raw)
    receipt = _clean_receipt(receipt_raw)
    received_date = format_received_date(date_raw)
    fields: dict[str, str] = {
        "creditor": creditor,
        "invoice_number": invoice,
        "receipt_number": receipt,
        "received_date": received_date,
    }
    field_confidences = {
        "creditor": creditor_confidence if creditor else 0.0,
        "invoice_number": invoice_confidence if invoice else 0.0,
        "receipt_number": receipt_confidence if receipt else 0.0,
        "received_date": date_confidence if received_date else 0.0,
    }
    return _build_result(fields, field_confidences)


def _recognize_focused_code(
    image: np.ndarray, lines: list[OCRLine], field: str
) -> tuple[str, float]:
    height, width = image.shape[:2]
    candidates: list[tuple[float, str]] = []
    for anchor in lines:
        if _label_kind(anchor.text) != field:
            continue
        left, top, right, bottom = _bounds(anchor)
        line_height = max(12.0, bottom - top)
        x1 = max(0, int(left - line_height))
        x2 = min(width, int(left + max((right - left) * 1.7, width * 0.20)))
        y1 = max(0, int(bottom - line_height * 0.25))
        y2 = min(height, int(bottom + line_height * 3.2))
        # Do not let a missing value borrow the next field's number.
        for other in lines:
            other_left, other_top, _, _ = _bounds(other)
            if (other is not anchor and _label_kind(other.text) is not None
                    and other_top > bottom and x1 <= other_left < x2):
                y2 = min(y2, int(other_top))
        if x2 - x1 < 80 or y2 - y1 < 20:
            continue
        focus = image[y1:y2, x1:x2]
        focus = cv2.resize(focus, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        for focused_line in recognize(focus):
            if _label_kind(focused_line.text) is not None:
                continue
            value = _clean_text_field(field, focused_line.text)
            if value:
                confidence = focused_line.confidence
                if field == "invoice" and _invoice_needs_review(value):
                    confidence = min(confidence, 0.79)
                candidates.append((confidence, value))
    confidence, value = max(candidates, default=(0.0, ""))
    return value, confidence


def _recognize_focused_date(
    image: np.ndarray, lines: list[OCRLine]
) -> tuple[str, float]:
    height, width = image.shape[:2]
    candidates: list[tuple[float, str]] = []
    for anchor in lines:
        if _label_kind(anchor.text) != "received_date":
            continue
        left, top, right, bottom = _bounds(anchor)
        line_height = max(12.0, bottom - top)
        x1 = max(0, int(left - line_height))
        x2 = min(width, int(left + max((right - left) * 1.5, width * 0.16)))
        y1 = max(0, int(bottom - line_height * 0.25))
        y2 = min(height, int(bottom + line_height * 3.2))
        if x2 - x1 < 80 or y2 - y1 < 20:
            continue
        focus = image[y1:y2, x1:x2]
        focus = cv2.resize(focus, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        focused_lines = recognize(focus)
        for focused_line in focused_lines:
            value = format_received_date(focused_line.text)
            if value:
                candidates.append((focused_line.confidence, value))
        joined = " ".join(
            line.text for line in sorted(focused_lines, key=lambda item: (_bounds(item)[1], _bounds(item)[0]))
        )
        joined_value = format_received_date(joined)
        if joined_value:
            candidates.append((min((line.confidence for line in focused_lines), default=0.0), joined_value))
    confidence, value = max(candidates, default=(0.0, ""))
    return value, confidence


def analyze_receipt_screenshot(image: np.ndarray) -> dict[str, object]:
    height, width = image.shape[:2]
    scale = max(1.0, min(2.5, 850 / max(height, 1)))
    enlarged = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    variants = [image, enlarged]
    recognized = [(variant, recognize(variant)) for variant in variants]
    results = [extract_receipt_naming(lines, variant.shape) for variant, lines in recognized]

    for field, output_field in (("receipt", "receipt_number"), ("invoice", "invoice_number")):
        values = {result[output_field] for result in results if result[output_field]}
        confident = any(result["_field_confidences"].get(output_field, 0.0) >= 0.8 for result in results)
        if len(values) == 1 and confident:
            continue
        for variant, lines in recognized:
            value, confidence = _recognize_focused_code(variant, lines, field)
            if value:
                fields = {name: "" for name in ("creditor", "invoice_number", "receipt_number", "received_date")}
                fields[output_field] = value
                results.append(_build_result(fields, {output_field: confidence}))

    if not any(result["received_date"] for result in results):
        received_date, confidence = _recognize_focused_date(image, recognized[0][1])
        if received_date:
            first = results[0]
            fields = {
                field: str(first.get(field, ""))
                for field in ("creditor", "invoice_number", "receipt_number", "received_date")
            }
            field_confidences = dict(first["_field_confidences"])
            fields["received_date"] = received_date
            field_confidences["received_date"] = confidence
            results[0] = _build_result(fields, field_confidences)

    # A crisp monochrome pass helps screenshots whose browser antialiasing
    # makes small, thin characters difficult to detect.
    if not any(not result["missing_fields"] and result["confidence"] >= 0.8 for result in results):
        gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        results.append(extract_receipt_naming(recognize(binary), binary.shape))

    field_names = ("creditor", "invoice_number", "receipt_number", "received_date")
    combined_fields: dict[str, str] = {}
    combined_confidences: dict[str, float] = {}
    for field in field_names:
        candidates = [
            (float(result["_field_confidences"].get(field, 0.0)), str(result.get(field, "")))
            for result in results
            if result.get(field)
        ]
        confidence, value = max(candidates, default=(0.0, ""))
        # Disagreement is evidence of uncertainty even when OCR scores are high.
        if len({candidate_value for _, candidate_value in candidates}) > 1:
            confidence = min(confidence, 0.79)
        combined_fields[field] = value
        combined_confidences[field] = confidence

    combined = _build_result(combined_fields, combined_confidences)
    return {key: value for key, value in combined.items() if not key.startswith("_")}


def _largest_embedded_page_image(page) -> np.ndarray | None:
    candidates: list[tuple[int, Image.Image]] = []
    try:
        page_images = list(page.images)
    except Exception:
        return None

    for image_file in page_images[:20]:
        try:
            pil_image = getattr(image_file, "image", None)
            if pil_image is None:
                pil_image = Image.open(io.BytesIO(image_file.data))
            pil_image.load()
            width, height = pil_image.size
            if width * height < 90_000 or width * height > 30_000_000:
                continue
            candidates.append((width * height, pil_image.copy()))
        except (OSError, ValueError, Image.DecompressionBombError):
            continue

    if not candidates:
        return None
    pil_image = max(candidates, key=lambda item: item[0])[1].convert("RGB")
    rgb = np.asarray(pil_image)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def analyze_receipt_pdf(data: bytes) -> dict[str, object]:
    """Read receipt fields from PDF text, with embedded-image OCR fallback."""
    if not data:
        raise ImageError("PDF kedua kosong.")
    if len(data) > MAX_RECEIPT_PDF_BYTES:
        raise ImageError("Ukuran maksimal PDF kedua adalah 100 MB.")

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ImageError("PDF kedua memakai kata sandi dan tidak dapat dibaca.")
        if not reader.pages:
            raise ImageError("PDF kedua tidak memiliki halaman.")
        if len(reader.pages) > MAX_RECEIPT_PDF_PAGES:
            raise ImageError(f"PDF penamaan maksimal {MAX_RECEIPT_PDF_PAGES} halaman.")

        text_results: list[dict[str, object]] = []
        text_characters = 0
        for page in reader.pages[:5]:
            page_text = page.extract_text() or ""
            text_characters += len(page_text.strip())
            if page_text.strip():
                text_results.append(extract_receipt_text(page_text))

        field_names = ("creditor", "invoice_number", "receipt_number", "received_date")
        best_text = max(
            text_results,
            key=lambda result: sum(bool(result.get(field)) for field in field_names),
            default=_build_result({field: "" for field in field_names}, {field: 0.0 for field in field_names}),
        )
        if not best_text["missing_fields"]:
            return {
                **{key: value for key, value in best_text.items() if not key.startswith("_")},
                "source": "pdf_text",
                "text_characters": text_characters,
            }

        ocr_results: list[dict[str, object]] = []
        for page in reader.pages[:3]:
            image = _largest_embedded_page_image(page)
            if image is not None:
                ocr_results.append(analyze_receipt_screenshot(image))

        combined_fields: dict[str, str] = {}
        combined_confidences: dict[str, float] = {}
        used_text = False
        used_ocr = False
        for field in field_names:
            if best_text.get(field):
                combined_fields[field] = str(best_text[field])
                combined_confidences[field] = float(best_text["_field_confidences"].get(field, 0.0))
                used_text = True
                continue
            ocr_candidates = [
                (float(result.get("confidence", 0.0)), str(result.get(field, "")))
                for result in ocr_results
                if result.get(field)
            ]
            confidence, value = max(ocr_candidates, default=(0.0, ""))
            combined_fields[field] = value
            combined_confidences[field] = confidence
            used_ocr = used_ocr or bool(value)

        combined = _build_result(combined_fields, combined_confidences)
        if used_text and used_ocr:
            source = "pdf_text_ocr"
        elif used_ocr:
            source = "pdf_ocr"
        else:
            source = "pdf_text" if text_characters else "unreadable"
        return {
            **{key: value for key, value in combined.items() if not key.startswith("_")},
            "source": source,
            "text_characters": text_characters,
        }
    except ImageError:
        raise
    except (PdfReadError, ValueError, OSError, EOFError) as exc:
        raise ImageError("PDF kedua tidak valid atau rusak.") from exc
