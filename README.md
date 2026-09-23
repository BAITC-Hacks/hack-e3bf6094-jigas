# Jigas — граф переводов

Локальный аналитический прототип для AML-аналитика. Он собирает ориентированный граф переводов из трёх Parquet-файлов, рассчитывает объяснимые структурные гипотезы и выпускает CSV-таблицы вместе с автономным HTML-отчётом.

> **Статус проверенного P0-контура:** на коде `aed8e63` Docker выпустил все пять артефактов; `test_contract.py` прошёл на каждом. CSV побайтно совпали между прогонами; медиана — 4,327 с, максимум — 4,642 с. Headless system Chrome открыл `file://`-отчёт с блокировкой HTTP(S): canvas vis-network отрисовался, интерактивные сценарии прошли, ошибок страницы и внешних запросов нет. Пользовательский headed review ещё продолжается; Linux clean-clone Docker-путь пока не проверен.

## Требования

- Docker Engine и Docker Compose v2 на Linux либо Docker Desktop с Docker Compose на Windows/macOS — рекомендуемый способ запустить проект без установки Python на хосте.
- Для локального запуска без Docker — Python 3.13.
- Стек: Python 3.13, pandas, NumPy, PyArrow и NetworkX; batch CLI без БД и API-сервера; HTML/JavaScript с локальной копией vis-network 10.1.2. Версии Python-зависимостей закреплены в `requirements.txt`.
- Официальный архив входа находится в `track_data/data (1).zip` и уже включён в репозиторий. Подготовленные Parquet хранятся в игнорируемой `data/` и не попадают в Git.

## Запуск в Docker

Из корня репозитория соберите образ:

```powershell
docker compose build pipeline
```

На Windows/macOS затем выполните команды по порядку:

```powershell
docker compose run --rm prepare
docker compose run --rm pipeline
```

Сервис `prepare` извлекает три Parquet из архива в локальную папку `data/`; сервис `pipeline` читает её только для чтения и записывает выпуск в `out/`. Образы нужно собрать при наличии доступа к пакетам Python. После сборки само приложение не требует внешних API, ключей, GPU или передачи входных файлов наружу; фактический запуск с отключённой сетью будет зафиксирован в [протоколе проверки](docs/validation.md).

Проверить контракт на выпущенных файлах можно в контейнере:

```powershell
docker compose run --rm --entrypoint python pipeline test_contract.py --data /app/data --out /app/out
```

### Linux: bind-mounted каталоги

Образ запускает приложение от UID 10001. Чтобы `prepare` и pipeline могли записывать в локальные bind mounts на обычном Linux, создайте `data/` и `out/` от имени текущего пользователя и запускайте сервисы с его UID/GID:

```bash
mkdir -p data out
docker compose build pipeline
docker compose run --rm --user "$(id -u):$(id -g)" prepare
docker compose run --rm --user "$(id -u):$(id -g)" pipeline --data /app/data --out /app/out
docker compose run --rm --user "$(id -u):$(id -g)" --entrypoint python pipeline test_contract.py --data /app/data --out /app/out
```

Если каталоги уже существуют, у текущего пользователя должны быть права записи. Этот Linux clean-clone путь пока не проверялся на Linux-хосте.

## Локальный запуск Python

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/prepare_data.py
.\.venv\Scripts\python.exe starter.py --data .\data --out .\out
.\.venv\Scripts\python.exe test_contract.py --data .\data --out .\out
```

### Linux / macOS

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/prepare_data.py
.venv/bin/python starter.py --data ./data --out ./out
.venv/bin/python test_contract.py --data ./data --out ./out
```

Установка библиотек обращается к PyPI. Проверка приложения без сети относится к уже установленной среде и пока не заявляется как пройденная.

## Результаты запуска

Проверенный pipeline создаёт в `out/`:

- `nodes_roles.csv` — по одной строке на каждый узел, роль, поддержка гипотезы, кластер, приоритет и числовое объяснение;
- `clusters.csv` — размер и число seed в кластере, внутренний оборот в KZT, список ключевых узлов и осторожная гипотеза;
- `top_nodes.csv` — первые 20 узлов по неокруглённому приоритету с детерминированным порядком равных значений;
- `report.html` — автономный локальный отчёт с поиском, карточкой узла и графом;
- `validation.json` — версия среды, хеши входов, параметры и итоговые проверки выпуска.

CSV используют полные десятичные `gid`, суммы кластеров содержат две цифры после точки. В HTML идентификаторы передаются строками, чтобы браузер не округлял большие числа. `role_score` и `priority_score` — поддержка структурной гипотезы, а не вероятность преступления.

Откройте `out/report.html` двойным щелчком в браузере. В отчёте можно искать полный `gid`, изучать направленные связи и ограничения наблюдения; таблицы CSV можно открыть отдельно.

Отчёт встраивает локальные JS/CSS vis-network; ссылки на CDN не используются. Библиотека указана как `Apache-2.0 OR MIT`; файл лицензии лежит в [assets/vis-network.LICENSE.txt](assets/vis-network.LICENSE.txt). Если распространяете автономный HTML отдельно от репозитория, приложите этот файл лицензии.

## Как читать выводы

Граф ограничен исходящей выборкой до четырёх шагов от seed и июлем 2026 года. Нулевой исходящий поток на depth=4 означает границу наблюдения, а не доказанный конечный перевод. Кластер, достижимость от seed, сумма или роль сами по себе не показывают владельца денег и не устанавливают нарушение. Ground truth для оценки точности гипотез не предоставлен.

## Документы

- [Спецификация продукта и пользовательского сценария](docs/PRODUCT_SPEC.md)
- [Технический контракт](docs/SPEC.md)
- [План работ](docs/PLAN.md)
- [Архитектура и модули](docs/ARCHITECTURE.md)
- [Пятиминутное демо](docs/DEMO.md)
- [Протокол автоматической и ручной проверки](docs/validation.md)
- [Исследование и архитектурный blueprint](HackAlem_AI_Research_Architecture_Blueprint.md)
- [Альтернативный blueprint Влада](HackAlem_AI_Blueprint_v2_Vlad.md)
- [Сравнение, проверенные измерения и принятые идеи объединения](docs/BLUEPRINT_COMPARISON.md)

Доска Trello: https://trello.com/b/6tCEnwZQ/hackathon

Актуальный контракт — PRODUCT_SPEC 1.1 и SPEC 1.2, порядок работ — PLAN 1.1. Текущий проверенный выпуск содержит P0-функции; дневные профили, обзор SCC/межкластерных потоков и запросы недостающих данных из P1 в нём не заявлены. Их определения и приёмка зафиксированы в SPEC §10. Карточки Trello синхронизированы; ссылки и журнал изменений — PLAN §8–10. Архитектурная схема модулей и текущие результаты проверки приведены в документах выше.
