from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np
from PIL import Image

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 30_000_000


class ImageError(ValueError):
    pass


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise ImageError("File gambar kosong.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("Ukuran gambar maksimal 20 MB.")
    array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ImageError("Format gambar tidak dapat dibaca.")
    height, width = image.shape[:2]
    if height * width > MAX_PIXELS:
        raise ImageError("Resolusi gambar terlalu besar.")
    return image


def _order_points(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]  # top-left
    ordered[2] = points[np.argmax(sums)]  # bottom-right
    ordered[1] = points[np.argmin(differences)]  # top-right
    ordered[3] = points[np.argmax(differences)]  # bottom-left
    return ordered


def detect_document(image: np.ndarray) -> tuple[np.ndarray, bool]:
    height, width = image.shape[:2]
    scale = min(1.0, 1400.0 / max(height, width))
    resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_area = resized.shape[0] * resized.shape[1] * 0.16

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        if cv2.contourArea(contour) < min_area:
            break
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            points = _order_points(polygon.reshape(4, 2) / scale)
            return points, True

    margin_x = width * 0.025
    margin_y = height * 0.025
    fallback = np.array(
        [
            [margin_x, margin_y],
            [width - margin_x, margin_y],
            [width - margin_x, height - margin_y],
            [margin_x, height - margin_y],
        ],
        dtype=np.float32,
    )
    return fallback, False


def normalized_points(points: np.ndarray, width: int, height: int) -> list[list[float]]:
    return [[round(float(x / width), 6), round(float(y / height), 6)] for x, y in points]


def parse_corners(value: str | None, width: int, height: int) -> np.ndarray:
    if not value:
        points, _ = detect_document(np.zeros((height, width, 3), dtype=np.uint8))
        return points
    try:
        raw = json.loads(value)
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError
        points = np.asarray(raw, dtype=np.float32)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            raise ValueError
        if (points < 0).any() or (points > 1).any():
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ImageError("Koordinat crop tidak valid.") from exc
    points[:, 0] *= width
    points[:, 1] *= height
    return _order_points(points)


def perspective_crop(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    top_left, top_right, bottom_right, bottom_left = _order_points(points)
    width = int(
        max(
            np.linalg.norm(bottom_right - bottom_left),
            np.linalg.norm(top_right - top_left),
        )
    )
    height = int(
        max(
            np.linalg.norm(top_right - bottom_right),
            np.linalg.norm(top_left - bottom_left),
        )
    )
    if width < 100 or height < 100:
        raise ImageError("Area crop terlalu kecil.")
    width = min(width, 6000)
    height = min(height, 6000)
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(_order_points(points), destination)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC)


def _normalize_lighting(gray: np.ndarray) -> np.ndarray:
    longest = max(gray.shape[:2])
    sigma = max(12, int(longest / 45))
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    normalized = cv2.divide(gray, np.maximum(background, 1), scale=245)
    return cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(normalized)


def apply_filter(image: np.ndarray, mode: str) -> np.ndarray:
    if mode == "original":
        return image
    if mode == "no_shadow_color":
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        lightness, channel_a, channel_b = cv2.split(lab)
        lightness = _normalize_lighting(lightness)
        return cv2.cvtColor(cv2.merge((lightness, channel_a, channel_b)), cv2.COLOR_LAB2BGR)
    if mode == "no_shadow_bw":
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        normalized = _normalize_lighting(gray)
        block_size = max(31, (min(normalized.shape[:2]) // 18) | 1)
        block_size = min(block_size, 151)
        return cv2.adaptiveThreshold(
            normalized,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            11,
        )
    raise ImageError("Pilihan filter tidak dikenal.")


def encode_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ImageError("Gagal memproses gambar.")
    return encoded.tobytes()


def build_pdf(page_bytes: Iterable[bytes]) -> bytes:
    pages: list[Image.Image] = []
    for data in page_bytes:
        page = Image.open(io.BytesIO(data))
        page.load()
        if page.mode != "RGB":
            page = page.convert("RGB")
        pages.append(page)
    if not pages:
        raise ImageError("Minimal satu halaman diperlukan.")
    output = io.BytesIO()
    first, *rest = pages
    first.save(output, format="PDF", save_all=True, append_images=rest, resolution=150.0)
    return output.getvalue()


def safe_filename(value: str) -> str:
    value = value.strip()
    if value.lower().endswith(".pdf"):
        value = value[:-4]
    value = re.sub(r"[^A-Za-z0-9._ -]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[-_.]{2,}", "-", value).strip("-._")
    value = value.strip()
    if not value:
        raise ImageError("Nama file tidak valid.")
    return value[:120] + ".pdf"
