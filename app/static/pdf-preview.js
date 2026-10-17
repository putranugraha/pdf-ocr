// PDF.js is bundled locally: previews do not send documents to another service.
let libraryPromise;

function pdfLibrary() {
  if (!libraryPromise) {
    libraryPromise = import("/vendor/pdfjs/pdf.min.mjs").then((library) => {
      library.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";
      return library;
    }).catch((error) => {
      libraryPromise = null;
      throw error;
    });
  }
  return libraryPromise;
}

const dialog = document.querySelector("#mergePreviewDialog");
document.querySelector("#closeMergePreview").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => {
  const canvas = document.querySelector("#mergePreviewCanvas");
  canvas.width = canvas.height = 0;
});

function pageRanges(pages) {
  const ranges = [];
  for (let index = 0; index < pages.length; index += 1) {
    const start = pages[index];
    let end = start;
    while (pages[index + 1] === end + 1) end = pages[++index];
    ranges.push(start === end ? `${start}` : `${start}–${end}`);
  }
  return ranges.join(", ");
}

export function createPdfPreview(root, { allowRotation = false, title }) {
  const controls = Object.fromEntries(
    Array.from(root.querySelectorAll("[data-action]")).map((element) => [element.dataset.action, element])
  );
  const status = root.querySelector(".pdf-preview-message");
  const count = root.querySelector('[data-role="count"]');
  const rotationSummary = root.querySelector('[data-role="rotations"]');
  let documentPdf = null;
  let loadingTask = null;
  let renderTask = null;
  let generation = 0;
  let renderGeneration = 0;
  let pageNumber = 1;
  let filename = "";
  let rendering = false;
  let ready = false;
  const rotatedPages = new Set();

  function rotationPages() {
    return [...rotatedPages].sort((a, b) => a - b);
  }

  function updateControls() {
    const unavailable = !documentPdf || rendering;
    controls.previous.disabled = unavailable || pageNumber <= 1;
    controls.next.disabled = unavailable || pageNumber >= documentPdf.numPages;
    controls.page.disabled = unavailable;
    controls.page.value = String(pageNumber);
    controls.zoom.disabled = controls.enlarge.disabled = !ready || rendering;
    if (allowRotation) {
      controls.rotate.disabled = unavailable || !ready;
      controls.rotate.setAttribute("aria-pressed", String(rotatedPages.has(pageNumber)));
      controls.rotate.textContent = rotatedPages.has(pageNumber)
        ? "Kembalikan halaman ini" : "Putar halaman ini 180°";
      controls["reset-rotation"].disabled = unavailable || !rotatedPages.size;
      rotationSummary.textContent = rotatedPages.size
        ? `Diputar 180° pada hasil: halaman ${pageRanges(rotationPages())}.`
        : "Belum ada halaman yang diputar.";
    }
  }

  function reset() {
    generation += 1;
    renderGeneration += 1;
    renderTask?.cancel();
    renderTask = null;
    // Destroy also terminates the old worker when a file is replaced mid-load.
    if (loadingTask) void loadingTask.destroy().catch(() => {});
    loadingTask = null;
    documentPdf = null;
    rotatedPages.clear();
    filename = "";
    pageNumber = 1;
    rendering = ready = false;
    controls.page.replaceChildren();
    count.textContent = status.textContent = "";
    status.classList.remove("error");
    const canvas = controls.enlarge.querySelector("canvas");
    canvas.width = canvas.height = 0;
    controls.enlarge.hidden = true;
    root.hidden = true;
    if (dialog.open) dialog.close();
    updateControls();
  }

  async function renderPage() {
    if (!documentPdf) return;
    const currentGeneration = generation;
    const currentRender = ++renderGeneration;
    renderTask?.cancel();
    rendering = true;
    ready = false;
    controls.enlarge.hidden = true;
    status.classList.remove("error");
    status.textContent = `Menampilkan halaman ${pageNumber}…`;
    updateControls();
    try {
      const page = await documentPdf.getPage(pageNumber);
      if (currentGeneration !== generation || currentRender !== renderGeneration) return;
      const rotation = (page.rotate + (rotatedPages.has(pageNumber) ? 180 : 0)) % 360;
      const original = page.getViewport({ scale: 1, rotation });
      // Bound both dimensions and total pixels for unusually large pages.
      const scale = Math.min(1600 / original.width, 2200 / original.height,
        Math.sqrt(3500000 / (original.width * original.height)));
      const viewport = page.getViewport({ scale, rotation });
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.ceil(viewport.width));
      canvas.height = Math.max(1, Math.ceil(viewport.height));
      canvas.setAttribute("aria-label", `${title}, halaman ${pageNumber}`);
      const task = page.render({ canvasContext: canvas.getContext("2d"), viewport });
      renderTask = task;
      await task.promise;
      if (currentGeneration !== generation || currentRender !== renderGeneration) return;
      controls.enlarge.replaceChildren(canvas);
      controls.enlarge.hidden = false;
      ready = true;
      status.textContent = `${title} · Halaman ${pageNumber} dari ${documentPdf.numPages}`
        + (rotatedPages.has(pageNumber) ? " · Diputar 180°" : " · Orientasi asli");
      page.cleanup();
    } catch (error) {
      if (currentGeneration !== generation || currentRender !== renderGeneration) return;
      if (error.name !== "RenderingCancelledException") {
        status.textContent = "Halaman ini gagal ditampilkan. Coba pilih halaman lain atau pilih ulang PDF.";
        status.classList.add("error");
      }
    } finally {
      if (currentGeneration === generation && currentRender === renderGeneration) {
        rendering = false;
        renderTask = null;
        updateControls();
      }
    }
  }

  async function load(file) {
    reset();
    if (!file) return;
    const currentGeneration = generation;
    filename = file.name;
    root.hidden = false;
    status.textContent = "Membuka pratinjau PDF…";
    try {
      if (file.size > 100 * 1024 * 1024) throw new Error("Ukuran maksimal setiap PDF adalah 100 MB.");
      const library = await pdfLibrary();
      if (currentGeneration !== generation) return;
      const data = await file.arrayBuffer();
      if (currentGeneration !== generation) return;
      loadingTask = library.getDocument({
        data, isEvalSupported: false,
        cMapUrl: "/vendor/pdfjs/cmaps/", cMapPacked: true,
        standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
        wasmUrl: "/vendor/pdfjs/wasm/",
        iccUrl: "/vendor/pdfjs/iccs/",
      });
      const loadedDocument = await loadingTask.promise;
      if (currentGeneration !== generation) return;
      documentPdf = loadedDocument;
      if (documentPdf.numPages > 500) throw new Error("Jumlah hasil gabungan maksimal 500 halaman.");
      for (let number = 1; number <= documentPdf.numPages; number += 1) {
        controls.page.add(new Option(String(number), String(number)));
      }
      count.textContent = `dari ${documentPdf.numPages}`;
      await renderPage();
    } catch (error) {
      if (currentGeneration !== generation) return;
      documentPdf = null;
      if (loadingTask) void loadingTask.destroy().catch(() => {});
      loadingTask = null;
      status.textContent = error.name === "PasswordException"
        ? "PDF memakai kata sandi. Gunakan salinan PDF tanpa kata sandi."
        : file.size > 100 * 1024 * 1024 || error.message.includes("500 halaman")
          ? error.message
          : "Pratinjau tidak tersedia. Pastikan file PDF valid, lalu pilih ulang.";
      status.classList.add("error");
      updateControls();
    }
  }

  controls.previous.addEventListener("click", () => { pageNumber -= 1; void renderPage(); });
  controls.next.addEventListener("click", () => { pageNumber += 1; void renderPage(); });
  controls.page.addEventListener("change", () => { pageNumber = Number(controls.page.value); void renderPage(); });
  if (allowRotation) {
    controls.rotate.addEventListener("click", () => {
      if (rotatedPages.has(pageNumber)) rotatedPages.delete(pageNumber);
      else rotatedPages.add(pageNumber);
      void renderPage();
    });
    controls["reset-rotation"].addEventListener("click", () => {
      rotatedPages.clear();
      void renderPage();
    });
  }
  function enlarge() {
    if (!ready || rendering) return;
    document.querySelector("#mergePreviewTitle").textContent = `${title} · Halaman ${pageNumber}`;
    document.querySelector("#mergePreviewDetail").textContent = filename
      + (rotatedPages.has(pageNumber) ? " · Diputar 180°" : "");
    const source = controls.enlarge.querySelector("canvas");
    const target = document.querySelector("#mergePreviewCanvas");
    target.width = source.width;
    target.height = source.height;
    target.getContext("2d").drawImage(source, 0, 0);
    dialog.showModal();
  }
  controls.zoom.addEventListener("click", enlarge);
  controls.enlarge.addEventListener("click", enlarge);
  reset();
  return { load, reset, rotationPages };
}
