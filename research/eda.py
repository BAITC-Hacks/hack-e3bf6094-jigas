"""Quick EDA for the HackAlem 'Graph of money' dataset.
Writes ASCII-only report to eda_out.txt (console on this machine is CP1251).
Usage: python eda.py <data_dir>
"""
import sys, io, os, time, json, math
from collections import Counter, defaultdict

t0 = time.time()
data_dir = sys.argv[1] if len(sys.argv) > 1 else "data_unz/data"
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eda_out.txt")
lines = []
def w(s=""):
    lines.append(str(s))

import pandas as pd
import numpy as np
w(f"pandas {pd.__version__}, numpy {np.__version__}")
try:
    import pyarrow; w(f"pyarrow {pyarrow.__version__}")
except Exception as e:
    w(f"pyarrow missing: {e}")
import networkx as nx
w(f"networkx {nx.__version__}")
try:
    import igraph; w(f"igraph {igraph.__version__}")
except Exception as e:
    w(f"igraph missing: {e}")
try:
    import scipy; w(f"scipy {scipy.__version__}")
except Exception as e:
    w(f"scipy missing: {e}")

E = pd.read_parquet(os.path.join(data_dir, "edges.parquet"))
N = pd.read_parquet(os.path.join(data_dir, "nodes.parquet"))
T = pd.read_parquet(os.path.join(data_dir, "transactions.parquet"))
w("\n=== SHAPES ===")
w(f"edges {E.shape}, nodes {N.shape}, tx {T.shape}")
w("edges dtypes: " + str(dict(E.dtypes.astype(str))))
w("nodes dtypes: " + str(dict(N.dtypes.astype(str))))
w("tx dtypes: " + str(dict(T.dtypes.astype(str))))
w("edges head:\n" + E.head(5).to_string())
w("nodes head:\n" + N.head(5).to_string())
w("tx head:\n" + T.head(5).to_string())

w("\n=== BASIC CONSISTENCY ===")
w(f"total sum edges = {E.sum_kzt.sum():,.0f}; total sum tx = {T.sum_kzt.sum():,.0f}; n_tx sum = {E.n_tx.sum()}")
w(f"unique pairs in tx = {T.groupby(['src','dst']).ngroups}; edges rows = {len(E)}; duplicate edge rows = {E.duplicated(['src','dst']).sum()}")
agg = T.groupby(["src","dst"]).agg(sum_tx=("sum_kzt","sum"), n=("sum_kzt","size")).reset_index()
m = E.merge(agg, on=["src","dst"], how="outer", indicator=True)
w("merge edges<->tx: " + str(m["_merge"].value_counts().to_dict()))
both = m[m._merge=="both"]
w(f"pairs where |sum_kzt - sum_tx| > 1: {(abs(both.sum_kzt-both.sum_tx)>1).sum()}; n_tx mismatch: {(both.n_tx!=both.n).sum()}")
w(f"self loops: {(E.src==E.dst).sum()}")
w(f"nodes in edges not in nodes.parquet: {len((set(E.src)|set(E.dst)) - set(N.gid))}; nodes.parquet not in edges: {len(set(N.gid) - (set(E.src)|set(E.dst)))}")
w(f"seed count: {N.is_seed.sum()}; depth dist: {N.depth.value_counts().sort_index().to_dict()}")
w(f"edge depth dist: {E.depth.value_counts().sort_index().to_dict()}")
w(f"tx date range: {T.date.min()} .. {T.date.max()}; tx per date:\n{T.groupby('date').size().to_string()}")
w(f"min tx amount: {T.sum_kzt.min()}, max: {T.sum_kzt.max():,.0f}")
w("tx amount quantiles: " + str(T.sum_kzt.quantile([.05,.1,.25,.5,.75,.9,.95,.99]).round(0).to_dict()))
w(f"tx in [5000, 6000): {((T.sum_kzt>=5000)&(T.sum_kzt<6000)).sum()}; round to 1000: {(T.sum_kzt%1000==0).sum()}; round to 10000: {(T.sum_kzt%10000==0).sum()}")
w("edge sum quantiles: " + str(E.sum_kzt.quantile([.05,.25,.5,.75,.9,.95,.99]).round(0).to_dict()))
w("n_tx per edge dist: " + str(E.n_tx.value_counts().sort_index().head(15).to_dict()))

# edge depth vs node depth consistency
nd = dict(zip(N.gid, N.depth))
E["src_depth"] = E.src.map(nd); E["dst_depth"] = E.dst.map(nd)
w("edge depth - src_depth dist: " + str((E.depth - E.src_depth).value_counts().sort_index().to_dict()))
w("dst_depth - src_depth dist: " + str((E.dst_depth - E.src_depth).value_counts().sort_index().to_dict()))

w("\n=== NODE METRICS ===")
G = nx.DiGraph()
G.add_nodes_from(N.gid.tolist())
for r in E.itertuples(index=False):
    G.add_edge(int(r.src), int(r.dst), w=float(r.sum_kzt), n=int(r.n_tx))
in_deg = dict(G.in_degree()); out_deg = dict(G.out_degree())
in_sum = dict(G.in_degree(weight="w")); out_sum = dict(G.out_degree(weight="w"))
df = N.copy()
df["in_deg"] = df.gid.map(in_deg); df["out_deg"] = df.gid.map(out_deg)
df["in_sum"] = df.gid.map(in_sum); df["out_sum"] = df.gid.map(out_sum)
df["ratio"] = np.where(df.in_sum>0, df.out_sum/df.in_sum.replace(0,np.nan), np.nan)
w("in_deg dist (unique payers): " + str(df.in_deg.value_counts().sort_index().to_dict()))
w("out_deg dist (unique payees) top: " + str(df.out_deg.value_counts().sort_index().to_dict()))
w(f"nodes with in_deg>=5: {(df.in_deg>=5).sum()}, >=8: {(df.in_deg>=8).sum()}, >=10: {(df.in_deg>=10).sum()}; max in_deg {df.in_deg.max()}")
w(f"nodes with out_deg>=5: {(df.out_deg>=5).sum()}, >=10: {(df.out_deg>=10).sum()}, >=20: {(df.out_deg>=20).sum()}, >=60: {(df.out_deg>=60).sum()}; max out_deg {df.out_deg.max()}")
w(f"nodes out_deg==0: {(df.out_deg==0).sum()}; depth4 & out_deg==0: {((df.depth==4)&(df.out_deg==0)).sum()}; depth<4 & out_deg==0 & in_deg>0: {((df.depth<4)&(df.out_deg==0)&(df.in_deg>0)).sum()}")
w(f"nodes in_deg==0: {(df.in_deg==0).sum()} (seeds among them: {((df.in_deg==0)&df.is_seed).sum()})")
w(f"nodes with in>0 and out>0: {((df.in_sum>0)&(df.out_sum>0)).sum()}; ratio in [0.8,1.2]: {df.ratio.between(0.8,1.2).sum()}; ratio>1.2: {(df.ratio>1.2).sum()}; ratio<0.8 (& out>0): {((df.ratio<0.8)&(df.out_sum>0)).sum()}")
w(f"nodes giving more than received (out_sum > in_sum): {(df.out_sum>df.in_sum).sum()}; among non-seed: {((df.out_sum>df.in_sum)&(~df.is_seed)).sum()}")
w("ratio quantiles (in>0&out>0): " + str(df.ratio.dropna().quantile([.1,.25,.5,.75,.9]).round(2).to_dict()))
seeds = df[df.is_seed]
w(f"seeds: with out edges {(seeds.out_deg>0).sum()}, only receivers {((seeds.out_deg==0)&(seeds.in_deg>0)).sum()}, isolated {((seeds.out_deg==0)&(seeds.in_deg==0)).sum()}")
w(f"seed->seed edges: {E[E.src.isin(seeds.gid)&E.dst.isin(seeds.gid)].shape[0]}; edges into seeds: {E.dst.isin(seeds.gid).sum()}")
w("in_sum quantiles: " + str(df.in_sum.quantile([.5,.75,.9,.95,.99]).round(0).to_dict()))
w("out_sum quantiles: " + str(df.out_sum.quantile([.5,.75,.9,.95,.99]).round(0).to_dict()))
w("\nTOP 15 by in_deg:\n" + df.sort_values("in_deg", ascending=False).head(15).to_string())
w("\nTOP 15 by out_deg:\n" + df.sort_values("out_deg", ascending=False).head(15).to_string())
w("\nTOP 15 by in_sum:\n" + df.sort_values("in_sum", ascending=False).head(15).to_string())
w("\nTOP 15 by out_sum:\n" + df.sort_values("out_sum", ascending=False).head(15).to_string())

w("\n=== STRUCTURE ===")
wcc = sorted((len(c) for c in nx.weakly_connected_components(G)), reverse=True)
w(f"WCC count {len(wcc)}; sizes {wcc[:20]}")
scc = sorted((len(c) for c in nx.strongly_connected_components(G)), reverse=True)
w(f"SCC count {len(scc)}; sizes>1 {[s for s in scc if s>1][:20]}")
recip = sum(1 for u,v in G.edges() if G.has_edge(v,u))
w(f"reciprocal edge pairs (directed edges with reverse): {recip}")
try:
    cyc = list(nx.simple_cycles(G, length_bound=4))
    w(f"simple cycles len<=4: {len(cyc)}; by length {Counter(len(c) for c in cyc)}; examples {cyc[:5]}")
except TypeError:
    w("simple_cycles length_bound unsupported (old networkx)")
comp_of = {}
for i,c in enumerate(nx.weakly_connected_components(G)):
    for n_ in c: comp_of[n_] = i
df["wcc"] = df.gid.map(comp_of)
w("seeds per WCC (top): " + str(df[df.is_seed].wcc.value_counts().head(20).to_dict()))
w("size per WCC (top): " + str(df.wcc.value_counts().head(20).to_dict()))

w("\n=== CENTRALITY ===")
t1=time.time()
pr = nx.pagerank(G, weight="w")
w(f"pagerank {time.time()-t1:.2f}s; top10 {sorted(pr.items(), key=lambda x:-x[1])[:10]}")
t1=time.time()
bt = nx.betweenness_centrality(G, normalized=True)
w(f"betweenness {time.time()-t1:.2f}s; top10 {sorted(bt.items(), key=lambda x:-x[1])[:10]}")
try:
    t1=time.time()
    h,a = nx.hits(G, max_iter=500)
    w(f"hits {time.time()-t1:.2f}s; top hubs {sorted(h.items(), key=lambda x:-x[1])[:8]}; top auth {sorted(a.items(), key=lambda x:-x[1])[:8]}")
except Exception as e:
    w(f"hits failed: {e}")
try:
    pers = {n_: (1.0 if s else 0.0) for n_, s in zip(N.gid, N.is_seed)}
    ppr = nx.pagerank(G, weight="w", personalization=pers)
    w(f"PPR from seeds top10 {sorted(ppr.items(), key=lambda x:-x[1])[:10]}")
    Gr = G.reverse(copy=True)
    rppr = nx.pagerank(Gr, weight="w", personalization=pers)
    w(f"reverse PPR (upstream of seeds) top10 {sorted(rppr.items(), key=lambda x:-x[1])[:10]}")
except Exception as e:
    w(f"ppr failed: {e}")
core = nx.core_number(G.to_undirected())
w("k-core dist: " + str(Counter(core.values())))

w("\n=== COMMUNITIES ===")
U = nx.Graph()
for u,v,d in G.edges(data=True):
    if U.has_edge(u,v): U[u][v]["w"] += d["w"]
    else: U.add_edge(u,v,w=d["w"])
U.add_nodes_from(G.nodes())
for seed in (0, 42):
    t1=time.time()
    comms = nx.community.louvain_communities(U, weight="w", seed=seed)
    sizes = sorted((len(c) for c in comms), reverse=True)
    sset = set(seeds.gid)
    multi = sum(1 for c in comms if len(c & sset) > 1)
    w(f"louvain(seed={seed}) {time.time()-t1:.2f}s: {len(comms)} comms; sizes {sizes[:15]}; comms with >1 seed: {multi}; with >=1 seed: {sum(1 for c in comms if c & sset)}; modularity {nx.community.modularity(U, comms, weight='w'):.3f}")
try:
    t1=time.time()
    comms2 = nx.community.louvain_communities(U, weight=None, seed=42)
    w(f"louvain unweighted: {len(comms2)} comms; sizes {sorted((len(c) for c in comms2), reverse=True)[:15]}")
except Exception as e:
    w(f"unweighted louvain failed {e}")

w("\n=== TEMPORAL ===")
T["date"] = pd.to_datetime(T.date)
first_in = T.groupby("dst").date.min(); first_out = T.groupby("src").date.min()
last_in = T.groupby("dst").date.max(); last_out = T.groupby("src").date.max()
lag = (first_out - first_in).dt.days.dropna()
w(f"nodes with both in&out tx: {len(lag)}; lag(first_out - first_in) days dist: {lag.value_counts().sort_index().head(20).to_dict()}")
w(f"lag<=1 day: {(lag<=1).sum()}, <=2: {(lag<=2).sum()}, negative (out before in): {(lag<0).sum()}")
# per-node per-day inflow from multiple payers
dd = T.groupby(["dst","date"]).agg(n_payers=("src","nunique"), s=("sum_kzt","sum")).reset_index()
w(f"node-days with >=3 distinct payers same day: {(dd.n_payers>=3).sum()}; >=5: {(dd.n_payers>=5).sum()}; top: {dd.sort_values('n_payers',ascending=False).head(8).to_string()}")
# pass-through within 2 days: for nodes with in and out, share of out amount that occurs within 2 days after an inflow
act = T.groupby("date").agg(n=("sum_kzt","size"), s=("sum_kzt","sum"))
w("daily activity:\n" + act.to_string())
w(f"tx per src quantiles: {T.groupby('src').size().quantile([.5,.9,.99]).to_dict()}; max {T.groupby('src').size().max()}")
# repeated same amount splits
same = T.groupby(["src","date","sum_kzt"]).size()
w(f"same src+date+amount repeated >=3 times (splitting): {(same>=3).sum()}")

w(f"\nTOTAL EDA TIME {time.time()-t0:.1f}s")
with io.open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written", out_path, "lines", len(lines))
