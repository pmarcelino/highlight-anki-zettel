# Vendored PDF.js

`pdfjs-dist` **4.10.38**, copied verbatim from the npm tarball
(`https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-4.10.38.tgz`) — no build
step, the extension is loaded unpacked.

| here                 | tarball path                |
| -------------------- | --------------------------- |
| `pdf.min.mjs`        | `build/pdf.min.mjs`         |
| `pdf.worker.min.mjs` | `build/pdf.worker.min.mjs`  |
| `pdf_viewer.css`     | `web/pdf_viewer.css`        |
| `standard_fonts/`    | `standard_fonts/`           |
| `cmaps/`             | `cmaps/`                    |
| `LICENSE`            | `LICENSE` (Apache-2.0)      |

`pdf_viewer.css` is the whole viewer stylesheet: only its `.pdfViewer`,
`.page`, `.canvasWrapper` and `.textLayer` rules ever match, since
`viewer.js` builds nothing else. It is the text-layer CSS — the dist does not
ship a separate `text_layer.css`.

To bump: download the new tarball, copy the same six paths, update the
version above. 4.x is the last line that needs no `wasm/` or `iccs/`
side-files; 5.x+ also renames the `--scale-factor` CSS variable, so check
`viewer.js` when crossing that boundary.

sha256:

```
27fc2a057a00f92a4334ad06e17dbd7259912954e9fb7f76400bcca5fd190a9c  pdf.min.mjs
1baa1844c89c80a5b2797c916e75ab29254be46d8e9cb53cb6364d7aad84be36  pdf.worker.min.mjs
f684e5c55f3baa4516519e8e0d4543726ab2c4223a0ded8c71ed9d5bf3782e7b  pdf_viewer.css
```
