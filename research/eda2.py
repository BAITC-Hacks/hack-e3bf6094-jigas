import sys, io, os, time
from collections import Counter
from itertools import combinations
import pandas as pd, numpy as np, networkx as nx

d = sys.argv[1]
E = pd.read_parquet(f"{d}/edges.parquet"); N = pd.read_parquet(f"{d}/nodes.parquet"); T = pd.read_parquet(f"{d}/transactions.parquet")
T["date"] = pd.to_datetime(T.date)
L = []
def w(s=""):
    L.append(str(s))

G = nx.DiGraph(); G.add_nodes_from(N.gid.tolist())
for r in E.itertuples(index=False):
    G.add_edge(int(r.src), int(r.dst), w=float(r.sum_kzt), n=int(r.n_tx))
df = N.copy()
df["in_deg"] = df.gid.map(dict(G.in_degree())); df["out_deg"] = df.gid.map(dict(G.out_degree()))
df["in_sum"] = df.gid.map(dict(G.in_degree(weight="w"))); df["out_sum"] = df.gid.map(dict(G.out_degree(weight="w")))
df["in_tx"] = df.gid.map(dict(G.in_degree(weight="n"))); df["out_tx"] = df.gid.map(dict(G.out_degree(weight="n")))
seeds = set(N[N.is_seed].gid)

w("=== endpoint check (fixed precedence) ===")
w(f"edge endpoints not in nodes: {len((set(E.src)|set(E.dst)) - set(N.gid))}; unique src: {E.src.nunique()}; unique dst: {E.dst.nunique()}")

w("\n=== has_outflow rate by depth (prior for frontier nodes) ===")
for dep in range(0, 5):
    s = df[df.depth == dep]
    w(f"depth {dep}: n={len(s)}, has_out={(s.out_deg>0).sum()} ({(s.out_deg>0).mean():.2f}), in_deg mean {s.in_deg.mean():.2f}, in_sum median {s.in_sum.median():,.0f}, in_deg>=3: {(s.in_deg>=3).sum()}")
w("depth1-3 has_out rate by in_deg bucket:")
s = df[df.depth.between(1, 3)].copy()
s["b"] = pd.cut(s.in_deg, [0, 1, 2, 4, 100], labels=["1", "2", "3-4", "5+"])
w(s.groupby("b", observed=True).agg(n=("gid", "size"), has_out=("out_deg", lambda x: (x > 0).mean()), in_sum_med=("in_sum", "median")).to_string())
s["sb"] = pd.qcut(s.in_sum, [0, .25, .5, .75, .9, 1], duplicates="drop")
w(s.groupby("sb", observed=True).agg(n=("gid", "size"), has_out=("out_deg", lambda x: (x > 0).mean())).to_string())

w("\n=== seed-payer counts (collecting from couriers) ===")
sp = E[E.src.isin(seeds)].groupby("dst").agg(n_seed_payers=("src", "nunique"), s=("sum_kzt", "sum")).reset_index()
w(f"nodes receiving from >=2 distinct seeds: {(sp.n_seed_payers>=2).sum()}; >=3: {(sp.n_seed_payers>=3).sum()}; >=5: {(sp.n_seed_payers>=5).sum()}")
w(sp.sort_values("n_seed_payers", ascending=False).head(12).merge(df[["gid", "depth", "is_seed", "in_deg", "out_deg", "in_sum", "out_sum"]], left_on="dst", right_on="gid").drop(columns="gid").to_string())

w("\n=== largest SCC composition ===")
sccs = sorted(nx.strongly_connected_components(G), key=len, reverse=True); big = sccs[0]
s = df[df.gid.isin(big)]
w(f"size {len(big)}; seeds {s.is_seed.sum()}; depth dist {s.depth.value_counts().sort_index().to_dict()}; in_deg mean {s.in_deg.mean():.1f}; out_deg mean {s.out_deg.mean():.1f}; total in {s.in_sum.sum():,.0f}; total out {s.out_sum.sum():,.0f}")
w("SCC top by in_sum:\n" + s.sort_values("in_sum", ascending=False).head(10).to_string())
internal = E[E.src.isin(big) & E.dst.isin(big)]
outof = E[E.src.isin(big) & ~E.dst.isin(big)]; into = E[~E.src.isin(big) & E.dst.isin(big)]
w(f"internal SCC edges {len(internal)}, sum {internal.sum_kzt.sum():,.0f}; edges out of SCC {len(outof)} sum {outof.sum_kzt.sum():,.0f}; into SCC {len(into)} sum {into.sum_kzt.sum():,.0f}")

w("\n=== communities & bridging ===")
U = nx.Graph(); U.add_nodes_from(G.nodes())
for u, v, dd in G.edges(data=True):
    if U.has_edge(u, v): U[u][v]["w"] += dd["w"]
    else: U.add_edge(u, v, w=dd["w"])
comms = nx.community.louvain_communities(U, weight="w", seed=42)
cid = {n: i for i, c in enumerate(comms) for n in c}; df["cid"] = df.gid.map(cid)
for res in (0.5, 1.0, 2.0):
    c2 = nx.community.louvain_communities(U, weight="w", seed=42, resolution=res)
    w(f"resolution {res}: {len(c2)} comms, >1 seed: {sum(1 for c in c2 if len(c & seeds) > 1)}, sizes {sorted((len(c) for c in c2), reverse=True)[:10]}")
def nmi(a, b):
    try:
        from sklearn.metrics import normalized_mutual_info_score as f
        return f(a, b)
    except Exception:
        return float("nan")
parts = []
for sd in range(5):
    c = nx.community.louvain_communities(U, weight="w", seed=sd); m = {n: i for i, cc in enumerate(c) for n in cc}; parts.append([m[g] for g in N.gid])
w(f"NMI between louvain seeds: {[round(nmi(parts[i], parts[j]), 3) for i, j in combinations(range(5), 2)]}")
pc = E.merge(df[["gid", "cid"]].rename(columns={"gid": "src", "cid": "src_cid"}), on="src").merge(df[["gid", "cid"]].rename(columns={"gid": "dst", "cid": "dst_cid"}), on="dst")
br = pc[pc.src_cid != pc.dst_cid]
w(f"cross-community edges: {len(br)} ({len(br)/len(E):.1%}), sum {br.sum_kzt.sum():,.0f} ({br.sum_kzt.sum()/E.sum_kzt.sum():.1%})")
npc = pc.groupby("dst").src_cid.nunique()
w(f"nodes receiving from >=2 distinct communities: {(npc>=2).sum()}; >=3: {(npc>=3).sum()}")
w(npc.sort_values(ascending=False).head(10).to_string())

w("\n=== cluster summary (seed=42) ===")
cs = df.groupby("cid").agg(n=("gid", "size"), n_seed=("is_seed", "sum"), depth_mean=("depth", "mean")).sort_values("n", ascending=False)
w(f"clusters: {len(cs)}; size dist: {cs.n.describe().round(1).to_dict()}; clusters with n_seed==0: {(cs.n_seed==0).sum()}; singletons {(cs.n==1).sum()}")
w(cs.head(15).to_string())

w("\n=== temporal pass-through (FIFO approx, within 2 days) ===")
tin = T.groupby("dst"); tout = T.groupby("src")
rows = []
for g in df[(df.in_sum > 0) & (df.out_sum > 0)].gid:
    if g not in tin.groups or g not in tout.groups: continue
    i = tin.get_group(g); o = tout.get_group(g)
    idates = i.date.values
    fast = 0.0
    for r in o.itertuples(index=False):
        mask = idates <= np.datetime64(r.date)
        if mask.any():
            lag = (np.datetime64(r.date) - idates[mask]).min()
            if lag <= np.timedelta64(2, "D"): fast += r.sum_kzt
    rows.append((g, fast, o.sum_kzt.sum(), i.sum_kzt.sum()))
ft = pd.DataFrame(rows, columns=["gid", "out_fast", "out_sum", "in_sum"])
ft["fast_share"] = ft.out_fast / ft.out_sum; ft["fwd_share"] = np.minimum(ft.out_fast / ft.in_sum, 5)
w(f"nodes evaluated {len(ft)}; fast_share>=0.8: {(ft.fast_share>=0.8).sum()}; fast_share>=0.5: {(ft.fast_share>=0.5).sum()}; median fast_share {ft.fast_share.median():.2f}")
w(f"nodes with fwd_share (fast out / in) in [0.7,1.3]: {ft.fwd_share.between(0.7,1.3).sum()}")

w("\n=== structuring near threshold ===")
nt = T[(T.sum_kzt >= 5000) & (T.sum_kzt < 6000)].groupby("src").size().sort_values(ascending=False)
w(f"payers with >=3 tx in [5000,6000): {(nt>=3).sum()}; top {nt.head(8).to_dict()}")
sp2 = T.groupby(["src", "date"]).agg(n=("sum_kzt", "size"), s=("sum_kzt", "sum"), nd=("dst", "nunique")).reset_index()
w(f"src-days with >=5 tx: {(sp2.n>=5).sum()}; >=10: {(sp2.n>=10).sum()}; src-days with >=5 distinct payees: {(sp2.nd>=5).sum()}")

w("\n=== 2-hop chains through pass-through nodes ===")
df["ratio"] = np.where(df.in_sum > 0, df.out_sum / df.in_sum.replace(0, np.nan), np.nan)
tr = set(df[(df.ratio.between(0.8, 1.2)) & (~df.is_seed)].gid)
chains = sum(G.in_degree(b) * G.out_degree(b) for b in tr)
w(f"transit-like nodes (ratio 0.8-1.2, non-seed): {len(tr)}; A->B->C chains through them: {chains}")
w("transit-like nodes sample:\n" + df[df.gid.isin(tr)].sort_values("in_sum", ascending=False).head(10).to_string())

w("\n=== robustness: remove top-k by betweenness / out_sum ===")
bt = nx.betweenness_centrality(G)
def stats(H):
    wc = sorted((len(c) for c in nx.weakly_connected_components(H)), reverse=True)
    return len(wc), wc[0], sum(1 for x in wc if x == 1)
w(f"base: n_wcc, giant, singletons = {stats(G)}")
for k in (5, 10, 20):
    top = [n for n, _ in sorted(bt.items(), key=lambda x: -x[1])[:k]]; H = G.copy(); H.remove_nodes_from(top)
    w(f"remove top{k} betweenness: {stats(H)}; flow touched {E[E.src.isin(top)|E.dst.isin(top)].sum_kzt.sum()/E.sum_kzt.sum():.1%}")
    top2 = df.sort_values("out_sum", ascending=False).gid.head(k).tolist(); H = G.copy(); H.remove_nodes_from(top2)
    w(f"remove top{k} out_sum: {stats(H)}; flow touched {E[E.src.isin(top2)|E.dst.isin(top2)].sum_kzt.sum()/E.sum_kzt.sum():.1%}")

w("\n=== in_tx vs in_deg; frontier ===")
w(f"nodes with in_tx>=10: {(df.in_tx>=10).sum()}; in_tx>=10 & in_deg<=2: {((df.in_tx>=10)&(df.in_deg<=2)).sum()}")
w(f"share of flow into depth4 frontier nodes: {E[E.depth==4].sum_kzt.sum()/E.sum_kzt.sum():.1%}; frontier nodes with in_deg>=3: {((df.depth==4)&(df.in_deg>=3)).sum()}; frontier in_sum>=1M: {((df.depth==4)&(df.in_sum>=1e6)).sum()}")

io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eda2_out.txt"), "w", encoding="utf-8").write("\n".join(L))
print("ok", len(L))
