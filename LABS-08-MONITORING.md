# Module 08 — Monitoring & Observability

Репозиторій створено на базі шаблону `mentorchita/telco-churn-mlops-synthetic-08`.
Проєкт розгорнуто, модель натреновано, стек моніторингу піднято через Docker Compose.

## Середовище

| Компонент | Версія / параметри |
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

## 1. Підготовка даних і тренування моделі

```bash
python3 -m venv venv && source venv/bin/activate
pip install scikit-learn==1.5.2 pandas==2.2.3 numpy joblib tqdm faker

python src/generate_dataset.py --samples 20000 --output data/telco_customers.csv
python pipelines/train.py
```

Результат: `RandomForestClassifier` у sklearn-Pipeline, **accuracy 0.6420**,
модель збережено у `models/churn_model.pkl`.

Перевірка відповідності схеми API та моделі:

```bash
python -c "from src.api.predict import predict_churn; print(predict_churn({...19 полів...}))"
# {'churn_probability': 0.95, 'churn_prediction': 1, 'features_used': [19 ознак]}
```

## 2. Розгортання стека

```bash
docker compose up -d --build api mlflow prometheus grafana loki promtail \
                            alertmanager node-exporter cadvisor pushgateway
```

Усі 10 контейнерів у стані `Up`, healthcheck'и проходять.

## 3. Перевірка компонентів

**Prometheus targets — 8 з 8 UP:**

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

**Правила:** 12 груп, 36 правил (`ml_alerts.yml`, `api_alerts.yml`,
`infra_alerts.yml`, `recording_rules.yml`).

**Grafana:** три джерела даних (Prometheus, Loki, Alertmanager) та два дашборди
(`ML Model Health — Telco Churn`, `API Performance — Telco Churn`) підіймаються
через provisioning автоматично.

**Loki:** логи `churn-api` збираються Promtail'ом, мітки `container`, `env`,
`job`, `service`, `stream` доступні для LogQL.

## 4. Навантаження та метрики

```bash
python scripts/load_test.py --requests 3000 --concurrency 4
python scripts/load_test.py --requests 200  --error-rate 0.25
```

| Показник | Значення |
|---|---|
| Оброблено передбачень | 3328 (200 OK) + 55 (422) |
| Розподіл | churn 75 % / no_churn 25 % |
| p99 latency інференсу | 158 ms |
| p99 latency HTTP `/predict` | 226 ms |
| Throughput | ~10 req/s на `/predict` |

## 5. Метрики якості моделі через Pushgateway

Додано скрипт `monitoring/push_model_metrics.py`: оцінює збережену модель
на hold-out вибірці та публікує `model_accuracy`, `model_f1_score`, `model_auc_roc`
у Pushgateway (патерн із презентації — метрики якості публікує пайплайн
тренування, а не сервіс інференсу).

```
pushed: accuracy=0.6420  f1=0.6299
```

## 6. Детекція дрейфу

Датасет згенеровано з вбудованим дрейфом між 2023 та 2024 роком, тому
reference/live розділено за роком запису:

```bash
# data/reference/ — 9 971 запис за 2023
# data/live/      — 10 029 записів за 2024

python monitoring/drift_monitor.py \
  --reference data/reference/ --live data/live/ \
  --pushgateway http://localhost:9091 --model-version v1.0-local
```

| Ознака | Тест | Значення | Рівень |
|---|---|---|---|
| MonthlyCharges | KS | 0.2244 | moderate |
| TotalCharges | KS | 0.1964 | minor |
| tenure | KS | 0.1479 | minor |
| Contract | PSI | 0.0563 | stable |
| InternetService | PSI | 0.0567 | stable |
| PaymentMethod | PSI | 0.0688 | stable |
| gender / Partner / Dependents | PSI | до 0.0228 | stable |

## 7. Композитний Model Health Score

```
job:model_health:composite_score = 0.767
= 0.4 x 0.642 (accuracy) + 0.4 x (1 - 0.224) (drift) + 0.2 x 1 (uptime)
```

## Виправлення відносно шаблону

1. **`pipelines/train.py`** — до ознак потрапляли `customerID` і `RecordDate`,
   через що натренована модель вимагала ці колонки й падала на запитах від API
   (у якому їх немає). Додано видалення ідентифікатора та дати перед тренуванням.
2. **`mlflow_db/mlflow.db`** — закомічена БД створена новішою версією MLflow,
   ніж образ v2.11.3: контейнер падав із `alembic ... Can't locate revision '1b5f0d9ad7c1'`.
   Несумісний файл прибрано, MLflow створює схему заново.
3. **Healthcheck'и у `docker-compose.yml`** — викликали `curl`, якого немає в
   `python:3.11-slim`; сервіси висіли у статусі `unhealthy`. Замінено на
   `python -c "import urllib.request; ..."`.
4. **Дашборди Grafana** — експортовані з плейсхолдерами `${DS_PROMETHEUS}` /
   `${DS_LOKI}`, які provisioning не розкриває: усі панелі показували
   `Datasource ${DS_PROMETHEUS} was not found`. Плейсхолдери замінено на реальні
   uid (`prometheus_telco`, `loki_telco`), блоки `__inputs` / `__requires` видалено.
5. **`src/api/main.py`** — HTTP-метрики `api_requests_total`, `request_duration_seconds`
   та `active_http_connections`, на яких побудовано дашборд API Performance, ніде
   не заповнювалися (middleware лежав у неробочому `src/app_with_metrics.py`, який
   до того ж імпортує неіснуючу функцію `validate_feature` із `src/metrics.py`).
   Додано робочий `MetricsMiddleware`.
6. **`monitoring/prometheus/rules/recording_rules.yml`** — правило
   `job:model_health:composite_score` додавало `model_accuracy` (з мітками
   `model_version`, `dataset_split`, `job`) до агрегатів без міток, через що
   PromQL повертав порожній вектор. Перший доданок загорнуто у `max()`, інтервал
   обчислення знижено з 5m до 1m.
7. **`docker-compose.monitoring.yml`** — дублює сервіси, вже описані у
   `docker-compose.yml`, і оголошує мережу як `external`. Запуск обох файлів через
   `-f ... -f ...` (як радять Makefile і презентація) призводить до конфлікту, тому
   стек піднімається одним `docker-compose.yml`.

## Панелі, що лишилися без даних — і чому

| Панель | Причина |
|---|---|
| Error Rate % / 5xx Error Rate | Помилки навантажувального тесту — 422 (pydantic відхиляє запит до обробника), лічильник `predictions_total{outcome="error"}` не зростає; 5xx не виникало |
| Null Feature Counts | Генератор навантаження не надсилає null-поля |
| Recent Errors & Warnings | У логах немає записів рівня ERROR |

## Очищення

```bash
docker compose down
```
