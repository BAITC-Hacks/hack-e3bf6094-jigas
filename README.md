# Jigas — граф денежных переводов

Локальный batch-инструмент для AML-аналитика. Он читает три Parquet-файла, строит ориентированный граф, рассчитывает структурные гипотезы и выпускает три CSV вместе с React-отчётом.

> **Статус:** React/Vite, `report.json` и localhost viewer проверены на официальных данных в Docker. Пять выпусков, контрактные тесты и локальный Playwright smoke прошли на ветке `feat/react-integration`; точные результаты и ограничения — в [протоколе](docs/validation.md).

## Быстрый запуск для жюри

Выполните из корня репозитория:

```powershell
docker compose build pipeline
docker compose run --rm prepare
docker compose run --rm pipeline
docker compose run --rm --entrypoint python pipeline test_contract.py --data /app/data --out /app/out
```

В отдельном терминале запустите `docker compose up viewer`, затем откройте `http://127.0.0.1:8000/report.html`. Точные результаты и проверенные SHA ведутся в [протоколе](docs/validation.md).

## Стек и требования

- Docker Engine и Docker Compose v2 — рекомендуемый путь; Docker Desktop подходит на Windows/macOS, Docker Engine + Compose — на Linux.
- Docker build собирает frontend на Node 22, затем включает его статику в Python 3.13 runtime. Python-слой использует pandas, NumPy, PyArrow и NetworkX; версии закреплены в `requirements.txt`.
- Frontend — React + Vite + vis-network. Python остаётся batch CLI и выпускает JSON/CSV; БД, backend API, GPU и Node на машине, где запускают готовый Docker-образ, не нужны.
- Для сборки frontend напрямую нужен Node, совместимый с `frontend/package-lock.json`; для native расчёта нужен Python 3.13.
- Официальный архив входа хранится в `track_data/data (1).zip`. Подготовленные Parquet размещаются в игнорируемой папке `data/` и не коммитятся.

## Запуск в Docker

Из корня репозитория соберите runtime image. Frontend собирается на Node-стадии:

```powershell
docker compose build pipeline
```

Подготовьте данные и запустите batch:

```powershell
docker compose run --rm prepare
docker compose run --rm pipeline
```

`prepare` распаковывает Parquet в `data/`; `pipeline` читает вход только для чтения и пишет выпуск в `out/`. Compose по умолчанию использует `HACKALEM_IMAGE=jigas-graph-money:local` и `HACKALEM_CPUS=12`; при необходимости значения можно переопределить переменными окружения. Сборка требует доступа к npm/PyPI. Уже собранный runtime не обращается к внешним сервисам.

Проверить конкретный выпуск можно в том же образе:

```powershell
docker compose run --rm --entrypoint python pipeline test_contract.py --data /app/data --out /app/out
```

Для просмотра оставьте viewer запущенным в отдельном терминале:

```powershell
docker compose up viewer
```

Затем откройте `http://127.0.0.1:8000/report.html`. Viewer раздаёт `out/` только для чтения и привязан к localhost. Откройте отчёт через HTTP: React загружает соседний `report.json`; режим `file://` для этого контракта не поддерживается.

### Linux: права на bind mounts

Создайте каталоги от своего пользователя и запускайте запись с его UID/GID:

```bash
mkdir -p data out
docker compose build pipeline
docker compose run --rm --user "$(id -u):$(id -g)" prepare
docker compose run --rm --user "$(id -u):$(id -g)" pipeline --data /app/data --out /app/out
docker compose run --rm --user "$(id -u):$(id -g)" --entrypoint python pipeline test_contract.py --data /app/data --out /app/out
docker compose up viewer
```

Откройте `http://127.0.0.1:8000/report.html` в браузере на той же машине. Linux clean-clone маршрут пока не проверен на Linux-хосте; результат не следует считать подтверждённым до такого прогона.

## Native запуск

Сначала соберите frontend один раз. Для этой команды нужна установленная npm-версия, совместимая с lockfile:

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
```

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/prepare_data.py
.\.venv\Scripts\python.exe starter.py --data .\data --out .\out
.\.venv\Scripts\python.exe test_contract.py --data .\data --out .\out
```

В отдельном PowerShell-окне запустите viewer:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1 --directory .\out
```

Откройте `http://127.0.0.1:8000/report.html`.

### Linux / macOS

```bash
npm --prefix frontend ci
npm --prefix frontend run build
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/prepare_data.py
.venv/bin/python starter.py --data ./data --out ./out
.venv/bin/python test_contract.py --data ./data --out ./out
```

В отдельном терминале запустите `.venv/bin/python -m http.server 8000 --bind 127.0.0.1 --directory ./out`, затем откройте `http://127.0.0.1:8000/report.html`.

Сборка frontend и установка Python-пакетов требуют интернета. Приложение после установки должно работать с отключённой сетью; localhost browser smoke нового отчёта ещё ожидает отдельного подтверждения в [протоколе проверки](docs/validation.md).

## Файлы выпуска

Pipeline публикует единый согласованный набор в `out/`:

- `nodes_roles.csv` — основная роль, поддержка гипотезы, кластер, приоритет и короткое фактическое объяснение;
- `clusters.csv` — размеры групп, seed, внутренние суммы, ключевые узлы и осторожные гипотезы;
- `top_nodes.csv` — до 20 узлов по числовому приоритету и детерминированному tie-break;
- `report.json` — полный набор узлов, рёбер, кластеров, параметров и доступных дополнительных признаков;
- `report.html` и `assets/` — заранее собранный локальный React UI;
- `validation.json` — статус выпуска, параметры и SHA-256 сформированных файлов.

Строковые ID в JSON сохраняют полную десятичную запись и не округляются браузером. Отчёт загружает `./report.json` с того же localhost и не пересчитывает роли. `test_contract.py` сверяет JSON с CSV/raw входом и проверяет целостность локальных ресурсов. `validation.json` со статусом success появляется только после проверки полного набора.

## Как интерпретировать результат

Наблюдение ограничено исходящей выборкой до четырёх шагов от seed, июлем 2026 года и входным порогом операций. Нулевая исходящая активность на глубине 4 — граница выборки, а не доказательство конечного перевода. Seed также могут иметь неполный входящий профиль. Роль, приоритет, сумма, достижимость или сообщество сами по себе не определяют владельца средств и не устанавливают нарушение. Ground truth для оценки точности гипотез не предоставлен; AML accuracy/F1 не заявляются.

Дополнительные P1-поля могут отсутствовать в P0 отчёте. Отсутствующий блок означает «не рассчитано» и не должен отображаться как ноль. Для старых report-объектов без `matched_roles[].reason` интерфейс показывает доступное evidence без выдуманной детализации.

## Критерии ролей и приоритет

Роли — baseline-эвристики по наблюдаемому графу, а не калиброванные вероятности. Все совпадения сохраняются; основная роль выбирается по порядку `coordinator → distributor → consolidator → transit → terminal → peripheral`. `S` — число различных seed, достигающих узла за 1–4 направленных шага; `B` — betweenness; `r=out_tiyn/in_tiyn`; `D` — дни после последнего наблюдаемого входящего перевода.

| Роль | Критерий | `u` | `cap` |
|---|---|---|---:|
| coordinator | `S≥2`, `in_deg≥2`, `out_deg≥2`, `B>0`, `B≥Q90(B>0)` | `min(1,S/4)` | 0.60 |
| distributor | `out_deg≥10` | `min(1,out_deg/20)` | 0.90 |
| consolidator | `in_deg≥3` | `min(1,in_deg/6)` | 0.90 |
| transit | Не seed, `depth<4`, вход и выход положительны, `0.8≤r≤1.2` | `max(0,1−abs(r−1)/0.2)` | 0.65 |
| terminal | Не seed, `depth<4`, вход положителен, `out_deg=0`, `D≥2` | `min(1,D/7)` | 0.50 |
| peripheral | Ни одно правило выше не совпало | — | — |

Для совпавшей роли `support=cap×(0.5+0.5×u)`; поддержка основной роли становится `role_score`, у `peripheral` она равна 0. Порог `Q90(B>0)` считается по положительным B всего графа линейным методом; если таких значений нет, `coordinator` не назначается. Граница `depth=4` не считается terminal только из-за отсутствия наблюдаемых исходящих рёбер. Полные определения — в [SPEC 1.4](docs/SPEC.md).

Приоритет `P` лежит в диапазоне `[0,1]` и равен `0.35×M + 0.30×A + 0.20×C + 0.15×H`:

| Компонент | Расчёт |
|---|---|
| `M` | Максимальная поддержка совпавших ролей, иначе 0 |
| `V_kzt` | `(in_tiyn+out_tiyn)/100` — наблюдаемый объём в KZT |
| `A` | `min(1, log1p(V_kzt)/log1p(Q95(V_kzt>0)))` |
| `C` | `min(1,S/5)` |
| `H` | `min(1,B/Q95(B>0))` |

Квантили считаются по полному графу линейным методом; если положительных значений нет или знаменатель равен нулю, соответствующий компонент равен 0. Узлы сортируются по неокруглённому `P` по убыванию, затем по числовому `gid` по возрастанию. Пороги и веса приведены в [SPEC 1.4](docs/SPEC.md).

## Если граф вырастет примерно до 1 млн узлов

Это план масштабирования, а не подтверждённая производительность текущего пайплайна: точная betweenness на NetworkX и передача полного графа в HTML при таком размере не проверялись.

- Читать только нужные столбцы и агрегировать данные порциями/по периодам; отдельно измерить память, учитывая число рёбер. Сравнить колонночные инструменты вроде Arrow, DuckDB или Polars перед выбором.
- Профилировать объектный NetworkX-граф и при нехватке памяти перейти на компактные массивы/CSR или подходящий графовый backend. GPU не является обязательным условием.
- Степени, суммы и количества оставить линейными агрегациями. Для betweenness рассмотреть выборку источников или кандидатные подграфы и явно маркировать результат как приближённый; пересчитать и сверить приоритеты на контрольной выборке.
- Ограничивать seed-обход глубиной и числом источников; если меняется определение `S`, повторно проверить связанные пороги и приоритет.
- Кластеры считать пакетно и проверить устойчивость Louvain/Leiden с учётом границ периода и выгрузки. В браузер передавать выбранный кластер/окрестность и агрегированный обзор, а не миллион узлов в один HTML/canvas.

Конкретный план описан в [архитектурном blueprint](HackAlem_AI_Research_Architecture_Blueprint.md); он не означает, что эти оптимизации уже реализованы или измерены.

## Документы

- [Спецификация продукта — PRODUCT_SPEC 1.3](docs/PRODUCT_SPEC.md)
- [Технический контракт — SPEC 1.4](docs/SPEC.md)
- [План реализации — PLAN 1.3](docs/PLAN.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Пятиминутное демо](docs/DEMO.md)
- [Протокол проверки](docs/validation.md)
- [Исследование второй волны](docs/SECOND_WAVE_RESEARCH.md)
- [Архитектурный blueprint](HackAlem_AI_Research_Architecture_Blueprint.md)
- [Сравнение blueprint-ов](docs/BLUEPRINT_COMPARISON.md)

Trello: https://trello.com/b/6tCEnwZQ/hackathon
