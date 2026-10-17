from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from .scanner import ImageError

MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_MERGED_PAGES = 500


def merge_two_pdfs(
    primary: bytes, secondary: bytes, *, rotate_primary_pages: list[int] | None = None
) -> bytes:
    """Append PDF 2, rotating selected one-based pages of PDF 1 by 180 degrees."""
    if not primary or not secondary:
        raise ImageError("PDF pertama dan PDF kedua wajib dipilih.")
    if len(primary) > MAX_PDF_BYTES or len(secondary) > MAX_PDF_BYTES:
        raise ImageError("Ukuran maksimal setiap PDF adalah 100 MB.")
    requested_pages = [] if rotate_primary_pages is None else rotate_primary_pages
    if (not isinstance(requested_pages, list) or len(requested_pages) > MAX_MERGED_PAGES
            or any(type(number) is not int or number < 1 for number in requested_pages)):
        raise ImageError("Pilihan rotasi harus berupa daftar nomor halaman PDF pertama yang valid.")
    rotation_pages = set(requested_pages)

    writer = PdfWriter()
    try:
        for position, data in enumerate((primary, secondary), start=1):
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ImageError(f"PDF ke-{position} memakai kata sandi dan tidak dapat digabungkan.")
            if not reader.pages:
                raise ImageError(f"PDF ke-{position} tidak memiliki halaman.")
            if position == 1 and any(number > len(reader.pages) for number in rotation_pages):
                raise ImageError("Halaman yang ingin diputar tidak ada di PDF pertama.")
            for number, page in enumerate(reader.pages, start=1):
                output_page = writer.add_page(page)
                if position == 1 and number in rotation_pages:
                    output_page.rotation = (output_page.rotation + 180) % 360

        if len(writer.pages) > MAX_MERGED_PAGES:
            raise ImageError(f"Jumlah hasil maksimal {MAX_MERGED_PAGES} halaman.")

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except ImageError:
        raise
    except (PdfReadError, ValueError, OSError, EOFError) as exc:
        raise ImageError("Salah satu file bukan PDF yang valid atau rusak.") from exc
    finally:
        writer.close()
