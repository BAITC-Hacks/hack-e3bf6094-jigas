import { useEffect, useMemo, useState } from 'react';
import GraphView from './components/GraphView.jsx';
import { formatInteger, formatPeriod, formatScore, formatTiyn, loadReport, warningText } from './report.js';
import { filterTopNodes, matchesGraphFilters, nodesWithExactPriority } from './graphModel.js';

const CLIENTS_PAGE_SIZE = 50;

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

function TopNodes({ topNodes, allNodes, nodeIndex, topRankByGid, filters, selectedGid, onSelect }) {
  const [mode, setMode] = useState('top');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const allMatches = useMemo(() => allNodes.filter((node) => matchesGraphFilters(node, filters)
    && node.gid.includes(query.trim())), [allNodes, filters, query]);
  useEffect(() => setPage(0), [mode, query, filters]);
  const rows = mode === 'top'
    ? topNodes
    : allMatches.slice(page * CLIENTS_PAGE_SIZE, (page + 1) * CLIENTS_PAGE_SIZE).map((node) => ({
      ...node, rank: topRankByGid.get(node.gid) ?? null,
    }));
  const total = mode === 'top' ? topNodes.length : allMatches.length;
  return (
    <section className="panel top-panel" aria-labelledby="top-title">
      <div className="panel-head">
        <div><p className="eyebrow">Список клиентов</p><h2 id="top-title">Кого проверить</h2></div>
        <span className="count-pill">{formatInteger(total)} найдено</span>
      </div>
      <div className="list-controls">
        <div className="list-tabs" role="group" aria-label="Показать клиентов">
          <button type="button" aria-pressed={mode === 'top'} onClick={() => setMode('top')}>Топ-20</button>
          <button type="button" aria-pressed={mode === 'all'} onClick={() => setMode('all')}>Все {formatInteger(allNodes.length)}</button>
        </div>
        {mode === 'all' && <label className="table-search">Найти по части ID
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Введите цифры ID" />
        </label>}
      </div>
      {rows.length === 0 ? (
        <div className="empty-state"><strong>Клиенты не найдены</strong><span>{mode === 'top' ? 'В топе нет клиентов с этими фильтрами. Откройте полный список.' : 'Измените запрос или сбросьте фильтры графа.'}</span></div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead><tr><th scope="col">Топ</th><th scope="col">Клиент</th><th scope="col">Роль</th><th scope="col">Приоритет</th></tr></thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.gid} className={selectedGid === item.gid ? 'is-selected' : ''}>
                  <td className="rank-cell">{item.rank == null ? '—' : formatInteger(item.rank)}</td>
                  <td>
                    <button className="gid-action" type="button" aria-pressed={selectedGid === item.gid} onClick={() => onSelect(item.gid)}>{item.gid}</button>
                    <details className="mobile-reason"><summary>Основание</summary><p>{item.why || item.evidence || nodeIndex.get(item.gid)?.evidence || 'Подробная причина не приложена.'}</p></details>
                  </td>
                  <td><RoleTag role={item.role} /></td>
                  <td className="numeric-cell">{formatScore(item.priority_score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {mode === 'all' && total > CLIENTS_PAGE_SIZE && <div className="list-pagination">
        <span>{page * CLIENTS_PAGE_SIZE + 1}–{Math.min((page + 1) * CLIENTS_PAGE_SIZE, total)} из {formatInteger(total)}</span>
        <button className="button button-quiet" type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Назад</button>
        <button className="button button-quiet" type="button" disabled={(page + 1) * CLIENTS_PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>Далее</button>
      </div>}
      <p className="panel-footnote">Оценки рассчитаны Python. Ранг указан только для топ-20; полный список идёт по ID.</p>
    </section>
  );
}

function RoleTag({ role }) {
  const labels = {
    seed: 'Seed',
    coordinator: 'Координатор',
    distributor: 'Распределитель',
    consolidator: 'Консолидатор',
    transit: 'Транзитный',
    terminal: 'Кандидат в конечные',
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

function NodeDetails({ node, topIndex, topItem, tiedNodes, topRankByGid, selectedGid, onSelect }) {
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
        <div><p className="eyebrow">Карточка клиента{topIndex ? ` · ранг ${formatInteger(topIndex)}` : ''}</p><h2 id="detail-title" className="detail-gid" tabIndex="-1">{node.gid}</h2><CopyGidButton gid={node.gid} /></div>
        <RoleTag role={node.role} />
      </div>
      <div className="score-row">
        <div><span>Приоритет</span><strong>{formatScore(node.priority_score)}</strong></div>
        <div><span>Оценка роли</span><strong>{formatScore(node.role_score)}</strong></div>
        <div><span>Кластер</span><strong>{node.cluster_id ?? '—'}</strong></div>
      </div>
      <div className="priority-reason"><strong>Ведущие основания приоритета</strong><p>{topItem?.why || node.why || node.evidence || 'Подробная причина не приложена к этому отчёту.'}</p></div>
      {node.evidence && node.evidence !== (topItem?.why || node.why) && <details className="evidence-details"><summary>Дополнительные данные расчёта</summary><p>{node.evidence}</p></details>}
      {matchedRoles.length > 0 && (
        <details className="matched-roles"><summary>Совпавшие правила · {formatInteger(matchedRoles.length)}</summary><ul>
          {matchedRoles.map((match, index) => (
            <li key={`${match?.role || 'role'}-${index}`}><span className="match-copy"><RoleTag role={match?.role} />{match?.reason && <small>{match.reason}</small>}</span><span>Поддержка: {formatScore(match?.support)}</span></li>
          ))}
        </ul></details>
      )}
      {matchedRoles.length > 0 && matchedRoles.every((match) => !match?.reason) && <p className="reason-fallback">Для отдельных совпавших правил нет объяснений в этом отчёте. Роль не пересчитывается.</p>}
      <details className="node-metrics-details"><summary>Объёмы и связи</summary><dl className="node-metrics">
        {metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl></details>
      {tiedNodes.length > 1 && <details className="priority-ties">
        <summary>Одинаковый приоритет у {formatInteger(tiedNodes.length)} клиентов</summary>
        <p>Сравнены сохранённые значения без округления. Ранг есть только у клиентов из топ-20.</p>
        <ul className="priority-tie-list">
          {tiedNodes.map((tiedNode) => (
            <li key={tiedNode.gid}>
              <button className="gid-action" type="button" aria-pressed={selectedGid === tiedNode.gid} onClick={() => onSelect(tiedNode.gid)}>{tiedNode.gid}</button>
              <RoleTag role={tiedNode.role} />
              <span>{topRankByGid.has(tiedNode.gid) ? 'Ранг ' + formatInteger(topRankByGid.get(tiedNode.gid)) : 'Вне топ-20'}</span>
            </li>
          ))}
        </ul>
      </details>}
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
      <details className="cluster-browser"><summary>Показать список кластеров</summary><div className="cluster-list">
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
      </div></details>
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
  const [searchDraft, setSearchDraft] = useState('');
  const [searchMessage, setSearchMessage] = useState('');
  const [filters, setFilters] = useState({ role: 'all', roleMode: 'primary', cluster: 'all', seedMode: 'all', graphScope: 'neighborhood', colorMode: 'role' });
  const nodeIndex = useMemo(() => new Map((report?.nodes || []).map((node) => [node.gid, node])), [report]);
  const topRankByGid = useMemo(() => new Map((report?.top_nodes || []).map((item) => [item.gid, item.rank])), [report]);
  const topItemByGid = useMemo(() => new Map((report?.top_nodes || []).map((item) => [item.gid, item])), [report]);
  const filteredTopNodes = useMemo(
    () => report ? filterTopNodes(report.top_nodes, nodeIndex, filters) : [],
    [report, nodeIndex, filters],
  );
  const selectedNode = typeof selectedGid === 'string' ? nodeIndex.get(selectedGid) : null;
  const tiedNodes = useMemo(
    () => report && selectedNode ? nodesWithExactPriority(report, selectedNode) : [],
    [report, selectedNode],
  );

  function selectGid(gid) {
    setSelectedGid(gid);
    setSearchDraft(gid ?? '');
    setSearchMessage('');
  }

  function selectAndReveal(gid) {
    selectGid(gid);
    window.requestAnimationFrame(() => {
      const title = document.getElementById('detail-title');
      title?.focus({ preventScroll: true });
      title?.closest('.detail-panel')?.scrollIntoView({ block: 'start' });
    });
  }

  function searchExact(event) {
    event.preventDefault();
    const gid = searchDraft.trim();
    if (!gid || !nodeIndex.has(gid)) {
      setSelectedGid(null);
      setSearchMessage(gid ? 'Клиент с таким ID не найден. Выбор очищен.' : 'Введите полный ID клиента.');
      return;
    }
    setSelectedGid(gid);
    setSearchMessage('Клиент найден. Его карточка и связи обновлены.');
  }

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
          <section className="workspace-heading" aria-labelledby="workspace-title">
            <div><p className="eyebrow">1 · Выберите клиента</p><h2 id="workspace-title">Приоритеты и основания</h2></div>
            <form className="client-search" role="search" onSubmit={searchExact}>
              <label htmlFor="client-search">Поиск по полному ID</label>
              <div className="client-search-row"><input id="client-search" type="search" inputMode="numeric" autoComplete="off" spellCheck="false" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="Введите ID клиента" /><button className="button button-primary" type="submit">Найти</button></div>
              <span className={searchMessage.includes('не найден') || searchMessage.includes('Введите') ? 'search-error' : 'search-success'} role={searchMessage.includes('не найден') || searchMessage.includes('Введите') ? 'alert' : 'status'}>{searchMessage || 'ID сравнивается как строка без округления.'}</span>
            </form>
          </section>
          <section className="content-grid">
            <TopNodes topNodes={filteredTopNodes} allNodes={report.nodes} nodeIndex={nodeIndex} topRankByGid={topRankByGid} filters={filters} selectedGid={selectedGid} onSelect={selectAndReveal} />
            <NodeDetails node={selectedNode} topIndex={selectedGid ? topRankByGid.get(selectedGid) : null} topItem={selectedGid ? topItemByGid.get(selectedGid) : null} tiedNodes={tiedNodes} topRankByGid={topRankByGid} selectedGid={selectedGid} onSelect={selectAndReveal} />
          </section>
          <GraphView report={report} selectedGid={selectedGid} onSelectGid={selectGid} filters={filters} onFiltersChange={setFilters} />
        </>
      )}

      <section className="secondary-grid">
        <ClusterList clusters={report.clusters} nodes={report.nodes} onSelect={selectAndReveal} />
        <CsvLinks />
      </section>
      <footer className="page-footer"><span>schema_version {report.schema_version}</span><span>Сводки и оценки сформированы Python batch-процессом</span></footer>
    </main>
  );
}
