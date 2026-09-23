"""Serialize validated graph analysis into one strict JSON object."""

import json
import numpy as np
import pandas as pd
from .clusters import _format_kzt

def _exact_int(value, label: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{label} must be an exact integer")
    return int(value)


def _json_safe(value):
    """Convert pandas/numpy values to strict JSON values; unknowns become null."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        timestamp = pd.Timestamp(value)
        return None if pd.isna(timestamp) else timestamp.date().isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def build_report(
    df: pd.DataFrame,
    edges: pd.DataFrame,
    clusters: pd.DataFrame,
    metadata: dict,
    parameters: dict,
) -> dict:
    """Build the single strict-JSON report object consumed by the HTML and CSVs."""
    node_columns = [
        "gid", "role", "role_score", "cluster_id", "priority_score", "evidence",
        "depth", "is_seed", "in_deg", "out_deg", "in_tiyn", "out_tiyn",
        "in_tx", "out_tx", "pass_through", "boundary", "isolated",
        "seed_reach_count", "betweenness", "last_in", "days_after_last_in",
        "matched_roles", "score_components", "why",
    ]
    missing_nodes = [column for column in node_columns if column not in df.columns]
    if missing_nodes:
        raise ValueError(f"report nodes are missing columns: {', '.join(missing_nodes)}")
    if not isinstance(metadata, dict) or not isinstance(parameters, dict):
        raise ValueError("report metadata and parameters must be dictionaries")
    required_metadata = {
        "period_start", "period_end", "node_count", "edge_count",
        "transaction_count", "sum_tiyn", "sha256_files",
    }
    missing_metadata = required_metadata - set(metadata)
    if missing_metadata:
        raise ValueError(f"report metadata is missing keys: {', '.join(sorted(missing_metadata))}")
    for key in ("period_start", "period_end"):
        value = metadata[key]
        parsed = pd.to_datetime(value, errors="coerce") if isinstance(value, str) else pd.NaT
        if pd.isna(parsed) or parsed.strftime("%Y-%m-%d") != value:
            raise ValueError(f"dataset.{key} must be a YYYY-MM-DD date")
    for key in ("node_count", "edge_count", "transaction_count", "sum_tiyn"):
        amount = _exact_int(metadata[key], f"dataset.{key}")
        if amount < 0:
            raise ValueError(f"dataset.{key} cannot be negative")
    expected_hashes = {"nodes.parquet", "edges.parquet", "transactions.parquet"}
    hashes = metadata["sha256_files"]
    if not isinstance(hashes, dict) or set(hashes) != expected_hashes:
        raise ValueError("dataset.sha256_files must contain the three input parquet filenames")
    for filename, digest in hashes.items():
        if (
            not isinstance(filename, str)
            or "/" in filename
            or "\\" in filename
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in digest)
        ):
            raise ValueError("dataset.sha256_files must map filenames without paths to SHA-256 hex digests")
    if df["gid"].isna().any() or df["gid"].duplicated().any():
        raise ValueError("report requires one row per exact gid")

    node_records = []
    for row in df.sort_values("gid", kind="mergesort").itertuples(index=False):
        values = row._asdict()
        gid = _exact_int(values["gid"], "nodes.gid")
        cluster_id = _exact_int(values["cluster_id"], f"nodes[{gid}].cluster_id")
        warnings = []
        if bool(values["boundary"]):
            warnings.append("boundary")
        if bool(values["is_seed"]):
            warnings.append("seed_incomplete_in")
        if bool(values["isolated"]):
            warnings.append("isolated")
        if (
            _exact_int(values["in_tiyn"], f"nodes[{gid}].in_tiyn") > 0
            and _exact_int(values["out_deg"], f"nodes[{gid}].out_deg") == 0
            and pd.notna(values["days_after_last_in"])
            and _exact_int(values["days_after_last_in"], f"nodes[{gid}].days_after_last_in") < 2
        ):
            warnings.append("short_followup")

        node = {
            column: _json_safe(values[column])
            for column in node_columns
            if column not in ("gid", "why")
        }
        node["gid"] = str(gid)
        node["cluster_id"] = cluster_id
        node["warnings"] = warnings
        for column in ("role_score", "priority_score"):
            score = node[column]
            if score is None or not np.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"nodes[{gid}].{column} must be finite and within [0, 1]")
        for column in ("in_tiyn", "out_tiyn"):
            amount = _exact_int(values[column], f"nodes[{gid}].{column}")
            if abs(amount) > 2**53 - 1:
                raise ValueError(f"nodes[{gid}].{column} exceeds the safe JSON integer range")
            node[column] = amount
        node_records.append(node)

    edge_columns = ["src", "dst", "sum_tiyn", "n_tx", "depth"]
    missing_edges = [column for column in edge_columns if column not in edges.columns]
    if missing_edges:
        raise ValueError(f"report edges are missing columns: {', '.join(missing_edges)}")
    edge_records = []
    sorted_edges = edges.sort_values(["src", "dst"], kind="mergesort")[edge_columns]
    for row in sorted_edges.itertuples(index=False, name=None):
        src = _exact_int(row[0], "edges.src")
        dst = _exact_int(row[1], "edges.dst")
        sum_tiyn = _exact_int(row[2], f"edge {src}->{dst}.sum_tiyn")
        n_tx = _exact_int(row[3], f"edge {src}->{dst}.n_tx")
        depth = _exact_int(row[4], f"edge {src}->{dst}.depth")
        if abs(sum_tiyn) > 2**53 - 1:
            raise ValueError(f"edge {src}->{dst}.sum_tiyn exceeds the safe JSON integer range")
        edge_records.append({
            "src": str(src), "dst": str(dst), "sum_tiyn": sum_tiyn,
            "n_tx": n_tx, "depth": depth,
        })

    cluster_columns = [
        "cluster_id", "n_nodes", "n_seed", "sum_tiyn_internal",
        "sum_kzt_internal", "top_gids", "hypothesis",
    ]
    missing_clusters = [column for column in cluster_columns if column not in clusters.columns]
    if missing_clusters:
        raise ValueError(f"report clusters are missing columns: {', '.join(missing_clusters)}")
    cluster_records = []
    for row in clusters.sort_values("cluster_id", kind="mergesort").itertuples(index=False):
        values = row._asdict()
        cluster_id = _exact_int(values["cluster_id"], "clusters.cluster_id")
        amount = _exact_int(values["sum_tiyn_internal"], f"clusters[{cluster_id}].sum_tiyn_internal")
        if abs(amount) > 2**53 - 1:
            raise ValueError(f"clusters[{cluster_id}].sum_tiyn_internal exceeds the safe JSON integer range")
        top_gids = [_exact_int(gid, f"clusters[{cluster_id}].top_gids") for gid in values["top_gids"]]
        sum_kzt_internal = str(values["sum_kzt_internal"])
        if sum_kzt_internal != _format_kzt(amount):
            raise ValueError(f"clusters[{cluster_id}].sum_kzt_internal does not match exact tiyn")
        record = {
            "cluster_id": cluster_id,
            "n_nodes": _exact_int(values["n_nodes"], f"clusters[{cluster_id}].n_nodes"),
            "n_seed": _exact_int(values["n_seed"], f"clusters[{cluster_id}].n_seed"),
            "sum_tiyn_internal": amount,
            "sum_kzt_internal": sum_kzt_internal,
            "top_gids": [str(gid) for gid in top_gids],
            "hypothesis": str(values["hypothesis"]),
        }
        if "n_edges_internal" in values:
            record["n_edges_internal"] = _exact_int(
                values["n_edges_internal"], f"clusters[{cluster_id}].n_edges_internal"
            )
        cluster_records.append(record)

    nodes_by_gid = {int(node["gid"]): node for node in node_records}
    cluster_by_gid = {gid: int(node["cluster_id"]) for gid, node in nodes_by_gid.items()}
    summary_cluster_ids = {cluster["cluster_id"] for cluster in cluster_records}
    if set(cluster_by_gid.values()) != summary_cluster_ids:
        raise ValueError("report cluster summaries must cover exactly the assigned clusters")
    for cluster in cluster_records:
        cluster_id = cluster["cluster_id"]
        members = [node for node in node_records if node["cluster_id"] == cluster_id]
        seed_count = sum(bool(node["is_seed"]) for node in members)
        if len(members) != cluster["n_nodes"] or seed_count != cluster["n_seed"]:
            raise ValueError(f"clusters[{cluster_id}] counts do not match report nodes")
        if any(cluster_by_gid.get(int(gid)) != cluster_id for gid in cluster["top_gids"]):
            raise ValueError(f"clusters[{cluster_id}].top_gids contains a node from another cluster")
    total_tiyn = sum(edge["sum_tiyn"] for edge in edge_records)
    if total_tiyn > 2**53 - 1:
        raise ValueError("dataset turnover exceeds the safe JSON integer range")
    transaction_count = sum(edge["n_tx"] for edge in edge_records)
    if _exact_int(metadata["transaction_count"], "dataset.transaction_count") != transaction_count:
        raise ValueError("dataset.transaction_count does not match aggregated edge counts")
    for edge in edge_records:
        if int(edge["src"]) not in cluster_by_gid or int(edge["dst"]) not in cluster_by_gid:
            raise ValueError("report edge endpoint is missing from report nodes")
    internal_tiyn = sum(cluster["sum_tiyn_internal"] for cluster in cluster_records)
    intercluster_tiyn = sum(
        edge["sum_tiyn"]
        for edge in edge_records
        if cluster_by_gid[int(edge["src"])] != cluster_by_gid[int(edge["dst"])]
    )
    if internal_tiyn + intercluster_tiyn != total_tiyn:
        raise ValueError("report cluster turnover does not reconcile to directed edges")
    if _exact_int(metadata["sum_tiyn"], "dataset.sum_tiyn") != total_tiyn:
        raise ValueError("dataset.sum_tiyn does not match report edges")
    for key, actual in (
        ("node_count", len(node_records)),
        ("edge_count", len(edge_records)),
    ):
        if _exact_int(metadata[key], f"dataset.{key}") != actual:
            raise ValueError(f"dataset.{key} does not match report records")

    ranked = df.sort_values(
        ["priority_score", "gid"], ascending=[False, True], kind="mergesort"
    ).head(20)
    top_records = []
    for rank, row in enumerate(ranked.itertuples(index=False), start=1):
        values = row._asdict()
        top_records.append({
            "rank": rank,
            "gid": str(_exact_int(values["gid"], "top_nodes.gid")),
            "role": str(values["role"]),
            "priority_score": _json_safe(values["priority_score"]),
            "why": str(values["why"]),
        })

    report = {
        "schema_version": "1.0",
        "dataset": _json_safe(metadata),
        "parameters": _json_safe(parameters),
        "nodes": node_records,
        "edges": edge_records,
        "clusters": cluster_records,
        "top_nodes": top_records,
    }
    json.dumps(report, ensure_ascii=False, allow_nan=False)
    return report
