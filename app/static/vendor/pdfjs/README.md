# Local PDF.js preview assets

Vendored from Mozilla's `pdfjs-dist` npm package, version **6.4.299**.
`VERSION.json` records the exact tarball URL and verified SHA-512 integrity.
The package's Apache-2.0 license is included in `LICENSE`; supporting font and
decoder licenses remain alongside those assets.

Included: legacy minified display/worker modules, character maps, standard fonts,
WebAssembly image decoders, and ICC resources. Source maps, the full PDF viewer,
and examples are not required by this app.

The app loads these assets from its own server. Uploaded PDFs are rendered into
canvas previews in the browser; final merging and page rotation use pypdf on the
server. PDF.js evaluation is disabled with `isEvalSupported: false`.

Upstream usage documentation: https://mozilla.github.io/pdf.js/examples/
