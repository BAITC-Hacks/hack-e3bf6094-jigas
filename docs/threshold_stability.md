# HA-12.2 — чувствительность порогов ролей

Эксперимент проверяет, как отдельное изменение одного числового порога роли на ±20% влияет на назначения ролей и P-top-20. Это техническая чувствительность к фиксированному набору; она не оценивает точность ролей, риск или полезность AML.

## Метод

Скрипт импортирует release `hackalem.scoring.assign_roles`, получает его исходный текст через `inspect`, разбирает в AST и компилирует варианты. Для обычных порогов заменяется ровно один найденный литерал сравнения; для coordinator betweenness AST добавляет масштабирование уже рассчитанного release-кодом Q90 на 0.8 или 1.2. Условия вокруг порога, порядок ролей, поддержки и структура результата остаются из release-функции. Production-файл не меняется. Перед вариациями отдельно скомпилированный неизменённый AST-клон сравнивается с прямым вызовом release-функции по полному отпечатку результатов. Приоритет пересчитывается неизменённым `compute_priority`; P-веса не меняются.

Проверены оба направления ±20% для S coordinator, входящей/исходящей степени coordinator, порога coordinator betweenness относительно Q90, out-degree distributor, in-degree consolidator, обеих границ pass-through у transit и D terminal. Каждый сценарий меняет только один порог.

Для каждого входа вычислены release признаки; `assign_roles`/вариант и `compute_priority` повторены отдельно для baseline и каждого сценария. Полный отпечаток по каждому узлу (основная роль, все совпавшие роли и support, неокруглённый P) и упорядоченный top-20 сравнен между повторами.

## Набор и среда

- Официальные таблицы: 2248 узлов, 3119 агрегированных рёбер, 4840 транзакций, 81 seed; повторов на состояние: 2.
- Python 3.11.4, pandas 2.2.3, NumPy 1.26.4, PyArrow 25.0.1, NetworkX 3.2.1.
- SHA-256 исходника `hackalem/scoring.py` (для функции `assign_roles`): `323d9e38a7f2d0f97e7bd673b789c58b3088c3a7fd28061eddb0538667d9efed`.
- Commit, последний изменявший `hackalem/scoring.py`: `7a6da9ba1fce66e8ed62a0756459564bac416e41`; отпечаток baseline AST-клона совпал с прямым вызовом release `assign_roles` и `compute_priority`: `42abcb2c2301dcc989f941bff53b21550fccd3cdf4b4e0a50694bfadcd227b2b`.
- SHA-256 `nodes.parquet`: `d2a45b0df6e9352832d5fb09839d10b9e23f898156c3bab263b051b31cc0296d`.
- SHA-256 `edges.parquet`: `4e71dde5cd3115bcb26e91202665532ee6581cf8233a9fc8059ea59fb7358a38`.
- SHA-256 `transactions.parquet`: `c30c5317b5439591dde86f2058dc47a3d19b2900c055ded994fe547f6fb7e7da`.
- Baseline P-top-20, gid в порядке ранга: 100000003684369100, 100000008346837100, 100000000331309100, 100000000437046100, 100000004156082100, 100000008603629100, 100000002957787100, 100000005910114100, 100000000343175100, 100000001857829100, 100000003016635100, 100000006866783100, 100000008477350100, 100000008686313100, 100000008547844100, 100000008547948100, 100000008165763100, 100000008710791100, 100000003242289100, 100000004400305100.
- Baseline primary roles: peripheral 1012, terminal 947, consolidator 158, transit 55, coordinator 43, distributor 33.

В этой среде используются установленные версии Python 3.11.4 / NumPy 1.26.4 / NetworkX 3.2.1; SPEC требует Python 3.13, а `requirements.txt` фиксирует NumPy 2.2.6 и NetworkX 3.6.1. pandas 2.2.3 и PyArrow 25.0.1 совпадают с pin-ами. Результат фиксирует фактическую среду, не подтверждает воспроизводимость на указанном релизном окружении.

## Результаты

`Primary changes` — число узлов с другой основной ролью относительно baseline. `Role-membership changes` — число узлов, у которых изменился набор всех совпавших ролей; `membership edits` — число добавленных/удалённых совпадений. Top-20 overlap сравнивает узлы варианта с baseline P-top-20 из 20.

| Роль / параметр | Baseline | Сценарий | Primary changes | Узлы с изменением набора ролей | Membership edits | P-top-20 overlap | Выпало / вошло в top-20 | Повторный отпечаток |
|---|---:|---|---:|---:|---:|---:|---|---|
| coordinator: `seed_reach_count_min` | 2 | 1 (цель 1.6; floor; фактически -50%) | 2 (0.09%) | 2 | 2 | 20/20 (100%) | − —; + — | `b52c6cc473815f23` |
| coordinator: `seed_reach_count_min` | 2 | 3 (цель 2.4; ceil; фактически +50%) | 15 (0.67%) | 15 | 15 | 20/20 (100%) | − —; + — | `724fe893d6007905` |
| coordinator: `in_deg_min` | 2 | 1 (цель 1.6; floor; фактически -50%) | 3 (0.13%) | 3 | 3 | 20/20 (100%) | − —; + — | `49437721129e86bd` |
| coordinator: `in_deg_min` | 2 | 3 (цель 2.4; ceil; фактически +50%) | 11 (0.49%) | 11 | 11 | 20/20 (100%) | − —; + — | `1c2fa2269087944f` |
| coordinator: `out_deg_min` | 2 | 1 (цель 1.6; floor; фактически -50%) | 1 (0.04%) | 1 | 1 | 20/20 (100%) | − —; + — | `9a9d971362d424bb` |
| coordinator: `out_deg_min` | 2 | 3 (цель 2.4; ceil; фактически +50%) | 1 (0.04%) | 1 | 1 | 20/20 (100%) | − —; + — | `a9830858c70bfe5a` |
| coordinator: `betweenness_threshold` | Q90 = 0.00048324374 | B ≥ Q90 × 0.8 = 0.000386595 (-20%) | 2 (0.09%) | 2 | 2 | 20/20 (100%) | − —; + — | `be9f8018dffe2b15` |
| coordinator: `betweenness_threshold` | Q90 = 0.00048324374 | B ≥ Q90 × 1.2 = 0.00057989249 (+20%) | 3 (0.13%) | 3 | 3 | 20/20 (100%) | − —; + — | `48ec7444727acf47` |
| distributor: `out_deg_min` | 10 | 8 (цель 8; floor; фактически -20%) | 11 (0.49%) | 14 | 14 | 20/20 (100%) | − —; + — | `e24fdaafd3986968` |
| distributor: `out_deg_min` | 10 | 12 (цель 12; ceil; фактически +20%) | 9 (0.40%) | 11 | 11 | 20/20 (100%) | − —; + — | `0ec01944086febe4` |
| consolidator: `in_deg_min` | 3 | 2 (цель 2.4; floor; фактически -33%) | 230 (10.23%) | 246 | 246 | 20/20 (100%) | − —; + — | `074ee93fcffa02ac` |
| consolidator: `in_deg_min` | 3 | 4 (цель 3.6; ceil; фактически +33%) | 95 (4.23%) | 111 | 111 | 20/20 (100%) | − —; + — | `0ed475624ca71ad6` |
| transit: `pass_through_min` | 0.8 | 0.64 (-20%) | 32 (1.42%) | 42 | 42 | 20/20 (100%) | − —; + — | `a20bcbd2243f7d72` |
| transit: `pass_through_min` | 0.8 | 0.96 (+20%) | 10 (0.44%) | 13 | 13 | 20/20 (100%) | − —; + — | `1468a159b061259c` |
| transit: `pass_through_max` | 1.2 | 0.96 (-20%) | 45 (2.00%) | 57 | 57 | 20/20 (100%) | − —; + — | `3b531a462c5f848a` |
| transit: `pass_through_max` | 1.2 | 1.44 (+20%) | 18 (0.80%) | 21 | 21 | 20/20 (100%) | − —; + — | `81f2854e246bd736` |
| terminal: `days_after_last_in_min` | 2 | 1 (цель 1.6; floor; фактически -50%) | 43 (1.91%) | 47 | 47 | 20/20 (100%) | − —; + — | `def4da70cadbc8de` |
| terminal: `days_after_last_in_min` | 2 | 3 (цель 2.4; ceil; фактически +50%) | 36 (1.60%) | 41 | 41 | 20/20 (100%) | − —; + — | `1cf45c5126c6ca71` |

Изменения primary role по сценариям:
- `coordinator.seed_reach_count_min:-20%`: coordinator +2, distributor -1, transit -1
- `coordinator.seed_reach_count_min:+20%`: coordinator -15, distributor +10, consolidator +2, transit +1, peripheral +2
- `coordinator.in_deg_min:-20%`: coordinator +3, distributor -1, peripheral -2
- `coordinator.in_deg_min:+20%`: coordinator -11, distributor +4, transit +1, peripheral +6
- `coordinator.out_deg_min:-20%`: coordinator +1, consolidator -1
- `coordinator.out_deg_min:+20%`: coordinator -1, consolidator +1
- `coordinator.betweenness_threshold:-20%`: coordinator +2, consolidator -1, peripheral -1
- `coordinator.betweenness_threshold:+20%`: coordinator -3, distributor +2, consolidator +1
- `distributor.out_deg_min:-20%`: distributor +11, consolidator -6, peripheral -5
- `distributor.out_deg_min:+20%`: distributor -9, consolidator +3, peripheral +6
- `consolidator.in_deg_min:-20%`: consolidator +230, transit -14, terminal -84, peripheral -132
- `consolidator.in_deg_min:+20%`: consolidator -95, transit +11, terminal +20, peripheral +64
- `transit.pass_through_min:-20%`: transit +32, peripheral -32
- `transit.pass_through_min:+20%`: transit -10, peripheral +10
- `transit.pass_through_max:-20%`: transit -45, peripheral +45
- `transit.pass_through_max:+20%`: transit +18, peripheral -18
- `terminal.days_after_last_in_min:-20%`: terminal +43, peripheral -43
- `terminal.days_after_last_in_min:+20%`: terminal -36, peripheral +36

## Ограничения интерпретации

- Пороговые счётчики целочисленные. Сначала рассчитана цель ±20%; затем для отрицательного сценария применён `floor`, для положительного — `ceil`, чтобы условие действительно сдвинулось. Таблица показывает эффективный integer-порог, точную дробную цель и фактический процентный шаг. Поэтому для малых исходных порогов фактический шаг больше 20%.
- Не изменялись supports/caps, знаменатели `u`, порядок precedence, компоненты и веса priority, структурные условия (`depth < 4`, seed, положительные суммы, `out_deg = 0`) и правило `B > 0`. Это анализ порогов входа в роль, а не чувствительности всей модели.
- Текст `matched_roles.reason` в AST-вариантах формируется релизной функцией и может упоминать исходный порог. В метриках эксперимента используются роли, supports и P; текст причины не служит объяснением контрфактического сценария.
- Квантили Q90/Q95 в priority не заменялись. В сценарии coordinator меняется числовой cutoff от Q90; P-параметры и Q95-нормировка остаются release baseline.
- Пересечение top-20 и изменения ролей — описательные метрики стабильности на этих входных данных, не ground truth, accuracy/F1 или доказательство практической AML-полезности.

## Воспроизведение

Фактическая команда в этой рабочей среде:

```powershell
python scripts\evaluate_threshold_sensitivity.py --data G:\HACKATON\hack-e3bf6094-jigas\data --output docs\threshold_stability.md --repeats 2
```

Для локальной копии с официальными файлами в `./data`:

```powershell
python scripts/evaluate_threshold_sensitivity.py --data ./data --output docs/threshold_stability.md --repeats 2
```
