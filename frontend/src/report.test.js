import test from 'node:test';
import assert from 'node:assert/strict';
import { ruleFacts } from './report.js';

test('role explanations use observed values and report thresholds', () => {
  const node = {
    seed_reach_count: 4, in_deg: 5, out_deg: 99, betweenness: 0.0045572341,
    depth: 2, in_tiyn: 9000000, out_tiyn: 9000000, pass_through: 1,
    days_after_last_in: 9,
  };
  const parameters = { role_parameters: {
    coordinator: { seed_reach_count_min: 2, in_deg_min: 2, out_deg_min: 2, betweenness_threshold: 0.00048324374 },
    distributor: { out_deg_min: 10 },
    consolidator: { in_deg_min: 3 },
    transit: { depth_max_exclusive: 4, pass_through_min: 0.8, pass_through_max: 1.2 },
    terminal: { depth_max_exclusive: 4, days_after_last_in_min: 2 },
  } };
  const explain = (role) => ruleFacts(node, { role, reason: 'machine evidence' }, parameters).join(' ');
  assert.match(explain('coordinator'), /4 seed-клиентов.*не менее 2/);
  assert.match(explain('coordinator'), /5 входящих и 99 исходящих/);
  assert.match(explain('distributor'), /99 разным получателям.*от 10/);
  assert.match(explain('consolidator'), /5 разных плательщиков.*от 3/);
  assert.match(explain('transit'), /90\s000,00 ₸.*0,8–1,2/);
  assert.match(explain('terminal'), /9 дней.*порог — 2/);
  assert.deepEqual(ruleFacts(node, { role: 'coordinator' }, parameters), []);
});
