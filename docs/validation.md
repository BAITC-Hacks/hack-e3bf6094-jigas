# Протокол проверки

## Проверенный P0-выпуск

Код `aed8e63`; основная проверка выполнялась в Docker на официальном наборе данных под Python 3.13. Это фактический handoff HA-08.2, а не финальное закрытие: headless browser smoke пройден, пользовательский headed review ещё в процессе; clean-clone Docker-путь на Linux не проверен.

| Проверка | Результат |
|---|---|
| `docker compose build pipeline` и автоочистка Docker cache/images | Успешно |
| `docker compose run --rm prepare` | PASS; извлечены три Parquet |
| `docker compose run --rm pipeline` | PASS; создан выпуск из 2 248 узлов, 3 119 рёбер и 4 840 транзакций; 89 кластеров |
| `test_contract.py` на пяти отдельных выпусках | PASS на каждом запуске |
| Runtime генерации с `--network none --cpus 12` на `d58551f` | PASS; CSV совпали по SHA-256 с `run1`; staging-fix `aed8e63` не повторялся без сети |
| Headless system Chrome / Playwright на `aed8e63`, `file://`, HTTP(S) заблокирован | PASS; canvas vis-network отрисован, UI smoke прошёл, ошибок страницы/консоли и внешних запросов нет |
| Linux bind-mount запуск с host UID/GID | Не проверен на Linux-хосте |
| Пользовательский headed visual review | В процессе |

Пять запусков выполнялись на хосте AMD Ryzen 7 6800H (8 физических / 16 логических ядер), Docker Engine 29.7.2. Контейнеру был выделен лимит 12 CPU (`cpu.max=1200000 100000`). Доступная контейнеру память в протоколе не зафиксирована.

| Выпуск | Внешний Stopwatch | Exit code | `test_contract.py` |
|---|---:|---:|---|
| `final1` | 4,125 с | 0 | PASS |
| `final2` | 4,327 с | 0 | PASS |
| `final3` | 4,035 с | 0 | PASS |
| `final4` | 4,642 с | 0 | PASS |
| `final5` | 4,336 с | 0 | PASS |
| **Медиана / максимум** | **4,327 / 4,642 с** |  |  |

Все пять запусков выполнили контракт:

```powershell
docker compose run --rm --entrypoint python pipeline test_contract.py --data /app/data --out /app/out/final1
```

В командах для остальных каталогов заменялся `final1` на `final2` … `final5`. Сырые данные готовились один раз до серии; каждый выпуск писался в отдельную папку, старые папки не очищались. Максимум 4,642 с ниже порога `<300 секунд` для выпуска трёх обязательных CSV.

SHA-256 CSV одинаковы на всех пяти прогонах:

| CSV | SHA-256 |
|---|---|
| `nodes_roles.csv` | `c0eb5067d49a4c06ccc0a35c6005fc50dd048dc7a54ec10096a4a477139fc107` |
| `clusters.csv` | `9e377a1232d1637683abcf7a8fcdbce7d72cf1b4017cc9b6e14cbaaaabc8d363` |
| `top_nodes.csv` | `16899590ecba75ac6fb23b40a5af2e36b8bd9c62910177e42d9d79c89db54e56` |

Отдельный контейнерный запуск на `d58551f` с `--network none --cpus 12` использовал входной bind mount только для чтения, создал `out/offline` и завершился успешно. Все три CSV имеют те же SHA-256, что и предыдущий Docker `run1`. Это подтверждает offline runtime генерации на том commit; после staging-fix `aed8e63` прогон не повторялся. Открытие и использование полученного `report.html` в браузере при отключённой сети не проверено.

Дополнительный native smoke прошёл в локальном Python 3.11.4, который не соответствует заявленному Python 3.13. `starter.py` и `test_contract.py` завершились успешно на выходе из 2 248/3 119/4 840, но этот запуск не считается подтверждением поддерживаемого native окружения; для него нужна проверка Python 3.13 из README. Официальными приёмочными измерениями выше являются Docker-запуски.

## Повторяемые команды

Сборка и подготовка выполняются один раз до серии:

```powershell
docker compose build pipeline
docker compose run --rm prepare
```

Далее каждый запуск получает отдельную папку:

```powershell
docker compose run --rm pipeline --data /app/data --out /app/out/final1
docker compose run --rm pipeline --data /app/data --out /app/out/final2
docker compose run --rm pipeline --data /app/data --out /app/out/final3
docker compose run --rm pipeline --data /app/data --out /app/out/final4
docker compose run --rm pipeline --data /app/data --out /app/out/final5
```

В PowerShell снимать время можно внешним Stopwatch:

```powershell
$timer = [System.Diagnostics.Stopwatch]::StartNew()
docker compose run --rm pipeline --data /app/data --out /app/out/final1
$exitCode = $LASTEXITCODE
$timer.Stop()
"exit={0}; elapsed={1:N3} s" -f $exitCode, $timer.Elapsed.TotalSeconds
```

Контрактный тест каждого выпуска запускается в том же контейнере:

```powershell
docker compose run --rm --entrypoint python pipeline test_contract.py --data /app/data --out /app/out/final1
```

Образы требуют доступа к PyPI при сборке. Для проверки runtime без сети использовался уже собранный образ; выполненный прогон на `d58551f` имел `--network none`, `--cpus 12` и read-only bind mount для данных. После staging-fix `aed8e63` этот сценарий повторно не запускался. Пример команды для PowerShell из корня репозитория:

```powershell
$repoPath = (Get-Location).Path
docker run --rm --network none --cpus 12 `
  --mount "type=bind,source=$repoPath\data,target=/app/data,readonly" `
  --mount "type=bind,source=$repoPath\out,target=/app/out" `
  jigas-graph-money:local --data /app/data --out /app/out/offline
```

## Что проверяет `test_contract.py`

Это один скрипт без отдельного тестового фреймворка. Он проверяет синтетический граничный граф и файлы конкретного выпуска:

- невалидный endpoint, неположительную сумму и нулевое число переводов;
- большой `gid` больше `2^53`, без округления в графе, отчёте и CSV;
- включение изолята и глубинной границы, встречные рёбра и цикл BFS;
- достижимость от разных seed, направленную нормированную невзвешенную betweenness и календарные даты;
- порядок конфликтующих ролей, исключение seed и узлов depth=4 из transit/terminal;
- формулы поддержки роли и приоритета, конечные score и числовые основания;
- обязательные столбцы и порядок, точные множества `gid`, rank, связи таблиц, кластеры и внутренние суммы;
- наличие HTML/validation JSON, остаточные маркеры шаблона и удалённые ссылки в HTML.

Локальная команда для уже созданного выпуска:

```powershell
python test_contract.py --data ./data --out ./out
```

### Проверка отказа на испорченных копиях

На отдельных копиях выпуска `aed8e63` были изменены по одному обязательному полю. `test_contract.py` завершился ненулевым кодом во всех четырёх случаях:

| Изменение в копии | Результат |
|---|---|
| Переименована обязательная колонка `evidence` | Rejected: `required columns/order mismatch` |
| Первый `gid` заменён на `0` | Rejected: `gid set differs from nodes.parquet` |
| Первый rank заменён на `99` | Rejected: `rank must be consecutive from 1` |
| Первая сумма кластера заменена на `0.00` | Rejected: `cluster_id=0: internal sum differs from directed edges` |

Проверки выполнялись на временных копиях CSV/HTML/manifest и не меняли исходный выпуск. Локальный интерпретатор был Python 3.11.4, поэтому этот mutation smoke не считается проверкой Docker/Python 3.13; основной Docker contract прошёл на каждом из пяти финальных запусков.

## Browser smoke и открытые ручные проверки

Предварительный fake-DOM smoke на официальном payload подтвердил exact string search для ID больше `2^53`, очистку карточки для неизвестного ID, boundary warning, отдельный изолят, directed edge и переход к полному составу кластера; CSV согласованы с JSON по 2 248 узлам, 89 кластерам и top-20. Smoke использует stub vis-network и не рисует настоящий canvas.

Отдельный headless Playwright smoke проверил финальный `out/final1/report.html` на `aed8e63` в system Chrome через `file://` при блокировке HTTP(S). Настоящий canvas vis-network отрисовался; проверены точный поиск и выбор top-узла, Enter на фокусируемой строке, копирование ID, режим всего графа (2 248 узлов), фильтр кластера, boundary warning, отдельный isolate (1 узел / 0 рёбер), очистка при неизвестном gid и три локальные CSV-ссылки. Ошибки страницы/консоли — 0; внешние запросы — 0; viewport 390 px без горизонтальной прокрутки. QA-скрипт и desktop/mobile screenshots находятся вне репозитория и не входят в сдачу.

Headless smoke подтверждает browser execution, canvas и перечисленные действия, но не заменяет текущий headed review пользователем. Приёмка полного UI останется открытой до его результата.

Пользовательский headed review должен проверить:

1. Визуально оценить desktop/mobile скриншоты и реальный граф: перекрытия узлов/подписей, читаемость таблиц и карточки.
2. Полный keyboard-only сценарий: поиск, переход по результатам, фильтры, кнопки и CSV-ссылки.
3. Ручные карточки depth=4, узла со встречными рёбрами и узла, скрытого текущим фильтром.
4. Пустое состояние, легенду, сообщения об ограничениях и консоль браузера.
5. Linux clean clone: запись `prepare` и pipeline в bind-mounted каталоги с host UID/GID.

По завершении headed review записать браузер/версию, SHA выпуска и фактические наблюдения.

## Ограничения проверки

Контрактные тесты проверяют соответствие документированным правилам и форматам. Они не измеряют AML accuracy, precision/recall или вероятность нарушения: размеченного ground truth нет. Результат не подтверждает масштабирование на миллион узлов. Контрольные размеры и оборот берутся из [SPEC §2](SPEC.md#2-вход-и-валидация); синтетический граф не заменяет реальный вход.
