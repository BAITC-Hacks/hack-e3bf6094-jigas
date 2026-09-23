"""Stage a built viewer; retain the legacy standalone renderer as a facade."""

import json
from pathlib import Path
import shutil


def stage_viewer(stage: Path) -> tuple[str, ...]:
    """Copy the prebuilt React viewer into a release staging directory."""
    root = Path(__file__).resolve().parents[1]
    build = root / "frontend" / "dist"
    page = build / "report.html"
    index = build / "index.html"
    asset_dir = build / "assets"
    if not page.is_file() or page.is_symlink() or not index.is_file() or index.is_symlink() or not asset_dir.is_dir() or asset_dir.is_symlink():
        raise ValueError("frontend build is missing; run npm --prefix frontend run build")
    if page.stat().st_size == 0 or index.read_bytes() != page.read_bytes():
        raise ValueError("frontend build index.html and report.html must be identical and nonempty")

    entries = sorted(asset_dir.rglob("*"))
    files = [path for path in entries if path.is_file()]
    if not files or any(path.is_symlink() for path in entries) or any(path.stat().st_size == 0 for path in files):
        raise ValueError("frontend build assets are missing or invalid")
    if (stage / "assets").exists():
        raise ValueError("release staging assets already exist")

    shutil.copy2(page, stage / "report.html")
    shutil.copy2(index, stage / "index.html")
    shutil.copytree(asset_dir, stage / "assets")
    license_path = root / "assets" / "vis-network.LICENSE.txt"
    if not license_path.is_file():
        raise ValueError("vis-network license is missing")
    shutil.copy2(license_path, stage / "assets" / license_path.name)
    cytoscape_license = root / "assets" / "cytoscape.LICENSE.txt"
    if not cytoscape_license.is_file():
        raise ValueError("Cytoscape.js license is missing")
    shutil.copy2(cytoscape_license, stage / "assets" / cytoscape_license.name)
    return tuple(str(path.relative_to(stage)).replace("\\", "/") for path in sorted((stage / "assets").rglob("*")) if path.is_file())

def render_report(report: dict, out_path: Path) -> None:
    """Embed the report and local assets into a standalone offline HTML file."""
    root = Path(__file__).resolve().parents[1]
    template = (root / "report_template.html").read_text(encoding="utf-8")
    assets = {
        "__VIS_NETWORK_JS__": (root / "assets" / "vis-network.min.js").read_text(encoding="utf-8"),
        "__VIS_NETWORK_CSS__": (root / "assets" / "vis-network.min.css").read_text(encoding="utf-8"),
    }
    for marker in ("__REPORT_DATA__", *assets):
        if template.count(marker) != 1:
            raise ValueError(f"HTML template must contain exactly one {marker} marker")
    if "</script" in assets["__VIS_NETWORK_JS__"].lower():
        raise ValueError("embedded JavaScript contains a closing script tag")
    if "</style" in assets["__VIS_NETWORK_CSS__"].lower():
        raise ValueError("embedded CSS contains a closing style tag")

    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    payload = (payload.replace("&", "\\u0026")
                      .replace("<", "\\u003c")
                      .replace(">", "\\u003e")
                      .replace("\u2028", "\\u2028")
                      .replace("\u2029", "\\u2029"))
    html = template.replace("__REPORT_DATA__", payload)
    for marker, content in assets.items():
        html = html.replace(marker, content)
    if any(marker in html for marker in ("__REPORT_DATA__", *assets)):
        raise ValueError("HTML output contains an unexpanded template marker")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
