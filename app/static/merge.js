import { createPdfPreview } from "/pdf-preview.js";

const primaryInput = document.querySelector("#primaryPdf");
const secondaryInput = document.querySelector("#secondaryPdf");
const primaryName = document.querySelector("#primaryName");
const secondaryName = document.querySelector("#secondaryName");
const resultName = document.querySelector("#resultName");
const namingInput = document.querySelector("#namingImage");
const namingMessage = document.querySelector("#namingMessage");
const namingSelection = document.querySelector("#namingSelection");
const pasteZone = document.querySelector("#pasteZone");
const pdfNamingMessage = document.querySelector("#pdfNamingMessage");
const screenshotFallback = document.querySelector("#screenshotFallback");
const mergeButton = document.querySelector("#mergeButton");
const clearButton = document.querySelector("#clearMergeButton");
const message = document.querySelector("#mergeMessage");
const busy = document.querySelector("#mergeBusy");
const busyText = document.querySelector("#mergeBusyText");
let pdfAnalysisSequence = 0;
const primaryPreview = createPdfPreview(document.querySelector("#primaryPreview"), {
  allowRotation: true, title: "PDF pertama",
});
const secondaryPreview = createPdfPreview(document.querySelector("#secondaryPreview"), {
  title: "PDF kedua",
});

const fieldLabels = {
  creditor: "Kreditur",
  invoice_number: "Nomor Faktur",
  receipt_number: "Nomor Penerimaan Barang",
  received_date: "Tanggal Penerimaan Barang",
};

function selected(input) {
  return input.files && input.files[0] ? input.files[0] : null;
}

function canMerge() {
  return Boolean(selected(primaryInput) && selected(secondaryInput) && resultName.value.trim());
}

function refresh(resetFilename = false) {
  const first = selected(primaryInput);
  const second = selected(secondaryInput);
  primaryName.textContent = first ? first.name : "Belum ada file dipilih";
  secondaryName.textContent = second ? second.name : "Belum ada file dipilih";
  if (resetFilename) resultName.value = first ? first.name : "";
  resultName.disabled = !first;
  mergeButton.disabled = !canMerge();
  message.hidden = true;
}

function showMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("error", isError);
  message.hidden = false;
}

function clearScreenshotFallback() {
  namingInput.value = "";
  namingSelection.textContent = "Belum ada screenshot";
  namingMessage.hidden = true;
  screenshotFallback.open = false;
}

async function analyzeSecondaryPdf(pdf) {
  const sequence = ++pdfAnalysisSequence;
  const form = new FormData();
  form.append("secondary_pdf", pdf, pdf.name);
  busyText.textContent = "Sedang membaca data dari PDF kedua…";
  busy.hidden = false;
  pdfNamingMessage.hidden = true;

  try {
    const response = await fetch("/api/pdf-receipt-name", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (sequence !== pdfAnalysisSequence) return;
    if (!response.ok) throw new Error(data.detail || "PDF kedua gagal dianalisis.");

    if (data.suggested_filename) {
      resultName.value = data.suggested_filename;
      const source = data.source === "pdf_text"
        ? "teks PDF"
        : data.source === "pdf_ocr"
          ? "OCR halaman PDF"
          : "teks dan OCR PDF";
      const details = `${data.creditor} · Faktur ${data.invoice_number} · ${data.receipt_number} · ${data.received_date}`;
      pdfNamingMessage.textContent = data.needs_review
        ? `Nama dibuat dari ${source}, tetapi mohon periksa kembali: ${details}`
        : `Nama otomatis dari ${source}: ${details}`;
      pdfNamingMessage.classList.toggle("success", !data.needs_review);
      pdfNamingMessage.classList.remove("error");
      screenshotFallback.open = false;
    } else {
      const missing = (data.missing_fields || []).map((field) => fieldLabels[field] || field).join(", ");
      pdfNamingMessage.textContent = `PDF kedua belum terbaca lengkap: ${missing || "data tidak ditemukan"}. Gunakan screenshot di bawah atau isi nama secara manual.`;
      pdfNamingMessage.classList.remove("success", "error");
      screenshotFallback.open = true;
    }
    pdfNamingMessage.hidden = false;
    mergeButton.disabled = !canMerge();
  } catch (error) {
    if (sequence !== pdfAnalysisSequence) return;
    pdfNamingMessage.textContent = error.message || "PDF kedua gagal dianalisis.";
    pdfNamingMessage.classList.remove("success");
    pdfNamingMessage.classList.add("error");
    pdfNamingMessage.hidden = false;
    screenshotFallback.open = true;
  } finally {
    if (sequence === pdfAnalysisSequence) busy.hidden = true;
  }
}

primaryInput.addEventListener("change", () => {
  void primaryPreview.load(selected(primaryInput));
  clearScreenshotFallback();
  refresh(true);
  const second = selected(secondaryInput);
  if (second) analyzeSecondaryPdf(second);
});
secondaryInput.addEventListener("change", () => {
  void secondaryPreview.load(selected(secondaryInput));
  pdfAnalysisSequence += 1;
  clearScreenshotFallback();
  pdfNamingMessage.hidden = true;
  refresh(true);
  const second = selected(secondaryInput);
  if (second) analyzeSecondaryPdf(second);
});
resultName.addEventListener("input", () => {
  mergeButton.disabled = !canMerge();
  message.hidden = true;
});

async function analyzeNamingImage(screenshot, sourceName) {
  const form = new FormData();
  form.append("screenshot", screenshot, sourceName);
  namingSelection.textContent = sourceName;
  busyText.textContent = "Sedang membaca data penamaan…";
  busy.hidden = false;
  namingMessage.hidden = true;

  try {
    const response = await fetch("/api/ocr-receipt-name", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Screenshot gagal dibaca OCR.");

    if (data.suggested_filename) {
      resultName.value = data.suggested_filename;
      const details = `${data.creditor} · Faktur ${data.invoice_number} · ${data.receipt_number} · ${data.received_date}`;
      namingMessage.textContent = data.needs_review
        ? `Nama berhasil dibuat, tetapi mohon periksa kembali: ${details}`
        : `Nama otomatis berhasil dibuat: ${details}`;
      namingMessage.classList.toggle("success", !data.needs_review);
      namingMessage.classList.remove("error");
    } else {
      const missing = (data.missing_fields || []).map((field) => fieldLabels[field] || field).join(", ");
      namingMessage.textContent = `Belum terbaca lengkap: ${missing || "data tidak ditemukan"}. Coba screenshot ulang atau isi nama secara manual.`;
      namingMessage.classList.remove("success", "error");
    }
    namingMessage.hidden = false;
    mergeButton.disabled = !canMerge();
  } catch (error) {
    namingMessage.textContent = error.message || "Screenshot gagal dibaca OCR.";
    namingMessage.classList.remove("success");
    namingMessage.classList.add("error");
    namingMessage.hidden = false;
  } finally {
    busy.hidden = true;
    pasteZone.classList.remove("pasting");
  }
}

namingInput.addEventListener("change", async () => {
  const screenshot = selected(namingInput);
  if (!screenshot) {
    namingSelection.textContent = "Belum ada screenshot";
    namingMessage.hidden = true;
    return;
  }
  await analyzeNamingImage(screenshot, screenshot.name);
});

pasteZone.addEventListener("click", () => pasteZone.focus());
document.addEventListener("paste", async (event) => {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find((item) => item.kind === "file" && item.type.startsWith("image/"));
  if (!imageItem) {
    if (document.activeElement === pasteZone) {
      namingMessage.textContent = "Clipboard tidak berisi gambar. Salin screenshot lalu tekan Ctrl + V lagi.";
      namingMessage.classList.remove("success");
      namingMessage.classList.add("error");
      namingMessage.hidden = false;
    }
    return;
  }

  const screenshot = imageItem.getAsFile();
  if (!screenshot) return;
  event.preventDefault();
  namingInput.value = "";
  screenshotFallback.open = true;
  pasteZone.classList.add("pasting");
  const extension = screenshot.type === "image/jpeg" ? "jpg" : "png";
  await analyzeNamingImage(screenshot, `Screenshot clipboard.${extension}`);
});

clearButton.addEventListener("click", () => {
  primaryPreview.reset();
  secondaryPreview.reset();
  pdfAnalysisSequence += 1;
  primaryInput.value = "";
  secondaryInput.value = "";
  clearScreenshotFallback();
  pdfNamingMessage.hidden = true;
  refresh(true);
});

mergeButton.addEventListener("click", async () => {
  const first = selected(primaryInput);
  const second = selected(secondaryInput);
  if (!first || !second) return;
  const rotationPages = primaryPreview.rotationPages();

  let saveHandle = null;
  const requestedName = resultName.value.trim().toLowerCase().endsWith(".pdf")
    ? resultName.value.trim()
    : `${resultName.value.trim()}.pdf`;
  if ("showSaveFilePicker" in window) {
    try {
      saveHandle = await window.showSaveFilePicker({
        suggestedName: requestedName,
        types: [{
          description: "Dokumen PDF",
          accept: { "application/pdf": [".pdf"] },
        }],
      });
    } catch (error) {
      if (error.name === "AbortError") return;
      // Some browsers expose the API but block it by policy. In that case,
      // keep the ordinary browser download as a safe fallback.
      saveHandle = null;
    }
  }

  const form = new FormData();
  form.append("primary_pdf", first, first.name);
  form.append("secondary_pdf", second, second.name);
  form.append("output_filename", resultName.value.trim());
  form.append("rotate_primary_pages", JSON.stringify(rotationPages));
  busyText.textContent = "Sedang menggabungkan PDF…";
  busy.hidden = false;
  mergeButton.disabled = true;
  message.hidden = true;

  try {
    const response = await fetch("/api/merge-pdf", { method: "POST", body: form });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || "PDF gagal digabungkan.");
    }

    const blob = await response.blob();
    const encodedName = response.headers.get("X-Download-Filename");
    const downloadName = encodedName ? decodeURIComponent(encodedName) : resultName.value.trim();
    if (saveHandle) {
      const writable = await saveHandle.createWritable();
      await writable.write(blob);
      await writable.close();
      showMessage(`Selesai. File disimpan sebagai ${saveHandle.name}`);
    } else {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = downloadName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      showMessage(`Selesai. Hasil diunduh sebagai ${downloadName}`);
    }
  } catch (error) {
    showMessage(error.message || "PDF gagal digabungkan.", true);
  } finally {
    busy.hidden = true;
    mergeButton.disabled = !canMerge();
  }
});

refresh(true);
