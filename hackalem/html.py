"""Embed the report and local assets into offline HTML."""

import json
from pathlib import Path

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
