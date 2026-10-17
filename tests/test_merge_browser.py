"""Optional browser regression: pip install playwright; uses installed Edge on Windows."""
from __future__ import annotations

import io
import socket
import threading
import time

import pytest
import uvicorn
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.scanner import build_pdf
from app.main import app


def preview_fixtures(directory):
    images = []
    for number in range(1, 4):
        image = Image.new("RGB", (400, 600), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((15, 15, 100, 70), fill="red")
        draw.text((30, 100), f"PRIMARY PAGE {number}", fill="black", font_size=24)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        images.append(buffer.getvalue())
    writer = PdfWriter()
    for index, page in enumerate(PdfReader(io.BytesIO(build_pdf(images))).pages):
        added = writer.add_page(page)
        if index == 1:
            added.rotate(90)
    primary = directory / "primary.pdf"
    with primary.open("wb") as stream:
        writer.write(stream)
    writer.close()

    writer = PdfWriter()
    for number in range(2):
        page = writer.add_blank_page(400, 600)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        })
        lines = [f"SECONDARY PAGE {number + 1}", "Kreditur", "TEST MEDICA PT",
                 "Nomor Penerimaan Barang", "RC11117F012631861G",
                 "Tanggal Penerimaan Barang", "11 Mei 2026", "Nomor Faktur", "1234567890"]
        content = DecodedStreamObject()
        content.set_data(("BT /F1 14 Tf 30 560 Td 25 TL "
                          + " T* ".join(f"({line}) Tj" for line in lines) + " ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    secondary = directory / "secondary.pdf"
    with secondary.open("wb") as stream:
        writer.write(stream)
    writer.close()
    return primary, secondary


def test_browser_preview_rotation_download_and_reset(tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    with browser_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        except browser_api.Error:
            pytest.skip("Microsoft Edge is required for this optional browser check")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Preview test server did not start")
            primary, secondary = preview_fixtures(tmp_path)
            page = browser.new_page(viewport={"width": 1280, "height": 1000}, accept_downloads=True)
            page.add_init_script("Object.defineProperty(window, 'showSaveFilePicker', {value: undefined});")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url + "/merge")
            expect = browser_api.expect
            rotate = page.locator('#primaryPreview [data-action="rotate"]')
            selector = page.locator('#primaryPreview [data-action="page"]')
            summary = page.locator('#primaryPreview [data-role="rotations"]')
            canvas = page.locator("#primaryPreview canvas")
            page.locator("#primaryPdf").set_input_files(primary)
            expect(rotate).to_be_enabled(timeout=30000)
            original_pixels = canvas.evaluate("canvas => canvas.toDataURL()")
            rotate.click()
            expect(rotate).to_be_enabled()
            assert canvas.evaluate("canvas => canvas.toDataURL()") != original_pixels
            expect(summary).to_contain_text("halaman 1.")
            rotate.click()
            expect(rotate).to_be_enabled()
            assert canvas.evaluate("canvas => canvas.toDataURL()") == original_pixels
            selector.select_option("2")
            expect(rotate).to_be_enabled()
            rotate.click()
            expect(rotate).to_be_enabled()
            selector.select_option("3")
            expect(rotate).to_be_enabled()
            rotate.click()
            expect(rotate).to_be_enabled()
            expect(summary).to_contain_text("2–3")
            selector.select_option("2")
            expect(rotate).to_have_attribute("aria-pressed", "true")
            expect(rotate).to_be_enabled()
            page.locator('#primaryPreview [data-action="zoom"]').click()
            expect(page.locator("#mergePreviewDialog")).to_be_visible()
            expect(page.locator("#mergePreviewTitle")).to_contain_text("Halaman 2")
            page.locator("#closeMergePreview").click()
            page.locator("#secondaryPdf").set_input_files(secondary)
            next_secondary = page.locator('#secondaryPreview [data-action="next"]')
            expect(next_secondary).to_be_enabled(timeout=30000)
            page.locator("#mergeBusy").wait_for(state="hidden", timeout=30000)
            next_secondary.click()
            expect(page.locator("#secondaryPreview .pdf-preview-message")).to_contain_text("Halaman 2 dari 2")
            expect(page.locator("#resultName")).to_have_value("TM_1234567890_RC11117F012631861G_110526.pdf")
            with page.expect_download() as download_info:
                page.locator("#mergeButton").click()
            downloaded = tmp_path / "merged.pdf"
            download_info.value.save_as(downloaded)
            merged = PdfReader(downloaded)
            assert [page.rotation for page in merged.pages] == [0, 270, 180, 0, 0]
            assert "SECONDARY PAGE 1" in merged.pages[3].extract_text()
            assert "SECONDARY PAGE 2" in merged.pages[4].extract_text()
            # A new first PDF must not inherit any previous page selections.
            page.locator("#primaryPdf").set_input_files(secondary)
            expect(rotate).to_be_enabled(timeout=30000)
            page.locator("#mergeBusy").wait_for(state="hidden", timeout=30000)
            expect(selector.locator("option")).to_have_count(2)
            expect(summary).to_contain_text("Belum ada")
            rotate.click()
            expect(rotate).to_be_enabled()
            page.locator('#primaryPreview [data-action="reset-rotation"]').click()
            expect(summary).to_contain_text("Belum ada")
            # Check the responsive layout and cleanup on Clear.
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("#clearMergeButton").click()
            expect(page.locator("#primaryPreview")).to_be_hidden()
            expect(page.locator("#secondaryPreview")).to_be_hidden()
            expect(page.locator("#mergeButton")).to_be_disabled()
            # Invalid uploads show a recoverable error instead of breaking the page.
            page.locator("#primaryPdf").set_input_files({"name": "bad.pdf", "mimeType": "application/pdf", "buffer": b"not a pdf"})
            expect(page.locator("#primaryPreview .pdf-preview-message")).to_contain_text("Pratinjau tidak tersedia", timeout=30000)
            page.locator("#primaryPdf").set_input_files(primary)
            expect(rotate).to_be_enabled(timeout=30000)
            assert errors == []
        finally:
            browser.close()
            server.should_exit = True
            server_thread.join(timeout=10)
