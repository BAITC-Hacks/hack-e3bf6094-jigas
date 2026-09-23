import { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { formatInteger, formatTiyn } from '../report.js';
import { createEdgeWidthScale } from '../graphModel.js';

const PAGE_SIZE = 30;

export default function CommunityView({ report, onSelectGid, onFocusScc }) {
  const canvasRef = useRef(null);
  const cyRef = useRef(null);
  const [selection, setSelection] = useState(null);
  const [page, setPage] = useState(0);
  const [selectedScc, setSelectedScc] = useState(null);
  const edges = report.community_edges;
  const sccs = report.sccs;
  const byGid = useMemo(() => new Map(report.nodes.map((node) => [node.gid, node])), [report.nodes]);
  const defaultScc = useMemo(() => [...sccs].filter((item) => item.n_nodes > 1)
    .sort((a, b) => b.n_nodes - a.n_nodes || a.scc_id - b.scc_id)[0], [sccs]);
  const activeScc = sccs.find((item) => item.scc_id === selectedScc) || defaultScc;
  const edgeWidth = useMemo(() => createEdgeWidthScale(edges), [edges]);
  const members = selection?.kind === 'cluster'
    ? report.nodes.filter((node) => node.cluster_id === selection.id)
    : [];
  const rawEdges = selection?.kind === 'flow'
    ? report.edges.filter((edge) => {
      return byGid.get(edge.src)?.cluster_id === selection.src && byGid.get(edge.dst)?.cluster_id === selection.dst;
    })
    : [];
  const chosenFlow = selection?.kind === 'flow'
    ? edges.find((edge) => edge.src_cluster_id === selection.src && edge.dst_cluster_id === selection.dst)
    : null;
  useEffect(() => setPage(0), [selection]);

  useEffect(() => {
    if (!canvasRef.current) return undefined;
    const cy = cytoscape({
      container: canvasRef.current,
      elements: [
        ...report.clusters.map((cluster) => ({ data: {
          id: `c-${cluster.cluster_id}`, clusterId: cluster.cluster_id,
          label: `${cluster.cluster_id}`, size: Math.min(52, 20 + Math.sqrt(cluster.n_nodes) * 3),
          color: cluster.n_seed > 0 ? '#2f776c' : '#779198',
        } })),
        ...edges.map((edge) => ({ data: {
          id: `f-${edge.src_cluster_id}-${edge.dst_cluster_id}`,
          source: `c-${edge.src_cluster_id}`, target: `c-${edge.dst_cluster_id}`,
          src: edge.src_cluster_id, dst: edge.dst_cluster_id, width: edgeWidth(edge.sum_tiyn),
        } })),
      ],
      style: [
        { selector: 'node', style: { 'background-color': 'data(color)', width: 'data(size)', height: 'data(size)',
          label: 'data(label)', color: '#fff', 'font-size': 11, 'font-weight': 700, 'text-valign': 'center', 'text-halign': 'center' } },
        { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#1a3039' } },
        { selector: 'edge', style: { width: 'data(width)', 'line-color': '#a3b6b7', 'target-arrow-color': '#61858a',
          'target-arrow-shape': 'triangle', 'curve-style': 'bezier', opacity: 0.65 } },
        { selector: 'edge:selected', style: { 'line-color': '#2f776c', 'target-arrow-color': '#2f776c', opacity: 1 } },
      ],
      layout: { name: 'concentric', fit: true, padding: 36, minNodeSpacing: 18,
        concentric: (node) => node.data('size'), levelWidth: () => 8 },
      minZoom: 0.15, maxZoom: 3, autoungrabify: true,
    });
    cyRef.current = cy;
    cy.on('tap', 'node', (event) => setSelection({ kind: 'cluster', id: event.target.data('clusterId') }));
    cy.on('tap', 'edge', (event) => setSelection({ kind: 'flow', src: event.target.data('src'), dst: event.target.data('dst') }));
    const observer = new ResizeObserver(() => { cy.resize(); cy.fit(cy.elements(), 30); });
    observer.observe(canvasRef.current);
    return () => { observer.disconnect(); cy.destroy(); cyRef.current = null; };
  }, [report.clusters, edges, edgeWidth]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    const id = selection?.kind === 'cluster' ? `c-${selection.id}`
      : selection?.kind === 'flow' ? `f-${selection.src}-${selection.dst}` : null;
    if (id) cy.getElementById(id).select();
  }, [selection]);

  return <section className="panel community-panel" aria-labelledby="community-title">
    <div className="panel-head"><div><p className="eyebrow">Структура сети</p><h2 id="community-title">Потоки между кластерами</h2></div></div>
    <p className="community-intro">Круг — сообщество Louvain, стрелка — сумма наблюдаемых переводов между сообществами. Это не путь конкретных денег.</p>
    <div className="community-controls">
      <label>Выбрать кластер<select value={selection?.kind === 'cluster' ? selection.id : ''} onChange={(event) => setSelection(event.target.value === '' ? null : { kind: 'cluster', id: Number(event.target.value) })}>
        <option value="">Выберите</option>{report.clusters.map((cluster) => <option key={cluster.cluster_id} value={cluster.cluster_id}>Кластер {cluster.cluster_id} · {cluster.n_nodes} клиентов</option>)}
      </select></label>
      <label>Выбрать поток<select value={selection?.kind === 'flow' ? `${selection.src}-${selection.dst}` : ''} onChange={(event) => {
        const edge = edges.find((item) => `${item.src_cluster_id}-${item.dst_cluster_id}` === event.target.value);
        setSelection(edge ? { kind: 'flow', src: edge.src_cluster_id, dst: edge.dst_cluster_id } : null);
      }}>
        <option value="">Выберите</option>{edges.map((edge) => <option key={`${edge.src_cluster_id}-${edge.dst_cluster_id}`} value={`${edge.src_cluster_id}-${edge.dst_cluster_id}`}>{edge.src_cluster_id} → {edge.dst_cluster_id} · {formatTiyn(edge.sum_tiyn)}</option>)}
      </select></label>
    </div>
    <div className="community-canvas-wrap"><div ref={canvasRef} className="community-canvas" role="img" aria-label={`Мета-граф: ${report.clusters.length} кластеров, ${edges.length} направленных потоков. Для клавиатуры используйте списки выбора выше.`} /><div className="community-zoom" role="group" aria-label="Масштаб мета-графа">
      <button type="button" onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.3)}>+</button><button type="button" onClick={() => cyRef.current?.zoom(cyRef.current.zoom() / 1.3)}>−</button><button type="button" onClick={() => cyRef.current?.fit(cyRef.current.elements(), 30)}>Вписать</button>
    </div></div>
    <p className="community-scale">Толщина стрелок — шкала по всем межкластерным потокам; точная сумма ниже. Направление обратного потока показывается отдельно.</p>
    {selection?.kind === 'cluster' && <div className="community-selection"><h3>Кластер {selection.id} · {formatInteger(members.length)} клиентов</h3><p>Выберите ID для карточки и связей.</p><div className="community-members">{members.map((node) => <button type="button" key={node.gid} onClick={() => onSelectGid(node.gid)}>{node.gid}</button>)}</div></div>}
    {chosenFlow && <div className="community-selection"><h3>Кластер {selection.src} → {selection.dst}</h3><p>{formatTiyn(chosenFlow.sum_tiyn)} · {formatInteger(chosenFlow.n_edges)} исходных связей · {formatInteger(chosenFlow.n_tx)} переводов.</p>
      <div className="table-scroll"><table><thead><tr><th scope="col">Отправитель</th><th scope="col">Получатель</th><th scope="col">Сумма</th></tr></thead><tbody>{rawEdges.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((edge) => <tr key={`${edge.src}-${edge.dst}`}><td><button className="gid-action" type="button" onClick={() => onSelectGid(edge.src)}>{edge.src}</button></td><td><button className="gid-action" type="button" onClick={() => onSelectGid(edge.dst)}>{edge.dst}</button></td><td>{formatTiyn(edge.sum_tiyn)}</td></tr>)}</tbody></table></div>
      {rawEdges.length > PAGE_SIZE && <div className="community-pages"><button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Назад</button><span>{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, rawEdges.length)} из {rawEdges.length}</span><button type="button" disabled={(page + 1) * PAGE_SIZE >= rawEdges.length} onClick={() => setPage(page + 1)}>Далее</button></div>}
    </div>}
    <div className="community-scc"><h3>Сильносвязные компоненты</h3><p>Это группы, в которых между узлами есть направленные пути в обе стороны. Они отличаются от кластеров Louvain и не доказывают возврат тех же денег.</p>
      {defaultScc ? <div className="community-scc-actions"><label>Компонента<select value={activeScc.scc_id} onChange={(event) => setSelectedScc(Number(event.target.value))}>{sccs.filter((item) => item.n_nodes > 1).map((item) => <option key={item.scc_id} value={item.scc_id}>SCC {item.scc_id} · {item.n_nodes} узлов</option>)}</select></label><button className="button button-primary" type="button" onClick={() => onFocusScc(activeScc.scc_id)}>Подсветить на полном графе</button><span>{formatInteger(activeScc.n_nodes)} узлов · {formatInteger(activeScc.n_external_recipients)} внешних получателей · {formatTiyn(activeScc.sum_tiyn_internal)} внутри</span></div>
        : <p role="status">Компонент больше одного узла нет; одиночные узлы сохранены в расчёте.</p>}
    </div>
    <div className="community-coverage"><strong>Граница выгрузки</strong><span>{formatInteger(report.coverage.boundary_n_nodes)} клиентов на глубине 4 · {formatTiyn(report.coverage.sum_tiyn_to_boundary)} входящего оборота · {(report.coverage.share_to_boundary * 100).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}% общего оборота.</span><p>Отсутствие видимых исходящих на границе не означает, что деньги остались у этих клиентов.</p></div>
  </section>;
}
