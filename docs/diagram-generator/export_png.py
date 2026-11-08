#!/usr/bin/env python3
"""Render docs/diagrams/src-class-diagram.excalidraw to the PNG a reader trusts.

Usage: python3 docs/diagram-generator/export_png.py

Needs node (to bundle @excalidraw/excalidraw and minify it) and a Playwright chromium, both fetched
into a cache directory outside the repository on first run and reused after that. Nothing is written
into the repo except the PNG itself. The page side of the export lives in EXPORT_BUNDLE_JS below,
which is what makes this reproducible rather than a bundle that happened to exist in /tmp once.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

EXCALIDRAW_VERSION = "0.17.3"
ESBUILD_VERSION = "0.24.0"

# docs/diagram-generator/export_png.py -> <repo>/docs/diagram-generator -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIAGRAM = os.path.join(REPO_ROOT, "docs", "diagrams", "src-class-diagram.excalidraw")
PNG = os.path.join(REPO_ROOT, "docs", "diagrams", "src-class-diagram.png")

EXPORT_BUNDLE_JS = """
import { exportToCanvas } from "@excalidraw/excalidraw";
window.__KMT_EXPORT_READY__ = true;
window.__kmtExport = async () => {
  const data = window.__DIAGRAM__;
  const els = data.elements.filter((e) => !e.isDeleted);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const e of els) {
    minX = Math.min(minX, e.x); minY = Math.min(minY, e.y);
    maxX = Math.max(maxX, e.x + (e.width || 0)); maxY = Math.max(maxY, e.y + (e.height || 0));
  }
  const w = Math.round(maxX - minX) + 20, h = Math.round(maxY - minY) + 20;
  const appState = Object.assign({}, data.appState, {
    exportScale: 1, exportBackground: true, exportEmbedScene: false,
    viewBackgroundColor: "#ffffff", scrollX: -minX + 10, scrollY: -minY + 10,
  });
  const canvas = await exportToCanvas({
    elements: els, appState, files: data.files || {},
    exportType: "image/png", exportPadding: 10, width: w, height: h, mimeType: "image/png",
  });
  return canvas.toDataURL("image/png");
};
"""

PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#fff}</style></head>
<body><script>window.process={env:{NODE_ENV:"production"},version:"",browser:true};window.global=window;</script><script src="data.js"></script><script src="bundle.js"></script></body></html>
"""


def cache_dir():
    root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(root, "kubementat", f"excalidraw-export-{EXCALIDRAW_VERSION}")


def bundle(work):
    """build bundle.js in the cache directory; returns its path."""
    out = os.path.join(work, "bundle.js")
    if os.path.exists(out):
        return out
    for tool, need in (("npm", "node/npm"), ("node", "node")):
        if not shutil.which(tool):
            raise SystemExit(f"export_png: {need} is not on PATH, so the export cannot be rebuilt")
    print(f"export_png: building the excalidraw exporter in {work} (one time)")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "package.json"), "w") as f:
        json.dump({"name": "kmt-export", "private": True, "version": "1.0.0"}, f)
    subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error",
                    f"@excalidraw/excalidraw@{EXCALIDRAW_VERSION}", f"esbuild@{ESBUILD_VERSION}"],
                   cwd=work, check=True, timeout=600)
    with open(os.path.join(work, "entry.js"), "w") as f:
        f.write(EXPORT_BUNDLE_JS)
    subprocess.run([os.path.join(work, "node_modules", ".bin", "esbuild"), "entry.js", "--bundle",
                    "--format=iife", "--minify", "--define:process.env.NODE_ENV=\"production\"",
                    "--loader:.css=text", "--outfile=bundle.js"], cwd=work, check=True, timeout=600)
    return out


def render(bundle_path, diagram_json, out_png):
    work = tempfile.mkdtemp(prefix="kmt-render-")
    try:
        shutil.copy(bundle_path, os.path.join(work, "bundle.js"))
        with open(os.path.join(work, "data.js"), "w") as f:
            f.write(f"window.__DIAGRAM__={diagram_json};")
        with open(os.path.join(work, "page.html"), "w") as f:
            f.write(PAGE_HTML)
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise SystemExit(f"export_png: cannot drive a headless browser ({e}); "
                         f"the .excalidraw is current, the PNG is not")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto("file://" + os.path.join(work, "page.html"), wait_until="load")
            if not page.wait_for_function("window.__KMT_EXPORT_READY__ === true", timeout=120000):
                raise SystemExit("export_png: the export bundle never loaded")
            data_url = page.evaluate("window.__kmtExport()")
            png = base64.b64decode(data_url.split(",", 1)[1])
        finally:
            browser.close()
    if not _has_ink(png):
        raise SystemExit("export_png: the render came back blank — refusing to overwrite the PNG "
                         "with one a reader would trust and that says nothing")
    with open(out_png, "wb") as f:
        f.write(png)
    return len(png)


def _has_ink(png):
    """A blank render is the failure that has bitten this file before, so count colours before writing."""
    try:
        from PIL import Image
        import io
        cols = Image.open(io.BytesIO(png)).convert("RGB").getcolors(maxcolors=1 << 24)
        return cols is not None and len(cols) > 1
    except Exception:
        return True


if __name__ == "__main__":
    if not os.path.exists(DIAGRAM):
        raise SystemExit(f"export_png: no diagram at {DIAGRAM}")
    with open(DIAGRAM, encoding="utf-8") as f:
        doc = json.load(f)
    n = render(bundle(cache_dir()), json.dumps(doc, separators=(",", ":")), PNG)
    print(f"export_png: {PNG} ({n:,} bytes)")
