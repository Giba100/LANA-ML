import pandas as pd
import json
import uuid

from modelo.predictor import (
    predecir_registro,
    predecir_registros,
    generar_candidatos_generales,
    generar_candidatos_especificos,
    cargar_dataset,
)
from services.senales_digitales import obtener_senal_digital, fusionar_prediccion_historica_y_digital
from services.guardar_historial import guardar_prediccion


# -----------------------------------------------------------------------------
# Reglas comerciales mensuales basadas en la lógica esperada de la defensa.
# -----------------------------------------------------------------------------
# La prenda se decide principalmente por el comportamiento histórico + el mes.
# Las señales digitales de Instagram, TikTok y Pinterest NO reemplazan esa lógica:
# sirven para reforzar estilo y color dentro de las prendas candidatas.
# -----------------------------------------------------------------------------
REGLAS_MENSUALES_PRENDAS = {
    # Reglas transcritas de la hoja de negocio entregada por el usuario.
    # IMPORTANTE: no existe "chaleco escolar" ni "chaleco militar".
    # Los chalecos solo pueden ser de hombre o de mujer.
    1: {
        'alto': {'prenda militar'},
        'medio': {'chompa de hombre', 'chompa de mujer', 'chompa profesional'},
        'bajo': set(),
    },
    2: {
        'alto': {'chompa escolar'},
        'medio': {'chompa de mujer', 'chaleco de mujer'},
        'bajo': {'prenda militar'},
    },
    3: {
        'alto': {'chompa de hombre'},
        'medio': {'chompa de mujer', 'chaleco de hombre', 'chaleco de mujer'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
    4: {
        # "Prendas variadas (ayuda digital)": Apify ayuda a ordenar color/estilo.
        'alto': {'chalina de lana', 'poncho de lana', 'manta de lana', 'gorro de lana', 'guantes de lana', 'medias de lana', 'chompa profesional'},
        'medio': {'chompa de hombre', 'chompa de mujer', 'chaleco de hombre', 'chaleco de mujer'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
    5: {
        'alto': {'chompa de hombre', 'chompa de mujer'},
        'medio': {'chaleco de hombre', 'chaleco de mujer', 'prenda militar'},
        'bajo': {'chompa escolar'},
    },
    6: {
        'alto': {'chalina de lana', 'poncho de lana', 'manta de lana', 'gorro de lana', 'guantes de lana', 'medias de lana', 'chompa profesional'},
        'medio': {'chompa de hombre', 'chompa de mujer'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
    7: {
        'alto': {'prenda militar'},
        'medio': {'chompa de hombre', 'chompa de mujer', 'chompa escolar'},
        'bajo': set(),
    },
    8: {
        'alto': {'chompa escolar'},
        'medio': {'chompa de hombre', 'chompa de mujer', 'chalina de lana', 'poncho de lana', 'manta de lana', 'gorro de lana', 'guantes de lana', 'medias de lana', 'chompa profesional'},
        'bajo': {'prenda militar'},
    },
    9: {
        'alto': {'chompa de mujer', 'chompa profesional'},
        'medio': {'chompa de hombre', 'chaleco de mujer', 'chaleco de hombre'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
    10: {
        'alto': {'chompa de hombre', 'chompa de mujer'},
        'medio': {'chalina de lana', 'poncho de lana', 'manta de lana', 'gorro de lana', 'guantes de lana', 'medias de lana', 'chompa profesional'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
    11: {
        'alto': {'chompa de hombre', 'chompa de mujer'},
        'medio': {'chalina de lana', 'poncho de lana', 'manta de lana', 'gorro de lana', 'guantes de lana', 'medias de lana', 'chompa profesional'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
    12: {
        'alto': {'chompa de hombre', 'chompa de mujer'},
        'medio': {'chalina de lana', 'poncho de lana', 'manta de lana', 'gorro de lana', 'guantes de lana', 'medias de lana', 'chompa profesional'},
        'bajo': {'prenda militar', 'chompa escolar'},
    },
}

PESO_DEMANDA = {'Alta': 100.0, 'Media': 60.0, 'Baja': 25.0}
MESES_DIGITALES_ALTOS = {3, 4, 5, 6, 9, 10, 11, 12}
MESES_PRENDA_ESPECIAL = {1, 2, 7, 8}  # escolar/militar: la prenda se decide por reglas históricas; Apify apoya poco.


# Reglas de color y estilo para que las recomendaciones sean comercialmente coherentes.
# Apify puede reordenar tendencias, pero estas reglas actúan como piso de negocio.
REGLAS_COLOR_PRENDA = {
    'prenda militar': ['verde militar', 'caqui', 'negro', 'gris', 'azul marino'],
    'chompa escolar': ['azul marino', 'gris', 'negro', 'guindo', 'verde militar'],
    'chompa profesional': ['negro', 'gris', 'azul marino', 'beige', 'crema'],
    'chompa de hombre': ['azul marino', 'gris', 'negro', 'beige', 'cafe'],
    'chaleco de hombre': ['azul marino', 'gris', 'negro', 'beige', 'cafe'],
    'chompa de mujer': ['beige', 'guindo', 'negro', 'crema', 'rosado'],
    'chaleco de mujer': ['beige', 'guindo', 'negro', 'crema', 'rosado'],
}

REGLAS_ESTILO_PRENDA = {
    'prenda militar': ['formal', 'funcional'],
    'chompa escolar': ['formal', 'funcional'],
    'chompa profesional': ['formal', 'casual'],
    'chompa de hombre': ['casual', 'formal', 'urbano'],
    'chaleco de hombre': ['casual', 'formal', 'urbano'],
    'chompa de mujer': ['casual', 'formal', 'urbano'],
    'chaleco de mujer': ['casual', 'formal', 'urbano'],
    'chalina de lana': ['artesanal', 'casual', 'urbano'],
    'poncho de lana': ['artesanal', 'casual', 'urbano'],
    'manta de lana': ['artesanal', 'funcional', 'casual'],
    'gorro de lana': ['casual', 'urbano', 'funcional'],
    'guantes de lana': ['funcional', 'casual', 'urbano'],
    'medias de lana': ['funcional', 'casual'],
}


def _score_regla_color(prenda, color, mes):
    prenda, color, mes = str(prenda), str(color), int(mes)
    preferidos = REGLAS_COLOR_PRENDA.get(prenda, [])
    if color in preferidos:
        # Primer color = 100, segundo = 94, tercero = 88, etc.
        return max(70.0, 100.0 - 6.0 * preferidos.index(color))
    # En prendas variadas la regla mensual deja más libertad a histórico/Apify.
    return 58.0 if mes in MESES_DIGITALES_ALTOS else 50.0


def _score_regla_estilo(prenda, estilo, mes):
    prenda, estilo, mes = str(prenda), str(estilo), int(mes)
    preferidos = REGLAS_ESTILO_PRENDA.get(prenda, [])
    if estilo in preferidos:
        return max(72.0, 100.0 - 7.0 * preferidos.index(estilo))
    return 58.0 if mes in MESES_DIGITALES_ALTOS else 48.0


def _evaluar_historico(registro):
    hist = predecir_registro(registro)
    return {**registro, **hist}


def _aplicar_senal_digital(row, proceso='general', modo_senal='cache'):
    registro = row.to_dict() if hasattr(row, 'to_dict') else dict(row)
    senal = obtener_senal_digital(
        registro['ano'], registro['mes'], registro['tipo_prenda'], registro['color'],
        registro['estilo'], 'lana', ciudad=registro.get('ciudad', 'Todas'),
        modo_senal=modo_senal
    )
    fusion = fusionar_prediccion_historica_y_digital(
        registro['score_historico'], senal['score_digital'], registro['mes'],
        score_color=senal.get('score_color'), score_estilo=senal.get('score_estilo')
    )
    mes = int(registro['mes'])
    color_sugerido = senal.get('color_sugerido') or registro.get('color')
    estilo_sugerido = senal.get('estilo_sugerido') or registro.get('estilo')
    rol = 'Apoyo fuerte en color/estilo' if mes in MESES_DIGITALES_ALTOS else 'Apoyo menor; domina patrón escolar/militar'

    return {
        **registro,
        'score_digital': senal['score_digital'],
        'score_color': senal.get('score_color'),
        'score_estilo': senal.get('score_estilo'),
        'color_sugerido': color_sugerido,
        'estilo_sugerido': estilo_sugerido,
        'recomendacion_digital': f'{color_sugerido} / {estilo_sugerido}',
        'rol_senal_digital': rol,
        'nivel_popularidad': senal['nivel_popularidad'],
        'fuente_senal': senal['fuente'],
        'plataformas_consultadas': senal.get('plataformas_consultadas'),
        'desde_cache': senal['desde_cache'],
        'id_senal': senal.get('id_senal'),
        **fusion,
    }


def _guardar_fila(proceso, row, id_ejecucion=None, filtro_ciudad=None):
    registro = {k: row[k] for k in ['ano','mes','ciudad','zona_comercial','tipo_prenda','cliente_principal','tipo_cliente','color','estilo','motivo_comercial']}
    hist = {'prediccion_historica': row['prediccion_historica'], 'confianza_historica': row['confianza_historica'], 'score_historico': row['score_historico']}
    senal = {
        'score_digital': row['score_digital'],
        'score_color': row.get('score_color') if hasattr(row, 'get') else None,
        'score_estilo': row.get('score_estilo') if hasattr(row, 'get') else None,
        'color_sugerido': row.get('color_sugerido') if hasattr(row, 'get') else None,
        'estilo_sugerido': row.get('estilo_sugerido') if hasattr(row, 'get') else None,
        'top_colores': json.dumps(row.get('top_colores') or [], ensure_ascii=False) if hasattr(row, 'get') else '[]',
        'recomendacion_digital': row.get('recomendacion_digital') if hasattr(row, 'get') else None,
        'rol_senal_digital': row.get('rol_senal_digital') if hasattr(row, 'get') else None,
        'fuente': row['fuente_senal'],
        'plataformas_consultadas': row.get('plataformas_consultadas') if hasattr(row, 'get') else None,
        'desde_cache': row['desde_cache'],
        'id_senal': row.get('id_senal') if hasattr(row, 'get') else None,
    }
    fusion = {
        'prediccion_final': row['prediccion_final'],
        'score_final': row['score_final'],
        'peso_historico': row.get('peso_historico') if hasattr(row, 'get') else None,
        'peso_digital': row.get('peso_digital') if hasattr(row, 'get') else None,
        'ajuste_digital': row.get('ajuste_digital') if hasattr(row, 'get') else None,
        'ajuste_estacional': row.get('ajuste_estacional') if hasattr(row, 'get') else None,
    }
    guardar_prediccion(proceso, registro, hist, senal, fusion, ranking=row.get('ranking') if hasattr(row, 'get') else None, id_ejecucion=id_ejecucion, filtro_ciudad=filtro_ciudad)


def _ctx_mismo_mes(filtros):
    df = cargar_dataset()
    mes = int(filtros.get('mes'))
    ciudad = filtros.get('ciudad')
    ctx = df[df['mes'].astype(int).eq(mes)].copy()

    if ciudad not in [None, '', 'Todas', 'Todos']:
        ctx_ciudad = ctx[ctx['ciudad'].astype(str).eq(str(ciudad))]
        if len(ctx_ciudad):
            ctx = ctx_ciudad

    if len(ctx):
        return ctx
    return df.copy()


def _score_subset_demanda(subset: pd.DataFrame, respaldo: pd.DataFrame) -> float:
    if subset is None or not len(subset):
        subset = respaldo
    if subset is None or not len(subset):
        return 50.0

    valores = subset['demanda_clase'].map(PESO_DEMANDA).fillna(45.0)
    score = float(valores.mean())
    soporte = min(6.0, len(subset) / 25.0)
    return max(20.0, min(100.0, score + soporte))


def _score_contextual(row, ctx: pd.DataFrame, proceso='general') -> float:
    prenda = row['tipo_prenda']
    estilo = row['estilo']
    color = row['color']

    por_prenda = ctx[ctx['tipo_prenda'].astype(str).eq(str(prenda))]
    por_combo = por_prenda[
        por_prenda['estilo'].astype(str).eq(str(estilo)) &
        por_prenda['color'].astype(str).eq(str(color))
    ]

    score_prenda = _score_subset_demanda(por_prenda, ctx)
    score_combo = _score_subset_demanda(por_combo, por_prenda if len(por_prenda) else ctx)

    if proceso == 'general':
        return round(0.82 * score_prenda + 0.18 * score_combo, 2)
    return round(0.35 * score_prenda + 0.65 * score_combo, 2)


def _prioridad_mensual_prenda(prenda, mes):
    reglas = REGLAS_MENSUALES_PRENDAS.get(int(mes), {})
    if prenda in reglas.get('alto', set()):
        return 3
    if prenda in reglas.get('medio', set()):
        return 2
    if prenda in reglas.get('bajo', set()):
        return 0
    return 1


def _score_estacional(row) -> float:
    mes = int(row['mes'])
    prenda = str(row['tipo_prenda'])
    estilo = str(row['estilo'])
    reglas = REGLAS_MENSUALES_PRENDAS.get(mes, {})

    prioridad = _prioridad_mensual_prenda(prenda, mes)
    if prioridad == 3:
        score = 98.0
    elif prioridad == 2:
        score = 76.0
    elif prioridad == 1:
        score = 58.0
    else:
        score = 25.0

    # Afinación por estilo. La señal digital ordena color/estilo, pero la regla del mes evita prendas incoherentes.
    if mes in {2, 8} and prenda == 'chompa escolar':
        score += 10
        if estilo in {'formal', 'funcional'}:
            score += 4
    if mes in {1, 7} and prenda == 'prenda militar' and estilo == 'formal':
        score += 6
    elif mes in {1, 7} and prenda == 'prenda militar' and estilo == 'funcional':
        score += 4
    if mes in {5, 10, 11} and prenda in {'chompa de hombre', 'chompa de mujer', 'chaleco de hombre', 'chaleco de mujer'}:
        if estilo in {'formal', 'casual'}:
            score += 5
        if estilo == 'funcional':
            score -= 5
    if mes in {9} and prenda in {'chompa de mujer', 'chaleco de mujer', 'chompa profesional'} and estilo == 'formal':
        score += 5
    if mes in {12} and estilo in {'artesanal', 'casual'}:
        score += 3

    return max(15.0, min(100.0, score))


def _clase_por_score(score):
    if score >= 75:
        return 'Alta'
    if score >= 50:
        return 'Media'
    return 'Baja'


def _calibrar_ranking(df: pd.DataFrame, filtros: dict, proceso='general') -> pd.DataFrame:
    if df.empty:
        return df

    ctx = _ctx_mismo_mes(filtros)
    df = df.copy()
    df['score_final_base'] = df['score_final'].astype(float)
    df['score_contextual_mes'] = df.apply(lambda r: _score_contextual(r, ctx, proceso), axis=1)
    df['score_estacional'] = df.apply(_score_estacional, axis=1)
    df['prioridad_mensual'] = df['tipo_prenda'].apply(lambda p: _prioridad_mensual_prenda(str(p), int(filtros.get('mes'))))

    # Color y estilo tienen su propio score. En meses institucionales la regla de negocio
    # pesa más; en meses comerciales Apify/histórico conservan mayor libertad.
    mes = int(filtros.get('mes'))
    df['score_color_base'] = pd.to_numeric(df.get('score_color', 0), errors='coerce').fillna(0)
    df['score_estilo_base'] = pd.to_numeric(df.get('score_estilo', 0), errors='coerce').fillna(0)
    df['score_regla_color'] = df.apply(lambda r: _score_regla_color(r['tipo_prenda'], r['color'], mes), axis=1)
    df['score_regla_estilo'] = df.apply(lambda r: _score_regla_estilo(r['tipo_prenda'], r['estilo'], mes), axis=1)
    peso_regla = 0.55 if mes in MESES_PRENDA_ESPECIAL else 0.28
    df['score_color'] = ((1-peso_regla) * df['score_color_base'] + peso_regla * df['score_regla_color']).round(2)
    df['score_estilo'] = ((1-peso_regla) * df['score_estilo_base'] + peso_regla * df['score_regla_estilo']).round(2)

    if proceso == 'general':
        # En meses comerciales generales, Apify pesa más para elegir color/estilo dentro de cada prenda.
        # En meses escolares/militares, las reglas mensuales dominan para no cambiar la prenda esperada.
        w_base, w_ctx, w_est = (0.46, 0.27, 0.27) if mes in MESES_DIGITALES_ALTOS else (0.22, 0.34, 0.44)
    else:
        # En específica la prenda ya está fija, por eso Apify ayuda más a decidir estilo/color.
        w_base, w_ctx, w_est = (0.62, 0.25, 0.13) if mes in MESES_DIGITALES_ALTOS else (0.36, 0.46, 0.18)

    df['score_final'] = (
        w_base * df['score_final_base'] +
        w_ctx * df['score_contextual_mes'] +
        w_est * df['score_estacional']
    ).round(2)

    # Castigo fuerte a prendas marcadas como bajas para el mes.
    mask_baja_mes = df['prioridad_mensual'].eq(0)
    if mask_baja_mes.any():
        df.loc[mask_baja_mes, 'score_final'] = df.loc[mask_baja_mes, 'score_final'].clip(upper=49.0)

    df['prediccion_final'] = df['score_final'].apply(_clase_por_score)
    df['ajuste_estacional'] = (df['score_final'] - df['score_final_base']).round(2)
    return df


def _preseleccionar_por_mes(hist_df: pd.DataFrame, filtros: dict, top_n: int) -> pd.DataFrame:
    """Preselecciona candidatos antes de consultar Apify.

    v8: no elimina de inmediato las combinaciones de color/estilo por prenda.
    En meses digitales altos conserva varias combinaciones por cada prenda para que
    Instagram, TikTok y Pinterest puedan ayudar a ordenar colores y estilos.
    En meses escolares/militares conserva menos combinaciones para evitar gasto y
    mantener la prenda definida por la lógica histórica.
    """
    mes = int(filtros.get('mes'))
    hist_df = hist_df.copy()
    hist_df['prioridad_mensual'] = hist_df['tipo_prenda'].apply(lambda p: _prioridad_mensual_prenda(str(p), mes))
    hist_df['score_regla_estilo_pre'] = hist_df.apply(lambda r: _score_regla_estilo(r['tipo_prenda'], r['estilo'], mes), axis=1)
    if mes in MESES_PRENDA_ESPECIAL:
        hist_df['score_preseleccion'] = (
            0.40 * hist_df['score_historico'].astype(float) +
            0.45 * hist_df.apply(_score_estacional, axis=1) +
            0.15 * hist_df['score_regla_estilo_pre']
        ).round(2)
    else:
        hist_df['score_preseleccion'] = (
            0.45 * hist_df['score_historico'].astype(float) +
            0.45 * hist_df.apply(_score_estacional, axis=1) +
            0.10 * hist_df['score_regla_estilo_pre']
        ).round(2)

    # No se consultan señales digitales para prendas bajas si existen suficientes prendas coherentes.
    coherentes = hist_df[hist_df['prioridad_mensual'] > 0].copy()
    base = coherentes if len(coherentes['tipo_prenda'].unique()) >= top_n else hist_df

    base = base.sort_values(['prioridad_mensual', 'score_preseleccion', 'confianza_historica'], ascending=False)

    # Para mostrar Top 3 de colores por prenda necesitamos diversidad de color.
    # Se conserva una combinación fuerte por color y luego hasta 5 colores por prenda.
    # Esto evita que varias consultas se gasten en el mismo color con estilos distintos.
    colores_por_prenda = 5
    base = base.groupby(['tipo_prenda', 'color'], group_keys=False).head(1)
    base = base.groupby('tipo_prenda', group_keys=False).head(colores_por_prenda)
    max_consultas = max(top_n * colores_por_prenda, 25)
    return base.head(max_consultas).reset_index(drop=True)


def _armar_top5_con_colores(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """Devuelve Top 5 de prendas y, dentro de cada una, Top 3 de colores.

    La prenda se ordena por regla mensual + score final. Los colores y el estilo
    se ordenan de forma independiente para no convertir Apify en selector de prenda.
    """
    if df.empty:
        return df
    ordenar_prenda = ['prioridad_mensual', 'score_final', 'score_contextual_mes', 'score_historico']
    mejores_prendas = (df.sort_values(ordenar_prenda, ascending=False)
                        .drop_duplicates(subset=['tipo_prenda'], keep='first')
                        .head(top_n))
    filas = []
    for _, representante in mejores_prendas.iterrows():
        prenda = representante['tipo_prenda']
        sub = df[df['tipo_prenda'].eq(prenda)].copy()
        # Cada color se evalúa una sola vez con su mejor combinación disponible.
        mes = int(sub.iloc[0]['mes'])
        regla_color = sub.apply(lambda r: _score_regla_color(r['tipo_prenda'], r['color'], mes), axis=1)
        if mes in MESES_PRENDA_ESPECIAL:
            sub['score_color_total'] = (0.35 * pd.to_numeric(sub['score_color'], errors='coerce').fillna(0) +
                                        0.10 * pd.to_numeric(sub['score_digital'], errors='coerce').fillna(0) +
                                        0.10 * pd.to_numeric(sub['score_historico'], errors='coerce').fillna(0) +
                                        0.45 * regla_color).round(2)
        else:
            sub['score_color_total'] = (0.48 * pd.to_numeric(sub['score_color'], errors='coerce').fillna(0) +
                                        0.20 * pd.to_numeric(sub['score_digital'], errors='coerce').fillna(0) +
                                        0.17 * pd.to_numeric(sub['score_historico'], errors='coerce').fillna(0) +
                                        0.15 * regla_color).round(2)
        colores = (sub.sort_values(['score_color_total', 'score_final'], ascending=False)
                     .drop_duplicates(subset=['color'], keep='first')
                     .head(3))
        top_colores = [
            {'ranking': i+1, 'color': str(r['color']), 'score': round(float(r['score_color_total']), 2)}
            for i, (_, r) in enumerate(colores.iterrows())
        ]
        # El estilo recomendado sale de la mejor evidencia de estilo de la prenda.
        estilos = sub.copy()
        estilos['score_estilo_total'] = (0.65 * pd.to_numeric(estilos['score_estilo'], errors='coerce').fillna(0) +
                                         0.20 * pd.to_numeric(estilos['score_digital'], errors='coerce').fillna(0) +
                                         0.15 * pd.to_numeric(estilos['score_historico'], errors='coerce').fillna(0)).round(2)
        mejor_estilo = estilos.sort_values('score_estilo_total', ascending=False).iloc[0]
        estilo_negocio = REGLAS_ESTILO_PRENDA.get(str(prenda), [])
        if str(prenda) in {'prenda militar', 'chompa escolar', 'chompa profesional'} and estilo_negocio:
            estilo_final = estilo_negocio[0]
            score_estilo_final = max(float(mejor_estilo['score_estilo_total']), 90.0)
        else:
            estilo_final = str(mejor_estilo['estilo'])
            score_estilo_final = float(mejor_estilo['score_estilo_total'])
        fila = representante.copy()
        fila['top_colores'] = top_colores
        if top_colores:
            fila['color_sugerido'] = top_colores[0]['color']
            fila['score_color'] = top_colores[0]['score']
        fila['estilo_sugerido'] = estilo_final
        fila['score_estilo'] = round(score_estilo_final, 2)
        fila['recomendacion_digital'] = f"{fila['color_sugerido']} / {fila['estilo_sugerido']}"
        filas.append(fila)
    out = pd.DataFrame(filas).reset_index(drop=True)
    out.insert(0, 'ranking', range(1, len(out)+1))
    return out


def prediccion_general(filtros, top_n=5, modo_senal='cache'):
    candidatos = generar_candidatos_generales(filtros)

    hist_df = pd.DataFrame(predecir_registros(candidatos))
    candidatos_digitales = _preseleccionar_por_mes(hist_df, filtros, top_n)

    evaluados = [_aplicar_senal_digital(row, proceso='general', modo_senal=modo_senal) for _, row in candidatos_digitales.iterrows()]
    df = pd.DataFrame(evaluados)
    df = _calibrar_ranking(df, filtros, proceso='general')

    # Primero se muestran prendas coherentes con el mes; las bajas solo entran si no alcanzan resultados.
    coherentes = df[df['prioridad_mensual'] > 0].copy()
    base = coherentes if coherentes['tipo_prenda'].nunique() >= top_n else df
    df = _armar_top5_con_colores(base, top_n=min(5, int(top_n)))

    id_ejecucion = uuid.uuid4().hex
    filtro_ciudad = filtros.get('ciudad', 'Todas')
    for _, row in df.iterrows():
        _guardar_fila('general', row, id_ejecucion=id_ejecucion, filtro_ciudad=filtro_ciudad)
    return df


def prediccion_especifica(filtros, top_n=5, modo_senal='cache'):
    candidatos = generar_candidatos_especificos(filtros)

    hist_df = pd.DataFrame(predecir_registros(candidatos))
    hist_df = hist_df.sort_values(['score_historico','confianza_historica'], ascending=False)
    hist_df = hist_df.drop_duplicates(subset=['estilo','color'], keep='first')
    candidatos_digitales = hist_df.head(max(15, top_n)).reset_index(drop=True)

    evaluados = [_aplicar_senal_digital(row, proceso='especifica', modo_senal=modo_senal) for _, row in candidatos_digitales.iterrows()]
    df = pd.DataFrame(evaluados)
    df = _calibrar_ranking(df, filtros, proceso='especifica')
    df = df.sort_values(['score_final','score_color','score_estilo','score_contextual_mes','score_historico','score_digital'], ascending=False)
    df = df.drop_duplicates(subset=['estilo','color'], keep='first').head(top_n).reset_index(drop=True)
    df.insert(0, 'ranking', range(1, len(df)+1))

    id_ejecucion = uuid.uuid4().hex
    filtro_ciudad = filtros.get('ciudad', 'Todas')
    for _, row in df.iterrows():
        _guardar_fila('especifica', row, id_ejecucion=id_ejecucion, filtro_ciudad=filtro_ciudad)
    return df
