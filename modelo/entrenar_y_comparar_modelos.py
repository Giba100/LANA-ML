"""Entrenamiento reproducible y comparación de modelos para la tesis.

Este script:
1) carga el dataset histórico oficial;
2) aplica el mismo conjunto de variables a cuatro clasificadores;
3) evalúa un holdout estratificado 80/20 (random_state=42);
4) realiza una validación temporal (2024-2025 -> 2026);
5) guarda métricas y matrices de confusión en ``modelo/reportes``;
6) selecciona el modelo final priorizando F1-macro temporal y holdout;
7) opcionalmente reemplaza el artefacto final usado por el prototipo.

Uso:
    python modelo/entrenar_y_comparar_modelos.py
    python modelo/entrenar_y_comparar_modelos.py --escribir-final

La selección usa un criterio explícito y reproducible:
    40% F1-macro holdout + 60% F1-macro temporal.
La validación temporal recibe mayor peso porque se acerca más al uso real del
prototipo: aprender de periodos pasados y evaluar un periodo posterior.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "dataset_historico_final_8000.csv"
REPORT_DIR = ROOT / "modelo" / "reportes"
ARTIFACT_DIR = ROOT / "modelo" / "artefactos"
FINAL_MODEL_PATH = ARTIFACT_DIR / "modelo_tendencias_lana_final.pkl"
FINAL_METADATA_PATH = ARTIFACT_DIR / "metadata_modelo_final.json"

TARGET = "demanda_clase"
FEATURE_COLUMNS = [
    "ano", "mes", "ciudad", "zona_comercial", "tipo_prenda",
    "cliente_principal", "tipo_cliente", "color", "estilo", "motivo_comercial",
]
CATEGORICAL_COLUMNS = [
    "ciudad", "zona_comercial", "tipo_prenda", "cliente_principal",
    "tipo_cliente", "color", "estilo", "motivo_comercial",
]
NUMERIC_COLUMNS = ["ano", "mes"]
CLASS_ORDER = ["Alta", "Media", "Baja"]
RANDOM_STATE = 42


def construir_preprocesador(escalar_numericas: bool = False) -> ColumnTransformer:
    num_transformer = StandardScaler() if escalar_numericas else "passthrough"
    return ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_COLUMNS),#transformacion en variables binarias
            ("num", num_transformer, NUMERIC_COLUMNS),
        ]
    )


def construir_modelos() -> Dict[str, Tuple[object, bool]]:
    """Devuelve modelos y si requieren escalado de variables numéricas."""
    return {
        "Regresion_Logistica": (
            LogisticRegression(
                max_iter=3000,
                class_weight="balanced",
                random_state=RANDOM_STATE,
            ),
            True,
        ),
        "SVM": (
            SVC(
                kernel="rbf",
                C=1.0,
                gamma="scale",
                class_weight="balanced",
                probability=True,  # requerido por el prototipo (predict_proba)
                random_state=RANDOM_STATE,
            ),
            True,
        ),
        "Arbol_Decision": (
            DecisionTreeClassifier(
                class_weight="balanced",
                min_samples_leaf=2,
                random_state=RANDOM_STATE,
            ),
            False,
        ),
        "Random_Forest": (
            RandomForestClassifier(
                n_estimators=250,
                min_samples_leaf=2,
                class_weight="balanced_subsample",
                max_features="sqrt",
                random_state=RANDOM_STATE,
                n_jobs=-1,
            ),
            False,
        ),
    }


def construir_pipeline(modelo, escalar_numericas: bool) -> Pipeline:   #objeto Pipeline de Scikit-Learn
    return Pipeline(
        steps=[
            ("preprocessor", construir_preprocesador(escalar_numericas)),
            ("classifier", modelo),
        ]
    )


def calcular_metricas(y_true, y_pred) -> dict:
    p_macro, r_macro, f_macro, _ = precision_recall_fscore_support(
        y_true, y_pred, average="macro", zero_division=0
    )
    p_w, r_w, f_w, _ = precision_recall_fscore_support(
        y_true, y_pred, average="weighted", zero_division=0
    )
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_macro": float(p_macro),
        "recall_macro": float(r_macro),
        "f1_macro": float(f_macro),
        "precision_weighted": float(p_w),
        "recall_weighted": float(r_w),
        "f1_weighted": float(f_w),
    }


def guardar_matriz(y_true, y_pred, ruta: Path) -> None:
    matriz = confusion_matrix(y_true, y_pred, labels=CLASS_ORDER)
    pd.DataFrame(matriz, index=CLASS_ORDER, columns=CLASS_ORDER).to_csv(
        ruta, encoding="utf-8-sig"
    )


def evaluar() -> tuple[pd.DataFrame, dict, dict, pd.DataFrame]:
    df = pd.read_csv(DATASET_PATH)
    faltantes = [c for c in FEATURE_COLUMNS + [TARGET] if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas en el dataset: {faltantes}")

    X = df[FEATURE_COLUMNS]
    y = df[TARGET]
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.20,
        random_state=RANDOM_STATE,
        stratify=y,
    )

    max_ano = int(df["ano"].max())
    temporal_train = df[df["ano"].astype(int) < max_ano].copy()
    temporal_test = df[df["ano"].astype(int) == max_ano].copy()
    if temporal_train.empty or temporal_test.empty:
        raise ValueError("No existe información suficiente para validación temporal.")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    filas = []
    pipelines_holdout = {}
    pipelines_temporales = {}

    for nombre, (modelo, escala) in construir_modelos().items():
        # Holdout 80/20.
        pipe = construir_pipeline(modelo, escala)
        pipe.fit(X_train, y_train)      #entrenamiento del dataset
        pred_train = pipe.predict(X_train)
        pred_holdout = pipe.predict(X_test)      
        m_train = calcular_metricas(y_train, pred_train)
        m_hold = calcular_metricas(y_test, pred_holdout)
        pipelines_holdout[nombre] = pipe
        guardar_matriz(
            y_test,
            pred_holdout,
            REPORT_DIR / f"matriz_confusion_holdout_{nombre}.csv",
        )

        # Validación temporal: todos los años anteriores -> último año disponible.
        pipe_temp = construir_pipeline(modelo, escala)
        pipe_temp.fit(temporal_train[FEATURE_COLUMNS], temporal_train[TARGET])
        pred_temp = pipe_temp.predict(temporal_test[FEATURE_COLUMNS])     #entrenamiento de vuelta 
        m_temp = calcular_metricas(temporal_test[TARGET], pred_temp)      #en base al ultimo año
        pipelines_temporales[nombre] = pipe_temp
        guardar_matriz(
            temporal_test[TARGET],
            pred_temp,
            REPORT_DIR / f"matriz_confusion_temporal_{nombre}.csv",
        )

        score_seleccion = 0.40 * m_hold["f1_macro"] + 0.60 * m_temp["f1_macro"]
        filas.append(
            {
                "modelo": nombre,
                "train_accuracy": m_train["accuracy"],
                "train_f1_macro": m_train["f1_macro"],
                "holdout_accuracy": m_hold["accuracy"],
                "holdout_precision_macro": m_hold["precision_macro"],
                "holdout_recall_macro": m_hold["recall_macro"],
                "holdout_f1_macro": m_hold["f1_macro"],
                "holdout_precision_weighted": m_hold["precision_weighted"],
                "holdout_recall_weighted": m_hold["recall_weighted"],
                "holdout_f1_weighted": m_hold["f1_weighted"],
                "temporal_accuracy": m_temp["accuracy"],
                "temporal_precision_macro": m_temp["precision_macro"],
                "temporal_recall_macro": m_temp["recall_macro"],
                "temporal_f1_macro": m_temp["f1_macro"],
                "temporal_precision_weighted": m_temp["precision_weighted"],
                "temporal_recall_weighted": m_temp["recall_weighted"],
                "temporal_f1_weighted": m_temp["f1_weighted"],
                "brecha_f1_train_holdout": m_train["f1_macro"] - m_hold["f1_macro"],
                "score_seleccion": score_seleccion,
            }
        )

    resultados = pd.DataFrame(filas).sort_values("score_seleccion", ascending=False)
    resultados.to_csv(
        REPORT_DIR / "comparacion_modelos_auditada.csv",
        index=False,
        encoding="utf-8-sig",
    )

    ganador = str(resultados.iloc[0]["modelo"])
    resumen = {
        "modelo_seleccionado": ganador,
        "criterio_seleccion": "0.40*F1_macro_holdout + 0.60*F1_macro_temporal",
        "ano_validacion_temporal": max_ano,
        "periodo_entrenamiento_temporal": f"{int(temporal_train['ano'].min())}-{int(temporal_train['ano'].max())}",
        "registros_total": int(len(df)),
        "registros_holdout_prueba": int(len(X_test)),
        "registros_temporal_prueba": int(len(temporal_test)),
    }
    (REPORT_DIR / "resumen_seleccion_modelo.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return resultados, pipelines_holdout, pipelines_temporales, df


def escribir_modelo_final(resultados, pipelines_holdout, df) -> None:
    ganador = str(resultados.iloc[0]["modelo"])
    # Reentrenar el ganador con todo el dataset para uso del prototipo.
    modelo, escala = construir_modelos()[ganador]
    pipeline_final = construir_pipeline(modelo, escala)
    pipeline_final.fit(df[FEATURE_COLUMNS], df[TARGET])   #Reentrenamiento del modelo ganador con el 100% de datos

    fila = resultados[resultados["modelo"].eq(ganador)].iloc[0]
    metadata_paquete = {"feature_columns": FEATURE_COLUMNS}
    joblib.dump(
        {"pipeline": pipeline_final, "metadata": metadata_paquete},
        FINAL_MODEL_PATH,
        compress=3,
    )

    metadata = {
        "modelo_seleccionado": ganador,
        "target": TARGET,
        "feature_columns": FEATURE_COLUMNS,
        "columnas_dataset": list(df.columns),
        "total_registros": int(len(df)),
        "total_prendas": int(df["tipo_prenda"].nunique()),
        "estilos": sorted(df["estilo"].dropna().astype(str).unique().tolist()),
        "periodo": (
            f"{int(df['ano'].min())}-{int(df[df['ano'].eq(df['ano'].min())]['mes'].min()):02d} "
            f"a {int(df['ano'].max())}-{int(df[df['ano'].eq(df['ano'].max())]['mes'].max()):02d}"
        ),
        "criterio_seleccion": "40% F1-macro holdout + 60% F1-macro validación temporal",
        "metodologia_accuracy_principal": "Holdout estratificado 80/20, random_state=42",
        "metodologia_validacion_temporal": "Entrenamiento con años anteriores y prueba con el último año disponible",
        "metricas_holdout": [{
            "modelo": ganador,
            "accuracy": round(float(fila["holdout_accuracy"]), 4),
            "precision": round(float(fila["holdout_precision_weighted"]), 4),
            "recall": round(float(fila["holdout_recall_weighted"]), 4),
            "f1_score": round(float(fila["holdout_f1_weighted"]), 4),
            "precision_macro": round(float(fila["holdout_precision_macro"]), 4),
            "recall_macro": round(float(fila["holdout_recall_macro"]), 4),
            "f1_macro": round(float(fila["holdout_f1_macro"]), 4),
        }],
        "metricas_validacion_temporal": {
            "modelo": ganador,
            "accuracy": round(float(fila["temporal_accuracy"]), 4),
            "precision": round(float(fila["temporal_precision_weighted"]), 4),
            "recall": round(float(fila["temporal_recall_weighted"]), 4),
            "f1_score": round(float(fila["temporal_f1_weighted"]), 4),
            "precision_macro": round(float(fila["temporal_precision_macro"]), 4),
            "recall_macro": round(float(fila["temporal_recall_macro"]), 4),
            "f1_macro": round(float(fila["temporal_f1_macro"]), 4),
        },
        "parametros_modelo": pipeline_final.named_steps["classifier"].get_params(),
        "nota": (
            "Modelo final seleccionado mediante comparación reproducible entre Regresión Logística, "
            "SVM, Árbol de Decisión y Random Forest. Las señales digitales y reglas mensuales se "
            "integran después de la predicción histórica y no forman parte del entrenamiento principal."
        ),
    }
    FINAL_METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--escribir-final",
        action="store_true",
        help="Reentrena y reemplaza el artefacto final con el modelo ganador.",
    )
    args = parser.parse_args()

    resultados, pipelines_holdout, pipelines_temporales, df = evaluar()
    columnas = [
        "modelo", "holdout_accuracy", "holdout_f1_macro",
        "temporal_accuracy", "temporal_f1_macro", "score_seleccion",
    ]
    print(resultados[columnas].to_string(index=False))
    print(f"\nModelo seleccionado: {resultados.iloc[0]['modelo']}")
    if args.escribir_final:
        escribir_modelo_final(resultados, pipelines_holdout, df)
        print(f"Artefacto final actualizado: {FINAL_MODEL_PATH}")
