const state = { options: null, currentSection: 'inicio' };
const pageTitles = {
  'inicio': 'Inicio',
  'prediccion': 'Predicción general',
  'color-estilo': 'Análisis de tendencias',
  'historial': 'Historial',
  'dashboard': 'Dashboard',
  'senales': 'Señales digitales',
};
const monthLabels = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'];

function $(selector) { return document.querySelector(selector); }
function $all(selector, scope=document) { return Array.from(scope.querySelectorAll(selector)); }
function asset(path) { return `${window.STATIC_PREFIX || '/static/'}${path}`; }
function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}
function formatNumber(value, decimals = 2) {
  const n = Number(value);
  if (Number.isNaN(n)) return value ?? '';
  return n.toLocaleString('es-BO', { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
}
function badgeClass(value) {
  if (value === 'Alta') return 'high';
  if (value === 'Media') return 'medium';
  if (value === 'Baja') return 'low';
  return 'neutral';
}
function toast(message, type='info') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show ${type}`;
  setTimeout(() => { el.className = 'toast'; }, 3400);
}
function setLoading(form, loading) {
  if (!form) return;
  form.classList.toggle('loading', loading);
  $all('button', form).forEach(btn => btn.disabled = loading);
}
async function api(url, options = {}) {
  const res = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401) {
    window.location.href = '/login';
    return null;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || 'Ocurrió un error en la solicitud.');
  return data;
}
function scoreText(label, value) {
  if (value === undefined || value === null || value === '') return '';
  return `<small class="cell-score">${escapeHtml(label)}: ${formatNumber(value)}</small>`;
}
function colorCell(row) {
  return `<strong>${escapeHtml(row.color_sugerido || row.color || '')}</strong>${scoreText('Score color', row.score_color)}`;
}
function estiloCell(row) {
  return `<strong>${escapeHtml(row.estilo_sugerido || row.estilo || '')}</strong>${scoreText('Score estilo', row.score_estilo)}`;
}
function periodoCell(row) {
  return `<strong>${String(row.mes ?? '').padStart(2,'0')}/${escapeHtml(row.ano ?? '')}</strong><small class="cell-score">${escapeHtml(row.ciudad || 'Todas')}</small>`;
}
function productImage(row) {
  return asset(row.image_path || 'media/prendas/chompa_base.png');
}
const COLOR_HEX = {
  'negro':'#171717','blanco':'#f5f5f3','gris':'#8b8f94','gris oscuro':'#53565b','gris claro':'#c8cbd0',
  'beige':'#d5b894','caqui':'#b6a47a','cafe':'#6f4b36','café':'#6f4b36','marron':'#795548','marrón':'#795548',
  'azul':'#4d78c4','azul marino':'#122b52','celeste':'#8ab9df','rojo':'#ad2f36','guindo':'#722f43',
  'verde':'#4f7457','verde militar':'#596445','rosado':'#d99aaa','rosa':'#d99aaa','morado':'#7a5b9a','amarillo':'#d8a921',
  'naranja':'#d97a2b','crema':'#eadfc5'
};
function colorHex(name='') { return COLOR_HEX[String(name).trim().toLowerCase()] || '#a9adb4'; }
function topColors(row) {
  const arr = Array.isArray(row.top_colores) ? row.top_colores.slice(0,3) : [];
  if (arr.length) return arr.map((c,i)=>({name:c.color || c.nombre || '', score:c.score, ranking:c.ranking || i+1}));
  const one = row.color_sugerido || row.color || '';
  return one ? [{name:one, score:row.score_color, ranking:1}] : [];
}
function renderColorDots(row, withNames=true) {
  const arr=topColors(row);
  if(!arr.length) return '<span class="muted-mini">Sin color</span>';
  return `<div class="color-dot-list">${arr.map(c=>`<span class="color-dot-item" title="${escapeHtml(c.name)}"><i style="background:${colorHex(c.name)}"></i>${withNames?`<small>${escapeHtml(c.name)}</small>`:''}</span>`).join('')}</div>`;
}
function platformLogo(name='') {
  const p=String(name).toLowerCase();
  if(p.includes('tiktok')) return '<span class="platform-logo tiktok">♪</span>';
  if(p.includes('pinterest')) return '<span class="platform-logo pinterest">P</span>';
  if(p.includes('instagram')) return '<span class="platform-logo instagram">◎</span>';
  return '<span class="platform-logo generic">◈</span>';
}
function renderEmpty(message) { return `<div class="empty-state">${escapeHtml(message)}</div>`; }
function renderCell(row, column) {
  if (typeof column.render === 'function') return column.render(row);
  let value = row[column.key];
  if (column.format === 'number') value = formatNumber(value, column.decimals ?? 2);
  if (column.format === 'pct') value = `${formatNumber(Number(value || 0) * 100, column.decimals ?? 2)}%`;
  if (column.format === 'int') value = Number(value || 0).toLocaleString('es-BO');
  if (column.format === 'badge') return `<span class="badge ${badgeClass(value)}">${escapeHtml(value)}</span>`;
  return escapeHtml(value ?? '');
}
function renderTable(title, rows, columns, options = {}) {
  if (!rows || rows.length === 0) return renderEmpty(options.empty || 'No existen datos para mostrar.');
  const thead = columns.map(c => `<th>${escapeHtml(c.label)}</th>`).join('');
  const tbody = rows.map(row => `<tr>${columns.map(c => `<td>${renderCell(row, c)}</td>`).join('')}</tr>`).join('');
  return `<article class="table-card"><header><h3>${escapeHtml(title)}</h3>${options.aside || ''}</header><div class="table-wrap"><table class="${options.compact ? 'compact-table' : ''}"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table></div></article>`;
}
function renderBarChart(title, rows, labelKey, valueKey) {
  if (!rows || rows.length === 0) return renderEmpty('No hay datos para graficar.');
  const max = Math.max(...rows.map(r => Number(r[valueKey] || 0)), 1);
  const bars = rows.map(row => {
    const value = Number(row[valueKey] || 0);
    const width = Math.max((value / max) * 100, 4);
    return `<div class="bar-row"><div class="bar-label" title="${escapeHtml(row[labelKey])}">${escapeHtml(row[labelKey])}</div><div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div><div class="bar-value">${formatNumber(value,0)}</div></div>`;
  }).join('');
  return `<article class="chart-card"><h3>${escapeHtml(title)}</h3><div class="bar-chart">${bars}</div></article>`;
}
function renderLineChart(title, rows, labelKey, valueKey, formatter = v => v) {
  if (!rows || rows.length === 0) return renderEmpty('No hay datos para graficar.');
  const values = rows.map(r => Number(r[valueKey] || 0));
  const width = 760, height = 230, pad = 28;
  const min = Math.min(...values, 0), max = Math.max(...values, 1);
  const range = Math.max(max - min, 1);
  const step = rows.length > 1 ? (width - pad * 2) / (rows.length - 1) : 0;
  const points = rows.map((row, i) => {
    const x = pad + step * i;
    const y = height - pad - ((Number(row[valueKey] || 0) - min) / range) * (height - pad * 2);
    return { x, y, row };
  });
  const poly = points.map(p => `${p.x},${p.y}`).join(' ');
  const area = `${pad},${height-pad} ${poly} ${pad + step * (points.length - 1)},${height-pad}`;
  const labels = points.map(p => `<g><circle cx="${p.x}" cy="${p.y}" r="5" class="line-point"/><text x="${p.x}" y="${height-8}" text-anchor="middle" class="axis-label">${escapeHtml(formatter(p.row[labelKey]))}</text><text x="${p.x}" y="${p.y-12}" text-anchor="middle" class="point-label">${formatNumber(p.row[valueKey],0)}</text></g>`).join('');
  return `<article class="chart-card"><h3>${escapeHtml(title)}</h3><svg viewBox="0 0 ${width} ${height}" class="mountain-chart line-chart"><defs><linearGradient id="lineGrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#f59e0b" stop-opacity="0.55"/><stop offset="100%" stop-color="#f59e0b" stop-opacity="0.02"/></linearGradient></defs><polygon points="${area}" fill="url(#lineGrad)"></polygon><polyline points="${poly}" class="line-stroke"></polyline>${labels}</svg></article>`;
}
function renderDonutChart(title, rows, labelKey, valueKey, options = {}) {
  if (!rows || rows.length === 0) return renderEmpty('No hay datos para graficar.');
  const total = rows.reduce((acc, row) => acc + Number(row[valueKey] || 0), 0) || 1;
  const centerValue = options.centerValue ?? formatNumber(total, 0);
  const centerLabel = options.centerLabel ?? 'registros';
  const colors = ['#14532d','#b45309','#991b1b','#4338ca','#0f766e'];
  let current = 0;
  const segments = rows.map((row, idx) => {
    const value = Number(row[valueKey] || 0);
    const dash = (value / total) * 251.2;
    const seg = `<circle r="40" cx="60" cy="60" fill="transparent" stroke="${colors[idx % colors.length]}" stroke-width="16" stroke-dasharray="${dash} ${251.2-dash}" stroke-dashoffset="${-current}"></circle>`;
    current += dash;
    return seg;
  }).join('');
  const legend = rows.map((row, idx) => `<div class="legend-item"><span style="background:${colors[idx % colors.length]}"></span>${escapeHtml(row[labelKey])}: <strong>${formatNumber(row[valueKey], 2)}</strong></div>`).join('');
  return `<article class="chart-card donut-card"><h3>${escapeHtml(title)}</h3><div class="donut-wrap"><svg viewBox="0 0 120 120" class="donut-svg"><circle r="40" cx="60" cy="60" fill="transparent" stroke="#eef2f7" stroke-width="16"></circle>${segments}<text x="60" y="58" text-anchor="middle" class="donut-total">${escapeHtml(centerValue)}</text><text x="60" y="73" text-anchor="middle" class="donut-sub">${escapeHtml(centerLabel)}</text></svg><div class="legend">${legend}</div></div></article>`;
}
function renderMetricCards(metrics = {}) {
  if (metrics.fuente === 'Predicción guardada') {
    return `<div class="metric-grid">
      <article class="metric-card"><span>Resultados</span><strong>${metrics.prendas || 0}</strong><small>Top 5 guardado</small></article>
      <article class="metric-card"><span>Colores detectados</span><strong>${metrics.colores || 0}</strong><small>Top 3 por prenda</small></article>
      <article class="metric-card"><span>Estilos</span><strong>${metrics.estilos || 0}</strong><small>recomendados</small></article>
      <article class="metric-card"><span>Score promedio</span><strong>${formatNumber(metrics.score_promedio || 0)}</strong><small>${escapeHtml(metrics.apify || '')}</small></article>
    </div>`;
  }
  return `<div class="metric-grid">
    <article class="metric-card"><span>Registros del mes</span><strong>${Number(metrics.registros_mes || 0).toLocaleString('es-BO')}</strong><small>histórico disponible</small></article>
    <article class="metric-card"><span>Prendas</span><strong>${metrics.prendas || 0}</strong><small>tipos distintos</small></article>
    <article class="metric-card"><span>Colores</span><strong>${metrics.colores || 0}</strong><small>variaciones</small></article>
    <article class="metric-card"><span>Estilos</span><strong>${metrics.estilos || 0}</strong><small>categorías</small></article>
  </div>`;
}
function renderInsightStrip(rows) {
  const avg = rows.reduce((a, r) => a + Number(r.score_final || 0), 0) / Math.max(rows.length, 1);
  const uniqueColors = new Set(rows.map(r => r.color_sugerido || r.color)).size;
  const uniqueStyles = new Set(rows.map(r => r.estilo_sugerido || r.estilo)).size;
  return `<div class="insight-strip">
    <div><span>Score promedio Top 5</span><strong>${formatNumber(avg)}</strong></div>
    <div><span>Colores presentes</span><strong>${uniqueColors}</strong></div>
    <div><span>Estilos presentes</span><strong>${uniqueStyles}</strong></div>
    <div><span>Resultados</span><strong>${rows.length}</strong></div>
  </div>`;
}
function renderPredictionHero(rows, filtros, modo) {
  const top = rows[0];
  if (!top) return '';
  return `<article class="prediction-hero-card exact-result">
    <div class="result-product-block">
      <span class="result-label">Prenda recomendada</span>
      <img src="${productImage(top)}" alt="${escapeHtml(top.tipo_prenda)}" class="prediction-hero-image">
      <strong>${escapeHtml(top.tipo_prenda)}</strong>
      <span class="badge ${badgeClass(top.prediccion_final)}">Demanda ${escapeHtml(top.prediccion_final || 'Referencial')}</span>
    </div>
    <div class="result-colors-block">
      <span class="result-label">Top 3 colores</span>
      ${renderColorDots(top,true)}
    </div>
    <div class="result-style-block">
      <span class="result-label">Estilo recomendado</span>
      <span class="style-pill">${escapeHtml(top.estilo_sugerido || top.estilo || '')}</span>
    </div>
  </article>`;
}
function renderTop5Strip(rows) {
  if (!rows || !rows.length) return '';
  return `<article class="top5-panel yarn-corner"><h3>Top 5 predicciones</h3><div class="top5-strip">${rows.slice(0,5).map((r,i)=>`
    <div class="top5-item"><b>${i+1}</b><span><strong>${escapeHtml(r.tipo_prenda || '')}</strong>${renderColorDots(r,false)}</span></div>`).join('')}</div></article>`;
}
function trendArrow(t){ return t==='Creciente' ? '↑' : (t==='Decreciente' ? '↓' : '→'); }
function trendClass(t){ return t==='Creciente' ? 'trend-up' : (t==='Decreciente' ? 'trend-down' : 'trend-stable'); }
function planningClass(p){ return p==='Alta' ? 'plan-high' : (p==='Baja' ? 'plan-low' : 'plan-medium'); }
function renderTrendMultiChart(analysis){
  if(!analysis || !analysis.length) return '';
  const width=900,height=280,padL=42,padR=24,padT=28,padB=42;
  const palette=['#d99008','#2563eb','#0f766e','#7c3aed','#b91c1c'];
  const all=analysis.flatMap(a=>(a.serie||[]).map(p=>Number(p.score||0)));
  const min=Math.min(...all,0), max=Math.max(...all,100), range=Math.max(max-min,1);
  const labels=(analysis[0].serie||[]).map(p=>p.periodo||'');
  const x=i=>padL+(labels.length<=1?0:i*(width-padL-padR)/(labels.length-1));
  const y=v=>height-padB-((Number(v||0)-min)/range)*(height-padT-padB);
  const grid=[25,50,75,100].map(v=>`<g><line x1="${padL}" y1="${y(v)}" x2="${width-padR}" y2="${y(v)}" class="trend-grid-line"/><text x="${padL-8}" y="${y(v)+4}" text-anchor="end" class="trend-axis-text">${v}</text></g>`).join('');
  const xlabels=labels.map((l,i)=>`<text x="${x(i)}" y="${height-12}" text-anchor="middle" class="trend-axis-text">${escapeHtml(l)}</text>`).join('');
  const lines=analysis.map((a,idx)=>{
    const pts=(a.serie||[]).map((p,i)=>`${x(i)},${y(p.score)}`).join(' ');
    const dots=(a.serie||[]).map((p,i)=>`<circle cx="${x(i)}" cy="${y(p.score)}" r="4" fill="${palette[idx%palette.length]}"/>`).join('');
    return `<polyline points="${pts}" fill="none" stroke="${palette[idx%palette.length]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${dots}`;
  }).join('');
  const legend=analysis.map((a,i)=>`<span><i style="background:${palette[i%palette.length]}"></i>#${escapeHtml(a.ranking)} ${escapeHtml(a.tipo_prenda)}</span>`).join('');
  return `<article class="trend-history-chart yarn-corner"><header><div><h3>Comportamiento histórico del Top 5</h3><p>Comparación de la señal histórica de demanda en los últimos seis periodos.</p></div></header><svg viewBox="0 0 ${width} ${height}" class="trend-multi-svg">${grid}${xlabels}${lines}</svg><div class="trend-chart-legend">${legend}</div></article>`;
}
function renderPlanningSupport(analysis){
  if(!analysis || !analysis.length) return '';
  return `<article class="planning-support yarn-corner"><header><div><h3>Apoyo a la planificación productiva</h3><p>Orientación derivada de la demanda prevista y del comportamiento histórico; no representa cantidades exactas de producción.</p></div></header><div class="planning-grid">${analysis.map(a=>`<div class="planning-card ${planningClass(a.prioridad_planificacion)}"><div class="planning-rank">#${escapeHtml(a.ranking)}</div><div><h4>${escapeHtml(a.tipo_prenda)}</h4><p>Demanda prevista: <strong>${escapeHtml(a.demanda)}</strong></p><p>Comportamiento: <strong class="${trendClass(a.tendencia)}">${trendArrow(a.tendencia)} ${escapeHtml(a.tendencia)}</strong> <small>${Number(a.variacion||0)>=0?'+':''}${formatNumber(a.variacion||0,1)}</small></p><span class="planning-priority">Prioridad ${escapeHtml(a.prioridad_planificacion)}</span><small>${escapeHtml(a.interpretacion||'')}</small></div></div>`).join('')}</div></article>`;
}
function renderTrendAnalysis(rows, analysis=[]) {
  if(!rows || !rows.length) return renderEmpty('No se generaron resultados.');
  const byName=Object.fromEntries((analysis||[]).map(a=>[String(a.tipo_prenda),a]));
  return `<article class="trend-analysis-card yarn-corner">
    <div class="trend-tabs"><span class="active">Por prenda</span><span>Color y estilo</span><span>Comportamiento</span></div>
    <div class="trend-table trend-table-expanded">
      <div class="trend-head"><span>Prenda</span><span>Prioridad</span><span>Comportamiento</span><span>Colores más populares</span><span>Estilo</span></div>
      ${rows.slice(0,5).map(r=>{const score=Math.max(0,Math.min(100,Number(r.score_final||0))); const a=byName[String(r.tipo_prenda)]||{}; return `<div class="trend-row">
        <strong>${escapeHtml(r.tipo_prenda||'')}</strong>
        <div class="trend-progress"><i style="width:${score}%"></i><b>${formatNumber(score,1)}</b></div>
        <span class="trend-behavior ${trendClass(a.tendencia)}">${trendArrow(a.tendencia)} ${escapeHtml(a.tendencia||'Estable')} <small>${Number(a.variacion||0)>=0?'+':''}${formatNumber(a.variacion||0,1)}</small></span>
        ${renderColorDots(r,false)}
        <span class="style-pill small">${escapeHtml(r.estilo_sugerido||r.estilo||'')}</span>
      </div>`}).join('')}
    </div>
  </article>${renderTrendMultiChart(analysis)}${renderPlanningSupport(analysis)}`;
}
function renderMountainChart(rows) {
  if (!rows || rows.length === 0) return renderEmpty('No hay datos para graficar.');
  const width = 860, height = 260, pad = 34;
  const values = rows.map(r => Number(r.score_final || 0));
  const max = Math.max(...values, 1), min = Math.min(...values, 0);
  const range = Math.max(max - min, 1);
  const step = rows.length > 1 ? (width - pad * 2) / (rows.length - 1) : 0;
  const points = rows.map((row, i) => {
    const x = pad + step * i;
    const y = height - pad - ((Number(row.score_final || 0) - min) / range) * (height - pad * 2);
    return { x, y, row };
  });
  const line = points.map(p => `${p.x},${p.y}`).join(' ');
  const area = `${pad},${height-pad} ${line} ${pad + step * (points.length - 1)},${height-pad}`;
  const labels = points.map((p, idx) => `
    <g>
      <circle cx="${p.x}" cy="${p.y}" r="6" class="line-point animate"></circle>
      <text x="${p.x}" y="${p.y - 14}" text-anchor="middle" class="point-label">#${idx+1}</text>
      <text x="${p.x}" y="${height - 10}" text-anchor="middle" class="axis-label small">${escapeHtml((p.row.tipo_prenda || '').split(' ')[0])}</text>
    </g>`).join('');
  return `<article class="chart-card"><div class="chart-header"><h3>Ranking Top 5 visual</h3><span class="subtle">Lectura cuantitativa del score final por prenda</span></div>
    <svg viewBox="0 0 ${width} ${height}" class="mountain-chart"><defs><linearGradient id="mountainGrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#f59e0b" stop-opacity="0.65"></stop><stop offset="100%" stop-color="#f59e0b" stop-opacity="0.05"></stop></linearGradient></defs><polygon points="${area}" fill="url(#mountainGrad)"></polygon><polyline points="${line}" class="mountain-line"></polyline>${labels}</svg></article>`;
}
function renderTopColors(row) {
  const colors = Array.isArray(row.top_colores) ? row.top_colores.slice(0, 3) : [];
  if (!colors.length) return `<strong>${escapeHtml(row.color_sugerido || row.color || '')}</strong><small>${formatNumber(row.score_color)}</small>`;
  return `<div class="top-colors-list">${colors.map(c => `<span><b>#${escapeHtml(c.ranking)}</b> ${escapeHtml(c.color)} <small>${formatNumber(c.score)}</small></span>`).join('')}</div>`;
}
function topColorsCell(row) {
  const colors = Array.isArray(row.top_colores) ? row.top_colores.slice(0, 3) : [];
  if (!colors.length) return escapeHtml(row.color_sugerido || row.color || '');
  return colors.map(c => `#${c.ranking} ${escapeHtml(c.color)}`).join('<br>');
}
function renderPredictionCards(rows, compact = false) {
  if (!rows || !rows.length) return renderEmpty('No se generaron resultados.');
  return `<div class="prediction-grid ${compact ? 'compact' : ''}">${rows.map(row => `
    <article class="prediction-card visual">
      <div class="rank-badge">#${escapeHtml(row.ranking || '')}</div>
      <img src="${productImage(row)}" alt="${escapeHtml(row.tipo_prenda || 'Prenda')}" class="product-thumb">
      <div class="prediction-main">
        <h3>${escapeHtml(row.tipo_prenda || 'Prenda')}</h3>
        <span class="badge ${badgeClass(row.prediccion_final)}">${escapeHtml(row.prediccion_final || 'Referencial')}</span>
      </div>
      <div class="mini-stat colors"><span>Top 3 colores</span>${renderTopColors(row)}</div>
      <div class="mini-stat"><span>Estilo</span><strong>${escapeHtml(row.estilo_sugerido || row.estilo || '')}</strong><small>${formatNumber(row.score_estilo)}</small></div>
      <div class="mini-stat score"><span>Score</span><strong>${formatNumber(row.score_final)}</strong><small>${escapeHtml(row.fuente_senal || row.origen_resultado || '')}</small></div>
    </article>`).join('')}</div>`;
}
function renderComparativoCiudad(rows) {
  if (!rows || rows.length === 0) return '';
  return `<article class="table-card"><header><h3>Referencia histórica por ciudad</h3></header><div class="reference-grid">${rows.map(row => `<div class="reference-item"><span>${escapeHtml(row.ciudad)}</span><strong>${escapeHtml(row.prenda_referencial)}</strong><small>Puntaje histórico: ${formatNumber(row.puntaje_historico,0)}</small></div>`).join('')}</div></article>`;
}
function initNavigation() {
  $all('.nav-item').forEach(btn => btn.addEventListener('click', () => goSection(btn.dataset.section)));
  $all('.nav-shortcut').forEach(btn => btn.addEventListener('click', () => goSection(btn.dataset.go)));
}
function goSection(target) {
  if (!target) return;
  state.currentSection = target;
  $all('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.section === target));
  $all('.section').forEach(section => section.classList.remove('active-section'));
  document.getElementById(target)?.classList.add('active-section');
  $('#pageTitle').textContent = pageTitles[target] || 'Sistema';

  // Las vistas del mockup se cargan al entrar, sin botones grandes adicionales.
  const autoForms = {
    'color-estilo': ['#specificForm', '#specificResult'],
    'historial': ['#historyForm', '#historyResult'],
    'dashboard': ['#dashboardForm', '#dashboardResult'],
    'senales': ['#signalsForm', '#signalsResult'],
  };
  const pair = autoForms[target];
  if (pair) {
    const form = $(pair[0]);
    const result = $(pair[1]);
    if (form && result && !result.dataset.loadedOnce) {
      result.dataset.loadedOnce = '1';
      setTimeout(() => form.requestSubmit(), 0);
    }
  }
}
function selectOptions(select, values, { includeAll = false, allLabel = 'Todas', defaultValue = null, map = null } = {}) {
  if (!select) return;
  select.innerHTML = '';
  if (includeAll) {
    const opt = document.createElement('option');
    opt.value = allLabel; opt.textContent = allLabel; select.appendChild(opt);
  }
  values.forEach(item => {
    const opt = document.createElement('option');
    opt.value = map ? map.value(item) : item;
    opt.textContent = map ? map.label(item) : item;
    select.appendChild(opt);
  });
  if (defaultValue !== null && Array.from(select.options).some(o => String(o.value) === String(defaultValue))) select.value = defaultValue;
}
async function initOptions() {
  const data = await api('/api/options');
  state.options = data;
  const monthMap = { value: m => m.value, label: m => m.label };
  ['generalAno','specificAno','dashboardAno','signalsAno'].forEach(id => selectOptions(document.getElementById(id), data.anos, { defaultValue: data.anos.includes(2026) ? 2026 : data.anos[0] }));
  ['generalMes','specificMes','dashboardMes','signalsMes'].forEach(id => selectOptions(document.getElementById(id), data.meses, { map: monthMap, defaultValue: 5 }));
  ['generalCiudad','specificCiudad','dashboardCiudad','signalsCiudad'].forEach(id => selectOptions(document.getElementById(id), data.ciudades, { includeAll: true, allLabel: 'Todas', defaultValue: 'Todas' }));
  selectOptions($('#historyAno'), data.anos, { includeAll: true, allLabel: 'Todos', defaultValue: 'Todos' });
  selectOptions($('#historyMes'), data.meses, { includeAll: true, allLabel: 'Todos', map: monthMap, defaultValue: 'Todos' });
  selectOptions($('#historyCiudad'), data.ciudades, { includeAll: true, allLabel: 'Todas', defaultValue: 'Todas' });
}
function getFormValues(form) { return Object.fromEntries(new FormData(form).entries()); }
function predictionColumns() {
  return [
    { key: 'ranking', label: '#', format: 'int' },
    { key: 'tipo_prenda', label: 'Prenda' },
    { key: 'prediccion_final', label: 'Demanda', format: 'badge' },
    { key: 'top_colores', label: 'Top 3 colores', render: topColorsCell },
    { key: 'estilo_sugerido', label: 'Estilo sugerido', render: estiloCell },
    { key: 'score_final', label: 'Score final', format: 'number' },
  ];
}
async function handleGeneral(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const result = $('#generalResult');
  setLoading(form, true);
  result.innerHTML = renderEmpty('Calculando predicción...');
  try {
    const payload = getFormValues(form);
    const data = await api('/api/prediccion/general', { method: 'POST', body: JSON.stringify(payload) });
    const rows = data.rows || [];
    result.innerHTML = `
      <div class="prediction-result-heading"><h3>Resultado de la predicción</h3><small>${escapeHtml(String((data.filtros||payload).mes).padStart(2,'0'))}/${escapeHtml((data.filtros||payload).ano)} · ${escapeHtml((data.filtros||payload).ciudad)}</small></div>
      ${renderPredictionHero(rows, data.filtros || payload, data.modo_senal)}
      ${renderTop5Strip(rows)}
    `;
    // Sincroniza los filtros del resto de módulos con el periodo recién generado.
    [['specificAno','ano'],['specificMes','mes'],['specificCiudad','ciudad'],['dashboardAno','ano'],['dashboardMes','mes'],['dashboardCiudad','ciudad'],['historyAno','ano'],['historyMes','mes'],['historyCiudad','ciudad']].forEach(([id,key]) => {
      const el = document.getElementById(id);
      if (el && Array.from(el.options).some(o => String(o.value) === String(payload[key]))) el.value = payload[key];
    });
    const usadas = rows[0]?.plataformas_consultadas || rows[0]?.fuente_senal || '';
    toast(`Predicción general generada y guardada${usadas ? ` con señales de ${String(usadas).replace(/^Apify:\s*/i,'')}` : ''}.`, 'success');
  } catch (err) {
    result.innerHTML = `<div class="notice danger">${escapeHtml(err.message)}</div>`;
  } finally {
    setLoading(form, false);
  }
}
async function handleSpecific(e) {
  e.preventDefault();
  const form = e.currentTarget;
  const result = $('#specificResult');
  setLoading(form, true);
  result.innerHTML = renderEmpty('Buscando mejores combinaciones...');
  try {
    const data = await api('/api/prediccion/color-estilo', { method: 'POST', body: JSON.stringify(getFormValues(form)) });
    const rows = data.rows || [];
    const origenTexto = {
      historial: 'desde la última predicción general guardada',
      cache: 'desde la tendencia/caché disponible',
      generado_cache_local: 'generado con base histórica local'
    }[data.origen] || 'desde datos guardados';
    result.innerHTML = `
      <div class="analysis-origin"><strong>Predicción guardada:</strong> ${origenTexto}. La vista reutiliza la evidencia existente sin consumir nuevas señales.</div>
      ${renderTrendAnalysis(rows, data.analisis_tendencia || [])}
    `
    toast('Análisis de tendencias cargado sin consumir Apify.', 'success');
  } catch (err) {
    result.innerHTML = `<div class="notice danger">${escapeHtml(err.message)}</div>`;
  } finally { setLoading(form, false); }
}
function historyQuery() { return new URLSearchParams(getFormValues($('#historyForm'))).toString(); }
async function handleHistory(e) {
  e?.preventDefault();
  const form = $('#historyForm');
  const result = $('#historyResult');
  setLoading(form, true);
  const q = historyQuery();
  $('#exportCsv').href = `/api/historial/export.csv?${q}`;
  $('#exportPdf').href = `/api/historial/export.pdf?${q}`;
  result.innerHTML = renderEmpty('Consultando historial...');
  try {
    const data = await api(`/api/historial?${q}`);
    result.innerHTML = renderTable(`Historial de predicciones (${data.total})`, data.rows || [], [
      { key: 'fecha_prediccion', label: 'Fecha' },
      { key: 'periodo', label: 'Mes predicho', render: periodoCell },
      { key: 'tipo_prenda', label: 'Prenda recomendada' },
      { key: 'top_colores', label: 'Colores Top 3', render: row => renderColorDots(row,false) },
      { key: 'estilo_sugerido', label: 'Estilo', render: row => `<span class="style-pill small">${escapeHtml(row.estilo_sugerido || row.estilo || '')}</span>` },
      { key: 'score_final', label: 'Score', format: 'number' },
    ], { compact: true, empty: 'Todavía no existen predicciones en el historial para ese filtro.' })
  } catch (err) {
    result.innerHTML = `<div class="notice danger">${escapeHtml(err.message)}</div>`;
  } finally { setLoading(form, false); }
}
async function handleDashboard(e) {
  e?.preventDefault();
  const form = $('#dashboardForm');
  const result = $('#dashboardResult');
  setLoading(form, true);
  const q = new URLSearchParams(getFormValues(form)).toString();
  result.innerHTML = renderEmpty('Actualizando dashboard...');
  try {
    const data = await api(`/api/dashboard?${q}`);
    if (data.empty) { result.innerHTML = renderEmpty('Aún no existe una predicción guardada para ese periodo y tampoco hay histórico mensual. Genera primero la predicción general.'); return; }
    const sourceLabel = data.source === 'prediccion_guardada' ? 'Predicción guardada del periodo' : 'Histórico disponible del periodo';
    result.innerHTML = `
      <div class="dashboard-source"><span>Resumen comercial</span><strong>${sourceLabel}</strong></div>
      ${renderMetricCards(data.metrics)}
      <div class="content-grid dashboard-main-charts">
        ${renderLineChart('Evolución mensual de la demanda', data.evolucion || [], 'mes', 'peso_demanda', v => String(v).padStart(2,'0'))}
        ${renderDonutChart('Distribución de predicciones por prenda', (data.prendas || []).slice(0,5).map(r=>({tipo_prenda:r.tipo_prenda,cantidad:Number(r.score_final||1)})), 'tipo_prenda', 'cantidad', data.source === 'prediccion_guardada' ? {centerValue: String((data.prendas || []).slice(0,5).length), centerLabel: 'prendas'} : {})}
      </div>
      <div class="content-grid two dashboard-secondary">
        ${renderBarChart('Colores principales', data.colores || [], 'color', 'peso_demanda')}
        ${renderBarChart('Estilos principales', data.estilos || [], 'estilo', 'peso_demanda')}
      </div>`;
  } catch (err) {
    result.innerHTML = `<div class="notice danger">${escapeHtml(err.message)}</div>`;
  } finally { setLoading(form, false); }
}
async function handleSignals(e) {
  e?.preventDefault();
  const form = $('#signalsForm');
  const result = $('#signalsResult');
  setLoading(form, true);
  const q = new URLSearchParams(getFormValues(form)).toString();
  result.innerHTML = renderEmpty('Consultando señales digitales...');
  try {
    const data = await api(`/api/senales?${q}&_=${Date.now()}`);
    const details = data.detalle || [];
    const used = details.filter(d => String(d.estado || 'OK').toUpperCase() === 'OK' || Number(d.publicaciones || 0) > 0 || Number(d.interacciones || 0) > 0);
    const trend = (data.cache || [])[0] || data.tendencia_mensual || {};
    const platformCards = used.length ? used.map(d => `<article class="platform-card">${platformLogo(d.plataforma)}<div><h4>${escapeHtml(d.plataforma)}</h4><span class="source-ok">Fuente utilizada</span><small>Datos extraídos: ${Number(d.publicaciones||0).toLocaleString('es-BO')}</small><small>Interacciones: ${Number(d.interacciones||0).toLocaleString('es-BO')}</small></div></article>`).join('') : '<div class="empty-state compact">No hay fuentes con datos guardados para este filtro.</div>';
    const colors = trend.color_sugerido ? [{color:trend.color_sugerido}] : [];
    const saved = data.guardadas || [];
    const savedRows = saved.map(g=>{
      const has = g.tiene_senales !== false;
      return `<button type="button" class="saved-signal-row ${has ? 'has-signal' : 'no-signal'}" data-year="${g.ano}" data-month="${g.mes}">
        <span><strong>${escapeHtml(g.periodo||'')}</strong><small>${has ? escapeHtml(g.ciudades||'') : 'Aún no generado'}</small></span>
        <span>${has ? escapeHtml(g.fuentes||'—') : '<em class="signal-empty-label">Sin señales</em>'}</span>
        <span>${has ? `<i style="background:${colorHex(g.color_sugerido||'')}"></i>${escapeHtml(g.color_sugerido||'—')}` : '—'}</span>
        <span class="${has ? 'style-pill small' : ''}">${has ? escapeHtml(g.estilo_sugerido||'—') : '—'}</span>
        <span>${has && g.fecha_extraccion ? escapeHtml(String(g.fecha_extraccion).split('T')[0]) : '—'}</span>
      </button>`;
    }).join('');
    result.innerHTML = `
      <article class="signals-sources yarn-corner"><header><h3>Fuentes de señales utilizadas</h3><span>${data.estado_tendencia?.ultima_actualizacion ? `Actualizado: ${escapeHtml(data.estado_tendencia.ultima_actualizacion.split('T')[0])}` : ''}</span></header><div class="platform-grid">${platformCards}</div></article>
      <article class="signals-summary yarn-corner"><h3>Resumen de señales</h3><div class="signals-summary-grid"><div><span>Color en tendencia</span><div class="big-color"><i style="background:${colorHex(trend.color_sugerido||'')}"></i><strong>${escapeHtml(trend.color_sugerido || '—')}</strong></div></div><div><span>Estilo en tendencia</span><strong class="style-pill">${escapeHtml(trend.estilo_sugerido || '—')}</strong></div><div><span>Próxima actualización recomendada</span><strong>${data.estado_tendencia?.proxima_actualizacion_recomendada ? escapeHtml(data.estado_tendencia.proxima_actualizacion_recomendada.split('T')[0]) : '—'}</strong><small>El caché se conserva hasta que lo borres o actualices.</small></div></div></article>
      <article class="saved-signals-card yarn-corner"><header><div><h3>Señales guardadas</h3><p>Periodos que ya cuentan con una tendencia mensual almacenada.</p></div><span>${saved.filter(g=>g.tiene_senales !== false).length} con señales · ${saved.length} meses</span></header><div class="saved-signals-head"><span>Periodo / ciudades</span><span>Fuentes</span><span>Color</span><span>Estilo</span><span>Actualización</span></div><div class="saved-signals-list">${savedRows}</div></article>
    `;
    result.querySelectorAll('.saved-signal-row').forEach(btn=>btn.addEventListener('click',()=>{ const y=document.getElementById('signalsAno'); const m=document.getElementById('signalsMes'); const c=document.getElementById('signalsCiudad'); if(y && Array.from(y.options).some(o=>String(o.value)===String(btn.dataset.year))) y.value=btn.dataset.year; if(m && Array.from(m.options).some(o=>String(o.value)===String(btn.dataset.month))) m.value=btn.dataset.month; if(c) c.value='Todas'; form.requestSubmit(); }));
  } catch (err) {
    result.innerHTML = `<div class="notice danger">${escapeHtml(err.message)}</div>`;
  } finally { setLoading(form, false); }
}

async function refreshTrends() {
  const form = $('#signalsForm');
  const data = getFormValues(form);
  if (!window.confirm(`¿Actualizar tendencias de ${data.mes}/${data.ano} para ${data.ciudad} usando Apify? El caché anterior de ESTE periodo se reemplazará; los demás meses se conservarán.`)) return;
  setLoading(form, true);
  const result = $('#signalsResult');
  result.innerHTML = renderEmpty('Extrayendo señales digitales. TikTok es la fuente principal y Pinterest e Instagram complementan cuando están disponibles...');
  try {
    const res = await api('/api/senales/actualizar', { method: 'POST', body: JSON.stringify(data) });
    toast(`Tendencias actualizadas. Fuentes utilizadas: ${res.tendencia?.fuente || 'señales digitales'}.`, 'success');
    await handleSignals();
  } catch (err) { toast(err.message, 'danger'); result.innerHTML = `<div class="notice danger">${escapeHtml(err.message)}</div>`; }
  finally { setLoading(form, false); }
}

async function clearSignals() {
  const form = $('#signalsForm');
  const data = getFormValues(form);
  if (!window.confirm(`¿Borrar el caché guardado de ${data.mes}/${data.ano} para ${data.ciudad}? Esta acción afecta solo al periodo seleccionado y no borra los demás meses.`)) return;
  setLoading(form, true);
  try {
    const res = await api('/api/senales/limpiar', { method: 'POST', body: JSON.stringify(data) });
    toast(`Caché limpiado para el periodo seleccionado, incluida la tendencia mensual.`, 'success');
    await handleSignals();
  } catch (err) { toast(err.message, 'danger'); }
  finally { setLoading(form, false); }
}
function bindForms() {
  $('#generalForm').addEventListener('submit', handleGeneral);
  $('#specificForm').addEventListener('submit', handleSpecific);
  $('#historyForm').addEventListener('submit', handleHistory);
  $('#dashboardForm').addEventListener('submit', handleDashboard);
  $('#signalsForm').addEventListener('submit', handleSignals);
  $('#refreshTrends').addEventListener('click', refreshTrends);
  $('#clearSignals').addEventListener('click', clearSignals);

  // Los selectores compactos del encabezado actualizan la vista automáticamente.
  [['#specificMes','#specificForm'],['#historyMes','#historyForm'],['#dashboardMes','#dashboardForm'],['#signalsMes','#signalsForm']].forEach(([sel,form]) => {
    const select = $(sel);
    const targetForm = $(form);
    if (select && targetForm) select.addEventListener('change', () => targetForm.requestSubmit());
  });
}
async function boot() {
  initNavigation(); bindForms();
  try {
    await initOptions();
    $('#exportCsv').href = `/api/historial/export.csv?${historyQuery()}`;
    $('#exportPdf').href = `/api/historial/export.pdf?${historyQuery()}`;
    handleDashboard(); handleSignals();
  } catch (err) { toast(err.message, 'danger'); }
}
document.addEventListener('DOMContentLoaded', boot);

// Botón visual del mockup: limpia el resultado y enfoca los parámetros sin alterar la lógica.
document.addEventListener('DOMContentLoaded', () => {
  const newPredictionBtn = document.getElementById('newPredictionBtn');
  const generalForm = document.getElementById('generalForm');
  const generalResult = document.getElementById('generalResult');
  if (newPredictionBtn && generalForm) {
    newPredictionBtn.addEventListener('click', () => {
      if (generalResult) generalResult.innerHTML = '';
      generalForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const mes = document.getElementById('generalMes');
      if (mes) mes.focus({ preventScroll: true });
    });
  }
});
