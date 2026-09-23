import { useEffect, useMemo, useState } from 'react';
import GraphView from './components/GraphView.jsx';
import { formatInteger, formatPeriod, formatScore, formatTiyn, loadReport, warningText } from './report.js';
import { filterTopNodes } from './graphModel.js';

function LoadingView() {
  return (
    <main className="page" aria-busy="true" aria-live="polite">
      <header className="masthead">
        <div className="brand"><span className="brand-mark">JG</span><div><p className="brand-name">Jigas · Граф денег</p><p className="brand-subtitle">Локальный аналитический отчёт</p></div></div>
        <span className="local-state">Чтение локального отчёта</span>
      </header>
      <div className="loading-block" aria-label="Загрузка отчёта">
        <span className="skeleton skeleton-title" /><span className="skeleton skeleton-line" />
        <div className="skeleton-grid"><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /><span className="skeleton" /></div>
        <span className="skeleton skeleton-panel" />
      </div>
    </main>
  );
}

function ErrorView({ message, onRetry }) {
  return (
    <main className="page">
      <header className="masthead">
        <div className="brand"><span className="brand-mark">JG</span><div><p className="brand-name">Jigas · Граф денег</p><p className="brand-subtitle">Локальный аналитический отчёт</p></div></div>
        <span className="local-state">Только локальные данные</span>
      </header>
      <section className="state-panel state-error" role="alert">
        <p className="eyebrow">Не удалось открыть выпуск</p>
        <h1>Отчёт недоступен</h1>
        <p>{message}</p>
        <p className="muted">Проверьте, что report.json находится рядом с report.html и открыт через localhost.</p>
        <button className="button button-primary" type="button" onClick={onRetry}>Повторить загрузку</button>
      </section>
    </main>
  );
}

function Metric({ label, value, detail, className = '' }) {
  return (
    <div className={`metric ${className}`}>
      <span className="metric-label">{label}</span>
      <strong className="metric-value">{value}</strong>
      {detail && <span className="metric-detail">{detail}</span>}
    </div>
  );
}

function ReportOverview({ report }) {
  const dataset = report.dataset;
  const nodeCount = typeof dataset.node_count === 'number' ? dataset.node_count : report.nodes.length;
  const edgeCount = typeof dataset.edge_count === 'number' ? dataset.edge_count : report.edges.length;
  const transactionCount = typeof dataset.transaction_count === 'number' ? dataset.transaction_count : null;
  const demo = report.demo === true;

  return (
    <>
      <section className="hero">
        <div>
          <p className="eyebrow">Приоритеты и структура переводов</p>
          <h1>Кого проверить дальше</h1>
          <p className="period">Период наблюдения: {formatPeriod(dataset)}</p>
        </div>
        <div className="hero-badges">
          {demo && <span className="demo-badge">Учебный пример</span>}
          <span className="schema-badge">Схема {report.schema_version}</span>
        </div>
      </section>

      <section className="metrics" aria-label="Сводка отчёта">
        <Metric label="Клиенты в графе" value={formatInteger(nodeCount)} />
        <Metric label="Направленные связи" value={formatInteger(edgeCount)} />
        <Metric label="Операции" value={formatInteger(transactionCount)} />
        <Metric label="Наблюдаемый оборот" value={formatTiyn(dataset.sum_tiyn)} className="metric-money" />
      </section>

      <aside className="scope-note">
        <span className="scope-mark" aria-hidden="true">i</span>
        <div>
          <strong>Результат показывает структуру выгрузки, а не подтверждённое нарушение.</strong>
          <p>{typeof dataset.limitations === 'string' && dataset.limitations.trim()
            ? dataset.limitations
            : 'Входящие и исходящие операции ограничены условиями выборки. Проверяйте выводы по полным данным банка.'}</p>
        </div>
      </aside>
    </>
  );
}

function TopNodes({ topNodes, nodeIndex, selectedGid, onSelect }) {
  const rows = topNodes;
  return (
    <section className="panel top-panel" aria-labelledby="top-title">
      <div className="panel-head">
        <div><p className="eyebrow">Список приоритета</p><h2 id="top-title">Топ-20 клиентов</h2></div>
        <span className="count-pill">{rows.length} строк</span>
      </div>
      {rows.length === 0 ? (
        <div className="empty-state"><strong>Нет клиентов для списка</strong><span>Проверьте фильтры: ни одна строка top_nodes им не соответствует.</span></div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead><tr><th scope="col">Ранг</th><th scope="col">Клиент</th><th scope="col">Роль</th><th scope="col">Приоритет</th><th scope="col">Основание</th></tr></thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.gid} className={selectedGid === item.gid ? 'is-selected' : ''}>
                  <td className="rank-cell">{formatInteger(item.rank)}</td>
                  <td>
                    <button className="gid-action" type="button" aria-pressed={selectedGid === item.gid} onClick={() => onSelect(item.gid)}>{item.gid}</button>
                    <details className="mobile-reason"><summary>Основание</summary><p>{item.why || nodeIndex.get(item.gid)?.evidence || 'Подробная причина не приложена.'}</p></details>
                  </td>
                  <td><RoleTag role={item.role} /></td>
                  <td className="numeric-cell">{formatScore(item.priority_score)}</td>
                  <td className="reason-cell">{item.why || nodeIndex.get(item.gid)?.evidence || 'Подробная причина не приложена.'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel-footnote">Порядок и оценки рассчитаны Python. Интерфейс их не пересчитывает.</p>
    </section>
  );
}

function RoleTag({ role }) {
  const labels = {
    seed: 'Seed',
    consolidator: 'Консолидатор',
    transit: 'Транзитный',
    peripheral: 'Периферийный',
    isolated: 'Изолят',
  };
  const value = typeof role === 'string' && role ? role : 'Не указана';
  return <span className="role-tag">{labels[value] || value}</span>;
}

function CopyGidButton({ gid }) {
  const [copyState, setCopyState] = useState('idle');
  async function copyGid() {
    try {
      await navigator.clipboard.writeText(gid);
      setCopyState('copied');
      window.setTimeout(() => setCopyState('idle'), 1800);
    } catch {
      setCopyState('failed');
    }
  }
  const label = copyState === 'copied' ? 'Скопировано' : copyState === 'failed' ? 'Не удалось скопировать' : 'Скопировать ID';
  return <button className="copy-button" type="button" onClick={copyGid} aria-label={`Скопировать полный ID ${gid}`}>{label}</button>;
}

function NodeDetails({ node, topIndex, topItem }) {
  if (!node) {
    return <section className="panel detail-panel" aria-labelledby="detail-title"><div className="empty-state"><strong id="detail-title">Выберите клиента</strong><span>Найдите точный ID или выберите клиента на графе, в топе или в кластере.</span></div></section>;
  }

  const metrics = [
    ['Входящий объём', formatTiyn(node.in_tiyn)],
    ['Исходящий объём', formatTiyn(node.out_tiyn)],
    ['Входящие операции', formatInteger(node.in_tx)],
    ['Исходящие операции', formatInteger(node.out_tx)],
    ['Входящие связи', formatInteger(node.in_deg)],
    ['Исходящие связи', formatInteger(node.out_deg)],
  ];
  const warnings = Array.isArray(node.warnings) ? [...node.warnings] : [];
  if (node.boundary === true && !warnings.some((item) => item === 'boundary' || item?.code === 'boundary')) warnings.push('boundary');
  if (node.isolated === true && !warnings.some((item) => item === 'isolated' || item?.code === 'isolated')) warnings.push('isolated');
  const matchedRoles = Array.isArray(node.matched_roles) ? node.matched_roles : [];

  return (
    <section className="panel detail-panel" aria-labelledby="detail-title">
      <div className="panel-head detail-head">
        <div><p className="eyebrow">Карточка клиента{topIndex ? ` · ранг ${formatInteger(topIndex)}` : ''}</p><h2 id="detail-title" className="detail-gid">{node.gid}</h2><CopyGidButton gid={node.gid} /></div>
        <RoleTag role={node.role} />
      </div>
      <div className="score-row">
        <div><span>Приоритет</span><strong>{formatScore(node.priority_score)}</strong></div>
        <div><span>Оценка роли</span><strong>{formatScore(node.role_score)}</strong></div>
        <div><span>Кластер</span><strong>{node.cluster_id ?? '—'}</strong></div>
      </div>
      <div className="priority-reason"><strong>Ведущие основания приоритета</strong><p>{topItem?.why || node.why || node.evidence || 'Подробная причина не приложена к этому отчёту.'}</p></div>
      {node.evidence && node.evidence !== (topItem?.why || node.why) && <p className="evidence-copy">{node.evidence}</p>}
      {matchedRoles.length > 0 && (
        <div className="matched-roles"><strong>Совпавшие правила</strong><ul>
          {matchedRoles.map((match, index) => (
            <li key={`${match?.role || 'role'}-${index}`}><span className="match-copy"><span>{match?.role || 'Роль'}</span>{match?.reason && <small>{match.reason}</small>}</span><span>{formatScore(match?.support)}</span></li>
          ))}
        </ul></div>
      )}
      {matchedRoles.length > 0 && matchedRoles.every((match) => !match?.reason) && <p className="reason-fallback">Для отдельных совпавших правил нет объяснений в этом отчёте. Роль не пересчитывается.</p>}
      <dl className="node-metrics">
        {metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      {warnings.length > 0 ? (
        <div className="warning-block"><strong>Ограничения и предупреждения</strong><ul>{warnings.map((warning, index) => <li key={`${warning?.code || warning}-${index}`}>{warningText(warning)}</li>)}</ul></div>
      ) : <p className="no-warning">Для клиента нет отдельных предупреждений. Общие ограничения отчёта остаются в силе.</p>}
    </section>
  );
}

function ClusterList({ clusters, nodes, onSelect }) {
  if (!clusters.length) {
    return <section className="panel cluster-panel"><div className="panel-head"><div><p className="eyebrow">Группы связей</p><h2>Кластеры</h2></div></div><div className="empty-state"><strong>Кластеры не сформированы</strong><span>В отчёте нет сводок кластеров.</span></div></section>;
  }

  return (
    <section className="panel cluster-panel" aria-labelledby="cluster-title">
      <div className="panel-head"><div><p className="eyebrow">Группы связей</p><h2 id="cluster-title">Кластеры</h2></div><span className="count-pill">{clusters.length}</span></div>
      <div className="cluster-list">
        {clusters.map((cluster, index) => {
          const key = String(cluster.cluster_id ?? `unknown-${index}`);
          const members = nodes.filter((node) => String(node.cluster_id) === String(cluster.cluster_id));
          const ordered = [...members].sort((a, b) => (b.priority_score ?? -1) - (a.priority_score ?? -1));
          return (
            <details className="cluster-item" key={`${key}-${index}`}>
              <summary>
                <span className="cluster-title">Кластер {cluster.cluster_id ?? '—'}</span>
                <span className="cluster-meta">{formatInteger(cluster.n_nodes ?? members.length)} клиентов · {formatInteger(cluster.n_seed)} seed</span>
                <span className="cluster-volume">{formatTiyn(cluster.sum_tiyn_internal)}</span>
              </summary>
              <div className="cluster-content">
                {cluster.hypothesis && <p>{cluster.hypothesis}</p>}
                {ordered.length > 0 ? <ul className="cluster-members">{ordered.map((node) => (
                  <li key={node.gid}><button className="gid-action" type="button" onClick={() => onSelect(node.gid)}>{node.gid}</button><RoleTag role={node.role} /><span>{formatScore(node.priority_score)}</span></li>
                ))}</ul> : <p className="muted">Узлы этого кластера не найдены в массиве nodes.</p>}
              </div>
            </details>
          );
        })}
      </div>
    </section>
  );
}

function CsvLinks() {
  const files = [
    ['nodes_roles.csv', 'Все клиенты и роли'],
    ['clusters.csv', 'Сводка кластеров'],
    ['top_nodes.csv', 'Топ приоритета'],
  ];
  return (
    <section className="panel exports-panel" aria-labelledby="exports-title">
      <div className="panel-head"><div><p className="eyebrow">Файлы выпуска</p><h2 id="exports-title">Скачать CSV</h2></div></div>
      <ul className="export-list">
        {files.map(([href, label]) => <li key={href}><a href={`./${href}`} download>{label}<span>{href}</span></a></li>)}
      </ul>
    </section>
  );
}

export default function App() {
  const [report, setReport] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [retryKey, setRetryKey] = useState(0);
  const [selectedGid, setSelectedGid] = useState(null);
  const [filters, setFilters] = useState({ role: 'all', cluster: 'all', seedMode: 'all', graphScope: 'neighborhood', colorMode: 'role' });
  const nodeIndex = useMemo(() => new Map((report?.nodes || []).map((node) => [node.gid, node])), [report]);
  const topRankByGid = useMemo(() => new Map((report?.top_nodes || []).map((item) => [item.gid, item.rank])), [report]);
  const topItemByGid = useMemo(() => new Map((report?.top_nodes || []).map((item) => [item.gid, item])), [report]);
  const filteredTopNodes = useMemo(
    () => report ? filterTopNodes(report.top_nodes, nodeIndex, filters) : [],
    [report, nodeIndex, filters],
  );

  useEffect(() => {
    const controller = new AbortController();
    setReport(null);
    setLoadError('');
    loadReport(controller.signal)
      .then((loaded) => {
        setReport(loaded);
        const firstTop = loaded.top_nodes.slice(0, 20).find((item) => loaded.nodes.some((node) => node.gid === item.gid));
        setSelectedGid(firstTop?.gid ?? loaded.nodes[0]?.gid ?? null);
      })
      .catch((error) => {
        if (error.name !== 'AbortError') setLoadError(error.message || 'Не удалось прочитать report.json.');
      });
    return () => controller.abort();
  }, [retryKey]);

  if (!report && !loadError) return <LoadingView />;
  if (!report) return <ErrorView message={loadError} onRetry={() => setRetryKey((value) => value + 1)} />;

  const selectedNode = typeof selectedGid === 'string' ? nodeIndex.get(selectedGid) : null;
  const emptyReport = report.nodes.length === 0;

  return (
    <main className="page">
      <header className="masthead">
        <div className="brand"><span className="brand-mark">JG</span><div><p className="brand-name">Jigas · Граф денег</p><p className="brand-subtitle">Структура переводов для проверки</p></div></div>
        <span className="local-state">Работает из локального выпуска</span>
      </header>
      <ReportOverview report={report} />

      {emptyReport ? (
        <section className="panel empty-report" role="status"><p className="eyebrow">Нет данных</p><h2>В этом выпуске нет клиентов</h2><p>Массив nodes пуст. Проверьте входные файлы и условия выгрузки.</p></section>
      ) : (
        <>
          <GraphView report={report} selectedGid={selectedGid} onSelectGid={setSelectedGid} filters={filters} onFiltersChange={setFilters} />
          <section className="content-grid">
            <TopNodes topNodes={filteredTopNodes} nodeIndex={nodeIndex} selectedGid={selectedGid} onSelect={setSelectedGid} />
            <NodeDetails node={selectedNode} topIndex={selectedGid ? topRankByGid.get(selectedGid) : null} topItem={selectedGid ? topItemByGid.get(selectedGid) : null} />
          </section>
        </>
      )}

      <section className="secondary-grid">
        <ClusterList clusters={report.clusters} nodes={report.nodes} onSelect={setSelectedGid} />
        <CsvLinks />
      </section>
      <footer className="page-footer"><span>schema_version {report.schema_version}</span><span>Сводки и оценки сформированы Python batch-процессом</span></footer>
    </main>
  );
}
