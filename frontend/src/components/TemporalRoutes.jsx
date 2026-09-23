import { formatInteger, formatTiyn } from '../report.js';

function Route({ label, route, mode, onSelect, onShowRoute }) {
  return <div className="temporal-route">
    <div className="temporal-route-heading"><strong>{label}</strong>{route && <button type="button" className="button button-quiet" onClick={() => onShowRoute(mode)}>Показать на графе</button>}</div>
    {route ? <>
      <p>От seed-клиента <button type="button" className="gid-action" onClick={() => onSelect(route.seed_gid)}>{route.seed_gid}</button> за {formatInteger(route.steps.length)} перевод(а).</p>
      <ol className="temporal-steps">{route.steps.map((step, index) => <li key={`${step.tx_row}-${index}`}>
        <time dateTime={step.date}>{step.date}</time> · <button type="button" className="gid-action" onClick={() => onSelect(step.src)}>{step.src}</button> → <button type="button" className="gid-action" onClick={() => onSelect(step.dst)}>{step.dst}</button> · {formatTiyn(step.sum_tiyn)} <span>строка {formatInteger(step.tx_row)}</span>
      </li>)}</ol>
    </> : <p>Маршрут до 4 переводов по заданному правилу дат не найден.</p>}
  </div>;
}

export default function TemporalRoutes({ node, routes, onSelect, onShowRoute }) {
  if (!routes) return <details className="temporal-routes"><summary>Маршруты от seed-клиентов</summary><p>Не рассчитано для этого выпуска.</p></details>;
  const witness = routes[node.gid];
  if (!witness) return <p className="route-error" role="alert">Данные маршрутов для клиента отсутствуют в выпуске.</p>;
  return <details className="temporal-routes"><summary>Маршруты от seed-клиентов</summary>
    <div className="temporal-counts">
      <div><span>Месячный граф (без дат)</span><strong>{formatInteger(node.seed_reach_count)}</strong></div>
      <div><span>Прямые seed-плательщики</span><strong>{Number.isInteger(node.n_seed_payers) ? formatInteger(node.n_seed_payers) : 'Не рассчитано'}</strong></div>
      <div><span>Строго позже</span><strong>{formatInteger(node.seed_reach_strict_count)}</strong></div>
      <div><span>В тот же день или позже</span><strong>{formatInteger(node.seed_reach_same_day_count)}</strong></div>
    </div>
    <Route label="Даты строго возрастают" route={witness.strict} mode="strict" onSelect={onSelect} onShowRoute={onShowRoute} />
    <Route label="Одинаковый день допускается" route={witness.same_day} mode="same_day" onSelect={onSelect} onShowRoute={onShowRoute} />
    <p className="temporal-caveat">Для переводов в один день порядок неизвестен. Это совместимый по датам путь, а не прослеженный объём денег. Отсутствие маршрута не означает безопасность.</p>
  </details>;
}
