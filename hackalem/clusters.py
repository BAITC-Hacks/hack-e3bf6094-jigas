"""Discover connected communities and summarize their observed flows."""

import numpy as np
import pandas as pd
import networkx as nx

LOUVAIN_RESOLUTION = 1.0
LOUVAIN_SEED = 42

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
    role_by_gid = {int(row.gid): str(row.role) for row in df[["gid", "role"]].itertuples(index=False)}
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
        first_gid = top_gids[0]
        incoming = list(G.in_edges(first_gid, data=True))
        outgoing = list(G.out_edges(first_gid, data=True))
        is_isolate = n_nodes == 1 and not incoming and not outgoing
        role_counts = members["role"].value_counts().sort_index().sort_values(ascending=False, kind="mergesort")
        leading_roles = ", ".join(
            f"{role}={int(count)}" for role, count in role_counts.head(2).items()
        ) or "нет назначенных ролей"

        if is_isolate:
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

        if not is_isolate:
            in_tiyn = sum(int(data["sum_tiyn"]) for _, _, data in incoming)
            out_tiyn = sum(int(data["sum_tiyn"]) for _, _, data in outgoing)
            hypothesis += (
                f" Первый для проверки по P: {first_gid}, роль {role_by_gid[first_gid]}; "
                f"{len(incoming)} плательщиков, {len(outgoing)} получателей; "
                f"вход {_format_kzt(in_tiyn)} KZT, выход {_format_kzt(out_tiyn)} KZT."
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


def summarize_structure(G: nx.DiGraph, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Observed directed flows between Louvain communities and original-graph SCCs."""
    required = {"gid", "cluster_id", "is_seed", "depth"}
    if not required.issubset(df.columns):
        raise ValueError(f"structure requires: {', '.join(sorted(required - set(df.columns)))}")
    by_gid = {int(row.gid): row for row in df.itertuples(index=False)}
    if len(by_gid) != len(df) or set(by_gid) != {int(gid) for gid in G.nodes}:
        raise ValueError("structure requires one feature row per graph node")
    cluster = {gid: int(row.cluster_id) for gid, row in by_gid.items()}
    components = sorted((set(part) for part in nx.strongly_connected_components(G)), key=min)
    scc_by_gid = {int(gid): sid for sid, part in enumerate(components) for gid in part}
    scc_size = {sid: len(part) for sid, part in enumerate(components)}
    result = df.copy()
    result["n_payer_comms"] = result["gid"].map(lambda gid: len({cluster[int(src)] for src in G.predecessors(int(gid))})).astype("int64")
    result["scc_id"] = result["gid"].map(scc_by_gid).astype("int64")
    result["scc_size"] = result["scc_id"].map(scc_size).astype("int64")

    flows: dict[tuple[int, int], dict] = {}
    sccs = [{
        "scc_id": sid, "n_nodes": len(part),
        "n_seed": sum(bool(by_gid[int(gid)].is_seed) for gid in part),
        "n_edges_internal": 0, "n_edges_incoming": 0, "n_edges_outgoing": 0,
        "sum_tiyn_internal": 0, "sum_tiyn_incoming": 0, "sum_tiyn_outgoing": 0,
        "n_external_recipients": 0, "origin_turnover_share": 0.0,
    } for sid, part in enumerate(components)]
    recipients = [set() for _ in components]
    total = 0
    boundary_sum = 0
    for src, dst, data in G.edges(data=True):
        src, dst = int(src), int(dst)
        amount, count = int(data["sum_tiyn"]), int(data["n_tx"])
        if amount <= 0 or count <= 0:
            raise ValueError("structure edges require positive sum_tiyn and n_tx")
        total += amount
        if int(by_gid[dst].depth) == 4:
            boundary_sum += amount
        src_cluster, dst_cluster = cluster[src], cluster[dst]
        if src_cluster != dst_cluster:
            flow = flows.setdefault((src_cluster, dst_cluster), {
                "src_cluster_id": src_cluster, "dst_cluster_id": dst_cluster,
                "sum_tiyn": 0, "n_edges": 0, "n_tx": 0,
            })
            flow["sum_tiyn"] += amount
            flow["n_edges"] += 1
            flow["n_tx"] += count
        src_scc, dst_scc = scc_by_gid[src], scc_by_gid[dst]
        if src_scc == dst_scc:
            sccs[src_scc]["n_edges_internal"] += 1
            sccs[src_scc]["sum_tiyn_internal"] += amount
        else:
            sccs[src_scc]["n_edges_outgoing"] += 1
            sccs[src_scc]["sum_tiyn_outgoing"] += amount
            sccs[dst_scc]["n_edges_incoming"] += 1
            sccs[dst_scc]["sum_tiyn_incoming"] += amount
            recipients[src_scc].add(dst)
    for sid, item in enumerate(sccs):
        item["n_external_recipients"] = len(recipients[sid])
        item["origin_turnover_share"] = (
            (item["sum_tiyn_internal"] + item["sum_tiyn_outgoing"]) / total if total else 0.0
        )
    structure = {
        "community_edges": [flows[key] for key in sorted(flows)],
        "sccs": sccs,
        "coverage": {
            "boundary_n_nodes": sum(int(row.depth) == 4 for row in by_gid.values()),
            "sum_tiyn_to_boundary": boundary_sum,
            "share_to_boundary": boundary_sum / total if total else 0.0,
        },
    }
    return result, structure
