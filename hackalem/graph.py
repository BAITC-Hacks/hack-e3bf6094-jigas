"""Construct the directed graph and observed node features."""

from collections import deque
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


def enrich_observed_features(
    G: nx.DiGraph,
    df: pd.DataFrame,
    tx: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, list[dict]]]:
    """Add direct seed payers and exact observed daily profiles for every node."""
    required_features = ("gid", "in_tiyn", "out_tiyn", "in_tx", "out_tx")
    missing_features = [column for column in required_features if column not in df.columns]
    if missing_features:
        raise ValueError(f"observed features are missing columns: {', '.join(missing_features)}")
    required_transactions = ("src", "dst", "date", "sum_tiyn")
    missing_transactions = [column for column in required_transactions if column not in tx.columns]
    if missing_transactions:
        raise ValueError(f"transactions are missing columns: {', '.join(missing_transactions)}")

    graph_ids = {int(gid) for gid in G.nodes}
    feature_ids = {int(gid) for gid in df["gid"]}
    if feature_ids != graph_ids or len(feature_ids) != len(df):
        raise ValueError("observed feature rows must cover graph nodes exactly once")
    if any("is_seed" not in G.nodes[gid] for gid in G.nodes):
        raise ValueError("graph nodes must include the is_seed attribute")

    # Each day stores [in_tiyn, out_tiyn, in_tx, out_tx, payers, payees].
    daily: dict[int, dict[str, list]] = {gid: {} for gid in graph_ids}
    dates = pd.to_datetime(tx["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("transactions.date contains an invalid date")
    for src_value, dst_value, date_value, amount_value in zip(
        tx["src"], tx["dst"], dates, tx["sum_tiyn"]
    ):
        src = int(src_value)
        dst = int(dst_value)
        if src not in graph_ids or dst not in graph_ids:
            raise ValueError("transaction endpoint is absent from graph nodes")
        if isinstance(amount_value, (bool, np.bool_)) or not isinstance(amount_value, (int, np.integer)):
            raise ValueError("transactions.sum_tiyn must contain exact integers")
        amount = int(amount_value)
        if amount <= 0:
            raise ValueError("transactions.sum_tiyn must be positive")
        day = pd.Timestamp(date_value).date().isoformat()

        incoming = daily[dst].setdefault(day, [0, 0, 0, 0, set(), set()])
        incoming[0] += amount
        incoming[2] += 1
        incoming[4].add(src)

        outgoing = daily[src].setdefault(day, [0, 0, 0, 0, set(), set()])
        outgoing[1] += amount
        outgoing[3] += 1
        outgoing[5].add(dst)

    result = df.copy()
    seed_payers = {}
    sync_payers_max = {}
    fanout_burst_max = {}
    daily_profiles_by_gid: dict[str, list[dict]] = {}
    for gid in sorted(graph_ids):
        seed_payers[gid] = sum(
            bool(G.nodes[payer]["is_seed"])
            for payer in G.predecessors(gid)
        )
        records = []
        for day in sorted(daily[gid]):
            in_tiyn, out_tiyn, in_tx, out_tx, payers, payees = daily[gid][day]
            records.append({
                "date": day,
                "in_tiyn": int(in_tiyn),
                "out_tiyn": int(out_tiyn),
                "in_tx": int(in_tx),
                "out_tx": int(out_tx),
                "n_payers": len(payers),
                "n_payees": len(payees),
            })
        daily_profiles_by_gid[str(gid)] = records
        sync_payers_max[gid] = max((record["n_payers"] for record in records), default=0)
        fanout_burst_max[gid] = max((record["n_payees"] for record in records), default=0)

    result["n_seed_payers"] = result["gid"].map(seed_payers).astype("int64")
    result["sync_payers_max"] = result["gid"].map(sync_payers_max).astype("int64")
    result["fanout_burst_max"] = result["gid"].map(fanout_burst_max).astype("int64")

    for row in result.itertuples(index=False):
        profile = daily_profiles_by_gid[str(int(row.gid))]
        for field in ("in_tiyn", "out_tiyn", "in_tx", "out_tx"):
            observed_total = sum(record[field] for record in profile)
            if observed_total != int(getattr(row, field)):
                raise ValueError(
                    f"daily {field} does not match node {int(row.gid)} aggregate"
                )

    return result, daily_profiles_by_gid


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
