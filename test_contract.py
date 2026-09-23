#!/usr/bin/env python3
"""Contract checks for the Jigas pipeline (stdlib runner; no pytest required).

This file follows the public function signatures in docs/SPEC.md §11. The
small graph uses synthetic values; the official Parquet files are only read by
the release-output checks. A passing run is meaningful only after HA-07.1 has
produced the current `out/` artifacts.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import re
import sys
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import networkx as nx
import pandas as pd


BIG_GID = 9_007_199_254_741_021  # Greater than JavaScript's exact integer limit.
REQUIRED_COLUMNS = {
    "nodes_roles.csv": [
        "gid", "role", "role_score", "cluster_id", "priority_score", "evidence"
    ],
    "clusters.csv": [
        "cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"
    ],
    "top_nodes.csv": ["rank", "gid", "role", "priority_score", "why"],
}
ALLOWED_ROLES = {
    "coordinator", "distributor", "consolidator", "transit", "terminal", "peripheral"
}


class ContractFailure(Exception):
    """An assertion that should be reported as a concise contract failure."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ContractFailure(message)


def close(actual: Any, expected: float, message: str, *, abs_tol: float = 1e-10) -> None:
    try:
        value = float(actual)
    except (TypeError, ValueError) as exc:
        raise ContractFailure(f"{message}: expected {expected}, got a non-number") from exc
    require(math.isfinite(value), f"{message}: value is not finite")
    require(math.isclose(value, expected, rel_tol=1e-9, abs_tol=abs_tol),
            f"{message}: expected {expected}, got {value}")


def frame_record(frame: pd.DataFrame, gid: int) -> dict[str, Any]:
    require("gid" in frame.columns, "result is missing gid")
    matches = frame.loc[frame["gid"].map(int) == gid]
    require(len(matches) == 1, f"gid={gid}: expected exactly one row, got {len(matches)}")
    return matches.iloc[0].to_dict()


def make_sample() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return a tiny graph with a cycle, opposing edges, seeds, and an isolate."""
    nodes = pd.DataFrame(
        {
            "gid": [2, 10, 20, 30, 40, 80, BIG_GID],
            "depth": [0, 0, 1, 2, 4, 1, 1],
            "is_seed": [True, True, False, False, False, False, False],
        }
    )
    edges = pd.DataFrame(
        [
            (2, 20, 5_000.00, 1, 1),
            (10, 20, 5_000.00, 1, 1),
            (20, 30, 12_000.00, 2, 1),
            (30, 20, 18_000.00, 1, 1),
            (30, 40, 9_000.00, 1, 3),
        ],
        columns=["src", "dst", "sum_kzt", "n_tx", "depth"],
    )
    tx = pd.DataFrame(
        [
            (2, 20, "2026-07-01", 5_000.00),
            (10, 20, "2026-07-02", 5_000.00),
            (20, 30, "2026-07-02", 6_000.00),
            (20, 30, "2026-07-04", 6_000.00),
            (30, 20, "2026-07-03", 18_000.00),
            (30, 40, "2026-07-31", 9_000.00),
        ],
        columns=["src", "dst", "date", "sum_kzt"],
    )
    tx["date"] = pd.to_datetime(tx["date"])
    return edges, nodes, tx


def without_stdout(function: Callable[..., Any], *args: Any) -> Any:
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args)


def expect_value_error(label: str, function: Callable[[], Any]) -> None:
    try:
        function()
    except ValueError as exc:
        require(bool(str(exc).strip()), f"{label}: ValueError must explain the invalid input")
        return
    except Exception as exc:  # noqa: BLE001 - report wrong exception type as a contract failure.
        raise ContractFailure(f"{label}: expected ValueError, got {type(exc).__name__}") from exc
    raise ContractFailure(f"{label}: invalid input was accepted")


def test_input_validation(starter: Any) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    edges, nodes, tx = make_sample()
    orphans = without_stdout(starter.sanity_check, edges, nodes, tx)
    require({int(gid) for gid in orphans} == {80, BIG_GID},
            f"input validation: expected isolated gids 80 and {BIG_GID}, got {orphans!r}")
    require("sum_tiyn" in edges.columns and "sum_tiyn" in tx.columns,
            "input validation: successful check must add integer sum_tiyn")
    require(pd.api.types.is_integer_dtype(edges["sum_tiyn"].dtype),
            "input validation: edge sum_tiyn must use integer tiyn")
    require(int(edges.loc[(edges.src == 2) & (edges.dst == 20), "sum_tiyn"].iloc[0]) == 500_000,
            "sample edge 2->20: conversion to tiyn changed the exact amount")

    bad_edges, bad_nodes, bad_tx = make_sample()
    bad_edges.loc[0, "dst"] = 999
    expect_value_error(
        "unknown edge endpoint dst=999",
        lambda: without_stdout(starter.sanity_check, bad_edges, bad_nodes, bad_tx),
    )

    bad_edges, bad_nodes, bad_tx = make_sample()
    bad_edges.loc[0, "sum_kzt"] = 0.0
    expect_value_error(
        "non-positive edge amount",
        lambda: without_stdout(starter.sanity_check, bad_edges, bad_nodes, bad_tx),
    )

    bad_edges, bad_nodes, bad_tx = make_sample()
    bad_edges.loc[0, "n_tx"] = 0
    expect_value_error(
        "non-positive edge transaction count",
        lambda: without_stdout(starter.sanity_check, bad_edges, bad_nodes, bad_tx),
    )
    return edges, nodes, tx


def test_graph_features_and_report(starter: Any) -> None:
    edges, nodes, tx = test_input_validation(starter)
    graph = starter.build_graph(edges, nodes)
    require(isinstance(graph, nx.DiGraph), "build_graph must return a directed NetworkX graph")
    require(set(graph.nodes) == {int(gid) for gid in nodes.gid},
            "build_graph must include every node row, including both isolates")
    require(BIG_GID in graph, f"build_graph rounded or dropped gid={BIG_GID}")
    require(graph.has_edge(20, 30) and graph.has_edge(30, 20),
            "build_graph must retain both directions of opposing edges")
    edge = graph[2][20]
    require(int(edge["sum_tiyn"]) == 500_000 and int(edge["n_tx"]) == 1,
            "gid=2->20: edge attributes do not preserve integer amount/count")

    basic = starter.basic_features(graph, nodes)
    required = {
        "gid", "depth", "is_seed", "in_deg", "out_deg", "in_tiyn", "out_tiyn",
        "in_tx", "out_tx", "pass_through", "boundary", "isolated",
    }
    require(required.issubset(basic.columns),
            f"basic_features missing columns: {sorted(required - set(basic.columns))}")
    alone = frame_record(basic, 80)
    require(bool(alone["isolated"]), "gid=80: isolated flag must be true")
    require(int(alone["in_deg"]) == 0 and int(alone["out_deg"]) == 0,
            "gid=80: isolated node must have zero in/out degree")
    boundary = frame_record(basic, 40)
    require(bool(boundary["boundary"]), "gid=40: depth=4 sink must be a boundary node")
    require(int(boundary["out_deg"]) == 0 and int(boundary["in_tiyn"]) == 900_000,
            "gid=40: boundary should retain its incoming amount and zero outgoing degree")
    require(float(boundary["pass_through"]) == 0.0,
            "gid=40: positive input and zero output should have pass_through=0")
    large_id_row = frame_record(basic, BIG_GID)
    require(pd.isna(large_id_row["pass_through"]),
            f"gid={BIG_GID}: zero incoming amount means pass_through is unknown")

    enriched = starter.enrich_features(graph, basic, tx)
    for name in ("seed_reach_count", "betweenness", "last_in", "days_after_last_in"):
        require(name in enriched.columns, f"enrich_features missing {name}")
    for gid in (20, 30, 40):
        row = frame_record(enriched, gid)
        require(int(row["seed_reach_count"]) == 2,
                f"gid={gid}: two distinct seeds should reach it within four steps, got {row['seed_reach_count']}")
    close(frame_record(enriched, 20)["betweenness"], 4 / 30,
          "gid=20: directed, unweighted, normalized betweenness")
    close(frame_record(enriched, 30)["betweenness"], 3 / 30,
          "gid=30: directed, unweighted, normalized betweenness")
    require(pd.Timestamp(frame_record(enriched, 20)["last_in"]).date() == date(2026, 7, 3),
            "gid=20: last_in must use the latest inbound calendar date")
    require(int(frame_record(enriched, 20)["days_after_last_in"]) == 28,
            "gid=20: D must be measured from 2026-07-31 in calendar days")
    require(int(frame_record(enriched, 40)["days_after_last_in"]) == 0,
            "gid=40: inbound transaction on 2026-07-31 must have D=0")
    require(pd.isna(frame_record(enriched, BIG_GID)["last_in"]),
            f"gid={BIG_GID}: no inbound transaction must remain unknown")

    # Test exact string serialization through the public report/export seam.
    roles, _role_parameters = starter.assign_roles(enriched)
    prioritized, _priority_parameters = starter.compute_priority(roles)
    cluster_map = starter.cluster_nodes(graph)
    require({int(gid) for gid in cluster_map} == {int(gid) for gid in nodes.gid},
            "cluster_nodes must assign every node exactly once")
    cluster_members = {}
    for gid, cid in cluster_map.items():
        cluster_members.setdefault(int(cid), set()).add(int(gid))
    for isolated_gid in (80, BIG_GID):
        require(next(group for group in cluster_members.values() if isolated_gid in group) == {isolated_gid},
                f"cluster_nodes must give isolated gid={isolated_gid} its own cluster")
    prioritized = prioritized.copy()
    prioritized["cluster_id"] = prioritized["gid"].map(cluster_map)
    summaries = starter.summarize_clusters(graph, prioritized)
    report = starter.build_report(
        prioritized, edges, summaries, metadata={}, parameters={}
    )
    require(isinstance(report, dict), "build_report must return a JSON-compatible dict")
    require(report.get("schema_version") == "1.0", "report schema_version must be '1.0'")
    nodes_json = report.get("nodes", [])
    try:
        json.dumps(report, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractFailure("build_report must contain JSON-safe values without NaN/Infinity") from exc
    require(all(isinstance(node.get("gid"), str) for node in nodes_json),
            "build_report must serialize every node gid as a string")
    require(any(node.get("gid") == str(BIG_GID) for node in nodes_json),
            f"build_report must serialize gid={BIG_GID} as its exact decimal string")
    node_json_by_gid = {node["gid"]: node for node in nodes_json}
    require("boundary" in node_json_by_gid["40"].get("warnings", []),
            "gid=40: report should expose the depth boundary warning")
    require("isolated" in node_json_by_gid["80"].get("warnings", []),
            "gid=80: report should expose the isolated-node warning")
    scores_by_gid = {
        node["gid"]: float(node["priority_score"])
        for node in nodes_json
    }
    report_top = report.get("top_nodes", [])
    report_top_gids = [item.get("gid") for item in report_top]
    expected_report_top = sorted(
        scores_by_gid,
        key=lambda gid: (-scores_by_gid[gid], int(gid)),
    )[:20]
    require(report_top_gids == expected_report_top,
            "build_report top_nodes must use unrounded score desc then numeric gid asc")
    require(scores_by_gid["2"] == scores_by_gid["10"],
            "synthetic seeds 2 and 10 should create an exact priority tie")
    require(report_top_gids.index("2") < report_top_gids.index("10"),
            "equal priority must be broken by numeric gid, not lexicographic string order")
    for edge_json in report.get("edges", []):
        require(isinstance(edge_json["src"], str) and isinstance(edge_json["dst"], str),
                "build_report must serialize edge endpoints as strings")
    require(all(isinstance(node.get("gid"), str) for node in report.get("top_nodes", [])),
            "build_report must serialize every top_nodes gid as a string")
    for cluster_json in report.get("clusters", []):
        require(all(isinstance(gid, str) for gid in cluster_json.get("top_gids", [])),
                "build_report must serialize cluster top_gids as strings")

    with tempfile.TemporaryDirectory(prefix="jigas-contract-") as temp:
        out_dir = Path(temp)
        starter.write_outputs(report, out_dir)
        nodes_csv = pd.read_csv(out_dir / "nodes_roles.csv", dtype={"gid": "string"})
        top_csv = pd.read_csv(out_dir / "top_nodes.csv", dtype={"gid": "string"})
        require(str(BIG_GID) in set(nodes_csv["gid"].dropna()),
                f"nodes_roles.csv rounded or dropped gid={BIG_GID}")
        require(str(BIG_GID) in set(top_csv["gid"].dropna()),
                f"top_nodes.csv rounded or dropped gid={BIG_GID}")
        clusters_csv = pd.read_csv(
            out_dir / "clusters.csv",
            dtype={"top_gids": "string", "sum_kzt_internal": "string"},
        )
        for cell in clusters_csv["top_gids"].dropna():
            values = json.loads(cell)
            require(all(isinstance(gid, str) for gid in values),
                    "clusters.csv top_gids must be a JSON array of decimal strings")


def linear_quantile(values: list[float], q: float) -> float:
    """Small independent implementation of the SPEC's linear quantile rule."""
    ordered = sorted(float(value) for value in values)
    require(bool(ordered), "test fixture error: quantile input must not be empty")
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def synthetic_role_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Matches coordinator, distributor, consolidator, and transit.
            (101, 1, False, 3, 10, 1_000_000, 1_000_000, 3, 10, 1.0,
             2, 0.9, False, False, 15),
            # Seed with positive B contributes to Q90 but cannot be transit/terminal.
            (102, 0, True, 1, 1, 1_000_000, 1_000_000, 1, 1, 1.0,
             0, 0.1, False, False, 20),
            # Depth-4 sink with inbound volume can consolidate but is not terminal.
            (103, 4, False, 3, 0, 500_000, 0, 3, 0, 0.0,
             0, 0.0, True, False, 14),
            # A seed with an otherwise transit-like ratio remains excluded.
            (104, 0, True, 1, 1, 500_000, 500_000, 1, 1, 1.0,
             0, 0.0, False, False, 10),
            (105, 1, False, 0, 0, 0, 0, 0, 0, math.nan,
             0, 0.0, False, True, math.nan),
        ],
        columns=[
            "gid", "depth", "is_seed", "in_deg", "out_deg", "in_tiyn", "out_tiyn",
            "in_tx", "out_tx", "pass_through", "seed_reach_count", "betweenness",
            "boundary", "isolated", "days_after_last_in",
        ],
    )


def test_roles_and_priority(starter: Any) -> None:
    role_frame, _parameters = starter.assign_roles(synthetic_role_rows())
    primary = frame_record(role_frame, 101)
    require(primary["role"] == "coordinator",
            f"gid=101: primary role precedence should select coordinator, got {primary['role']!r}")
    matched = primary["matched_roles"]
    names = [item["role"] for item in matched]
    require(names == ["coordinator", "distributor", "consolidator", "transit"],
            f"gid=101: all matching roles should be preserved in rule order, got {names!r}")
    expected_supports = {
        "coordinator": 0.45,
        "distributor": 0.675,
        "consolidator": 0.675,
        "transit": 0.65,
    }
    for item in matched:
        close(item["support"], expected_supports[item["role"]],
              f"gid=101 matched {item['role']} support")
    close(primary["role_score"], 0.45, "gid=101 primary role_score")

    boundary = frame_record(role_frame, 103)
    require(boundary["role"] == "consolidator", "gid=103: depth-4 inbound sink may consolidate")
    require("terminal" not in [item["role"] for item in boundary["matched_roles"]],
            "gid=103: depth-4 sink must not be labeled terminal")
    for gid in (102, 104):
        row = frame_record(role_frame, gid)
        require("transit" not in [item["role"] for item in row["matched_roles"]],
                f"gid={gid}: seed cannot be assigned transit from incomplete inbound context")
        require("terminal" not in [item["role"] for item in row["matched_roles"]],
                f"gid={gid}: seed cannot be assigned terminal")
    isolated = frame_record(role_frame, 105)
    require(isolated["role"] == "peripheral" and float(isolated["role_score"]) == 0.0,
            "gid=105: isolated node should be peripheral with zero role_score")

    prioritized, _parameters = starter.compute_priority(role_frame)
    positive_volume = [
        (int(row.gid), (int(row.in_tiyn) + int(row.out_tiyn)) / 100.0)
        for row in role_frame.itertuples(index=False)
        if int(row.in_tiyn) + int(row.out_tiyn) > 0
    ]
    volume_q95 = linear_quantile([volume for _, volume in positive_volume], 0.95)
    positive_b = [float(value) for value in role_frame["betweenness"] if float(value) > 0]
    b_q95 = linear_quantile(positive_b, 0.95)

    for row in prioritized.itertuples(index=False):
        source = frame_record(role_frame, int(row.gid))
        volume = (int(source["in_tiyn"]) + int(source["out_tiyn"])) / 100.0
        supports = [float(item["support"]) for item in source["matched_roles"]]
        M = max(supports, default=0.0)
        A = min(1.0, math.log1p(volume) / math.log1p(volume_q95)) if volume > 0 else 0.0
        C = min(1.0, int(source["seed_reach_count"]) / 5.0)
        B = float(source["betweenness"])
        H = min(1.0, B / b_q95) if B > 0 and b_q95 > 0 else 0.0
        expected_components = {"M": M, "A": A, "C": C, "H": H}
        components = row.score_components
        require(isinstance(components, dict), f"gid={row.gid}: score_components must be a mapping")
        for name, expected in expected_components.items():
            close(components[name], expected, f"gid={row.gid} score component {name}")
        expected_priority = 0.35 * M + 0.30 * A + 0.20 * C + 0.15 * H
        close(row.priority_score, expected_priority, f"gid={row.gid} priority_score")
        evidence = str(row.evidence)
        why = str(row.why)
        require(evidence and len(evidence) <= 200 and re.search(r"\d", evidence),
                f"gid={row.gid}: evidence must be <=200 characters and include a numeric basis")
        require(why and re.search(r"\d", why),
                f"gid={row.gid}: why must identify numeric priority evidence")


def validate_csv_release(data_dir: Path, out_dir: Path) -> None:
    for filename in (*REQUIRED_COLUMNS, "report.html", "validation.json"):
        require((out_dir / filename).is_file(),
                f"missing release artifact {out_dir / filename}")

    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    edges = pd.read_parquet(data_dir / "edges.parquet")
    expected_gids = {str(int(gid)) for gid in nodes["gid"]}
    nodes_csv = pd.read_csv(out_dir / "nodes_roles.csv", dtype={"gid": "string"})
    clusters_csv = pd.read_csv(
        out_dir / "clusters.csv",
        dtype={"top_gids": "string", "sum_kzt_internal": "string"},
    )
    top_csv = pd.read_csv(out_dir / "top_nodes.csv", dtype={"gid": "string"})

    for filename, frame in (
        ("nodes_roles.csv", nodes_csv),
        ("clusters.csv", clusters_csv),
        ("top_nodes.csv", top_csv),
    ):
        required = REQUIRED_COLUMNS[filename]
        require(list(frame.columns[: len(required)]) == required,
                f"{filename}: required columns/order mismatch; expected {required}")
        require(not frame[required].isna().any().any(),
                f"{filename}: one or more required output cells are empty")

    actual_gids = nodes_csv["gid"].astype(str).tolist()
    require(len(actual_gids) == len(set(actual_gids)), "nodes_roles.csv: duplicate gid")
    require(set(actual_gids) == expected_gids,
            "nodes_roles.csv: gid set differs from nodes.parquet")
    require(all(re.fullmatch(r"-?\d+", gid) for gid in actual_gids),
            "nodes_roles.csv: gid must be a full decimal integer without .0 or exponent")
    require([int(gid) for gid in actual_gids] == sorted(map(int, actual_gids)),
            "nodes_roles.csv: rows must be sorted by numeric gid")
    require(set(nodes_csv["role"].astype(str)).issubset(ALLOWED_ROLES),
            "nodes_roles.csv: unknown role value")
    for score_col in ("role_score", "priority_score"):
        values = pd.to_numeric(nodes_csv[score_col], errors="coerce").to_numpy(dtype=float)
        require(bool((values.size > 0) and all(math.isfinite(x) and 0 <= x <= 1 for x in values)),
                f"nodes_roles.csv: {score_col} must be finite and within [0,1]")
    for gid, evidence in zip(actual_gids, nodes_csv["evidence"].astype(str)):
        require(evidence.strip() and len(evidence) <= 200 and re.search(r"\d", evidence),
                f"gid={gid}: evidence must be present, numeric, and <=200 characters")

    cluster_ids = pd.to_numeric(nodes_csv["cluster_id"], errors="coerce")
    require(not cluster_ids.isna().any(), "nodes_roles.csv: cluster_id must be numeric")
    membership = {
        str(gid): int(cluster_id)
        for gid, cluster_id in zip(nodes_csv["gid"].astype(str), cluster_ids)
    }
    cluster_table_ids = pd.to_numeric(clusters_csv["cluster_id"], errors="coerce")
    require(not cluster_table_ids.isna().any(), "clusters.csv: cluster_id must be numeric")
    cluster_table_ids = [int(value) for value in cluster_table_ids]
    require(cluster_table_ids == sorted(set(cluster_table_ids)),
            "clusters.csv: cluster rows must be unique and sorted by cluster_id")
    require(set(cluster_table_ids) == set(membership.values()),
            "clusters.csv: cluster IDs must cover exactly the nodes_roles memberships")

    raw_seed = {str(int(row.gid)): bool(row.is_seed) for row in nodes.itertuples(index=False)}
    raw_edge_rows = list(edges.itertuples(index=False))
    cluster_rows_by_id = {
        int(row.cluster_id): row for row in clusters_csv.itertuples(index=False)
    }
    score_by_gid = {
        str(row.gid): float(row.priority_score) for row in nodes_csv.itertuples(index=False)
    }
    for cid in cluster_table_ids:
        members = {gid for gid, value in membership.items() if value == cid}
        summary = cluster_rows_by_id[cid]
        require(int(summary.n_nodes) == len(members),
                f"cluster_id={cid}: n_nodes does not match nodes_roles.csv")
        require(int(summary.n_seed) == sum(raw_seed[gid] for gid in members),
                f"cluster_id={cid}: n_seed does not match nodes.parquet")
        internal_tiyn = sum(
            round(float(edge.sum_kzt) * 100)
            for edge in raw_edge_rows
            if str(int(edge.src)) in members
            and str(int(edge.dst)) in members
        )
        amount_text = str(summary.sum_kzt_internal)
        require(re.fullmatch(r"\d+\.\d{2}", amount_text) is not None,
                f"cluster_id={cid}: sum_kzt_internal must have exactly two decimals")
        try:
            amount_tiyn = int(Decimal(amount_text) * 100)
        except (InvalidOperation, ValueError) as exc:
            raise ContractFailure(f"cluster_id={cid}: invalid internal amount") from exc
        require(amount_tiyn == internal_tiyn,
                f"cluster_id={cid}: internal sum differs from directed edges")
        try:
            top_gids = json.loads(str(summary.top_gids))
        except json.JSONDecodeError as exc:
            raise ContractFailure(f"cluster_id={cid}: top_gids is not valid JSON") from exc
        require(isinstance(top_gids, list) and all(isinstance(gid, str) for gid in top_gids),
                f"cluster_id={cid}: top_gids must be a JSON array of exact ID strings")
        expected_top = sorted(members, key=lambda gid: (-score_by_gid[gid], int(gid)))[:5]
        require(top_gids == expected_top,
                f"cluster_id={cid}: top_gids do not match cluster score order")

    top_gids = top_csv["gid"].astype(str).tolist()
    expected_top_gids = sorted(expected_gids, key=lambda gid: (-score_by_gid[gid], int(gid)))[:20]
    require(top_gids == expected_top_gids,
            "top_nodes.csv: must contain min(20,N) nodes ordered by score desc then numeric gid")
    ranks = pd.to_numeric(top_csv["rank"], errors="coerce").tolist()
    require(ranks == list(range(1, len(top_csv) + 1)),
            "top_nodes.csv: rank must be consecutive from 1")
    roles_by_gid = dict(zip(nodes_csv["gid"].astype(str), nodes_csv["role"].astype(str)))
    for row in top_csv.itertuples(index=False):
        gid = str(row.gid)
        require(str(row.role) == roles_by_gid[gid], f"gid={gid}: top role differs from nodes_roles.csv")
        close(row.priority_score, score_by_gid[gid], f"gid={gid} top priority differs from nodes_roles.csv")
        require(str(row.why).strip() != "", f"gid={gid}: top why must not be empty")

    html = (out_dir / "report.html").read_text(encoding="utf-8")
    for marker in ("__REPORT_DATA__", "__VIS_NETWORK_JS__", "__VIS_NETWORK_CSS__"):
        require(marker not in html, f"report.html still contains template marker {marker}")
    external_reference = re.search(
        r"\b(?:src|href)\s*=\s*(['\"])(?:https?:)?//", html, flags=re.IGNORECASE
    )
    require(external_reference is None,
            "report.html must not load scripts/styles from a remote host")

    def reject_nonstandard_json(value: str) -> None:
        raise ContractFailure(f"validation.json contains non-standard numeric value {value}")

    try:
        with (out_dir / "validation.json").open("r", encoding="utf-8") as stream:
            validation = json.load(stream, parse_constant=reject_nonstandard_json)
    except json.JSONDecodeError as exc:
        raise ContractFailure("validation.json is not valid JSON") from exc
    require(isinstance(validation, dict), "validation.json root must be an object")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Jigas output contracts")
    parser.add_argument("--data", type=Path, default=Path("./data"), help="folder with the three Parquet files")
    parser.add_argument("--out", type=Path, default=Path("./out"), help="folder with the current pipeline output")
    args = parser.parse_args()

    try:
        import starter

        test_graph_features_and_report(starter)
        test_roles_and_priority(starter)
        validate_csv_release(args.data, args.out)
    except ContractFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - preserve one-line, non-data diagnostic for reviewers.
        print(f"FAIL: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PASS: synthetic boundary graph, exact-ID export, and current release outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
