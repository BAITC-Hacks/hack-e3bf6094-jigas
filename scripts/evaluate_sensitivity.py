"""Reproducible, offline HA-12.2 sensitivity analysis for the official dataset.

This script leaves release modules and artifacts untouched.  It recalculates
the release baseline, then changes one priority weight at a time and
renormalizes the four weights to sum to one.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from hackalem.graph import basic_features, build_graph, enrich_features
from hackalem.scoring import assign_roles, compute_priority
from hackalem.validation import load, sanity_check


INPUT_FILES = ("nodes.parquet", "edges.parquet", "transactions.parquet")
EXPECTED_INPUT_SHA256 = {
    "nodes.parquet": "d2a45b0df6e9352832d5fb09839d10b9e23f898156c3bab263b051b31cc0296d",
    "edges.parquet": "4e71dde5cd3115bcb26e91202665532ee6581cf8233a9fc8059ea59fb7358a38",
    "transactions.parquet": "c30c5317b5439591dde86f2058dc47a3d19b2900c055ded994fe547f6fb7e7da",
}
BASE_WEIGHTS = {"M": 0.35, "A": 0.30, "C": 0.20, "H": 0.15}
COMPONENTS = ("M", "A", "C", "H")
BASELINE_P_TOP_20_HA121 = (
    100000003684369100,
    100000008346837100,
    100000000331309100,
    100000000437046100,
    100000004156082100,
    100000008603629100,
    100000002957787100,
    100000005910114100,
    100000000343175100,
    100000001857829100,
    100000003016635100,
    100000006866783100,
    100000008477350100,
    100000008686313100,
    100000008547844100,
    100000008547948100,
    100000008165763100,
    100000008710791100,
    100000003242289100,
    100000004400305100,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(path: Path) -> str:
    """Return the Git blob ID without invoking Git or depending on a checkout."""
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def normalized_weights(component: str, factor: float) -> dict[str, float]:
    weights = dict(BASE_WEIGHTS)
    weights[component] *= factor
    total = sum(weights.values())
    return {key: weights[key] / total for key in COMPONENTS}


def ranked_ids(
    gids: np.ndarray,
    roles: np.ndarray,
    components: dict[str, np.ndarray],
    weights: dict[str, float],
) -> tuple[list[int], np.ndarray, list[str]]:
    scores = np.zeros(len(gids), dtype=np.float64)
    for component in COMPONENTS:
        scores += weights[component] * components[component]
    scores = np.clip(scores, 0.0, 1.0)
    order = np.lexsort((gids, -scores))
    ranked = [int(gids[position]) for position in order]
    ranked_roles = [str(roles[position]) for position in order]
    return ranked, scores, ranked_roles


def top_role_counts(top_ids: list[int], role_by_gid: dict[int, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for gid in top_ids:
        role = role_by_gid[gid]
        counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        required=True,
        type=Path,
        help="directory containing the three official Parquet files",
    )
    args = parser.parse_args(argv)
    data_dir = args.data.resolve()

    input_hashes = {name: sha256(data_dir / name) for name in INPUT_FILES}
    mismatches = {
        name: {"expected": EXPECTED_INPUT_SHA256[name], "actual": input_hashes[name]}
        for name in INPUT_FILES
        if input_hashes[name] != EXPECTED_INPUT_SHA256[name]
    }
    if mismatches:
        print(
            "Official input SHA-256 mismatch; refusing to evaluate: "
            + json.dumps(mismatches, sort_keys=True),
            file=sys.stderr,
        )
        return 2

    # Existing release helpers print progress; keep stdout machine-readable.
    with redirect_stdout(sys.stderr):
        edges, nodes, tx = load(data_dir)
        sanity_check(edges, nodes, tx)
        graph = build_graph(edges, nodes)
        features = enrich_features(graph, basic_features(graph, nodes), tx)
    features, role_parameters = assign_roles(features)
    baseline, priority_parameters = compute_priority(features)

    gids = baseline["gid"].to_numpy(dtype=np.int64)
    roles = baseline["role"].to_numpy(dtype=object)
    components = {
        key: np.asarray(
            [float(row[key]) for row in baseline["score_components"]], dtype=np.float64
        )
        for key in COMPONENTS
    }
    if priority_parameters["weights"] != BASE_WEIGHTS:
        raise ValueError(
            "release weights differ from the fixed HA-12.2 baseline: "
            f"{priority_parameters['weights']}"
        )
    role_by_gid = {
        int(gid): str(role) for gid, role in zip(gids, roles)
    }
    p_top, p_scores, _ = ranked_ids(gids, roles, components, BASE_WEIGHTS)
    if not np.array_equal(p_scores, baseline["priority_score"].to_numpy(dtype=np.float64)):
        raise ValueError("reconstructed baseline scores differ from compute_priority")
    p_top_20 = p_top[:20]

    volume = baseline["in_tiyn"].to_numpy(dtype=np.int64) + baseline[
        "out_tiyn"
    ].to_numpy(dtype=np.int64)
    volume_order = np.lexsort((gids, -volume))
    v_top_20 = [int(gids[position]) for position in volume_order[:20]]
    p_set = set(p_top_20)
    v_set = set(v_top_20)

    scenarios = []
    scenario_vectors: dict[str, np.ndarray] = {}
    for component in COMPONENTS:
        for factor, label in ((0.8, "-20%"), (1.2, "+20%")):
            weights = normalized_weights(component, factor)
            ranked, scores, ranked_roles = ranked_ids(gids, roles, components, weights)
            top = ranked[:20]
            top_set = set(top)
            scenario_id = f"{component}{label}"
            digest_input = ",".join(str(gid) for gid in top).encode("ascii")
            scenarios.append(
                {
                    "scenario": scenario_id,
                    "weights": {key: round(weights[key], 12) for key in COMPONENTS},
                    "weight_sum": round(sum(weights.values()), 12),
                    "overlap_with_p_top_20": len(top_set & p_set),
                    "overlap_with_v_top_20": len(top_set & v_set),
                    "entered_vs_p_top_20": [gid for gid in top if gid not in p_set],
                    "left_vs_p_top_20": [gid for gid in p_top_20 if gid not in top_set],
                    "top_20_role_counts": top_role_counts(top, role_by_gid),
                    "role_assignment_changes": 0,
                    "top_20_gids": top,
                    "top_20_sha256": hashlib.sha256(digest_input).hexdigest(),
                }
            )
            scenario_vectors[scenario_id] = scores

    repeat_id = "M+20%"
    repeated_weights = normalized_weights("M", 1.2)
    repeated_ranked, repeated_scores, _ = ranked_ids(
        gids, roles, components, repeated_weights
    )
    repeat_equal = (
        np.array_equal(scenario_vectors[repeat_id], repeated_scores)
        and repeated_ranked[:20] == next(
            row["top_20_gids"] for row in scenarios if row["scenario"] == repeat_id
        )
    )

    source_path = Path(__file__).resolve()
    code_hashes = {
        name: {
            "git_blob_sha1": git_blob_sha1(REPO_ROOT / "hackalem" / name),
            "sha256": sha256(REPO_ROOT / "hackalem" / name),
        }
        for name in ("validation.py", "graph.py", "scoring.py")
    }
    code_hashes["evaluate_sensitivity.py"] = {
        "git_blob_sha1": git_blob_sha1(source_path),
        "sha256": sha256(source_path),
    }

    result = {
        "analysis": "HA-12.2 weight sensitivity",
        "spec": "1.4 section 10.4",
        "environment": {
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "pandas": version("pandas"),
            "networkx": version("networkx"),
            "pyarrow": version("pyarrow"),
        },
        "dataset": {
            "node_count": int(len(nodes)),
            "edge_count": int(len(edges)),
            "transaction_count": int(len(tx)),
            "input_sha256": input_hashes,
        },
        "code": code_hashes,
        "ranking": {
            "baseline_weights": BASE_WEIGHTS,
            "release_weights": priority_parameters["weights"],
            "release_formula": priority_parameters["priority_formula"],
            "fixed_role_parameters": role_parameters,
            "p_top_20_gids": p_top_20,
            "v_top_20_gids": v_top_20,
            "p_v_overlap": len(p_set & v_set),
            "ha121_p_top_20_match": p_top_20 == list(BASELINE_P_TOP_20_HA121),
            "ha121_p_top_20_overlap": len(p_set & set(BASELINE_P_TOP_20_HA121)),
            "baseline_top_20_role_counts": top_role_counts(p_top_20, role_by_gid),
        },
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "repeat_check": {
            "scenario": repeat_id,
            "same_all_scores_and_ordered_top_20": bool(repeat_equal),
            "top_20_sha256": next(
                row["top_20_sha256"] for row in scenarios if row["scenario"] == repeat_id
            ),
        },
        "scope": {
            "weight_scenarios": 8,
            "role_threshold_scenarios": 0,
            "community_membership_scenarios": 0,
            "notes": [
                "Role rules, thresholds, and all feature values are fixed to the release baseline.",
                "Overlap describes ranking stability, not AML accuracy or usefulness.",
                "V-top-20 is an independent volume-only ranking, not ground truth.",
            ],
        },
    }
    if not repeat_equal:
        print("Repeated scenario did not match exactly", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
