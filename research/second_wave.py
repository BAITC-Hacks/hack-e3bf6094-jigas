"""Reproduce docs/SECOND_WAVE_RESEARCH.md using the existing analytics functions.

Run: python research/second_wave.py [--repo /path/to/source-snapshot] [--revision SHA]
Reads the supplied ZIP locally. Does not run the product CLI or acceptance tests.
All experiments are descriptive; no role labels or analyst judgments are available.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from math import comb
from pathlib import Path
import platform
import random
import sys
import zipfile

import networkx as nx
import numpy as np
import pandas as pd
import pyarrow


def dated_reach(events, seed, same_day=False, max_hops=4):
    """Existence of date-compatible routes, with unlimited waiting; not fund tracing.

    Earliest arrival at each exact hop count dominates later arrivals. Each round
    reads only the previous round, so same-day chains cannot exceed max_hops.
    """
    arrivals, reached = {seed: -1}, set()
    for _ in range(max_hops):
        following = {}
        for src, dst, day in events:
            previous = arrivals.get(src)
            if previous is not None and (day >= previous if same_day else day > previous):
                following[dst] = min(day, following.get(dst, day))
        reached.update(following)
        arrivals = following
    return reached - {seed}


def adjusted_rand(left, right):
    """Label-invariant partition agreement from the contingency counts."""
    if len(left) != len(right):
        raise ValueError("partitions must cover the same ordered node set")
    if len(left) < 2:
        return 1.0
    pairs = sum(comb(n, 2) for n in Counter(zip(left, right)).values())
    a = sum(comb(n, 2) for n in Counter(left).values())
    b = sum(comb(n, 2) for n in Counter(right).values())
    expected = a * b / comb(len(left), 2)
    denominator = (a + b) / 2 - expected
    return (pairs - expected) / denominator if denominator else 1.0


def self_check():
    events = [(1, 2, 3), (2, 3, 2), (2, 4, 3), (2, 5, 4)]
    assert dated_reach(events, 1) == {2, 5}
    assert dated_reach(events, 1, same_day=True) == {2, 4, 5}
    assert dated_reach([(1, 2, 1), (2, 3, 1)], 1, True, 1) == {2}
    assert dated_reach([(1, 2, 1), (2, 1, 2)], 1) == {2}
    assert adjusted_rand([0, 0, 1, 1], [8, 8, 9, 9]) == 1
    assert np.isclose(adjusted_rand([0, 0, 1, 1], [0, 1, 0, 1]), -0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", default="unrecorded; consult source_sha256")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("second_wave_evidence.json"))
    args = parser.parse_args()
    self_check()
    source = args.repo / "starter.py"
    sys.path.insert(0, str(args.repo.resolve()))
    spec = importlib.util.spec_from_file_location("wave2_starter", source)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    tables, hashes = {}, {}
    with zipfile.ZipFile(args.repo / "track_data/data (1).zip") as archive:
        for name in ("nodes", "edges", "transactions"):
            raw = archive.read(f"data/{name}.parquet")
            hashes[name] = hashlib.sha256(raw).hexdigest()
            tables[name] = pd.read_parquet(io.BytesIO(raw))
    nodes, edges, tx = (tables[k] for k in ("nodes", "edges", "transactions"))
    app.sanity_check(edges, nodes, tx)
    graph = app.build_graph(edges, nodes)
    frame = app.enrich_features(graph, app.basic_features(graph, nodes), tx)
    frame, role_parameters = app.assign_roles(frame)
    frame, priority_parameters = app.compute_priority(frame)
    cluster_map = app.cluster_nodes(graph)
    frame["cluster_id"] = frame.gid.map(cluster_map)
    frame = frame.set_index("gid", drop=False)
    # Index names must not collide with column names in subsequent stable sorts.
    frame.index.name = "node_index"
    top = frame.head(20)
    ids, seed_ids = sorted(map(int, frame.index)), sorted(map(int, frame.index[frame.is_seed]))
    top_ids = set(map(int, top.index))
    components = pd.DataFrame(frame.score_components.tolist(), index=frame.index)
    volume = (frame.in_tiyn + frame.out_tiyn) / 100
    volume_order = sorted(ids, key=lambda gid: (-volume[gid], gid))
    score_groups = frame.groupby("priority_score").size().sort_index(ascending=False)
    direct_seed = edges[edges.src.isin(seed_ids)].groupby("dst").src.nunique()
    frame["direct_seed_payers"] = direct_seed.reindex(frame.index, fill_value=0)
    static_sets = {
        seed: set(nx.single_source_shortest_path_length(graph, seed, cutoff=4)) - {seed}
        for seed in seed_ids
    }
    static_counts = Counter(gid for reached in static_sets.values() for gid in reached)
    assert all(static_counts[gid] == int(frame.loc[gid, "seed_reach_count"]) for gid in ids)
    events = [(int(r.src), int(r.dst), pd.Timestamp(r.date).day) for r in tx.itertuples()]
    temporal = {}
    for name, allow_same_day in (("strict_later_day", False), ("same_day_allowed", True)):
        reachable = {seed: dated_reach(events, seed, allow_same_day) for seed in seed_ids}
        assert all(reachable[s] <= static_sets[s] for s in seed_ids)
        counts = Counter(gid for reached in reachable.values() for gid in reached)
        frame[name] = pd.Series(counts).reindex(frame.index, fill_value=0).astype(int)
        temporal[name] = {
            "seed_target_pairs": sum(map(len, reachable.values())),
            "nodes_with_seed_route": len(counts),
            "nodes_with_fewer_seed_routes_than_static": sum(counts[g] < static_counts[g] for g in ids),
            "top20_seed_target_pairs": sum(counts[g] for g in top_ids),
            "top20_nodes_with_fewer_seed_routes": sum(counts[g] < static_counts[g] for g in top_ids),
        }
    assert (frame.strict_later_day <= frame.same_day_allowed).all()
    weights = priority_parameters["weights"]
    sensitivity = []
    for component in weights:
        for factor in (0.8, 1.2):
            adjusted = {key: weight * (factor if key == component else 1) for key, weight in weights.items()}
            total = sum(adjusted.values())
            adjusted = {key: value / total for key, value in adjusted.items()}
            scores = sum(adjusted[key] * components[key] for key in weights)
            order = sorted(ids, key=lambda gid: (-scores[gid], gid))
            sensitivity.append({"component": component, "factor": factor, "top20_overlap": len(top_ids & set(order[:20]))})

    if (args.repo / "hackalem").is_dir():
        from hackalem.clusters import _undirected_projection
    else:
        _undirected_projection = app._undirected_projection
    projection = _undirected_projection(graph)
    active = projection.subgraph([g for g in ids if projection.degree(g) > 0]).copy()
    baseline_labels = [cluster_map[g] for g in active]
    stability = []
    for resolution in (0.8, 1.0, 1.2):
        for seed in (0, 1, 2, 42):
            raw_groups = nx.community.louvain_communities(active, weight="weight", resolution=resolution, seed=seed)
            groups = [set(c) for group in raw_groups for c in nx.connected_components(active.subgraph(group))]
            label = {g: i for i, group in enumerate(groups) for g in group}
            stability.append({
                "resolution": resolution, "seed": seed, "nonisolated_communities": len(groups),
                "disconnected_raw_communities": sum(not nx.is_connected(active.subgraph(g)) for g in raw_groups),
                "adjusted_rand_vs_production": adjusted_rand(baseline_labels, [label[g] for g in active]),
            })
    assert any(r["resolution"] == 1 and r["seed"] == 42 and r["adjusted_rand_vs_production"] == 1 for r in stability)

    def removal_stats(removed):
        remaining = graph.subgraph(set(ids) - set(removed))
        surviving_seeds = set(seed_ids) - set(removed)
        before = sum(len(static_sets[s] - set(removed)) for s in surviving_seeds)
        after = sum(len(nx.single_source_shortest_path_length(remaining, s, cutoff=4)) - 1 for s in surviving_seeds)
        assert 0 <= after <= before
        return {"surviving_seed_target_pairs_before": before, "after": after,
                "lost_pair_share": (before - after) / before if before else 0,
                "largest_wcc_nodes": max(map(len, nx.weakly_connected_components(remaining)), default=0),
                "removed_seed_count": len(set(removed) & set(seed_ids))}

    impact = {}
    betweenness_order = sorted(ids, key=lambda gid: (-frame.loc[gid, "betweenness"], gid))
    for name, order in (("priority", list(map(int, frame.index))), ("volume", volume_order), ("betweenness", betweenness_order)):
        impact[name] = {str(k): removal_stats(order[:k]) for k in (5, 10, 20)}
    rng = random.Random(42)
    random_impact = [removal_stats(rng.sample(ids, 20))["lost_pair_share"] for _ in range(30)]
    frontier = frame[frame.depth == 4].sort_values(["in_tiyn", "gid"], ascending=[False, True])
    top = frame.loc[list(top.index)]
    largest_scc = max(nx.strongly_connected_components(graph), key=len)
    inspection_columns = ["gid", "role", "priority_score", "is_seed", "depth", "cluster_id", "in_deg", "out_deg", "seed_reach_count", "direct_seed_payers", "strict_later_day", "same_day_allowed"]
    top_records = top[inspection_columns].to_dict("records")
    for row in top_records:
        row["gid"] = str(row["gid"])
    representatives = {}
    for role, group in frame.groupby("role", sort=True):
        row = group.iloc[0]
        representatives[role] = {"gid": str(int(row.gid)), "evidence": row.evidence, "why": row.why,
                                 "in_deg": int(row.in_deg), "out_deg": int(row.out_deg),
                                 "in_kzt": int(row.in_tiyn) / 100, "out_kzt": int(row.out_tiyn) / 100,
                                 "days_after_last_in": None if pd.isna(row.days_after_last_in) else int(row.days_after_last_in)}
    result = {
        "scope": "Research of implemented metrics; not product acceptance, AML accuracy, or analyst usefulness.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": args.revision,
        "source_sha256": {str(p.relative_to(args.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [source, *sorted((args.repo / "hackalem").glob("*.py"))]},
        "template_sha256": hashlib.sha256((args.repo / "report_template.html").read_bytes()).hexdigest(),
        "sha256_parquet": hashes,
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "pandas": pd.__version__, "numpy": np.__version__, "networkx": nx.__version__, "pyarrow": pyarrow.__version__},
        "dataset": {"nodes": len(nodes), "edges": len(edges), "transactions": len(tx), "seeds": len(seed_ids), "isolates": len(list(nx.isolates(graph))), "wcc_with_isolates": nx.number_weakly_connected_components(graph), "wcc_without_isolates": nx.number_connected_components(active), "total_tiyn": int(edges.sum_tiyn.sum())},
        "roles": frame.role.value_counts().sort_index().to_dict(),
        "clusters": {"including_isolates": len(set(cluster_map.values())), "with_multiple_seeds": int(frame.groupby("cluster_id").is_seed.sum().ge(2).sum()), "top20_distinct_clusters": int(top.cluster_id.nunique()), "top20_largest_scc_members": len(top_ids & largest_scc)},
        "ranking": {
            "maximum": float(frame.priority_score.max()), "nodes_at_maximum": int(score_groups.iloc[0]),
            "top20_distinct_exact_scores": int(top.priority_score.nunique()),
            "top20_seed_count": int(top.is_seed.sum()), "top20_volume_overlap": len(top_ids & set(volume_order[:20])),
            "top20_role_counts": top.role.value_counts().to_dict(),
            "top20_matching_consolidator_count": sum(any(m["role"] == "consolidator" for m in row) for row in top.matched_roles),
            "score_at_rank20": float(top.priority_score.iloc[-1]),
            "nodes_tied_at_cutoff": int(score_groups.loc[top.priority_score.iloc[-1]]),
            "component_saturation_counts": {key: int((components[key] == 1).sum()) for key in weights},
            "positive_activity_component_quantiles": {str(q): float(components.loc[volume > 0, "A"].quantile(q)) for q in (0.1, 0.5, 0.9)},
            "top20": top_records, "weight_sensitivity": sensitivity,
        },
        "temporal_reach": {
            "definition": "Distinct seed-target pairs with a directed route of 1-4 transfers; unlimited waiting within July. Amounts are not traced. Same-day ordering is unknown.",
            "static_seed_target_pairs": sum(map(len, static_sets.values())),
            "static_nodes_with_seed_route": len(static_counts),
            "static_top20_seed_target_pairs": int(top.seed_reach_count.sum()),
            **temporal,
        },
        "community_stability": stability,
        "topological_removal": {
            "definition": "Remove fixed top-k nodes without reranking. Compare surviving seed/target pairs reachable within 4 hops before vs after; endpoint removal is excluded from denominator. No adaptive rerouting or financial-loss claim.",
            "methods": impact,
            "uniform_random20_control": {"seed": 42, "runs": 30, "lost_pair_share_min": min(random_impact), "median": float(np.median(random_impact)), "max": max(random_impact)},
        },
        "frontier": {"nodes": len(frontier), "incoming_tiyn": int(frontier.in_tiyn.sum()), "top5_incoming_share": int(frontier.head(5).in_tiyn.sum()) / int(frontier.in_tiyn.sum()), "top20_incoming_share": int(frontier.head(20).in_tiyn.sum()) / int(frontier.in_tiyn.sum()), "nodes_in_global_top20": len(top_ids & set(frontier.index))},
        "ui_edge_width": {
            "scope": "Evaluate the width formula read in graphEdgeData at the recorded revision; not a browser rendering check.",
            "formula": "max(1.25, min(4.5, log10(abs(sum_tiyn) + 1)))",
            "unique_widths": sorted(set(np.clip(np.log10(edges.sum_tiyn + 1), 1.25, 4.5))),
            "minimum_edge_kzt": int(edges.sum_tiyn.min()) / 100,
            "maximum_edge_kzt": int(edges.sum_tiyn.max()) / 100,
        },
        "representative_explanations": representatives,
        "production_parameters": {"roles": role_parameters, "priority": priority_parameters},
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Research evidence written to {args.out}")


if __name__ == "__main__":
    main()
