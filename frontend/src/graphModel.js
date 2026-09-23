export const NO_CLUSTER_FILTER = '__no_cluster__';

export function matchesGraphFilters(node, filters) {
  if (!node) return false;
  if (filters.role !== 'all') {
    if (filters.roleMode === 'any' && filters.role !== 'peripheral' && node.role !== 'peripheral') {
      const matchedRoles = Array.isArray(node.matched_roles) ? node.matched_roles : [];
      const matchesAnyRule = matchedRoles.some((match) => match?.role === filters.role);
      // P0/older reports may not carry matched_roles; in that case only the
      // stored primary role is available and remains a useful fallback.
      if (matchedRoles.length > 0 ? !matchesAnyRule : node.role !== filters.role) return false;
    } else if (node.role !== filters.role) {
      return false;
    }
  }
  if (filters.cluster !== 'all') {
    const cluster = node.cluster_id === null || node.cluster_id === undefined
      ? NO_CLUSTER_FILTER
      : String(node.cluster_id);
    if (cluster !== filters.cluster) return false;
  }
  if (filters.seedMode === 'seed' && node.is_seed !== true) return false;
  if (filters.seedMode === 'others' && node.is_seed === true) return false;
  return true;
}

export function filterTopNodes(topNodes, nodeIndex, filters) {
  return topNodes.slice(0, 20).filter((entry) => {
    if (!entry || typeof entry.gid !== 'string') return false;
    const node = nodeIndex.get(entry.gid);
    return node && matchesGraphFilters(node, filters);
  });
}

export function createEdgeWidthScale(edges) {
  let minimum = Infinity;
  let maximum = -Infinity;
  edges.forEach((edge) => {
    const amount = edge?.sum_tiyn;
    if (typeof amount === 'number' && Number.isFinite(amount) && amount > 0) {
      minimum = Math.min(minimum, amount);
      maximum = Math.max(maximum, amount);
    }
  });
  if (minimum === Infinity) return () => 1.25;
  if (minimum === maximum) return (amount) => amount > 0 ? 2.875 : 1.25;

  const logMinimum = Math.log1p(minimum);
  const logRange = Math.log1p(maximum) - logMinimum;
  return (amount) => {
    if (typeof amount !== 'number' || !Number.isFinite(amount) || amount <= 0) return 1.25;
    return 1.25 + 3.25 * (Math.log1p(amount) - logMinimum) / logRange;
  };
}

export function nodesWithExactPriority(report, selected) {
  if (!selected || typeof selected.priority_score !== 'number' || !Number.isFinite(selected.priority_score)) return [];
  return report.nodes.filter((node) => node.priority_score === selected.priority_score);
}

export function buildGraphScope(report, selectedGid, filters) {
  const nodes = report.nodes;
  const nodeIndex = new Map(nodes.map((node) => [node.gid, node]));
  const filteredNodes = nodes.filter((node) => matchesGraphFilters(node, filters));
  const selectedNode = typeof selectedGid === 'string' ? nodeIndex.get(selectedGid) || null : null;
  const selectionOutsideFilters = Boolean(selectedNode && !matchesGraphFilters(selectedNode, filters));
  const visibleIds = new Set();
  const visibleEdges = [];

  if (filters.graphScope === 'full') {
    filteredNodes.forEach((node) => visibleIds.add(node.gid));
    if (selectedNode) visibleIds.add(selectedNode.gid);
    report.edges.forEach((edge, index) => {
      if (!edge || !nodeIndex.has(edge.src) || !nodeIndex.has(edge.dst)) return;
      if (visibleIds.has(edge.src) && visibleIds.has(edge.dst)) visibleEdges.push({ edge, index });
    });
  } else if (selectedNode) {
    visibleIds.add(selectedNode.gid);
    report.edges.forEach((edge, index) => {
      if (!edge || (edge.src !== selectedNode.gid && edge.dst !== selectedNode.gid)) return;
      if (!nodeIndex.has(edge.src) || !nodeIndex.has(edge.dst)) return;
      const otherGid = edge.src === selectedNode.gid ? edge.dst : edge.src;
      const otherNode = nodeIndex.get(otherGid);
      if (!otherNode || (otherGid !== selectedNode.gid && !matchesGraphFilters(otherNode, filters))) return;
      visibleIds.add(otherGid);
      visibleEdges.push({ edge, index });
    });
  }

  const visibleNodes = nodes.filter((node) => visibleIds.has(node.gid));
  const signature = JSON.stringify([
    visibleNodes.map((node) => node.gid),
    visibleEdges.map(({ index }) => index),
  ]);

  return {
    nodeIndex,
    filteredNodes,
    visibleNodes,
    visibleEdges,
    selectedNode,
    selectionOutsideFilters,
    signature,
  };
}
