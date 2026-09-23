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
import hashlib
import io
import json
import math
import re
import sys
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

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


def close_json_score(actual: Any, expected: float, message: str) -> None:
    require(isinstance(actual, (int, float)) and not isinstance(actual, bool),
            f"{message}: JSON score must be a number")
    require(math.isfinite(actual), f"{message}: JSON score must be finite")
    close(actual, expected, message)


def frame_record(frame: pd.DataFrame, gid: int) -> dict[str, Any]:
    require("gid" in frame.columns, "result is missing gid")
    matches = frame.loc[frame["gid"].map(int) == gid]
    require(len(matches) == 1, f"gid={gid}: expected exactly one row, got {len(matches)}")
    return matches.iloc[0].to_dict()


def reject_nonstandard_json(value: str) -> None:
    raise ContractFailure(f"JSON contains non-standard numeric value {value}")


def read_strict_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            report = json.load(stream, parse_constant=reject_nonstandard_json)
    except json.JSONDecodeError as exc:
        raise ContractFailure(f"{path.name}: not valid JSON") from exc
    require(isinstance(report, dict), f"{path.name}: JSON root must be an object")
    return report


class HtmlResourceParser(HTMLParser):
    """Collect browser-fetched HTML resources for local release validation."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "script" and values.get("src"):
            self.references.append(("script", str(values["src"])))
        elif tag.lower() == "link" and values.get("href"):
            self.references.append(("link", str(values["href"])))
        elif tag.lower() in {"img", "source"} and values.get("src"):
            self.references.append((tag.lower(), str(values["src"])))


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
    sample_total_tiyn = int(edges["sum_tiyn"].sum())
    require(sample_total_tiyn == 4_900_000,
            f"synthetic fixture turnover changed: expected 4900000 tiyn, got {sample_total_tiyn}")
    metadata = {
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "transaction_count": len(tx),
        "sum_tiyn": sample_total_tiyn,
        # These format-valid digests are metadata fixtures, not hashes of the sample frames.
        "sha256_files": {
            "nodes.parquet": "0" * 64,
            "edges.parquet": "1" * 64,
            "transactions.parquet": "a" * 64,
        },
    }
    report = starter.build_report(
        prioritized, edges, summaries, metadata=metadata, parameters={}
    )
    require(isinstance(report, dict), "build_report must return a JSON-compatible dict")
    require(report.get("schema_version") == "1.0", "report schema_version must be '1.0'")
    require(report.get("dataset") == metadata,
            "build_report must preserve validated period, counts, turnover, and source hashes")
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
    observed_node_fields = {"n_seed_payers", "sync_payers_max", "fanout_burst_max"}
    require("daily_profiles_by_gid" not in report
            and all(not (observed_node_fields & set(node)) for node in nodes_json),
            "P0 build_report without observed inputs must omit all optional P1 fields")
    try:
        p0_payload = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ContractFailure("P0 report must be serializable as strict JSON") from exc
    require(p0_payload["schema_version"] == "1.0"
            and p0_payload["nodes"][0]["gid"] == str(min(map(int, nodes["gid"]))),
            "P0 JSON compatibility fixture must preserve schema version and exact IDs")
    for node in p0_payload["nodes"]:
        for matched_role in node.get("matched_roles", []):
            matched_role.pop("reason", None)
    require(all("reason" not in matched_role
                for node in p0_payload["nodes"]
                for matched_role in node.get("matched_roles", [])),
            "compatibility fixture must represent an older P0 report without role reasons")
    validate_p1_daily_profiles(
        p0_payload,
        {node["gid"]: node for node in p0_payload["nodes"]},
        nodes,
        tx,
    )  # Missing P1 fields/profile is a valid legacy P0 payload.
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
        starter.write_outputs(p0_payload, out_dir)
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


def test_observed_daily_profiles(starter: Any) -> None:
    """Check HA-11.1 against independent transaction aggregation and P0 compatibility."""
    try:
        from hackalem.graph import enrich_observed_features
    except ImportError:
        print("SKIP: HA-11.1 is absent; P1 payload remains optional")
        return

    edges, nodes, tx = test_input_validation(starter)
    edges = edges.copy()
    tx = tx.copy()

    # Two same-day rows from payer 2 count as two transactions but one counterparty;
    # payer 10 on the same day establishes the distinct-payer count of two.
    extra_rows = pd.DataFrame(
        [
            (2, 20, "2026-07-01", 5_000.00, 500_000),
            (10, 20, "2026-07-01", 5_000.00, 500_000),
        ],
        columns=tx.columns,
    )
    extra_rows["date"] = pd.to_datetime(extra_rows["date"])
    tx = pd.concat([tx, extra_rows], ignore_index=True)
    for src in (2, 10):
        edge_mask = (edges["src"] == src) & (edges["dst"] == 20)
        require(int(edge_mask.sum()) == 1, f"fixture edge {src}->20 must be unique")
        edges.loc[edge_mask, "sum_kzt"] += 5_000.00
        edges.loc[edge_mask, "sum_tiyn"] += 500_000
        edges.loc[edge_mask, "n_tx"] += 1

    graph = starter.build_graph(edges, nodes)
    basic = starter.basic_features(graph, nodes)
    p0_features = starter.enrich_features(graph, basic, tx)
    p1_features, daily_profiles = enrich_observed_features(graph, p0_features, tx)
    require(set(daily_profiles) == {str(int(gid)) for gid in nodes["gid"]},
            "daily profiles must contain every node, including isolates")

    node_20 = frame_record(p1_features, 20)
    require(int(node_20["n_seed_payers"]) == 2,
            "gid=20: direct seed payers must count distinct immediate seed predecessors")
    require(int(node_20["sync_payers_max"]) == 2,
            "gid=20: same-day maximum must count distinct payers, not transaction rows")
    expected_20 = [
        {"date": "2026-07-01", "in_tiyn": 1_500_000, "out_tiyn": 0,
         "in_tx": 3, "out_tx": 0, "n_payers": 2, "n_payees": 0},
        {"date": "2026-07-02", "in_tiyn": 500_000, "out_tiyn": 600_000,
         "in_tx": 1, "out_tx": 1, "n_payers": 1, "n_payees": 1},
        {"date": "2026-07-03", "in_tiyn": 1_800_000, "out_tiyn": 0,
         "in_tx": 1, "out_tx": 0, "n_payers": 1, "n_payees": 0},
        {"date": "2026-07-04", "in_tiyn": 0, "out_tiyn": 600_000,
         "in_tx": 0, "out_tx": 1, "n_payers": 0, "n_payees": 1},
    ]
    require(daily_profiles[str(20)] == expected_20,
            "gid=20: daily sums/counts/unique counterparties must match the independent fixture")
    require(int(node_20["fanout_burst_max"]) == 1,
            "gid=20: fanout maximum must count unique same-day destinations")

    boundary_profile = daily_profiles[str(40)]
    require(boundary_profile == [
        {"date": "2026-07-31", "in_tiyn": 900_000, "out_tiyn": 0,
         "in_tx": 1, "out_tx": 0, "n_payers": 1, "n_payees": 0},
    ], "gid=40: depth-4 profile must keep observed input and zero unobserved output")
    for gid in (80, BIG_GID):
        row = frame_record(p1_features, gid)
        require(daily_profiles[str(gid)] == [], f"gid={gid}: isolate profile must be empty")
        require(int(row["n_seed_payers"]) == 0
                and int(row["sync_payers_max"]) == 0
                and int(row["fanout_burst_max"]) == 0,
                f"gid={gid}: absent observations must produce integer zero maxima")

    # P1 context must not feed back into P0 role or priority calculations.
    p0_roles, _ = starter.assign_roles(p0_features)
    p0_priority, _ = starter.compute_priority(p0_roles)
    p1_roles, _ = starter.assign_roles(p1_features)
    p1_priority, _ = starter.compute_priority(p1_roles)
    for gid in nodes["gid"].map(int):
        before = frame_record(p0_priority, int(gid))
        after = frame_record(p1_priority, int(gid))
        for field in ("role", "role_score", "priority_score", "evidence", "why", "score_components"):
            require(before[field] == after[field],
                    f"gid={int(gid)}: P1 observed features changed baseline {field}")

    cluster_map = starter.cluster_nodes(graph)
    p1_priority = p1_priority.copy()
    p1_priority["cluster_id"] = p1_priority["gid"].map(cluster_map)
    summaries = starter.summarize_clusters(graph, p1_priority)
    metadata = {
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "transaction_count": len(tx),
        "sum_tiyn": int(tx["sum_tiyn"].sum()),
        "sha256_files": {
            "nodes.parquet": "0" * 64,
            "edges.parquet": "1" * 64,
            "transactions.parquet": "a" * 64,
        },
    }
    report = starter.build_report(
        p1_priority, edges, summaries, metadata=metadata, parameters={},
        daily_profiles_by_gid=daily_profiles,
    )
    require("daily_profiles_by_gid" in report,
            "P1 build_report must attach daily_profiles_by_gid")
    report_20 = {node["gid"]: node for node in report["nodes"]}[str(20)]
    require(report_20["n_seed_payers"] == 2 and report_20["sync_payers_max"] == 2,
            "P1 report must serialize exact node-level observed features")
    require(report["daily_profiles_by_gid"][str(20)] == expected_20,
            "P1 report must retain the complete sorted daily profile")
    json.dumps(report, allow_nan=False)
    validate_p1_daily_profiles(
        report,
        {node["gid"]: node for node in report["nodes"]},
        nodes,
        tx,
    )

    bad_profiles = {
        gid: [record.copy() for record in records]
        for gid, records in daily_profiles.items()
    }
    bad_profiles[str(20)][0]["in_tiyn"] += 1
    expect_value_error(
        "P1 report rejects daily sums inconsistent with node totals",
        lambda: starter.build_report(
            p1_priority, edges, summaries, metadata=metadata, parameters={},
            daily_profiles_by_gid=bad_profiles,
        ),
    )
    expect_value_error(
        "P1 report rejects node features without the matching daily profile",
        lambda: starter.build_report(
            p1_priority, edges, summaries, metadata=metadata, parameters={},
        ),
    )


def test_release_json_compatibility(starter: Any) -> None:
    """Exercise JSON/CSV/assets/manifest checks on a complete tiny P0 release."""
    edges, nodes, tx = make_sample()
    with tempfile.TemporaryDirectory(prefix="jigas-release-contract-") as temp:
        root = Path(temp)
        data_dir = root / "data"
        out_dir = root / "out"
        data_dir.mkdir()
        out_dir.mkdir()
        for name, frame in (
            ("nodes", nodes), ("edges", edges), ("transactions", tx),
        ):
            frame.to_parquet(data_dir / f"{name}.parquet", index=False)
        input_hashes = {
            name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
            for name in ("nodes.parquet", "edges.parquet", "transactions.parquet")
        }

        without_stdout(starter.sanity_check, edges, nodes, tx)
        graph = starter.build_graph(edges, nodes)
        features = starter.enrich_features(graph, starter.basic_features(graph, nodes), tx)
        roles, _role_parameters = starter.assign_roles(features)
        priority, _priority_parameters = starter.compute_priority(roles)
        priority = priority.copy()
        priority["cluster_id"] = priority["gid"].map(starter.cluster_nodes(graph))
        summaries = starter.summarize_clusters(graph, priority)
        metadata = {
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "transaction_count": len(tx),
            "sum_tiyn": int(edges["sum_tiyn"].sum()),
            "sha256_files": input_hashes,
        }
        report = starter.build_report(
            priority, edges, summaries, metadata=metadata, parameters={},
        )
        # Model an older report produced before HA-17.1 added reason strings.
        for node in report["nodes"]:
            for matched_role in node.get("matched_roles", []):
                matched_role.pop("reason", None)
        json_payload = json.dumps(report, ensure_ascii=False, allow_nan=False) + "\n"
        starter.write_outputs(report, out_dir)
        (out_dir / "report.json").write_text(json_payload, encoding="utf-8")
        asset_dir = out_dir / "assets"
        asset_dir.mkdir()
        (asset_dir / "app.js").write_text("const reportPath = './report.json';\n", encoding="utf-8")
        (asset_dir / "app.css").write_text("body { color: #111; }\n", encoding="utf-8")
        (asset_dir / "vis-network.LICENSE.txt").write_text(
            "Local dependency license fixture\n", encoding="utf-8",
        )
        (out_dir / "report.html").write_text(
            "<!doctype html><html><head><link rel='stylesheet' href='./assets/app.css'>"
            "</head><body><script type='module' src='./assets/app.js'></script></body></html>\n",
            encoding="utf-8",
        )
        (out_dir / "index.html").write_bytes((out_dir / "report.html").read_bytes())
        release_paths = [
            "nodes_roles.csv", "clusters.csv", "top_nodes.csv", "report.json", "report.html", "index.html",
            "assets/app.js", "assets/app.css", "assets/vis-network.LICENSE.txt",
        ]
        output_hashes = {
            name: hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
            for name in release_paths
        }
        validation = {
            "status": "success",
            "schema_version": "1.0",
            "dataset": metadata,
            "output_sha256": output_hashes,
        }
        (out_dir / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, allow_nan=False), encoding="utf-8",
        )
        validate_csv_release(data_dir, out_dir)

        # Keep the score equal to the CSV value so only its JSON type can fail.
        malformed_report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        malformed_report["nodes"][0]["role_score"] = str(malformed_report["nodes"][0]["role_score"])
        malformed_text = json.dumps(malformed_report, ensure_ascii=False, allow_nan=False) + "\n"
        (out_dir / "report.json").write_text(malformed_text, encoding="utf-8")
        validation["output_sha256"]["report.json"] = hashlib.sha256(
            (out_dir / "report.json").read_bytes()
        ).hexdigest()
        (out_dir / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, allow_nan=False), encoding="utf-8",
        )
        try:
            validate_csv_release(data_dir, out_dir)
        except ContractFailure as exc:
            require("role_score" in str(exc) and "JSON score must be a number" in str(exc),
                    f"synthetic string role_score failed for the wrong reason: {exc}")
        else:
            raise ContractFailure("synthetic string role_score was accepted")


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
            # Primary distributor with no competing role.
            (106, 1, False, 0, 10, 0, 1_000_000, 0, 10, math.nan,
             0, 0.0, False, False, math.nan),
            # Primary transit with a balanced, observed input/output pair.
            (107, 2, False, 1, 1, 100_000, 100_000, 1, 1, 1.0,
             0, 0.0, False, False, 0),
            # Primary terminal; the boundary fixture above remains non-terminal.
            (108, 2, False, 2, 0, 100_000, 0, 2, 0, 0.0,
             0, 0.0, False, False, 3),
            # Coordinator whose why selects M and H as the two leading terms.
            (109, 1, False, 2, 2, 2, 1, 2, 2, 0.5,
             2, 0.9, False, False, 0),
        ],
        columns=[
            "gid", "depth", "is_seed", "in_deg", "out_deg", "in_tiyn", "out_tiyn",
            "in_tx", "out_tx", "pass_through", "seed_reach_count", "betweenness",
            "boundary", "isolated", "days_after_last_in",
        ],
    )


def test_roles_and_priority(starter: Any) -> None:
    role_frame, role_parameters = starter.assign_roles(synthetic_role_rows())
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
    expected_primary = {
        101: "coordinator", 102: "peripheral", 103: "consolidator",
        104: "peripheral", 105: "peripheral",
        106: "distributor", 107: "transit", 108: "terminal", 109: "coordinator",
    }
    for gid, expected_role in expected_primary.items():
        row = frame_record(role_frame, gid)
        require(row["role"] == expected_role,
                f"gid={gid}: expected primary role {expected_role}, got {row['role']!r}")
    require(set(role_frame["role"]) == ALLOWED_ROLES,
            "synthetic role fixture must cover all six primary roles")

    has_ha17_explanations = role_parameters.get("explanation_version") == "1.0"
    if has_ha17_explanations:
        require(role_parameters.get("matched_role_fields") == ["role", "support", "reason"],
                "role_parameters: HA-17.1 matched_role_fields must include reason")
        positive_b = [float(value) for value in role_frame["betweenness"]
                      if float(value) > 0]
        q90 = linear_quantile(positive_b, 0.90)
        close(role_parameters["coordinator"]["betweenness_threshold"], q90,
              "role_parameters coordinator Q90")
        ge = chr(0x2265)
        in_set = chr(0x2208)
        expected_reasons = {
            (101, "coordinator"): [
                f"S=2{ge}2", f"in_deg=3{ge}2", f"out_deg=10{ge}2",
                "B=0.9>0", f"Q90={format(q90, '.8g')}",
            ],
            (101, "distributor"): [f"out_deg=10{ge}10"],
            (101, "consolidator"): [f"in_deg=3{ge}3"],
            (101, "transit"): [
                "depth=1<4", "in_tiyn=1000000>0", "out_tiyn=1000000>0",
                f"r=1{in_set}[0.8,1.2]",
            ],
            (103, "consolidator"): [f"in_deg=3{ge}3"],
            (106, "distributor"): [f"out_deg=10{ge}10"],
            (107, "transit"): [
                "depth=2<4", "in_tiyn=100000>0", "out_tiyn=100000>0",
                f"r=1{in_set}[0.8,1.2]",
            ],
            (108, "terminal"): [
                "depth=2<4", "in_tiyn=100000>0", "out_deg=0", f"D=3{ge}2",
            ],
            (109, "coordinator"): [
                f"S=2{ge}2", f"in_deg=2{ge}2", f"out_deg=2{ge}2",
                "B=0.9>0", f"Q90={format(q90, '.8g')}",
            ],
        }
        for row in role_frame.itertuples(index=False):
            matches = row.matched_roles
            require(isinstance(matches, list),
                    f"gid={row.gid}: matched_roles must remain a list")
            if row.role == "peripheral":
                require(not matches, f"gid={row.gid}: peripheral must have no matched roles")
                continue
            primary_match = next(
                (match for match in matches if match.get("role") == row.role), None
            )
            require(primary_match is not None,
                    f"gid={row.gid}: primary role must appear in matched_roles")
            for match in matches:
                reason = match.get("reason")
                require(isinstance(reason, str) and reason.strip() and re.search(r"\d", reason),
                        f"gid={row.gid}: matched role reason must contain numeric facts")
                fragments = expected_reasons.get((int(row.gid), match["role"]))
                if fragments is not None:
                    for fragment in fragments:
                        require(fragment in reason,
                                f"gid={row.gid} {match['role']} reason misses {fragment!r}")

    prioritized, priority_parameters = starter.compute_priority(role_frame)
    if has_ha17_explanations:
        require(priority_parameters.get("explanation_version") == "1.0",
                "priority_parameters: explanation_version must be 1.0")
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
        if has_ha17_explanations:
            require("\u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435:" in why,
                    f"gid={row.gid}: HA-17.1 why must state a limitation")

    if has_ha17_explanations:
        peripheral = frame_record(prioritized, 105)
        peripheral_evidence = str(peripheral["evidence"]).casefold()
        for fragment in (
            "in_deg=0", "out_deg=0", "0.00",
            "\u0440\u043e\u043b\u044c \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0430",
            "\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u044c \u043d\u0435 \u043e\u0446\u0435\u043d\u0435\u043d\u0430",
        ):
            require(fragment in peripheral_evidence,
                    f"gid=105: peripheral evidence misses {fragment!r}")

        coordinator_why = str(frame_record(prioritized, 101)["why"])
        for fragment in (
            "max support distributor=0.675", "V=20000.00 KZT", "Q95(V>0)=",
            "\u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435:",
        ):
            require(fragment in coordinator_why,
                    f"gid=101: why misses max-support/volume/limitation detail {fragment!r}")
        coordinator_why_lower = coordinator_why.casefold()
        require("\u043e\u0441\u043d\u043e\u0432\u043d\u0430\u044f=coordinator" in coordinator_why_lower,
                "gid=101: why must distinguish max-support role from primary role")

        balanced = str(frame_record(prioritized, 109)["why"])
        for fragment in ("max support coordinator=0.45", "B=0.9", "Q95(B>0)="):
            require(fragment in balanced,
                    f"gid=109: why misses role/B threshold detail {fragment!r}")

        peripheral_why = str(peripheral["why"])
        require(peripheral_why.index("M=") < peripheral_why.index("A="),
                "gid=105: equal why contributions must retain M,A,C,H tie order")


def exact_json_gid(value: Any, label: str) -> int:
    require(isinstance(value, str) and re.fullmatch(r"-?\d+", value) is not None,
            f"{label}: gid must be a decimal JSON string")
    number = int(value)
    require(str(number) == value, f"{label}: gid must use canonical decimal spelling")
    return number


def amount_to_tiyn(value: Any, label: str) -> int:
    try:
        scaled = float(value) * 100.0
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContractFailure(f"{label}: amount is not numeric") from exc
    require(math.isfinite(scaled), f"{label}: amount must be finite")
    rounded = round(scaled)
    require(abs(scaled - rounded) <= 1e-6,
            f"{label}: amount is not exact to 0.01 KZT")
    return int(rounded)


def validate_p1_daily_profiles(
    report: dict[str, Any],
    nodes_by_gid: dict[str, dict[str, Any]],
    nodes: pd.DataFrame,
    tx: pd.DataFrame,
) -> None:
    observed_fields = {"n_seed_payers", "sync_payers_max", "fanout_burst_max"}
    present_by_node = [observed_fields & set(node) for node in nodes_by_gid.values()]
    has_daily_profiles = "daily_profiles_by_gid" in report
    if not any(present_by_node) and not has_daily_profiles:
        return  # A pre-HA-11 P0 payload remains a supported input.
    require(all(fields == observed_fields for fields in present_by_node),
            "report.json: optional observed node fields must be absent or present together")
    require(has_daily_profiles,
            "report.json: observed node fields require daily_profiles_by_gid")

    profiles = report["daily_profiles_by_gid"]
    require(isinstance(profiles, dict), "report.json: daily_profiles_by_gid must be an object")
    require(set(profiles) == set(nodes_by_gid),
            "report.json: daily_profiles_by_gid keys must match exact node gids")

    # Independent aggregation from raw transaction rows, not from the report's
    # edge/node aggregates. Duplicate rows increment transaction counts while
    # payer/payee counts remain unique within each node-day.
    calculated: dict[str, dict[str, dict[str, Any]]] = {
        gid: {} for gid in nodes_by_gid
    }
    direct_seed_payers: dict[str, set[int]] = {gid: set() for gid in nodes_by_gid}
    seed_ids = {int(row.gid) for row in nodes.itertuples(index=False) if bool(row.is_seed)}
    for row in tx.itertuples(index=False):
        src, dst = str(int(row.src)), str(int(row.dst))
        day = pd.Timestamp(row.date).date().isoformat()
        tiyn = amount_to_tiyn(row.sum_kzt, f"transaction {src}->{dst} on {day}")
        source_day = calculated[src].setdefault(day, {
            "date": day, "in_tiyn": 0, "out_tiyn": 0,
            "in_tx": 0, "out_tx": 0, "n_payers": set(), "n_payees": set(),
        })
        destination_day = calculated[dst].setdefault(day, {
            "date": day, "in_tiyn": 0, "out_tiyn": 0,
            "in_tx": 0, "out_tx": 0, "n_payers": set(), "n_payees": set(),
        })
        source_day["out_tiyn"] += tiyn
        source_day["out_tx"] += 1
        source_day["n_payees"].add(int(row.dst))
        destination_day["in_tiyn"] += tiyn
        destination_day["in_tx"] += 1
        destination_day["n_payers"].add(int(row.src))
        if int(row.src) in seed_ids:
            direct_seed_payers[dst].add(int(row.src))

    for gid, node in nodes_by_gid.items():
        records = profiles[gid]
        require(isinstance(records, list),
                f"gid={gid}: daily profile must be an array")
        expected = []
        for day in sorted(calculated[gid]):
            bucket = calculated[gid][day]
            expected.append({
                "date": day,
                "in_tiyn": bucket["in_tiyn"],
                "out_tiyn": bucket["out_tiyn"],
                "in_tx": bucket["in_tx"],
                "out_tx": bucket["out_tx"],
                "n_payers": len(bucket["n_payers"]),
                "n_payees": len(bucket["n_payees"]),
            })
        require(records == expected,
                f"gid={gid}: daily profile differs from independent transaction aggregation")
        for field in ("n_seed_payers", "sync_payers_max", "fanout_burst_max"):
            require(isinstance(node[field], int) and not isinstance(node[field], bool),
                    f"gid={gid}: {field} must be an integer")
        require(node["n_seed_payers"] == len(direct_seed_payers[gid]),
                f"gid={gid}: n_seed_payers differs from raw seed predecessors")
        require(node["sync_payers_max"] == max((row["n_payers"] for row in expected), default=0),
                f"gid={gid}: sync_payers_max differs from daily profile")
        require(node["fanout_burst_max"] == max((row["n_payees"] for row in expected), default=0),
                f"gid={gid}: fanout_burst_max differs from daily profile")


def validate_local_reference(reference: str, base_dir: Path, out_dir: Path, label: str) -> None:
    parsed = urlsplit(reference)
    require(not parsed.scheme and not parsed.netloc,
            f"{label}: external resource is not allowed ({reference})")
    path_text = unquote(parsed.path)
    if not path_text:
        return  # Fragment-only or query-only link.
    relative = PurePosixPath(path_text)
    require(not relative.is_absolute() and ".." not in relative.parts,
            f"{label}: resource path must stay inside the release ({reference})")
    resolved = (base_dir.joinpath(*relative.parts)).resolve()
    release_root = out_dir.resolve()
    require(resolved == release_root or release_root in resolved.parents,
            f"{label}: resource path escapes the release ({reference})")
    require(resolved.is_file() and resolved.stat().st_size > 0,
            f"{label}: local resource is missing or empty ({reference})")


def validate_release_assets(out_dir: Path, validation: dict[str, Any]) -> None:
    html_path = out_dir / "report.html"
    html = html_path.read_text(encoding="utf-8")
    require((out_dir / "index.html").read_text(encoding="utf-8") == html,
            "index.html must serve the same dashboard as report.html")
    require("report-data" not in html and "__REPORT_DATA__" not in html,
            "report.html must load the sibling report.json instead of embedding the payload")
    parser = HtmlResourceParser()
    parser.feed(html)
    require(bool(parser.references), "report.html must reference locally built UI assets")
    for tag, reference in parser.references:
        validate_local_reference(reference, out_dir, out_dir, f"report.html {tag}")

    asset_root = out_dir / "assets"
    require(asset_root.is_dir(), "release is missing the built assets/ directory")
    asset_files = sorted(path for path in asset_root.rglob("*") if path.is_file())
    require(bool(asset_files), "release assets/ must contain built resources")
    require((asset_root / "vis-network.LICENSE.txt").is_file(),
            "release is missing the local vis-network license")
    javascript_files = [path for path in asset_files if path.suffix.lower() in {".js", ".mjs"}]
    require(bool(javascript_files), "release assets/ must contain the built JavaScript UI")
    javascript_bundle = "\n".join(path.read_text(encoding="utf-8") for path in javascript_files)
    require("report.json" in javascript_bundle.lower(),
            "built JavaScript must request the sibling report.json")
    expected_paths = {
        "nodes_roles.csv", "clusters.csv", "top_nodes.csv", "report.json", "report.html", "index.html",
        *(path.relative_to(out_dir).as_posix() for path in asset_files),
    }

    # CSS may reference fonts/images as secondary local resources.
    for css_path in (path for path in asset_files if path.suffix.lower() == ".css"):
        css = css_path.read_text(encoding="utf-8")
        for match in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", css, flags=re.IGNORECASE):
            reference = match.group(2).strip()
            if reference and not reference.lower().startswith("data:"):
                validate_local_reference(reference, css_path.parent, out_dir, css_path.name)

    manifest = validation.get("output_sha256")
    require(isinstance(manifest, dict), "validation.json: output_sha256 must be an object")
    require(set(manifest) == expected_paths,
            "validation.json: output_sha256 must cover every release CSV, JSON, HTML and asset")
    for relative_name, expected_hash in manifest.items():
        relative = PurePosixPath(relative_name)
        require(not relative.is_absolute() and ".." not in relative.parts
                and "\\" not in relative_name,
                f"validation.json: invalid release path {relative_name!r}")
        artifact = out_dir.joinpath(*relative.parts)
        require(artifact.is_file() and artifact.stat().st_size > 0,
                f"validation.json: hashed artifact is missing or empty ({relative_name})")
        require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None,
                f"validation.json: invalid SHA-256 for {relative_name}")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        require(digest == expected_hash,
                f"validation.json: SHA-256 mismatch for {relative_name}")


def validate_report_release(
    data_dir: Path,
    out_dir: Path,
    raw_nodes: pd.DataFrame,
    raw_edges: pd.DataFrame,
    raw_tx: pd.DataFrame,
    nodes_csv: pd.DataFrame,
    clusters_csv: pd.DataFrame,
    top_csv: pd.DataFrame,
    validation: dict[str, Any],
) -> None:
    report = read_strict_json(out_dir / "report.json")
    require(report.get("schema_version") == "1.0", "report.json: schema_version must be 1.0")
    dataset = report.get("dataset")
    require(isinstance(dataset, dict), "report.json: dataset must be an object")
    nodes = report.get("nodes")
    edges = report.get("edges")
    clusters = report.get("clusters")
    top_nodes = report.get("top_nodes")
    require(all(isinstance(value, list) for value in (nodes, edges, clusters, top_nodes)),
            "report.json: nodes/edges/clusters/top_nodes must be arrays")
    expected_gids = {str(int(gid)) for gid in raw_nodes["gid"]}
    node_ids = [exact_json_gid(node.get("gid") if isinstance(node, dict) else None,
                               f"report.json nodes[{index}]") for index, node in enumerate(nodes)]
    require(len(node_ids) == len(set(node_ids)), "report.json: duplicate node gid")
    require(node_ids == sorted(node_ids), "report.json: nodes must be sorted by numeric gid")
    nodes_by_gid = {str(gid): node for gid, node in zip(node_ids, nodes)}
    require(set(nodes_by_gid) == expected_gids,
            "report.json: exact node gid set differs from nodes.parquet")
    validate_ha17_explanations(report, nodes_by_gid)
    for field, expected in (
        ("node_count", len(raw_nodes)), ("edge_count", len(raw_edges)),
        ("transaction_count", len(raw_tx)),
    ):
        require(dataset.get(field) == expected,
                f"report.json dataset.{field} differs from raw Parquet")
    source_hashes = dataset.get("sha256_files")
    require(isinstance(source_hashes, dict)
            and set(source_hashes) == {"nodes.parquet", "edges.parquet", "transactions.parquet"},
            "report.json dataset.sha256_files must name all three Parquet inputs")
    for filename, expected_hash in source_hashes.items():
        require(isinstance(expected_hash, str)
                and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None,
                f"report.json dataset.sha256_files has an invalid digest for {filename}")
        actual_hash = hashlib.sha256((data_dir / filename).read_bytes()).hexdigest()
        require(actual_hash == expected_hash,
                f"report.json dataset SHA-256 differs from input {filename}")
    require(validation.get("dataset") == dataset,
            "validation.json dataset must match report.json dataset")

    csv_rows = {str(row.gid): row for row in nodes_csv.itertuples(index=False)}
    for gid, node in nodes_by_gid.items():
        csv_row = csv_rows[gid]
        require(node.get("role") == str(csv_row.role),
                f"gid={gid}: report role differs from nodes_roles.csv")
        require(node.get("cluster_id") == int(csv_row.cluster_id),
                f"gid={gid}: report cluster differs from nodes_roles.csv")
        require(node.get("evidence") == str(csv_row.evidence),
                f"gid={gid}: report evidence differs from nodes_roles.csv")
        close_json_score(node.get("role_score"), float(csv_row.role_score),
                         f"gid={gid}: report role_score differs from nodes_roles.csv")
        close_json_score(node.get("priority_score"), float(csv_row.priority_score),
                         f"gid={gid}: report priority_score differs from nodes_roles.csv")

    expected_edges = {}
    for row in raw_edges.itertuples(index=False):
        src, dst = str(int(row.src)), str(int(row.dst))
        expected_edges[(src, dst)] = (
            amount_to_tiyn(row.sum_kzt, f"raw edge {src}->{dst}"), int(row.n_tx), int(row.depth)
        )
    actual_edges = {}
    for index, edge in enumerate(edges):
        require(isinstance(edge, dict), f"report.json edges[{index}] must be an object")
        src = str(exact_json_gid(edge.get("src"), f"report.json edges[{index}].src"))
        dst = str(exact_json_gid(edge.get("dst"), f"report.json edges[{index}].dst"))
        key = (src, dst)
        require(key not in actual_edges, f"report.json: duplicate edge {src}->{dst}")
        require(src in nodes_by_gid and dst in nodes_by_gid,
                f"report.json: edge {src}->{dst} has an unknown endpoint")
        amount = edge.get("sum_tiyn")
        count = edge.get("n_tx")
        depth = edge.get("depth")
        require(isinstance(amount, int) and not isinstance(amount, bool)
                and isinstance(count, int) and not isinstance(count, bool)
                and isinstance(depth, int) and not isinstance(depth, bool),
                f"report.json: edge {src}->{dst} amount/count/depth must be integers")
        actual_edges[key] = (amount, count, depth)
    require(actual_edges == expected_edges,
            "report.json edges differ from raw edges.parquet")
    require(dataset.get("sum_tiyn") == sum(value[0] for value in expected_edges.values()),
            "report.json dataset.sum_tiyn differs from raw edges.parquet")

    cluster_rows = {int(row.cluster_id): row for row in clusters_csv.itertuples(index=False)}
    report_clusters = {}
    for index, cluster in enumerate(clusters):
        require(isinstance(cluster, dict), f"report.json clusters[{index}] must be an object")
        cid = cluster.get("cluster_id")
        require(isinstance(cid, int) and not isinstance(cid, bool),
                f"report.json clusters[{index}].cluster_id must be an integer")
        require(cid not in report_clusters, f"report.json: duplicate cluster_id={cid}")
        report_clusters[cid] = cluster
    require(set(report_clusters) == set(cluster_rows),
            "report.json clusters do not match clusters.csv IDs")
    for cid, cluster in report_clusters.items():
        csv_row = cluster_rows[cid]
        for field in ("n_nodes", "n_seed"):
            require(cluster.get(field) == int(getattr(csv_row, field)),
                    f"cluster_id={cid}: report {field} differs from clusters.csv")
        require(cluster.get("sum_kzt_internal") == str(csv_row.sum_kzt_internal),
                f"cluster_id={cid}: report amount differs from clusters.csv")
        require(cluster.get("top_gids") == json.loads(str(csv_row.top_gids)),
                f"cluster_id={cid}: report top_gids differs from clusters.csv")
        require(cluster.get("hypothesis") == str(csv_row.hypothesis),
                f"cluster_id={cid}: report hypothesis differs from clusters.csv")

    validate_ha18_cluster_hypotheses(
        list(report_clusters.values()),
        nodes_by_gid,
        {gid: int(row.cluster_id) for gid, row in csv_rows.items()},
        raw_nodes,
        raw_edges,
    )

    require(len(top_nodes) == len(top_csv),
            "report.json top_nodes count differs from top_nodes.csv")
    for index, (record, csv_row) in enumerate(zip(top_nodes, top_csv.itertuples(index=False))):
        require(isinstance(record, dict), f"report.json top_nodes[{index}] must be an object")
        gid = str(exact_json_gid(record.get("gid"), f"report.json top_nodes[{index}].gid"))
        require(record.get("rank") == int(csv_row.rank) and gid == str(csv_row.gid),
                f"report.json top_nodes[{index}] rank/gid differs from top_nodes.csv")
        require(record.get("role") == str(csv_row.role) and record.get("why") == str(csv_row.why),
                f"gid={gid}: report top role/why differs from top_nodes.csv")
        close_json_score(record.get("priority_score"), float(csv_row.priority_score),
                         f"gid={gid}: report top priority differs from top_nodes.csv")

    validate_p1_daily_profiles(report, nodes_by_gid, raw_nodes, raw_tx)
    validate_release_assets(out_dir, validation)


HA18_LEADER_PREFIX = "Первый для проверки по P:"
HA18_LEADER_RE = re.compile(
    r"Первый для проверки по P: (?P<gid>-?\d+), роль (?P<role>[^;]+); "
    r"(?P<in_degree>\d+) плательщиков, (?P<out_degree>\d+) получателей; "
    r"вход (?P<in_amount>\d+\.\d{2}) KZT, выход (?P<out_amount>\d+\.\d{2}) KZT\."
)


def validate_ha18_cluster_hypotheses(
    cluster_records: list[dict[str, Any]],
    nodes_by_gid: dict[str, dict[str, Any]],
    cluster_by_gid: dict[str, int],
    raw_nodes: pd.DataFrame,
    raw_edges: pd.DataFrame,
) -> None:
    """Independently verify HA-18 leader text and preserved cluster facts."""
    seeds = {
        str(int(row.gid)): bool(row.is_seed)
        for row in raw_nodes.itertuples(index=False)
    }
    edges = [
        (str(int(row.src)), str(int(row.dst)),
         amount_to_tiyn(row.sum_kzt, f"raw edge {int(row.src)}->{int(row.dst)}"))
        for row in raw_edges.itertuples(index=False)
    ]
    for record in cluster_records:
        cid = int(record["cluster_id"])
        members = {gid for gid, member_cid in cluster_by_gid.items() if member_cid == cid}
        require(bool(members), f"cluster_id={cid}: HA-18 cluster has no node members")
        expected_seeds = sum(seeds[gid] for gid in members)
        expected_top = sorted(
            members,
            key=lambda gid: (-float(nodes_by_gid[gid]["priority_score"]), int(gid)),
        )[:5]
        top_gids = record.get("top_gids")
        require(isinstance(top_gids, list) and all(isinstance(gid, str) for gid in top_gids),
                f"cluster_id={cid}: HA-18 top_gids must be exact decimal strings")
        top_gids = [str(exact_json_gid(gid, f"cluster_id={cid} top_gids")) for gid in top_gids]
        require(top_gids == expected_top,
                f"cluster_id={cid}: HA-18 top_gids order/tie-break differs from P desc and numeric gid asc")

        cluster_edges = [edge for edge in edges
                         if edge[0] in members and edge[1] in members]
        expected_internal_tiyn = sum(edge[2] for edge in cluster_edges)
        expected_internal_kzt = f"{expected_internal_tiyn // 100}.{expected_internal_tiyn % 100:02d}"
        require(record.get("n_nodes") == len(members),
                f"cluster_id={cid}: HA-18 n_nodes differs from membership")
        require(record.get("n_seed") == expected_seeds,
                f"cluster_id={cid}: HA-18 n_seed differs from raw nodes")
        require(record.get("sum_kzt_internal") == expected_internal_kzt,
                f"cluster_id={cid}: HA-18 internal turnover differs from raw edges")

        hypothesis = str(record.get("hypothesis", ""))
        leader_gid = top_gids[0]
        leader_edges_in = [edge for edge in edges if edge[1] == leader_gid]
        leader_edges_out = [edge for edge in edges if edge[0] == leader_gid]
        is_isolate = len(members) == 1 and not leader_edges_in and not leader_edges_out

        if is_isolate:
            require(HA18_LEADER_PREFIX not in hypothesis,
                    f"cluster_id={cid}: isolated gid={leader_gid} must not claim a review leader")
            require("Один узел" in hypothesis and "без внутренних рёбер" in hypothesis
                    and "внутренний оборот 0.00 KZT" in hypothesis,
                    f"cluster_id={cid}: isolate text must preserve the absence-of-links summary")
        else:
            require(hypothesis.count(HA18_LEADER_PREFIX) == 1,
                    f"cluster_id={cid}: non-isolated group must name exactly one HA-18 leader")
            match = HA18_LEADER_RE.search(hypothesis)
            require(match is not None,
                    f"cluster_id={cid}: HA-18 leader details do not match the contract format")
            require(str(exact_json_gid(match.group("gid"), f"cluster_id={cid} leader gid")) == leader_gid,
                    f"cluster_id={cid}: HA-18 leader must equal top_gids[0]={leader_gid}")
            in_degree = len({edge[0] for edge in leader_edges_in})
            out_degree = len({edge[1] for edge in leader_edges_out})
            in_tiyn = sum(edge[2] for edge in leader_edges_in)
            out_tiyn = sum(edge[2] for edge in leader_edges_out)
            expected_facts = (
                nodes_by_gid[leader_gid]["role"], in_degree, out_degree,
                f"{in_tiyn // 100}.{in_tiyn % 100:02d}",
                f"{out_tiyn // 100}.{out_tiyn % 100:02d}",
            )
            actual_facts = (
                match.group("role"), int(match.group("in_degree")), int(match.group("out_degree")),
                match.group("in_amount"), match.group("out_amount"),
            )
            require(actual_facts == expected_facts,
                    f"gid={leader_gid}: HA-18 role/degrees/sums differ from raw graph")
            expected_edge_count = len({(edge[0], edge[1]) for edge in cluster_edges})
            base_summary = (
                f"{len(members)} узлов, {expected_seeds} seed, {expected_edge_count} внутренних рёбер, "
                f"{expected_internal_kzt} KZT"
            )
            require(base_summary in hypothesis,
                    f"cluster_id={cid}: HA-18 must preserve node/seed/internal-flow summary")

        if expected_seeds == 0:
            require("Отсутствие seed не означает безопасность." in hypothesis,
                    f"cluster_id={cid}: no-seed caveat must remain in hypothesis")


def test_ha18_hypothesis_fixtures(starter: Any) -> None:
    """Exercise HA-18 ties, cross-cluster observed flow, no-seed groups and isolates."""
    raw_nodes = pd.DataFrame({"gid": [2, 10, 50, 60, 80, 90],
                              "is_seed": [False, True, False, False, False, True]})
    raw_edges = pd.DataFrame(
        [(2, 10, 100.00), (10, 2, 200.00), (50, 60, 400.00),
         (60, 50, 50.00), (50, 2, 300.00)],
        columns=["src", "dst", "sum_kzt"],
    )
    raw_edges["n_tx"], raw_edges["depth"] = 1, 1
    raw_edges["sum_tiyn"] = raw_edges["sum_kzt"].map(
        lambda value: amount_to_tiyn(value, "HA-18 synthetic edge")
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(int(gid) for gid in raw_nodes["gid"])
    for edge in raw_edges.itertuples(index=False):
        graph.add_edge(int(edge.src), int(edge.dst), sum_tiyn=int(edge.sum_tiyn))

    features = pd.DataFrame([
        (2, 0, "coordinator", False, 0.9), (10, 0, "coordinator", True, 0.9),
        (50, 1, "distributor", False, 0.8), (60, 1, "transit", False, 0.4),
        (80, 2, "peripheral", False, 0.2), (90, 3, "peripheral", True, 0.1),
    ], columns=["gid", "cluster_id", "role", "is_seed", "priority_score"])
    summary = starter.summarize_clusters(graph, features)
    expected_numeric = {
        0: (2, 1, 30_000, ["2", "10"]),
        1: (2, 0, 45_000, ["50", "60"]),
        2: (1, 0, 0, ["80"]),
        3: (1, 1, 0, ["90"]),
    }
    records: list[dict[str, Any]] = []
    for row in summary.itertuples(index=False):
        expected_nodes, expected_seeds, expected_tiyn, expected_top = expected_numeric[int(row.cluster_id)]
        require(int(row.n_nodes) == expected_nodes and int(row.n_seed) == expected_seeds
                and int(row.sum_tiyn_internal) == expected_tiyn
                and [str(gid) for gid in row.top_gids] == expected_top,
                f"cluster_id={row.cluster_id}: HA-18 changed synthetic numeric baseline")
        records.append(
            {
                "cluster_id": int(row.cluster_id),
                "n_nodes": int(row.n_nodes),
                "n_seed": int(row.n_seed),
                "sum_kzt_internal": str(row.sum_kzt_internal),
                "top_gids": [str(gid) for gid in row.top_gids],
                "hypothesis": str(row.hypothesis),
            }
        )

    nodes_by_gid = {str(int(row.gid)): {"role": row.role, "priority_score": row.priority_score}
                    for row in features.itertuples(index=False)}
    cluster_by_gid = {str(int(row.gid)): int(row.cluster_id)
                      for row in features.itertuples(index=False)}
    validate_ha18_cluster_hypotheses(
        records, nodes_by_gid, cluster_by_gid, raw_nodes, raw_edges
    )


def validate_ha17_explanations(
    report: dict[str, Any],
    nodes_by_gid: dict[str, dict[str, Any]],
) -> None:
    """Require HA-17.1 details when versioned explanations are in the report."""
    parameters = report.get("parameters", {})
    require(isinstance(parameters, dict), "report.json parameters must be an object")
    role_parameters = parameters.get("role_parameters", {})
    priority_parameters = parameters.get("priority_parameters", {})
    require(isinstance(role_parameters, dict),
            "report.json parameters.role_parameters must be an object")
    require(isinstance(priority_parameters, dict),
            "report.json parameters.priority_parameters must be an object")

    has_reason = any(
        isinstance(match, dict) and "reason" in match
        for node in nodes_by_gid.values()
        for match in (
            node.get("matched_roles")
            if isinstance(node.get("matched_roles"), list) else []
        )
    )
    role_version = role_parameters.get("explanation_version")
    priority_version = priority_parameters.get("explanation_version")
    if role_version is None and priority_version is None and not has_reason:
        return  # Pre-HA-17 report payloads remain supported.

    require(role_version == "1.0" and priority_version == "1.0",
            "report.json HA-17.1 role/priority explanation versions must both be 1.0")
    weights = priority_parameters.get("weights")
    require(isinstance(weights, dict) and set(weights) == {"M", "A", "C", "H"},
            "report.json priority weights must define M/A/C/H")
    for component, weight in weights.items():
        require(isinstance(weight, (int, float)) and not isinstance(weight, bool)
                and math.isfinite(float(weight)),
                f"report.json priority weight {component} must be finite")

    def number_text(value: Any) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "\u2014"
        return format(float(value), ".8g")

    for gid, node in nodes_by_gid.items():
        matches = node.get("matched_roles")
        require(isinstance(matches, list),
                f"gid={gid}: matched_roles must be an array")
        evidence = node.get("evidence")
        require(isinstance(evidence, str) and evidence.strip()
                and len(evidence) <= 200 and re.search(r"\d", evidence),
                f"gid={gid}: HA-17.1 evidence must be numeric and <=200 characters")
        if node.get("role") == "peripheral":
            require(not matches, f"gid={gid}: peripheral cannot have matched roles")
            continue

        primary_matches = []
        for position, match in enumerate(matches):
            require(isinstance(match, dict),
                    f"gid={gid}: matched_roles[{position}] must be an object")
            role = match.get("role")
            support = match.get("support")
            reason = match.get("reason")
            require(isinstance(role, str) and role.strip(),
                    f"gid={gid}: matched_roles[{position}].role must not be empty")
            require(isinstance(support, (int, float)) and not isinstance(support, bool)
                    and math.isfinite(float(support)) and 0 <= float(support) <= 1,
                    f"gid={gid}: matched_roles[{position}].support must be in [0, 1]")
            require(isinstance(reason, str) and reason.strip() and re.search(r"\d", reason),
                    f"gid={gid}: matched_roles[{position}].reason must contain numeric facts")
            if role == node.get("role"):
                primary_matches.append(match)
        require(len(primary_matches) == 1,
                f"gid={gid}: primary role must appear exactly once in matched_roles")
        require(primary_matches[0]["reason"] in evidence,
                f"gid={gid}: evidence must include the primary role's reason")

    top_nodes = report.get("top_nodes")
    require(isinstance(top_nodes, list), "report.json top_nodes must be an array")
    for position, top_node in enumerate(top_nodes):
        require(isinstance(top_node, dict),
                f"report.json top_nodes[{position}] must be an object")
        why = top_node.get("why")
        require(isinstance(why, str) and why.strip() and re.search(r"\d", why)
                and "\u041e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0435:" in why,
                f"report.json top_nodes[{position}].why must include numeric rationale and a limitation")
        gid = str(exact_json_gid(top_node.get("gid"),
                                 f"report.json top_nodes[{position}].gid"))
        node = nodes_by_gid[gid]
        components = node.get("score_components")
        require(isinstance(components, dict) and set(components) == {"M", "A", "C", "H"},
                f"gid={gid}: score_components must define M/A/C/H")
        for component, value in components.items():
            require(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(float(value)) and 0 <= float(value) <= 1,
                    f"gid={gid}: score component {component} must be finite in [0, 1]")
        leading = sorted(
            ((float(weights[key]) * float(components[key]), key)
             for key in ("M", "A", "C", "H")),
            key=lambda item: (-item[0], ("M", "A", "C", "H").index(item[1])),
        )[:2]
        top_priority = top_node.get("priority_score")
        require(isinstance(top_priority, (int, float)) and not isinstance(top_priority, bool)
                and math.isfinite(float(top_priority)),
                f"gid={gid}: top priority must be finite")
        require(f"P={float(top_priority):.6f}" in why,
                f"gid={gid}: why must show the unaltered score to six decimals")
        for _contribution, component in leading:
            if component == "M":
                matches = node["matched_roles"]
                if matches:
                    maximum = max(matches, key=lambda item: float(item["support"]))
                    role_text = str(maximum["role"])
                    support_text = number_text(maximum["support"])
                    expected = f"max support {role_text}={support_text}"
                    if role_text != node.get("role"):
                        expected += f"; \u043e\u0441\u043d\u043e\u0432\u043d\u0430\u044f={node.get('role')}"
                else:
                    expected = "max support \u043d\u0435\u0442 \u0441\u043e\u0432\u043f\u0430\u0432\u0448\u0438\u0445 \u0440\u043e\u043b\u0435\u0439=0"
            elif component == "A":
                volume = (int(node["in_tiyn"]) + int(node["out_tiyn"])) / 100.0
                q95 = number_text(priority_parameters.get("volume_q95_kzt"))
                expected = f"V={volume:.2f} KZT"
                require(f"Q95(V>0)={q95} KZT" in why,
                        f"gid={gid}: why must name the volume threshold")
            elif component == "C":
                expected = f"S={int(node['seed_reach_count'])}/5"
            else:
                b_value = number_text(node.get("betweenness"))
                q95 = number_text(priority_parameters.get("betweenness_q95_positive"))
                expected = f"B={b_value}"
                require(f"Q95(B>0)={q95}" in why,
                        f"gid={gid}: why must name the betweenness threshold")
            require(expected in why,
                    f"gid={gid}: why does not explain leading component {component} with {expected!r}")


def validate_csv_release(data_dir: Path, out_dir: Path) -> None:
    for filename in (*REQUIRED_COLUMNS, "report.json", "report.html", "index.html", "validation.json"):
        require((out_dir / filename).is_file(),
                f"missing release artifact {out_dir / filename}")

    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    edges = pd.read_parquet(data_dir / "edges.parquet")
    tx = pd.read_parquet(data_dir / "transactions.parquet")
    tx["date"] = pd.to_datetime(tx["date"])
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

    validation = read_strict_json(out_dir / "validation.json")
    require(validation.get("status") == "success",
            "validation.json: release status must be success")
    require(validation.get("schema_version") == "1.0",
            "validation.json: schema_version must be 1.0")
    validate_report_release(data_dir, out_dir, nodes, edges, tx,
                            nodes_csv, clusters_csv, top_csv, validation)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Jigas output contracts")
    parser.add_argument("--data", type=Path, default=Path("./data"), help="folder with the three Parquet files")
    parser.add_argument("--out", type=Path, default=Path("./out"), help="folder with the current pipeline output")
    args = parser.parse_args()

    try:
        import starter

        test_graph_features_and_report(starter)
        test_ha18_hypothesis_fixtures(starter)
        test_observed_daily_profiles(starter)
        test_release_json_compatibility(starter)
        test_roles_and_priority(starter)
        validate_csv_release(args.data, args.out)
    except ContractFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - preserve one-line, non-data diagnostic for reviewers.
        print(f"FAIL: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PASS: synthetic P0/P1 graphs, exact-ID JSON/CSV/assets contract, and current release outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
