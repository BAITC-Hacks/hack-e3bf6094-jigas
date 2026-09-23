#!/usr/bin/env python3
"""HackAlem AI starter helpers for transaction-network analysis.

The module loads and validates the provided Parquet tables, builds the full
directed graph, and calculates deterministic node features and heuristics.
The CLI keeps the provided --data and --out interface while output integration
is completed by the remaining implementation tasks.
"""

import argparse
from collections import deque
from pathlib import Path
import time

import numpy as np
import pandas as pd
import networkx as nx

ROLES = ["coordinator", "distributor", "consolidator", "transit", "terminal", "peripheral"]
LOUVAIN_RESOLUTION = 1.0
LOUVAIN_SEED = 42


# ---------------------------------------------------------------- загрузка

def load(data_dir: Path):
    edges = pd.read_parquet(data_dir / "edges.parquet")
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    tx = pd.read_parquet(data_dir / "transactions.parquet")
    tx["date"] = pd.to_datetime(tx["date"])
    return edges, nodes, tx


def _require_columns(frame, table, columns):
    if not isinstance(frame, pd.DataFrame):
        raise ValueError(f"{table} must be a pandas DataFrame")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{table} is missing required columns: {', '.join(missing)}")
    null_columns = [column for column in columns if frame[column].isna().any()]
    if null_columns:
        raise ValueError(f"{table} has missing values in: {', '.join(null_columns)}")


def _require_integer_columns(frame, table, columns):
    for column in columns:
        if not pd.api.types.is_integer_dtype(frame[column].dtype):
            raise ValueError(f"{table}.{column} must use an integer dtype; float IDs/counts are rejected")
        if len(frame):
            lower = int(frame[column].min())
            upper = int(frame[column].max())
            if lower < np.iinfo(np.int64).min or upper > np.iinfo(np.int64).max:
                raise ValueError(f"{table}.{column} must fit signed int64")


def _money_to_tiyn(frame, table, *, minimum=None):
    column = "sum_kzt"
    if not pd.api.types.is_numeric_dtype(frame[column].dtype) or pd.api.types.is_bool_dtype(frame[column].dtype):
        raise ValueError(f"{table}.sum_kzt must be numeric")
    values = frame[column].to_numpy(dtype=np.float64, na_value=np.nan)
    if not np.isfinite(values).all():
        raise ValueError(f"{table}.sum_kzt must contain only finite values")
    if (values <= 0).any():
        raise ValueError(f"{table}.sum_kzt must be positive")
    if minimum is not None and (values < minimum).any():
        raise ValueError(f"{table}.sum_kzt transactions must be at least {minimum} KZT")

    scaled = values * 100.0
    rounded = np.rint(scaled)
    # Allow at most 1e-6 tiyn for binary float representation; comparisons below use integers.
    if (np.abs(scaled - rounded) > 1e-6).any():
        raise ValueError(f"{table}.sum_kzt must be exact to 0.01 KZT within 1e-6 tiyn")
    if (rounded >= float(2**63)).any():
        raise ValueError(f"{table}.sum_kzt exceeds the signed int64 tiyn range")
    return rounded.astype(np.int64)


def sanity_check(edges, nodes, tx):
    """Validate the input tables and attach exact integer amounts in tiyn."""
    _require_columns(nodes, "nodes", ["gid", "depth", "is_seed"])
    _require_columns(edges, "edges", ["src", "dst", "sum_kzt", "n_tx", "depth"])
    _require_columns(tx, "transactions", ["src", "dst", "date", "sum_kzt"])

    _require_integer_columns(nodes, "nodes", ["gid", "depth"])
    _require_integer_columns(edges, "edges", ["src", "dst", "n_tx", "depth"])
    _require_integer_columns(tx, "transactions", ["src", "dst"])
    if not pd.api.types.is_bool_dtype(nodes["is_seed"].dtype):
        raise ValueError("nodes.is_seed must use a boolean dtype")

    if nodes["gid"].duplicated().any():
        raise ValueError("nodes.gid values must be unique")
    if edges.duplicated(["src", "dst"]).any():
        raise ValueError("edges (src, dst) pairs must be unique")
    if not nodes["depth"].between(0, 4).all():
        raise ValueError("nodes.depth must be between 0 and 4")
    if not edges["depth"].between(1, 4).all():
        raise ValueError("edges.depth must be between 1 and 4")
    if not edges["n_tx"].gt(0).all():
        raise ValueError("edges.n_tx must be a positive integer")
    if not nodes["is_seed"].eq(nodes["depth"].eq(0)).all():
        raise ValueError("nodes.is_seed must be true exactly when nodes.depth is 0")

    try:
        dates = pd.to_datetime(tx["date"], errors="coerce")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("transactions.date could not be parsed") from exc
    if dates.isna().any():
        raise ValueError("transactions.date contains an invalid date")
    calendar_dates = [value.date() for value in dates]
    if any(value.year != 2026 or value.month != 7 for value in calendar_dates):
        raise ValueError("transactions.date must fall within July 2026")

    node_ids = {int(value) for value in nodes["gid"]}
    edge_endpoints = {int(value) for value in edges["src"]} | {int(value) for value in edges["dst"]}
    tx_endpoints = {int(value) for value in tx["src"]} | {int(value) for value in tx["dst"]}
    unknown_endpoints = (edge_endpoints | tx_endpoints) - node_ids
    if unknown_endpoints:
        raise ValueError(f"edge/transaction endpoints must all exist in nodes ({len(unknown_endpoints)} unknown endpoint(s))")

    edge_tiyn = _money_to_tiyn(edges, "edges")
    tx_tiyn = _money_to_tiyn(tx, "transactions", minimum=5000)

    edges_by_pair = {
        (int(src), int(dst)): (int(amount), int(count))
        for src, dst, amount, count in zip(edges["src"], edges["dst"], edge_tiyn, edges["n_tx"])
    }
    tx_by_pair = {}
    for src, dst, amount in zip(tx["src"], tx["dst"], tx_tiyn):
        key = (int(src), int(dst))
        total, count = tx_by_pair.get(key, (0, 0))
        total += int(amount)
        count += 1
        if total > np.iinfo(np.int64).max:
            raise ValueError("transactions aggregate exceeds the signed int64 tiyn range")
        tx_by_pair[key] = (total, count)

    missing_transactions = set(edges_by_pair) - set(tx_by_pair)
    missing_edges = set(tx_by_pair) - set(edges_by_pair)
    if missing_transactions or missing_edges:
        raise ValueError(
            "edges and transactions have different (src, dst) pairs "
            f"({len(missing_transactions)} edge pair(s) without transactions, "
            f"{len(missing_edges)} transaction pair(s) without edges)"
        )

    sum_mismatches = sum(edges_by_pair[pair][0] != tx_by_pair[pair][0] for pair in edges_by_pair)
    if sum_mismatches:
        raise ValueError(f"edges.sum_tiyn does not match transaction totals for {sum_mismatches} pair(s)")
    count_mismatches = sum(edges_by_pair[pair][1] != tx_by_pair[pair][1] for pair in edges_by_pair)
    if count_mismatches:
        raise ValueError(f"edges.n_tx does not match transaction counts for {count_mismatches} pair(s)")

    # Normalize only after every check has passed; duplicate transaction rows remain separate.
    edges["sum_tiyn"] = edge_tiyn
    tx["sum_tiyn"] = tx_tiyn
    tx["date"] = dates
    isolates = node_ids - edge_endpoints
    seed_count = int(nodes["is_seed"].sum())
    print("Input validation passed: "
          f"{len(nodes)} nodes, {len(edges)} edges, {len(tx)} transactions, "
          f"{seed_count} seeds, {len(isolates)} isolates, "
          f"{sum(map(int, edge_tiyn))} tiyn")
    return isolates


# ---------------------------------------------------------------- граф

def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    """Build a full directed graph from validated nodes and aggregated edges."""
    G = nx.DiGraph()
    for row in nodes[["gid", "depth", "is_seed"]].itertuples(index=False):
        G.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    for row in edges[["src", "dst", "sum_tiyn", "n_tx", "depth"]].itertuples(index=False):
        G.add_edge(
            int(row.src),
            int(row.dst),
            sum_tiyn=int(row.sum_tiyn),
            n_tx=int(row.n_tx),
            depth=int(row.depth),
        )
    return G


def basic_features(G: nx.DiGraph, nodes: pd.DataFrame) -> pd.DataFrame:
    """Compute exact directed degree, flow, boundary, and isolation features."""
    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())
    in_tiyn = dict(G.in_degree(weight="sum_tiyn"))
    out_tiyn = dict(G.out_degree(weight="sum_tiyn"))
    in_tx = dict(G.in_degree(weight="n_tx"))
    out_tx = dict(G.out_degree(weight="n_tx"))

    df = nodes[["gid", "depth", "is_seed"]].copy()
    df["in_deg"] = df["gid"].map(in_deg).astype("int64")
    df["out_deg"] = df["gid"].map(out_deg).astype("int64")
    df["in_tiyn"] = df["gid"].map(in_tiyn).astype("int64")
    df["out_tiyn"] = df["gid"].map(out_tiyn).astype("int64")
    df["in_tx"] = df["gid"].map(in_tx).astype("int64")
    df["out_tx"] = df["gid"].map(out_tx).astype("int64")
    df["pass_through"] = np.divide(
        df["out_tiyn"].to_numpy(dtype=np.float64),
        df["in_tiyn"].to_numpy(dtype=np.float64),
        out=np.full(len(df), np.nan, dtype=np.float64),
        where=df["in_tiyn"].to_numpy(dtype=np.int64) != 0,
    )
    df["boundary"] = df["depth"].eq(4) & df["out_deg"].eq(0)
    df["isolated"] = df["in_deg"].eq(0) & df["out_deg"].eq(0)
    return df


def enrich_features(G: nx.DiGraph, df: pd.DataFrame, tx: pd.DataFrame) -> pd.DataFrame:
    """Add bounded seed reachability, exact betweenness, and inbound dates."""
    graph_ids = set(G.nodes)
    frame_ids = {int(gid) for gid in df["gid"]}
    if frame_ids != graph_ids:
        raise ValueError("feature rows must cover exactly the graph nodes")

    bfs_started = time.perf_counter()
    reach_count = {gid: 0 for gid in G.nodes}
    seeds = [int(gid) for gid, is_seed in zip(df["gid"], df["is_seed"]) if bool(is_seed)]
    for seed in seeds:
        distances = {seed: 0}
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            distance = distances[current]
            if distance == 4:
                continue
            for neighbor in G.successors(current):
                if neighbor not in distances:
                    distances[neighbor] = distance + 1
                    queue.append(neighbor)
        for gid, distance in distances.items():
            if gid != seed and 1 <= distance <= 4:
                reach_count[gid] += 1
    bfs_seconds = time.perf_counter() - bfs_started

    betweenness_started = time.perf_counter()
    betweenness = nx.betweenness_centrality(
        G,
        normalized=True,
        endpoints=False,
        weight=None,
    )
    betweenness_seconds = time.perf_counter() - betweenness_started

    if "dst" not in tx.columns or "date" not in tx.columns:
        raise ValueError("transactions must include dst and date to calculate last_in")
    dates = pd.to_datetime(tx["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("transactions.date contains an invalid date")
    incoming = pd.DataFrame({"gid": tx["dst"].to_numpy(), "last_in": dates.dt.normalize().to_numpy()})
    last_in_by_gid = incoming.groupby("gid", sort=False)["last_in"].max()

    enriched = df.copy()
    enriched["seed_reach_count"] = enriched["gid"].map(reach_count).astype("int64")
    enriched["betweenness"] = enriched["gid"].map(betweenness).astype("float64")
    enriched["last_in"] = enriched["gid"].map(last_in_by_gid)
    enriched["days_after_last_in"] = (
        pd.Timestamp("2026-07-31") - enriched["last_in"]
    ).dt.days.astype("Int64")

    print(f"Feature timings: seed_bfs={bfs_seconds:.3f}s, exact_betweenness={betweenness_seconds:.3f}s")
    return enriched


def assign_roles(df: pd.DataFrame):
    """Apply the documented role rules and return their concrete parameters."""
    required = [
        "seed_reach_count", "betweenness", "in_deg", "out_deg", "in_tiyn",
        "out_tiyn", "pass_through", "depth", "is_seed", "days_after_last_in",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"role assignment is missing required features: {', '.join(missing)}")

    positive_b = pd.to_numeric(df["betweenness"], errors="coerce")
    positive_b = positive_b[np.isfinite(positive_b) & positive_b.gt(0)]
    q90_positive = (
        float(np.quantile(positive_b.to_numpy(dtype=np.float64), 0.90, method="linear"))
        if len(positive_b)
        else None
    )

    caps = {
        "coordinator": 0.60,
        "distributor": 0.90,
        "consolidator": 0.90,
        "transit": 0.65,
        "terminal": 0.50,
    }
    role_parameters = {
        "precedence": list(ROLES),
        "quantile_method": "linear",
        "coordinator": {
            "seed_reach_count_min": 2,
            "in_deg_min": 2,
            "out_deg_min": 2,
            "betweenness_positive_quantile": 0.90,
            "betweenness_threshold": q90_positive,
            "support_cap": caps["coordinator"],
            "u": "min(1, seed_reach_count / 4)",
        },
        "distributor": {
            "out_deg_min": 10,
            "support_cap": caps["distributor"],
            "u": "min(1, out_deg / 20)",
        },
        "consolidator": {
            "in_deg_min": 3,
            "support_cap": caps["consolidator"],
            "u": "min(1, in_deg / 6)",
        },
        "transit": {
            "is_seed": False,
            "depth_max_exclusive": 4,
            "pass_through_min": 0.8,
            "pass_through_max": 1.2,
            "support_cap": caps["transit"],
            "u": "max(0, 1 - abs(pass_through - 1) / 0.2)",
        },
        "terminal": {
            "is_seed": False,
            "depth_max_exclusive": 4,
            "in_tiyn_min_exclusive": 0,
            "out_deg": 0,
            "days_after_last_in_min": 2,
            "support_cap": caps["terminal"],
            "u": "min(1, days_after_last_in / 7)",
        },
        "support_formula": "cap * (0.5 + 0.5 * u)",
    }

    primary_roles = []
    role_scores = []
    all_matches = []

    def add_match(matches, role, u):
        support = caps[role] * (0.5 + 0.5 * min(1.0, max(0.0, float(u))))
        matches.append({"role": role, "support": float(support)})

    for row in df.itertuples(index=False):
        matches = []
        seed_reach = int(row.seed_reach_count)
        betweenness = float(row.betweenness) if pd.notna(row.betweenness) else np.nan
        in_deg = int(row.in_deg)
        out_deg = int(row.out_deg)
        in_tiyn = int(row.in_tiyn)
        out_tiyn = int(row.out_tiyn)
        depth = int(row.depth)
        is_seed = bool(row.is_seed)

        if (
            q90_positive is not None
            and seed_reach >= 2
            and in_deg >= 2
            and out_deg >= 2
            and np.isfinite(betweenness)
            and betweenness > 0
            and betweenness >= q90_positive
        ):
            add_match(matches, "coordinator", min(1.0, seed_reach / 4.0))

        if out_deg >= 10:
            add_match(matches, "distributor", min(1.0, out_deg / 20.0))

        if in_deg >= 3:
            add_match(matches, "consolidator", min(1.0, in_deg / 6.0))

        ratio = row.pass_through
        if (
            not is_seed
            and depth < 4
            and in_tiyn > 0
            and out_tiyn > 0
            and pd.notna(ratio)
            and np.isfinite(float(ratio))
            and 0.8 <= float(ratio) <= 1.2
        ):
            u = max(0.0, 1.0 - abs(float(ratio) - 1.0) / 0.2)
            add_match(matches, "transit", u)

        days_after = row.days_after_last_in
        if (
            not is_seed
            and depth < 4
            and in_tiyn > 0
            and out_deg == 0
            and pd.notna(days_after)
            and int(days_after) >= 2
        ):
            add_match(matches, "terminal", min(1.0, int(days_after) / 7.0))

        if matches:
            primary_roles.append(matches[0]["role"])
            role_scores.append(matches[0]["support"])
            all_matches.append(matches)
        else:
            primary_roles.append("peripheral")
            role_scores.append(0.0)
            all_matches.append([])

    result = df.copy()
    result["role"] = primary_roles
    result["role_score"] = np.asarray(role_scores, dtype=np.float64)
    result["matched_roles"] = all_matches
    return result, role_parameters


def compute_priority(df: pd.DataFrame):
    """Score nodes from normalized role, volume, reach, and betweenness signals."""
    required = [
        "gid", "role", "role_score", "matched_roles", "in_tiyn", "out_tiyn",
        "seed_reach_count", "betweenness", "boundary", "isolated", "is_seed",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"priority calculation is missing required features: {', '.join(missing)}")

    weights = {"M": 0.35, "A": 0.30, "C": 0.20, "H": 0.15}
    volumes_kzt = np.asarray(
        [(int(in_tiyn) + int(out_tiyn)) / 100.0 for in_tiyn, out_tiyn in zip(df["in_tiyn"], df["out_tiyn"])],
        dtype=np.float64,
    )
    if not np.isfinite(volumes_kzt).all() or (volumes_kzt < 0).any():
        raise ValueError("node volumes must be finite and non-negative")

    positive_volumes = volumes_kzt[volumes_kzt > 0]
    q95_volume_kzt = (
        float(np.quantile(positive_volumes, 0.95, method="linear"))
        if len(positive_volumes)
        else None
    )
    betweenness_values = pd.to_numeric(df["betweenness"], errors="coerce").to_numpy(dtype=np.float64)
    if not np.isfinite(betweenness_values).all() or (betweenness_values < 0).any():
        raise ValueError("betweenness must contain finite non-negative values")
    positive_betweenness = betweenness_values[betweenness_values > 0]
    q95_betweenness = (
        float(np.quantile(positive_betweenness, 0.95, method="linear"))
        if len(positive_betweenness)
        else None
    )

    role_strength = []
    for matches in df["matched_roles"]:
        supports = []
        for match in matches or []:
            support = float(match["support"])
            if not np.isfinite(support) or not 0 <= support <= 1:
                raise ValueError("matched role support must be finite and within [0, 1]")
            supports.append(support)
        role_strength.append(max(supports, default=0.0))
    M = np.asarray(role_strength, dtype=np.float64)

    if q95_volume_kzt is None or q95_volume_kzt <= 0:
        A = np.zeros(len(df), dtype=np.float64)
    else:
        denominator = float(np.log1p(q95_volume_kzt))
        A = np.clip(np.log1p(volumes_kzt) / denominator, 0.0, 1.0)

    reach = pd.to_numeric(df["seed_reach_count"], errors="raise").to_numpy(dtype=np.float64)
    if not np.isfinite(reach).all() or (reach < 0).any():
        raise ValueError("seed_reach_count must contain finite non-negative values")
    C = np.clip(reach / 5.0, 0.0, 1.0)

    if q95_betweenness is None or q95_betweenness <= 0:
        H = np.zeros(len(df), dtype=np.float64)
    else:
        H = np.clip(betweenness_values / q95_betweenness, 0.0, 1.0)

    components = {"M": M, "A": A, "C": C, "H": H}
    priority = np.clip(
        weights["M"] * M + weights["A"] * A + weights["C"] * C + weights["H"] * H,
        0.0,
        1.0,
    )

    component_labels = {
        "M": "поддержка роли",
        "A": "наблюдаемый объём",
        "C": "охват seed",
        "H": "посредническая центральность",
    }
    evidence = []
    why = []
    component_rows = []
    for position, row in enumerate(df.itertuples(index=False)):
        values = {key: float(components[key][position]) for key in ("M", "A", "C", "H")}
        component_rows.append(values)
        gid = int(row.gid)
        volume = float(volumes_kzt[position])
        reach_count = int(row.seed_reach_count)
        betweenness = float(betweenness_values[position])

        if bool(row.boundary):
            limitation = "граф заканчивается на depth=4"
        elif bool(row.isolated):
            limitation = "нет наблюдаемых рёбер"
        elif bool(row.is_seed):
            limitation = "входящие связи seed могут быть неполными"
        elif reach_count == 0:
            limitation = "seed не достигнут за 1–4 шага"
        else:
            limitation = "учтены только наблюдаемые переводы"

        evidence_text = (
            f"{row.role}; P={priority[position]:.3f}; M={values['M']:.3f}, "
            f"A={values['A']:.3f}, C={values['C']:.3f}, H={values['H']:.3f}; "
            f"V={volume:.2f} KZT, S={reach_count}, B={betweenness:.4g}. "
            f"Ограничение: {limitation}."
        )
        if len(evidence_text) > 200:
            raise ValueError(f"evidence for gid {gid} exceeds 200 characters")
        evidence.append(evidence_text)

        if priority[position] == 0:
            why_text = f"P=0: M=A=C=H=0. Ограничение: {limitation}."
        else:
            contributions = sorted(
                ((weights[key] * values[key], key) for key in ("M", "A", "C", "H")),
                key=lambda item: (-item[0], ("M", "A", "C", "H").index(item[1])),
            )[:2]
            first, second = contributions
            why_text = (
                f"P={priority[position]:.3f}; вклад: {component_labels[first[1]]}={first[0]:.3f}, "
                f"{component_labels[second[1]]}={second[0]:.3f}. Ограничение: {limitation}."
            )
        why.append(why_text)

    result = df.copy()
    result["priority_score"] = priority
    result["score_components"] = component_rows
    result["evidence"] = evidence
    result["why"] = why
    result = result.sort_values(
        ["priority_score", "gid"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    priority_parameters = {
        "rule_version": "1.0",
        "weights": weights,
        "volume_definition": "(in_tiyn + out_tiyn) / 100, in KZT",
        "volume_quantile": 0.95,
        "volume_quantile_method": "linear",
        "volume_q95_kzt": q95_volume_kzt,
        "component_formulas": {
            "M": "max(matched_roles.support), or 0 when there are no matches",
            "A": "min(1, log1p(V_kzt) / log1p(Q95(V_kzt > 0)))",
            "C": "min(1, seed_reach_count / 5)",
            "H": "min(1, betweenness / Q95(betweenness > 0))",
        },
        "betweenness_quantile": 0.95,
        "betweenness_quantile_method": "linear",
        "betweenness_q95_positive": q95_betweenness,
        "priority_formula": "0.35*M + 0.30*A + 0.20*C + 0.15*H",
        "sort_order": ["priority_score desc (unrounded)", "numeric gid asc"],
    }
    return result, priority_parameters


def _undirected_projection(G: nx.DiGraph) -> nx.Graph:
    """Project directed edge amounts into undirected KZT weights."""
    projection = nx.Graph()
    projection.add_nodes_from(sorted(int(gid) for gid in G.nodes))

    weight_tiyn_by_pair = {}
    for src, dst, data in sorted(G.edges(data=True), key=lambda edge: (int(edge[0]), int(edge[1]))):
        if "sum_tiyn" not in data:
            raise ValueError("graph edges must include integer sum_tiyn")
        amount = data["sum_tiyn"]
        if isinstance(amount, bool) or not isinstance(amount, (int, np.integer)):
            raise ValueError("graph edge sum_tiyn must be an integer")
        amount = int(amount)
        if amount <= 0:
            raise ValueError("graph edge sum_tiyn must be positive")
        pair = (min(int(src), int(dst)), max(int(src), int(dst)))
        weight_tiyn_by_pair[pair] = weight_tiyn_by_pair.get(pair, 0) + amount

    for (src, dst), amount_tiyn in sorted(weight_tiyn_by_pair.items()):
        projection.add_edge(src, dst, weight=amount_tiyn / 100.0)
    return projection


def cluster_nodes(G: nx.DiGraph) -> dict[int, int]:
    """Cluster the undirected projection and assign reproducible numeric IDs."""
    projection = _undirected_projection(G)
    nonisolates = sorted(gid for gid, degree in projection.degree() if degree > 0)

    groups = []
    if nonisolates:
        active_projection = projection.subgraph(nonisolates).copy()
        for community in nx.community.louvain_communities(
            active_projection,
            weight="weight",
            resolution=LOUVAIN_RESOLUTION,
            seed=LOUVAIN_SEED,
        ):
            community_graph = active_projection.subgraph(community)
            groups.extend(set(component) for component in nx.connected_components(community_graph))

    isolates = {gid for gid, degree in projection.degree() if degree == 0}
    groups.extend({gid} for gid in isolates)
    groups.sort(key=lambda group: min(group))
    return {
        int(gid): cluster_id
        for cluster_id, group in enumerate(groups)
        for gid in group
    }


def _format_kzt(sum_tiyn: int) -> str:
    whole, fraction = divmod(int(sum_tiyn), 100)
    return f"{whole}.{fraction:02d}"


def summarize_clusters(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """Summarize cluster membership, internal flow, ranking, and caveats."""
    required = ["gid", "cluster_id", "role", "is_seed", "priority_score"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"cluster summary is missing required features: {', '.join(missing)}")

    cluster_by_gid = {int(row.gid): int(row.cluster_id) for row in df[required].itertuples(index=False)}
    graph_ids = {int(gid) for gid in G.nodes}
    if set(cluster_by_gid) != graph_ids:
        raise ValueError("cluster assignments must cover exactly the graph nodes")
    if len(cluster_by_gid) != len(df):
        raise ValueError("cluster summary requires one row per gid")

    internal_tiyn = {}
    internal_edges = {}
    intercluster_tiyn = 0
    total_tiyn = 0
    for src, dst, data in G.edges(data=True):
        if "sum_tiyn" not in data:
            raise ValueError("graph edges must include sum_tiyn")
        amount = int(data["sum_tiyn"])
        total_tiyn += amount
        source_cluster = cluster_by_gid[int(src)]
        destination_cluster = cluster_by_gid[int(dst)]
        if source_cluster == destination_cluster:
            internal_tiyn[source_cluster] = internal_tiyn.get(source_cluster, 0) + amount
            internal_edges[source_cluster] = internal_edges.get(source_cluster, 0) + 1
        else:
            intercluster_tiyn += amount

    ranked = df.sort_values(
        ["priority_score", "gid"],
        ascending=[False, True],
        kind="mergesort",
    )
    records = []
    for cluster_id, members in df.groupby("cluster_id", sort=True):
        cluster_id = int(cluster_id)
        n_nodes = len(members)
        n_seed = int(members["is_seed"].sum())
        volume_tiyn = internal_tiyn.get(cluster_id, 0)
        internal_edge_count = internal_edges.get(cluster_id, 0)
        top_gids = [int(gid) for gid in ranked.loc[ranked["cluster_id"] == cluster_id, "gid"].head(5)]
        role_counts = members["role"].value_counts().sort_index().sort_values(ascending=False, kind="mergesort")
        leading_roles = ", ".join(
            f"{role}={int(count)}" for role, count in role_counts.head(2).items()
        ) or "нет назначенных ролей"

        if n_nodes == 1 and internal_edge_count == 0 and volume_tiyn == 0:
            if n_seed:
                hypothesis = (
                    "Один узел (seed) без внутренних рёбер; внутренний оборот 0.00 KZT. "
                    "Наблюдаемые данные не описывают связи за пределами выгрузки."
                )
            else:
                hypothesis = (
                    "Один узел без внутренних рёбер; внутренний оборот 0.00 KZT. "
                    "Отсутствие seed не означает безопасность."
                )
        elif n_seed:
            hypothesis = (
                f"Наблюдаемая группа: {n_nodes} узлов, {n_seed} seed, "
                f"{internal_edge_count} внутренних рёбер, {_format_kzt(volume_tiyn)} KZT; "
                f"частые роли: {leading_roles}. Описаны только наблюдаемые переводы."
            )
        else:
            hypothesis = (
                f"Группа: {n_nodes} узлов, 0 seed, {internal_edge_count} внутренних рёбер, "
                f"{_format_kzt(volume_tiyn)} KZT; частые роли: {leading_roles}. "
                "Отсутствие seed не означает безопасность."
            )

        records.append({
            "cluster_id": cluster_id,
            "n_nodes": n_nodes,
            "n_seed": n_seed,
            "sum_tiyn_internal": int(volume_tiyn),
            "sum_kzt_internal": _format_kzt(volume_tiyn),
            "n_edges_internal": internal_edge_count,
            "top_gids": top_gids,
            "hypothesis": hypothesis,
        })

    summary = pd.DataFrame(records, columns=[
        "cluster_id", "n_nodes", "n_seed", "sum_tiyn_internal",
        "sum_kzt_internal", "n_edges_internal", "top_gids", "hypothesis",
    ])
    internal_total = sum(internal_tiyn.values())
    if internal_total + intercluster_tiyn != total_tiyn:
        raise ValueError("internal and intercluster turnover do not reconcile to total turnover")
    summary.attrs["sum_tiyn_total"] = int(total_tiyn)
    summary.attrs["sum_tiyn_internal_total"] = int(internal_total)
    summary.attrs["sum_tiyn_intercluster_total"] = int(intercluster_tiyn)
    return summary


# ---------------------------------------------------------------- выгрузки

def write_outputs(df: pd.DataFrame, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. nodes_roles.csv — схема из ТЗ, роли не заполнены
    roles = df[["gid"]].copy()
    roles["role"] = ""            # TODO: одна из ROLES
    roles["role_score"] = 0.0     # TODO: 0..1
    roles["cluster_id"] = -1      # TODO: номер кластера
    roles["priority_score"] = 0.0 # TODO: 0..1
    roles["evidence"] = ""        # TODO: почему — с числами, до 200 символов
    roles = roles.merge(
        df[["gid", "in_deg", "out_deg", "in_tiyn", "out_tiyn",
            "pass_through", "depth", "is_seed", "boundary", "isolated"]],
        on="gid", how="left")
    roles.to_csv(out_dir / "nodes_roles.csv", index=False)

    # 2. clusters.csv — пустой каркас
    pd.DataFrame(columns=["cluster_id", "n_nodes", "n_seed",
                          "sum_kzt_internal", "top_gids", "hypothesis"]) \
        .to_csv(out_dir / "clusters.csv", index=False)

    # 3. top_nodes.csv — пустой каркас, нужно ≥20 строк
    pd.DataFrame(columns=["rank", "gid", "role", "priority_score", "why"]) \
        .to_csv(out_dir / "top_nodes.csv", index=False)

    print(f"Выгрузки записаны в {out_dir}/  (роли пока пустые — это ваша задача)")


# ---------------------------------------------------------------- подсказки

def hints(G: nx.DiGraph, df: pd.DataFrame):
    """Куда смотреть дальше. Ответов здесь нет — только направления."""
    print("\nС ЧЕГО НАЧАТЬ")
    print("-" * 64)
    print(f"  узлов, получающих от 3+ разных плательщиков : {(df.in_deg >= 3).sum()}")
    print(f"  узлов, рассылающих на 10+ получателей       : {(df.out_deg >= 10).sum()}")
    print(f"  узлов и с входом, и с выходом               : {((df.in_deg > 0) & (df.out_deg > 0)).sum()}")
    print(f"  boundary на depth=4 без исходящих           : {df.boundary.sum()}")
    print(f"  слабосвязных компонент                      : {nx.number_weakly_connected_components(G)}")
    print("""
  Вопросы, на которые стоит ответить метриками:
    * чем «деньги пришли и остались» отличается от «пришли и ушли дальше»?
    * что важнее для роли — количество плательщиков или сумма?
    * узел собирает средства от нескольких SEED — это случайность или структура?
    * если убрать узел, сеть распадётся или переживёт?

  Полезное в networkx: betweenness_centrality, community.louvain_communities,
  simple_cycles, all_simple_paths.
  Не забудьте: граф НАПРАВЛЕННЫЙ и ВЗВЕШЕННЫЙ.
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data", help="папка с parquet-файлами")
    ap.add_argument("--out", default="./out", help="куда писать выгрузки")
    a = ap.parse_args()

    try:
        edges, nodes, tx = load(Path(a.data))
        sanity_check(edges, nodes, tx)
    except ValueError as exc:
        ap.error(str(exc))
    G = build_graph(edges, nodes)
    df = basic_features(G, nodes)
    df = enrich_features(G, df, tx)
    write_outputs(df, Path(a.out))
    hints(G, df)


if __name__ == "__main__":
    main()
