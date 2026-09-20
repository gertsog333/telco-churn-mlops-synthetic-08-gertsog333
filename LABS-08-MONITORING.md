# Module 08 — Monitoring & Observability

Репозиторий создан на базе шаблона `mentorchita/telco-churn-mlops-synthetic-08`.
Проект развёрнут, модель обучена, стек мониторинга поднят через Docker Compose.

## Окружение

| Компонент | Версия / параметры |
|---|---|
| VM | Ubuntu 22.04, 4 vCPU, 8 GB RAM, 61 GB диск |
| Docker | 29.7.2, Compose v5.5.0 |
| Python (venv) | 3.10, scikit-learn 1.5.2, pandas 2.2.3 |
| FastAPI API | `churn-api`, порт 8000 |
| MLflow | v2.11.3, порт 5000 |
| Prometheus | v2.48.0, порт 9090 |
| Grafana | 10.2.0, порт 3000 (admin / mlops_pass) |
| Loki / Promtail | 2.9.2, порт 3100 |
| Alertmanager | v0.26.0, порт 9093 |
| node-exporter / cAdvisor / Pushgateway | 9100 / 8080 / 9091 |

## 1. Подготовка данных и обучение модели

```bash
python3 -m venv venv && source venv/bin/activate
pip install scikit-learn==1.5.2 pandas==2.2.3 numpy joblib tqdm faker

python src/generate_dataset.py --samples 20000 --output data/telco_customers.csv
python pipelines/train.py
```

Результат: `RandomForestClassifier` в sklearn-Pipeline, **accuracy 0.6420**,
модель сохранена в `models/churn_model.pkl`.

Проверка соответствия схемы API и модели:

```bash
python -c "from src.api.predict import predict_churn; print(predict_churn({...19 полей...}))"
# {'churn_probability': 0.95, 'churn_prediction': 1, 'features_used': [19 признаков]}
```

## 2. Развёртывание стека

```bash
docker compose up -d --build api mlflow prometheus grafana loki promtail \
                            alertmanager node-exporter cadvisor pushgateway
```

Все 10 контейнеров в состоянии `Up`, healthcheck'и проходят.

## 3. Проверка компонентов

**Prometheus targets — 8 из 8 UP:**

```
UP  alertmanager    http://alertmanager:9093/metrics
UP  cadvisor        http://cadvisor:8080/metrics
UP  churn-api       http://churn-api:8000/metrics
UP  grafana         http://grafana:3000/metrics
UP  mlflow          http://mlflow.int:5000/metrics
UP  node-exporter   http://node-exporter:9100/metrics
UP  prometheus      http://localhost:9090/metrics
UP  pushgateway     http://pushgateway:9091/metrics
```

**Правила:** 12 групп, 36 правил (`ml_alerts.yml`, `api_alerts.yml`,
`infra_alerts.yml`, `recording_rules.yml`).

**Grafana:** три датасорса (Prometheus, Loki, Alertmanager) и два дашборда
(`ML Model Health — Telco Churn`, `API Performance — Telco Churn`) поднимаются
через provisioning автоматически.

**Loki:** логи `churn-api` собираются Promtail'ом, лейблы `container`, `env`,
`job`, `service`, `stream` доступны для LogQL.

## 4. Нагрузка и метрики

```bash
python scripts/load_test.py --requests 3000 --concurrency 4
python scripts/load_test.py --requests 200  --error-rate 0.25
```

| Показатель | Значение |
|---|---|
| Предсказаний обработано | 3328 (200 OK) + 55 (422) |
| Распределение | churn 75 % / no_churn 25 % |
| p99 latency инференса | 158 ms |
| p99 latency HTTP `/predict` | 226 ms |
| Throughput | ~10 req/s на `/predict` |

## 5. Метрики качества модели через Pushgateway

Добавлен скрипт `monitoring/push_model_metrics.py`: оценивает сохранённую модель
на hold-out выборке и публикует `model_accuracy`, `model_f1_score`, `model_auc_roc`
в Pushgateway (паттерн из презентации — метрики качества публикует пайплайн
обучения, а не сервис инференса).

```
pushed: accuracy=0.6420  f1=0.6299  auc=...
```

## 6. Детекция дрейфа

Датасет сгенерирован с встроенным дрейфом между 2023 и 2024 годом, поэтому
reference/live разделены по году записи:

```bash
# data/reference/ — 9 971 запись за 2023
# data/live/      — 10 029 записей за 2024

python monitoring/drift_monitor.py \
  --reference data/reference/ --live data/live/ \
  --pushgateway http://localhost:9091 --model-version v1.0-local
```

| Признак | Тест | Значение | Уровень |
|---|---|---|---|
| MonthlyCharges | KS | 0.2244 | moderate |
| TotalCharges | KS | 0.1964 | minor |
| tenure | KS | 0.1479 | minor |
| Contract | PSI | 0.0563 | stable |
| InternetService | PSI | 0.0567 | stable |
| PaymentMethod | PSI | 0.0688 | stable |
| gender / Partner / Dependents | PSI | ≤ 0.0228 | stable |

## 7. Композитный Model Health Score

```
job:model_health:composite_score = 0.767
= 0.4 × 0.642 (accuracy) + 0.4 × (1 − 0.224) (drift) + 0.2 × 1 (uptime)
```

## Исправления относительно шаблона

1. **`pipelines/train.py`** — в признаки попадали `customerID` и `RecordDate`,
   из-за чего обученная модель требовала эти колонки и падала на запросах от API
   (в котором их нет). Добавлено удаление идентификатора и даты перед обучением.
2. **`mlflow_db/mlflow.db`** — закоммиченная БД создана более новой версией MLflow,
   чем образ v2.11.3: контейнер падал с `alembic ... Can't locate revision '1b5f0d9ad7c1'`.
   Несовместимый файл убран, MLflow создаёт схему заново.
3. **Healthcheck'и в `docker-compose.yml`** — вызывали `curl`, которого нет в
   `python:3.11-slim`; сервисы висели в статусе `unhealthy`. Заменены на
   `python -c "import urllib.request; ..."`.
4. **Дашборды Grafana** — экспортированы с плейсхолдерами `${DS_PROMETHEUS}` /
   `${DS_LOKI}`, которые provisioning не раскрывает: все панели показывали
   `Datasource ${DS_PROMETHEUS} was not found`. Плейсхолдеры заменены на реальные
   uid (`prometheus_telco`, `loki_telco`), блоки `__inputs` / `__requires` удалены.
5. **`src/api/main.py`** — HTTP-метрики `api_requests_total`, `request_duration_seconds`
   и `active_http_connections`, на которых построен дашборд API Performance, нигде
   не заполнялись (middleware лежал в нерабочем `src/app_with_metrics.py`, который
   к тому же импортирует несуществующую функцию `validate_feature` из `src/metrics.py`).
   Добавлен рабочий `MetricsMiddleware`.
6. **`monitoring/prometheus/rules/recording_rules.yml`** — правило
   `job:model_health:composite_score` складывало `model_accuracy` (с лейблами
   `model_version`, `dataset_split`, `job`) с безлейбловыми агрегатами, из-за чего
   PromQL возвращал пустой вектор. Первый член обёрнут в `max()`, интервал расчёта
   снижен с 5m до 1m.
7. **`docker-compose.monitoring.yml`** — дублирует сервисы, уже описанные в
   `docker-compose.yml`, и объявляет сеть как `external`. Запуск обоих файлов через
   `-f ... -f ...` (как советуют Makefile и презентация) приводит к конфликту, поэтому
   стек поднимается одним `docker-compose.yml`.

## Панели, оставшиеся без данных — и почему

| Панель | Причина |
|---|---|
| Error Rate % / 5xx Error Rate | Ошибки нагрузочного теста — 422 (pydantic отклоняет запрос до обработчика), счётчик `predictions_total{outcome="error"}` не растёт; 5xx не возникало |
| Null Feature Counts | Генератор нагрузки не отправляет null-поля |
| Recent Errors & Warnings | В логах нет записей уровня ERROR |

## Очистка

```bash
docker compose down
```
