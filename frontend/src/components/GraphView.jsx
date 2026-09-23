import { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { formatInteger, formatTiyn } from '../report.js';
import { buildGraphScope, createEdgeWidthScale, NO_CLUSTER_FILTER } from '../graphModel.js';

const ROLE_COLORS = {
  coordinator: '#a95c32',
  distributor: '#bd7b22',
  consolidator: '#268777',
  transit: '#567bb4',
  terminal: '#8065a8',
  peripheral: '#77838d',
};
const ROLE_LABELS = {
  coordinator: 'Координатор',
  distributor: 'Распределитель',
  consolidator: 'Консолидатор',
  transit: 'Транзитный',
  terminal: 'Кандидат в конечные',
  peripheral: 'Периферийный',
};
const PAGE_SIZE = 50;
const NEIGHBORHOOD_EDGE_LIMIT = 32;

function clusterColor(clusterId) {
  if (clusterId === null || clusterId === undefined) return '#89949a';
  const value = String(clusterId);
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) hash = Math.imul(hash ^ value.charCodeAt(i), 16777619);
  const hue = (hash >>> 0) % 360;
  const saturation = 0.48;
  const lightness = 0.43;
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const x = chroma * (1 - Math.abs((hue / 60) % 2 - 1));
  const match = lightness - chroma / 2;
  const channels = hue < 60 ? [chroma, x, 0] : hue < 120 ? [x, chroma, 0] : hue < 180 ? [0, chroma, x] : hue < 240 ? [0, x, chroma] : hue < 300 ? [x, 0, chroma] : [chroma, 0, x];
  return '#' + channels.map((channel) => Math.round((channel + match) * 255).toString(16).padStart(2, '0')).join('');
}

function buildNodeItem(node, colorMode, position, sccFocusId) {
  const color = colorMode === 'cluster'
    ? clusterColor(node.cluster_id)
    : ROLE_COLORS[node.role] || '#687983';
  const focused = sccFocusId !== null && node.scc_id === sccFocusId;
  const border = focused ? '#1a7b70' : node.boundary === true ? '#bb6e30' : node.isolated === true ? '#53636b' : '#ffffff';
  const shape = node.isolated === true ? 'triangle' : node.is_seed === true ? 'diamond' : 'ellipse';
  const nodeTitle = [
    'ID: ' + node.gid,
    'Роль: ' + (ROLE_LABELS[node.role] || node.role || 'не указана'),
    'Кластер: ' + (node.cluster_id ?? '—'),
    'Приоритет: ' + (typeof node.priority_score === 'number' ? node.priority_score.toFixed(3) : '—'),
    node.is_seed === true ? 'Seed' : null,
    node.boundary === true ? 'Граница выгрузки' : null,
    node.isolated === true ? 'Изолят в этой выгрузке' : null,
  ].filter(Boolean);

  return {
    group: 'nodes',
    data: {
      id: node.gid,
      color,
      border,
      shape,
      size: node.is_seed === true ? 30 : 24,
      borderWidth: focused || node.boundary === true || node.isolated === true ? 4 : 2,
      opacity: sccFocusId === null || focused ? 1 : 0.18,
      tooltip: nodeTitle,
    },
    position,
  };
}

function buildEdgeItem(entry, edgeWidthScale, graphScope, sccFocusId, nodeIndex) {
  const edge = entry.edge;
  const focused = sccFocusId !== null && nodeIndex.get(edge.src)?.scc_id === sccFocusId
    && nodeIndex.get(edge.dst)?.scc_id === sccFocusId;
  const amount = typeof edge.sum_tiyn === 'number' ? formatTiyn(edge.sum_tiyn) : '—';
  const count = typeof edge.n_tx === 'number' ? formatInteger(edge.n_tx) : '—';
  return {
    group: 'edges',
    data: {
      id: 'edge-' + entry.index,
      source: edge.src,
      target: edge.dst,
      width: edgeWidthScale(edge.sum_tiyn),
      opacity: sccFocusId !== null ? focused ? 0.85 : 0.08 : graphScope === 'full' ? 0.22 : 0.8,
      lineColor: focused ? '#2f776c' : '#93a6aa',
      arrowColor: focused ? '#2f776c' : '#788f95',
      tooltip: [
        'Направление: ' + edge.src + ' → ' + edge.dst,
        'Сумма: ' + amount,
        'Переводов: ' + count,
        'Глубина: ' + (edge.depth ?? '—'),
      ],
    },
  };
}

const GRAPH_STYLE = [
  { selector: 'node', style: {
    'background-color': 'data(color)', 'border-color': 'data(border)', 'border-width': 'data(borderWidth)',
    shape: 'data(shape)', width: 'data(size)', height: 'data(size)', opacity: 'data(opacity)', label: '',
  } },
  { selector: 'node:selected', style: {
    'border-color': '#173c38', 'border-width': 5, width: 36, height: 36,
    opacity: 1,
    label: 'data(id)', 'font-size': 13, 'font-weight': 700, 'text-background-color': '#ffffff',
    'text-background-opacity': 0.9, 'text-background-padding': '3px', 'text-margin-y': -23,
  } },
  { selector: 'edge', style: {
    width: 'data(width)', 'line-color': 'data(lineColor)', 'target-arrow-color': 'data(arrowColor)',
    'target-arrow-shape': 'triangle', 'arrow-scale': 0.9, 'curve-style': 'bezier', opacity: 'data(opacity)',
  } },
  { selector: 'edge:selected', style: { 'line-color': '#286f69', 'target-arrow-color': '#286f69', opacity: 1 } },
  { selector: '.route-node', style: { 'border-color': '#d16c2e', 'border-width': 6 } },
  { selector: '.route-edge', style: { 'line-color': '#d16c2e', 'target-arrow-color': '#d16c2e', width: 5, opacity: 1 } },
];

function graphPositions(graph, graphScope) {
  const positions = new Map();
  if (graphScope !== 'full' && graph.selectedNode) {
    const center = graph.selectedNode.gid;
    positions.set(center, { x: 0, y: 0 });
    const incoming = new Set();
    const outgoing = new Set();
    graph.visibleEdges.forEach(({ edge }) => {
      if (edge.dst === center && edge.src !== center) incoming.add(edge.src);
      if (edge.src === center && edge.dst !== center) outgoing.add(edge.dst);
    });
    const left = [];
    const right = [];
    graph.visibleNodes.filter((node) => node.gid !== center)
      .sort((a, b) => a.gid.localeCompare(b.gid, undefined, { numeric: true }))
      .forEach((node) => {
        if (incoming.has(node.gid) && (!outgoing.has(node.gid) || left.length <= right.length)) left.push(node);
        else right.push(node);
      });
    for (const [side, items] of [[-1, left], [1, right]]) {
      const rows = Math.min(10, items.length);
      items.forEach((node, index) => {
        const row = index % rows;
        const column = Math.floor(index / rows);
        positions.set(node.gid, { x: side * (210 + column * 135), y: (row - (rows - 1) / 2) * 76 });
      });
    }
    return positions;
  }

  // Give each cluster a compact circular footprint, then place the footprints
  // on concentric rings. The full graph remains deterministic and needs no physics.
  const groups = new Map();
  graph.visibleNodes.forEach((node) => {
    const key = String(node.cluster_id ?? 'unclustered');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  });
  const clusters = [...groups]
    .map(([key, members]) => ({ key, members, radius: 28 * Math.sqrt(members.length) + 24 }))
    .sort((a, b) => b.members.length - a.members.length || a.key.localeCompare(b.key, undefined, { numeric: true }));
  const placeCluster = (cluster, x, y) => {
    cluster.members.sort((a, b) => Number(b.is_seed === true) - Number(a.is_seed === true)
      || (b.priority_score ?? 0) - (a.priority_score ?? 0)
      || a.gid.localeCompare(b.gid, undefined, { numeric: true }));
    cluster.members.forEach((node, index) => {
      const angle = index * 2.399963229728653;
      const radius = 28 * Math.sqrt(index);
      positions.set(node.gid, { x: x + radius * Math.cos(angle), y: y + radius * Math.sin(angle) });
    });
  };
  if (clusters.length === 0) return positions;
  const center = clusters.shift();
  placeCluster(center, 0, 0);
  let previousRadius = 0;
  let previousExtent = center.radius;
  while (clusters.length) {
    const ringRadius = previousRadius + previousExtent + clusters[0].radius + 72;
    const ring = [];
    let usedAngle = 0;
    while (clusters.length) {
      const cluster = clusters[0];
      const angle = 2 * Math.asin(Math.min(1, (cluster.radius + 28) / ringRadius));
      if (ring.length && usedAngle + angle > 2 * Math.PI) break;
      ring.push({ cluster: clusters.shift(), angle });
      usedAngle += angle;
    }
    const gap = (2 * Math.PI - usedAngle) / ring.length;
    let angle = -Math.PI / 2;
    ring.forEach(({ cluster, angle: width }) => {
      angle += width / 2;
      placeCluster(cluster, ringRadius * Math.cos(angle), ringRadius * Math.sin(angle));
      angle += width / 2 + gap;
    });
    previousRadius = ringRadius;
    previousExtent = Math.max(...ring.map(({ cluster }) => cluster.radius));
  }
  return positions;
}

function updateFilter(onFiltersChange, key, value) {
  onFiltersChange((current) => ({ ...current, [key]: value }));
}

export default function GraphView({ report, selectedGid, onSelectGid, filters, onFiltersChange, routeSteps, sccFocusId = null, onClearScc }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const onSelectRef = useRef(onSelectGid);
  const previousSelectedRef = useRef(undefined);
  const [edgePage, setEdgePage] = useState(0);
  const [graphTooltip, setGraphTooltip] = useState(null);
  const [showAllNeighbors, setShowAllNeighbors] = useState(false);
  const activeSccFocus = filters.graphScope === 'full' ? sccFocusId : null;

  onSelectRef.current = onSelectGid;

  const graph = useMemo(
    () => buildGraphScope(report, selectedGid, filters),
    [report, selectedGid, filters],
  );
  const displayGraph = useMemo(() => {
    if (filters.graphScope === 'full' || showAllNeighbors || graph.visibleEdges.length <= NEIGHBORHOOD_EDGE_LIMIT) return graph;
    const visibleEdges = [...graph.visibleEdges]
      .sort((a, b) => (b.edge.sum_tiyn ?? 0) - (a.edge.sum_tiyn ?? 0) || a.index - b.index)
      .slice(0, NEIGHBORHOOD_EDGE_LIMIT);
    const ids = new Set([selectedGid]);
    visibleEdges.forEach(({ edge }) => { ids.add(edge.src); ids.add(edge.dst); });
    return {
      ...graph,
      visibleNodes: graph.visibleNodes.filter((node) => ids.has(node.gid)),
      visibleEdges,
      signature: visibleEdges.map(({ index }) => index).join(','),
    };
  }, [graph, filters.graphScope, selectedGid, showAllNeighbors]);
  const edgeWidthScale = useMemo(() => createEdgeWidthScale(report.edges), [report.edges]);
  const graphKey = (filters.graphScope === 'full' ? 'full' : selectedGid) + '|'
    + displayGraph.signature + '|' + filters.graphScope + '|' + filters.colorMode + '|' + activeSccFocus;
  const clusterOptions = useMemo(() => {
    const values = new Map();
    report.nodes.forEach((node) => {
      const key = node.cluster_id === null || node.cluster_id === undefined
        ? NO_CLUSTER_FILTER
        : String(node.cluster_id);
      if (!values.has(key)) values.set(key, node.cluster_id);
    });
    return [...values.entries()].sort((a, b) => {
      if (a[0] === NO_CLUSTER_FILTER) return 1;
      if (b[0] === NO_CLUSTER_FILTER) return -1;
      return String(a[0]).localeCompare(String(b[0]), undefined, { numeric: true });
    });
  }, [report.nodes]);
  const roles = useMemo(() => {
    const values = new Set();
    report.nodes.forEach((node) => {
      if (typeof node.role === 'string' && node.role) values.add(node.role);
      (Array.isArray(node.matched_roles) ? node.matched_roles : []).forEach((match) => {
        if (typeof match?.role === 'string' && match.role) values.add(match.role);
      });
    });
    return [...values].sort();
  }, [report.nodes]);
  const visibleClusters = useMemo(() => {
    const values = new Map();
    displayGraph.visibleNodes.forEach((node) => {
      const key = node.cluster_id === null || node.cluster_id === undefined ? NO_CLUSTER_FILTER : String(node.cluster_id);
      if (!values.has(key)) values.set(key, node.cluster_id);
    });
    return [...values.entries()];
  }, [displayGraph.visibleNodes]);
  const edgePageCount = Math.max(1, Math.ceil(graph.visibleEdges.length / PAGE_SIZE));
  const edgeRows = graph.visibleEdges.slice(edgePage * PAGE_SIZE, (edgePage + 1) * PAGE_SIZE);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: GRAPH_STYLE,
      layout: { name: 'preset', fit: false },
      minZoom: 0.04,
      maxZoom: 3,
      autoungrabify: true,
      boxSelectionEnabled: false,
    });
    cyRef.current = cy;
    cy.on('tap', 'node', (event) => onSelectRef.current(event.target.id()));
    const showTooltip = (event) => {
      const position = event.renderedPosition || event.target.renderedPosition?.() || { x: 8, y: 8 };
      const canvasTop = containerRef.current?.offsetTop || 0;
      const width = containerRef.current?.clientWidth || 300;
      const height = containerRef.current?.clientHeight || 300;
      setGraphTooltip({
        lines: event.target.data('tooltip'),
        x: Math.max(8, Math.min(position.x + 12, width - 230)),
        y: canvasTop + Math.max(8, Math.min(position.y + 12, height - 110)),
      });
    };
    cy.on('mouseover', 'node', showTooltip);
    cy.on('mouseover', 'edge', showTooltip);
    cy.on('tap', 'edge', showTooltip);
    cy.on('mouseout', 'node', () => setGraphTooltip(null));
    cy.on('mouseout', 'edge', () => setGraphTooltip(null));
    cy.on('tap', (event) => { if (event.target === cy) setGraphTooltip(null); });
    const observer = new ResizeObserver(() => {
      cy.resize();
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const positions = graphPositions(displayGraph, filters.graphScope);
    const nodes = displayGraph.visibleNodes.map((node) => buildNodeItem(node, filters.colorMode, positions.get(node.gid), activeSccFocus));
    const edges = displayGraph.visibleEdges.map((entry) => buildEdgeItem(entry, edgeWidthScale, filters.graphScope, activeSccFocus, graph.nodeIndex));
    cy.batch(() => {
      cy.elements().remove();
      cy.add([...nodes, ...edges]);
    });
    const frame = window.requestAnimationFrame(() => {
      cy.resize();
      if (cy.elements().length) {
        cy.fit(cy.elements(), 28);
      }
    });
    setGraphTooltip(null);
    return () => window.cancelAnimationFrame(frame);
  }, [graphKey, edgeWidthScale]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return undefined;
    cy.elements().removeClass('route-node route-edge');
    if (!Array.isArray(routeSteps) || routeSteps.length === 0) return undefined;
    const ids = new Set(routeSteps.flatMap((step) => [step.src, step.dst]));
    const pairs = new Set(routeSteps.map((step) => `${step.src}|${step.dst}`));
    const nodes = cy.nodes().filter((node) => ids.has(node.id())).addClass('route-node');
    const edges = cy.edges().filter((edge) => pairs.has(`${edge.source().id()}|${edge.target().id()}`)).addClass('route-edge');
    const frame = window.requestAnimationFrame(() => { if (nodes.length) cy.fit(nodes.union(edges), 55); });
    return () => window.cancelAnimationFrame(frame);
  }, [routeSteps, graphKey]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (typeof selectedGid === 'string') {
      const selected = cy.getElementById(selectedGid);
      if (selected.length) {
        selected.select();
        if (filters.graphScope === 'full' && previousSelectedRef.current !== undefined
          && previousSelectedRef.current !== selectedGid) {
          cy.center(selected);
          cy.zoom(Math.max(cy.zoom(), 0.65));
        } else if (filters.graphScope !== 'full' && cy.zoom() < 0.55) {
          cy.center(selected);
          cy.zoom(0.55);
        }
      }
    }
    previousSelectedRef.current = selectedGid;
  }, [selectedGid, graphKey]);

  function zoomGraph(factor) {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({
      level: Math.max(cy.minZoom(), Math.min(cy.maxZoom(), cy.zoom() * factor)),
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  }

  function fitGraph() {
    const cy = cyRef.current;
    if (cy?.elements().length) cy.fit(cy.elements(), 28);
  }

  useEffect(() => setEdgePage(0), [graph.signature]);
  useEffect(() => setShowAllNeighbors(false), [selectedGid]);

  function resetFilters() {
    onFiltersChange({ role: 'all', roleMode: 'primary', cluster: 'all', seedMode: 'all', graphScope: 'neighborhood', colorMode: 'role' });
    const firstTop = report.top_nodes.find((item) => graph.nodeIndex.has(item.gid));
    onSelectGid(firstTop?.gid ?? report.nodes[0]?.gid ?? null);
  }

  const filteredNodeCount = graph.filteredNodes.length;
  const selectedBoundary = graph.selectedNode?.boundary === true;
  const activeFilterCount = Number(filters.role !== 'all') + Number(filters.roleMode !== 'primary')
    + Number(filters.cluster !== 'all') + Number(filters.seedMode !== 'all') + Number(filters.colorMode !== 'role');

  return (
    <section className="panel graph-panel" aria-labelledby="graph-title">
      <div className="panel-head graph-heading">
        <div><p className="eyebrow">2 · Проверьте связи</p><h2 id="graph-title">Направленный граф</h2></div>
        <div className="graph-counters" aria-live="polite">
          <span><strong>{formatInteger(displayGraph.visibleNodes.length)}</strong> узлов</span>
          <span><strong>{formatInteger(displayGraph.visibleEdges.length)}</strong> направленных связей</span>
          <span><strong>{formatInteger(filteredNodeCount)}</strong> подходят фильтрам</span>
        </div>
      </div>

      <div className="graph-controls">
        <div className="graph-quick-actions" role="group" aria-label="Область графа">
          <button className="button button-quiet" type="button" aria-pressed={filters.graphScope === 'neighborhood'} onClick={() => updateFilter(onFiltersChange, 'graphScope', 'neighborhood')}>Связи клиента</button>
          <button className="button button-quiet" type="button" aria-pressed={filters.graphScope === 'full'} onClick={() => updateFilter(onFiltersChange, 'graphScope', 'full')}>Весь граф</button>
          {filters.graphScope === 'neighborhood' && graph.visibleEdges.length > NEIGHBORHOOD_EDGE_LIMIT && <button className="graph-show-all" type="button" onClick={() => setShowAllNeighbors((value) => !value)}>{showAllNeighbors ? `Вернуть крупные ${NEIGHBORHOOD_EDGE_LIMIT}` : `Показать все ${formatInteger(graph.visibleEdges.length)} связей`}</button>}
        </div>
        <details className="graph-advanced">
          <summary>Фильтры и вид{activeFilterCount > 0 ? ` · ${activeFilterCount} выбрано` : ''}</summary>
          <div className="graph-filter-grid" aria-label="Фильтры графа">
          <label>Роль
            <select value={filters.role} onChange={(event) => updateFilter(onFiltersChange, 'role', event.target.value)}>
              <option value="all">Все роли</option>
              {roles.map((role) => <option key={role} value={role}>{ROLE_LABELS[role] || role}</option>)}
            </select>
          </label>
          <label>Режим роли
            <select value={filters.roleMode} onChange={(event) => updateFilter(onFiltersChange, 'roleMode', event.target.value)}>
              <option value="primary">Основная роль</option>
              <option value="any">Любое совпавшее правило</option>
            </select>
          </label>
          <label>Кластер
            <select value={filters.cluster} onChange={(event) => updateFilter(onFiltersChange, 'cluster', event.target.value)}>
              <option value="all">Все кластеры</option>
              {clusterOptions.map(([key, value]) => <option key={key} value={key}>{key === NO_CLUSTER_FILTER ? 'Не указан' : 'Кластер ' + value}</option>)}
            </select>
          </label>
          <label>Цвет узлов
            <select value={filters.colorMode} onChange={(event) => updateFilter(onFiltersChange, 'colorMode', event.target.value)}>
              <option value="role">По роли</option>
              <option value="cluster">По кластеру</option>
            </select>
          </label>
          <label>Узлы
            <select value={filters.seedMode} onChange={(event) => updateFilter(onFiltersChange, 'seedMode', event.target.value)}>
              <option value="all">Seed и остальные</option>
              <option value="seed">Только seed</option>
              <option value="others">Без seed</option>
            </select>
          </label>
          <button className="button button-quiet graph-reset" type="button" onClick={resetFilters}>Сбросить фильтры</button>
          </div>
        </details>
      </div>

      <div className="graph-statuses" aria-live="polite">
        {activeSccFocus !== null && <p className="graph-notice">Подсвечена SCC {activeSccFocus}: направленная связность внутри компоненты. <button type="button" className="gid-action" onClick={onClearScc}>Сбросить подсветку</button></p>}
        {graph.selectionOutsideFilters && graph.selectedNode && (
          <p className="graph-notice"><strong>Выбранный клиент не соответствует фильтрам.</strong> Он всё равно показан для контекста; измените фильтры, чтобы увидеть его соседей.</p>
        )}
        {selectedBoundary && <p className="graph-notice">Это граничный узел выгрузки: отсутствие исходящих связей за её пределами неизвестно.</p>}
        {filteredNodeCount === 0 && !graph.selectedNode && <p className="graph-empty" role="status">Фильтры не нашли узлов. Сбросьте фильтры или выберите другие значения.</p>}
        {graph.visibleNodes.length === 0 && graph.selectedNode && <p className="graph-empty" role="status">Для выбранного клиента нет узлов в текущем графе.</p>}
        {graph.selectedNode && graph.selectedNode.isolated === true && graph.visibleEdges.length === 0 && <p className="graph-notice">Узел изолирован в этой выгрузке: наблюдаемых связей нет.</p>}
      </div>

      <div className="graph-layout">
        <div className="graph-canvas-wrap">
          <div className="graph-toolbar" role="group" aria-label="Управление масштабом графа">
            <button className="graph-zoom-button" type="button" onClick={() => zoomGraph(1.35)} aria-label="Увеличить граф">+</button>
            <button className="graph-zoom-button" type="button" onClick={() => zoomGraph(1 / 1.35)} aria-label="Уменьшить граф">−</button>
            <button className="graph-zoom-button" type="button" onClick={fitGraph}>Вписать</button>
          </div>
          <p className="graph-help">{filters.graphScope === 'neighborhood'
            ? (graph.visibleEdges.length > NEIGHBORHOOD_EDGE_LIMIT && !showAllNeighbors ? 'Крупнейшие связи по сумме · входящие слева, исходящие справа' : 'Входящие слева · исходящие справа')
            : 'Все кластеры по кругу · выберите узел для подробностей, «Вписать» вернёт обзор'}</p>
          <div ref={containerRef} className="graph-canvas" role="region" aria-label="Интерактивный направленный граф клиентов. Перетаскивайте поле и меняйте масштаб колесом или двумя пальцами." />
          {graphTooltip && <div className="graph-tooltip-overlay" style={{ left: graphTooltip.x, top: graphTooltip.y }} role="status">{graphTooltip.lines.map((line) => <span key={line}>{line}</span>)}</div>}
          <details className="graph-legend" aria-label="Легенда графа">
            <summary>Обозначения</summary>
            <div className="legend-row">
              {filters.colorMode === 'role'
                ? roles.map((role) => <span className="legend-key" key={role}><i style={{ backgroundColor: ROLE_COLORS[role] || '#687983' }} />{ROLE_LABELS[role] || role}</span>)
                : <span className="legend-key"><i style={{ backgroundColor: clusterColor(graph.selectedNode?.cluster_id) }} />Цвет показывает кластер; точный ID — в карточке</span>}
              <span className="legend-key"><i className="legend-shape legend-seed" />ромб — seed</span>
              <span className="legend-key"><i className="legend-shape legend-boundary" />контур — граница</span>
              <span className="legend-key"><i className="legend-shape legend-isolate" />треугольник — изолят</span>
              <span className="legend-key"><i className="legend-arrow">→</i>стрелка — направление</span>
            </div>
            {filters.colorMode === 'cluster' && <details className="legend-clusters"><summary>Цвета кластеров ({visibleClusters.length})</summary><div className="legend-row">
              {visibleClusters.map(([key, value]) => <span className="legend-key" key={key}><i style={{ backgroundColor: clusterColor(value) }} />{key === NO_CLUSTER_FILTER ? 'Кластер не указан' : 'Кластер ' + value}</span>)}
            </div></details>}
            <span className="legend-scale">Толщина ребра — логарифмическая шкала по всему выпуску; точная сумма указана в подсказке и таблице.</span>
          </details>
        </div>
      </div>

      <details className="edge-table-section">
        <summary>Таблица направленных связей · {formatInteger(graph.visibleEdges.length)}</summary>
        <div className="edge-table-heading">
          <div><p className="eyebrow">Проверяемые связи</p><h3 id="edge-table-title">Направленные рёбра графа</h3></div>
          <div className="edge-pagination">
            <span>{graph.visibleEdges.length === 0 ? 'Нет рёбер' : (edgePage * PAGE_SIZE + 1) + '–' + Math.min((edgePage + 1) * PAGE_SIZE, graph.visibleEdges.length) + ' из ' + graph.visibleEdges.length}</span>
            <button type="button" className="button button-quiet" disabled={edgePage <= 0} onClick={() => setEdgePage((page) => Math.max(0, page - 1))}>Назад</button>
            <button type="button" className="button button-quiet" disabled={edgePage + 1 >= edgePageCount} onClick={() => setEdgePage((page) => Math.min(edgePageCount - 1, page + 1))}>Далее</button>
          </div>
        </div>
        {edgeRows.length === 0 ? (
          <div className="empty-state edge-empty"><strong>Связей в текущем графе нет</strong><span>Выберите узел со связями, смените режим просмотра или ослабьте фильтры.</span></div>
        ) : (
          <div className="table-scroll edge-table-scroll">
            <table>
              <thead><tr><th scope="col">Отправитель</th><th scope="col">Получатель</th><th scope="col">Сумма</th><th scope="col">Переводов</th><th scope="col">Глубина</th></tr></thead>
              <tbody>{edgeRows.map(({ edge, index }) => (
                <tr key={'edge-row-' + index}>
                  <td><button type="button" className="gid-action" onClick={() => onSelectGid(edge.src)}>{edge.src}</button></td>
                  <td><button type="button" className="gid-action" onClick={() => onSelectGid(edge.dst)}>{edge.dst}</button></td>
                  <td className="numeric-cell">{formatTiyn(edge.sum_tiyn)}</td>
                  <td className="numeric-cell">{formatInteger(edge.n_tx)}</td>
                  <td className="numeric-cell">{formatInteger(edge.depth)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}
