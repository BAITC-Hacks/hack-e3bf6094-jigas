"""Run the batch pipeline and publish a validated static viewer release."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import uuid

from .validation import load, sanity_check
from .graph import build_graph, basic_features, enrich_features, enrich_observed_features
from .scoring import assign_roles, compute_priority
from .clusters import LOUVAIN_RESOLUTION, LOUVAIN_SEED, cluster_nodes, summarize_clusters, summarize_structure
from .report import build_report
from .exports import write_outputs
from .html import stage_viewer


INPUT_FILES = ("nodes.parquet", "edges.parquet", "transactions.parquet")
OUTPUT_FILES = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "report.json", "report.html", "index.html")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pipeline(data_dir: Path, out_dir: Path) -> dict:
    """Read raw Parquet once, calculate every artifact, then publish success last."""
    data_dir = Path(data_dir).resolve()
    out_dir = Path(out_dir).resolve()
    if data_dir == out_dir:
        raise ValueError("input and output directories must be different")

    started = time.perf_counter()
    timings = {}
    phase = "load"
    stage = None
    try:
        checkpoint = time.perf_counter()
        edges, nodes, tx = load(data_dir)
        sanity_check(edges, nodes, tx)
        timings["load_validate_seconds"] = time.perf_counter() - checkpoint

        phase = "graph_features"
        checkpoint = time.perf_counter()
        graph = build_graph(edges, nodes)
        features = enrich_features(graph, basic_features(graph, nodes), tx)
        features, daily_profiles_by_gid = enrich_observed_features(graph, features, tx)
        timings["graph_features_seconds"] = time.perf_counter() - checkpoint

        phase = "roles_priority"
        checkpoint = time.perf_counter()
        features, role_parameters = assign_roles(features)
        features, priority_parameters = compute_priority(features)
        timings["roles_priority_seconds"] = time.perf_counter() - checkpoint

        phase = "clusters"
        checkpoint = time.perf_counter()
        features = features.copy()
        features["cluster_id"] = features["gid"].map(cluster_nodes(graph))
        clusters = summarize_clusters(graph, features)
        features, structure = summarize_structure(graph, features)
        timings["clusters_seconds"] = time.perf_counter() - checkpoint

        phase = "source_hashes"
        source_hashes = {name: _sha256(data_dir / name) for name in INPUT_FILES}
        metadata = {
            "period_start": tx["date"].min().date().isoformat(),
            "period_end": tx["date"].max().date().isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "transaction_count": len(tx),
            "n_seed": int(nodes["is_seed"].sum()),
            "sum_tiyn": sum(int(amount) for amount in edges["sum_tiyn"]),
            "sha256_files": source_hashes,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "limitations": (
                "Наблюдаются переводы только в переданной выборке: четыре шага от seed, "
                "операции от 5 000 KZT и июль 2026. Отсутствие исходящих на границе "
                "и неполный вход seed не доказывают конечное назначение средств."
            ),
        }
        parameters = {
            "rule_version": "1.0",
            "role_parameters": role_parameters,
            "priority_parameters": priority_parameters,
            "weights": priority_parameters["weights"],
            "louvain": {"resolution": LOUVAIN_RESOLUTION, "seed": LOUVAIN_SEED},
            "observed_features": {
                "n_seed_payers": "number of distinct direct graph predecessors with is_seed=true",
                "sync_payers_max": "maximum distinct src per (dst, date); 0 when no incoming rows are observed",
                "fanout_burst_max": "maximum distinct dst per (src, date); 0 when no outgoing rows are observed",
                "daily_profiles_by_gid": {
                    "dates": "observed transaction days only, ascending YYYY-MM-DD",
                    "amount_unit": "integer tiyn",
                    "counts": "all transaction rows, including duplicates",
                    "counterparties": "unique IDs per node and date",
                    "missing_direction": 0,
                },
            },
        }

        phase = "report"
        checkpoint = time.perf_counter()
        report = build_report(
            features,
            edges,
            clusters,
            metadata,
            parameters,
            daily_profiles_by_gid=daily_profiles_by_gid,
            structure=structure,
        )
        timings["report_seconds"] = time.perf_counter() - checkpoint

        phase = "staged_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".staging.", dir=out_dir))
        checkpoint = time.perf_counter()
        write_outputs(report, stage)
        (stage / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        asset_files = stage_viewer(stage)
        for name in (*OUTPUT_FILES, *asset_files):
            if not (stage / name).is_file() or (stage / name).stat().st_size == 0:
                raise ValueError(f"required output is missing or empty: {name}")
        timings["staged_outputs_seconds"] = time.perf_counter() - checkpoint
        timings["total_before_publish_seconds"] = time.perf_counter() - started

        phase = "validation_manifest"
        validation = {
            "status": "success",
            "schema_version": report["schema_version"],
            "dataset": metadata,
            "parameters": parameters,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "pandas": version("pandas"),
                "numpy": version("numpy"),
                "networkx": version("networkx"),
                "pyarrow": version("pyarrow"),
            },
            "timings": timings,
            "checks": {
                "node_count": len(report["nodes"]),
                "edge_count": len(report["edges"]),
                "cluster_count": len(report["clusters"]),
                "top_count": len(report["top_nodes"]),
                "viewer_asset_count": len(asset_files),
                "sum_tiyn_internal": clusters.attrs["sum_tiyn_internal_total"],
                "sum_tiyn_intercluster": clusters.attrs["sum_tiyn_intercluster_total"],
                "sum_tiyn_total": clusters.attrs["sum_tiyn_total"],
            },
            "output_sha256": {name: _sha256(stage / name) for name in (*OUTPUT_FILES, *asset_files)},
        }
        (stage / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )

        phase = "publish"
        previous = out_dir / "validation.json"
        if previous.exists():
            previous.replace(out_dir / f"validation.previous.{uuid.uuid4().hex}.json")
        previous_assets = out_dir / "assets"
        if previous_assets.exists():
            previous_assets.replace(out_dir / f"assets.previous.{uuid.uuid4().hex}")
        for name in OUTPUT_FILES:
            (stage / name).replace(out_dir / name)
        (stage / "assets").replace(out_dir / "assets")
        (stage / "validation.json").replace(out_dir / "validation.json")
        stage.rmdir()
        print(f"Validated release: {out_dir} ({len(report['nodes'])} nodes, {len(report['clusters'])} clusters)")
        return validation
    except Exception as exc:
        if stage is not None and stage.parent == out_dir and stage.name.startswith(".staging."):
            shutil.rmtree(stage, ignore_errors=True)
        raise RuntimeError(f"{phase}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline HackAlem transaction-network analysis")
    parser.add_argument("--data", default="./data", help="directory with the three Parquet files")
    parser.add_argument("--out", default="./out", help="directory for the validated release")
    args = parser.parse_args(argv)
    try:
        run_pipeline(Path(args.data), Path(args.out))
    except (RuntimeError, ValueError) as exc:
        print(f"Pipeline failed during {exc}", file=sys.stderr)
        return 1
    return 0
