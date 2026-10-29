from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import uuid

import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    import psycopg2
except Exception:  # pragma: no cover
    psycopg2 = None

from modelo.predictor import cargar_dataset
from services.guardar_historial import leer_historial
from services.prediccion_final import prediccion_especifica, prediccion_general
from services.senales_digitales import actualizar_tendencia_mensual, obtener_tendencia_mensual

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORTES_DIR = ROOT / "modelo" / "reportes"
METADATA_PATH = ROOT / "modelo" / "artefactos" / "metadata_modelo_final.json"
PREDICCIONES_STORE_PATH = DATA_DIR / "predicciones_generales.csv"
PREDICCIONES_JSON_PATH = DATA_DIR / "predicciones_generales.json"

load_dotenv(ROOT / ".env", override=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-cambiar-en-produccion")

MESES_NOMBRES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}
MESES_DIGITALES_ALTOS = {3, 4, 5, 6, 9, 10, 11, 12}

PRODUCT_IMAGE_MAP = {
    # Imágenes alineadas a la prenda real del dataset.
    # Cuando no existe una foto exacta, se usa una imagen genérica coherente
    # o un placeholder visual con el nombre correcto para evitar errores.
    "chompa de hombre": "media/prendas/chompa_base.png",
    "chompa de mujer": "media/prendas/chompa_mujer.png",
    "chompa escolar": "media/prendas/chompa_escolar.png",
    "prenda militar": "media/prendas/prenda_militar.png",
    "chalina de lana": "media/prendas/chalina_lana.png",
    "manta de lana": "media/prendas/manta_lana.png",
    "chompa profesional": "media/prendas/chompa_profesional.png",
    "chaleco de hombre": "media/prendas/chaleco_base.png",
    "chaleco de mujer": "media/prendas/chaleco_base.png",
    "guantes de lana": "media/prendas/guantes_lana.png",
    "gorro de lana": "media/prendas/gorro_lana.svg",
    "medias de lana": "media/prendas/medias_lana.png",
    "poncho de lana": "media/prendas/poncho_lana.png",
}


def product_image_path(tipo_prenda: str) -> str:
    return PRODUCT_IMAGE_MAP.get(str(tipo_prenda).strip().lower(), "media/prendas/chompa_base.png")


def enriquecer_imagenes(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "tipo_prenda" not in df.columns:
        return df
    temp = df.copy()
    temp["image_path"] = temp["tipo_prenda"].astype(str).map(product_image_path)
    return temp


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validar_credenciales(usuario: str, password: str) -> bool:
    usuario_esperado = os.getenv("APP_USER", "admin").strip()
    password_hash = os.getenv("APP_PASSWORD_SHA256", "").strip()
    password_plano = os.getenv("APP_PASSWORD", "admin123").strip()

    if usuario.strip() != usuario_esperado:
        return False
    if password_hash:
        return _sha256(password) == password_hash
    return password == password_plano


def login_required_view():
    if not session.get("auth"):
        return redirect(url_for("login"))
    return None


def login_required_api():
    if not session.get("auth"):
        return jsonify({"ok": False, "error": "Sesión no iniciada"}), 401
    return None


def load_dataset() -> pd.DataFrame:
    return cargar_dataset()


def load_metadata() -> dict[str, Any]:
    if not METADATA_PATH.exists():
        return {}
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def apify_estado() -> bool:
    # Para la interfaz v11 basta con tener APIFY_TOKEN.
    # El usuario decide desde cada predicción si extrae datos nuevos o si usa caché/local.
    return bool(os.getenv("APIFY_TOKEN", "").strip())


def db_connect():
    if psycopg2 is None:
        return None
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return None
    try:
        return psycopg2.connect(url)
    except Exception:
        return None


def leer_csv_seguro(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()




def _periodo_key(ano: Any, mes: Any, ciudad: Any = "Todas") -> str:
    """Clave estable para una predicción mensual.

    El JSON es la fuente de verdad para compartir la misma predicción entre
    Predicción general, Análisis de tendencias, Historial y Dashboard.
    """
    return f"{int(ano):04d}-{int(mes):02d}-{str(ciudad or 'Todas').strip()}"


def _json_safe_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def leer_predicciones_json() -> dict[str, Any]:
    if not PREDICCIONES_JSON_PATH.exists():
        return {}
    try:
        data = json.loads(PREDICCIONES_JSON_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _guardar_predicciones_json(data: dict[str, Any]) -> None:
    PREDICCIONES_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporal = PREDICCIONES_JSON_PATH.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporal.replace(PREDICCIONES_JSON_PATH)


def leer_predicciones_store() -> pd.DataFrame:
    """Lee todas las predicciones mensuales guardadas.

    Primero usa el almacén JSON (más robusto para listas como Top 3 colores).
    El CSV se conserva solo como compatibilidad/exportación de versiones previas.
    """
    store_json = leer_predicciones_json()
    filas: list[dict[str, Any]] = []
    for item in store_json.values():
        if isinstance(item, dict):
            rows = item.get("rows", [])
            if isinstance(rows, list):
                filas.extend([r for r in rows if isinstance(r, dict)])
    if filas:
        return pd.DataFrame(filas)
    if not PREDICCIONES_STORE_PATH.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(PREDICCIONES_STORE_PATH)
    except Exception:
        return pd.DataFrame()


def guardar_prediccion_general_store(resultado: pd.DataFrame, filtros: dict[str, Any], modo_senal: str) -> None:
    if resultado is None or resultado.empty:
        return

    ano = int(filtros.get("ano"))
    mes = int(filtros.get("mes"))
    ciudad = str(filtros.get("ciudad", "Todas")).strip()
    ejecucion = uuid.uuid4().hex
    fecha = datetime.now().isoformat(timespec="seconds")

    nuevo = resultado.copy().reset_index(drop=True)
    if "ranking" not in nuevo.columns:
        nuevo.insert(0, "ranking", range(1, len(nuevo) + 1))
    nuevo["fecha_prediccion"] = fecha
    nuevo["id_ejecucion"] = ejecucion
    nuevo["proceso"] = "general"
    nuevo["filtro_ciudad"] = ciudad
    nuevo["modo_senal"] = str(modo_senal)
    nuevo["ano"] = ano
    nuevo["mes"] = mes

    # 1) Fuente de verdad: JSON por año/mes/ciudad. Regenerar el mismo periodo
    # reemplaza solo ese periodo; los demás meses nunca se borran.
    rows_json: list[dict[str, Any]] = []
    for record in nuevo.to_dict(orient="records"):
        limpio = {k: _json_safe_value(v) for k, v in record.items()}
        rows_json.append(limpio)
    store_json = leer_predicciones_json()
    store_json[_periodo_key(ano, mes, ciudad)] = {
        "ano": ano,
        "mes": mes,
        "ciudad": ciudad,
        "fecha_prediccion": fecha,
        "id_ejecucion": ejecucion,
        "modo_senal": str(modo_senal),
        "rows": rows_json,
    }
    _guardar_predicciones_json(store_json)

    # 2) CSV espejo para compatibilidad con exportaciones y versiones anteriores.
    actual = pd.DataFrame()
    if PREDICCIONES_STORE_PATH.exists():
        try:
            actual = pd.read_csv(PREDICCIONES_STORE_PATH)
        except Exception:
            actual = pd.DataFrame()
    nuevo_csv = nuevo.copy()
    if "top_colores" in nuevo_csv.columns:
        nuevo_csv["top_colores"] = nuevo_csv["top_colores"].apply(
            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        )
    if not actual.empty and {"ano", "mes"}.issubset(actual.columns):
        anos = pd.to_numeric(actual["ano"], errors="coerce")
        meses = pd.to_numeric(actual["mes"], errors="coerce")
        mask = anos.eq(ano) & meses.eq(mes)
        if "filtro_ciudad" in actual.columns:
            mask &= actual["filtro_ciudad"].fillna("Todas").astype(str).str.strip().eq(ciudad)
        actual = actual.loc[~mask].copy()
    columnas = sorted(set(actual.columns).union(set(nuevo_csv.columns))) if not actual.empty else list(nuevo_csv.columns)
    for c in columnas:
        if c not in actual.columns:
            actual[c] = None
        if c not in nuevo_csv.columns:
            nuevo_csv[c] = None
    final = pd.concat([actual[columnas], nuevo_csv[columnas]], ignore_index=True)
    final.to_csv(PREDICCIONES_STORE_PATH, index=False, encoding="utf-8-sig")


def _predicciones_store_filtradas(ano=None, mes=None, ciudad="Todas") -> pd.DataFrame:
    """Consulta el almacén persistente y soporta filtros 'Todos'."""
    store_json = leer_predicciones_json()
    filas: list[dict[str, Any]] = []
    for item in store_json.values():
        if not isinstance(item, dict):
            continue
        item_ano = item.get("ano")
        item_mes = item.get("mes")
        item_ciudad = str(item.get("ciudad", "Todas"))
        if ano not in (None, "", "Todos") and int(item_ano) != int(ano):
            continue
        if mes not in (None, "", "Todos") and int(item_mes) != int(mes):
            continue
        if ciudad not in (None, "", "Todas") and item_ciudad != str(ciudad):
            continue
        rows = item.get("rows", [])
        if isinstance(rows, list):
            filas.extend([r for r in rows if isinstance(r, dict)])
    if filas:
        df = pd.DataFrame(filas)
        if "ranking" in df.columns:
            df["ranking"] = pd.to_numeric(df["ranking"], errors="coerce")
        return df

    # Compatibilidad con CSV previo si todavía no existe JSON.
    store = pd.DataFrame()
    if PREDICCIONES_STORE_PATH.exists():
        try:
            store = pd.read_csv(PREDICCIONES_STORE_PATH)
        except Exception:
            store = pd.DataFrame()
    if store.empty:
        return store
    store = filtrar_por_periodo(store, ano, mes, None)
    if store.empty:
        return store
    if "filtro_ciudad" in store.columns:
        objetivo = str(ciudad or "Todas").strip()
        if objetivo != "Todas":
            exacto = store[store["filtro_ciudad"].fillna("Todas").astype(str).str.strip().eq(objetivo)].copy()
            if not exacto.empty:
                store = exacto
        else:
            exacto = store[store["filtro_ciudad"].fillna("Todas").astype(str).str.strip().eq("Todas")].copy()
            if not exacto.empty:
                store = exacto
    return store

def metricas_principales() -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = load_metadata()
    holdout = metadata.get("metricas_holdout", [])
    mejor = holdout[0] if holdout else {}
    temporal = metadata.get("metricas_validacion_temporal", {})
    return mejor, temporal


def filtrar_por_periodo(dataframe: pd.DataFrame, ano=None, mes=None, ciudad=None) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe
    temp = dataframe.copy()
    if ano and ano != "Todos" and "ano" in temp.columns:
        temp["ano"] = pd.to_numeric(temp["ano"], errors="coerce")
        temp = temp[temp["ano"] == int(ano)]
    if mes and mes != "Todos" and "mes" in temp.columns:
        temp["mes"] = pd.to_numeric(temp["mes"], errors="coerce")
        temp = temp[temp["mes"] == int(mes)]
    if ciudad and ciudad != "Todas" and "ciudad" in temp.columns:
        temp = temp[temp["ciudad"].astype(str) == str(ciudad)]
    return temp


def preparar_top5_historial(historial_filtrado: pd.DataFrame) -> pd.DataFrame:
    if historial_filtrado.empty:
        return historial_filtrado
    hist = historial_filtrado.copy()
    if "score_final" not in hist.columns and "confianza_final" in hist.columns:
        hist["score_final"] = hist["confianza_final"]
    if "score_digital" not in hist.columns:
        hist["score_digital"] = ""
    if "ranking" in hist.columns:
        hist["ranking"] = pd.to_numeric(hist["ranking"], errors="coerce")
        hist = hist[hist["ranking"].notna()]
        hist = hist[hist["ranking"] <= 5]
        if all(c in hist.columns for c in ["ano", "mes", "ranking"]):
            hist = hist.sort_values(["ano", "mes", "ranking"])
    else:
        hist["score_final"] = pd.to_numeric(hist.get("score_final", 0), errors="coerce").fillna(0)
        hist = hist.sort_values(["ano", "mes", "score_final"], ascending=[True, True, False]).groupby(["ano", "mes"]).head(5)

    columnas = [
        "fecha_prediccion", "ano", "mes", "ciudad", "ranking", "tipo_prenda", "estilo", "color",
        "color_sugerido", "estilo_sugerido", "top_colores", "recomendacion_digital", "prediccion_final",
        "score_final", "score_digital", "score_color", "score_estilo", "fuente_senal",
        "plataformas_consultadas", "desde_cache",
    ]
    columnas = [c for c in columnas if c in hist.columns]
    return hist[columnas]


def generar_pdf_historial(historial_filtrado: pd.DataFrame, titulo: str) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25,
    )
    styles = getSampleStyleSheet()
    elementos = [Paragraph(titulo, styles["Title"]), Spacer(1, 12)]

    if historial_filtrado.empty:
        elementos.append(Paragraph("No existen registros de historial para el filtro seleccionado.", styles["Normal"]))
        doc.build(elementos)
        buffer.seek(0)
        return buffer

    hist = preparar_top5_historial(historial_filtrado)
    if hist.empty:
        elementos.append(Paragraph("No existen registros de Top 5 para el filtro seleccionado.", styles["Normal"]))
        doc.build(elementos)
        buffer.seek(0)
        return buffer

    for (ano, mes), grupo in hist.groupby(["ano", "mes"]):
        mes_int = int(mes)
        elementos.append(Paragraph(f"Top 5 - {MESES_NOMBRES.get(mes_int, mes)} {int(ano)}", styles["Heading2"]))
        elementos.append(Spacer(1, 8))
        columnas_pdf = [
            "ranking", "tipo_prenda", "estilo", "color", "color_sugerido", "estilo_sugerido",
            "prediccion_final", "score_final", "score_digital", "score_color", "score_estilo", "fuente_senal",
        ]
        columnas_pdf = [c for c in columnas_pdf if c in grupo.columns]
        tabla_datos = [columnas_pdf]
        for _, row in grupo[columnas_pdf].iterrows():
            fila = []
            for c in columnas_pdf:
                val = row[c]
                if isinstance(val, float):
                    fila.append(f"{val:.2f}")
                else:
                    fila.append("" if pd.isna(val) else str(val))
            tabla_datos.append(fila)
        tabla = Table(tabla_datos, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
        ]))
        elementos.append(tabla)
        elementos.append(Spacer(1, 14))

    elementos.append(Spacer(1, 12))
    elementos.append(Paragraph("Nota. Reporte generado automáticamente a partir del historial del prototipo.", styles["Normal"]))
    doc.build(elementos)
    buffer.seek(0)
    return buffer


def resumen_ciudad_referencial(mes: int) -> pd.DataFrame:
    df = load_dataset()
    base = df[df["mes"] == int(mes)].copy()
    if base.empty:
        return pd.DataFrame()
    base["peso"] = base["demanda_clase"].map({"Alta": 3, "Media": 2, "Baja": 1}).fillna(1)
    resumen = (
        base.groupby(["ciudad", "tipo_prenda"], as_index=False)["peso"]
        .sum()
        .sort_values(["ciudad", "peso"], ascending=[True, False])
    )
    resumen = resumen.groupby("ciudad").head(3)
    return resumen.rename(columns={"tipo_prenda": "prenda_referencial", "peso": "puntaje_historico"})


def limpiar_csv_cache_filtrado(path: Path, ano: int, mes: int, ciudad: str = "Todas") -> int:
    if not path.exists():
        return 0
    data = leer_csv_seguro(path)
    if data.empty or "ano" not in data.columns or "mes" not in data.columns:
        return 0
    temp = data.copy()
    temp["ano"] = pd.to_numeric(temp["ano"], errors="coerce")
    temp["mes"] = pd.to_numeric(temp["mes"], errors="coerce")
    mask = (temp["ano"] == int(ano)) & (temp["mes"] == int(mes))
    if ciudad != "Todas" and "ciudad" in temp.columns:
        mask = mask & (temp["ciudad"].astype(str) == str(ciudad))
    eliminados = int(mask.sum())
    temp = temp[~mask]
    temp.to_csv(path, index=False, encoding="utf-8-sig")
    return eliminados


def limpiar_cache_local_filtrado(ano: int, mes: int, ciudad: str = "Todas") -> dict[str, int]:
    archivos = [DATA_DIR / "senales_cache.csv", DATA_DIR / "senales_detalle_plataformas.csv", DATA_DIR / "tendencias_mensuales_cache.csv"]
    return {archivo.name: limpiar_csv_cache_filtrado(archivo, ano, mes, ciudad) for archivo in archivos}


def limpiar_cache_postgres_filtrado(ano: int, mes: int, ciudad: str = "Todas") -> tuple[bool, str]:
    conn = db_connect()
    if conn is None:
        return False, "No se pudo conectar a PostgreSQL. Revisa DATABASE_URL."
    try:
        sql = "DELETE FROM senales WHERE ano = %s AND mes = %s"
        params = [int(ano), int(mes)]
        if ciudad != "Todas":
            sql += " AND ciudad = %s"
            params.append(ciudad)
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                eliminados = cur.rowcount
        return True, f"Se eliminaron {eliminados} señales en PostgreSQL."
    except Exception as exc:
        return False, str(exc)
    finally:
        conn.close()


def df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    safe = df.copy()
    safe = safe.replace({pd.NA: None})
    safe = safe.where(pd.notna(safe), None)
    records = safe.to_dict(orient="records")
    return records


def to_float_percent(value: Any) -> float:
    try:
        return round(float(value) * 100, 2)
    except Exception:
        return 0.0


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "")
        password = request.form.get("password", "")
        if validar_credenciales(usuario, password):
            session["auth"] = True
            session["usuario"] = usuario.strip()
            return redirect(url_for("index"))
        return render_template("login.html", error="Usuario o contraseña incorrectos.")
    if session.get("auth"):
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    redirect_response = login_required_view()
    if redirect_response:
        return redirect_response
    df = load_dataset()
    metadata = load_metadata()
    modelo_id = str(metadata.get("modelo_seleccionado", "Modelo ML"))
    nombres_modelo = {
        "Arbol_Decision": "Árbol de Decisión",
        "Random_Forest": "Random Forest",
        "Regresion_Logistica": "Regresión Logística",
        "SVM": "SVM",
    }
    modelo_nombre = nombres_modelo.get(modelo_id, modelo_id.replace("_", " "))
    context = {
        "usuario": session.get("usuario", "admin"),
        "modelo_nombre": modelo_nombre,
        "apify_configurado": apify_estado(),
        "registros": len(df),
        "prendas": int(df["tipo_prenda"].nunique()),
        "colores": int(df["color"].nunique()) if "color" in df.columns else 0,
        "estilos": int(df["estilo"].nunique()) if "estilo" in df.columns else 0,
        "hero_la_paz": url_for("static", filename="media/ciudades/la_paz.png"),
        "hero_el_alto": url_for("static", filename="media/ciudades/el_alto.png"),
        "hero_lana": url_for("static", filename="media/lana/lana_cono.png"),
    }
    return render_template("index.html", **context)


@app.route("/api/options")
def api_options():
    auth = login_required_api()
    if auth:
        return auth
    df = load_dataset()
    anos = sorted(df["ano"].dropna().astype(int).unique().tolist())
    ciudades = sorted(df["ciudad"].dropna().astype(str).unique().tolist())
    prendas = sorted(df["tipo_prenda"].dropna().astype(str).unique().tolist())
    return jsonify({
        "ok": True,
        "anos": anos,
        "meses": [{"value": m, "label": f"{m:02d} - {MESES_NOMBRES[m]}"} for m in range(1, 13)],
        "ciudades": ciudades,
        "prendas": prendas,
    })


@app.route("/api/summary")
def api_summary():
    auth = login_required_api()
    if auth:
        return auth
    df = load_dataset()
    mejor, temporal = metricas_principales()
    return jsonify({
        "ok": True,
        "registros": len(df),
        "prendas": int(df["tipo_prenda"].nunique()),
        "colores": int(df["color"].nunique()),
        "estilos": int(df["estilo"].nunique()),
        "accuracy_holdout": to_float_percent(mejor.get("accuracy", 0)),
        "accuracy_temporal": to_float_percent(temporal.get("accuracy", 0)),
        "apify_configurado": apify_estado(),
    })


@app.route("/api/prediccion/general", methods=["POST"])
def api_prediccion_general():
    auth = login_required_api()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    try:
        filtros = {
            "ano": int(data.get("ano", 2026)),
            "mes": int(data.get("mes", 5)),
            "ciudad": data.get("ciudad", "Todas"),
        }
        modo_senal = str(data.get("modo_senal", "cache")).strip().lower()
        if modo_senal not in {"cache", "apify", "auto"}:
            modo_senal = "cache"
        res = enriquecer_imagenes(prediccion_general(filtros, top_n=5, modo_senal=modo_senal))
        guardar_prediccion_general_store(res, filtros, modo_senal)
        # Verificación de escritura: si el Top 5 no puede releerse, no mostramos una
        # predicción como "guardada". Esto evita que otros módulos queden vacíos.
        verificacion = _predicciones_store_filtradas(filtros["ano"], filtros["mes"], filtros["ciudad"])
        if verificacion.empty or len(verificacion) < min(5, len(res)):
            raise RuntimeError("La predicción se calculó, pero no pudo guardarse de forma persistente. Verifica permisos de escritura en la carpeta data.")
        comparativo = resumen_ciudad_referencial(filtros["mes"])
        return jsonify({"ok": True, "filtros": filtros, "modo_senal": modo_senal, "rows": df_to_records(res), "comparativo_ciudad": df_to_records(comparativo), "guardado": True, "filas_guardadas": int(len(verificacion))})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500



def _preparar_color_estilo_desde_historial(filtros: dict[str, Any], top_n: int = 5) -> pd.DataFrame:
    """Obtiene color/estilo desde la última predicción general guardada.

    Este módulo no consulta Apify. Reutiliza el resultado general más reciente para
    el mismo año, mes y ciudad. Si no hay predicción general, devuelve vacío para
    que la ruta use el caché local o genere una predicción con modo cache.
    """
    hist = _ultima_prediccion_general_guardada(
        int(filtros.get("ano")), int(filtros.get("mes")), filtros.get("ciudad", "Todas")
    )
    if hist.empty:
        return pd.DataFrame()
    hist = hist.head(top_n).reset_index(drop=True)
    hist["ranking"] = range(1, len(hist) + 1)
    hist["origen_resultado"] = "Última predicción general"
    return hist


def _preparar_color_estilo_desde_cache(filtros: dict[str, Any], top_n: int = 5) -> pd.DataFrame:
    cache = filtrar_por_periodo(leer_csv_seguro(DATA_DIR / "senales_cache.csv"), filtros.get("ano"), filtros.get("mes"), filtros.get("ciudad", "Todas"))
    if cache.empty:
        return pd.DataFrame()
    cache = cache.copy()
    for col in ["score_digital", "score_color", "score_estilo"]:
        if col in cache.columns:
            cache[col] = pd.to_numeric(cache[col], errors="coerce").fillna(0)
        else:
            cache[col] = 0
    cache["score_final"] = (0.45 * cache["score_digital"] + 0.30 * cache["score_color"] + 0.25 * cache["score_estilo"]).round(2)
    if "fuente" in cache.columns and "fuente_senal" not in cache.columns:
        cache["fuente_senal"] = cache["fuente"]
    cache["prediccion_final"] = "Referencial"
    cache["origen_resultado"] = "Caché de señales"
    sort_cols = ["score_final", "score_color", "score_estilo", "score_digital"]
    cache = cache.sort_values(sort_cols, ascending=False)
    cache = cache.drop_duplicates(subset=["tipo_prenda", "color_sugerido", "estilo_sugerido"], keep="first").head(top_n).reset_index(drop=True)
    cache["ranking"] = range(1, len(cache) + 1)
    return cache

@app.route("/api/prediccion/color-estilo", methods=["POST"])
def api_color_estilo():
    auth = login_required_api()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    try:
        filtros = {
            "ano": int(data.get("ano", 2026)),
            "mes": int(data.get("mes", 5)),
            "ciudad": data.get("ciudad", "Todas"),
        }
        top_n = int(data.get("top_n", 5))

        # 1) Prioridad: usar la última predicción general ya generada para el mismo filtro.
        res = _preparar_color_estilo_desde_historial(filtros, top_n=top_n)
        origen = "historial"

        # 2) Si no hay historial, usar caché de señales ya captadas.
        if res.empty:
            res = _preparar_color_estilo_desde_cache(filtros, top_n=top_n)
            origen = "cache"

        # 3) Si tampoco hay caché, generar una predicción general en modo cache/local.
        #    Esto NO gasta Apify; solo crea un punto de partida para color y estilo.
        if res.empty:
            res = prediccion_general(filtros, top_n=5, modo_senal="cache").head(top_n).copy()
            res["origen_resultado"] = "Generado con caché/local"
            origen = "generado_cache_local"

        res = enriquecer_imagenes(res)
        analisis_tendencia = _analisis_tendencia_top5(res, filtros)
        return jsonify({"ok": True, "filtros": filtros, "modo_senal": "cache", "origen": origen, "rows": df_to_records(res), "analisis_tendencia": analisis_tendencia})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/historial")
def api_historial():
    auth = login_required_api()
    if auth:
        return auth
    ano = request.args.get("ano", "Todos")
    mes = request.args.get("mes", "Todos")
    ciudad = request.args.get("ciudad", "Todas")
    # El historial visible representa las predicciones generales vigentes guardadas.
    # Así coincide exactamente con Análisis de tendencias y Dashboard.
    hist_filtrado = _predicciones_store_filtradas(ano, mes, ciudad)
    if hist_filtrado.empty:
        hist = leer_historial(20000)
        hist_filtrado = filtrar_por_periodo(hist, ano, mes, ciudad)
    if "score_final" in hist_filtrado.columns:
        hist_filtrado["score_final"] = pd.to_numeric(hist_filtrado["score_final"], errors="coerce").round(2)
    return jsonify({"ok": True, "rows": df_to_records(hist_filtrado.sort_index(ascending=False)), "total": int(len(hist_filtrado))})


@app.route("/api/historial/export.csv")
def export_historial_csv():
    redirect_response = login_required_view()
    if redirect_response:
        return redirect_response
    ano = request.args.get("ano", "Todos")
    mes = request.args.get("mes", "Todos")
    ciudad = request.args.get("ciudad", "Todas")
    # El historial visible representa las predicciones generales vigentes guardadas.
    # Así coincide exactamente con Análisis de tendencias y Dashboard.
    hist_filtrado = _predicciones_store_filtradas(ano, mes, ciudad)
    if hist_filtrado.empty:
        hist = leer_historial(20000)
        hist_filtrado = filtrar_por_periodo(hist, ano, mes, ciudad)
    csv_text = hist_filtrado.to_csv(index=False, encoding="utf-8-sig")
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=historial_predicciones_filtrado.csv"},
    )


@app.route("/api/historial/export.pdf")
def export_historial_pdf():
    redirect_response = login_required_view()
    if redirect_response:
        return redirect_response
    ano = request.args.get("ano", "Todos")
    mes = request.args.get("mes", "Todos")
    ciudad = request.args.get("ciudad", "Todas")
    # El historial visible representa las predicciones generales vigentes guardadas.
    # Así coincide exactamente con Análisis de tendencias y Dashboard.
    hist_filtrado = _predicciones_store_filtradas(ano, mes, ciudad)
    if hist_filtrado.empty:
        hist = leer_historial(20000)
        hist_filtrado = filtrar_por_periodo(hist, ano, mes, ciudad)
    pdf = generar_pdf_historial(hist_filtrado, "Reporte de historial de predicciones - Top 5 por mes")
    return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name="historial_predicciones_top5.pdf")


def _ultima_prediccion_general_guardada(ano: int, mes: int, ciudad: str = "Todas") -> pd.DataFrame:
    """Devuelve el Top 5 completo de la ejecución general más reciente del periodo.

    Las versiones nuevas guardan un id_ejecucion común para las cinco filas. Para
    historiales antiguos sin ese identificador, se reconstruye la última ejecución
    usando las filas más recientes y el ranking 1-5. Así funciona para cualquier mes
    (enero-diciembre), no solo para meses presentes en el dataset histórico.
    """
    store = _predicciones_store_filtradas(ano, mes, ciudad)
    if not store.empty:
        if "ranking" in store.columns:
            store["ranking"] = pd.to_numeric(store["ranking"], errors="coerce")
            store = store.sort_values("ranking")
        return store.head(5).reset_index(drop=True)

    hist = leer_historial(50000)
    hist = filtrar_por_periodo(hist, ano, mes, None)
    if hist.empty or "proceso" not in hist.columns:
        return pd.DataFrame()
    hist = hist[hist["proceso"].astype(str).str.lower().eq("general")].copy()
    if hist.empty:
        return pd.DataFrame()

    # Respeta el filtro original con que se generó la predicción cuando está disponible.
    if "filtro_ciudad" in hist.columns and hist["filtro_ciudad"].notna().any():
        objetivo = str(ciudad or "Todas")
        exacto = hist[hist["filtro_ciudad"].astype(str).eq(objetivo)].copy()
        if not exacto.empty:
            hist = exacto
    elif ciudad and ciudad != "Todas" and "ciudad" in hist.columns:
        exacto = hist[hist["ciudad"].astype(str).eq(str(ciudad))].copy()
        if not exacto.empty:
            hist = exacto

    hist["fecha_prediccion"] = hist.get("fecha_prediccion", "").astype(str)
    if "id_ejecucion" in hist.columns:
        ids = hist["id_ejecucion"].fillna("").astype(str)
        validos = hist[ids.str.len() > 0].copy()
        if not validos.empty:
            ult_id = validos.sort_values("fecha_prediccion").iloc[-1]["id_ejecucion"]
            run = validos[validos["id_ejecucion"].astype(str).eq(str(ult_id))].copy()
            if "ranking" in run.columns:
                run["ranking"] = pd.to_numeric(run["ranking"], errors="coerce")
                run = run.sort_values("ranking")
            return run.head(5).reset_index(drop=True)

    # Compatibilidad con historial previo: tomar la última fila de cada ranking.
    if "ranking" in hist.columns:
        hist["ranking"] = pd.to_numeric(hist["ranking"], errors="coerce")
        hist = hist[hist["ranking"].between(1, 5, inclusive="both")].copy()
        if not hist.empty:
            run = (hist.sort_values("fecha_prediccion")
                       .drop_duplicates(subset=["ranking"], keep="last")
                       .sort_values("ranking"))
            if len(run) >= 1:
                return run.head(5).reset_index(drop=True)
    return hist.sort_values("fecha_prediccion", ascending=False).head(5).sort_values("fecha_prediccion").reset_index(drop=True)




DEMANDA_PESO_TENDENCIA = {"Alta": 100.0, "Media": 60.0, "Baja": 25.0}

def _score_demanda_periodo(df: pd.DataFrame, prenda: str, ano: int, mes: int, ciudad: str = "Todas") -> float:
    base = df[df["tipo_prenda"].astype(str).eq(str(prenda))].copy()
    if ciudad not in {None, "", "Todas", "Todos"}:
        base_ciudad = base[base["ciudad"].astype(str).eq(str(ciudad))]
        if not base_ciudad.empty:
            base = base_ciudad
    exacto = base[(pd.to_numeric(base["ano"], errors="coerce") == int(ano)) & (pd.to_numeric(base["mes"], errors="coerce") == int(mes))]
    muestra = exacto
    if muestra.empty:
        muestra = base[(pd.to_numeric(base["ano"], errors="coerce") < int(ano)) & (pd.to_numeric(base["mes"], errors="coerce") == int(mes))]
    if muestra.empty:
        muestra = base[pd.to_numeric(base["mes"], errors="coerce") == int(mes)]
    if muestra.empty:
        return 0.0
    valores = muestra["demanda_clase"].map(DEMANDA_PESO_TENDENCIA).fillna(45.0)
    return round(float(valores.mean()), 2)

def _periodos_hacia_atras(ano: int, mes: int, cantidad: int = 6) -> list[tuple[int, int]]:
    periodos = []
    a, m = int(ano), int(mes)
    for _ in range(cantidad):
        periodos.append((a, m))
        m -= 1
        if m == 0:
            m = 12
            a -= 1
    return list(reversed(periodos))

def _clasificar_tendencia(delta: float) -> str:
    if delta >= 4.0:
        return "Creciente"
    if delta <= -4.0:
        return "Decreciente"
    return "Estable"

def _prioridad_planificacion(demanda: str, tendencia: str) -> str:
    demanda = str(demanda or "Media")
    tendencia = str(tendencia or "Estable")
    if demanda == "Alta":
        return "Media" if tendencia == "Decreciente" else "Alta"
    if demanda == "Media":
        if tendencia == "Creciente": return "Alta"
        if tendencia == "Decreciente": return "Baja"
        return "Media"
    return "Media" if tendencia == "Creciente" else "Baja"

def _analisis_tendencia_top5(rows: pd.DataFrame, filtros: dict[str, Any]) -> list[dict[str, Any]]:
    if rows is None or rows.empty:
        return []
    df = load_dataset()
    ano, mes = int(filtros["ano"]), int(filtros["mes"])
    ciudad = str(filtros.get("ciudad", "Todas"))
    periodos = _periodos_hacia_atras(ano, mes, 6)
    salida = []
    for _, row in rows.head(5).iterrows():
        prenda = str(row.get("tipo_prenda", ""))
        serie = []
        for a, m in periodos:
            score = _score_demanda_periodo(df, prenda, a, m, ciudad)
            serie.append({"ano": a, "mes": m, "periodo": f"{m:02d}/{a}", "score": score})
        actual = float(serie[-1]["score"] if serie else 0)
        anterior = float(serie[-2]["score"] if len(serie) > 1 else actual)
        delta = round(actual - anterior, 2)
        clasificacion = _clasificar_tendencia(delta)
        demanda = str(row.get("prediccion_final", "Media"))
        prioridad = _prioridad_planificacion(demanda, clasificacion)
        if clasificacion == "Creciente":
            lectura = "El comportamiento histórico reciente favorece una mayor atención productiva."
        elif clasificacion == "Decreciente":
            lectura = "El comportamiento histórico reciente sugiere moderar la prioridad productiva."
        else:
            lectura = "El comportamiento histórico se mantiene estable para el periodo analizado."
        salida.append({
            "ranking": int(row.get("ranking", len(salida)+1) or len(salida)+1),
            "tipo_prenda": prenda,
            "demanda": demanda,
            "tendencia": clasificacion,
            "variacion": delta,
            "score_actual": actual,
            "score_anterior": anterior,
            "prioridad_planificacion": prioridad,
            "interpretacion": lectura,
            "serie": serie,
        })
    return salida

def _json_counts(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {str(k): float(v or 0) for k, v in value.items()}
    try:
        obj = json.loads(str(value or "{}"))
        return {str(k): float(v or 0) for k, v in obj.items()} if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _ultimas_por_ciudad(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    temp = df.copy()
    if "fecha_extraccion" not in temp.columns:
        return temp
    temp["fecha_extraccion"] = temp["fecha_extraccion"].astype(str)
    if "ciudad" in temp.columns:
        idx = temp.groupby(temp["ciudad"].astype(str))["fecha_extraccion"].idxmax()
        return temp.loc[idx].copy()
    return temp.sort_values("fecha_extraccion").tail(1).copy()

def _consolidar_tendencias_ciudad(df: pd.DataFrame, ciudad: str = "Todas") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    temp = _ultimas_por_ciudad(df) if str(ciudad) in {"Todas", "Todos"} else df.sort_values("fecha_extraccion").tail(1).copy()
    if temp.empty or len(temp) == 1:
        return temp.tail(1).copy()
    color_counts, estilo_counts = {}, {}
    for _, r in temp.iterrows():
        for k, v in _json_counts(r.get("color_counts")).items(): color_counts[k] = color_counts.get(k, 0.0) + v
        for k, v in _json_counts(r.get("estilo_counts")).items(): estilo_counts[k] = estilo_counts.get(k, 0.0) + v
    def best(counts, fallback):
        positivos = [(k,v) for k,v in counts.items() if float(v) > 0]
        return max(positivos, key=lambda x:x[1])[0] if positivos else fallback
    ultima = temp.sort_values("fecha_extraccion").iloc[-1].to_dict()
    fila = {
        **ultima,
        "ciudad": "Todas",
        "score_digital": round(float(pd.to_numeric(temp.get("score_digital"), errors="coerce").fillna(0).mean()), 2),
        "publicaciones": int(pd.to_numeric(temp.get("publicaciones"), errors="coerce").fillna(0).sum()),
        "menciones": int(pd.to_numeric(temp.get("menciones"), errors="coerce").fillna(0).sum()),
        "interacciones": int(pd.to_numeric(temp.get("interacciones"), errors="coerce").fillna(0).sum()),
        "color_counts": json.dumps(color_counts, ensure_ascii=False),
        "estilo_counts": json.dumps(estilo_counts, ensure_ascii=False),
        "color_sugerido": best(color_counts, str(ultima.get("color_sugerido") or "beige")),
        "estilo_sugerido": best(estilo_counts, str(ultima.get("estilo_sugerido") or "casual")),
        "fuente": ", ".join(sorted({str(x) for x in temp.get("fuente", pd.Series(dtype=str)).dropna() if str(x).strip()})),
        "plataformas_consultadas": ", ".join(sorted({str(x) for x in temp.get("plataformas_consultadas", pd.Series(dtype=str)).dropna() if str(x).strip()})),
        "fecha_extraccion": str(temp["fecha_extraccion"].max()),
    }
    return pd.DataFrame([fila])

def _senales_guardadas_resumen(ano_filtro=None) -> list[dict[str, Any]]:
    """Calendario GLOBAL de señales del año.

    Siempre devuelve los 12 meses del año seleccionado. Los meses que ya tienen
    tendencia mensual muestran sus datos; los demás aparecen explícitamente como
    "Sin señales". No depende del mes seleccionado en la pantalla.
    """
    try:
        ano_objetivo = int(ano_filtro) if str(ano_filtro) not in {"", "None", "Todos", "Todas"} else datetime.now().year
    except Exception:
        ano_objetivo = datetime.now().year

    all_trends = leer_csv_seguro(DATA_DIR / "tendencias_mensuales_cache.csv")
    if not all_trends.empty and "ano" in all_trends.columns:
        all_trends = all_trends[pd.to_numeric(all_trends["ano"], errors="coerce").eq(ano_objetivo)].copy()

    por_mes: dict[int, dict[str, Any]] = {}
    if not all_trends.empty:
        for mes, grp in all_trends.groupby("mes", dropna=False):
            try:
                mes_int = int(mes)
            except Exception:
                continue
            latest = _ultimas_por_ciudad(grp)
            consolidated = _consolidar_tendencias_ciudad(grp, "Todas")
            c = consolidated.iloc[0].to_dict() if not consolidated.empty else {}
            ciudades = sorted({str(x) for x in latest.get("ciudad", pd.Series(dtype=str)).dropna() if str(x).strip()})
            fuentes = sorted({str(x) for x in latest.get("fuente", pd.Series(dtype=str)).dropna() if str(x).strip()})
            por_mes[mes_int] = {
                "ano": ano_objetivo,
                "mes": mes_int,
                "periodo": f"{mes_int:02d} - {MESES_NOMBRES.get(mes_int, mes_int)} {ano_objetivo}",
                "ciudades": " · ".join(ciudades),
                "fuentes": " · ".join(fuentes),
                "color_sugerido": c.get("color_sugerido"),
                "estilo_sugerido": c.get("estilo_sugerido"),
                "publicaciones": c.get("publicaciones", 0),
                "interacciones": c.get("interacciones", 0),
                "fecha_extraccion": c.get("fecha_extraccion"),
                "tiene_senales": True,
            }

    rows=[]
    for mes_int in range(1, 13):
        if mes_int in por_mes:
            rows.append(por_mes[mes_int])
        else:
            rows.append({
                "ano": ano_objetivo,
                "mes": mes_int,
                "periodo": f"{mes_int:02d} - {MESES_NOMBRES.get(mes_int, mes_int)} {ano_objetivo}",
                "ciudades": "—",
                "fuentes": "Sin señales",
                "color_sugerido": None,
                "estilo_sugerido": None,
                "publicaciones": 0,
                "interacciones": 0,
                "fecha_extraccion": None,
                "tiene_senales": False,
            })
    return rows

def _parse_top_colores(valor) -> list[dict[str, Any]]:
    if isinstance(valor, list):
        return valor
    try:
        obj = json.loads(str(valor or "[]"))
        return obj if isinstance(obj, list) else []
    except Exception:
        return []


@app.route("/api/dashboard")
def api_dashboard():
    auth = login_required_api()
    if auth:
        return auth
    df = load_dataset()
    ano = int(request.args.get("ano", 2026))
    mes = int(request.args.get("mes", 5))
    ciudad = request.args.get("ciudad", "Todas")

    # Contexto histórico disponible para el año/ciudad. El dataset llega hasta 2026-05,
    # por eso para meses futuros el dashboard usa la predicción general guardada.
    dash = df[df["ano"] == ano].copy()
    if ciudad != "Todas":
        dash = dash[dash["ciudad"].astype(str) == str(ciudad)]
    if not dash.empty:
        dash["peso_demanda"] = dash["demanda_clase"].map({"Alta": 3, "Media": 2, "Baja": 1}).fillna(1)
    dash_mes = dash[dash["mes"] == mes].copy() if not dash.empty else pd.DataFrame()
    pred = _ultima_prediccion_general_guardada(ano, mes, ciudad)

    # Si existe una predicción guardada, esa es la lectura principal del periodo.
    if not pred.empty:
        pred = enriquecer_imagenes(pred.copy())
        for c in ["score_final", "score_digital", "score_color", "score_estilo"]:
            if c in pred.columns:
                pred[c] = pd.to_numeric(pred[c], errors="coerce").fillna(0)
        prendas = pred.copy()
        prendas["peso_demanda"] = prendas.get("score_final", 0)

        color_rows = []
        for _, r in pred.iterrows():
            tops = _parse_top_colores(r.get("top_colores"))
            if not tops and r.get("color_sugerido"):
                tops = [{"color": r.get("color_sugerido"), "score": r.get("score_color", 0)}]
            for item in tops:
                color_rows.append({"color": item.get("color"), "peso_demanda": float(item.get("score", 0) or 0)})
        colores = pd.DataFrame(color_rows)
        if not colores.empty:
            colores = colores.groupby("color", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False).head(8)

        estilos = pred.groupby("estilo_sugerido", as_index=False)["score_estilo"].sum().rename(columns={"estilo_sugerido":"estilo", "score_estilo":"peso_demanda"}).sort_values("peso_demanda", ascending=False)
        demanda = pred.groupby("prediccion_final", as_index=False).size().rename(columns={"prediccion_final":"demanda_clase", "size":"cantidad"})

        actividad = leer_historial(50000)
        actividad = filtrar_por_periodo(actividad, ano, mes, ciudad)
        if not actividad.empty and "proceso" in actividad.columns:
            actividad = actividad.groupby("proceso", as_index=False).size().rename(columns={"size":"cantidad"}).sort_values("cantidad", ascending=False)
        else:
            actividad = pd.DataFrame()

        # Evolución histórica del año hasta donde exista dataset; se conserva como contexto.
        evolucion = dash.groupby("mes", as_index=False)["peso_demanda"].sum().sort_values("mes") if not dash.empty else pd.DataFrame()
        ciudades = pd.DataFrame()
        zonas = pd.DataFrame()
        if not dash_mes.empty:
            base_ciudades = df[(df["ano"] == ano) & (df["mes"] == mes)].copy()
            base_ciudades["peso_demanda"] = base_ciudades["demanda_clase"].map({"Alta":3,"Media":2,"Baja":1}).fillna(1)
            ciudades = base_ciudades.groupby("ciudad", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False)
            zonas = dash_mes.groupby("zona_comercial", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False).head(8)

        tendencia = filtrar_por_periodo(leer_csv_seguro(DATA_DIR / "tendencias_mensuales_cache.csv"), ano, mes, ciudad)
        metrics = {
            "fuente": "Predicción guardada",
            "prendas": int(len(pred)),
            "colores": int(len({i.get('color') for v in pred.get('top_colores', []) for i in _parse_top_colores(v) if i.get('color')})) if 'top_colores' in pred.columns else int(pred.get('color_sugerido', pd.Series(dtype=str)).nunique()),
            "estilos": int(pred.get("estilo_sugerido", pd.Series(dtype=str)).nunique()),
            "score_promedio": round(float(pred.get("score_final", pd.Series([0])).mean()), 2),
            "apify": "Con tendencia" if not tendencia.empty else "Sin tendencia mensual",
        }
        return jsonify({"ok":True,"empty":False,"source":"prediccion_guardada","metrics":metrics,
                        "prendas":df_to_records(prendas),"colores":df_to_records(colores),"estilos":df_to_records(estilos),
                        "demanda":df_to_records(demanda),"evolucion":df_to_records(evolucion),"ciudades":df_to_records(ciudades),
                        "zonas":df_to_records(zonas),"actividad":df_to_records(actividad)})

    # Si todavía no se generó una predicción para ese periodo, mostrar histórico cuando exista.
    if dash_mes.empty:
        return jsonify({"ok": True, "empty": True, "reason":"sin_prediccion", "metrics": {}, "prendas": [], "colores": [], "estilos": [], "demanda": [], "evolucion": [], "ciudades": [], "zonas": [], "actividad": []})

    prendas = dash_mes.groupby("tipo_prenda", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False).head(5)
    prendas["score_final"] = prendas["peso_demanda"]
    prendas["prediccion_final"] = "Histórico"
    prendas["color_sugerido"] = "—"
    prendas["estilo_sugerido"] = "—"
    prendas = enriquecer_imagenes(prendas)
    colores = dash_mes.groupby("color", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False).head(8)
    estilos = dash_mes.groupby("estilo", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False).head(8)
    demanda = dash_mes.groupby("demanda_clase", as_index=False).size().rename(columns={"size": "cantidad"}).sort_values("cantidad", ascending=False)
    evolucion = dash.groupby("mes", as_index=False)["peso_demanda"].sum().sort_values("mes")
    ciudades = df[(df["ano"] == ano) & (df["mes"] == mes)].copy()
    ciudades["peso_demanda"] = ciudades["demanda_clase"].map({"Alta": 3, "Media": 2, "Baja": 1}).fillna(1)
    ciudades = ciudades.groupby("ciudad", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False)
    zonas = dash_mes.groupby("zona_comercial", as_index=False)["peso_demanda"].sum().sort_values("peso_demanda", ascending=False).head(8)
    return jsonify({"ok":True,"empty":False,"source":"historico","metrics":{"fuente":"Histórico","registros_mes":int(len(dash_mes)),"prendas":int(dash_mes['tipo_prenda'].nunique()),"colores":int(dash_mes['color'].nunique()),"estilos":int(dash_mes['estilo'].nunique())},
                    "prendas":df_to_records(prendas),"colores":df_to_records(colores),"estilos":df_to_records(estilos),"demanda":df_to_records(demanda),
                    "evolucion":df_to_records(evolucion),"ciudades":df_to_records(ciudades),"zonas":df_to_records(zonas),"actividad":[]})


@app.route("/api/senales")
def api_senales():
    auth = login_required_api()
    if auth:
        return auth

    ano = request.args.get("ano", "Todos")
    mes = request.args.get("mes", "Todos")
    ciudad = request.args.get("ciudad", "Todas")

    # Fuente de verdad visual: tendencias_mensuales_cache.csv.
    # Si un periodo ya existe aquí, DEBE mostrarse aunque el detalle técnico
    # contenga intentos posteriores fallidos/simulados o el filtro sea "Todas".
    all_trends = leer_csv_seguro(DATA_DIR / "tendencias_mensuales_cache.csv")
    tendencia_base = filtrar_por_periodo(all_trends, ano, mes, ciudad)
    tendencia = _consolidar_tendencias_ciudad(tendencia_base, ciudad)
    ultima_tendencia = df_to_records(tendencia.tail(1))[0] if not tendencia.empty else None

    # Construir las tarjetas de fuentes directamente desde el caché mensual
    # (una tarjeta por ciudad/fuente). Esto hace que Mayo, Agosto y Septiembre
    # se comporten exactamente igual si ya tienen señal guardada.
    detalle_visual = pd.DataFrame()
    if not tendencia_base.empty:
        tb = _ultimas_por_ciudad(tendencia_base) if str(ciudad) in {"Todas", "Todos"} else tendencia_base.sort_values("fecha_extraccion").tail(1).copy()
        filas = []
        for _, r in tb.iterrows():
            fuente = str(r.get("fuente") or r.get("plataformas_consultadas") or "Señal digital").strip()
            plataformas = [x.strip() for x in fuente.replace(";", ",").split(",") if x.strip()] or ["Señal digital"]
            for plataforma in plataformas:
                filas.append({
                    "fecha_extraccion": r.get("fecha_extraccion"),
                    "ano": r.get("ano"),
                    "mes": r.get("mes"),
                    "ciudad": r.get("ciudad"),
                    "plataforma": plataforma.lower(),
                    "estado": "OK",
                    "publicaciones": r.get("publicaciones", 0),
                    "menciones": r.get("menciones", 0),
                    "interacciones": r.get("interacciones", 0),
                    "score_digital": r.get("score_digital", 0),
                    "color_sugerido": r.get("color_sugerido"),
                    "estilo_sugerido": r.get("estilo_sugerido"),
                })
        detalle_visual = pd.DataFrame(filas)

    # Se conserva el detalle técnico solo como respaldo cuando todavía no existe
    # una tendencia mensual consolidada.
    if detalle_visual.empty:
        detalle_raw = filtrar_por_periodo(leer_csv_seguro(DATA_DIR / "senales_detalle_plataformas.csv"), ano, mes, ciudad)
        if not detalle_raw.empty:
            if "estado" in detalle_raw.columns:
                ok_mask = detalle_raw["estado"].astype(str).str.upper().eq("OK")
                datos_mask = (pd.to_numeric(detalle_raw.get("publicaciones", 0), errors="coerce").fillna(0) > 0) | (pd.to_numeric(detalle_raw.get("interacciones", 0), errors="coerce").fillna(0) > 0)
                detalle_raw = detalle_raw[ok_mask | datos_mask].copy()
            if not detalle_raw.empty and "fecha_extraccion" in detalle_raw.columns:
                detalle_raw["fecha_extraccion"] = detalle_raw["fecha_extraccion"].astype(str)
                group_cols = [c for c in ["ciudad", "plataforma"] if c in detalle_raw.columns]
                if group_cols:
                    idx = detalle_raw.groupby(group_cols)["fecha_extraccion"].idxmax()
                    detalle_raw = detalle_raw.loc[idx].copy()
            detalle_visual = detalle_raw

    cache = filtrar_por_periodo(leer_csv_seguro(DATA_DIR / "senales_cache.csv"), ano, mes, ciudad)
    log = leer_csv_seguro(DATA_DIR / "apify_diagnostico_log.csv")

    estado_tendencia = None
    if ultima_tendencia and ultima_tendencia.get("fecha_extraccion"):
        try:
            fecha = datetime.fromisoformat(str(ultima_tendencia["fecha_extraccion"]))
            dias_recomendacion = int(os.getenv("APIFY_RECOMENDACION_DIAS", "14"))
            proxima = fecha + timedelta(days=dias_recomendacion)
            dias_transcurridos = max(0, (datetime.now() - fecha).days)
            estado_tendencia = {
                "ultima_actualizacion": fecha.isoformat(timespec="seconds"),
                "proxima_actualizacion_recomendada": proxima.isoformat(timespec="seconds"),
                "dias_transcurridos": dias_transcurridos,
                "dias_recomendacion": dias_recomendacion,
                "actualizacion_recomendada": datetime.now() >= proxima,
                "persistente": True,
            }
        except Exception:
            estado_tendencia = None

    return jsonify({
        "ok": True,
        "tendencia_mensual": ultima_tendencia,
        "estado_tendencia": estado_tendencia,
        "cache_total": 1 if ultima_tendencia else 0,
        "cache_individual_total": int(len(cache)),
        "detalle_total": int(len(detalle_visual)),
        "log_total": int(len(log)),
        "cache": df_to_records(tendencia.tail(20).sort_index(ascending=False)),
        "detalle": df_to_records(detalle_visual.tail(200).sort_index(ascending=False)),
        # GLOBAL: nunca depende del mes seleccionado.
        "guardadas": _senales_guardadas_resumen(ano),
    })


@app.route("/api/senales/actualizar", methods=["POST"])
def api_actualizar_tendencias():
    auth = login_required_api()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    try:
        ano = int(data.get("ano"))
        mes = int(data.get("mes"))
        ciudad = data.get("ciudad", "Todas")
        tendencia = actualizar_tendencia_mensual(ano, mes, ciudad, forzar=True)
        return jsonify({"ok": True, "tendencia": tendencia})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/senales/limpiar", methods=["POST"])
def api_limpiar_senales():
    auth = login_required_api()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    try:
        ano = int(data.get("ano"))
        mes = int(data.get("mes"))
        ciudad = data.get("ciudad", "Todas")
        resultado_local = limpiar_cache_local_filtrado(ano, mes, ciudad)
        respuesta = {"ok": True, "local": resultado_local, "postgres": None}
        if bool(data.get("postgres")):
            ok, msg = limpiar_cache_postgres_filtrado(ano, mes, ciudad)
            respuesta["postgres"] = {"ok": ok, "mensaje": msg}
        return jsonify(respuesta)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/metricas")
def api_metricas():
    auth = login_required_api()
    if auth:
        return auth
    metadata = load_metadata()
    holdout = metadata.get("metricas_holdout", [])
    temporal = metadata.get("metricas_validacion_temporal", {})
    return jsonify({
        "ok": True,
        "metadata": metadata,
        "holdout": holdout,
        "temporal": temporal,
        "matriz_confusion_url": url_for("reporte_matriz_confusion"),
        "matrices_csv": [
            {"nombre": "Holdout 80/20", "url": url_for("reporte_archivo", filename="holdout_80_20_matriz_confusion.csv")},
            {"nombre": "Validación temporal", "url": url_for("reporte_archivo", filename="validacion_temporal_2024_2025_a_2026_matriz_confusion.csv")},
        ],
    })


@app.route("/reportes/matriz-confusion.png")
def reporte_matriz_confusion():
    redirect_response = login_required_view()
    if redirect_response:
        return redirect_response
    return send_from_directory(REPORTES_DIR, "matriz_confusion_final.png")


@app.route("/reportes/<path:filename>")
def reporte_archivo(filename: str):
    redirect_response = login_required_view()
    if redirect_response:
        return redirect_response
    permitidos = {
        "holdout_80_20_matriz_confusion.csv",
        "validacion_temporal_2024_2025_a_2026_matriz_confusion.csv",
    }
    if filename not in permitidos:
        return "Archivo no permitido", 404
    return send_from_directory(REPORTES_DIR, filename, as_attachment=True)


@app.errorhandler(404)
def not_found(_):
    if not session.get("auth"):
        return redirect(url_for("login"))
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
