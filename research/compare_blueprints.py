"""Reproduce the dataset claims audited in docs/BLUEPRINT_COMPARISON.md.

Run: python research/compare_blueprints.py
Requires pandas, numpy, networkx and pyarrow. Reads the supplied ZIP directly.
This is a research probe, not a role classifier or a measure of AML accuracy.
"""

import hashlib
import io
import json
import platform
import sys
import zipfile
from collections import deque
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pyarrow


def match_days(incoming, outgoing, same_day=False):
    """FIFO compatibility within two days; consume each amount at most once.

    same_day=True assumes incoming precedes outgoing within each day. Neither
    variant establishes actual provenance or reconstructs account balances.
    """
    available = deque()
    matched = 0
    for day in sorted(incoming.keys() | outgoing.keys()):
        while available and day - available[0][0] > 2:
            available.popleft()
        if same_day and incoming.get(day, 0):
            available.append([day, incoming[day]])
        remaining = outgoing.get(day, 0)
        while remaining and available:
            used = min(remaining, available[0][1])
            remaining -= used
            available[0][1] -= used
            matched += used
            if available[0][1] == 0:
                available.popleft()
        if not same_day and incoming.get(day, 0):
            available.append([day, incoming[day]])
    return matched


def self_check():
    assert match_days({1: 100}, {1: 100}) == 0
    assert match_days({1: 100}, {1: 100}, same_day=True) == 100
    assert match_days({1: 100}, {2: 80, 3: 80}) == 100
    assert match_days({1: 100, 2: 100}, {3: 150}) == 150
    assert match_days({1: 100}, {4: 100}) == 0


def main():
    self_check()
    root = Path(__file__).resolve().parents[1]
    tables, hashes = {}, {}
    with zipfile.ZipFile(root / "track_data/data (1).zip") as archive:
        for name in ("nodes", "edges", "transactions"):
            raw = archive.read(f"data/{name}.parquet")
            hashes[name] = hashlib.sha256(raw).hexdigest()
            tables[name] = pd.read_parquet(io.BytesIO(raw))
    nodes, edges, tx = (tables[k] for k in ("nodes", "edges", "transactions"))
    for table in (edges, tx):
        cents = table.sum_kzt.to_numpy() * 100
        assert np.allclose(cents, np.rint(cents), atol=1e-6, rtol=0)
        table["sum_tiyn"] = np.rint(cents).astype("int64")
    grouped = tx.groupby(["src", "dst"]).agg(
        sum_tiyn=("sum_tiyn", "sum"), n_tx=("sum_tiyn", "size")
    )
    actual = edges.set_index(["src", "dst"])[["sum_tiyn", "n_tx"]].sort_index()
    assert actual.equals(grouped.sort_index())
    assert nodes.gid.is_unique and len(actual) == len(edges)
    assert (set(edges.src) | set(edges.dst)) <= set(nodes.gid)

    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(nodes.gid))
    for row in edges.sort_values(["src", "dst"]).itertuples(index=False):
        graph.add_edge(row.src, row.dst, amount=row.sum_tiyn)
    frame = nodes.set_index("gid").copy()
    for name, values in (
        ("in_deg", graph.in_degree()), ("out_deg", graph.out_degree()),
        ("in_tiyn", graph.in_degree(weight="amount")),
        ("out_tiyn", graph.out_degree(weight="amount")),
    ):
        frame[name] = pd.Series(dict(values), dtype="int64")
    frame["in_tx"] = tx.groupby("dst").size().reindex(frame.index, fill_value=0)
    seeds = set(frame.index[frame.is_seed])
    frame["seed_payers"] = edges[edges.src.isin(seeds)].groupby("dst").src.nunique().reindex(frame.index, fill_value=0)
    frame["bucket"] = pd.cut(frame.in_deg, [0, 1, 2, 4, np.inf], labels=["1", "2", "3-4", "5+"])
    frame["has_out"] = frame.out_deg > 0
    observed = frame[frame.depth.between(1, 3)]
    rates = observed.groupby("bucket", observed=True).has_out.mean()
    frontier = frame[frame.depth == 4].copy()
    frontier["p"] = frontier.bucket.map(rates).astype(float)
    frontier_gate = (frontier.p <= .35) & ((frontier.in_tiyn >= 15_000_000) | (frontier.in_deg >= 2))

    buckets = []
    for bucket in rates.index:
        row = {"bucket": str(bucket), "all_depths_1_to_3_p": float(rates[bucket])}
        for depth in (1, 2, 3, 4):
            subset = frame[(frame.depth == depth) & (frame.bucket == bucket)]
            row[f"depth_{depth}_n"] = len(subset)
            row[f"depth_{depth}_p"] = float(subset.has_out.mean()) if depth < 4 and len(subset) else None
        buckets.append(row)
    train, holdout = frame[frame.depth.between(1, 2)], frame[frame.depth == 3]
    train_rates = train.groupby("bucket", observed=True).has_out.mean()
    predictions = holdout.bucket.map(train_rates).astype(float).fillna(train.has_out.mean())
    y = holdout.has_out.astype(float)
    # Replay a depth-3 crawl: hide every edge sent by a depth-3 source before
    # computing input features, then evaluate against its held-out outgoing edges.
    visible = edges[edges.src.map(frame.depth) < 3]
    visible_in_deg = visible.groupby("dst").src.nunique().reindex(frame.index, fill_value=0)
    visible_buckets = pd.cut(visible_in_deg, [0, 1, 2, 4, np.inf], labels=["1", "2", "3-4", "5+"])
    replay_train = train.assign(bucket=visible_buckets.loc[train.index])
    replay_rates = replay_train.groupby("bucket", observed=True).has_out.mean()
    replay_predictions = visible_buckets.loc[holdout.index].map(replay_rates).astype(float).fillna(train.has_out.mean())

    scc = max(nx.strongly_connected_components(graph), key=len)
    source, target = edges.src.isin(scc), edges.dst.isin(scc)
    internal, leaving, entering = edges[source & target], edges[source & ~target], edges[~source & target]
    total = int(edges.sum_tiyn.sum())
    def flow_rows(part):
        return {"edges": len(part), "kzt": int(part.sum_tiyn.sum()) / 100}

    tx["day"] = pd.to_datetime(tx.date).map(lambda date: date.toordinal())
    daily_in = {gid: dict(zip(part.day, part.sum_tiyn)) for gid, part in tx.groupby(["dst", "day"], as_index=False).sum_tiyn.sum().groupby("dst")}
    daily_out = {gid: dict(zip(part.day, part.sum_tiyn)) for gid, part in tx.groupby(["src", "day"], as_index=False).sum_tiyn.sum().groupby("src")}
    temporal = []
    for gid in sorted(daily_in.keys() & daily_out.keys()):
        incoming, outgoing = daily_in[gid], daily_out[gid]
        naive = sum(amount for day, amount in outgoing.items() if any(0 <= day - date <= 2 for date in incoming))
        strict, permissive = match_days(incoming, outgoing), match_days(incoming, outgoing, same_day=True)
        assert strict <= min(sum(incoming.values()), sum(outgoing.values()))
        assert permissive <= min(sum(incoming.values()), sum(outgoing.values()))
        temporal.append({"gid": str(gid), "naive": naive, "strict": strict, "same_day_assumed": permissive, "incoming": sum(incoming.values()), "outgoing": sum(outgoing.values())})
    ratios = frame.out_tiyn / frame.in_tiyn.replace(0, np.nan)
    vlad_transit_gate = (~frame.is_seed) & (frame.depth < 4) & (frame.in_tiyn > 0) & (frame.out_tiyn > 0) & ratios.between(.7, 1.3) & (frame.out_deg <= 5)
    # These IDs are audit examples cited by the documents, never label overrides.
    demo_ids = (100000005075949100, 100000008165763100, 100000003684369100, 100000005382566100)
    demos = {}
    for gid in demo_ids:
        row = frame.loc[gid]
        demos[str(gid)] = {key: int(row[key]) for key in ("depth", "in_deg", "out_deg", "in_tx", "seed_payers")}
        demos[str(gid)].update(in_kzt=int(row.in_tiyn) / 100, out_kzt=int(row.out_tiyn) / 100, vlad_transit_gate=bool(vlad_transit_gate.loc[gid]))
        if row.depth == 4:
            p = float(rates[row.bucket])
            amount_ramp = float(np.clip((row.in_tiyn / 100 - 150_000) / 850_000, 0, 1))
            count_ramp = float(np.clip((row.in_tx - 1) / 4, 0, 1))
            demos[str(gid)].update(p_outflow_est=p, vlad_terminal_score=(amount_ramp + count_ramp) / 2 * (1 - p))
    activity = (frame.in_tiyn + frame.out_tiyn) / 100
    q95 = float(activity[activity > 0].quantile(.95))
    astra_activity = np.minimum(1, np.log1p(activity) / np.log1p(q95))
    result = {
        "scope": "Dataset and formula audit; no AML ground truth, no product benchmark.",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "pandas": pd.__version__, "numpy": np.__version__, "networkx": nx.__version__, "pyarrow": pyarrow.__version__},
        "sha256_parquet": hashes,
        "dataset": {"nodes": len(nodes), "edges": len(edges), "transactions": len(tx), "seed": len(seeds), "isolates": len(list(nx.isolates(graph))), "wcc": nx.number_weakly_connected_components(graph), "total_kzt": total / 100, "duplicate_transaction_rows": int(tx.duplicated(["src", "dst", "date", "sum_kzt"]).sum()), "all_ids_exceed_js_safe_integer": bool((nodes.gid > 2**53 - 1).all()), "all_edge_depths_equal_src_depth_plus_one": bool((edges.depth == edges.src.map(frame.depth) + 1).all())},
        "scc": {"nodes": len(scc), "seed": len(scc & seeds), "internal": flow_rows(internal), "leaving": {**flow_rows(leaving), "unique_recipients": int(leaving.dst.nunique())}, "entering": flow_rows(entering), "all_scc_origin_kzt": int(edges[source].sum_tiyn.sum()) / 100, "origin_share_of_edge_turnover": int(edges[source].sum_tiyn.sum()) / total},
        "frontier": {"n": len(frontier), "no_recorded_outflow": int((frontier.out_deg == 0).sum()), "vlad_p_gate_eligible": int((frontier.p <= .35).sum()), "vlad_terminal_gate_eligible": int(frontier_gate.sum()), "incoming_at_least_1m_kzt": int((frontier.in_tiyn >= 100_000_000).sum()), "incoming_kzt": int(frontier.in_tiyn.sum()) / 100, "incoming_share": int(frontier.in_tiyn.sum()) / total, "edge_depth_4_kzt": int(edges.loc[edges.depth == 4, "sum_tiyn"].sum()) / 100, "edge_depth_4_share": int(edges.loc[edges.depth == 4, "sum_tiyn"].sum()) / total, "buckets": buckets},
        "holdout_depth_3": {"train_n": len(train), "holdout_n": len(holdout), "train_has_out_rate": float(train.has_out.mean()), "holdout_has_out_rate": float(y.mean()), "bucket_model_brier": float(((predictions - y) ** 2).mean()), "crawl_replay_bucket_model_brier": float(((replay_predictions - y) ** 2).mean()), "constant_train_rate_brier": float(((train.has_out.mean() - y) ** 2).mean()), "limitation": "Interior-depth diagnostic, not calibration on the unobserved fourth layer. Replay hides all depth-3 source edges before constructing incoming features."},
        "temporal": {"nodes_with_both": len(temporal), "share_at_least_80pct": {metric: sum(row[metric] / row["outgoing"] >= .8 for row in temporal) for metric in ("naive", "strict", "same_day_assumed")}, "naive_matched_exceeds_observed_inflow_nodes": sum(row["naive"] > row["incoming"] for row in temporal), "naive_fast_out_over_in_in_07_to_13": sum(.7 <= row["naive"] / row["incoming"] <= 1.3 for row in temporal), "definitions": {"naive": "Existing eda2 timing co-occurrence, allows same-day and unlimited reuse of inflow.", "strict": "Amount-conserving FIFO, 1-2 later calendar days.", "same_day_assumed": "Amount-conserving FIFO, 0-2 calendar days assuming all same-day incoming precedes outgoing."}},
        "role_formula_diagnostics": {"vlad_transit_gate_upper_bound": int(vlad_transit_gate.sum()), "astra_activity_q95_kzt": q95, "astra_activity_component_positive_quantiles": {str(q): float(astra_activity[activity > 0].quantile(q)) for q in (.1, .5, .9)}, "examples": demos},
    }
    output = Path(__file__).with_name("blueprint_comparison_evidence.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
