const REQUIRED_ARRAYS = ['nodes', 'edges', 'clusters', 'top_nodes'];

export function validateReport(report) {
  if (!report || typeof report !== 'object' || Array.isArray(report)) {
    throw new Error('Корень report.json должен быть JSON-объектом.');
  }

  if (report.schema_version !== '1.0') {
    const version = typeof report.schema_version === 'string'
      ? report.schema_version
      : 'не указана';
    throw new Error(`Неподдерживаемая schema_version: ${version}. Ожидается 1.0.`);
  }

  if (!report.dataset || typeof report.dataset !== 'object' || Array.isArray(report.dataset)) {
    throw new Error('В report.json отсутствует объект dataset.');
  }

  for (const key of REQUIRED_ARRAYS) {
    if (!Array.isArray(report[key])) {
      throw new Error(`Поле ${key} должно быть массивом.`);
    }
  }

  const gids = new Set();
  for (const [index, node] of report.nodes.entries()) {
    if (!node || typeof node !== 'object' || typeof node.gid !== 'string') {
      throw new Error(`Узел ${index + 1}: gid должен быть строкой.`);
    }
    if (gids.has(node.gid)) {
      throw new Error(`В report.json повторяется gid ${node.gid}.`);
    }
    gids.add(node.gid);
  }

  for (const [index, edge] of report.edges.entries()) {
    if (!edge || typeof edge !== 'object' || typeof edge.src !== 'string' || typeof edge.dst !== 'string') {
      throw new Error(`Ребро ${index + 1}: src и dst должны быть строками.`);
    }
  }

  for (const [index, item] of report.top_nodes.entries()) {
    if (!item || typeof item !== 'object' || typeof item.gid !== 'string') {
      throw new Error(`Строка top_nodes ${index + 1}: gid должен быть строкой.`);
    }
  }

  for (const [index, cluster] of report.clusters.entries()) {
    if (!cluster || typeof cluster !== 'object') {
      throw new Error(`Кластер ${index + 1} должен быть объектом.`);
    }
    if (cluster.top_gids !== undefined && (!Array.isArray(cluster.top_gids)
      || cluster.top_gids.some((gid) => typeof gid !== 'string'))) {
      throw new Error(`Кластер ${index + 1}: top_gids должен содержать строковые ID.`);
    }
  }

  return report;
}

export async function loadReport(signal) {
  const reportUrl = new URL('./report.json', window.location.href);
  let response;
  try {
    response = await fetch(reportUrl, { cache: 'no-store', signal });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Не удалось загрузить report.json с локального сервера.');
  }

  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Рядом с report.html не найден report.json. Запустите выпуск через локальный статический сервер.');
    }
    throw new Error(`Локальный сервер вернул HTTP ${response.status} для report.json.`);
  }

  let report;
  try {
    report = await response.json();
  } catch {
    throw new Error('report.json содержит некорректный JSON.');
  }

  return validateReport(report);
}

export function formatInteger(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(value);
}

export function formatScore(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3, minimumFractionDigits: 3 }).format(value);
}

export function formatTiyn(value) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) return '—';
  const amount = BigInt(value);
  const sign = amount < 0n ? '−' : '';
  const absolute = amount < 0n ? -amount : amount;
  const whole = absolute / 100n;
  const fractional = (absolute % 100n).toString().padStart(2, '0');
  return `${sign}${new Intl.NumberFormat('ru-RU').format(whole)},${fractional} ₸`;
}

export function formatPeriod(dataset) {
  const start = typeof dataset?.period_start === 'string' ? dataset.period_start : null;
  const end = typeof dataset?.period_end === 'string' ? dataset.period_end : null;
  if (!start && !end) return 'Период не указан';
  if (start === end || !end) return start || end;
  if (!start) return `до ${end}`;
  return `${start} — ${end}`;
}

export function warningText(warning) {
  const code = typeof warning === 'string'
    ? warning
    : warning && typeof warning.code === 'string'
      ? warning.code
      : '';
  const message = warning && typeof warning.message === 'string' ? warning.message : '';
  const labels = {
    boundary: 'Граничный узел: граф выгружен не полностью.',
    seed_incomplete_in: 'Входящие операции от seed могут быть неполными.',
    isolated: 'В этой выгрузке у клиента нет входящих и исходящих связей.',
    short_followup: 'Для последнего поступления недостаточно периода наблюдения.',
  };
  return message || labels[code] || (code ? `Ограничение: ${code}` : 'Ограничение данных.');
}
