/**
 * HA-15.2 contract: App owns selectedGid and filters. GraphView owns only the
 * vis-network instance and calls onSelectGid when a graph node is selected.
 */
export default function GraphView({ report, selectedGid, onSelectGid, filters, onFiltersChange }) {
  const nodeCount = Array.isArray(report?.nodes) ? report.nodes.length : 0;
  void onSelectGid;
  void filters;
  void onFiltersChange;
  return (
    <section className="panel graph-panel" aria-labelledby="graph-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Связи</p>
          <h2 id="graph-title">Направленный граф</h2>
        </div>
        <span className="stage-label">Подключается в HA-15.2</span>
      </div>
      <div className="graph-placeholder">
        <span className="graph-placeholder-mark" aria-hidden="true">G</span>
        <p>Граф появится после подключения локального vis-network.</p>
        <p className="muted">В отчёте {nodeCount} узлов. Общий selectedGid и фильтры принадлежат App.</p>
        {typeof selectedGid === 'string' && <code className="graph-selected-gid">{selectedGid}</code>}
      </div>
    </section>
  );
}
