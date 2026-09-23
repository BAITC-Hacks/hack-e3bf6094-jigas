import { useEffect, useMemo, useRef, useState } from 'react';
import { DataSet, Network } from 'vis-network/standalone';
import 'vis-network/styles/vis-network.css';
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

function tooltip(lines) {
  const element = document.createElement('div');
  element.className = 'graph-tooltip';
  element.textContent = lines.join('\n');
  return element;
}

function buildNodeItem(node, selectedGid, colorMode) {
  const color = colorMode === 'cluster'
    ? clusterColor(node.cluster_id)
    : ROLE_COLORS[node.role] || '#687983';
  const border = node.boundary === true ? '#bb6e30' : node.isolated === true ? '#53636b' : '#ffffff';
  const shape = node.isolated === true ? 'triangle' : node.is_seed === true ? 'diamond' : 'dot';
  const label = node.gid === selectedGid ? node.gid : '';
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
    id: node.gid,
    label,
    title: tooltip(nodeTitle),
    shape,
    size: node.gid === selectedGid ? 21 : node.is_seed === true ? 16 : 13,
    color: {
      background: color,
      border,
      highlight: { background: color, border: '#173c38' },
      hover: { background: color, border: '#173c38' },
    },
    borderWidth: node.boundary === true || node.isolated === true ? 3 : 1.5,
    font: { color: '#23323a', size: 12, face: 'system-ui, sans-serif', strokeWidth: 3, strokeColor: '#ffffff' },
    chosen: { node: (_values, _id, selected) => { if (selected) _values.borderWidth = 4; } },
  };
}

function buildEdgeItem(entry, edgeWidthScale) {
  const edge = entry.edge;
  const amount = typeof edge.sum_tiyn === 'number' ? formatTiyn(edge.sum_tiyn) : '—';
  const count = typeof edge.n_tx === 'number' ? formatInteger(edge.n_tx) : '—';
  return {
    id: 'edge-' + entry.index,
    from: edge.src,
    to: edge.dst,
    title: tooltip([
      'Направление: ' + edge.src + ' → ' + edge.dst,
      'Сумма: ' + amount,
      'Переводов: ' + count,
      'Глубина: ' + (edge.depth ?? '—'),
    ]),
    arrows: { to: { enabled: true, scaleFactor: 0.72 } },
    color: { color: '#9aa9ad', highlight: '#287b72', hover: '#5477b5' },
    width: edgeWidthScale(edge.sum_tiyn),
    selectionWidth: 1.2,
    smooth: { enabled: true, type: 'continuous', roundness: 0.12 },
  };
}

const NETWORK_OPTIONS = {
  autoResize: true,
  nodes: { borderWidth: 1.5, chosen: true, shadow: { enabled: false } },
  edges: { chosen: true, shadow: { enabled: false } },
  interaction: {
    hover: true,
    hoverConnectedEdges: true,
    keyboard: { enabled: true, bindToWindow: false },
    navigationButtons: true,
    multiselect: false,
    zoomView: true,
  },
  physics: {
    enabled: false,
    solver: 'barnesHut',
    barnesHut: { gravitationalConstant: -9000, centralGravity: 0.18, springLength: 95, springConstant: 0.035, damping: 0.16, avoidOverlap: 0.28 },
    stabilization: { enabled: true, iterations: 55, updateInterval: 20, fit: true },
  },
};

function syncDataSet(dataSet, records) {
  const nextById = new Map(records.map((record) => [record.id, record]));
  const currentIds = dataSet.getIds();
  const obsolete = currentIds.filter((id) => !nextById.has(id));
  if (obsolete.length) dataSet.remove(obsolete);
  if (records.length) dataSet.update(records);
}

function updateFilter(onFiltersChange, key, value) {
  onFiltersChange((current) => ({ ...current, [key]: value }));
}

export default function GraphView({ report, selectedGid, onSelectGid, filters, onFiltersChange }) {
  const containerRef = useRef(null);
  const networkRef = useRef(null);
  const nodesRef = useRef(null);
  const edgesRef = useRef(null);
  const onSelectRef = useRef(onSelectGid);
  const previousSelectedRef = useRef(undefined);
  const previousScopeSignatureRef = useRef(null);
  const stabilizationRef = useRef({ listener: null, timer: null });
  const [searchDraft, setSearchDraft] = useState('');
  const [searchState, setSearchState] = useState({ kind: 'idle', message: '' });
  const [edgePage, setEdgePage] = useState(0);

  onSelectRef.current = onSelectGid;

  const graph = useMemo(
    () => buildGraphScope(report, selectedGid, filters),
    [report, selectedGid, filters],
  );
  const nodeRecords = useMemo(
    () => graph.visibleNodes.map((node) => buildNodeItem(node, selectedGid, filters.colorMode)),
    [graph.visibleNodes, selectedGid, filters.colorMode],
  );
  const edgeWidthScale = useMemo(() => createEdgeWidthScale(report.edges), [report.edges]);
  const edgeRecords = useMemo(
    () => graph.visibleEdges.map((entry) => buildEdgeItem(entry, edgeWidthScale)),
    [graph.visibleEdges, edgeWidthScale],
  );
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
    graph.visibleNodes.forEach((node) => {
      const key = node.cluster_id === null || node.cluster_id === undefined ? NO_CLUSTER_FILTER : String(node.cluster_id);
      if (!values.has(key)) values.set(key, node.cluster_id);
    });
    return [...values.entries()];
  }, [graph.visibleNodes]);
  const edgePageCount = Math.max(1, Math.ceil(graph.visibleEdges.length / PAGE_SIZE));
  const edgeRows = graph.visibleEdges.slice(edgePage * PAGE_SIZE, (edgePage + 1) * PAGE_SIZE);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const nodes = new DataSet([]);
    const edges = new DataSet([]);
    const network = new Network(containerRef.current, { nodes, edges }, NETWORK_OPTIONS);
    const onSelectNode = (event) => {
      const gid = event.nodes?.[0];
      if (typeof gid === 'string') onSelectRef.current(gid);
    };
    network.on('selectNode', onSelectNode);
    networkRef.current = network;
    nodesRef.current = nodes;
    edgesRef.current = edges;

    return () => {
      network.off('selectNode', onSelectNode);
      if (stabilizationRef.current.listener) network.off('stabilized', stabilizationRef.current.listener);
      if (stabilizationRef.current.timer) window.clearTimeout(stabilizationRef.current.timer);
      network.destroy();
      networkRef.current = null;
      nodesRef.current = null;
      edgesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const network = networkRef.current;
    const nodes = nodesRef.current;
    const edges = edgesRef.current;
    if (!network || !nodes || !edges) return;

    syncDataSet(nodes, nodeRecords);
    syncDataSet(edges, edgeRecords);

    if (previousScopeSignatureRef.current !== graph.signature) {
      const pending = stabilizationRef.current;
      if (pending.listener) network.off('stabilized', pending.listener);
      if (pending.timer) window.clearTimeout(pending.timer);
      previousScopeSignatureRef.current = graph.signature;

      if (graph.visibleNodes.length === 0) {
        network.setOptions({ physics: { enabled: false } });
      } else {
        const iterations = graph.visibleNodes.length > 500 ? 48 : graph.visibleNodes.length > 100 ? 72 : 55;
        let finished = false;
        const finishLayout = () => {
          if (finished) return;
          finished = true;
          network.off('stabilized', finishLayout);
          if (stabilizationRef.current.timer) window.clearTimeout(stabilizationRef.current.timer);
          stabilizationRef.current = { listener: null, timer: null };
          network.setOptions({ physics: { enabled: false } });
        };
        network.on('stabilized', finishLayout);
        stabilizationRef.current.listener = finishLayout;
        network.setOptions({
          physics: {
            enabled: true,
            solver: 'barnesHut',
            barnesHut: { gravitationalConstant: -9000, centralGravity: 0.18, springLength: 95, springConstant: 0.035, damping: 0.16, avoidOverlap: 0.28 },
            stabilization: { enabled: true, iterations, updateInterval: 20, fit: true },
          },
        });
        network.stabilize(iterations);
        stabilizationRef.current.timer = window.setTimeout(finishLayout, graph.visibleNodes.length > 500 ? 3200 : 1800);
      }
    }

    if (typeof selectedGid === 'string' && nodes.get(selectedGid)) {
      network.selectNodes([selectedGid], true);
      if (previousSelectedRef.current !== selectedGid) {
        network.focus(selectedGid, { scale: 1.05, animation: { duration: 220, easingFunction: 'easeInOutQuad' } });
      }
    } else {
      network.unselectAll();
    }
    previousSelectedRef.current = selectedGid;
  }, [nodeRecords, edgeRecords, selectedGid]);

  useEffect(() => setEdgePage(0), [graph.signature]);

  function searchExact(event) {
    event.preventDefault();
    if (!searchDraft) {
      onSelectGid(null);
      setSearchState({ kind: 'idle', message: 'Выбор и карточка очищены.' });
      return;
    }
    const node = graph.nodeIndex.get(searchDraft);
    if (!node) {
      setSearchState({ kind: 'error', message: 'Точный ID не найден. Предыдущий выбор сохранён.' });
      return;
    }
    onSelectGid(node.gid);
    setSearchState({ kind: 'success', message: 'Клиент найден по точному строковому ID.' });
  }

  function resetFilters() {
    onFiltersChange({ role: 'all', roleMode: 'primary', cluster: 'all', seedMode: 'all', graphScope: 'neighborhood', colorMode: 'role' });
    const firstTop = report.top_nodes.find((item) => graph.nodeIndex.has(item.gid));
    onSelectGid(firstTop?.gid ?? report.nodes[0]?.gid ?? null);
    setSearchDraft('');
    setSearchState({ kind: 'idle', message: '' });
  }

  const filteredNodeCount = graph.filteredNodes.length;
  const selectedBoundary = graph.selectedNode?.boundary === true;

  return (
    <section className="panel graph-panel" aria-labelledby="graph-title">
      <div className="panel-head graph-heading">
        <div><p className="eyebrow">Связи и поиск</p><h2 id="graph-title">Направленный граф</h2></div>
        <div className="graph-counters" aria-live="polite">
          <span><strong>{formatInteger(graph.visibleNodes.length)}</strong> узлов</span>
          <span><strong>{formatInteger(graph.visibleEdges.length)}</strong> направленных связей</span>
          <span><strong>{formatInteger(filteredNodeCount)}</strong> подходят фильтрам</span>
        </div>
      </div>

      <div className="graph-controls">
        <form className="graph-search" onSubmit={searchExact} role="search">
          <label htmlFor="graph-gid-search">Точный поиск по полному ID</label>
          <div className="graph-search-row">
            <input
              id="graph-gid-search"
              type="search"
              inputMode="numeric"
              autoComplete="off"
              spellCheck="false"
              value={searchDraft}
              onChange={(event) => setSearchDraft(event.target.value)}
              placeholder="Например, 9007199254740993"
            />
            <button className="button button-primary" type="submit">Найти</button>
          </div>
          <span className={'search-feedback search-' + searchState.kind} role={searchState.kind === 'error' ? 'alert' : 'status'}>{searchState.message || 'ID сравнивается как строка без округления.'}</span>
        </form>

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
          <label>Вид графа
            <select value={filters.graphScope} onChange={(event) => updateFilter(onFiltersChange, 'graphScope', event.target.value)}>
              <option value="neighborhood">Выбранный узел и 1 переход</option>
              <option value="full">Полный граф по фильтрам</option>
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
      </div>

      <div className="graph-statuses" aria-live="polite">
        {graph.selectionOutsideFilters && graph.selectedNode && (
          <p className="graph-notice"><strong>Выбранный клиент скрыт фильтрами.</strong> Он оставлен на графе; сбросьте или измените фильтры, чтобы увидеть соответствующий контекст.</p>
        )}
        {selectedBoundary && <p className="graph-notice">Это граничный узел выгрузки: отсутствие исходящих связей за её пределами неизвестно.</p>}
        {filteredNodeCount === 0 && !graph.selectedNode && <p className="graph-empty" role="status">Фильтры не нашли узлов. Сбросьте фильтры или выберите другие значения.</p>}
        {graph.visibleNodes.length === 0 && graph.selectedNode && <p className="graph-empty" role="status">Для выбранного клиента нет узлов в текущем графе.</p>}
        {graph.selectedNode && graph.selectedNode.isolated === true && graph.visibleEdges.length === 0 && <p className="graph-notice">Узел изолирован в этой выгрузке: наблюдаемых связей нет.</p>}
      </div>

      <div className="graph-layout">
        <div className="graph-canvas-wrap">
          <div ref={containerRef} className="graph-canvas" role="region" aria-label="Интерактивный направленный граф клиентов" />
          <div className="graph-legend" aria-label="Легенда графа">
            <strong>Легенда</strong>
            <div className="legend-row">
              {filters.colorMode === 'role'
                ? roles.map((role) => <span className="legend-key" key={role}><i style={{ backgroundColor: ROLE_COLORS[role] || '#687983' }} />{ROLE_LABELS[role] || role}</span>)
                : <>
                  {visibleClusters.map(([key, value]) => <span className="legend-key" key={key}><i style={{ backgroundColor: clusterColor(value) }} />{key === NO_CLUSTER_FILTER ? 'Кластер не указан' : 'Кластер ' + value}</span>)}
                  {visibleClusters.length === 0 && <span className="legend-key">Нет узлов для легенды</span>}
                </>}
              <span className="legend-key"><i className="legend-shape legend-seed" />ромб — seed</span>
              <span className="legend-key"><i className="legend-shape legend-boundary" />контур — граница</span>
              <span className="legend-key"><i className="legend-shape legend-isolate" />треугольник — изолят</span>
              <span className="legend-key"><i className="legend-arrow">→</i>стрелка — направление</span>
            </div>
            <span className="legend-scale">Толщина ребра — логарифмическая шкала по всему выпуску; точная сумма указана в подсказке и таблице.</span>
          </div>
        </div>
        <aside className="graph-selection" aria-label="Выбранный узел">
          <p className="eyebrow">Текущий выбор</p>
          {graph.selectedNode ? (
            <>
              <strong className="graph-selected-gid">{graph.selectedNode.gid}</strong>
              <span>{ROLE_LABELS[graph.selectedNode.role] || graph.selectedNode.role || 'Роль не указана'}</span>
              <span>Кластер: {graph.selectedNode.cluster_id ?? '—'}</span>
              <span>Входящие / исходящие связи: {formatInteger(graph.selectedNode.in_deg)} / {formatInteger(graph.selectedNode.out_deg)}</span>
              <span>Приоритет: {typeof graph.selectedNode.priority_score === 'number' ? graph.selectedNode.priority_score.toFixed(3) : '—'}</span>
            </>
          ) : <p className="muted">Введите полный ID или выберите узел на графе, в таблице либо в кластере.</p>}
        </aside>
      </div>

      <section className="edge-table-section" aria-labelledby="edge-table-title">
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
      </section>
    </section>
  );
}
