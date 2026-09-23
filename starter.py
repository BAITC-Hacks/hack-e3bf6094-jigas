#!/usr/bin/env python3
"""
Стартовый код кейса «Граф денег» — HackAlem AI.

Что он делает:
  1. грузит три parquet-файла и проверяет их консистентность;
  2. собирает направленный взвешенный граф;
  3. считает БАЗОВЫЕ метрики узлов (степени, обороты, PageRank);
  4. пишет три выгрузки в требуемой ТЗ схеме — с ПУСТЫМИ ролями.

Чего он НЕ делает — это ваша работа:
  * не присваивает роли,
  * не кластеризует,
  * не ранжирует узлы,
  * не рисует граф.

Запуск:
    python starter.py --data ../data --out ./out
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx

ROLES = ["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]


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

def build_graph(edges) -> nx.DiGraph:
    """Направленный граф. sum_kzt — вес ребра, n_tx — количество переводов."""
    G = nx.DiGraph()
    for r in edges.itertuples(index=False):
        G.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return G


def basic_features(G: nx.DiGraph, nodes: pd.DataFrame) -> pd.DataFrame:
    """Базовые метрики. Это старт, а не финиш — добавляйте свои."""
    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())
    in_kzt = dict(G.in_degree(weight="sum_kzt"))
    out_kzt = dict(G.out_degree(weight="sum_kzt"))
    in_tx = dict(G.in_degree(weight="n_tx"))
    out_tx = dict(G.out_degree(weight="n_tx"))
    pr = nx.pagerank(G, weight="sum_kzt")

    df = nodes[["gid", "depth", "is_seed"]].copy()
    df["in_deg"] = df.gid.map(in_deg).fillna(0).astype(int)
    df["out_deg"] = df.gid.map(out_deg).fillna(0).astype(int)
    df["in_kzt"] = df.gid.map(in_kzt).fillna(0.0)
    df["out_kzt"] = df.gid.map(out_kzt).fillna(0.0)
    df["in_tx"] = df.gid.map(in_tx).fillna(0).astype(int)
    df["out_tx"] = df.gid.map(out_tx).fillna(0).astype(int)
    df["pagerank"] = df.gid.map(pr).fillna(0.0)

    # доля полученного, которая ушла дальше. Около 1.0 — деньги не задерживаются.
    df["pass_through"] = np.where(df.in_kzt > 0, df.out_kzt / df.in_kzt.replace(0, np.nan), np.nan)

    # ЛОВУШКА КЕЙСА: узел на 4-м колене без исходящих может быть не «стоком»,
    # а просто местом, где закончился обход. Разберитесь с этим.
    df["truncated_by_depth"] = (df.depth == 4) & (df.out_deg == 0)
    return df


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
        df[["gid", "in_deg", "out_deg", "in_kzt", "out_kzt", "pagerank",
            "pass_through", "depth", "is_seed", "truncated_by_depth"]],
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
    print(f"  узлов, обрезанных 4-м коленом               : {df.truncated_by_depth.sum()}  <- разберитесь")
    print(f"  слабосвязных компонент                      : {nx.number_weakly_connected_components(G)}")
    print("""
  Вопросы, на которые стоит ответить метриками:
    * чем «деньги пришли и остались» отличается от «пришли и ушли дальше»?
    * что важнее для роли — количество плательщиков или сумма?
    * узел собирает средства от нескольких SEED — это случайность или структура?
    * если убрать узел, сеть распадётся или переживёт?

  Полезное в networkx: pagerank, hits, betweenness_centrality,
  community.louvain_communities, simple_cycles, all_simple_paths.
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
    G = build_graph(edges)
    df = basic_features(G, nodes)
    write_outputs(df, Path(a.out))
    hints(G, df)


if __name__ == "__main__":
    main()
