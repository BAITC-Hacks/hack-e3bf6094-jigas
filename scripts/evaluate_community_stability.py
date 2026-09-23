"""Measure Louvain community membership sensitivity on the official dataset."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from math import comb
from pathlib import Path
import sys

import networkx as nx
import numpy as np
import pandas as pd
import pyarrow as pa


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hackalem.clusters import (  # noqa: E402
    LOUVAIN_RESOLUTION,
    LOUVAIN_SEED,
    _undirected_projection,
    cluster_nodes,
)
from hackalem.graph import build_graph  # noqa: E402
from hackalem.validation import load, sanity_check  # noqa: E402


EXPECTED_INPUT_SHA256 = {
    "nodes.parquet": "d2a45b0df6e9352832d5fb09839d10b9e23f898156c3bab263b051b31cc0296d",
    "edges.parquet": "4e71dde5cd3115bcb26e91202665532ee6581cf8233a9fc8059ea59fb7358a38",
    "transactions.parquet": "c30c5317b5439591dde86f2058dc47a3d19b2900c055ded994fe547f6fb7e7da",
}
RESOLUTIONS = (0.8, LOUVAIN_RESOLUTION, 1.2)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_inputs(data_dir: Path) -> dict[str, str]:
    hashes = {}
    for filename, expected in EXPECTED_INPUT_SHA256.items():
        path = data_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"required official input is missing: {path}")
        actual = _file_sha256(path)
        hashes[filename] = actual
        if actual != expected:
            raise ValueError(
                f"{filename} SHA-256 mismatch: expected {expected}, got {actual}"
            )
    return hashes


def _partition_from_projection(
    projection: nx.Graph,
    *,
    resolution: float,
    seed: int,
) -> tuple[dict[int, int], int, int]:
    """Apply production Louvain settings and its disconnected-group repair."""
    nonisolates = sorted(gid for gid, degree in projection.degree() if degree > 0)
    groups: list[set[int]] = []
    disconnected_communities = 0
    raw_community_count = 0

    if nonisolates:
        active_projection = projection.subgraph(nonisolates).copy()
        communities = nx.community.louvain_communities(
            active_projection,
            weight="weight",
            resolution=resolution,
            seed=seed,
        )
        raw_community_count = len(communities)
        for community in communities:
            community_graph = active_projection.subgraph(community)
            components = [set(component) for component in nx.connected_components(community_graph)]
            disconnected_communities += len(components) > 1
            groups.extend(components)

    isolates = {gid for gid, degree in projection.degree() if degree == 0}
    groups.extend({gid} for gid in isolates)
    groups.sort(key=min)
    assignment = {
        int(gid): cluster_id
        for cluster_id, group in enumerate(groups)
        for gid in group
    }
    if set(assignment) != {int(gid) for gid in projection.nodes}:
        raise AssertionError("partition must cover exactly the projection nodes")
    return assignment, raw_community_count, int(disconnected_communities)


def _groups(assignment: dict[int, int]) -> list[frozenset[int]]:
    members: dict[int, set[int]] = {}
    for gid, cluster_id in assignment.items():
        members.setdefault(cluster_id, set()).add(gid)
    return [frozenset(members[key]) for key in sorted(members)]


def _adjusted_rand_index(left: dict[int, int], right: dict[int, int], gids: list[int]) -> float:
    """Compute adjusted Rand agreement without an sklearn dependency."""
    if set(left) != set(right) or not set(gids).issubset(left):
        raise ValueError("ARI partitions must cover the same node set")
    pair_count = comb(len(gids), 2)
    if pair_count == 0:
        return 1.0

    cells = Counter((left[gid], right[gid]) for gid in gids)
    left_counts = Counter(left[gid] for gid in gids)
    right_counts = Counter(right[gid] for gid in gids)
    cell_pairs = sum(comb(count, 2) for count in cells.values())
    left_pairs = sum(comb(count, 2) for count in left_counts.values())
    right_pairs = sum(comb(count, 2) for count in right_counts.values())
    expected = left_pairs * right_pairs / pair_count
    maximum = (left_pairs + right_pairs) / 2
    denominator = maximum - expected
    if denominator == 0:
        left_groups = set(_groups({gid: left[gid] for gid in gids}))
        right_groups = set(_groups({gid: right[gid] for gid in gids}))
        return 1.0 if left_groups == right_groups else 0.0
    return (cell_pairs - expected) / denominator


def _compare(
    baseline: dict[int, int],
    scenario: dict[int, int],
    *,
    active_gids: list[int],
) -> dict[str, object]:
    baseline_groups = _groups(baseline)
    scenario_groups = _groups(scenario)
    active_set = set(active_gids)
    baseline_active_groups = [
        frozenset(group & active_set)
        for group in baseline_groups
        if group & active_set
    ]
    scenario_active_groups = [
        frozenset(group & active_set)
        for group in scenario_groups
        if group & active_set
    ]
    scenario_active_group_set = set(scenario_active_groups)

    changed_baseline_sizes = sorted(
        len(group)
        for group in baseline_active_groups
        if group not in scenario_active_group_set
    )
    baseline_peers = {
        gid: group for group in baseline_active_groups for gid in group
    }
    scenario_peers = {
        gid: group for group in scenario_active_groups for gid in group
    }
    changed_gids = [gid for gid in active_gids if baseline_peers[gid] != scenario_peers[gid]]

    return {
        "adjusted_rand_index_nonisolates": round(
            _adjusted_rand_index(baseline, scenario, active_gids), 12
        ),
        "changed_membership_nodes_nonisolates": len(changed_gids),
        "changed_membership_node_percent_nonisolates": round(
            100.0 * len(changed_gids) / len(active_gids), 6
        ) if active_gids else 0.0,
        "changed_baseline_communities_nonisolates": len(changed_baseline_sizes),
        "nodes_in_changed_baseline_communities_nonisolates": sum(changed_baseline_sizes),
        "changed_baseline_community_sizes_nonisolates": changed_baseline_sizes,
        "baseline_communities_nonisolates": len(baseline_active_groups),
        "scenario_communities_nonisolates": len(scenario_active_groups),
        "exact_baseline_communities_retained_nonisolates": len(
            set(baseline_active_groups) & scenario_active_group_set
        ),
    }


def evaluate(data_dir: Path) -> dict[str, object]:
    input_hashes = _verify_inputs(data_dir)
    edges, nodes, transactions = load(data_dir)
    with redirect_stdout(io.StringIO()):
        isolates = sanity_check(edges, nodes, transactions)
    graph = build_graph(edges, nodes)

    # This is the exact sorted KZT projection used by production cluster_nodes.
    projection = _undirected_projection(graph)
    baseline = cluster_nodes(graph)
    baseline_membership, baseline_raw_count, baseline_disconnected_count = (
        _partition_from_projection(
            projection,
            resolution=LOUVAIN_RESOLUTION,
            seed=LOUVAIN_SEED,
        )
    )
    baseline_matches_production = baseline_membership == baseline
    if not baseline_matches_production:
        raise AssertionError("resolution=1 seed=42 membership differs from cluster_nodes")

    active_gids = sorted(gid for gid, degree in projection.degree() if degree > 0)
    results = []
    for resolution in RESOLUTIONS:
        first, raw_count, disconnected_count = _partition_from_projection(
            projection,
            resolution=resolution,
            seed=LOUVAIN_SEED,
        )
        repeated, repeated_raw_count, repeated_disconnected_count = _partition_from_projection(
            projection,
            resolution=resolution,
            seed=LOUVAIN_SEED,
        )
        deterministic = first == repeated
        if not deterministic:
            raise AssertionError(f"resolution={resolution} did not repeat identically")
        if (raw_count, disconnected_count) != (repeated_raw_count, repeated_disconnected_count):
            raise AssertionError(f"resolution={resolution} repeat diagnostics differ")

        results.append({
            "resolution": resolution,
            "seed": LOUVAIN_SEED,
            "repeat_membership_identical": deterministic,
            "louvain_communities_before_connectivity_split": raw_count,
            "disconnected_louvain_communities_split": disconnected_count,
            "total_communities_including_isolates": len(_groups(first)),
            "total_isolated_singletons": len(isolates),
            **_compare(baseline, first, active_gids=active_gids),
        })

    return {
        "dataset": "official HackAlem July 2026 Parquet",
        "input_sha256": input_hashes,
        "environment": {
            "python": sys.version.split()[0],
            "networkx": nx.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "pyarrow": pa.__version__,
        },
        "projection": {
            "type": "undirected KZT sum of both directed edge amounts",
            "edge_order": "sorted by numeric (min_gid, max_gid)",
            "nodes": graph.number_of_nodes(),
            "directed_edges": graph.number_of_edges(),
            "nonisolated_nodes": len(active_gids),
            "isolated_nodes": len(isolates),
        },
        "baseline": {
            "resolution": LOUVAIN_RESOLUTION,
            "seed": LOUVAIN_SEED,
            "membership_exactly_matches_cluster_nodes": baseline_matches_production,
            "louvain_communities_before_connectivity_split": baseline_raw_count,
            "disconnected_louvain_communities_split": baseline_disconnected_count,
            "total_communities_including_isolates": len(_groups(baseline)),
            "communities_nonisolates": len(_groups(baseline)) - len(isolates),
        },
        "membership_comparison": "ARI and changed-node counts use nonisolated nodes; cluster labels are ignored.",
        "scenarios": results,
        "interpretation_limit": (
            "Membership stability describes algorithm sensitivity only; it is not AML accuracy, "
            "ground truth, or evidence that a community is an organization."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=ROOT / "data",
        help="directory containing the three official Parquet inputs",
    )
    args = parser.parse_args()
    print(json.dumps(evaluate(args.data.resolve()), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
