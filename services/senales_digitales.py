from pathlib import Path
import os
import json
import hashlib
import time
import unicodedata
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import load_dotenv

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
except Exception:  # PostgreSQL es opcional para que el prototipo corra aun sin DB.
    psycopg2 = None
    RealDictCursor = None
    Json = None

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=True)

CACHE_PATH = ROOT / 'data' / 'senales_cache.csv'
DETALLE_PATH = ROOT / 'data' / 'senales_detalle_plataformas.csv'
APIFY_LOG_PATH = ROOT / 'data' / 'apify_diagnostico_log.csv'
TENDENCIAS_PATH = ROOT / 'data' / 'tendencias_mensuales_cache.csv'
CONFIG_PATH = ROOT / 'config' / 'config_senales_digitales.json'

MESES_DIGITALES_ALTOS = {3, 4, 5, 6, 9, 10, 11, 12}
MESES_PRENDA_ESPECIAL = {1, 2, 7, 8}
FUENTE_SIMULACION = 'Simulación local controlada'

PLATAFORMAS_DEFAULT = ['tiktok', 'pinterest', 'instagram']
PESOS_PLATAFORMAS = {'tiktok': 0.50, 'pinterest': 0.30, 'instagram': 0.20}
INTENTOS_PLATAFORMA = {'tiktok': 3, 'pinterest': 2, 'instagram': 1}
ACTORES_DEFAULT = {
    # Identificadores en formato API: usuario~actor.
    'instagram': 'apify~instagram-search-scraper',
    'tiktok': 'clockworks~tiktok-scraper',
    'pinterest': 'memo23~pinterest-scraper',
}
TEMPLATES_DEFAULT = {
    # Instagram Search Scraper: búsqueda por hashtag.
    'instagram': '{"search":"{query}","searchType":"popular","searchLimit":8}',
    # TikTok Scraper de Clockworks: búsqueda por palabra clave en videos.
    'tiktok': '{"searchQueries":["{query}"],"searchSection":"/video","resultsPerPage":8,"videoSearchSorting":"MOST_RELEVANT","videoSearchDateFilter":"PAST_MONTH"}',
    # Pinterest Scraper de memo23: busca pines, no tableros; parámetros del esquema oficial.
    'pinterest': '{"searchQueries":["{query}"],"searchScope":"pins","maxItems":20}',
}

COLORES = [
    'beige', 'caqui', 'celeste', 'gris', 'guindo', 'verde militar', 'morado', 'negro',
    'blanco', 'rosado', 'cafe', 'café', 'azul marino', 'crema', 'rojo', 'mostaza'
]
ESTILOS = {
    'formal': ['formal', 'oficina', 'profesional', 'elegante', 'institucional', 'uniforme'],
    'casual': ['casual', 'diario', 'urbano casual', 'street', 'outfit', 'moderno'],
    'artesanal': ['artesanal', 'andino', 'tradicional', 'tejido', 'hecho a mano', 'alpaca'],
    'urbano': ['urbano', 'juvenil', 'streetwear', 'moderno', 'tendencia', 'fashion'],
    'funcional': ['funcional', 'abrigo', 'frio', 'frío', 'invierno', 'caliente', 'protección'],
}


def _env_bool(name, default=False):
    val = os.getenv(name, str(default)).strip().lower()
    return val in {'1', 'true', 'yes', 'si', 'sí', 'on'}


def _usar_apify(forzado=False):
    """Indica si se puede consultar Apify.

    - forzado=True: el usuario eligió 'Extraer datos nuevos con Apify' desde la web.
      En ese caso basta con tener APIFY_TOKEN configurado; no es necesario USAR_APIFY=true.
    - forzado=False: respeta la configuración tradicional del .env: USAR_APIFY=true.
    """
    token = bool(os.getenv('APIFY_TOKEN', '').strip())
    if forzado:
        return token
    return _env_bool('USAR_APIFY', False) and token


def _forzar_apify():
    return _env_bool('FORZAR_APIFY', False)


def _load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _normalizar_texto(texto):
    texto = str(texto or '').lower()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    return texto


def _cache_columns():
    return [
        'ano', 'mes', 'ciudad', 'tipo_prenda', 'color', 'estilo', 'material_principal', 'consulta',
        'menciones', 'interacciones', 'publicaciones', 'score_digital', 'score_color', 'score_estilo',
        'color_sugerido', 'estilo_sugerido', 'nivel_popularidad', 'fuente', 'plataformas_consultadas',
        'detalle_plataformas', 'fecha_extraccion'
    ]


def _detalle_columns():
    return [
        'fecha_extraccion', 'ano', 'mes', 'ciudad', 'plataforma', 'actor_id', 'estado', 'consulta',
        'tipo_prenda', 'color', 'estilo', 'material_principal', 'publicaciones', 'menciones',
        'interacciones', 'score_digital', 'score_color', 'score_estilo', 'color_sugerido', 'estilo_sugerido',
        'mensaje'
    ]


def _read_cache():
    if CACHE_PATH.exists():
        try:
            df = pd.read_csv(CACHE_PATH)
            for col in _cache_columns():
                if col not in df.columns:
                    df[col] = 'Todas' if col == 'ciudad' else None
            return df[_cache_columns()]
        except Exception:
            return pd.DataFrame(columns=_cache_columns())
    return pd.DataFrame(columns=_cache_columns())


def _write_cache(df):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df[_cache_columns()].to_csv(CACHE_PATH, index=False, encoding='utf-8-sig')


def _append_detalle_plataformas(rows):
    if not rows:
        return
    DETALLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    nuevo = pd.DataFrame(rows)
    for col in _detalle_columns():
        if col not in nuevo.columns:
            nuevo[col] = None
    if DETALLE_PATH.exists():
        try:
            actual = pd.read_csv(DETALLE_PATH)
        except Exception:
            actual = pd.DataFrame(columns=_detalle_columns())
    else:
        actual = pd.DataFrame(columns=_detalle_columns())
    for col in _detalle_columns():
        if col not in actual.columns:
            actual[col] = None
    pd.concat([actual[_detalle_columns()], nuevo[_detalle_columns()]], ignore_index=True).to_csv(
        DETALLE_PATH, index=False, encoding='utf-8-sig'
    )


def _log_apify(estado, query, plataforma='', actor_id='', status_code='', detalle='', url=''):
    APIFY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        'fecha': datetime.now().isoformat(timespec='seconds'),
        'estado': estado,
        'plataforma': plataforma,
        'actor_id': actor_id,
        'status_code': status_code,
        'query': query,
        'detalle': str(detalle)[:1200],
        # No se guarda el token real en el log.
        'url': url.split('?token=')[0][:300] if url else '',
    }
    if APIFY_LOG_PATH.exists():
        try:
            df = pd.read_csv(APIFY_LOG_PATH)
        except Exception:
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(APIFY_LOG_PATH, index=False, encoding='utf-8-sig')


def _nivel(score):
    if score >= 75:
        return 'Alto'
    if score >= 50:
        return 'Medio'
    return 'Bajo'


def _score_local_deterministico(ano, mes, plataforma, tipo_prenda, color, estilo, material_principal):
    """Modo local para probar el prototipo sin gastar Apify o cuando la API falle."""
    key = f'{ano}-{mes}-{plataforma}-{tipo_prenda}-{color}-{estilo}-{material_principal}'.encode('utf-8')
    h = int(hashlib.sha256(key).hexdigest()[:8], 16)
    base = 35 + (h % 30)  # 35-64
    if int(mes) in MESES_DIGITALES_ALTOS:
        base += 14
    if estilo in ['urbano', 'artesanal', 'casual'] and int(mes) in [3, 4, 9, 10, 11, 12]:
        base += 8
    if estilo in ['formal', 'casual'] and int(mes) in [5, 10, 11]:
        base += 6
    if estilo == 'funcional' and int(mes) in [5, 6]:
        base += 5
    # En meses escolares/militares la señal digital existe, pero no debe dominar la prenda.
    if 'escolar' in str(tipo_prenda) and int(mes) in [2, 8]:
        base += 6
    if 'militar' in str(tipo_prenda) and int(mes) in [1, 7]:
        base += 6
    if color in ['azul marino', 'gris', 'negro', 'beige', 'caqui', 'verde militar']:
        base += 3
    # Reglas institucionales: la señal digital no contradice colores/estilos típicos.
    if 'militar' in str(tipo_prenda) and int(mes) in [1, 7]:
        if color in ['verde militar', 'caqui', 'negro']:
            base += 12
        if estilo in ['formal', 'funcional']:
            base += 10
    if 'escolar' in str(tipo_prenda) and int(mes) in [2, 8]:
        if color in ['azul marino', 'gris', 'negro']:
            base += 10
        if estilo in ['formal', 'funcional']:
            base += 9
    if 'profesional' in str(tipo_prenda):
        if color in ['negro', 'gris', 'azul marino', 'beige']:
            base += 6
        if estilo == 'formal':
            base += 8
    return max(20, min(98, base))


def _extraer_numero(item, keys):
    for k in keys:
        try:
            v = item.get(k)
            if v is None:
                continue
            if isinstance(v, str):
                v = v.replace(',', '').replace('K', '000').replace('k', '000')
            return int(float(v))
        except Exception:
            pass
    return 0


def _contar_colores_y_estilos(texto):
    texto_norm = _normalizar_texto(texto)
    color_counts = {}
    for color in COLORES:
        c = _normalizar_texto(color)
        color_counts[color.replace('é', 'e')] = texto_norm.count(c)

    estilo_counts = {}
    for estilo, kws in ESTILOS.items():
        estilo_counts[estilo] = sum(texto_norm.count(_normalizar_texto(k)) for k in kws)
    return color_counts, estilo_counts


def _max_key(counts, fallback):
    if not counts:
        return fallback
    k, v = max(counts.items(), key=lambda item: item[1])
    return k if v > 0 else fallback


def _resumir_items_apify(data, query, plataforma, color_objetivo, estilo_objetivo):
    publicaciones = len(data) if isinstance(data, list) else 0
    texto = json.dumps(data, ensure_ascii=False).lower()[:120000]
    query_norm = _normalizar_texto(query)
    palabras = [p for p in query_norm.split() if len(p) > 3]
    texto_norm = _normalizar_texto(texto)
    menciones = sum(texto_norm.count(p) for p in palabras[:8])
    menciones = max(publicaciones, menciones)

    interacciones = 0
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            interacciones += _extraer_numero(item, [
                'likes', 'likeCount', 'likesCount', 'diggCount', 'hearts', 'heartCount',
                'comments', 'commentCount', 'commentsCount', 'shares', 'shareCount',
                'views', 'viewCount', 'playCount', 'videoViewCount', 'repinCount',
                'saveCount', 'reactionCount', 'postsCount', 'followersCount', 'followingCount'
            ])
    if interacciones <= 0:
        interacciones = publicaciones * 15 + menciones * 3

    color_counts, estilo_counts = _contar_colores_y_estilos(texto)
    color_norm = color_objetivo.replace('é', 'e')
    color_match = int(color_counts.get(color_norm, 0))
    estilo_match = int(estilo_counts.get(estilo_objetivo, 0))
    color_sugerido = _max_key(color_counts, color_objetivo)
    estilo_sugerido = _max_key(estilo_counts, estilo_objetivo)

    score_base = min(100, 30 + publicaciones * 4 + menciones * 1.6 + min(25, interacciones / 350))
    score_color = min(100, 35 + color_match * 13 + min(25, publicaciones * 2))
    score_estilo = min(100, 35 + estilo_match * 13 + min(25, publicaciones * 2))
    score = min(100, 0.50 * score_base + 0.25 * score_color + 0.25 * score_estilo)

    return {
        'plataforma': plataforma,
        'menciones': int(menciones),
        'interacciones': int(interacciones),
        'publicaciones': int(publicaciones),
        'score_digital': int(round(score)),
        'score_color': int(round(score_color)),
        'score_estilo': int(round(score_estilo)),
        'color_sugerido': color_sugerido,
        'estilo_sugerido': estilo_sugerido,
        'fuente': 'Apify',
        'estado': 'OK',
        'color_counts': color_counts,
        'estilo_counts': estilo_counts,
    }


def _normalizar_actor_id(actor_id):
    actor_id = (actor_id or '').strip()
    if '/' in actor_id and '~' not in actor_id:
        actor_id = actor_id.replace('/', '~', 1)
    return actor_id


def _plataformas_activas():
    raw = os.getenv('APIFY_PLATAFORMAS', '').strip()
    if raw:
        plataformas = [p.strip().lower() for p in raw.split(',') if p.strip()]
    else:
        plataformas = PLATAFORMAS_DEFAULT[:]
    return [p for p in plataformas if p in ACTORES_DEFAULT]


def _actor_plataforma(plataforma):
    env_name = f'APIFY_{plataforma.upper()}_ACTOR_ID'
    actor_id = os.getenv(env_name, '').strip()
    if not actor_id and plataforma == 'instagram':
        actor_id = os.getenv('APIFY_ACTOR_ID', '').strip()
    if not actor_id:
        actor_id = ACTORES_DEFAULT.get(plataforma, '')
    return _normalizar_actor_id(actor_id)


def _preparar_payload_plataforma(plataforma, query):
    env_name = f'APIFY_{plataforma.upper()}_INPUT_TEMPLATE'
    input_template = os.getenv(env_name, '').strip()
    if not input_template:
        if plataforma == 'instagram' and os.getenv('APIFY_ACTOR_INPUT_TEMPLATE', '').strip():
            input_template = os.getenv('APIFY_ACTOR_INPUT_TEMPLATE', '').strip()
        else:
            input_template = TEMPLATES_DEFAULT[plataforma]
    texto = input_template.replace('{query}', query.replace('"', '\\"'))
    return json.loads(texto)


def _intentar_apify_plataforma(query, plataforma, color, estilo):
    """Ejecuta un actor sin bloquear una sola petición HTTP durante toda la extracción.

    Inicia el actor, consulta su estado y descarga su dataset solo cuando termina.
    No repite una ejecución que sigue RUNNING y conserva el formato de salida
    esperado por las predicciones y por la caché existente.
    """
    token = os.getenv('APIFY_TOKEN', '').strip()
    actor_id = _actor_plataforma(plataforma)
    if not token or not actor_id:
        _log_apify('NO_CONFIGURADO', query, plataforma, actor_id, '', 'Falta APIFY_TOKEN o actor de plataforma.')
        return None

    try:
        payload = _preparar_payload_plataforma(plataforma, query)
    except Exception as exc:
        _log_apify('ERROR_INPUT_TEMPLATE', query, plataforma, actor_id, '', exc)
        return None

    # Límite por ejecución, no por petición HTTP. Cada consulta HTTP es corta.
    max_secs = max(60, int(os.getenv('APIFY_MAX_TOTAL_SECS', '1800')))
    poll_secs = max(5, int(os.getenv('APIFY_POLL_SECS', '15')))
    actor_url = quote(actor_id, safe='~')
    base_url = 'https://api.apify.com/v2'
    auth = {'Authorization': f'Bearer {token}'}
    start_url = f'{base_url}/acts/{actor_url}/runs'
    run_id = None
    last_url = start_url

    try:
        _log_apify('INTENTANDO', query, plataforma, actor_id, '', f'Iniciando actor. Payload={payload}', start_url)
        response = requests.post(start_url, json=payload, headers=auth, timeout=(15, 60))
        if response.status_code not in (200, 201):
            _log_apify('ERROR_HTTP', query, plataforma, actor_id, response.status_code, response.text[:1200], start_url)
            return None
        run = (response.json() or {}).get('data') or {}
        run_id = run.get('id')
        if not run_id:
            _log_apify('ERROR_RESPUESTA', query, plataforma, actor_id, response.status_code, 'Apify no devolvió run id.', start_url)
            return None

        deadline = time.monotonic() + max_secs
        status_url = f'{base_url}/actor-runs/{quote(str(run_id), safe="")}'
        estado = str(run.get('status') or '').upper()
        errores_estado = 0
        while estado not in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
            restante = deadline - time.monotonic()
            if restante <= 0:
                _log_apify('TIEMPO_AGOTADO', query, plataforma, actor_id, '', f'Run {run_id} sigue activo tras {max_secs}s. Consultar Apify Runs; no se inicia duplicado.', status_url)
                return None
            time.sleep(min(poll_secs, restante))
            last_url = status_url
            try:
                status_response = requests.get(status_url, headers=auth, timeout=(15, 30))
                status_response.raise_for_status()
                run = (status_response.json() or {}).get('data') or {}
                estado = str(run.get('status') or '').upper()
                errores_estado = 0
            except (requests.RequestException, ValueError) as exc:
                errores_estado += 1
                if errores_estado >= 5:
                    _log_apify('ERROR_SEGUIMIENTO', query, plataforma, actor_id, '', f'Run {run_id}: {exc}', status_url)
                    return None
                continue

        if estado != 'SUCCEEDED':
            _log_apify('ERROR_ACTOR', query, plataforma, actor_id, estado, f'Run {run_id} terminó en {estado}. Revisar su log en Apify.', status_url)
            return None

        dataset_id = run.get('defaultDatasetId')
        if not dataset_id:
            _log_apify('SIN_DATOS', query, plataforma, actor_id, '', f'Run {run_id} sin dataset.', status_url)
            return None

        dataset_url = f'{base_url}/datasets/{quote(str(dataset_id), safe="")}/items'
        last_url = dataset_url
        # Los actores actuales suelen devolver pocos elementos. Paginación evita
        # perder datos cuando se aumenta resultsPerPage/limit.
        data = []
        offset = 0
        max_items = max(1, int(os.getenv('APIFY_MAX_ITEMS', '500')))
        while offset < max_items:
            limite = min(250, max_items - offset)
            item_response = requests.get(dataset_url, headers=auth,
                                         params={'offset': offset, 'limit': limite, 'clean': 'true'},
                                         timeout=(15, 90))
            item_response.raise_for_status()
            pagina = item_response.json()
            if not isinstance(pagina, list):
                _log_apify('ERROR_RESPUESTA', query, plataforma, actor_id, '', f'Dataset {dataset_id} no contiene lista.', dataset_url)
                return None
            data.extend(pagina)
            if len(pagina) < limite:
                break
            offset += len(pagina)

        if not data:
            _log_apify('SIN_DATOS', query, plataforma, actor_id, '', f'Run {run_id} terminó correctamente pero devolvió 0 elementos.', dataset_url)
            return None
        # Un dataset sin publicaciones utilizables no debe considerarse exitoso.
        data = [item for item in data if isinstance(item, dict) and item]
        if not data:
            _log_apify('SIN_DATOS', query, plataforma, actor_id, '', f'Run {run_id} sin elementos válidos.', dataset_url)
            return None
        resumen = _resumir_items_apify(data, query, plataforma, color, estilo)
        _log_apify('OK', query, plataforma, actor_id, 200,
                   f'Run={run_id} Items={len(data)} score={resumen["score_digital"]}', dataset_url)
        return resumen
    except (requests.RequestException, ValueError, TypeError) as exc:
        _log_apify('ERROR_EXCEPCION', query, plataforma, actor_id, '',
                   f'Run={run_id or "sin iniciar"}: {repr(exc)}', last_url)
        return None


def _consultas_plataforma_mensual(plataforma, ciudad='Todas'):
    lugar = 'Bolivia' if str(ciudad) == 'Todas' else f'Bolivia {ciudad}'
    # Consultas más cortas y específicas producen mejores resultados que una frase larga.
    if plataforma == 'instagram':
        # Tres intentos cortos: moda, color y tejido. Son más robustos que una frase larga.
        return [
            f'moda lana {lugar}',
            f'colores moda invierno {lugar}',
            f'tejidos lana alpaca {lugar}',
        ]
    if plataforma == 'tiktok':
        return [
            f'moda lana {lugar}',
            f'colores tendencia invierno {lugar}',
            f'tejidos lana alpaca {lugar}',
        ]
    if plataforma == 'pinterest':
        # Búsquedas breves para encontrar pines sobre prendas y sus atributos.
        return [
            'chompas de lana',
            'tejidos de alpaca',
            'colores moda tejidos',
        ]
    return [f'ropa de lana {lugar} moda colores estilos']


def _intentar_apify_plataforma_robusta(plataforma, ciudad, color='beige', estilo='casual'):
    """Consulta cada plataforma con una política distinta.

    TikTok es la fuente principal (hasta 3 intentos), Pinterest complementaria
    (hasta 2) e Instagram complementaria (1). Los fallos se conservan únicamente
    en el diagnóstico técnico y no perjudican el agregado de las fuentes válidas.
    """
    consultas = _consultas_plataforma_mensual(plataforma, ciudad)
    max_intentos = int(INTENTOS_PLATAFORMA.get(plataforma, len(consultas)))
    for consulta in consultas[:max_intentos]:
        resultado = _intentar_apify_plataforma(consulta, plataforma, color, estilo)
        if resultado is not None and int(resultado.get('publicaciones', 0) or 0) > 0:
            resultado['consulta_usada'] = consulta
            return resultado
    return None

def _agregar_resumenes(resumenes, query, fallback_color, fallback_estilo):
    """Combina solo fuentes exitosas con ponderación dinámica.

    Pesos base: TikTok 50 %, Pinterest 30 %, Instagram 20 %. Si una fuente no
    devuelve datos, su peso se redistribuye proporcionalmente entre las que sí
    respondieron. Un fallo nunca aporta cero ni reduce artificialmente la señal.
    """
    if not resumenes:
        return None

    # Orden estable para que TikTok aparezca primero cuando está disponible.
    orden = {'tiktok': 0, 'pinterest': 1, 'instagram': 2}
    resumenes = sorted(resumenes, key=lambda r: orden.get(str(r.get('plataforma','')).lower(), 99))
    pesos_base = [float(PESOS_PLATAFORMAS.get(str(r.get('plataforma','')).lower(), 0.0)) for r in resumenes]
    total_peso = sum(pesos_base) or float(len(resumenes))
    pesos = [(pb / total_peso) if sum(pesos_base) else (1.0 / len(resumenes)) for pb in pesos_base]

    publicaciones = sum(int(r.get('publicaciones', 0) or 0) for r in resumenes)
    menciones = sum(int(r.get('menciones', 0) or 0) for r in resumenes)
    interacciones = sum(int(r.get('interacciones', 0) or 0) for r in resumenes)
    score_digital = round(sum(float(r.get('score_digital', 0) or 0) * w for r, w in zip(resumenes, pesos)))
    score_color = round(sum(float(r.get('score_color', 0) or 0) * w for r, w in zip(resumenes, pesos)))
    score_estilo = round(sum(float(r.get('score_estilo', 0) or 0) * w for r, w in zip(resumenes, pesos)))

    color_counts = {}
    estilo_counts = {}
    for r, w in zip(resumenes, pesos):
        for k, v in (r.get('color_counts') or {}).items():
            color_counts[k] = color_counts.get(k, 0.0) + float(v or 0) * w
        for k, v in (r.get('estilo_counts') or {}).items():
            estilo_counts[k] = estilo_counts.get(k, 0.0) + float(v or 0) * w
        if not r.get('color_counts'):
            k = r.get('color_sugerido') or fallback_color
            color_counts[k] = color_counts.get(k, 0.0) + float(r.get('score_color',0) or 0) * w
        if not r.get('estilo_counts'):
            k = r.get('estilo_sugerido') or fallback_estilo
            estilo_counts[k] = estilo_counts.get(k, 0.0) + float(r.get('score_estilo',0) or 0) * w

    color_sugerido = _max_key(color_counts, fallback_color)
    estilo_sugerido = _max_key(estilo_counts, fallback_estilo)
    nombres = {'tiktok':'TikTok', 'pinterest':'Pinterest', 'instagram':'Instagram'}
    plataformas = ', '.join(nombres.get(str(r.get('plataforma','')).lower(), str(r.get('plataforma','')).capitalize()) for r in resumenes)
    detalle = []
    for r, w in zip(resumenes, pesos):
        detalle.append({
            'plataforma': r.get('plataforma'),
            'peso_aplicado': round(w * 100, 1),
            'publicaciones': r.get('publicaciones', 0),
            'menciones': r.get('menciones', 0),
            'interacciones': r.get('interacciones', 0),
            'score_digital': r.get('score_digital', 0),
            'score_color': r.get('score_color', 0),
            'score_estilo': r.get('score_estilo', 0),
            'color_sugerido': r.get('color_sugerido', fallback_color),
            'estilo_sugerido': r.get('estilo_sugerido', fallback_estilo),
        })

    return {
        'menciones': int(menciones),
        'interacciones': int(interacciones),
        'publicaciones': int(publicaciones),
        'score_digital': int(score_digital),
        'score_color': int(score_color),
        'score_estilo': int(score_estilo),
        'color_sugerido': color_sugerido,
        'estilo_sugerido': estilo_sugerido,
        'fuente': plataformas,
        'plataformas_consultadas': plataformas,
        'detalle_plataformas': json.dumps(detalle, ensure_ascii=False),
        'color_counts': color_counts,
        'estilo_counts': estilo_counts,
    }


def _intentar_apify_multiplataforma(ano, mes, ciudad, tipo_prenda, color, estilo, material_principal, forzar_apify=False):
    if not _usar_apify(forzado=forzar_apify):
        if forzar_apify:
            _log_apify('NO_CONFIGURADO', '', '', '', '', 'El usuario pidió extraer con Apify, pero falta APIFY_TOKEN en .env.')
        else:
            _log_apify('NO_CONFIGURADO', '', '', '', '', 'Falta USAR_APIFY=true o APIFY_TOKEN.')
        return None, []

    # La consulta está orientada EXCLUSIVAMENTE a señales de color/estilo.
    # No se incluye tipo_prenda en la búsqueda: la prenda ya viene definida por las reglas de negocio mensuales.
    query = f'ropa de lana color {color} estilo {estilo} {material_principal} tendencia moda Bolivia La Paz El Alto'
    resumenes_ok = []
    detalle_rows = []
    fecha = datetime.now().isoformat(timespec='seconds')

    plataformas = _plataformas_activas()
    # Las plataformas se consultan en paralelo: el tiempo total se aproxima al actor más lento,
    # en lugar de sumar Instagram + TikTok + Pinterest.
    with ThreadPoolExecutor(max_workers=max(1, len(plataformas))) as executor:
        futuros = {executor.submit(_intentar_apify_plataforma, query, p, color, estilo): p for p in plataformas}
        for futuro in as_completed(futuros):
            plataforma = futuros[futuro]
            actor_id = _actor_plataforma(plataforma)
            try:
                resumen = futuro.result()
            except Exception:
                resumen = None
            if resumen is not None:
                resumenes_ok.append(resumen)
                estado = 'OK'
                msg = 'Consulta real Apify correcta.'
            else:
                estado = 'ERROR_O_SIN_DATOS'
                msg = 'No se obtuvo respuesta válida; revisar apify_diagnostico_log.csv.'
                resumen = {
                    'publicaciones': 0, 'menciones': 0, 'interacciones': 0,
                    'score_digital': 0, 'score_color': 0, 'score_estilo': 0,
                    'color_sugerido': color, 'estilo_sugerido': estilo,
                }
            detalle_rows.append({
                'fecha_extraccion': fecha, 'ano': ano, 'mes': mes, 'ciudad': ciudad,
                'plataforma': plataforma, 'actor_id': actor_id, 'estado': estado, 'consulta': query,
                'tipo_prenda': tipo_prenda, 'color': color, 'estilo': estilo,
                'material_principal': material_principal, 'publicaciones': resumen['publicaciones'],
                'menciones': resumen['menciones'], 'interacciones': resumen['interacciones'],
                'score_digital': resumen['score_digital'], 'score_color': resumen['score_color'],
                'score_estilo': resumen['score_estilo'], 'color_sugerido': resumen['color_sugerido'],
                'estilo_sugerido': resumen['estilo_sugerido'], 'mensaje': msg,
            })

    agregado = _agregar_resumenes(resumenes_ok, query, color, estilo)
    return agregado, detalle_rows




def _eliminar_periodo_csv(path, ano, mes, ciudad='Todas'):
    """Elimina solo el caché del periodo indicado, conservando los demás meses."""
    path = Path(path)
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0
    if df.empty or 'ano' not in df.columns or 'mes' not in df.columns:
        return 0
    temp = df.copy()
    anos = pd.to_numeric(temp['ano'], errors='coerce')
    meses = pd.to_numeric(temp['mes'], errors='coerce')
    mask = anos.eq(int(ano)) & meses.eq(int(mes))
    if str(ciudad) != 'Todas' and 'ciudad' in temp.columns:
        mask = mask & temp['ciudad'].astype(str).eq(str(ciudad))
    eliminados = int(mask.sum())
    temp.loc[~mask].to_csv(path, index=False, encoding='utf-8-sig')
    return eliminados

def _read_tendencias_mensuales():
    cols = ['ano','mes','ciudad','consulta','score_digital','publicaciones','menciones','interacciones',
            'color_counts','estilo_counts','color_sugerido','estilo_sugerido','fuente',
            'plataformas_consultadas','fecha_extraccion']
    if not TENDENCIAS_PATH.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(TENDENCIAS_PATH)
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


def _write_tendencias_mensuales(df):
    TENDENCIAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TENDENCIAS_PATH, index=False, encoding='utf-8-sig')


def _tendencia_vigente(row, dias=14):
    """Solo informa si conviene actualizar; NO invalida ni elimina el caché."""
    try:
        fecha = datetime.fromisoformat(str(row.get('fecha_extraccion')))
        return datetime.now() - fecha <= timedelta(days=int(dias))
    except Exception:
        return False


def actualizar_tendencia_mensual(ano, mes, ciudad='Todas', forzar=True):
    """Hace UNA extracción mensual reutilizable para color y estilo.

    Ejecuta Instagram/TikTok/Pinterest en paralelo y conserva el agregado de forma
    persistente hasta que el usuario lo limpie o pulse Actualizar tendencias.
    APIFY_RECOMENDACION_DIAS (14 por defecto) solo indica cuándo conviene refrescar.
    """
    ano, mes = int(ano), int(mes)
    ciudad = ciudad or 'Todas'
    cache = _read_tendencias_mensuales()
    mask = (cache['ano'].astype(str).eq(str(ano)) & cache['mes'].astype(str).eq(str(mes)) &
            cache['ciudad'].astype(str).eq(str(ciudad))) if not cache.empty else []
    if len(cache) and hasattr(mask, 'any') and mask.any() and not forzar:
        # El caché mensual no caduca automáticamente. Se conserva hasta que el usuario
        # lo limpie o solicite explícitamente una actualización con Apify.
        vigente = cache[mask].iloc[-1].to_dict()
        vigente['desde_cache'] = True
        return vigente

    if not _usar_apify(forzado=True):
        raise RuntimeError('APIFY_TOKEN no está configurado en el archivo .env.')

    lugar = 'Bolivia La Paz El Alto' if ciudad == 'Todas' else f'Bolivia {ciudad}'
    query = f'Tendencia mensual de moda en lana - {lugar}'
    plataformas = _plataformas_activas()
    resumenes = []
    detalle_rows = []
    fecha = datetime.now().isoformat(timespec='seconds')
    with ThreadPoolExecutor(max_workers=max(1, len(plataformas))) as executor:
        futuros = {executor.submit(_intentar_apify_plataforma_robusta, p, ciudad, 'beige', 'casual'): p for p in plataformas}
        for futuro in as_completed(futuros):
            plataforma = futuros[futuro]
            try:
                r = futuro.result()
            except Exception:
                r = None
            if r:
                resumenes.append(r)
                estado, msg = 'OK', f"Tendencia mensual extraída correctamente ({int(r.get('publicaciones',0) or 0)} resultados)."
            else:
                r = {'publicaciones':0,'menciones':0,'interacciones':0,'score_digital':0,'score_color':0,'score_estilo':0,
                     'color_sugerido':'beige','estilo_sugerido':'casual'}
                estado, msg = 'SIN_DATOS_O_ERROR', 'No se obtuvo respuesta válida después de los intentos alternativos; revisa el diagnóstico.'
            detalle_rows.append({
                'fecha_extraccion': fecha, 'ano': ano, 'mes': mes, 'ciudad': ciudad,
                'plataforma': plataforma, 'actor_id': _actor_plataforma(plataforma), 'estado': estado, 'consulta': r.get('consulta_usada', query) if isinstance(r, dict) else query,
                'tipo_prenda': 'TENDENCIA MENSUAL', 'color': 'Todos', 'estilo': 'Todos', 'material_principal': 'lana',
                'publicaciones': r['publicaciones'], 'menciones': r['menciones'], 'interacciones': r['interacciones'],
                'score_digital': r['score_digital'], 'score_color': r['score_color'], 'score_estilo': r['score_estilo'],
                'color_sugerido': r['color_sugerido'], 'estilo_sugerido': r['estilo_sugerido'], 'mensaje': msg,
            })
    agregado = _agregar_resumenes(resumenes, query, 'beige', 'casual')
    if not agregado:
        raise RuntimeError('Apify no devolvió datos válidos en ninguna plataforma. Revisa el diagnóstico.')

    # La extracción fue correcta: recién ahora se reemplaza el caché anterior del
    # periodo seleccionado. Así, si Apify falla, la tendencia previa permanece intacta.
    _eliminar_periodo_csv(CACHE_PATH, ano, mes, ciudad)
    _eliminar_periodo_csv(DETALLE_PATH, ano, mes, ciudad)
    row = {
        'ano': ano, 'mes': mes, 'ciudad': ciudad, 'consulta': query, 'score_digital': agregado['score_digital'],
        'publicaciones': agregado['publicaciones'], 'menciones': agregado['menciones'], 'interacciones': agregado['interacciones'],
        'color_counts': json.dumps(agregado.get('color_counts', {}), ensure_ascii=False),
        'estilo_counts': json.dumps(agregado.get('estilo_counts', {}), ensure_ascii=False),
        'color_sugerido': agregado.get('color_sugerido', 'beige'), 'estilo_sugerido': agregado.get('estilo_sugerido', 'casual'),
        'fuente': agregado['fuente'], 'plataformas_consultadas': agregado.get('plataformas_consultadas',''),
        'fecha_extraccion': fecha, 'desde_cache': False,
    }
    if len(cache) and hasattr(mask, 'any') and mask.any():
        cache = cache[~mask].copy()
    cache = pd.concat([cache, pd.DataFrame([row])], ignore_index=True)
    _write_tendencias_mensuales(cache)
    _append_detalle_plataformas(detalle_rows)
    return row


def obtener_tendencia_mensual(ano, mes, ciudad='Todas', actualizar_si_falta=False):
    """Obtiene una tendencia mensual sin acceso a red.

    Si se solicita ``Todas`` y no existe una fila literal con esa ciudad, consolida
    las tendencias guardadas de La Paz/El Alto para el mismo periodo.
    """
    cache = _read_tendencias_mensuales()
    ano_i, mes_i = int(ano), int(mes)
    ciudad_s = str(ciudad or 'Todas')
    if not cache.empty:
        periodo = (cache['ano'].astype(str).eq(str(ano_i)) & cache['mes'].astype(str).eq(str(mes_i)))
        exacta = periodo & cache['ciudad'].astype(str).eq(ciudad_s)
        if exacta.any():
            row = cache[exacta].iloc[-1].to_dict()
            row['desde_cache'] = True
            return row

        # La interfaz usa normalmente Ciudad=Todas. En ese caso se combinan las
        # señales reales ya guardadas de las ciudades del periodo, sin consultar Apify.
        if ciudad_s.lower() in {'todas', 'todos'} and periodo.any():
            sub = cache[periodo].copy()
            if len(sub):
                def _parse_counts(v):
                    if isinstance(v, dict):
                        return v
                    try:
                        return json.loads(v or '{}')
                    except Exception:
                        return {}
                cc, ec = {}, {}
                for _, r in sub.iterrows():
                    for k, v in _parse_counts(r.get('color_counts')).items():
                        cc[k] = cc.get(k, 0.0) + float(v or 0)
                    for k, v in _parse_counts(r.get('estilo_counts')).items():
                        ec[k] = ec.get(k, 0.0) + float(v or 0)
                def _avg(col):
                    vals = pd.to_numeric(sub[col], errors='coerce').dropna() if col in sub.columns else pd.Series(dtype=float)
                    return int(round(float(vals.mean()))) if len(vals) else 0
                color_sug = max(cc.items(), key=lambda x: x[1])[0] if cc and max(cc.values()) > 0 else str(sub.iloc[-1].get('color_sugerido') or 'beige')
                estilo_sug = max(ec.items(), key=lambda x: x[1])[0] if ec and max(ec.values()) > 0 else str(sub.iloc[-1].get('estilo_sugerido') or 'casual')
                plataformas = sorted({p.strip() for x in sub.get('plataformas_consultadas', pd.Series(dtype=str)).fillna('').astype(str) for p in x.split(',') if p.strip()})
                return {
                    'ano': ano_i, 'mes': mes_i, 'ciudad': 'Todas',
                    'consulta': 'Consolidado de caché mensual La Paz + El Alto',
                    'score_digital': _avg('score_digital'),
                    'publicaciones': int(pd.to_numeric(sub.get('publicaciones', 0), errors='coerce').fillna(0).sum()),
                    'menciones': int(pd.to_numeric(sub.get('menciones', 0), errors='coerce').fillna(0).sum()),
                    'interacciones': int(pd.to_numeric(sub.get('interacciones', 0), errors='coerce').fillna(0).sum()),
                    'color_counts': json.dumps(cc, ensure_ascii=False),
                    'estilo_counts': json.dumps(ec, ensure_ascii=False),
                    'color_sugerido': color_sug, 'estilo_sugerido': estilo_sug,
                    'fuente': 'Caché mensual', 'plataformas_consultadas': ', '.join(plataformas),
                    'fecha_extraccion': str(sub['fecha_extraccion'].dropna().astype(str).max()) if 'fecha_extraccion' in sub.columns else '',
                    'desde_cache': True,
                }
    if actualizar_si_falta:
        return actualizar_tendencia_mensual(ano_i, mes_i, ciudad_s, forzar=True)
    return None


def _senal_desde_tendencia_mensual(tendencia, color, estilo):
    def parse(v):
        if isinstance(v, dict): return v
        try: return json.loads(v or '{}')
        except Exception: return {}
    cc, ec = parse(tendencia.get('color_counts')), parse(tendencia.get('estilo_counts'))
    maxc = max([int(v or 0) for v in cc.values()] or [1])
    maxe = max([int(v or 0) for v in ec.values()] or [1])
    ckey = str(color).replace('é','e')
    cm = int(cc.get(ckey, cc.get(color, 0)) or 0)
    em = int(ec.get(str(estilo), 0) or 0)
    # Si una palabra no aparece, conserva una señal base moderada; la regla de negocio sigue mandando.
    score_color = int(round(40 + 60 * cm / maxc)) if maxc else 40
    score_estilo = int(round(40 + 60 * em / maxe)) if maxe else 40
    return {
        'menciones': int(tendencia.get('menciones') or 0), 'interacciones': int(tendencia.get('interacciones') or 0),
        'publicaciones': int(tendencia.get('publicaciones') or 0), 'score_digital': int(tendencia.get('score_digital') or 0),
        'score_color': score_color, 'score_estilo': score_estilo, 'color_sugerido': color, 'estilo_sugerido': estilo,
        'nivel_popularidad': _nivel(int(tendencia.get('score_digital') or 0)), 'fuente': str(tendencia.get('fuente') or 'Apify') + ' · caché mensual',
        'plataformas_consultadas': tendencia.get('plataformas_consultadas',''), 'detalle_plataformas': '',
        'fecha_extraccion': tendencia.get('fecha_extraccion'), 'desde_cache': bool(tendencia.get('desde_cache', True)), 'id_senal': None,
    }

def _db_connect():
    if psycopg2 is None:
        return None
    url = os.getenv('DATABASE_URL', '').strip()
    if not url:
        return None
    try:
        return psycopg2.connect(url)
    except Exception as exc:
        _log_apify('ERROR_DB_CONEXION', '', '', '', '', repr(exc))
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


def _buscar_cache_db(ano, mes, ciudad, tipo_prenda, color, estilo, material_principal):
    conn = _db_connect()
    if conn is None:
        return None
    try:
        cols_existentes = _columnas_tabla(conn, 'senales')
        if not cols_existentes:
            return None
        cols = [c for c in [
            'id', 'ano', 'mes', 'ciudad', 'tipo_prenda', 'color', 'estilo', 'material_principal',
            'palabras_clave', 'publicaciones_encontradas', 'interacciones_totales',
            'indice_popularidad_digital', 'nivel_popularidad', 'herramienta', 'plataforma',
            'detalle_plataformas', 'fecha_consulta', 'score_color', 'score_estilo',
            'color_sugerido', 'estilo_sugerido'
        ] if c in cols_existentes]
        where = ['ano=%s', 'mes=%s', 'tipo_prenda=%s', 'color=%s', 'estilo=%s', 'material_principal=%s']
        params = [ano, mes, tipo_prenda, color, estilo, material_principal]
        if 'ciudad' in cols_existentes:
            where.insert(2, 'ciudad=%s')
            params.insert(2, ciudad)
        sql = f"SELECT {', '.join(cols)} FROM senales WHERE {' AND '.join(where)} ORDER BY fecha_consulta DESC LIMIT 1"
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            r = cur.fetchone()
        if not r:
            return None
        return {
            'id_senal': r.get('id'),
            'ano': r.get('ano', ano), 'mes': r.get('mes', mes), 'ciudad': r.get('ciudad', ciudad),
            'tipo_prenda': r.get('tipo_prenda', tipo_prenda), 'color': r.get('color', color),
            'estilo': r.get('estilo', estilo), 'material_principal': r.get('material_principal', material_principal),
            'consulta': r.get('palabras_clave') or '',
            'menciones': int(r.get('publicaciones_encontradas') or 0),
            'interacciones': int(float(r.get('interacciones_totales') or 0)),
            'publicaciones': int(r.get('publicaciones_encontradas') or 0),
            'score_digital': int(float(r.get('indice_popularidad_digital') or 0)),
            'score_color': int(float(r.get('score_color') or r.get('indice_popularidad_digital') or 0)),
            'score_estilo': int(float(r.get('score_estilo') or r.get('indice_popularidad_digital') or 0)),
            'color_sugerido': r.get('color_sugerido') or color,
            'estilo_sugerido': r.get('estilo_sugerido') or estilo,
            'nivel_popularidad': r.get('nivel_popularidad') or _nivel(float(r.get('indice_popularidad_digital') or 0)),
            'fuente': r.get('herramienta') or 'PostgreSQL cache',
            'plataformas_consultadas': r.get('plataforma') or '',
            'detalle_plataformas': json.dumps(r.get('detalle_plataformas'), ensure_ascii=False) if r.get('detalle_plataformas') is not None else '',
            'fecha_extraccion': str(r.get('fecha_consulta')),
            'desde_cache': True,
        }
    except Exception as exc:
        _log_apify('ERROR_DB_CACHE_SELECT', '', '', '', '', repr(exc))
        return None
    finally:
        conn.close()


def _guardar_cache_db(row):
    conn = _db_connect()
    if conn is None:
        return None
    try:
        cols_existentes = _columnas_tabla(conn, 'senales')
        if not cols_existentes:
            return None

        base_values = {
            'ano': int(row['ano']),
            'mes': int(row['mes']),
            'ciudad': row.get('ciudad'),
            'herramienta': row.get('fuente'),
            'plataforma': row.get('plataformas_consultadas'),
            'tipo_prenda': row.get('tipo_prenda'),
            'color': row.get('color'),
            'estilo': row.get('estilo'),
            'material_principal': row.get('material_principal'),
            'palabras_clave': row.get('consulta'),
            'hashtags': row.get('consulta'),
            'publicaciones_encontradas': int(row.get('publicaciones') or 0),
            'interacciones_totales': float(row.get('interacciones') or 0),
            'promedio_interaccion': float(row.get('interacciones') or 0) / max(1, int(row.get('publicaciones') or 0)),
            'indice_popularidad_digital': float(row.get('score_digital') or 0),
            'score_color': float(row.get('score_color') or 0),
            'score_estilo': float(row.get('score_estilo') or 0),
            'color_sugerido': row.get('color_sugerido'),
            'estilo_sugerido': row.get('estilo_sugerido'),
            'nivel_popularidad': row.get('nivel_popularidad'),
            'detalle_plataformas': Json(json.loads(row.get('detalle_plataformas') or '[]')) if Json else row.get('detalle_plataformas'),
            'mensaje': 'Señal digital mensual consultada/cacheada desde Instagram, TikTok y Pinterest para apoyar color y estilo.'
        }

        # Buscar si ya existe la señal mensual para no duplicar/gastar.
        where = ['ano=%s', 'mes=%s', 'tipo_prenda=%s', 'color=%s', 'estilo=%s', 'material_principal=%s']
        params = [base_values['ano'], base_values['mes'], base_values['tipo_prenda'], base_values['color'], base_values['estilo'], base_values['material_principal']]
        if 'ciudad' in cols_existentes:
            where.insert(2, 'ciudad=%s')
            params.insert(2, base_values['ciudad'])
        select_sql = f"SELECT id FROM senales WHERE {' AND '.join(where)} ORDER BY fecha_consulta DESC LIMIT 1"
        with conn.cursor() as cur:
            cur.execute(select_sql, params)
            existe = cur.fetchone()

            columnas = [c for c in base_values.keys() if c in cols_existentes]
            valores = [base_values[c] for c in columnas]

            if existe:
                set_cols = [c for c in columnas if c not in {'id'}]
                set_sql = ', '.join([f'{c}=%s' for c in set_cols])
                update_sql = f"UPDATE senales SET fecha_consulta=CURRENT_TIMESTAMP, {set_sql} WHERE id=%s RETURNING id"
                cur.execute(update_sql, [base_values[c] for c in set_cols] + [existe[0]])
            else:
                placeholders = ','.join(['%s'] * len(columnas))
                insert_sql = f"INSERT INTO senales ({', '.join(columnas)}) VALUES ({placeholders}) RETURNING id"
                cur.execute(insert_sql, valores)
            r = cur.fetchone()
            conn.commit()
            return r[0] if r else None
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_apify('ERROR_DB_SENALES_INSERT', row.get('consulta', ''), '', '', '', repr(exc))
        return None
    finally:
        conn.close()


def _row_simulacion(ano, mes, ciudad, tipo_prenda, color, estilo, material_principal):
    detalle = []
    scores = []
    for plataforma in PLATAFORMAS_DEFAULT:
        score = _score_local_deterministico(ano, mes, plataforma, tipo_prenda, color, estilo, material_principal)
        scores.append(score)
        detalle.append({
            'plataforma': plataforma, 'publicaciones': int(max(3, score // 8)),
            'menciones': int(score * 2.0), 'interacciones': int(score * 10.5),
            'score_digital': int(score), 'score_color': int(max(25, score - 5)),
            'score_estilo': int(max(25, score - 4)), 'color_sugerido': color, 'estilo_sugerido': estilo,
        })
    score_prom = int(round(sum(scores) / len(scores)))
    return {
        'menciones': int(score_prom * 2.4),
        'interacciones': int(score_prom * 11.0),
        'publicaciones': int(max(5, score_prom // 6)),
        'score_digital': score_prom,
        'score_color': int(max(25, score_prom - 5)),
        'score_estilo': int(max(25, score_prom - 4)),
        'color_sugerido': color,
        'estilo_sugerido': estilo,
        'fuente': FUENTE_SIMULACION,
        'plataformas_consultadas': 'Instagram, TikTok, Pinterest',
        'detalle_plataformas': json.dumps(detalle, ensure_ascii=False),
    }


def _normalizar_modo_senal(modo_senal=None):
    modo = str(modo_senal or 'cache').strip().lower()
    equivalencias = {
        'extraer_apify': 'apify',
        'forzar_apify': 'apify',
        'nuevo_apify': 'apify',
        'real': 'apify',
        'usar_cache': 'cache',
        'solo_cache': 'cache',
        'cache_local': 'cache',
        'automatico': 'auto',
        'automático': 'auto',
    }
    modo = equivalencias.get(modo, modo)
    return modo if modo in {'cache', 'apify', 'auto'} else 'cache'


def obtener_senal_digital(ano, mes, tipo_prenda, color, estilo, material_principal, ciudad='Todas', modo_senal='cache'):
    ano = int(ano)
    mes = int(mes)
    ciudad = ciudad or 'Todas'
    modo_senal = _normalizar_modo_senal(modo_senal)
    query = f'ropa de lana color {color} estilo {estilo} {material_principal} tendencia moda Bolivia La Paz El Alto'
    extraer_apify = modo_senal == 'apify'
    auto_apify = modo_senal == 'auto'
    solo_cache = modo_senal == 'cache'
    usar_apify = False if solo_cache else _usar_apify(forzado=(extraer_apify or auto_apify))
    forzar = extraer_apify or _forzar_apify()

    # MODO DEMOSTRACIÓN RÁPIDA: en modo caché jamás se consulta la red ni se
    # escribe una señal simulada por cada combinación. Primero se intenta usar
    # el único caché mensual del periodo. Si no existe, se devuelve una señal
    # local neutra en memoria. Esto mantiene la predicción por debajo de unos
    # pocos segundos incluso sin internet o mientras se comparte pantalla.
    if solo_cache and not forzar:
        tendencia = obtener_tendencia_mensual(ano, mes, ciudad, actualizar_si_falta=False)
        if tendencia is not None:
            return _senal_desde_tendencia_mensual(tendencia, color, estilo)
        score_local = _score_local_deterministico(ano, mes, 'cache-local', tipo_prenda, color, estilo, material_principal)
        return {
            'menciones': 0, 'interacciones': 0, 'publicaciones': 0,
            'score_digital': int(score_local),
            'score_color': int(max(35, score_local - 4)),
            'score_estilo': int(max(35, score_local - 3)),
            'color_sugerido': color, 'estilo_sugerido': estilo,
            'nivel_popularidad': _nivel(int(score_local)),
            'fuente': 'Modo caché local', 'plataformas_consultadas': '',
            'detalle_plataformas': '', 'fecha_extraccion': '',
            'desde_cache': True, 'id_senal': None,
        }

    # Flujo mensual optimizado:
    # - cache: reutiliza únicamente señales ya guardadas; no consume Apify.
    # - auto: reutiliza la tendencia mensual si existe y, solo si falta, consulta Apify una vez.
    # - apify: fuerza una extracción/actualización mensual con Apify.
    if auto_apify:
        tendencia = obtener_tendencia_mensual(ano, mes, ciudad, actualizar_si_falta=False)
        if tendencia is None:
            if not os.getenv('APIFY_TOKEN', '').strip():
                raise RuntimeError('No existe caché para este mes y APIFY_TOKEN no está configurado en .env.')
            tendencia = actualizar_tendencia_mensual(ano, mes, ciudad, forzar=True)
        if tendencia is not None:
            return _senal_desde_tendencia_mensual(tendencia, color, estilo)

    if extraer_apify:
        if not os.getenv('APIFY_TOKEN', '').strip():
            raise RuntimeError('APIFY_TOKEN no está configurado en .env.')
        tendencia = actualizar_tendencia_mensual(ano, mes, ciudad, forzar=True)
        if tendencia is not None:
            return _senal_desde_tendencia_mensual(tendencia, color, estilo)

    cache = _read_cache()
    mask = (
        cache['ano'].astype(str).eq(str(ano)) &
        cache['mes'].astype(str).eq(str(mes)) &
        cache['ciudad'].astype(str).eq(str(ciudad)) &
        cache['tipo_prenda'].astype(str).eq(str(tipo_prenda)) &
        cache['color'].astype(str).eq(str(color)) &
        cache['estilo'].astype(str).eq(str(estilo)) &
        cache['material_principal'].astype(str).eq(str(material_principal))
    ) if not cache.empty else []

    # 1) Caché local CSV mensual. Si fue simulación y Apify está activo, se intenta Apify real.
    if len(cache) and mask.any() and not forzar:
        row = cache[mask].iloc[-1].to_dict()
        if not (usar_apify and str(row.get('fuente')) == FUENTE_SIMULACION):
            row['desde_cache'] = True
            row.setdefault('id_senal', None)
            return row

    # 2) Caché PostgreSQL mensual.
    row_db = _buscar_cache_db(ano, mes, ciudad, tipo_prenda, color, estilo, material_principal)
    if row_db is not None and not forzar:
        if not (usar_apify and str(row_db.get('fuente')) == FUENTE_SIMULACION):
            cache = pd.concat([cache, pd.DataFrame([{k: row_db.get(k) for k in _cache_columns()}])], ignore_index=True)
            _write_cache(cache[_cache_columns()])
            return row_db

    # 3) Consulta real a Apify en tres plataformas solo si el usuario lo eligió
    #    o si el modo automático lo permite por .env. En modo caché no se consume Apify.
    if solo_cache and not forzar:
        agregado, detalle_rows = None, []
        _log_apify('MODO_CACHE', query, '', '', '', 'Se usó modo caché/local desde la interfaz; no se consultó Apify.')
    else:
        agregado, detalle_rows = _intentar_apify_multiplataforma(ano, mes, ciudad, tipo_prenda, color, estilo, material_principal, forzar_apify=extraer_apify)

    if agregado is None:
        agregado = _row_simulacion(ano, mes, ciudad, tipo_prenda, color, estilo, material_principal)
        detalle_rows = [{
            'fecha_extraccion': datetime.now().isoformat(timespec='seconds'), 'ano': ano, 'mes': mes, 'ciudad': ciudad,
            'plataforma': p, 'actor_id': _actor_plataforma(p), 'estado': 'SIMULADO', 'consulta': query,
            'tipo_prenda': tipo_prenda, 'color': color, 'estilo': estilo, 'material_principal': material_principal,
            'publicaciones': 0, 'menciones': 0, 'interacciones': 0,
            'score_digital': 0, 'score_color': 0, 'score_estilo': 0,
            'color_sugerido': color, 'estilo_sugerido': estilo,
            'mensaje': 'Señal simulada porque Apify no está configurado o falló.',
        } for p in PLATAFORMAS_DEFAULT]

    row = {
        'ano': ano,
        'mes': mes,
        'ciudad': ciudad,
        'tipo_prenda': tipo_prenda,
        'color': color,
        'estilo': estilo,
        'material_principal': material_principal,
        'consulta': query,
        'menciones': int(agregado['menciones']),
        'interacciones': int(agregado['interacciones']),
        'publicaciones': int(agregado['publicaciones']),
        'score_digital': int(agregado['score_digital']),
        'score_color': int(agregado.get('score_color', agregado['score_digital'])),
        'score_estilo': int(agregado.get('score_estilo', agregado['score_digital'])),
        'color_sugerido': agregado.get('color_sugerido', color),
        'estilo_sugerido': agregado.get('estilo_sugerido', estilo),
        'nivel_popularidad': _nivel(agregado['score_digital']),
        'fuente': agregado['fuente'],
        'plataformas_consultadas': agregado.get('plataformas_consultadas', ''),
        'detalle_plataformas': agregado.get('detalle_plataformas', ''),
        'fecha_extraccion': datetime.now().isoformat(timespec='seconds'),
        'modo_senal': modo_senal,
    }

    # Caché CSV mensual agregado.
    hay_cache = False
    try:
        hay_cache = bool(len(cache) and hasattr(mask, 'any') and mask.any())
    except Exception:
        hay_cache = False
    if hay_cache:
        for col in _cache_columns():
            cache.loc[mask, col] = row.get(col)
    else:
        cache = pd.concat([cache, pd.DataFrame([row])], ignore_index=True)
    _write_cache(cache[_cache_columns()])

    # CSV detalle por plataforma.
    _append_detalle_plataformas(detalle_rows)

    # PostgreSQL mensual agregado.
    id_senal = _guardar_cache_db(row)
    row['id_senal'] = id_senal
    row['desde_cache'] = False
    return row


def fusionar_prediccion_historica_y_digital(score_historico, score_digital, mes, score_color=None, score_estilo=None):
    config = _load_config()
    mes = int(mes)
    if mes in set(config.get('meses_peso_digital_alto', list(MESES_DIGITALES_ALTOS))):
        peso_h = float(config.get('peso_historico_meses_digitales', 0.55))
        peso_d = float(config.get('peso_digital_meses_digitales', 0.45))
    else:
        peso_h = float(config.get('peso_historico_meses_normales', 0.85))
        peso_d = float(config.get('peso_digital_meses_normales', 0.15))

    # La señal digital se usa principalmente para color y estilo.
    # La popularidad general apoya, pero no debe reemplazar al patrón histórico de prendas.
    digital = float(score_digital)
    if score_color is not None and score_estilo is not None:
        w_pop = float(config.get('peso_popularidad_dentro_senal', 0.25))
        w_color = float(config.get('peso_color_dentro_senal', 0.40))
        w_estilo = float(config.get('peso_estilo_dentro_senal', 0.35))
        total = max(0.01, w_pop + w_color + w_estilo)
        digital = (w_pop * digital + w_color * float(score_color) + w_estilo * float(score_estilo)) / total

    final = peso_h * float(score_historico) + peso_d * digital
    if final >= 75:
        clase = 'Alta'
    elif final >= 50:
        clase = 'Media'
    else:
        clase = 'Baja'
    return {
        'score_final': round(final, 2),
        'prediccion_final': clase,
        'peso_historico': peso_h,
        'peso_digital': peso_d,
        'ajuste_digital': round(float(digital) - float(score_historico), 2)
    }
