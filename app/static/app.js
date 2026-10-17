const state = {
  pages: [],
  visitFiles: [],
  editingFile: null,
  editingIndex: -1,
  recordConfidence: 0,
  preparedShareFile: null,
  preparedShareSignature: "",
  trackingSummary: null,
  duplicateStatus: null,
  duplicateKey: "",
  duplicateConfirmedKey: "",
  duplicateRequest: 0,
  duplicateTimer: null,
  image: null,
  corners: [],
  dragging: -1,
};

const $ = (selector) => document.querySelector(selector);
const documentInput = $("#documentInput");
const visitInput = $("#visitInput");
const dialog = $("#cropDialog");
const previewDialog = $("#previewDialog");
const periodDialog = $("#periodDialog");
const previewImage = $("#previewImage");
const canvas = $("#cropCanvas");
const ctx = canvas.getContext("2d");

function showBusy(show, text = "Memproses…") {
  $("#busy p").textContent = text;
  $("#busy").hidden = !show;
}

function showMessage(element, text, error = false) {
  element.textContent = text;
  element.classList.toggle("error", error);
  element.hidden = !text;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Permintaan gagal (${response.status})`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response;
}

function periodLabel(period) {
  const [year, month] = (period || "").split("-").map(Number);
  if (!year || !month) return period || "—";
  return new Intl.DateTimeFormat("id-ID", { month: "long", year: "numeric" })
    .format(new Date(year, month - 1, 1));
}

function sharedAtLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("id-ID", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function nextPeriod(period) {
  const [year, month] = (period || "").split("-").map(Number);
  const date = year && month ? new Date(year, month, 1) : new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function renderPeriodOptions(summary) {
  const select = $("#periodSelect");
  select.innerHTML = "";
  summary.periods.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.period;
    option.textContent = `${periodLabel(item.period)} — ${item.completed_count}/${item.target_count}${item.period === summary.active_period ? " (aktif)" : ""}`;
    option.selected = item.period === summary.period;
    select.appendChild(option);
  });
}

function renderChecklist() {
  const summary = state.trackingSummary;
  if (!summary) return;
  const search = $("#checklistSearch").value.trim();
  const filter = $("#checklistFilter").value;
  const entryByNumber = new Map(summary.entries.map((entry) => [entry.record_number, entry]));
  const numbers = [];
  for (let number = 1; number <= summary.target_count; number += 1) numbers.push(String(number));
  summary.entries.forEach((entry) => {
    if (!numbers.includes(entry.record_number)) numbers.push(entry.record_number);
  });

  const visible = numbers.filter((number) => {
    const done = entryByNumber.has(number);
    if (search && !number.includes(search)) return false;
    if (filter === "done" && !done) return false;
    if (filter === "pending" && done) return false;
    return true;
  });

  const list = $("#checklistList");
  const fragment = document.createDocumentFragment();
  visible.forEach((number) => {
    const entry = entryByNumber.get(number);
    const item = document.createElement("div");
    item.className = `checklist-item${entry ? " done" : ""}`;

    const mark = document.createElement("span");
    mark.className = "checklist-mark";
    mark.textContent = entry ? "✓" : "";

    const data = document.createElement("div");
    data.className = "checklist-data";
    const title = document.createElement("strong");
    title.textContent = `Nomor ${number}`;
    data.appendChild(title);
    if (entry) {
      const detail = document.createElement("small");
      detail.textContent = `${entry.visit_number} • ${sharedAtLabel(entry.shared_at)}`;
      detail.title = entry.filename;
      data.appendChild(detail);
    }

    item.append(mark, data);
    if (entry && summary.is_active) {
      const undo = document.createElement("button");
      undo.type = "button";
      undo.className = "undo-record";
      undo.dataset.record = number;
      undo.textContent = "Batalkan";
      item.appendChild(undo);
    }
    fragment.appendChild(item);
  });
  list.replaceChildren(fragment);
  $("#checklistEmpty").hidden = visible.length > 0;
}

async function loadTracking(period = "") {
  try {
    const suffix = period ? `?period=${encodeURIComponent(period)}` : "";
    const summary = await (await api(`/api/tracking/summary${suffix}`)).json();
    state.trackingSummary = summary;
    $("#activePeriodLabel").textContent = periodLabel(summary.period);
    $("#progressCount").textContent = `${summary.completed_count} / ${summary.target_count}`;
    $("#progressBar").style.width = `${Math.min(100, summary.completed_count / summary.target_count * 100)}%`;
    $("#progressText").textContent = summary.is_active
      ? `${summary.remaining_count} nomor belum selesai pada periode aktif.`
      : `Arsip ${periodLabel(summary.period)}. Periode aktif: ${periodLabel(summary.active_period)}.`;
    renderPeriodOptions(summary);
    renderChecklist();
    showMessage($("#trackingMessage"), "");
  } catch (error) {
    showMessage($("#trackingMessage"), `Checklist gagal dimuat: ${error.message}`, true);
  }
}

function duplicateInputKey() {
  const record = $("#recordNumber").value.trim();
  const visit = $("#visitNumber").value.trim();
  return record ? `${record}|${visit}` : "";
}

function clearDuplicateWarning() {
  state.duplicateStatus = null;
  state.duplicateKey = "";
  const warning = $("#duplicateWarning");
  warning.hidden = true;
  warning.textContent = "";
  warning.classList.remove("possible");
}

function showDuplicateStatus(status, key) {
  const warning = $("#duplicateWarning");
  state.duplicateStatus = status;
  state.duplicateKey = key;
  if (!status.record_match) {
    warning.hidden = true;
    warning.textContent = "";
    return;
  }
  const previous = status.latest;
  const when = sharedAtLabel(previous?.shared_at);
  if (status.exact_match) {
    warning.classList.remove("possible");
    warning.textContent = `Duplikat: nomor ${status.record_number} dengan nomor kunjungan/resep ini sudah pernah dibagikan${when ? ` pada ${when}` : ""}.`;
  } else {
    warning.classList.add("possible");
    warning.textContent = `Perhatian: nomor ${status.record_number} sudah pernah dibagikan${previous?.visit_number ? ` dengan nomor ${previous.visit_number}` : ""}${when ? ` pada ${when}` : ""}. Nomor kunjungan/resep sekarang berbeda.`;
  }
  warning.hidden = false;
}

async function checkDuplicateNow() {
  const key = duplicateInputKey();
  const record = $("#recordNumber").value.trim();
  const visit = $("#visitNumber").value.trim();
  if (!record) {
    clearDuplicateWarning();
    return null;
  }
  const requestId = ++state.duplicateRequest;
  try {
    const params = new URLSearchParams({ record_number: record, visit_number: visit });
    const status = await (await api(`/api/tracking/check?${params}`)).json();
    if (requestId !== state.duplicateRequest || key !== duplicateInputKey()) return null;
    showDuplicateStatus(status, key);
    return status;
  } catch (_) {
    if (requestId === state.duplicateRequest) clearDuplicateWarning();
    return null;
  }
}

function scheduleDuplicateCheck() {
  const key = duplicateInputKey();
  if (key !== state.duplicateKey) state.duplicateConfirmedKey = "";
  window.clearTimeout(state.duplicateTimer);
  if (!key) return clearDuplicateWarning();
  state.duplicateTimer = window.setTimeout(checkDuplicateNow, 300);
}

async function checkHealth() {
  try {
    const data = await (await api("/api/health")).json();
    const badge = $("#serverStatus");
    badge.textContent = data.ocr_ready ? "OCR siap" : "OCR bermasalah";
    badge.className = `status ${data.ocr_ready ? "ready" : "warning"}`;
    if (!data.ocr_ready) showMessage($("#ocrMessage"), data.ocr_error || "OCR belum siap.", true);
  } catch (error) {
    $("#serverStatus").textContent = "Server putus";
    $("#serverStatus").className = "status warning";
  }
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) * canvas.width / rect.width,
    y: (event.clientY - rect.top) * canvas.height / rect.height,
  };
}

function drawCrop() {
  if (!state.image) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
  ctx.save();
  ctx.fillStyle = "rgba(8, 20, 16, .48)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.globalCompositeOperation = "destination-out";
  ctx.beginPath();
  state.corners.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  ctx.beginPath();
  state.corners.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
  ctx.closePath();
  ctx.strokeStyle = "#58e09d";
  ctx.lineWidth = Math.max(3, canvas.width / 250);
  ctx.stroke();
  state.corners.forEach((point) => {
    ctx.beginPath();
    ctx.arc(point.x, point.y, Math.max(10, canvas.width / 70), 0, Math.PI * 2);
    ctx.fillStyle = "#58e09d";
    ctx.fill();
    ctx.strokeStyle = "white";
    ctx.lineWidth = 3;
    ctx.stroke();
  });
}

function setCanvasImage(file, detectedCorners) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => {
      const maxSide = 1400;
      const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.round(image.naturalWidth * scale);
      canvas.height = Math.round(image.naturalHeight * scale);
      state.image = image;
      state.corners = detectedCorners.map(([x, y]) => ({ x: x * canvas.width, y: y * canvas.height }));
      drawCrop();
      URL.revokeObjectURL(url);
      resolve();
    };
    image.onerror = reject;
    image.src = url;
  });
}

async function autoScanFiles(files) {
  const failures = [];
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    showBusy(true, `Auto scan ${index + 1} dari ${files.length}…`);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("filter_mode", $("#scanFilterMode").value);
      const response = await api("/api/auto-scan", { method: "POST", body: form });
      const blob = await response.blob();
      const corners = JSON.parse(response.headers.get("X-Crop-Corners"));
      const strategy = response.headers.get("X-Crop-Strategy") || "fallback";
      const recordNumber = response.headers.get("X-Record-Number") || "";
      const confidence = Number(response.headers.get("X-Record-Confidence") || 0);
      const isFirstDocument = state.pages.length === 0;
      state.pages.push({ raw: file, blob, url: URL.createObjectURL(blob), corners, strategy });

      // The large patient number belongs to the first document photo. Never
      // let dates or other numbers from later PDF pages overwrite it.
      if (isFirstDocument && recordNumber) {
        state.recordConfidence = confidence;
        $("#recordNumber").value = recordNumber;
        $("#recordDetected").textContent = `Nomor ${recordNumber} terbaca otomatis dari hasil scan.`;
        updateFilename();
      } else if (isFirstDocument && !recordNumber) {
        $("#recordDetected").textContent = "Angka besar pada foto pertama belum terbaca. Foto ulang lebih dekat atau isi manual.";
      }
      renderPages();
    } catch (error) {
      failures.push(`${file.name}: ${error.message}`);
    }
  }
  showBusy(false);
  if (failures.length) alert(`Ada foto yang gagal diproses:\n${failures.join("\n")}`);
}

async function openPageEditor(index) {
  const page = state.pages[index];
  if (!page) return;
  state.editingFile = page.raw;
  state.editingIndex = index;
  $("#filterMode").value = $("#scanFilterMode").value;
  showMessage($("#cropMessage"), "Crop ini opsional. Geser titik hanya jika hasil otomatis perlu diperbaiki.");
  await setCanvasImage(page.raw, page.corners);
  dialog.showModal();
}

canvas.addEventListener("pointerdown", (event) => {
  const point = canvasPoint(event);
  let best = -1;
  let distance = Infinity;
  state.corners.forEach((corner, index) => {
    const current = Math.hypot(corner.x - point.x, corner.y - point.y);
    if (current < distance) { best = index; distance = current; }
  });
  if (distance < canvas.width / 8) {
    state.dragging = best;
    canvas.setPointerCapture(event.pointerId);
  }
});

canvas.addEventListener("pointermove", (event) => {
  if (state.dragging < 0) return;
  const point = canvasPoint(event);
  state.corners[state.dragging] = {
    x: Math.max(0, Math.min(canvas.width, point.x)),
    y: Math.max(0, Math.min(canvas.height, point.y)),
  };
  drawCrop();
});

canvas.addEventListener("pointerup", () => { state.dragging = -1; });
canvas.addEventListener("pointercancel", () => { state.dragging = -1; });

documentInput.addEventListener("change", () => {
  const files = [...documentInput.files];
  documentInput.value = "";
  if (files.length) autoScanFiles(files);
});

$("#cancelCrop").addEventListener("click", () => {
  state.editingFile = null;
  state.editingIndex = -1;
});

dialog.addEventListener("close", () => {
  // Also recover when the user closes the dialog with the browser back/Escape action.
  if (state.editingFile) {
    state.editingFile = null;
    state.editingIndex = -1;
  }
});

$("#confirmCrop").addEventListener("click", async () => {
  if (!state.editingFile) return;
  const button = $("#confirmCrop");
  button.disabled = true;
  showMessage($("#cropMessage"), "");
  try {
    const corners = state.corners.map(({ x, y }) => [x / canvas.width, y / canvas.height]);
    const form = new FormData();
    form.append("file", state.editingFile);
    form.append("corners", JSON.stringify(corners));
    form.append("filter_mode", $("#filterMode").value);
    const response = await api("/api/process", { method: "POST", body: form });
    const blob = await response.blob();
    const oldPage = state.pages[state.editingIndex];
    URL.revokeObjectURL(oldPage.url);
    state.pages[state.editingIndex] = {
      ...oldPage,
      blob,
      url: URL.createObjectURL(blob),
      corners,
      strategy: "manual",
    };
    state.editingFile = null;
    state.editingIndex = -1;
    dialog.close();
    renderPages();
  } catch (error) {
    showMessage($("#cropMessage"), error.message, true);
  } finally {
    button.disabled = false;
  }
});

function movePage(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.pages.length) return;
  [state.pages[index], state.pages[target]] = [state.pages[target], state.pages[index]];
  renderPages();
}

function deletePage(index) {
  URL.revokeObjectURL(state.pages[index].url);
  state.pages.splice(index, 1);
  renderPages();
}

function openPagePreview(index) {
  const page = state.pages[index];
  if (!page) return;
  previewImage.src = page.url;
  previewImage.alt = `Preview hasil scan halaman ${index + 1}`;
  previewImage.classList.remove("zoomed");
  $("#previewTitle").textContent = `Preview halaman ${index + 1}`;
  $("#previewHint").textContent = "Ketuk gambar untuk memperbesar.";
  previewDialog.showModal();
}

function closePagePreview() {
  if (previewDialog.open) previewDialog.close();
}

function renderPages() {
  invalidatePreparedShare();
  const list = $("#pageList");
  list.innerHTML = "";
  $("#emptyPages").hidden = state.pages.length > 0;
  state.pages.forEach((page, index) => {
    const item = document.createElement("article");
    item.className = "page-item";
    item.innerHTML = `<img alt="Halaman ${index + 1}"><span class="page-number">${index + 1}</span><div class="page-actions"><button type="button" aria-label="Atur crop">Atur</button><button type="button" aria-label="Naik">←</button><button type="button" aria-label="Turun">→</button><button type="button" aria-label="Hapus">×</button></div>`;
    const image = item.querySelector("img");
    image.src = page.url;
    image.tabIndex = 0;
    image.setAttribute("role", "button");
    image.setAttribute("aria-label", `Lihat halaman ${index + 1} ukuran besar`);
    image.onclick = () => openPagePreview(index);
    image.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPagePreview(index);
      }
    };
    const buttons = item.querySelectorAll("button");
    buttons[0].onclick = () => openPageEditor(index);
    buttons[1].onclick = () => movePage(index, -1);
    buttons[2].onclick = () => movePage(index, 1);
    buttons[3].onclick = () => deletePage(index);
    list.appendChild(item);
  });
}

$("#closePreview").addEventListener("click", closePagePreview);

previewDialog.addEventListener("click", (event) => {
  if (event.target === previewDialog) closePagePreview();
});

previewDialog.addEventListener("close", () => {
  previewImage.classList.remove("zoomed");
  previewImage.removeAttribute("src");
});

previewImage.addEventListener("click", () => {
  const zoomed = previewImage.classList.toggle("zoomed");
  $("#previewHint").textContent = zoomed
    ? "Ketuk lagi untuk menampilkan seluruh halaman."
    : "Ketuk gambar untuk memperbesar.";
});

visitInput.addEventListener("change", () => {
  state.visitFiles = [...visitInput.files];
  $("#visitCount").textContent = state.visitFiles.length
    ? `${state.visitFiles.length} foto sedang dibaca otomatis.`
    : "Pastikan label “No Kunjungan” atau “No Resep” beserta angkanya terlihat.";
  if (state.visitFiles.length) readVisitNumber();
});

function validateVisitInput() {
  const input = $("#visitNumber");
  const valid = /^[A-Z0-9]{19}$/.test(input.value);
  input.setCustomValidity(valid ? "" : "Nomor kunjungan / resep harus tepat 19 karakter huruf dan angka.");
  input.setAttribute("aria-invalid", String(Boolean(input.value) && !valid));
  const counter = $("#visitLength");
  counter.textContent = `${input.value.length}/19 karakter — termasuk huruf dan angka.`;
  counter.classList.toggle("error", Boolean(input.value) && !valid);
  return valid;
}

function updateFilename() {
  const record = $("#recordNumber").value.trim().replace(/[^0-9]/g, "");
  const visit = $("#visitNumber").value.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  $("#recordNumber").value = record;
  $("#visitNumber").value = visit;
  const validVisit = validateVisitInput();
  $("#filenamePreview").textContent = record && validVisit ? `${record} ${visit}.pdf` : "—.pdf";
  invalidatePreparedShare();
  scheduleDuplicateCheck();
}

$("#recordNumber").addEventListener("input", updateFilename);
$("#visitNumber").addEventListener("input", updateFilename);

async function readVisitNumber() {
  const message = $("#ocrMessage");
  if (!state.visitFiles.length) return showMessage(message, "Tambahkan foto yang memuat No. Kunjungan atau No. Resep.", true);
  const button = $("#ocrButton");
  button.disabled = true;
  showBusy(true, "Membaca nomor kunjungan / resep…");
  try {
    const form = new FormData();
    state.visitFiles.forEach((file) => form.append("visit_images", file));
    const result = await (await api("/api/ocr-visit", { method: "POST", body: form })).json();
    $("#visitNumber").value = result.visit_number;
    updateFilename();
    const note = result.needs_review
      ? "Nomor harus tepat 19 karakter dan belum terbaca dengan yakin. Foto label dan nomornya lebih dekat atau isi manual."
      : `Nomor ${result.visit_number} berhasil dibaca. Periksa sebelum menyimpan.`;
    showMessage(message, note, result.needs_review);
    $("#visitCount").textContent = result.visit_number
      ? `Terbaca: ${result.visit_number}`
      : "Nomor belum ditemukan. Coba foto lebih dekat.";
  } catch (error) {
    showMessage(message, error.message, true);
  } finally {
    button.disabled = false;
    showBusy(false);
  }
}

$("#ocrButton").addEventListener("click", readVisitNumber);

function currentPdfData() {
  const record = $("#recordNumber").value.trim();
  const visit = $("#visitNumber").value.trim();
  if (!state.pages.length) {
    alert("Tambahkan minimal satu halaman PDF.");
    return null;
  }
  if (!record || !visit) {
    alert("Lengkapi nomor kartu dan nomor kunjungan / resep.");
    return null;
  }
  if (!validateVisitInput()) {
    $("#visitNumber").reportValidity();
    $("#visitNumber").focus();
    return null;
  }
  return {
    filename: `${record} ${visit}.pdf`,
    signature: `${record}|${visit}|${state.pages.map((page) => page.url).join("|")}`,
  };
}

function createPdfForm(filename) {
  const form = new FormData();
  state.pages.forEach((page, index) => form.append("pages", page.blob, `page-${index + 1}.jpg`));
  form.append("filename", filename);
  return form;
}

async function approveDuplicateIfNeeded() {
  const key = duplicateInputKey();
  let status = state.duplicateKey === key ? state.duplicateStatus : null;
  if (!status) status = await checkDuplicateNow();
  if (!status?.record_match || state.duplicateConfirmedKey === key) return true;

  const previous = status.latest;
  const when = sharedAtLabel(previous?.shared_at);
  const message = status.exact_match
    ? `Nomor ${status.record_number} dengan nomor kunjungan/resep ${status.visit_number} sudah pernah dibagikan${when ? ` pada ${when}` : ""}.\n\nTetap lanjut dan bagikan ulang?`
    : `Nomor ${status.record_number} sudah pernah dibagikan${previous?.visit_number ? ` dengan nomor ${previous.visit_number}` : ""}${when ? ` pada ${when}` : ""}. Nomor kunjungan/resep sekarang berbeda.\n\nTetap lanjut?`;
  if (!window.confirm(message)) return false;
  state.duplicateConfirmedKey = key;
  return true;
}

async function markSharedPdf(pdfData) {
  const form = new FormData();
  form.append("record_number", $("#recordNumber").value.trim());
  form.append("visit_number", $("#visitNumber").value.trim());
  form.append("filename", pdfData.filename);
  return (await api("/api/tracking/complete", { method: "POST", body: form })).json();
}

function invalidatePreparedShare() {
  state.preparedShareFile = null;
  state.preparedShareSignature = "";
  const button = $("#shareButton");
  if (button) button.textContent = "Siapkan PDF untuk dibagikan";
  const message = $("#shareMessage");
  if (message) showMessage(message, "");
}

$("#shareButton").addEventListener("click", async () => {
  const pdfData = currentPdfData();
  if (!pdfData) return;
  const button = $("#shareButton");
  const message = $("#shareMessage");

  // The second tap performs no network work, preserving Safari's transient
  // user activation and sharing the PDF as a real File rather than a URL.
  if (state.preparedShareFile && state.preparedShareSignature === pdfData.signature) {
    const files = [state.preparedShareFile];
    if (!navigator.canShare || !navigator.canShare({ files })) {
      return showMessage(message, "Browser ini tidak mendukung berbagi file PDF. Gunakan tombol unduh.", true);
    }
    try {
      // Send only the file. Safari turns `title` into a second text item in
      // several share targets; the filename is already embedded in File.name.
      await navigator.share({ files });
      state.preparedShareFile = null;
      state.preparedShareSignature = "";
      state.duplicateConfirmedKey = "";
      button.textContent = "Siapkan PDF untuk dibagikan";
      try {
        const tracked = await markSharedPdf(pdfData);
        await loadTracking();
        await checkDuplicateNow();
        const progressNote = tracked.newly_completed
          ? " Checklist bertambah satu."
          : " Nomor ini sudah ada, jadi hitungan tidak bertambah.";
        showMessage(message, `PDF sudah dikirim ke menu Share sebagai file.${progressNote}`);
      } catch (trackingError) {
        showMessage(message, `PDF sudah dibagikan, tetapi checklist gagal dicatat: ${trackingError.message}`, true);
      }
    } catch (error) {
      if (error.name !== "AbortError") showMessage(message, `Gagal membuka Share: ${error.message}`, true);
    }
    return;
  }

  if (!await approveDuplicateIfNeeded()) return;

  button.disabled = true;
  showBusy(true, "Menyiapkan file PDF…");
  try {
    const response = await api("/api/pdf", { method: "POST", body: createPdfForm(pdfData.filename) });
    const blob = await response.blob();
    const file = new File([blob], pdfData.filename, { type: "application/pdf", lastModified: Date.now() });
    if (!navigator.canShare || !navigator.canShare({ files: [file] })) {
      throw new Error("Safari ini tidak mendukung berbagi file PDF melalui website.");
    }
    state.preparedShareFile = file;
    state.preparedShareSignature = pdfData.signature;
    button.textContent = "Bagikan PDF sekarang";
    showMessage(message, `File ${file.name} sudah siap. Ketuk “Bagikan PDF sekarang”.`);
  } catch (error) {
    showMessage(message, error.message, true);
  } finally {
    button.disabled = false;
    showBusy(false);
  }
});

if (!navigator.share || !navigator.canShare) {
  $("#shareButton").hidden = true;
}

$("#resetButton").addEventListener("click", () => {
  const hasData = state.pages.length || state.visitFiles.length ||
    $("#recordNumber").value || $("#visitNumber").value;
  if (hasData && !window.confirm("Kosongkan semua scan dan mulai data baru? PDF yang sudah diunduh tidak akan terhapus.")) {
    return;
  }

  state.pages.forEach((page) => URL.revokeObjectURL(page.url));
  state.pages = [];
  state.visitFiles = [];
  state.editingFile = null;
  state.editingIndex = -1;
  state.recordConfidence = 0;
  state.image = null;
  state.corners = [];
  state.dragging = -1;
  state.duplicateConfirmedKey = "";
  window.clearTimeout(state.duplicateTimer);
  documentInput.value = "";
  visitInput.value = "";
  $("#recordNumber").value = "";
  $("#visitNumber").value = "";
  validateVisitInput();
  $("#filenamePreview").textContent = "—.pdf";
  $("#recordDetected").textContent = "Foto pertama harus memuat angka besar seperti 697. Nomor dikunci dari foto pertama agar tidak tertimpa halaman lain.";
  $("#visitCount").textContent = "Pastikan label “No Kunjungan” atau “No Resep” beserta angkanya terlihat.";
  showMessage($("#ocrMessage"), "");
  showMessage($("#cropMessage"), "");
  clearDuplicateWarning();
  invalidatePreparedShare();
  if (dialog.open) dialog.close();
  if (previewDialog.open) previewDialog.close();
  renderPages();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

$("#periodSelect").addEventListener("change", (event) => loadTracking(event.target.value));
$("#checklistSearch").addEventListener("input", renderChecklist);
$("#checklistFilter").addEventListener("change", renderChecklist);

$("#checklistList").addEventListener("click", async (event) => {
  const button = event.target.closest(".undo-record");
  if (!button) return;
  const record = button.dataset.record;
  const period = state.trackingSummary?.period;
  if (!record || !period || !window.confirm(`Batalkan tanda selesai untuk nomor ${record}?`)) return;
  button.disabled = true;
  try {
    const response = await api(`/api/tracking/record/${encodeURIComponent(record)}?period=${encodeURIComponent(period)}`, {
      method: "DELETE",
    });
    const result = await response.json();
    await loadTracking(period);
    await checkDuplicateNow();
    showMessage($("#trackingMessage"), result.removed
      ? `Tanda selesai nomor ${record} sudah dibatalkan.`
      : `Nomor ${record} memang belum ditandai selesai.`);
  } catch (error) {
    showMessage($("#trackingMessage"), error.message, true);
    button.disabled = false;
  }
});

$("#openPeriodDialog").addEventListener("click", () => {
  const summary = state.trackingSummary;
  const active = summary?.active_period || new Date().toISOString().slice(0, 7);
  const activeData = summary?.periods.find((item) => item.period === active);
  $("#newPeriod").value = nextPeriod(active);
  $("#newPeriodTarget").value = activeData?.target_count || 1000;
  showMessage($("#periodMessage"), "");
  periodDialog.showModal();
});

$("#closePeriodDialog").addEventListener("click", () => periodDialog.close());
periodDialog.addEventListener("click", (event) => {
  if (event.target === periodDialog) periodDialog.close();
});

$("#periodForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const period = $("#newPeriod").value;
  const target = Number($("#newPeriodTarget").value);
  if (!period || !Number.isInteger(target) || target < 1) {
    return showMessage($("#periodMessage"), "Isi bulan dan target dengan benar.", true);
  }
  const active = state.trackingSummary?.active_period;
  const prompt = period === active
    ? `Perbarui target periode ${periodLabel(period)} menjadi ${target}?`
    : `Aktifkan periode ${periodLabel(period)} dengan target ${target}? Riwayat ${periodLabel(active)} tetap tersimpan sebagai arsip.`;
  if (!window.confirm(prompt)) return;

  const submit = event.submitter;
  submit.disabled = true;
  try {
    const form = new FormData();
    form.append("period", period);
    form.append("target_count", String(target));
    await api("/api/tracking/period", { method: "POST", body: form });
    periodDialog.close();
    await loadTracking();
    await checkDuplicateNow();
    showMessage($("#trackingMessage"), `Periode aktif sekarang ${periodLabel(period)}. Arsip lama tidak dihapus.`);
  } catch (error) {
    showMessage($("#periodMessage"), error.message, true);
  } finally {
    submit.disabled = false;
  }
});

checkHealth();
loadTracking();
