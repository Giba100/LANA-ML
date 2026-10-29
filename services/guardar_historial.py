from pathlib import Path
from datetime import datetime
import os
import pandas as pd
from dotenv import load_dotenv

try:
    import psycopg2
except Exception:
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=True)

HIST_PATH = ROOT / 'data' / 'historial_predicciones.csv'

COLUMNS = [
    'fecha_prediccion','id_ejecucion','filtro_ciudad','proceso','ano','mes','ciudad','ranking','zona_comercial','tipo_prenda',
    'cliente_principal','tipo_cliente','color','estilo','material_principal','motivo_comercial',
    'prediccion_historica','confianza_historica','score_historico','score_digital','score_color','score_estilo',
    'color_sugerido','estilo_sugerido','top_colores','recomendacion_digital','rol_senal_digital',
    'prediccion_final','score_final','peso_historico','peso_digital','ajuste_digital','ajuste_estacional',
    'fuente_senal','plataformas_consultadas','desde_cache','id_senal'
]


def _db_connect():
    if psycopg2 is None:
        return None
    url = os.getenv('DATABASE_URL', '').strip()
    if not url:
        return None
    try:
        return psycopg2.connect(url)
    except Exception:
        return None


def _columnas_tabla(conn, tabla):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
            """,
            (tabla,)
        )
        return {r[0] for r in cur.fetchall()}


def _guardar_historial_db(row):
    """Guarda en PostgreSQL si DATABASE_URL está configurado.

    Es dinámico para soportar tanto la tabla historial inicial como la tabla corregida:
    si existe tipo_prediccion, lo llena; si existe ranking, lo deja null; si existe confianza_final,
    guarda score_final ahí. Así no se rompe por pequeñas diferencias de esquema.
    """
    conn = _db_connect()
    if conn is None:
        return False
    try:
        cols_existentes = _columnas_tabla(conn, 'historial')
        if not cols_existentes:
            return False

        valores_posibles = {
            'fecha_prediccion': row.get('fecha_prediccion'),
            'id_ejecucion': row.get('id_ejecucion'),
            'filtro_ciudad': row.get('filtro_ciudad'),
            'proceso': row.get('proceso'),
            'tipo_prediccion': row.get('proceso'),
            'ano': row.get('ano'),
            'mes': row.get('mes'),
            'ciudad': row.get('ciudad'),
            'zona_comercial': row.get('zona_comercial'),
            'tipo_prenda': row.get('tipo_prenda'),
            'cliente_principal': row.get('cliente_principal'),
            'tipo_cliente': row.get('tipo_cliente'),
            'color': row.get('color'),
            'estilo': row.get('estilo'),
            'material_principal': row.get('material_principal'),
            'motivo_comercial': row.get('motivo_comercial'),
            'ranking': row.get('ranking'),
            'prediccion_historica': row.get('prediccion_historica'),
            'confianza_historica': row.get('confianza_historica'),
            'score_historico': row.get('score_historico'),
            'score_digital': row.get('score_digital'),
            'score_color': row.get('score_color'),
            'score_estilo': row.get('score_estilo'),
            'color_sugerido': row.get('color_sugerido'),
            'estilo_sugerido': row.get('estilo_sugerido'),
            'top_colores': row.get('top_colores'),
            'recomendacion_digital': row.get('recomendacion_digital'),
            'rol_senal_digital': row.get('rol_senal_digital'),
            'prediccion_final': row.get('prediccion_final'),
            'confianza_final': row.get('score_final'),
            'score_final': row.get('score_final'),
            'peso_historico': row.get('peso_historico'),
            'peso_digital': row.get('peso_digital'),
            'ajuste_digital': row.get('ajuste_digital'),
            'ajuste_estacional': row.get('ajuste_estacional'),
            'fuente_senal': row.get('fuente_senal'),
            'plataformas_consultadas': row.get('plataformas_consultadas'),
            'desde_cache': row.get('desde_cache'),
            'explicacion': f"Predicción {row.get('proceso')} generada por modelo histórico y señal digital ({row.get('fuente_senal')}).",
            'id_senal': row.get('id_senal'),
        }

        columnas = []
        valores = []
        for col, val in valores_posibles.items():
            if col in cols_existentes and col != 'id':
                columnas.append(col)
                valores.append(val)

        if not columnas:
            return False

        placeholders = ','.join(['%s'] * len(columnas))
        columnas_sql = ','.join(columnas)
        sql = f"INSERT INTO historial ({columnas_sql}) VALUES ({placeholders})"
        with conn, conn.cursor() as cur:
            cur.execute(sql, valores)
        return True
    except Exception:
        return False
    finally:
        conn.close()


def guardar_prediccion(proceso, registro, resultado_hist, senal, fusion, ranking=None, id_ejecucion=None, filtro_ciudad=None):
    row = {
        'fecha_prediccion': datetime.now().isoformat(timespec='seconds'),
        'id_ejecucion': id_ejecucion,
        'filtro_ciudad': filtro_ciudad,
        'proceso': proceso,
        **{k: registro.get(k) for k in ['ano','mes','ciudad','zona_comercial','tipo_prenda','cliente_principal','tipo_cliente','color','estilo','material_principal','motivo_comercial']},
        'ranking': ranking,
        'prediccion_historica': resultado_hist.get('prediccion_historica'),
        'confianza_historica': resultado_hist.get('confianza_historica'),
        'score_historico': resultado_hist.get('score_historico'),
        'score_digital': senal.get('score_digital'),
        'score_color': senal.get('score_color'),
        'score_estilo': senal.get('score_estilo'),
        'color_sugerido': senal.get('color_sugerido'),
        'estilo_sugerido': senal.get('estilo_sugerido'),
        'top_colores': senal.get('top_colores'),
        'recomendacion_digital': senal.get('recomendacion_digital'),
        'rol_senal_digital': senal.get('rol_senal_digital'),
        'prediccion_final': fusion.get('prediccion_final'),
        'score_final': fusion.get('score_final'),
        'peso_historico': fusion.get('peso_historico'),
        'peso_digital': fusion.get('peso_digital'),
        'ajuste_digital': fusion.get('ajuste_digital'),
        'ajuste_estacional': fusion.get('ajuste_estacional'),
        'fuente_senal': senal.get('fuente'),
        'plataformas_consultadas': senal.get('plataformas_consultadas'),
        'desde_cache': senal.get('desde_cache'),
        'id_senal': senal.get('id_senal'),
    }

    # 1) Historial local CSV: siempre se guarda para que el prototipo funcione sin DB.
    if HIST_PATH.exists():
        try:
            df = pd.read_csv(HIST_PATH)
        except Exception:
            df = pd.DataFrame(columns=COLUMNS)
    else:
        df = pd.DataFrame(columns=COLUMNS)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    nueva_fila = pd.DataFrame([{col: row.get(col) for col in COLUMNS}])
    if df.empty:
        df = nueva_fila
    else:
        df = pd.concat([df[COLUMNS], nueva_fila], ignore_index=True)
    df[COLUMNS].to_csv(HIST_PATH, index=False, encoding='utf-8-sig')

    # 2) Historial PostgreSQL: se guarda si DATABASE_URL apunta a la base correcta.
    _guardar_historial_db(row)
    return row


def leer_historial(limit=100):
    if not HIST_PATH.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(HIST_PATH)
    except Exception:
        return pd.DataFrame(columns=COLUMNS)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[COLUMNS].tail(limit).sort_index(ascending=False)
