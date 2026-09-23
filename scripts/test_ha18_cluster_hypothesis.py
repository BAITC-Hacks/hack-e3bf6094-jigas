"""Focused checks for HA-18 cluster hypothesis text."""

import json
from pathlib import Path
import re
import unittest

import networkx as nx
import pandas as pd

from hackalem.clusters import _format_kzt, summarize_clusters
from hackalem.graph import build_graph
from hackalem.validation import load, sanity_check


FIRST = "\u041f\u0435\u0440\u0432\u044b\u0439 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 \u043f\u043e P:"
NO_SEED_CAVEAT = (
    "\u041e\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0438\u0435 seed "
    "\u043d\u0435 \u043e\u0437\u043d\u0430\u0447\u0430\u0435\u0442 \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u044c"
)
ISOLATE_TEXT = (
    "\u0431\u0435\u0437 \u0432\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u0438\u0445 \u0440\u0451\u0431\u0435\u0440"
)


class ClusterHypothesisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.first_gid = 100000000000000001
        cls.tied_gid = 100000000000000009
        cls.graph = nx.DiGraph()
        cls.graph.add_edge(cls.first_gid, cls.tied_gid, sum_tiyn=10000)
        cls.graph.add_edge(cls.tied_gid, cls.first_gid, sum_tiyn=5000)
        cls.graph.add_edge(7, cls.first_gid, sum_tiyn=333)
        cls.graph.add_edge(cls.first_gid, 9, sum_tiyn=250)
        cls.graph.add_node(11)
        cls.features = pd.DataFrame([
            {"gid": cls.first_gid, "cluster_id": 1, "role": "coordinator", "is_seed": True, "priority_score": 0.5},
            {"gid": cls.tied_gid, "cluster_id": 1, "role": "distributor", "is_seed": False, "priority_score": 0.5},
            {"gid": 7, "cluster_id": 2, "role": "peripheral", "is_seed": False, "priority_score": 0.4},
            {"gid": 9, "cluster_id": 3, "role": "terminal", "is_seed": False, "priority_score": 0.3},
            {"gid": 11, "cluster_id": 4, "role": "peripheral", "is_seed": False, "priority_score": 0.2},
        ])
        cls.summary = summarize_clusters(cls.graph, cls.features).set_index("cluster_id")

    def test_exact_numeric_gid_tie_break_and_complete_observed_totals(self):
        cluster = self.summary.loc[1]
        self.assertEqual(cluster["top_gids"][0], self.first_gid)
        self.assertIn(f"{FIRST} {self.first_gid}, \u0440\u043e\u043b\u044c coordinator", cluster["hypothesis"])
        self.assertIn("2 \u043f\u043b\u0430\u0442\u0435\u043b\u044c\u0449\u0438\u043a\u043e\u0432, 2 \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u0435\u0439", cluster["hypothesis"])
        self.assertIn("\u0432\u0445\u043e\u0434 53.33 KZT, \u0432\u044b\u0445\u043e\u0434 102.50 KZT", cluster["hypothesis"])

    def test_singleton_without_seed_but_with_edges_is_not_called_isolate(self):
        hypothesis = self.summary.loc[2, "hypothesis"]
        self.assertIn(FIRST + " 7,", hypothesis)
        self.assertIn(NO_SEED_CAVEAT, hypothesis)

    def test_true_isolate_keeps_no_observed_edges_text_without_candidate_claim(self):
        hypothesis = self.summary.loc[4, "hypothesis"]
        self.assertNotIn(FIRST, hypothesis)
        self.assertIn(ISOLATE_TEXT, hypothesis)
        self.assertIn(NO_SEED_CAVEAT, hypothesis)

    def test_official_csv_and_embedded_json_match_observed_graph_facts(self):
        data_dir = Path("data")
        out_dir = Path("out")
        required = [
            data_dir / "nodes.parquet",
            data_dir / "edges.parquet",
            out_dir / "clusters.csv",
            out_dir / "nodes_roles.csv",
            out_dir / "report.html",
        ]
        if not all(path.is_file() for path in required):
            self.skipTest("official pipeline output is not available")

        edges, nodes, transactions = load(data_dir)
        sanity_check(edges, nodes, transactions)
        graph = build_graph(edges, nodes)
        clusters = pd.read_csv(out_dir / "clusters.csv", encoding="utf-8", keep_default_na=False)
        node_roles = pd.read_csv(
            out_dir / "nodes_roles.csv", encoding="utf-8", keep_default_na=False,
            dtype={"gid": "string"},
        )
        html = (out_dir / "report.html").read_text(encoding="utf-8")
        match = re.search(
            r'<script type="application/json" id="report-data">(.*?)</script>', html, re.DOTALL
        )
        self.assertIsNotNone(match, "report.html must embed the generated report JSON")
        report = json.loads(match.group(1))
        json_clusters = {int(item["cluster_id"]): item for item in report["clusters"]}

        for row in clusters.itertuples(index=False):
            cluster_id = int(row.cluster_id)
            top_gids = json.loads(row.top_gids)
            first_gid = int(top_gids[0])
            feature = node_roles.loc[node_roles["gid"] == str(first_gid)].iloc[0]
            self.assertEqual(int(feature["cluster_id"]), cluster_id)

            hypothesis = str(row.hypothesis)
            self.assertEqual(hypothesis, json_clusters[cluster_id]["hypothesis"])
            incoming = list(graph.in_edges(first_gid, data=True))
            outgoing = list(graph.out_edges(first_gid, data=True))
            if not incoming and not outgoing:
                self.assertNotIn(FIRST, hypothesis)
            else:
                in_tiyn = sum(int(data["sum_tiyn"]) for _, _, data in incoming)
                out_tiyn = sum(int(data["sum_tiyn"]) for _, _, data in outgoing)
                expected = (
                    f"{FIRST} {first_gid}, \u0440\u043e\u043b\u044c {feature['role']}; "
                    f"{len(incoming)} \u043f\u043b\u0430\u0442\u0435\u043b\u044c\u0449\u0438\u043a\u043e\u0432, "
                    f"{len(outgoing)} \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u0435\u0439; "
                    f"\u0432\u0445\u043e\u0434 {_format_kzt(in_tiyn)} KZT, "
                    f"\u0432\u044b\u0445\u043e\u0434 {_format_kzt(out_tiyn)} KZT."
                )
                self.assertIn(expected, hypothesis)
            if int(row.n_seed) == 0:
                self.assertIn(NO_SEED_CAVEAT, hypothesis)


if __name__ == "__main__":
    unittest.main()
