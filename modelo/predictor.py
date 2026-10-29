from pathlib import Path
import joblib
import pandas as pd
from functools import lru_cache

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / 'modelo' / 'artefactos' / 'modelo_tendencias_lana_final.pkl'
DATASET_PATH = ROOT / 'data' / 'dataset_historico_final_8000.csv'

ORDEN_DEMANDA = {'Alta': 3, 'Media': 2, 'Baja': 1}
VALORES_TODOS = {None, '', 'Todos', 'Todas', 'todo', 'todas'}


@lru_cache(maxsize=1)
def cargar_modelo():
    paquete = joblib.load(MODEL_PATH)
    return paquete['pipeline'], paquete['metadata']


@lru_cache(maxsize=1)
def cargar_dataset():
    return pd.read_csv(DATASET_PATH)


def score_historico_from_proba(probabilidades, clases):
    mapa = dict(zip(clases, probabilidades))
    return float(mapa.get('Alta', 0) * 100 + mapa.get('Media', 0) * 62 + mapa.get('Baja', 0) * 25)


def predecir_registro(registro: dict):
    pipeline, metadata = cargar_modelo()
    feature_columns = metadata['feature_columns']
    df = pd.DataFrame([{c: registro.get(c) for c in feature_columns}])
    pred = pipeline.predict(df)[0]
    prob = pipeline.predict_proba(df)[0]
    clases = list(pipeline.classes_)
    confianza = float(max(prob) * 100)
    score_historico = score_historico_from_proba(prob, clases)
    return {
        'prediccion_historica': pred,
        'confianza_historica': round(confianza, 2),
        'score_historico': round(score_historico, 2),
        'probabilidades': {c: round(float(p) * 100, 2) for c, p in zip(clases, prob)},
    }


def predecir_registros(registros):
    """Predice muchos candidatos en una sola llamada al pipeline.

    Evita cientos de llamadas individuales a ``predict``/``predict_proba`` y
    reduce de forma importante el tiempo de la predicción general.
    """
    if not registros:
        return []
    pipeline, metadata = cargar_modelo()
    feature_columns = metadata['feature_columns']
    df = pd.DataFrame([{c: r.get(c) for c in feature_columns} for r in registros])
    preds = pipeline.predict(df)
    probs = pipeline.predict_proba(df)
    clases = list(pipeline.classes_)
    out = []
    for registro, pred, prob in zip(registros, preds, probs):
        confianza = float(max(prob) * 100)
        score_historico = score_historico_from_proba(prob, clases)
        out.append({
            **registro,
            'prediccion_historica': pred,
            'confianza_historica': round(confianza, 2),
            'score_historico': round(score_historico, 2),
            'probabilidades': {c: round(float(v) * 100, 2) for c, v in zip(clases, prob)},
        })
    return out


def _valor_valido(valor):
    return valor not in VALORES_TODOS


def _contexto_por_filtros(df: pd.DataFrame, filtros: dict) -> pd.DataFrame:
    """Obtiene el contexto histórico para armar candidatos de predicción.

    La interfaz permite seleccionar meses 1-12 para 2026. Como el histórico de 2026 llega
    hasta mayo, si no existe un contexto exacto año-mes, se usa el mismo mes de años
    anteriores. De esta forma, por ejemplo, junio de 2026 se apoya en junio de 2024 y 2025,
    manteniendo la lógica de predicción futura sin agregar columnas al dataset.
    """
    target_ano = filtros.get('ano')
    target_mes = filtros.get('mes')
    target_ciudad = filtros.get('ciudad')

    def aplicar_ciudad(base: pd.DataFrame) -> pd.DataFrame:
        if _valor_valido(target_ciudad):
            return base[base['ciudad'].astype(str).eq(str(target_ciudad))]
        return base

    # 1) Contexto exacto: año + mes + ciudad.
    ctx = df.copy()
    if _valor_valido(target_ano):
        ctx = ctx[ctx['ano'].astype(str).eq(str(target_ano))]
    if _valor_valido(target_mes):
        ctx = ctx[ctx['mes'].astype(str).eq(str(target_mes))]
    ctx = aplicar_ciudad(ctx)
    if len(ctx):
        return ctx

    # 2) Si el año-mes no existe, usar el mismo mes en años históricos anteriores.
    #    Esto permite predecir meses futuros de 2026 con patrones de 2024 y 2025.
    if _valor_valido(target_mes):
        ctx_mes = df[df['mes'].astype(str).eq(str(target_mes))]
        if _valor_valido(target_ano):
            try:
                ctx_mes = ctx_mes[ctx_mes['ano'].astype(int) < int(target_ano)]
            except (TypeError, ValueError):
                pass
        ctx_mes_ciudad = aplicar_ciudad(ctx_mes)
        if len(ctx_mes_ciudad):
            return ctx_mes_ciudad
        if len(ctx_mes):
            return ctx_mes

    # 3) Si tampoco existe el mes, usar el contexto de ciudad.
    ctx_ciudad = aplicar_ciudad(df.copy())
    if len(ctx_ciudad):
        return ctx_ciudad

    # 4) Último respaldo: todo el histórico.
    return df.copy()


def _moda(subset: pd.DataFrame, df: pd.DataFrame, col: str):
    if len(subset) and col in subset.columns and subset[col].notna().any():
        return subset[col].mode()[0]
    return df[col].mode()[0]


def _completar_registro(registro: dict, subset: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Completa internamente las variables ocultas en el formulario.
    No agrega columnas nuevas; solo rellena las columnas existentes del dataset para poder consultar el modelo.
    """
    r = registro.copy()
    for col in ['zona_comercial', 'cliente_principal', 'tipo_cliente', 'motivo_comercial']:
        if not _valor_valido(r.get(col)):
            r[col] = _moda(subset, df, col)
    if not _valor_valido(r.get('ciudad')):
        r['ciudad'] = _moda(subset, df, 'ciudad')
    return r


def generar_candidatos_generales(filtros: dict):
    df = cargar_dataset()
    ctx = _contexto_por_filtros(df, filtros)
    prendas = sorted(df['tipo_prenda'].unique().tolist())
    ciudades = [filtros['ciudad']] if _valor_valido(filtros.get('ciudad')) else sorted(df['ciudad'].unique().tolist())

    candidatos = []
    for ciudad in ciudades:
        ctx_ciudad = ctx[ctx['ciudad'].astype(str).eq(str(ciudad))]
        if not len(ctx_ciudad):
            ctx_ciudad = ctx
        for prenda in prendas:
            hist = ctx_ciudad[ctx_ciudad['tipo_prenda'].eq(prenda)]
            if not len(hist):
                hist = df[df['tipo_prenda'].eq(prenda)]
            top_colores = hist['color'].value_counts().head(6).index.tolist()
            top_estilos = hist['estilo'].value_counts().head(3).index.tolist()
            for estilo in top_estilos:
                for color in top_colores:
                    base = {
                        'ano': int(filtros.get('ano')),
                        'mes': int(filtros.get('mes')),
                        'ciudad': ciudad,
                        'tipo_prenda': prenda,
                        'color': color,
                        'estilo': estilo,
                    }
                    candidatos.append(_completar_registro(base, hist, df))
    return candidatos


def generar_candidatos_especificos(filtros: dict):
    df = cargar_dataset()
    ctx = _contexto_por_filtros(df, filtros)
    prenda = filtros['tipo_prenda']
    ciudades = [filtros['ciudad']] if _valor_valido(filtros.get('ciudad')) else sorted(df['ciudad'].unique().tolist())

    candidatos = []
    for ciudad in ciudades:
        hist = ctx[(ctx['tipo_prenda'].eq(prenda)) & (ctx['ciudad'].astype(str).eq(str(ciudad)))]
        if not len(hist):
            hist = df[df['tipo_prenda'].eq(prenda)]
        colores = hist['color'].value_counts().head(8).index.tolist()
        estilos = hist['estilo'].value_counts().index.tolist()
        for estilo in estilos:
            for color in colores:
                base = {
                    'ano': int(filtros.get('ano')),
                    'mes': int(filtros.get('mes')),
                    'ciudad': ciudad,
                    'tipo_prenda': prenda,
                    'estilo': estilo,
                    'color': color,
                }
                candidatos.append(_completar_registro(base, hist, df))
    return candidatos
