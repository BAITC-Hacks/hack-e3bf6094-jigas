# Jigas — граф денежных переводов

Локальный batch-инструмент для AML-аналитика. Он читает три Parquet-файла, строит ориентированный граф, рассчитывает структурные гипотезы и выпускает три CSV вместе с React-отчётом.

> **Статус:** React/Vite и localhost viewer проверены на официальном выпуске: 2 248 клиентов, 3 119 связей, 89 кластеров. Контрактный тест и просмотр на десктопе и телефоне через Playwright прошли; результаты batch-проверок и ограничения — в [протоколе](docs/validation.md).

## Быстрый запуск для жюри

Выполните из корня репозитория:

```powershell
docker compose build pipeline
docker compose run --rm prepare
docker compose run --rm pipeline
docker compose run --rm --entrypoint python pipeline test_contract.py --data /app/data --out /app/out
```

В отдельном терминале запустите `docker compose up viewer`, затем откройте `http://127.0.0.1:8000/report.html`. Точные результаты и проверенные SHA ведутся в [протоколе](docs/validation.md).

В отчёте начните с топ-20 или найдите клиента по полному ID. Карточка рядом показывает приоритет, роль, основание и предупреждения; на телефоне она идёт сразу после поиска. Вкладка «Все 2 248» открывает полный список с поиском по части ID и страницами по 50 строк. Ниже можно переключить граф между связями клиента и всем выпуском, открыть дополнительные фильтры и скачать CSV. Для быстрой проверки введите несуществующий ID: интерфейс должен сообщить, что клиент не найден, и очистить прежний выбор.

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

Сборка frontend и установка Python-пакетов требуют интернета. Готовый отчёт загружается только с localhost и не обращается к внешним сервисам.

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
