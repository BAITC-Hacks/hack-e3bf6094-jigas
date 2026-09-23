#!/usr/bin/env python3
"""Compatibility CLI and public function facade for the supplied starter."""

from hackalem.validation import load, sanity_check
from hackalem.graph import build_graph, basic_features, enrich_features, hints
from hackalem.scoring import ROLES, assign_roles, compute_priority
from hackalem.clusters import LOUVAIN_RESOLUTION, LOUVAIN_SEED, cluster_nodes, summarize_clusters
from hackalem.report import build_report
from hackalem.exports import write_outputs
from hackalem.html import render_report
from hackalem.pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
