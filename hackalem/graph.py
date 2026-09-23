"""Construct the directed graph and observed node features."""

from collections import deque
from datetime import date
import time
import numpy as np
import pandas as pd
import networkx as nx

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


def enrich_temporal_routes(G: nx.DiGraph, df: pd.DataFrame, tx: pd.DataFrame):
    """Count date-compatible seed reach and keep one verifiable route per mode."""
    if {int(gid) for gid in df["gid"]} != set(G.nodes):
        raise ValueError("temporal route rows must cover exactly the graph nodes")
    required = {"src", "dst", "date", "sum_tiyn"}
    if not required.issubset(tx.columns):
        raise ValueError("temporal routes require validated transaction rows")
    events = []
    for row_number, row in enumerate(tx[["src", "dst", "date", "sum_tiyn"]].itertuples(index=False, name=None)):
        src, dst, day, amount = row
        day = pd.Timestamp(day).date()
        amount = int(amount)
        if amount <= 0 or amount > 2**53 - 1 or (int(src), int(dst)) not in G.edges:
            raise ValueError("temporal route transaction is invalid")
        events.append((day, int(src), int(dst), amount, row_number))
    events.sort()

    gids = [int(gid) for gid in df["gid"]]
    counts = {mode: {gid: 0 for gid in gids} for mode in ("strict", "same_day")}
    routes = {str(gid): {"strict": None, "same_day": None} for gid in gids}
    seeds = sorted(int(row.gid) for row in df[["gid", "is_seed"]].itertuples(index=False) if bool(row.is_seed))
    for seed in seeds:
        for mode in ("strict", "same_day"):
            arrivals = {seed: (date.min, ())}
            reached = set()
            for _ in range(4):
                following = {}
                for event_index, (day, src, dst, _amount, _row_number) in enumerate(events):
                    previous = arrivals.get(src)
                    if previous is None or (day < previous[0] if mode == "same_day" else day <= previous[0]):
                        continue
                    existing = following.get(dst)
                    if existing is None or day < existing[0]:
                        following[dst] = (day, previous[1] + (event_index,))
                for gid, (_day, path) in following.items():
                    if gid == seed:
                        continue
                    reached.add(gid)
                    slot = routes[str(gid)][mode]
                    if slot is None:
                        routes[str(gid)][mode] = {
                            "seed_gid": str(seed),
                            "steps": [
                                {"src": str(events[i][1]), "dst": str(events[i][2]),
                                 "date": events[i][0].isoformat(), "sum_tiyn": events[i][3],
                                 "tx_row": events[i][4]}
                                for i in path
                            ],
                        }
                arrivals = following
            for gid in reached:
                counts[mode][gid] += 1

    enriched = df.copy()
    enriched["seed_reach_strict_count"] = enriched["gid"].map(counts["strict"]).astype("int64")
    enriched["seed_reach_same_day_count"] = enriched["gid"].map(counts["same_day"]).astype("int64")
    return enriched, routes


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
