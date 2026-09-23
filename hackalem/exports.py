"""Validate and write deterministic CSV exports."""

import io
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .clusters import _format_kzt
from .report import _exact_int

def _report_gid(value, label: str) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a decimal string")
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a decimal string") from exc
    if str(number) != value or not np.iinfo(np.int64).min <= number <= np.iinfo(np.int64).max:
        raise ValueError(f"{label} must be a canonical signed int64 decimal string")
    return value, number


def _report_score(value, label: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be a finite number within [0, 1]")
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number within [0, 1]") from exc
    if not np.isfinite(score) or not 0 <= score <= 1:
        raise ValueError(f"{label} must be a finite number within [0, 1]")
    return score


def write_outputs(report: dict, out_dir: Path):
    """Write the three contract CSVs from the shared report object."""
    if not isinstance(report, dict) or report.get("schema_version") != "1.0":
        raise ValueError("write_outputs requires a schema_version 1.0 report object")
    dataset = report.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("report.dataset must be a dictionary")
    nodes = report.get("nodes")
    clusters = report.get("clusters")
    edges = report.get("edges")
    top_nodes = report.get("top_nodes")
    if not all(isinstance(value, list) for value in (nodes, clusters, edges, top_nodes)):
        raise ValueError("report nodes, edges, clusters, and top_nodes must be arrays")

    required_node_fields = ("gid", "role", "role_score", "cluster_id", "priority_score", "evidence", "is_seed")
    node_by_gid = {}
    node_rows = []
    previous_gid = None
    for position, node in enumerate(nodes):
        if not isinstance(node, dict) or any(field not in node for field in required_node_fields):
            raise ValueError(f"report.nodes[{position}] is missing required CSV fields")
        gid, numeric_gid = _report_gid(node["gid"], f"report.nodes[{position}].gid")
        if gid in node_by_gid:
            raise ValueError(f"duplicate report node gid {gid}")
        if previous_gid is not None and numeric_gid <= previous_gid:
            raise ValueError("report.nodes must be sorted by unique numeric gid")
        previous_gid = numeric_gid
        cluster_id = _exact_int(node["cluster_id"], f"report.nodes[{gid}].cluster_id")
        if not isinstance(node["is_seed"], bool):
            raise ValueError(f"report.nodes[{gid}].is_seed must be boolean")
        role = node["role"]
        evidence = node["evidence"]
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"report.nodes[{gid}].role must not be empty")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 200:
            raise ValueError(f"report.nodes[{gid}].evidence must be non-empty and at most 200 characters")
        role_score = _report_score(node["role_score"], f"report.nodes[{gid}].role_score")
        priority_score = _report_score(node["priority_score"], f"report.nodes[{gid}].priority_score")
        node_by_gid[gid] = {
            "gid": gid,
            "numeric_gid": numeric_gid,
            "role": role,
            "role_score": role_score,
            "cluster_id": cluster_id,
            "priority_score": priority_score,
            "evidence": evidence,
            "is_seed": node["is_seed"],
        }
        node_rows.append({
            "gid": gid,
            "role": role,
            "role_score": role_score,
            "cluster_id": cluster_id,
            "priority_score": priority_score,
            "evidence": evidence,
        })

    expected_node_count = _exact_int(dataset.get("node_count"), "dataset.node_count")
    if expected_node_count != len(nodes):
        raise ValueError("dataset.node_count does not match report.nodes")

    cluster_by_id = {}
    cluster_rows = []
    previous_cluster_id = None
    for position, cluster in enumerate(clusters):
        required = (
            "cluster_id", "n_nodes", "n_seed", "sum_tiyn_internal",
            "sum_kzt_internal", "top_gids", "hypothesis",
        )
        if not isinstance(cluster, dict) or any(field not in cluster for field in required):
            raise ValueError(f"report.clusters[{position}] is missing required CSV fields")
        cluster_id = _exact_int(cluster["cluster_id"], f"report.clusters[{position}].cluster_id")
        if cluster_id in cluster_by_id or (previous_cluster_id is not None and cluster_id <= previous_cluster_id):
            raise ValueError("report.clusters must be sorted by unique cluster_id")
        previous_cluster_id = cluster_id
        n_nodes = _exact_int(cluster["n_nodes"], f"clusters[{cluster_id}].n_nodes")
        n_seed = _exact_int(cluster["n_seed"], f"clusters[{cluster_id}].n_seed")
        sum_tiyn_internal = _exact_int(cluster["sum_tiyn_internal"], f"clusters[{cluster_id}].sum_tiyn_internal")
        sum_kzt_internal = cluster["sum_kzt_internal"]
        hypothesis = cluster["hypothesis"]
        if n_nodes < 1 or not 0 <= n_seed <= n_nodes or sum_tiyn_internal < 0:
            raise ValueError(f"clusters[{cluster_id}] has invalid counts or internal turnover")
        if sum_kzt_internal != _format_kzt(sum_tiyn_internal):
            raise ValueError(f"clusters[{cluster_id}].sum_kzt_internal does not match sum_tiyn_internal")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ValueError(f"clusters[{cluster_id}].hypothesis must not be empty")
        top_gids = cluster["top_gids"]
        if not isinstance(top_gids, list) or len(top_gids) > 5:
            raise ValueError(f"clusters[{cluster_id}].top_gids must be an array of at most five IDs")
        parsed_top_gids = [_report_gid(gid, f"clusters[{cluster_id}].top_gids")[0] for gid in top_gids]
        if not parsed_top_gids:
            raise ValueError(f"clusters[{cluster_id}].top_gids must not be empty")
        members = [node for node in node_by_gid.values() if node["cluster_id"] == cluster_id]
        seed_count = sum(node["is_seed"] for node in members)
        if len(members) != n_nodes or seed_count != n_seed:
            raise ValueError(f"clusters[{cluster_id}] counts do not match report nodes")
        expected_cluster_top = [
            node["gid"]
            for node in sorted(members, key=lambda node: (-node["priority_score"], node["numeric_gid"]))[:5]
        ]
        if parsed_top_gids != expected_cluster_top:
            raise ValueError(f"clusters[{cluster_id}].top_gids do not match priority/gid ordering")
        cluster_by_id[cluster_id] = {
            "n_nodes": n_nodes,
            "n_seed": n_seed,
            "sum_tiyn_internal": sum_tiyn_internal,
            "top_gids": parsed_top_gids,
        }
        cluster_rows.append({
            "cluster_id": cluster_id,
            "n_nodes": n_nodes,
            "n_seed": n_seed,
            "sum_kzt_internal": sum_kzt_internal,
            "top_gids": json.dumps(parsed_top_gids, ensure_ascii=False, separators=(",", ":")),
            "hypothesis": hypothesis,
        })

    if {node["cluster_id"] for node in node_by_gid.values()} != set(cluster_by_id):
        raise ValueError("report.clusters must cover exactly the assigned report nodes")

    edge_total = 0
    edge_transaction_total = 0
    internal_by_cluster = {cluster_id: 0 for cluster_id in cluster_by_id}
    for position, edge in enumerate(edges):
        if not isinstance(edge, dict) or not all(key in edge for key in ("src", "dst", "sum_tiyn", "n_tx")):
            raise ValueError(f"report.edges[{position}] is missing required fields")
        src, _ = _report_gid(edge["src"], f"report.edges[{position}].src")
        dst, _ = _report_gid(edge["dst"], f"report.edges[{position}].dst")
        if src not in node_by_gid or dst not in node_by_gid:
            raise ValueError(f"report.edges[{position}] has an endpoint absent from report.nodes")
        sum_tiyn = _exact_int(edge["sum_tiyn"], f"report.edges[{position}].sum_tiyn")
        n_tx = _exact_int(edge["n_tx"], f"report.edges[{position}].n_tx")
        depth = _exact_int(edge["depth"], f"report.edges[{position}].depth")
        if sum_tiyn <= 0 or n_tx <= 0 or not 1 <= depth <= 4:
            raise ValueError(f"report.edges[{position}] has invalid amount, count, or depth")
        edge_total += sum_tiyn
        edge_transaction_total += n_tx
        source_cluster = node_by_gid[src]["cluster_id"]
        destination_cluster = node_by_gid[dst]["cluster_id"]
        if source_cluster == destination_cluster:
            internal_by_cluster[source_cluster] += sum_tiyn
    if edge_total != _exact_int(dataset.get("sum_tiyn"), "dataset.sum_tiyn"):
        raise ValueError("dataset.sum_tiyn does not match report.edges")
    if edge_total > 2**53 - 1:
        raise ValueError("dataset.sum_tiyn exceeds the safe JSON integer range")
    if edge_transaction_total != _exact_int(dataset.get("transaction_count"), "dataset.transaction_count"):
        raise ValueError("dataset.transaction_count does not match report edge counts")
    if any(internal_by_cluster[key] != value["sum_tiyn_internal"] for key, value in cluster_by_id.items()):
        raise ValueError("cluster internal sums do not match report.edges")
    if _exact_int(dataset.get("edge_count"), "dataset.edge_count") != len(edges):
        raise ValueError("dataset.edge_count does not match report.edges")

    ranked_nodes = sorted(node_by_gid.values(), key=lambda node: (-node["priority_score"], node["numeric_gid"]))
    expected_top_ids = [node["gid"] for node in ranked_nodes[:20]]
    if len(top_nodes) != min(20, len(node_by_gid)):
        raise ValueError("report.top_nodes must contain exactly min(20, node_count) rows")
    if not isinstance(top_nodes, list):
        raise ValueError("report.top_nodes must be an array")
    top_rows = []
    actual_top_ids = []
    for position, top in enumerate(top_nodes, start=1):
        required = ("rank", "gid", "role", "priority_score", "why")
        if not isinstance(top, dict) or any(field not in top for field in required):
            raise ValueError(f"report.top_nodes[{position - 1}] is missing required CSV fields")
        rank = _exact_int(top["rank"], f"report.top_nodes[{position - 1}].rank")
        if rank != position:
            raise ValueError("report.top_nodes ranks must be consecutive starting at 1")
        gid, _ = _report_gid(top["gid"], f"report.top_nodes[{position - 1}].gid")
        node = node_by_gid.get(gid)
        if node is None:
            raise ValueError(f"report.top_nodes contains unknown gid {gid}")
        role = top["role"]
        why = top["why"]
        priority_score = _report_score(top["priority_score"], f"report.top_nodes[{position - 1}].priority_score")
        if role != node["role"] or priority_score != node["priority_score"]:
            raise ValueError(f"top_nodes role/priority does not match nodes_roles for gid {gid}")
        if not isinstance(why, str) or not why.strip():
            raise ValueError(f"report.top_nodes[{position - 1}].why must not be empty")
        actual_top_ids.append(gid)
        top_rows.append({
            "rank": rank,
            "gid": gid,
            "role": role,
            "priority_score": priority_score,
            "why": why,
        })
    if actual_top_ids != expected_top_ids:
        raise ValueError("report.top_nodes does not match global priority/gid ordering")

    csv_specs = {
        "nodes_roles.csv": pd.DataFrame(node_rows, columns=[
            "gid", "role", "role_score", "cluster_id", "priority_score", "evidence",
        ]),
        "clusters.csv": pd.DataFrame(cluster_rows, columns=[
            "cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis",
        ]),
        "top_nodes.csv": pd.DataFrame(top_rows, columns=[
            "rank", "gid", "role", "priority_score", "why",
        ]),
    }
    csv_bytes = {}
    for filename, frame in csv_specs.items():
        buffer = io.StringIO(newline="")
        frame.to_csv(buffer, index=False, lineterminator="\n")
        csv_bytes[filename] = buffer.getvalue().encode("utf-8")

    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    for filename, contents in csv_bytes.items():
        (output_path / filename).write_bytes(contents)
    print(f"Three CSV exports written to {output_path}/")
