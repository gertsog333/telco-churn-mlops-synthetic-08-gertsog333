"""Оценивает сохранённую модель на hold-out выборке и публикует метрики качества
в Prometheus Pushgateway (паттерн Module 8: обучение пушит метрики, API их не знает)."""
import os
import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

DATA    = os.getenv("DATA_PATH", "data/telco_customers.csv")
MODEL   = os.getenv("MODEL_PATH", "models/churn_model.pkl")
PUSHGW  = os.getenv("PUSHGATEWAY_URL", "http://localhost:9091")
VERSION = os.getenv("MODEL_VERSION", "v1.0-local")

df = pd.read_csv(DATA)
X = df.drop("Churn", axis=1)
X = X.drop(columns=[c for c in ("customerID", "RecordDate", "Year") if c in X.columns])
y = df["Churn"].map({"Yes": 1, "No": 0})
_, X_test, _, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

model = joblib.load(MODEL)
pred  = model.predict(X_test)
proba = model.predict_proba(X_test)[:, 1]

acc = accuracy_score(y_test, pred)
f1  = f1_score(y_test, pred, average="macro")
auc = roc_auc_score(y_test, proba)

reg = CollectorRegistry()
Gauge("model_accuracy", "Current model accuracy on evaluation dataset",
      ["model_version", "dataset_split"], registry=reg).labels(VERSION, "test").set(acc)
Gauge("model_f1_score", "Model F1 score (macro average)",
      ["model_version"], registry=reg).labels(VERSION).set(f1)
Gauge("model_auc_roc", "Model AUC-ROC score",
      ["model_version"], registry=reg).labels(VERSION).set(auc)

push_to_gateway(PUSHGW, job="model-evaluation", registry=reg)
print(f"pushed: accuracy={acc:.4f}  f1={f1:.4f}  auc={auc:.4f}")
