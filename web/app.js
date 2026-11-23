/* =========================================================
   量析 StockQuant — 前端应用
   原生 JS + 手写 SVG 图表，无任何第三方依赖
   ========================================================= */
(() => {
  'use strict';

  /* ----------------------------- 工具 ----------------------------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const state = { data: null, query: '', tab: 'price', years: 3 };
  const nav = { lastList: null };
  const showBack = (on) => { $('#backBtn').hidden = !on; };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
  const isNum = (v) => typeof v === 'number' && isFinite(v);

  const nf = (v, d = 2) => (isNum(v)
    ? v.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })
    : '—');
  const pct = (v, d = 2) => (isNum(v) ? `${(v * 100).toFixed(d)}%` : '—');
  const pctS = (v, d = 2) => (isNum(v) ? `${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%` : '—');
  const pctRaw = (v, d = 2) => (isNum(v) ? `${v.toFixed(d)}%` : '—');
  const cls = (v) => (isNum(v) ? (v > 0 ? 'up-text' : v < 0 ? 'down-text' : 'flat-text') : 'flat-text');
  const money = (yuan) => {
    if (!isNum(yuan)) return '—';
    const a = Math.abs(yuan);
    if (a >= 1e12) return `${(yuan / 1e12).toFixed(2)} 万亿`;
    if (a >= 1e8) return `${(yuan / 1e8).toFixed(2)} 亿`;
    if (a >= 1e4) return `${(yuan / 1e4).toFixed(0)} 万`;
    return nf(yuan, 0);
  };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  /* --------------------------- 图表引擎 --------------------------- */
  let chartSeq = 0;
  const charts = new Map(); // container -> spec

  function niceTicks(lo, hi, target = 5) {
    const span = Math.abs(hi - lo) || Math.abs(hi) || 1;
    const raw = span / target;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const candidates = [1, 2, 2.5, 5, 10].map((m) => m * mag);
    let best = null;
    candidates.forEach((step) => {
      const start = Math.ceil(lo / step - 1e-9) * step;
      const n = Math.floor((hi - start) / step + 1e-9) + 1;
      if (n < 3 || n > 7) return;
      const score = Math.abs(n - target);
      if (!best || score < best.score) best = { step, start, n, score };
    });
    if (!best) return [lo, (lo + hi) / 2, hi];
    const out = [];
    for (let i = 0; i < best.n; i += 1) {
      const v = best.start + i * best.step;
      out.push(Math.abs(v) < best.step * 1e-9 ? 0 : v);
    }
    return out;
  }

  function drawChart(container, spec) {
    if (!container) return;
    const W = Math.max(300, Math.floor(container.clientWidth || 640));
    const H = spec.height || 320;
    const pad = Object.assign({ l: 56, r: 16, t: 14, b: 26 }, spec.pad || {});
    const volH = spec.volume ? 46 : 0;
    const plot = { x0: pad.l, x1: W - pad.r, y0: pad.t, y1: H - pad.b - volH };
    const labels = spec.labels || [];
    const n = labels.length;
    if (!n) { container.innerHTML = '<p class="muted">暂无数据</p>'; return; }

    const vals = [];
    (spec.series || []).forEach((s) => s.data.forEach((v) => { if (isNum(v)) vals.push(v); }));
    (spec.bands || []).forEach((b) => {
      b.lo.forEach((v) => { if (isNum(v)) vals.push(v); });
      b.hi.forEach((v) => { if (isNum(v)) vals.push(v); });
    });
    if (isNum(spec.baseline)) vals.push(spec.baseline);
    if (!vals.length) { container.innerHTML = '<p class="muted">暂无数据</p>'; return; }

    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    if (spec.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const padY = (hi - lo) * 0.09 || Math.abs(hi || 1) * 0.05;
    lo -= padY; hi += padY;

    const x = (i) => (n <= 1 ? (plot.x0 + plot.x1) / 2 : plot.x0 + ((plot.x1 - plot.x0) * i) / (n - 1));
    const y = (v) => plot.y1 - ((v - lo) / (hi - lo)) * (plot.y1 - plot.y0);
    const fmt = spec.fmt || ((v) => nf(v, 2));
    const fmtAxis = spec.fmtAxis || fmt;
    const uid = `c${++chartSeq}`;

    const parts = [];
    const ticks = niceTicks(lo, hi, 4);

    if (spec.legend !== false && (spec.series || []).length) {
      const legend = (spec.series || []).filter((s) => s.name && s.showLegend !== false)
        .map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('');
      container.innerHTML = `<div class="chart-legend">${legend}</div>`;
    } else {
      container.innerHTML = '';
    }

    // 网格与 Y 轴
    ticks.forEach((t) => {
      const yy = y(t);
      if (yy < plot.y0 - 0.5 || yy > plot.y1 + 0.5) return;
      parts.push(`<line x1="${plot.x0}" y1="${yy.toFixed(1)}" x2="${plot.x1}" y2="${yy.toFixed(1)}" stroke="rgba(148,170,210,0.09)" stroke-width="1"/>`);
      parts.push(`<text x="${plot.x0 - 9}" y="${yy.toFixed(1)}" fill="#64708c" font-size="10" text-anchor="end" dominant-baseline="middle">${esc(fmtAxis(t))}</text>`);
    });

    // 零轴 / 基准线
    if (spec.zero && lo < 0 && hi > 0) {
      parts.push(`<line x1="${plot.x0}" y1="${y(0).toFixed(1)}" x2="${plot.x1}" y2="${y(0).toFixed(1)}" stroke="rgba(200,214,245,0.28)" stroke-width="1"/>`);
    }
    if (isNum(spec.baseline)) {
      const by = y(spec.baseline);
      parts.push(`<line x1="${plot.x0}" y1="${by.toFixed(1)}" x2="${plot.x1}" y2="${by.toFixed(1)}" stroke="${spec.baselineColor || 'rgba(200,214,245,0.3)'}" stroke-width="1" stroke-dasharray="4 4"/>`);
    }

    // 区间带（蒙特卡洛扇形 / 置信区间）
    (spec.bands || []).forEach((b) => {
      let up = '', down = '', started = false;
      for (let i = 0; i < n; i += 1) {
        const v = b.hi[i];
        if (!isNum(v)) { started = false; continue; }
        up += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        started = true;
      }
      started = false;
      for (let i = n - 1; i >= 0; i -= 1) {
        const v = b.lo[i];
        if (!isNum(v)) { started = false; continue; }
        down += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        started = true;
      }
      if (up) parts.push(`<path d="${up}${down.replace(/^M/, 'L')}Z" fill="${b.color}" stroke="none"/>`);
    });

    // 面积（回撤）
    (spec.series || []).forEach((s) => {
      if (!s.area || !isNum(s.areaBase)) return;
      let d = '', started = false, first = -1, last = -1;
      s.data.forEach((v, i) => {
        if (!isNum(v)) { started = false; return; }
        if (first < 0) first = i;
        last = i;
        d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        started = true;
      });
      if (!d) return;
      const base = s.areaBase === 'bottom' ? plot.y1 : y(s.areaBase);
      const area = `${d}L${x(last).toFixed(1)},${base.toFixed(1)}L${x(first).toFixed(1)},${base.toFixed(1)}Z`;
      const alpha = s.areaAlpha || 0.34;
      parts.push(`<defs><linearGradient id="${uid}-${s.name}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${s.color}" stop-opacity="${alpha}"/>
        <stop offset="1" stop-color="${s.color}" stop-opacity="${(alpha * 0.06).toFixed(3)}"/></linearGradient></defs>`);
      parts.push(`<path d="${area}" fill="url(#${uid}-${s.name})" stroke="none"/>`);
    });

    // 折线
    (spec.series || []).forEach((s) => {
      let d = '', started = false;
      s.data.forEach((v, i) => {
        if (!isNum(v)) { started = false; return; }
        d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        started = true;
      });
      if (!d) return;
      parts.push(`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.7}"
        stroke-linejoin="round" stroke-linecap="round"
        ${s.dash ? `stroke-dasharray="${s.dash}"` : ''} opacity="${s.opacity || 1}"/>`);
    });

    // 成交量
    let volBars = [];
    if (spec.volume && spec.volume.data) {
      const vd = spec.volume.data;
      const vmax = Math.max(...vd.filter(isNum), 1);
      const top = plot.y1 + 12;
      const bottom = H - pad.b;
      const bw = Math.max(1, ((plot.x1 - plot.x0) / n) * 0.62);
      vd.forEach((v, i) => {
        if (!isNum(v) || v <= 0) return;
        const h = Math.max(0.6, ((v / vmax) * (bottom - top)));
        const rising = i > 0 && isNum(spec.series[0].data[i]) && spec.series[0].data[i] >= spec.series[0].data[i - 1];
        volBars.push(`<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${(bottom - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"
          fill="${rising ? 'rgba(246,70,93,0.42)' : 'rgba(46,184,114,0.42)'}"/>`);
      });
      parts.push(`<text x="${plot.x0 - 9}" y="${(top + 4).toFixed(1)}" fill="#64708c" font-size="9.5" text-anchor="end">量</text>`);
      parts.push(volBars.join(''));
    }

    // X 轴刻度
    const tickCount = Math.min(5, n);
    const idxs = [];
    for (let k = 0; k < tickCount; k += 1) idxs.push(Math.round(((n - 1) * k) / Math.max(1, tickCount - 1)));
    const xfmt = spec.xfmt || ((s) => String(s).slice(2, 7));
    idxs.forEach((i, k) => {
      const anchor = k === 0 ? 'start' : k === idxs.length - 1 ? 'end' : 'middle';
      parts.push(`<text x="${x(i).toFixed(1)}" y="${H - 6}" fill="#64708c" font-size="10" text-anchor="${anchor}">${esc(xfmt(labels[i]))}</text>`);
    });

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    svg.style.width = '100%';
    svg.style.height = 'auto';
    svg.innerHTML = parts.join('');

    // 悬浮交互
    const hover = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    hover.setAttribute('opacity', '0');
    const vline = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    vline.setAttribute('stroke', 'rgba(200,214,245,0.35)');
    vline.setAttribute('stroke-dasharray', '3 3');
    vline.setAttribute('y1', plot.y0);
    vline.setAttribute('y2', plot.y1);
    hover.appendChild(vline);
    const dots = (spec.series || []).map((s) => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('r', 3.2);
      c.setAttribute('fill', s.color);
      c.setAttribute('stroke', '#0b1220');
      c.setAttribute('stroke-width', '1.5');
      hover.appendChild(c);
      return c;
    });
    const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    overlay.setAttribute('x', plot.x0);
    overlay.setAttribute('y', plot.y0);
    overlay.setAttribute('width', Math.max(0, plot.x1 - plot.x0));
    overlay.setAttribute('height', Math.max(0, plot.y1 - plot.y0));
    overlay.setAttribute('fill', 'transparent');
    svg.appendChild(overlay);
    svg.appendChild(hover);
    container.appendChild(svg);

    const tip = $('#tooltip');
    const geo = { W, H, pad, plot, n, x, y };
    let activeIdx = -1;

    const move = (ev) => {
      const rect = svg.getBoundingClientRect();
      const scale = rect.width / W;
      const px = (ev.clientX - rect.left) / scale;
      if (px < plot.x0 - 6 || px > plot.x1 + 6) return;
      const ratio = (px - plot.x0) / Math.max(1, plot.x1 - plot.x0);
      const idx = clamp(Math.round(ratio * (n - 1)), 0, n - 1);
      if (idx === activeIdx) return;
      activeIdx = idx;
      const xi = x(idx);
      vline.setAttribute('x1', xi);
      vline.setAttribute('x2', xi);
      (spec.series || []).forEach((s, k) => {
        const v = s.data[idx];
        if (isNum(v)) {
          dots[k].setAttribute('cx', xi);
          dots[k].setAttribute('cy', y(v));
          dots[k].setAttribute('opacity', '1');
        } else {
          dots[k].setAttribute('opacity', '0');
        }
      });
      hover.setAttribute('opacity', '1');

      const rows = (spec.series || []).filter((s) => s.name).map((s) => {
        const v = s.data[idx];
        return `<div class="tt-row"><span><i class="tt-dot" style="background:${s.color}"></i>${esc(s.name)}</span><b>${esc(fmt(v))}</b></div>`;
      }).join('');
      tip.innerHTML = `<div class="tt-date">${esc(labels[idx])}</div>${rows}`;
      tip.hidden = false;
      const tr = rect;
      let left = tr.left + xi * scale + 14;
      if (left + 170 > window.innerWidth) left = tr.left + xi * scale - 182;
      tip.style.left = `${Math.max(8, left)}px`;
      tip.style.top = `${Math.max(8, ev.clientY - 30)}px`;
    };
    const leave = () => {
      activeIdx = -1;
      hover.setAttribute('opacity', '0');
      tip.hidden = true;
    };
    svg.addEventListener('mousemove', move);
    svg.addEventListener('mouseleave', leave);

    charts.set(container, spec);
  }

  function redrawAll() {
    charts.forEach((spec, el) => {
      if (el.clientWidth > 0) drawChart(el, spec);
    });
  }

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawAll, 180);
  });

  /* --------------------------- 评分图形 --------------------------- */
  function renderGauge(el, score, tone) {
    const size = 132;
    const r = 52;
    const cx = size / 2;
    const cy = size / 2;
    const circ = 2 * Math.PI * r;
    const p = clamp(score / 100, 0, 1);
    const color = tone === 'up' ? '#f6465d' : tone === 'down' ? '#2eb872' : '#6d8cff';
    el.innerHTML = `
      <svg viewBox="0 0 ${size} ${size}">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="9"/>
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="9"
          stroke-linecap="round" stroke-dasharray="${(circ * p).toFixed(1)} ${circ.toFixed(1)}"
          transform="rotate(-90 ${cx} ${cy})" style="filter:drop-shadow(0 0 8px ${color}66)"/>
        <text x="${cx}" y="${cy - 2}" text-anchor="middle" fill="#e9eefb" font-size="30" font-weight="700">${score.toFixed(1)}</text>
        <text x="${cx}" y="${cy + 18}" text-anchor="middle" fill="#64708c" font-size="10">/ 100</text>
      </svg>`;
  }

  function renderRadar(el, dims) {
    const W = 300;
    const H = 210;
    const cx = W / 2;
    const cy = H / 2 + 4;
    const R = 74;
    const count = dims.length;
    const angle = (i) => -Math.PI / 2 + (2 * Math.PI * i) / count;
    const pt = (i, ratio) => {
      const a = angle(i);
      return [cx + Math.cos(a) * R * ratio, cy + Math.sin(a) * R * ratio];
    };
    const rings = [0.25, 0.5, 0.75, 1].map((ratio) => {
      const pts = dims.map((_, i) => pt(i, ratio).map((v) => v.toFixed(1)).join(',')).join(' ');
      return `<polygon points="${pts}" fill="none" stroke="rgba(148,170,210,0.13)" stroke-width="1"/>`;
    }).join('');
    const axes = dims.map((_, i) => {
      const [x2, y2] = pt(i, 1);
      return `<line x1="${cx}" y1="${cy}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="rgba(148,170,210,0.13)"/>`;
    }).join('');
    const shape = dims.map((d, i) => pt(i, clamp(d.score, 4, 100) / 100)
      .map((v) => v.toFixed(1)).join(',')).join(' ');
    const labels = dims.map((d, i) => {
      const [x, y] = pt(i, 1.27);
      const anchor = Math.abs(x - cx) < 12 ? 'middle' : x > cx ? 'start' : 'end';
      return `<text x="${x.toFixed(1)}" y="${(y + 3).toFixed(1)}" fill="#8b98b6" font-size="10.5"
        text-anchor="${anchor}">${esc(d.name)}</text>`;
    }).join('');
    el.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}">
        ${rings}${axes}
        <polygon points="${shape}" fill="rgba(109,140,255,0.22)" stroke="#7b95ff" stroke-width="1.6" stroke-linejoin="round"/>
        ${labels}
      </svg>`;
  }

  /* --------------------------- 渲染：概览 --------------------------- */
  const MARKET_NAME = { sh: '沪市', sz: '深市', bj: '北交所', us: '美股' };

  function quoteItem(label, value, id, colorFn) {
    const c = colorFn ? colorFn(value) : '';
    return `<div class="q-item"><div class="q-label">${label}</div>
      <div class="q-value ${c}" id="${id}">${value}</div></div>`;
  }

  function renderHero(d) {
    const m = d.meta;
    const q = d.quote || {};
    const chg = q.change_pct;
    const upCls = isNum(chg) && chg > 0 ? 'up-text' : isNum(chg) && chg < 0 ? 'down-text' : 'flat-text';
    const price = isNum(q.price) ? nf(q.price, 2) : nf(d.technical.last, 2);
    const isUs = m.currency === 'USD';
    const cur = isUs ? '$' : '';
    const mcapUnit = isUs ? ' 亿美元' : ' 亿';
    const links = (m.links || []).slice(0, 4).map((l) =>
      `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join('');

    const zeroToNull = (v) => (isNum(v) && v !== 0 ? v : null);
    const isIndex = m.kind === '指数';
    let quoteItems = [
      quoteItem('今开', cur + nf(q.open, 2), 'q-open'),
      quoteItem('最高', cur + nf(q.high, 2), 'q-high', (v) => (v && v !== '—' ? 'up-text' : '')),
      quoteItem('最低', cur + nf(q.low, 2), 'q-low', (v) => (v && v !== '—' ? 'down-text' : '')),
      quoteItem('昨收', cur + nf(q.prev_close, 2), 'q-prev'),
      quoteItem('成交额', isNum(q.amount) ? `${cur}${money(q.amount)}` : '—', 'q-amount'),
      quoteItem('换手率', isNum(q.turnover) ? `${nf(q.turnover, 2)}%` : '—', 'q-turnover'),
      quoteItem('市盈率 TTM', nf(zeroToNull(q.pe), 2), 'q-pe'),
      quoteItem('市净率', nf(zeroToNull(q.pb), 2), 'q-pb'),
      quoteItem('总市值', isNum(q.total_mcap) && q.total_mcap > 0 ? `${isUs ? '' : cur}${nf(q.total_mcap, 0)}${mcapUnit}` : '—', 'q-mcap'),
      quoteItem('流通市值', isNum(q.float_mcap) && q.float_mcap > 0 ? `${isUs ? '' : cur}${nf(q.float_mcap, 0)}${mcapUnit}` : '—', 'q-fmcap'),
      quoteItem('量比', nf(isNum(q.volume_ratio) ? q.volume_ratio : d.technical.vol_ratio, 2), 'q-vr'),
      quoteItem('振幅', isNum(q.amplitude) ? `${nf(q.amplitude, 2)}%` : '—', 'q-amp'),
    ];
    if (isIndex) quoteItems = quoteItems.filter((_, i) => ![6, 7, 8, 9].includes(i));
    quoteItems = quoteItems.join('');

    $('#hero').innerHTML = `
      <div class="hero-id">
        <div class="hero-tags">
          <span class="tag brand">${esc(m.code)}</span>
          <span class="tag">${MARKET_NAME[m.market] || m.market}</span>
          ${m.kind && m.kind !== (MARKET_NAME[m.market] || m.market) ? `<span class="tag">${esc(m.kind)}</span>` : ''}
          <span class="tag">${esc(m.adjusted || '前复权')}</span>
          ${m.fallback ? '<span class="tag" style="color:#ffb454;border-color:rgba(255,180,84,0.45)">备用数据源</span>' : ''}
        </div>
        <h2>${esc(m.name)} <span class="code">${esc(m.code)}${isUs || m.market === 'gold' ? '' : '.' + esc((m.market || '').toUpperCase())}</span></h2>
        <div class="hero-price">
          <span class="price" id="hPrice">${cur}${price}</span>
          <span class="chg ${upCls}" id="hChg">${isNum(q.change) ? `${q.change >= 0 ? '+' : ''}${nf(q.change, 2)}` : ''}
            ${isNum(chg) ? `${chg >= 0 ? '+' : ''}${nf(chg, 2)}%` : ''}</span>
        </div>
        <p class="hero-sub">数据区间 ${m.start} ~ ${m.end} · ${m.bars} 根日线 · 基准 ${esc(m.benchmark.name)}</p>
        <div class="hero-links">${links}</div>
        <p class="hero-source"><b>${esc(m.source)}</b><span>抓取于 ${esc(m.fetched_at)}</span>
          ${q.time ? `<span>报价 ${esc(q.time)}</span>` : ''}</p>
      </div>
      <div class="hero-quote">${quoteItems}</div>
      <div class="hero-side">
        <div class="mini" style="margin-bottom:10px">
          <div class="m-label">区间涨跌（自 ${m.start}）</div>
          <div class="m-value ${cls(d.performance.cum_return)}">${pctS(d.performance.cum_return)}</div>
        </div>
        <div class="mini" style="margin-bottom:10px">
          <div class="m-label">今年以来</div>
          <div class="m-value ${cls(periodValue(d, '今年以来'))}">${pctS(periodValue(d, '今年以来'))}</div>
        </div>
        <div class="mini">
          <div class="m-label">52周位置</div>
          <div class="m-value">${isNum(d.technical.pos52) ? `${(d.technical.pos52 * 100).toFixed(0)}%` : '—'}</div>
        </div>
      </div>`;
  }

  function periodValue(d, label) {
    const item = (d.periods || []).find((p) => p.label === label);
    return item ? item.value : null;
  }

  /* --------------------------- 渲染：KPI --------------------------- */
  function renderKpi(d) {
    const p = d.performance;
    const b = d.benchmark_performance;
    const items = [
      { label: '累计收益', formula: 'R = P_end / P_start − 1', value: pctS(p.cum_return), cls: cls(p.cum_return), sub: `基准 ${pctS(b.cum_return)}` },
      { label: '年化收益', formula: '(1 + R)^(252/n) − 1', value: pctS(p.annual_return), cls: cls(p.annual_return), sub: `${p.days} 个交易日` },
      { label: '夏普比率', formula: '(CAGR − r_f) / σ', value: nf(p.sharpe, 2), cls: p.sharpe >= 1 ? 'up-text' : p.sharpe < 0 ? 'down-text' : '', sub: '无风险利率 1.5%' },
      { label: '最大回撤', formula: 'min(净值 / 峰值 − 1)', value: pct(Number(p.max_dd)), cls: 'down-text',
        sub: `${String(p.max_dd_start).slice(0, 7)} → ${String(p.max_dd_end).slice(0, 7)} · ${p.max_dd_recover ? '已修复' : '未修复'}` },
      { label: '年化波动', formula: 'σ = std(r) × √252', value: pct(p.annual_vol), cls: '', sub: isNum(d.technical.vol_percentile) ? `历史分位 ${d.technical.vol_percentile}%` : '—' },
      { label: '年化 Alpha', formula: 'α = (μ − r_f) − β(μ_m − r_f)', value: pctS(d.relative.alpha_annual), cls: cls(d.relative.alpha_annual), sub: `信息比率 ${nf(d.relative.info_ratio, 2)}` },
    ];
    $('#kpiGrid').innerHTML = items.map((it) => `
      <article class="kpi" title="公式：${it.formula}">
        <div class="k-label">${it.label}</div>
        <div class="k-value ${it.cls}">${it.value}</div>
        <div class="k-sub">${it.sub}</div>
      </article>`).join('');
  }

  /* --------------------------- 渲染：图表 --------------------------- */
  function drawMainChart(d) {
    const s = d.series;
    const box = $('#mainChart');
    const tab = state.tab;
    const chartHeight = Math.round(Math.max(340, Math.min(570, (box.clientWidth || 820) * 0.66)));
    if (tab === 'price') {
      $('#chartSub').textContent = `${d.meta.adjusted}收盘价 · MA20 / MA60 · 成交量`;
      drawChart(box, {
        labels: s.dates,
        height: chartHeight,
        volume: { data: s.volume },
        series: [
          { name: '收盘价', data: s.close, color: '#dbe6ff', width: 1.8 },
          { name: 'MA20', data: s.ma20, color: '#ffb454', width: 1.2, dash: '4 3' },
          { name: 'MA60', data: s.ma60, color: '#4dd4c6', width: 1.2, dash: '2 3' },
        ],
        fmt: (v) => nf(v, 2),
        fmtAxis: (v) => nf(v, 0),
      });
    } else if (tab === 'drawdown') {
      $('#chartSub').textContent = '相对历史高点的回撤幅度';
      drawChart(box, {
        labels: s.dates,
        height: chartHeight,
        series: [{ name: '回撤', data: s.drawdown, color: '#f6465d', width: 1.5, area: true, areaBase: 0, areaAlpha: 0.18 }],
        fmt: (v) => pct(v, 2),
        fmtAxis: (v) => pct(v, 0),
        zero: true,
      });
    } else {
      $('#chartSub').textContent = `相对 ${d.meta.benchmark.name} 的强弱（起点 = 1）`;
      drawChart(box, {
        labels: s.dates,
        height: chartHeight,
        series: [{ name: '相对强弱', data: s.rs, color: '#8fa6ff', width: 1.8, area: true, areaBase: 1 }],
        fmt: (v) => nf(v, 3),
        fmtAxis: (v) => nf(v, 2),
        baseline: 1,
      });
    }
  }

  /* --------------------------- 渲染：评分 --------------------------- */
  function renderScore(d) {
    const sc = d.score;
    const badge = $('#scoreSignal');
    badge.textContent = sc.signal;
    badge.className = `badge ${sc.tone}`;
    renderGauge($('#gauge'), sc.total, sc.tone);
    $('#scoreGrade').innerHTML = `${esc(sc.grade)} <span class="muted" style="font-size:12px">评级</span>`;
    $('#scoreUpdated').textContent = `基于 ${d.meta.bars} 根日线 · ${d.meta.fetched_at.slice(5, 16)}`;
    renderRadar($('#radar'), sc.dims);
    $('#dimBars').innerHTML = sc.dims.map((dm) => `
      <div class="dim-row" title="${esc(dm.reason)}">
        <div class="dim-name">${esc(dm.name)}<small>权重 ${(dm.weight * 100).toFixed(0)}%</small></div>
        <div class="dim-track"><div class="dim-fill" style="width:${clamp(dm.score, 0, 100)}%"></div></div>
        <div class="dim-score">${nf(dm.score, 0)}</div>
      </div>`).join('') + `<p class="summary-text">${esc(sc.summary)}</p>`;

    const terms = sc.dims.map((dm) => `${dm.weight.toFixed(2)}×${dm.score.toFixed(1)}`);
    $('#scoreFormula').innerHTML =
      `<span class="f-label">加权公式</span>${terms.join(' + ')} = <b>${nf(sc.total, 1)}</b>`;
  }

  function renderCalc(d) {
    const p = d.performance;
    const r = d.relative || {};
    const a = d.advanced || {};
    const sc = d.score;
    const m = d.meta;
    const rows = [
      ['样本', `n 根前复权日线 · ${m.start} ~ ${m.end}`, `${m.bars} 根`],
      ['累计收益', 'R = P_end / P_start − 1', pctS(p.cum_return, 2)],
      ['年化收益', '(1 + R)^(252/n) − 1', pctS(p.annual_return, 2)],
      ['年化波动', 'σ = std(r) × √252', pct(p.annual_vol, 2)],
      ['夏普比率', '(CAGR − r_f) / σ', nf(p.sharpe, 2)],
      ['索提诺', '(CAGR − r_f) / σ_down', nf(p.sortino, 2)],
      ['最大回撤', 'min(净值 / 峰值 − 1)', pct(p.max_dd, 2)],
      ['Beta', 'Cov(r, r_m) / Var(r_m)', nf(r.beta, 2)],
      ['Alpha', '(μ − r_f) − β(μ_m − r_f)', pctS(r.alpha_annual, 2)],
      ['五维评分', 'Σ w_i × s_i', nf(sc.total, 1)],
    ];
    if (a.garch_vol != null) rows.push(['GARCH', 'σ²_t = ω + αε² + βσ²', pct(a.garch_vol, 1)]);
    if (a.kelly != null) rows.push(['Kelly', '(CAGR − r_f) / σ²', pct(a.kelly, 0)]);
    $('#calcTable').innerHTML = rows.map(([step, formula, value]) => `
      <div class="calc-row">
        <span class="c-step">${step}</span>
        <code class="c-formula">${formula}</code>
        <span class="c-value">${value}</span>
      </div>`).join('');
  }

  /* --------------------------- 渲染：技术面 --------------------------- */
  function renderTech(d) {
    const t = d.technical;
    const ma = t.ma;
    const items = [];
    const add = (label, value, note, span) => items.push({ label, value, note, span });

    add('MA5', nf(ma['5'], 2), `偏离 ${pctS(t.ma_dev['5'])}`);
    add('MA20', nf(ma['20'], 2), `偏离 ${pctS(t.ma_dev['20'])}`);
    add('MA60', nf(ma['60'], 2), `偏离 ${pctS(t.ma_dev['60'])}`);
    add('MA250', nf(ma['250'], 2), `偏离 ${pctS(t.ma_dev['250'])}`);
    add('RSI(14)', nf(t.rsi14, 2), t.rsi_zone);
    add('MACD', nf(t.macd.hist, 2), `${t.macd.cross} · DIF ${nf(t.macd.dif, 2)}`);
    add('KDJ', `${nf(t.kdj.k, 1)} / ${nf(t.kdj.d, 1)}`, `J ${nf(t.kdj.j, 1)}`);
    add('布林位置', isNum(t.boll.pos) ? `${(t.boll.pos * 100).toFixed(0)}%` : '—', `带宽 ${pct(t.boll.width, 1)}`);
    add('ATR(14)', pct(t.atr_pct, 2), '日均真实波幅');
    add('量比', nf(t.vol_ratio, 2), '5/60 日均量');
    add('20日年化波动', pct(t.vol20_ann, 1), `历史分位 ${isNum(t.vol_percentile) ? `${t.vol_percentile.toFixed(0)}%` : '—'}`);
    add('当前回撤', pct(d.performance.current_dd, 1), '距历史高点');
    add('52周区间', `${nf(t.lo52, 2)} ~ ${nf(t.hi52, 2)}`,
      `当前位于 ${isNum(t.pos52) ? `${(t.pos52 * 100).toFixed(0)}%` : '—'} 分位`, 'full');

    const bull = t.ma_bull_count;
    $('#techSub').textContent = `均线多头 ${bull}/4 · RSI ${nf(t.rsi14, 1)} · MACD ${t.macd.cross}`;
    $('#trendBadge').textContent = bull >= 3 ? '多头排列' : bull <= 1 ? '空头排列' : '均线纠缠';
    $('#trendBadge').className = `badge ${bull >= 3 ? 'up' : bull <= 1 ? 'down' : 'flat'}`;

    $('#techGrid').innerHTML = items.map((it) => `
      <div class="tech-item ${it.span || ''}">
        <div class="t-label"><span>${it.label}</span><span class="t-note">${esc(it.note)}</span></div>
        <div class="t-value">${it.value}</div>
      </div>`).join('');

    const lv = d.levels;
    const lcur = d.meta.currency === 'USD' ? '$' : '';
    const row = (l) => `<div class="level-row">
      <span class="l-label">${esc(l.label)}</span>
      <span class="l-price">${lcur}${nf(l.price, 2)}</span>
      <span class="l-dist ${cls(l.distance)}">${pctS(l.distance)}</span>
    </div>`;
    $('#levels').innerHTML = `
      <div class="level-col resistance"><h4>上方阻力（距现价）</h4>${lv.resistance.map(row).join('') || '<p class="muted">—</p>'}</div>
      <div class="level-col support"><h4>下方支撑（距现价）</h4>${lv.support.map(row).join('') || '<p class="muted">—</p>'}</div>`;
  }

  /* --------------------------- 渲染：区间收益 --------------------------- */
  function renderPeriods(d) {
    const list = d.periods || [];
    const maxAbs = Math.max(...list.map((p) => Math.abs(p.value || 0)), 0.01);
    $('#periodTable').innerHTML = list.map((p) => {
      const w = (Math.abs(p.value) / maxAbs) * 50;
      const color = p.value >= 0 ? 'rgba(246,70,93,0.75)' : 'rgba(46,184,114,0.75)';
      const margin = p.value >= 0 ? 50 : 50 - w;
      return `<div class="period-row">
        <span class="p-label">${esc(p.label)}</span>
        <span class="p-bar"><i style="width:${w.toFixed(1)}%;margin-left:${margin.toFixed(1)}%;background:${color}"></i></span>
        <span class="p-val ${cls(p.value)}">${pctS(p.value)}</span>
      </div>`;
    }).join('');

    const r = d.relative;
    const b = d.benchmark_performance;
    const mini = [
      ['Beta', nf(r.beta, 2)],
      ['年化 Alpha', pctS(r.alpha_annual)],
      ['信息比率', nf(r.info_ratio, 2)],
      ['跟踪误差', pct(r.tracking_error)],
      ['相关系数', nf(r.corr, 2)],
      ['基准夏普', nf(b.sharpe, 2)],
    ];
    $('#relStats').innerHTML = mini.map(([k, v]) => `
      <div class="mini"><div class="m-label">${k}</div><div class="m-value">${v}</div></div>`).join('');

    const a = d.advanced || {};
    const adv = [
      ['Omega', nf(a.omega, 2), 'Σ(r⁺) / Σ|r⁻|'],
      ['溃疡指数', pct(a.ulcer, 1), '√mean(DD²)'],
      ['Treynor', nf(a.treynor, 2), '(CAGR − r_f) / β'],
      ['M²', pctS(a.m2, 1), 'r_f + Sharpe × σ_m'],
      ['Kelly 仓位', pct(a.kelly, 0), '(CAGR − r_f) / σ²'],
      ['GARCH 次日波动', pct(a.garch_vol, 1), 'σ²_t = ω + αε² + βσ²'],
    ];
    $('#advStats').innerHTML = adv.map(([k, v, f]) => `
      <div class="mini" title="公式：${f}"><div class="m-label">${k}</div><div class="m-value">${v}</div></div>`).join('');
  }

  /* --------------------------- 渲染：回测 --------------------------- */
  function renderBacktest(d) {
    const bt = d.backtest;
    const m = bt.metrics;
    const bh = bt.bh_metrics;
    $('#btSub').textContent = `${bt.params.rule} · 单边成本 ${bt.params.cost_bps}bp`;

    $('#btBadges').innerHTML = [
      `交易 ${m.trades} 次`,
      `胜率 ${pct(m.win_rate, 0)}`,
      `持仓占比 ${pct(m.exposure, 0)}`,
    ].map((s) => `<span class="badge soft">${s}</span>`).join('');

    const cells = [
      ['策略年化', pctS(m.annual_return), cls(m.annual_return)],
      ['买入持有', pctS(bh.annual_return), cls(bh.annual_return)],
      ['年化超额', pctS((m.annual_return || 0) - (bh.annual_return || 0)),
        cls((m.annual_return || 0) - (bh.annual_return || 0))],
      ['策略最大回撤', pct(m.max_dd), 'down-text'],
      ['策略夏普', nf(m.sharpe, 2), ''],
      ['策略累计', pctS(m.cum_return), cls(m.cum_return)],
    ];
    $('#btMetrics').innerHTML = cells.map(([k, v, c]) => `
      <div class="bt-metric"><div class="b-label">${k}</div><div class="b-value ${c}">${v}</div></div>`).join('');

    $('#btGrid').innerHTML = `
      <table class="grid-table">
        <thead><tr><th>参数</th><th>年化</th><th>回撤</th><th>夏普</th><th>交易</th></tr></thead>
        <tbody>${bt.grid.map((g, i) => `
          <tr class="${i === 0 ? 'best' : ''}">
            <td>MA${g.fast}/${g.slow}</td>
            <td>${pctS(g.annual)}</td>
            <td>${pct(g.max_dd)}</td>
            <td>${nf(g.sharpe, 2)}</td>
            <td>${g.trades}</td>
          </tr>`).join('')}</tbody>
      </table>`;

    drawChart($('#btChart'), {
      labels: bt.dates,
      height: 300,
      series: [
        { name: '策略净值', data: bt.equity, color: '#8fa6ff', width: 1.9 },
        { name: '买入持有', data: bt.bh_equity, color: '#5d6b8c', width: 1.3, dash: '4 3' },
      ],
      fmt: (v) => nf(v, 3),
      fmtAxis: (v) => nf(v, 2),
      baseline: 1,
    });
  }

  /* --------------------------- 渲染：蒙特卡洛 --------------------------- */
  function renderMonte(d) {
    const mc = d.monte || {};
    drawChart($('#fanChart'), {
      labels: mc.dates || [],
      height: 250,
      series: [{ name: '中位数路径', data: mc.p50 || [], color: '#8fa6ff', width: 1.8 }],
      bands: [
        { lo: mc.p5 || [], hi: mc.p95 || [], color: 'rgba(109,140,255,0.12)' },
        { lo: mc.p25 || [], hi: mc.p75 || [], color: 'rgba(109,140,255,0.20)' },
      ],
      baseline: mc.last,
      fmt: (v) => nf(v, 2),
      fmtAxis: (v) => nf(v, 0),
      xfmt: (s) => String(s).slice(5),
    });

    const items = [
      ['上涨概率', pct(mc.prob_up, 1)],
      ['涨超 10%', pct(mc.prob_up10, 1)],
      ['跌超 10%', pct(mc.prob_down10, 1)],
      ['中位数目标', nf(mc.term_p50, 2)],
    ];
    $('#mcStats').innerHTML = items.map(([k, v]) => `
      <div class="mc-item"><div class="c-label">${k}</div><div class="c-value">${v}</div></div>`).join('');
  }

  /* --------------------------- 渲染：月度热力 --------------------------- */
  function heatColor(v) {
    if (!isNum(v)) return 'transparent';
    const t = clamp(Math.abs(v) / 0.12, 0, 1);
    const alpha = 0.08 + t * 0.7;
    return v >= 0 ? `rgba(246,70,93,${alpha.toFixed(2)})` : `rgba(46,184,114,${alpha.toFixed(2)})`;
  }

  function renderMonthly(d) {
    const rows = d.monthly;
    const head = `<tr><th>年份</th>${rows.months.map((m) => `<th>${m}月</th>`).join('')}<th>全年</th></tr>`;
    const body = rows.years.map((yr, yi) => {
      let compound = 1;
      let counted = 0;
      const cells = rows.months.map((_, mi) => {
        const v = rows.values[yi][mi];
        if (isNum(v)) { compound *= (1 + v); counted += 1; }
        return `<td style="background:${heatColor(v)}" title="${yr}-${mi + 1}: ${pctS(v)}">${isNum(v) ? `${(v * 100).toFixed(1)}` : ''}</td>`;
      }).join('');
      const annual = counted ? compound - 1 : null;
      return `<tr><td class="year">${yr}</td>${cells}<td class="annual ${cls(annual)}" style="background:${heatColor(annual)}">${isNum(annual) ? `${(annual * 100).toFixed(1)}` : '—'}</td></tr>`;
    }).join('');
    $('#heatmap').innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;

    // 统计
    const flat = [];
    rows.values.forEach((arr) => arr.forEach((v) => { if (isNum(v)) flat.push(v); }));
    if (!flat.length) { $('#monthlySummary').innerHTML = ''; return; }
    const best = flat.reduce((a, b) => (b > a ? b : a));
    const worst = flat.reduce((a, b) => (b < a ? b : a));
    const posRatio = flat.filter((v) => v > 0).length / flat.length;
    $('#monthlySummary').innerHTML = `
      <span>正收益月份 <b>${(posRatio * 100).toFixed(0)}%</b></span>
      <span>最佳单月 <b class="up-text">${pctS(best)}</b></span>
      <span>最差单月 <b class="down-text">${pctS(worst)}</b></span>`;
  }

  /* --------------------------- 渲染：数据源 --------------------------- */
  function renderLinks(d) {
    const m = d.meta;
    $('#metaLine').textContent =
      `${m.name}（${m.code}${m.market === 'gold' ? '' : '.' + (m.market || '').toUpperCase()}）· ${m.adjusted}日线 ${m.bars} 根 · `
      + `基准 ${m.benchmark.name} · 抓取时间 ${m.fetched_at}`;    $('#sourceLinks').innerHTML = (m.links || []).map((l) => `
      <a href="${esc(l.url)}" target="_blank" rel="noopener">
        <span>${esc(l.label)} ↗</span><small>${esc(l.note)}</small>
      </a>`).join('');
    $('#disclaimer').textContent = d.disclaimer;
    $('#externalLink').href = (m.links && m.links[0]) ? m.links[0].url : 'https://gu.qq.com/';
  }

  /* --------------------------- 汇总渲染 --------------------------- */
  function renderAll(d) {
    renderHero(d);
    renderKpi(d);
    renderScore(d);
    renderCalc(d);
    renderTech(d);
    renderPeriods(d);
    renderBacktest(d);
    renderMonte(d);
    renderMonthly(d);
    renderLinks(d);
    drawMainChart(d);
    state.data = d;
  }

  /* --------------------------- 涨跌榜 --------------------------- */
  const RANK_MARKET = { cn: 'A 股', us: '美股', gold: '黄金' };
  let rankMarket = 'cn';

  async function showRank(market = rankMarket) {
    rankMarket = market;
    stopLive();
    nav.lastList = { type: 'rank', market };
    showBack(false);
    $$('#rankTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = false;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#rankMeta').textContent = `${RANK_MARKET[market]} · 正在加载…`;
    $('#upCount').textContent = '';
    $('#downCount').textContent = '';
    $('#upTable').innerHTML = '<div class="rank-loading">加载中…</div>';
    $('#downTable').innerHTML = '<div class="rank-loading">加载中…</div>';
    try {
      const body = await api('/api/rank', { market }, 30000);
      renderRank(body.data);
    } catch (err) {
      $('#upTable').innerHTML = `<div class="rank-loading">${esc(err.message)}</div>`;
      $('#downTable').innerHTML = '';
      $('#rankMeta').textContent = `${RANK_MARKET[market]} · 加载失败`;
    }
  }

  function renderRank(d) {
    $('#rankMeta').textContent = `${d.label} · 样本 ${d.universe} 只 · 更新于 ${d.updated}`;
    $('#upCount').textContent = `涨幅前 ${d.up.length}`;
    $('#downCount').textContent = `跌幅前 ${d.down.length}`;
    const row = (item, i) => {
      const cur = item.currency === 'USD' ? '$' : '';
      return `<button type="button" class="rank-row" data-symbol="${esc(item.symbol)}" title="点击分析 ${esc(item.name)}">
        <span class="r-idx">${i + 1}</span>
        <span class="r-name"><b>${esc(item.name)}</b><small>${esc(item.code)}</small></span>
        <span class="r-price">${cur}${nf(item.price, 2)}</span>
        <span class="r-pct ${cls(item.change_pct)}">${pctS(item.change_pct / 100, 2)}</span>
        <span class="r-amt">${cur}${money(item.amount)}</span>
      </button>`;
    };
    $('#upTable').innerHTML = d.up.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
    $('#downTable').innerHTML = d.down.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
  }

  $('#rankBtn').addEventListener('click', () => showRank());
  $('#rankTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showRank(btn.dataset.market);
  });
  $('#rankRefresh').addEventListener('click', () => showRank(rankMarket));
  $('#rankClose').addEventListener('click', () => goHome());
  $('#rankView').addEventListener('click', (e) => {
    const row = e.target.closest('.rank-row');
    if (!row) return;
    $('#rankView').hidden = true;
    analyze(row.dataset.symbol);
  });

  /* --------------------------- 量化评分板 --------------------------- */
  const SCORE_MARKET = { cn: 'A 股', us: '美股' };
  let scoreMarket = 'cn';
  let scoreTimer = null;

  async function showScoreboard(market = scoreMarket) {
    scoreMarket = market;
    stopLive();
    nav.lastList = { type: 'score', market };
    showBack(false);
    $$('#scoreTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = false;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#scoreMeta').textContent = `${SCORE_MARKET[market]} · 正在准备候选池…`;
    $('#scoreTable').innerHTML = '<div class="rank-loading">加载中…</div>';
    clearInterval(scoreTimer);
    await loadScoreboard();
    scoreTimer = setInterval(loadScoreboard, 1800);
  }

  async function loadScoreboard() {
    if ($('#scoreView').hidden) {
      clearInterval(scoreTimer);
      return;
    }
    try {
      const body = await api('/api/scoreboard', { market: scoreMarket }, 30000);
      renderScoreboard(body.data);
      if (body.data.status === 'ok') clearInterval(scoreTimer);
    } catch (err) {
      $('#scoreMeta').textContent = `${SCORE_MARKET[scoreMarket]} · 计算失败：${err.message}`;
      clearInterval(scoreTimer);
    }
  }

  function renderScoreboard(d) {
    const items = d.items || d.partial || [];
    if (d.status === 'computing' && !items.length) {
      const progress = d.total ? ` ${d.progress}/${d.total}` : '';
      $('#scoreMeta').textContent = `${SCORE_MARKET[d.market]} · 正在计算${progress}…`;
      $('#scoreTable').innerHTML = `<div class="rank-loading">正在逐只计算五维评分${progress}…</div>`;
      return;
    }
    $('#scoreMeta').textContent = d.status === 'ok'
      ? `${SCORE_MARKET[d.market]} · 前 ${items.length} 名 · 已扫描 ${d.scanned} 只 · 更新于 ${d.updated}`
      : `${SCORE_MARKET[d.market]} · 计算中 ${d.progress}/${d.total}…`;
    const row = (it, i) => {
      const cur = it.symbol.startsWith('us') ? '$' : '';
      return `<button type="button" class="score-row" data-symbol="${esc(it.symbol)}"
        title="点击分析 ${esc(it.name)}">
        <span class="s-rank">${i + 1}</span>
        <span class="s-name"><b>${esc(it.name)}</b><small>${esc(it.code)} · ${esc(it.signal)}</small></span>
        <span class="s-price">${cur}${nf(it.price, 2)}</span>
        <span class="s-chg ${cls(it.change_pct)}">${pctS(it.change_pct / 100, 2)}</span>
        <span class="s-ann ${cls(it.annual_1y)}">${pctS((it.annual_1y || 0) / 100, 1)}</span>
        <span class="s-score">${nf(it.score, 1)}</span>
        <span class="grade-pill ${it.tone}">${esc(it.grade)}</span>
      </button>`;
    };
    $('#scoreTable').innerHTML = items.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
  }

  $('#scoreBtn').addEventListener('click', () => showScoreboard());
  $('#scoreTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showScoreboard(btn.dataset.market);
  });
  $('#scoreRefresh').addEventListener('click', () => showScoreboard(scoreMarket));
  $('#scoreClose').addEventListener('click', goHome);
  $('#scoreTable').addEventListener('click', (e) => {
    const row = e.target.closest('.score-row');
    if (!row) return;
    $('#scoreView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    analyze(row.dataset.symbol);
  });

  /* --------------------------- 资金模拟 --------------------------- */
  const SIM_MARKET = { cn: 'A 股', us: '美股' };
  const SIM_DEFAULT = { cn: 100000, us: 10000 };
  let simMarket = 'cn';
  let simRisk = 'balanced';
  let simRetry = 0;

  function showSim(market = simMarket) {
    const prev = simMarket;
    simMarket = market;
    stopLive();
    nav.lastList = null;
    showBack(false);
    $$('#simTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = false;
    $('#simCur').textContent = market === 'cn' ? '¥' : '$';
    const amountEl = $('#simAmount');
    if (!amountEl.value || Number(amountEl.value) === SIM_DEFAULT[prev]) {
      amountEl.value = SIM_DEFAULT[market];
    }
    $('#simHint').textContent = market === 'cn'
      ? 'A 股 T+1、100 股/手、涨跌停限制；金额按整手向下取整。'
      : '美股 T+0、1 股起、无涨跌停；金额按整股向下取整。';
    $('#simMeta').textContent = `${SIM_MARKET[market]} · 输入金额与持有天数，按资金档位与风险偏好给出配置建议`;
    $('#simResult').innerHTML = '';
  }

  async function runSim() {
    const amount = Number($('#simAmount').value);
    const days = Number($('#simDays').value);
    if (!(amount > 0)) {
      $('#simResult').innerHTML = '<div class="card sim-block muted">请输入有效金额</div>';
      return;
    }
    if (!(days >= 1 && days <= 250)) {
      $('#simResult').innerHTML = '<div class="card sim-block muted">天数需在 1–250 之间</div>';
      return;
    }
    $('#simRun').disabled = true;
    $('#simResult').innerHTML = '<div class="card sim-block muted">正在拉取日线并模拟路径…</div>';
    try {
      const body = await api('/api/simulate', { market: simMarket, amount, days, risk: simRisk }, 90000);
      const d = body.data;
      if (d.status === 'computing') {
        simRetry += 1;
        $('#simResult').innerHTML = `<div class="card sim-block muted">评分板还在计算（${d.progress || 0}/${d.total || 0}），请稍候…</div>`;
        if (simRetry <= 12) setTimeout(runSim, 2500);
        else $('#simRun').disabled = false;
        return;
      }
      simRetry = 0;
      if (d.status !== 'ok') {
        $('#simResult').innerHTML = `<div class="card sim-block muted">${esc(d.error || '模拟失败')}</div>`;
        return;
      }
      renderSim(d);
    } catch (err) {
      $('#simResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      if (simRetry === 0) $('#simRun').disabled = false;
    }
  }

  function renderSim(d) {
    const cur = d.symbol || '¥';
    const picks = d.picks.map((p, i) => `
      <div class="sim-row">
        <span class="n">${i + 1}</span>
        <span><b>${esc(p.name)}</b><br><small>${esc(p.code)} · 评分 ${nf(p.score, 1)} · 波动 ${nf(p.vol, 1)}%</small></span>
        <span class="n c-price">${cur}${nf(p.price, 2)}</span>
        <span class="n">${pct(p.weight, 1)}</span>
        <span class="n">${nf(p.shares, 0)}</span>
        <span class="n c-cost">${cur}${nf(p.cost, 0)}</span>
      </div>`).join('');

    let stats = '';
    if (d.sim) {
      stats = `<div class="sim-stats">
        <div class="sim-stat"><div class="s-label">期末中位数</div><div class="s-value">${cur}${nf(d.sim.p50, 0)}</div><div class="s-sub">初始 ${cur}${nf(d.amount, 0)}</div></div>
        <div class="sim-stat"><div class="s-label">5% ~ 95% 区间</div><div class="s-value">${cur}${nf(d.sim.p05, 0)} ~ ${cur}${nf(d.sim.p95, 0)}</div><div class="s-sub">3000 条路径</div></div>
        <div class="sim-stat"><div class="s-label">盈利概率</div><div class="s-value">${nf(d.sim.prob_profit, 1)}%</div><div class="s-sub">期末高于初始</div></div>
        <div class="sim-stat"><div class="s-label">最大回撤（中位）</div><div class="s-value">${nf(d.sim.median_mdd, 2)}%</div><div class="s-sub">持有 ${d.days} 天</div></div>
        <div class="sim-stat"><div class="s-label">预计费用</div><div class="s-value">${cur}${nf(d.fees, 0)}</div><div class="s-sub">税费与佣金估算</div></div>
        <div class="sim-stat"><div class="s-label">剩余现金</div><div class="s-value">${cur}${nf(d.cash, 0)}</div><div class="s-sub">已投入 ${cur}${nf(d.invested, 0)}</div></div>
      </div>`;
    }

    $('#simResult').innerHTML = `
      <div class="card sim-tier">
        <div>
          <div class="t-label">资金档位</div>
          <div class="t-big">${esc(d.tier.label)}</div>
          <div class="t-sub">建议 ${d.tier.count} 只 · 单票 ≤${nf(d.tier.cap * 100, 0)}% · 留 ${nf(d.tier.reserve * 100, 0)}% 现金</div>
        </div>
        <div><div class="t-label">对应心态与仓位建议</div><p>${esc(d.tier.mood)}</p></div>
      </div>
      <div class="card sim-block">
        <h3>${d.market === 'cn' ? 'A 股' : '美股'}交易规则</h3>
        <ul class="sim-rules">${d.rules.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
      </div>
      <div class="card sim-block">
        <h3>买入建议（按评分、波动与可买性）</h3>
        <div class="sim-row head"><span>#</span><span>名称 / 代码</span><span class="n">现价</span><span class="n">权重</span><span class="n">股数</span><span class="n">金额</span></div>
        ${picks}
      </div>
      <div class="card sim-block">
        <h3>持有 ${d.days} 天模拟</h3>
        ${stats || '<p class="muted">历史数据不足，未生成路径模拟。</p>'}
      </div>`;
  }

  $('#simBtn').addEventListener('click', () => showSim());
  $('#simTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showSim(btn.dataset.market);
  });
  $('#simRisk').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-risk]');
    if (!btn) return;
    simRisk = btn.dataset.risk;
    $$('#simRisk button').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#simRun').addEventListener('click', runSim);
  $('#simClose').addEventListener('click', goHome);

  /* --------------------------- 量化策略 --------------------------- */
  const STRATEGY_MARKET = { cn: 'A 股', us: '美股' };
  let strategyMarket = 'cn';
  let strategyLookback = 60;
  let strategyTop = 10;
  let strategyRetry = 0;

  function showStrategy(market = strategyMarket) {
    strategyMarket = market;
    showBack(false);
    nav.lastList = null;
    $$('#strategyTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#strategyView').hidden = false;
    $('#strategyResult').innerHTML = '';
    runStrategy();
    loadTracking();
  }

  async function runStrategy() {
    $('#strategyRun').disabled = true;
    $('#strategyResult').innerHTML = '<div class="card sim-block muted">正在回测策略…</div>';
    try {
      const body = await api('/api/strategy', {
        market: strategyMarket, lookback: strategyLookback, top: strategyTop,
      }, 120000);
      const d = body.data;
      if (d.status === 'computing') {
        strategyRetry += 1;
        $('#strategyResult').innerHTML = `<div class="card sim-block muted">评分板计算中（${d.progress || 0}/${d.total || 0}）…</div>`;
        if (strategyRetry <= 20) setTimeout(runStrategy, 2500);
        else $('#strategyRun').disabled = false;
        return;
      }
      strategyRetry = 0;
      if (d.status !== 'ok') {
        $('#strategyResult').innerHTML = `<div class="card sim-block muted">${esc(d.error || '回测失败')}</div>`;
        return;
      }
      renderStrategy(d);
    } catch (err) {
      $('#strategyResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      if (strategyRetry === 0) $('#strategyRun').disabled = false;
    }
  }

  function drawStrategyChart(el, curve) {
    if (!curve || curve.length < 2) return;
    const w = 900, h = 280, pad = 30;
    const s = curve.map((p) => p.s), b = curve.map((p) => p.b);
    const lo = Math.min(...s, ...b), hi = Math.max(...s, ...b);
    const x = (i) => pad + i * (w - 2 * pad) / (curve.length - 1);
    const y = (v) => h - pad - (v - lo) / (hi - lo || 1) * (h - 2 * pad);
    const path = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
    el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" class="strat-svg">
      <path d="${path(b)}" fill="none" stroke="#4a5a7a" stroke-width="1.5"/>
      <path d="${path(s)}" fill="none" stroke="#6d8cff" stroke-width="2"/>
      <text x="${pad}" y="${pad - 8}" fill="#8fa6ff" font-size="11">策略</text>
      <text x="${pad + 44}" y="${pad - 8}" fill="#7b8aa8" font-size="11">基准</text>
    </svg>`;
  }

  function renderStrategy(d) {
    const s = d.stats;
    $('#strategyMeta').textContent =
      `${STRATEGY_MARKET[d.market]} · ${d.lookback} 日动量 − 0.5×20日波动 · 前 ${d.top} 等权`
      + ` · 调仓 ${d.rebalances} 次 · 更新于 ${d.updated}`;
    const stats = [
      ['累计收益', `${nf(s.total, 1)}%`],
      ['年化收益', `${nf(s.cagr, 1)}%`],
      ['年化波动', `${nf(s.vol, 1)}%`],
      ['夏普', nf(s.sharpe, 2)],
      ['最大回撤', `${nf(s.maxdd, 1)}%`],
      ['超额 vs 基准', `${nf(s.excess, 1)}%`],
    ];
    const holdings = d.holdings.map((h) => `
      <tr>
        <td>${esc(h.name)} <small style="color:var(--dim)">${esc(h.code)}</small></td>
        <td>${nf(h.weight, 1)}%</td>
        <td>${nf(h.factor, 3)}</td>
      </tr>`).join('');
    $('#strategyResult').innerHTML = `
      <div class="sim-stats">${stats.map(([k, v]) => `
        <div class="sim-stat"><div class="s-label">${k}</div><div class="s-value">${v}</div></div>`).join('')}
      </div>
      <div class="card sim-block">
        <h3>策略净值 vs 基准</h3>
        <div id="stratChart"></div>
        <p class="sim-hint">基准年化 ${nf(s.bench_cagr, 1)}%；月均换手 ${nf(s.turnover, 1)}%；单边成本 ${d.cost_bps}bp。</p>
      </div>
      <div class="card sim-block">
        <h3>当前持仓</h3>
        <table class="research-table">
          <thead><tr><th>名称</th><th>权重</th><th>因子值</th></tr></thead>
          <tbody>${holdings}</tbody>
        </table>
        <p class="sim-hint">风险调整动量 = 60 日收益 − 0.5×20 日年化波动；仅历史回测，不构成投资建议。</p>
      </div>`;
    drawStrategyChart($('#stratChart'), d.curve);
  }

  $('#strategyBtn').addEventListener('click', () => showStrategy());
  $('#strategyTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showStrategy(btn.dataset.market);
  });
  $('#strategyLookback').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-lookback]');
    if (!btn) return;
    strategyLookback = Number(btn.dataset.lookback);
    $$('#strategyLookback button').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#strategyTop').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-top]');
    if (!btn) return;
    strategyTop = Number(btn.dataset.top);
    $$('#strategyTop button').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#strategyRun').addEventListener('click', runStrategy);
  $('#strategyClose').addEventListener('click', goHome);

  async function loadTracking() {
    try {
      const body = await api('/api/tracking', { market: strategyMarket }, 30000);
      renderTracking(body.data);
    } catch (err) { /* 静默 */ }
  }

  function renderTracking(d) {
    if (d.status !== 'ok') {
      $('#trackingResult').innerHTML = `<div class="card sim-block muted">${esc(d.message || '暂无跟踪中的策略')}</div>`;
      return;
    }
    const stats = [
      ['跟踪天数', nf(d.days, 0)],
      ['累计收益', `${nf(d.return, 2)}%`],
      ['超额 vs 基准', `${nf(d.excess, 2)}%`],
      ['最大回撤', `${nf(d.maxdd, 2)}%`],
    ];
    const holdings = d.holdings.map((h) => `
      <tr>
        <td>${esc(h.name)} <small style="color:var(--dim)">${esc(h.code)}</small></td>
        <td>${nf(h.weight, 1)}%</td>
        <td>${nf(h.base, 2)}</td>
      </tr>`).join('');
    const points = (d.points || []).slice(-8).reverse().map((p) => `
      <tr><td>${esc(p.date)}</td><td>${nf(p.nav, 4)}</td><td>${nf(p.bench, 4)}</td></tr>`).join('');
    $('#trackingResult').innerHTML = `
      <div class="sim-stats">${stats.map(([k, v]) => `
        <div class="sim-stat"><div class="s-label">${k}</div><div class="s-value">${v}</div></div>`).join('')}
      </div>
      <div class="card sim-block">
        <h3>跟踪组合（自 ${esc(d.start)}）</h3>
        <table class="research-table">
          <thead><tr><th>名称</th><th>权重</th><th>基准价</th></tr></thead>
          <tbody>${holdings}</tbody>
        </table>
        <p class="sim-hint">每 21 个交易日调仓，单边 12bp；每日按最新收盘更新，净值与基准均以 1 为起点。</p>
      </div>
      <div class="card sim-block">
        <h3>最近净值</h3>
        <table class="research-table">
          <thead><tr><th>日期</th><th>策略净值</th><th>基准净值</th></tr></thead>
          <tbody>${points || '<tr><td colspan="3" class="muted">暂无记录</td></tr>'}</tbody>
        </table>
      </div>`;
  }

  async function trackAction(path, payload) {
    $('#trackStart').disabled = true;
    $('#trackRefresh').disabled = true;
    try {
      const body = await apiPost(path, payload, 120000);
      renderTracking(body.data);
    } catch (err) {
      $('#trackingResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      $('#trackStart').disabled = false;
      $('#trackRefresh').disabled = false;
    }
  }

  $('#trackStart').addEventListener('click', () => trackAction('/api/tracking/create', {
    market: strategyMarket, lookback: strategyLookback, top: strategyTop,
  }));
  $('#trackRefresh').addEventListener('click', () => trackAction('/api/tracking/update', {
    market: strategyMarket,
  }));

  /* --------------------------- 因子研究 --------------------------- */
  const RESEARCH_MARKET = { cn: 'A 股', us: '美股' };
  let researchMarket = 'cn';
  let researchFactor = 'mom20';
  let researchRetry = 0;

  function showResearch(market = researchMarket) {
    researchMarket = market;
    showBack(false);
    nav.lastList = null;
    $$('#researchTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#researchView').hidden = false;
    $('#researchMeta').textContent = `${RESEARCH_MARKET[market]} · 横截面 IC / ICIR · 五分层 · 多空`;
    $('#researchResult').innerHTML = '';
  }

  async function runResearch() {
    $('#researchRun').disabled = true;
    $('#researchResult').innerHTML = '<div class="card sim-block muted">正在计算因子 IC 与分层组合…</div>';
    try {
      const body = await api('/api/research', { market: researchMarket, factor: researchFactor }, 120000);
      const d = body.data;
      if (d.status === 'computing') {
        researchRetry += 1;
        $('#researchResult').innerHTML = `<div class="card sim-block muted">评分板还在计算（${d.progress || 0}/${d.total || 0}），请稍候…</div>`;
        if (researchRetry <= 20) setTimeout(runResearch, 2500);
        else $('#researchRun').disabled = false;
        return;
      }
      researchRetry = 0;
      if (d.status !== 'ok') {
        $('#researchResult').innerHTML = `<div class="card sim-block muted">${esc(d.error || '研究失败')}</div>`;
        return;
      }
      renderResearch(d);
    } catch (err) {
      $('#researchResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      if (researchRetry === 0) $('#researchRun').disabled = false;
    }
  }

  function renderResearch(d) {
    $('#researchMeta').textContent =
      `${RESEARCH_MARKET[d.market]} · ${d.factor_name} · 股票池 ${d.n_stocks} 只 · ${d.n_periods} 个调仓期`
      + ` · 更新于 ${d.updated}`;
    const stats = [
      ['IC 均值', nf(d.ic_mean, 3)],
      ['ICIR', nf(d.icir, 2)],
      ['IC > 0 占比', `${nf(d.ic_pos, 1)}%`],
      ['样本内 IC', nf(d.is_ic, 3)],
      ['样本外 IC', d.os_ic == null ? '—' : nf(d.os_ic, 3)],
      ['多空年化', `${nf(d.ls_ann, 1)}%`],
    ];
    const maxAbs = Math.max(...d.quantiles.map((q) => Math.abs(q.ann)), 1);
    const bars = d.quantiles.map((q) => `
      <div class="q-row">
        <span class="q-label">${esc(q.group)}</span>
        <div class="q-track"><div class="q-fill" style="width:${Math.abs(q.ann) / maxAbs * 100}%;opacity:${q.ann >= 0 ? 1 : 0.45}"></div></div>
        <span class="q-val ${cls(q.ann / 100)}">${nf(q.ann, 1)}%</span>
      </div>`).join('');
    const rows = (d.periods || []).slice().reverse().map((p) => `
      <tr>
        <td>${esc(p.date)}</td>
        <td class="${cls(p.ic)}">${nf(p.ic, 3)}</td>
        <td class="${cls(p.q[0])}">${pctS(p.q[0], 2)}</td>
        <td class="${cls(p.q[4])}">${pctS(p.q[4], 2)}</td>
        <td class="${cls(p.ls)}">${pctS(p.ls, 2)}</td>
        <td>${nf(p.turnover * 100, 0)}%</td>
      </tr>`).join('');
    $('#researchResult').innerHTML = `
      <div class="sim-stats">${stats.map(([k, v]) => `
        <div class="sim-stat"><div class="s-label">${k}</div><div class="s-value">${v}</div></div>`).join('')}
      </div>
      <div class="card sim-block">
        <h3>五分层年化收益（Q1 因子值最高）</h3>
        <div class="q-bars">${bars}</div>
        <p class="sim-hint">多空 = Q1 − Q5，已扣换手与单边 12bp 成本；平均换手 ${nf(d.turnover, 1)}%。</p>
      </div>
      <div class="card sim-block">
        <h3>最近调仓期</h3>
        <table class="research-table">
          <thead><tr><th>日期</th><th>IC</th><th>Q1</th><th>Q5</th><th>多空</th><th>换手</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  $('#researchBtn').addEventListener('click', () => showResearch());
  $('#researchTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) {
      showResearch(btn.dataset.market);
      runResearch();
    }
  });
  $('#researchFactors').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-factor]');
    if (!btn) return;
    researchFactor = btn.dataset.factor;
    $$('#researchFactors button').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#researchRun').addEventListener('click', runResearch);
  $('#researchClose').addEventListener('click', goHome);

  /* --------------------------- 模拟盘 --------------------------- */
  let paperMarket = 'cn';
  let paperTimer = null;
  let paperStream = null;
  let notifyEnabled = false;
  const seenAlerts = new Set();

  async function apiPost(path, payload, timeoutMs = 30000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
      const body = await res.json();
      if (!res.ok || body.ok === false) throw new Error(body.error || `HTTP ${res.status}`);
      return body;
    } finally {
      clearTimeout(timer);
    }
  }

  function showPaper(market = paperMarket) {
    paperMarket = market;
    showBack(false);
    nav.lastList = null;
    $$('#paperTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#paperView').hidden = false;
    $('#paperMeta').textContent = `${market === 'cn' ? 'A 股' : '美股'} · 纸面交易 · 实时盯市`;
    $('#paperHint').textContent = market === 'cn'
      ? 'A 股 100 股/手、佣金最低 5 元、卖出印花税 0.05%。'
      : '美股 1 股起、佣金 0、T+0。';
    $('#paperResult').innerHTML = '<div class="card sim-block muted">加载账户…</div>';
    seenAlerts.clear();
    if (paperStream) { paperStream.close(); paperStream = null; }
    loadPaper();
    loadMonitor();
    clearInterval(paperTimer);
    paperTimer = setInterval(loadMonitor, 15000);
    try {
      paperStream = new EventSource(`/api/paper/stream?market=${market}`);
      paperStream.onmessage = (e) => {
        try {
          const body = JSON.parse(e.data);
          if (body.ok) renderPaper(body.data);
        } catch (err) { /* 忽略 */ }
      };
      paperStream.onerror = () => {
        if (!paperTimer) paperTimer = setInterval(loadPaper, 5000);
      };
    } catch (err) {
      paperTimer = setInterval(loadPaper, 5000);
    }
  }

  async function loadPaper() {
    if ($('#paperView').hidden) {
      clearInterval(paperTimer);
      return;
    }
    try {
      const body = await api('/api/paper', { market: paperMarket }, 20000);
      renderPaper(body.data);
      loadMonitor();
    } catch (err) {
      $('#paperResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    }
  }

  function renderPaper(d) {
    const stats = [
      ['总资产', nf(d.equity, 2)],
      ['现金', nf(d.cash, 2)],
      ['持仓市值', nf(d.market_value, 2)],
      ['持仓盈亏', nf(d.positions.reduce((s, p) => s + p.pnl, 0), 2)],
    ];
    const posRows = d.positions.map((p) => `
      <tr>
        <td>${esc(p.name)} <small style="color:var(--dim)">${esc(p.code)}</small></td>
        <td>${nf(p.shares, 0)}</td>
        <td>${nf(p.cost, 2)}</td>
        <td>${nf(p.price, 2)}</td>
        <td>${nf(p.value, 2)}</td>
        <td class="${cls(p.pnl)}">${p.pnl >= 0 ? '+' : ''}${nf(p.pnl, 2)}</td>
      </tr>`).join('');
    const tradeRows = (d.trades || []).map((t) => `
      <tr>
        <td>${esc(t.time)}</td>
        <td>${esc(t.name)}</td>
        <td class="${t.side === 'buy' ? 'up-text' : 'down-text'}">${t.side === 'buy' ? '买入' : '卖出'}</td>
        <td>${nf(t.shares, 0)}</td>
        <td>${nf(t.price, 2)}</td>
        <td>${nf(t.fee, 2)}</td>
      </tr>`).join('');
    $('#paperResult').innerHTML = `
      <div class="sim-stats">${stats.map(([k, v]) => `
        <div class="sim-stat"><div class="s-label">${k}</div><div class="s-value">${v}</div></div>`).join('')}
      </div>
      <div class="card sim-block">
        <h3>持仓</h3>
        <table class="research-table">
          <thead><tr><th>名称</th><th>股数</th><th>成本</th><th>现价</th><th>市值</th><th>盈亏</th></tr></thead>
          <tbody>${posRows || '<tr><td colspan="6" class="muted">暂无持仓</td></tr>'}</tbody>
        </table>
      </div>
      <div class="card sim-block">
        <h3>最近成交</h3>
        <table class="research-table">
          <thead><tr><th>时间</th><th>名称</th><th>方向</th><th>股数</th><th>价格</th><th>费用</th></tr></thead>
          <tbody>${tradeRows || '<tr><td colspan="6" class="muted">暂无成交</td></tr>'}</tbody>
        </table>
      </div>`;
  }

  async function loadMonitor() {
    if ($('#paperView').hidden) return;
    try {
      const body = await api('/api/monitor', { market: paperMarket }, 20000);
      const d = body.data;
      (d.alerts || []).forEach((a) => {
        if (a.level === 'high' && !seenAlerts.has(a.title)) {
          seenAlerts.add(a.title);
          if (notifyEnabled) {
            try { new Notification('量析告警', { body: `${a.title}：${a.detail}` }); } catch (err) { /* 忽略 */ }
          }
        }
      });
      const rows = (d.alerts || []).map((a) => `
        <div class="alert-row ${a.level}">
          <div>
            <div class="alert-title">${esc(a.title)}</div>
            <div class="alert-detail">${esc(a.detail)}</div>
          </div>
        </div>`).join('');
      $('#monitorResult').innerHTML = `
        <div class="card sim-block">
          <h3>监控告警 <span class="muted" style="font-size:11px">${esc(d.time || '')}</span></h3>
          ${rows}
        </div>`;
    } catch (err) { /* 静默 */ }
  }

  async function tradePaper(side) {
    const q = $('#paperQuery').value.trim();
    const qty = Number($('#paperQty').value);
    if (!q) {
      $('#paperResult').insertAdjacentHTML('afterbegin', '<div class="card sim-block muted">请输入代码或名称</div>');
      return;
    }
    try {
      const body = await apiPost('/api/paper/trade', {
        market: paperMarket, q, side, qty,
      }, 30000);
      renderPaper(body.data);
      $('#paperQuery').value = '';
    } catch (err) {
      $('#paperResult').insertAdjacentHTML('afterbegin', `<div class="card sim-block muted">${esc(err.message)}</div>`);
    }
  }

  $('#paperBtn').addEventListener('click', () => showPaper());
  $('#paperTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showPaper(btn.dataset.market);
  });
  $('#paperBuy').addEventListener('click', () => tradePaper('buy'));
  $('#paperSell').addEventListener('click', () => tradePaper('sell'));
  $('#paperNotify').addEventListener('click', async () => {
    if (!('Notification' in window)) {
      window.alert('当前浏览器不支持桌面通知');
      return;
    }
    const perm = await Notification.requestPermission();
    notifyEnabled = perm === 'granted';
    $('#paperNotify').textContent = notifyEnabled ? '通知已开' : '通知';
    if (notifyEnabled) {
      try { new Notification('量析 StockQuant', { body: '监控告警通知已开启' }); } catch (err) { /* 忽略 */ }
    }
  });
  $('#paperReset').addEventListener('click', async () => {
    if (!window.confirm('确认重置模拟盘？持仓和成交会清空。')) return;
    try {
      const body = await apiPost('/api/paper/reset', { market: paperMarket }, 20000);
      renderPaper(body.data);
    } catch (err) {
      $('#paperResult').insertAdjacentHTML('afterbegin', `<div class="card sim-block muted">${esc(err.message)}</div>`);
    }
  });
  $('#paperClose').addEventListener('click', goHome);

  /* --------------------------- 数据质量 --------------------------- */
  let qualityMarket = 'cn';
  let qualityRetry = 0;

  function showQuality(market = qualityMarket) {
    qualityMarket = market;
    showBack(false);
    nav.lastList = null;
    $$('#qualityTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#qualityView').hidden = false;
    $('#qualityResult').innerHTML = '';
    runQuality();
  }

  async function runQuality() {
    $('#qualityRun').disabled = true;
    $('#qualityResult').innerHTML = '<div class="card sim-block muted">正在检查数据质量…</div>';
    try {
      const body = await api('/api/quality', { market: qualityMarket, size: 20 }, 120000);
      const d = body.data;
      if (d.status === 'computing') {
        qualityRetry += 1;
        $('#qualityResult').innerHTML = `<div class="card sim-block muted">评分板计算中（${d.progress || 0}/${d.total || 0}）…</div>`;
        if (qualityRetry <= 20) setTimeout(runQuality, 2500);
        else $('#qualityRun').disabled = false;
        return;
      }
      qualityRetry = 0;
      if (d.status !== 'ok') {
        $('#qualityResult').innerHTML = `<div class="card sim-block muted">${esc(d.error || '检查失败')}</div>`;
        return;
      }
      renderQuality(d);
    } catch (err) {
      $('#qualityResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      if (qualityRetry === 0) $('#qualityRun').disabled = false;
    }
  }

  function renderQuality(d) {
    $('#qualityMeta').textContent =
      `${qualityMarket === 'cn' ? 'A 股' : '美股'} · 平均分 ${nf(d.avg_score, 1)} · 低于 70 分 ${d.bad} 只 · 更新于 ${d.updated}`;
    const rows = d.rows.map((r) => `
      <tr>
        <td>${esc(r.name)} <small style="color:var(--dim)">${esc(r.code)}</small></td>
        <td class="${r.score >= 85 ? 'up-text' : r.score < 70 ? 'down-text' : ''}">${nf(r.score, 0)}</td>
        <td>${nf(r.bars, 0)}</td>
        <td>${r.missing == null ? '—' : nf(r.missing, 1) + '%'}</td>
        <td>${r.zero == null ? '—' : nf(r.zero, 1) + '%'}</td>
        <td>${r.extreme == null ? '—' : nf(r.extreme, 0)}</td>
        <td>${esc(r.industry)}</td>
        <td class="muted">${esc((r.issues || []).join('；'))}</td>
      </tr>`).join('');
    $('#qualityResult').innerHTML = `
      <div class="card sim-block">
        <table class="research-table">
          <thead><tr><th>名称</th><th>数据分</th><th>K线</th><th>缺失</th><th>零成交</th><th>极端</th><th>行业</th><th>问题</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <p class="sim-hint">扣分规则：K 线不足 −20；数据滞后 −15；缺失 >5% −15；零成交 >5% −10；极端波动每次 −5（上限 15）；行业缺失 −5。</p>
      </div>`;
  }

  $('#qualityOpenBtn').addEventListener('click', () => showQuality());
  $('#qualityTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showQuality(btn.dataset.market);
  });
  $('#qualityRun').addEventListener('click', runQuality);
  $('#qualityClose').addEventListener('click', () => showResearch(qualityMarket));

  /* --------------------------- 基本面 --------------------------- */
  let fundamentalMarket = 'cn';
  let fundamentalRetry = 0;

  function showFundamental(market = fundamentalMarket) {
    fundamentalMarket = market;
    showBack(false);
    nav.lastList = null;
    $$('#fundamentalTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#fundamentalView').hidden = false;
    $('#fundamentalResult').innerHTML = '';
    runFundamental();
  }

  async function runFundamental() {
    $('#fundamentalRun').disabled = true;
    $('#fundamentalResult').innerHTML = '<div class="card sim-block muted">正在读取行业、估值与新闻…</div>';
    try {
      const body = await api('/api/fundamentals', { market: fundamentalMarket, size: 20 }, 120000);
      const d = body.data;
      if (d.status === 'computing') {
        fundamentalRetry += 1;
        $('#fundamentalResult').innerHTML = `<div class="card sim-block muted">评分板计算中（${d.progress || 0}/${d.total || 0}）…</div>`;
        if (fundamentalRetry <= 20) setTimeout(runFundamental, 2500);
        else $('#fundamentalRun').disabled = false;
        return;
      }
      fundamentalRetry = 0;
      if (d.status !== 'ok') {
        $('#fundamentalResult').innerHTML = `<div class="card sim-block muted">${esc(d.error || '加载失败')}</div>`;
        return;
      }
      renderFundamental(d);
    } catch (err) {
      $('#fundamentalResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      if (fundamentalRetry === 0) $('#fundamentalRun').disabled = false;
    }
  }

  function renderFundamental(d) {
    const rows = d.rows.map((r) => `
      <tr>
        <td>${esc(r.name)} <small style="color:var(--dim)">${esc(r.code)}</small></td>
        <td>${esc(r.industry)}</td>
        <td>${r.market_cap == null ? '—' : nf(r.market_cap, 0)}</td>
        <td>${r.pe == null ? '—' : nf(r.pe, 1)}</td>
        <td>${r.pb == null ? '—' : nf(r.pb, 2)}</td>
        <td>${r.roe == null ? '—' : nf(r.roe, 1) + '%'}</td>
        <td class="${cls((r.rev_yoy || 0) / 100)}">${r.rev_yoy == null ? '—' : pctS(r.rev_yoy / 100, 1)}</td>
        <td class="${cls((r.profit_yoy || 0) / 100)}">${r.profit_yoy == null ? '—' : pctS(r.profit_yoy / 100, 1)}</td>
        <td>${r.news_score == null ? '—' : nf(r.news_score, 0)}</td>
        <td title="${esc(r.gap_note || '')}" class="${cls((r.gap || 0) / 100)}">${r.gap == null ? '—' : (r.gap > 0 ? '+' : '') + nf(r.gap, 1)}</td>
      </tr>`).join('');
    const chips = d.industries.map((x) => `<span class="pill">${esc(x.name)} ${x.count}</span>`).join(' ');
    $('#fundamentalMeta').textContent =
      `${fundamentalMarket === 'cn' ? 'A 股' : '美股'} · 前 ${d.rows.length} 只 · 更新于 ${d.updated}`;
    $('#fundamentalResult').innerHTML = `
      <div class="card sim-block"><h3>行业分布</h3><div>${chips || '—'}</div></div>
      <div class="card sim-block"><h3>估值、盈利与事件</h3>
        <table class="research-table">
          <thead><tr><th>名称</th><th>行业</th><th>市值(亿)</th><th>PE</th><th>PB</th><th>ROE</th><th>营收同比</th><th>净利同比</th><th>消息分</th><th>预期差</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <p class="sim-hint">预期差 = 消息分 − 股价反应分；正值表示消息偏多但股价未充分反应。美股盈利同比字段缺失时显示 —。</p>
      </div>`;
  }

  $('#fundamentalOpenBtn').addEventListener('click', () => showFundamental());
  $('#fundamentalTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showFundamental(btn.dataset.market);
  });
  $('#fundamentalRun').addEventListener('click', runFundamental);
  $('#fundamentalClose').addEventListener('click', () => showResearch(fundamentalMarket));

  /* --------------------------- 组合优化 --------------------------- */
  const PORTFOLIO_MARKET = { cn: 'A 股', us: '美股' };
  let portfolioMarket = 'cn';
  let portfolioMethod = 'max_sharpe';
  let portfolioCap = 0.25;
  let portfolioRetry = 0;

  function showPortfolio(market = portfolioMarket) {
    portfolioMarket = market;
    showBack(false);
    nav.lastList = null;
    $$('#portfolioTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#portfolioView').hidden = false;
    $('#portfolioMeta').textContent = `${PORTFOLIO_MARKET[market]} · 协方差收缩 · 仅做多 · 单票上限 ${nf(portfolioCap * 100, 0)}%`;
    $('#portfolioResult').innerHTML = '';
  }

  async function runPortfolio() {
    $('#portfolioRun').disabled = true;
    $('#portfolioResult').innerHTML = '<div class="card sim-block muted">正在估计协方差并求解权重…</div>';
    try {
      const body = await api('/api/portfolio', {
        market: portfolioMarket,
        method: portfolioMethod,
        size: 20,
        cap: portfolioCap,
      }, 120000);
      const d = body.data;
      if (d.status === 'computing') {
        portfolioRetry += 1;
        $('#portfolioResult').innerHTML = `<div class="card sim-block muted">评分板还在计算（${d.progress || 0}/${d.total || 0}），请稍候…</div>`;
        if (portfolioRetry <= 20) setTimeout(runPortfolio, 2500);
        else $('#portfolioRun').disabled = false;
        return;
      }
      portfolioRetry = 0;
      if (d.status !== 'ok') {
        $('#portfolioResult').innerHTML = `<div class="card sim-block muted">${esc(d.error || '优化失败')}</div>`;
        return;
      }
      renderPortfolio(d);
    } catch (err) {
      $('#portfolioResult').innerHTML = `<div class="card sim-block muted">${esc(err.message)}</div>`;
    } finally {
      if (portfolioRetry === 0) $('#portfolioRun').disabled = false;
    }
  }

  function renderPortfolio(d) {
    const s = d.stats;
    $('#portfolioMeta').textContent =
      `${PORTFOLIO_MARKET[d.market]} · ${d.method_name} · ${d.n_stocks} 只 · 单票上限 ${nf(d.cap * 100, 0)}%`
      + ` · 收缩强度 ${nf(d.shrinkage, 2)} · 更新于 ${d.updated}`;
    const stats = [
      ['预期年化', `${nf(s.expected_return, 1)}%`],
      ['年化波动', `${nf(s.volatility, 1)}%`],
      ['夏普', nf(s.sharpe, 2)],
      ['分散化比率', nf(s.diversification, 2)],
      ['有效持仓数', nf(s.effective_n, 1)],
      ['最大单票', `${nf(s.max_weight, 1)}%`],
    ];
    const shown = d.weights.filter((w) => w.weight > 0.01);
    const maxWeight = Math.max(...shown.map((w) => w.weight), 1);
    const rows = shown.map((w) => `
      <tr>
        <td>${esc(w.name)} <small style="color:var(--dim)">${esc(w.code)}</small></td>
        <td>
          <span class="wbar"><i style="width:${w.weight / maxWeight * 100}%"></i></span>
          ${nf(w.weight, 1)}%
        </td>
        <td>${nf(w.risk, 1)}%</td>
      </tr>`).join('');
    $('#portfolioResult').innerHTML = `
      <div class="sim-stats">${stats.map(([k, v]) => `
        <div class="sim-stat"><div class="s-label">${k}</div><div class="s-value">${v}</div></div>`).join('')}
      </div>
      <div class="card sim-block">
        <h3>权重与风险贡献</h3>
        <table class="research-table">
          <thead><tr><th>名称</th><th>权重</th><th>风险贡献</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <p class="sim-hint">Σ = δ·diag(S) + (1−δ)·S；最大夏普 w ∝ Σ⁻¹(μ−r_f)；风险平价令 w_i(Σw)_i 相等；
          最大分散最大化 (w·σ)/√(w'Σw)。预期收益估计误差较大，结果仅供研究。</p>
      </div>`;
  }

  $('#portfolioOpenBtn').addEventListener('click', () => {
    showPortfolio(researchMarket);
    runPortfolio();
  });
  $('#portfolioTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (!btn) return;
    showPortfolio(btn.dataset.market);
    runPortfolio();
  });
  $('#portfolioMethods').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-method]');
    if (!btn) return;
    portfolioMethod = btn.dataset.method;
    $$('#portfolioMethods button').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#portfolioCaps').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cap]');
    if (!btn) return;
    portfolioCap = Number(btn.dataset.cap);
    $$('#portfolioCaps button').forEach((b) => b.classList.toggle('active', b === btn));
  });
  $('#portfolioRun').addEventListener('click', runPortfolio);
  $('#portfolioClose').addEventListener('click', () => showResearch(portfolioMarket));

  /* --------------------------- 隔日预测 --------------------------- */
  const PREDICT_MARKET = { cn: 'A 股', us: '美股' };
  let predictMarket = 'cn';
  const predictCache = new Map();
  let liveTimer = null;

  function stopLive() {
    if (liveTimer) {
      clearInterval(liveTimer);
      liveTimer = null;
    }
  }

  async function tickLive(symbols) {
    if ($('#predictView').hidden || !symbols.length) return;
    try {
      const body = await api('/api/live', { symbols: symbols.join(',') }, 20000);
      const data = body.data || {};
      const rows = $$('#predictView .predict-row');
      symbols.forEach((sym) => {
        const q = data[sym];
        if (!q || q.price == null) return;
        const row = rows.find((r) => r.dataset.symbol === sym);
        if (!row) return;
        const cur = sym.startsWith('us') ? '$' : '¥';
        const priceEl = row.querySelector('.r-price');
        if (priceEl) priceEl.textContent = `${cur}${nf(q.price, 2)}`;
        const pctEl = row.querySelector('.r-pct');
        if (pctEl && q.change_pct != null) {
          pctEl.textContent = pctS(q.change_pct / 100, 2);
          pctEl.className = `r-pct ${cls(q.change_pct)}`;
        }
      });
    } catch (err) { /* 静默重试 */ }
  }

  function startLive(symbols) {
    stopLive();
    if (!symbols.length) return;
    tickLive(symbols);
    liveTimer = setInterval(() => tickLive(symbols), 15000);
  }

  async function showPredict(market = predictMarket) {
    predictMarket = market;
    stopLive();
    nav.lastList = { type: 'predict', market };
    showBack(false);
    $$('#predictTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#predictView').hidden = false;
    $('#reviewView').hidden = true;
    $('#predictMeta').textContent = `${PREDICT_MARKET[market]} · 基于当日量价形态的规则模型`;
    $('#predictUp').innerHTML = '<div class="rank-loading">计算中…</div>';
    $('#predictDown').innerHTML = '<div class="rank-loading">计算中…</div>';
    try {
      const body = await api('/api/predict', { market }, 90000);
      renderPredict(body.data);
    } catch (err) {
      $('#predictUp').innerHTML = `<div class="rank-loading">${esc(err.message)}</div>`;
      $('#predictDown').innerHTML = '';
      $('#predictMeta').textContent = `${PREDICT_MARKET[market]} · 计算失败`;
    }
  }

  function renderPredict(d) {
    if (d.status === 'wait_close') {
      if (d.last && d.last.items && d.last.items.length) {
        renderSnapshot(d.last);
      } else {
        $('#predictMeta').textContent = `${d.label} · ${d.message}`;
        $('#predictUp').innerHTML = '<div class="rank-loading">尚未生成预测，收盘后再来</div>';
        $('#predictDown').innerHTML = '';
      }
      return;
    }
    const sent = d.sentiment || {};
    $('#predictMeta').textContent =
      `${d.label} · 基于 ${d.session_date || ''} 收盘 · 市场情绪 ${nf(sent.score, 0)}`
      + `（指数 ${pctS((sent.index_pct || 0) / 100, 2)}） · 样本 ${d.universe} 只 · 更新于 ${d.updated}`;
    predictCache.clear();
    d.up.forEach((x) => { x.side = 'up'; predictCache.set(x.symbol, x); });
    d.down.forEach((x) => { x.side = 'down'; predictCache.set(x.symbol, x); });
    const row = (item, i) => {
      const cur = item.currency === 'USD' ? '$' : '';
      return `<button type="button" class="rank-row predict-row" data-symbol="${esc(item.symbol)}"
        title="查看预测依据 ${esc(item.name)}">
        <span class="r-idx">${i + 1}</span>
        <span class="r-name"><b>${esc(item.name)}</b><small>${esc(item.code)} · ${esc(item.reason)}</small></span>
        <span class="r-price">${cur}${nf(item.price, 2)}</span>
        <span class="r-pct ${cls(item.change_pct)}">${pctS(item.change_pct / 100, 2)}</span>
        <span class="r-pred ${cls(item.pred_pct)}" title="模型估算区间 ±${nf(item.pred_range, 1)}%">${pctS(item.pred_pct / 100, 1)}</span>
        <span class="p-score" title="预测分">${nf(item.score, 0)}</span>
      </button>`;
    };
    $('#predictUp').innerHTML = d.up.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
    $('#predictDown').innerHTML = d.down.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
  }

  function renderSnapshot(snap) {
    const items = snap.items || [];
    predictCache.clear();
    items.forEach((x) => predictCache.set(x.symbol, x));
    const up = items.filter((x) => x.side === 'up');
    const down = items.filter((x) => x.side === 'down');
    const cur = snap.market === 'us' ? '$' : '¥';
    const row = (item, i) => `<button type="button" class="rank-row predict-row"
      data-symbol="${esc(item.symbol)}" title="点击分析 ${esc(item.name)}">
      <span class="r-idx">${i + 1}</span>
      <span class="r-name"><b>${esc(item.name)}</b><small>${esc(item.code)} · ${item.side === 'up' ? '看涨' : '看跌'}</small></span>
      <span class="r-price">${cur}${nf(item.price, 2)}</span>
      <span class="r-pct">—</span>
      <span class="r-pred ${cls(item.pred_pct)}">${pctS(item.pred_pct / 100, 1)}</span>
      <span class="p-score">${item.score ? nf(item.score, 0) : ''}</span>
    </button>`;
    $('#predictMeta').textContent =
      `${snap.label} · 上次预测基于 ${snap.date} 收盘 · 等待本次收盘后更新 · 盘中涨幅每 15 秒刷新`;
    $('#predictUp').innerHTML = up.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
    $('#predictDown').innerHTML = down.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
    startLive(items.map((x) => x.symbol));
  }

  /* 预测依据弹窗 */
  let basisItem = null;
  const basisModal = $('#basisModal');
  function closeBasis() {
    basisModal.hidden = true;
    document.body.classList.remove('modal-open');
  }
  function showBasis(item) {
    if (!item.basis) {
      $('#predictView').hidden = true;
      analyze(item.symbol);
      return;
    }
    basisItem = item;
    const cur = item.currency === 'USD' ? '$' : '';
    $('#basisTitle').textContent = `${item.name} · 预测依据`;
    $('#basisMeta').textContent =
      `${item.code} · 现价 ${cur}${nf(item.price, 2)} · 今日 ${pctS(item.change_pct / 100, 2)}`
      + ` · 预计 ${pctS(item.pred_pct / 100, 1)}（±${nf(item.pred_range, 1)}%）`;
    const parts = item.basis.factors || [];
    $('#basisBars').innerHTML = parts.map(([label, value, weight]) => `
      <div class="basis-row">
        <div class="basis-label">${label}<small>权重 ${weight}%</small></div>
        <div class="basis-track"><div class="basis-fill" style="width:${clamp(value, 0, 100)}%"></div></div>
        <div class="basis-val">${nf(value, 0)}</div>
      </div>`).join('');
    const terms = parts.map(([label, value, weight]) => `${(weight / 100).toFixed(2)}×${nf(value, 0)}`);
    $('#basisFormula').innerHTML =
      `${terms.join(' + ')} = <b>${nf(item.score, 1)}</b>`;
    const news = item.basis.news || {};
    const titles = (news.titles || []).slice(0, 2).map((t) => `「${esc(t)}」`).join(' ');
    $('#basisNews').innerHTML =
      `<div>资金：${esc(item.basis.fund_note || '—')}</div>`
      + (titles
        ? `<div>消息：${titles}</div><div>${esc(news.note || '')}</div>`
        : '<div>消息：暂无个股新闻（中性）</div>');
    $('#basisReason').textContent =
      `形态：${item.reason} · 当日振幅 ${nf(item.basis.amp, 1)}%`;
    basisModal.hidden = false;
    document.body.classList.add('modal-open');
  }
  $('#basisClose').addEventListener('click', closeBasis);
  $('#basisCancel').addEventListener('click', closeBasis);
  basisModal.addEventListener('click', (e) => {
    if (e.target === basisModal) closeBasis();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !basisModal.hidden) closeBasis();
  });
  $('#basisAnalyze').addEventListener('click', () => {
    if (!basisItem) return;
    const symbol = basisItem.symbol;
    closeBasis();
    $('#predictView').hidden = true;
    analyze(symbol);
  });

  $('#predictBtn').addEventListener('click', () => showPredict());
  $('#predictTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showPredict(btn.dataset.market);
  });
  $('#predictRefresh').addEventListener('click', () => showPredict(predictMarket));
  $('#predictClose').addEventListener('click', () => goHome());
  $('#predictView').addEventListener('click', (e) => {
    const row = e.target.closest('.rank-row');
    if (!row) return;
    const item = predictCache.get(row.dataset.symbol);
    if (item) showBasis(item);
    else {
      $('#predictView').hidden = true;
      analyze(row.dataset.symbol);
    }
  });

  /* --------------------------- 预测复盘 --------------------------- */
  const REVIEW_MARKET = { cn: 'A 股', us: '美股' };
  let reviewMarket = 'cn';

  async function showReview(market = reviewMarket) {
    reviewMarket = market;
    stopLive();
    nav.lastList = { type: 'review', market };
    showBack(false);
    $$('#reviewTabs button').forEach((b) => b.classList.toggle('active', b.dataset.market === market));
    $('#welcome').hidden = true;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = false;
    $('#reviewMeta').textContent = `${REVIEW_MARKET[market]} · 正在读取预测存档…`;
    $('#reviewStats').innerHTML = '';
    $('#reviewTable').innerHTML = '<div class="rank-loading">加载中…</div>';
    try {
      const body = await api('/api/review', { market }, 60000);
      renderReview(body.data);
    } catch (err) {
      $('#reviewTable').innerHTML = `<div class="rank-loading">${esc(err.message)}</div>`;
      $('#reviewMeta').textContent = `${REVIEW_MARKET[market]} · 读取失败`;
    }
  }

  function renderReview(d) {
    if (d.status !== 'ok') {
      $('#reviewMeta').textContent = `${REVIEW_MARKET[d.market]} · ${d.message}`;
      $('#reviewStats').innerHTML = '';
      $('#reviewTable').innerHTML = `<div class="rank-loading">${esc(d.message)}</div>`;
      return;
    }
    $('#reviewMeta').textContent =
      `${d.label} · 预测日 ${d.pred_date} → 对比日 ${d.review_date} · 共 ${d.total} 只`
      + ` · 次日估测：偏多 ${d.next_bias || 0} / 震荡 ${d.next_flat || 0} / 偏空 ${d.next_bear || 0}`
      + `（均值 ${pctS((d.next_avg || 0) / 100, 1)}）`;
    const stats = [
      ['方向命中率', `${nf(d.hit_rate, 1)}%`],
      ['平均实际涨跌', pctS(d.avg_actual / 100, 2)],
      ['平均绝对误差', `${nf(d.avg_abs_error, 2)}%`],
      ['看涨/看跌实际均值', `${pctS((d.up_avg || 0) / 100, 1)} / ${pctS((d.down_avg || 0) / 100, 1)}`],
    ];
    $('#reviewStats').innerHTML = stats.map(([k, v]) => `
      <div class="review-stat"><div class="s-label">${k}</div><div class="s-value">${v}</div></div>`).join('');

    const row = (r, i) => `<button type="button" class="rank-row review-row"
      data-symbol="${esc(r.symbol)}" title="${esc(r.reason)}">
      <span class="r-idx">${i + 1}</span>
      <span class="r-name"><b>${esc(r.name)}</b><small>${esc(r.code)} · ${r.side === 'up' ? '看涨' : '看跌'} · ${esc(r.reason)}</small></span>
      <span class="r-pred ${cls(r.pred_pct)}">${pctS(r.pred_pct / 100, 1)}</span>
      <span class="r-pct ${cls(r.actual_pct)}">${pctS(r.actual_pct / 100, 2)}</span>
      <span class="r-amt">${pctS(r.error / 100, 2)}</span>
      <span class="r-next ${cls(r.next ? r.next.pct : 0)}" title="${esc(r.next ? r.next.text : '')}">${r.next ? pctS(r.next.pct / 100, 1) : '—'}</span>
      <span class="hit-badge ${r.hit ? 'ok' : 'bad'}">${r.hit ? '命中' : '未中'}</span>
    </button>`;
    $('#reviewTable').innerHTML = d.records.map(row).join('') || '<div class="rank-loading">暂无数据</div>';
  }

  $('#reviewBtn').addEventListener('click', () => showReview());
  $('#reviewTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-market]');
    if (btn) showReview(btn.dataset.market);
  });
  $('#reviewRefresh').addEventListener('click', () => showReview(reviewMarket));
  $('#reviewBack').addEventListener('click', () => showPredict(reviewMarket));
  $('#reviewTable').addEventListener('click', (e) => {
    const row = e.target.closest('.rank-row');
    if (!row) return;
    $('#reviewView').hidden = true;
    analyze(row.dataset.symbol);
  });

  /* --------------------------- 请求与流程 --------------------------- */
  async function api(path, params, timeoutMs = 60000) {
    const qs = new URLSearchParams(params || {});
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(`${path}?${qs.toString()}`, {
        headers: { Accept: 'application/json' }, signal: ctrl.signal, cache: 'no-store',
      });
      let body;
      try { body = await res.json(); } catch (e) { throw new Error(`服务返回异常（HTTP ${res.status}）`); }
      if (!res.ok || !body.ok) throw new Error(body.error || `请求失败（HTTP ${res.status}）`);
      return body;
    } catch (err) {
      if (err && err.name === 'AbortError') {
        throw new Error('请求超时：行情接口响应过慢，请稍后重试');
      }
      if (err instanceof TypeError) {
        throw new Error('无法连接本地服务，请确认程序正在运行（重新双击桌面快捷方式）');
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  /* --------------------------- 连接检测 --------------------------- */
  async function checkConnection() {
    const banner = $('#connBanner');
    if (!banner) return;
    if (location.protocol === 'file:') {
      banner.hidden = false;
      banner.querySelector('span').textContent =
        '当前页面是以文件方式打开的，无法获取行情。请关闭本页，改用桌面「量析 StockQuant」快捷方式启动。';
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 6000);
    try {
      const res = await fetch('/api/health', { cache: 'no-store', signal: ctrl.signal });
      if (!res.ok) throw new Error('bad');
      banner.hidden = true;
    } catch (e) {
      banner.hidden = false;
    } finally {
      clearTimeout(timer);
    }
  }

  const loadingTexts = [
    '解析证券代码 · 下载前复权日线 · 运行量化模型',
    '对齐基准指数 · 计算风险收益特征',
    '五维评分 · 均线回测 · 蒙特卡洛模拟',
  ];
  let loadingTimer = null;

  function setLoading(on) {
    stopLive();
    $('#loading').hidden = !on;
    $('#analyzeBtn').disabled = on;
    if (on) {
      $('#welcome').hidden = true;
      $('#content').hidden = true;
      $('#errorBox').hidden = true;
      $('#rankView').hidden = true;
      $('#scoreView').hidden = true;
      $('#simView').hidden = true;
      $('#researchView').hidden = true;
      $('#portfolioView').hidden = true;
      $('#fundamentalView').hidden = true;
      $('#paperView').hidden = true;
      $('#qualityView').hidden = true;
      $('#strategyView').hidden = true;
      $('#predictView').hidden = true;
      $('#reviewView').hidden = true;
        let i = 0;
      $('#loadingText').textContent = loadingTexts[0];
      clearInterval(loadingTimer);
      loadingTimer = setInterval(() => {
        i = (i + 1) % loadingTexts.length;
        $('#loadingText').textContent = loadingTexts[i];
      }, 1100);
    } else {
      clearInterval(loadingTimer);
    }
  }

  function showError(msg) {
    $('#errorBox').hidden = false;
    $('#errorText').textContent = msg;
    $('#content').hidden = true;
    $('#welcome').hidden = true;
    $('#loading').hidden = true;
    showBack(true);
  }

  async function analyze(query, opts = {}) {
    const q = String(query || '').trim();
    if (!q) { $('#query').focus(); return; }
    state.query = q;
    setLoading(true);
    try {
      const years = opts.years || state.years;
      state.years = years;
      const body = await api(opts.force ? '/api/refresh' : '/api/analyze', {
        q, years, fast: 20, slow: 60,
      });
      const d = body.data;
      state.data = d;
      // 先显示容器，图表才能量到真实宽度
      $('#content').hidden = false;
      $('#welcome').hidden = true;
      $('#errorBox').hidden = true;
      renderAll(d);
      showBack(true);
      if (!opts.keepQuery) $('#query').value = d.meta.name;
      try {
        const url = new URL(window.location.href);
        url.hash = `q=${encodeURIComponent(d.meta.symbol)}`;
        history.replaceState(null, '', url);
      } catch (e) { /* 忽略 */ }
    } catch (err) {
      showError(err.message || '未知错误');
    } finally {
      setLoading(false);
    }
  }

  /* --------------------------- 搜索联想 --------------------------- */
  const suggestEl = $('#suggest');
  let suggestItems = [];
  let activeIdx = -1;
  let suggestTimer = null;

  function hideSuggest() {
    suggestEl.hidden = true;
    suggestEl.innerHTML = '';
    suggestItems = [];
    activeIdx = -1;
  }

  function renderSuggest(items) {
    suggestItems = items;
    activeIdx = items.length ? 0 : -1;
    if (!items.length) { hideSuggest(); return; }
    suggestEl.innerHTML = items.map((it, i) => `
      <div class="suggest-item ${i === 0 ? 'active' : ''}" data-i="${i}" role="option">
        <span class="s-name">${esc(it.name)}</span>
        <span class="s-code">${esc(it.code)}.${esc((it.market || '').toUpperCase())}</span>
        <span class="s-kind">${esc(it.kind)}</span>
      </div>`).join('');
    suggestEl.hidden = false;
  }

  function highlightSuggest() {
    $$('.suggest-item', suggestEl).forEach((el, i) => el.classList.toggle('active', i === activeIdx));
  }

  async function lookup(text) {
    try {
      const body = await api('/api/search', { q: text });
      if ($('#query').value.trim() !== text.trim()) return;
      renderSuggest(body.matches || []);
    } catch (e) { /* 静默失败，保留手动提交 */ }
  }

  $('#query').addEventListener('input', (e) => {
    const text = e.target.value.trim();
    clearTimeout(suggestTimer);
    if (text.length < 1) { hideSuggest(); return; }
    suggestTimer = setTimeout(() => lookup(text), 220);
  });

  $('#query').addEventListener('keydown', (e) => {
    if (suggestEl.hidden) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeIdx = (activeIdx + 1) % suggestItems.length;
      highlightSuggest();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeIdx = (activeIdx - 1 + suggestItems.length) % suggestItems.length;
      highlightSuggest();
    } else if (e.key === 'Enter' && activeIdx >= 0) {
      e.preventDefault();
      const it = suggestItems[activeIdx];
      hideSuggest();
      $('#query').value = it.name;
      analyze(it.symbol);
    } else if (e.key === 'Escape') {
      hideSuggest();
    }
  });

  suggestEl.addEventListener('mousedown', (e) => {
    const item = e.target.closest('.suggest-item');
    if (!item) return;
    const it = suggestItems[Number(item.dataset.i)];
    if (!it) return;
    hideSuggest();
    $('#query').value = it.name;
    analyze(it.symbol);
  });

  document.addEventListener('click', (e) => {
    if (!suggestEl.contains(e.target) && e.target !== $('#query')) hideSuggest();
  });

  $('#searchForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = $('#query').value.trim();
    if (!text) return;
    hideSuggest();
    analyze(text);
  });

  $('#chips').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-q]');
    if (!btn) return;
    const q = btn.dataset.q;
    $('#query').value = q;
    analyze(q);
  });

  $('#yearSelect').addEventListener('change', (e) => {
    state.years = Number(e.target.value) || 3;
    if (state.query && state.data) analyze(state.query, { years: state.years });
  });

  $('#chartTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-tab]');
    if (!btn || !state.data) return;
    setTab(btn.dataset.tab);
    drawMainChart(state.data);
  });

  function setTab(tab) {
    if (!['price', 'drawdown', 'relative'].includes(tab)) return;
    state.tab = tab;
    $$('#chartTabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  }

  $('#refreshBtn').addEventListener('click', () => {
    if (state.query) analyze(state.query, { force: true });
  });

  $('#printBtn').addEventListener('click', () => window.print());

  /* --------------------------- CSV 导出 --------------------------- */
  function downloadBlob(name, text, type) {
    const blob = new Blob([text], { type });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  $('#csvBtn').addEventListener('click', () => {
    const d = state.data;
    if (!d) return;
    const rows = [];
    const push = (a, b, c) => rows.push([a, b, c]);
    push('基本信息', '证券', `${d.meta.name} ${d.meta.code}.${d.meta.market.toUpperCase()}`);
    push('基本信息', '区间', `${d.meta.start} ~ ${d.meta.end}`);
    push('基本信息', '基准', d.meta.benchmark.name);
    push('评分', '综合评分', d.score.total);
    push('评分', '评级 / 信号', `${d.score.grade} / ${d.score.signal}`);
    d.score.dims.forEach((dm) => push('评分', `${dm.name}（权重 ${dm.weight * 100}%）`, dm.score));
    Object.entries(d.performance).forEach(([k, v]) => {
      if (typeof v === 'number') push('绩效', k, v);
    });
    Object.entries(d.relative).forEach(([k, v]) => push('相对基准', k, v));
    ['rsi14', 'atr_pct', 'vol_ratio', 'pos52', 'vol20_ann'].forEach((k) => {
      if (d.technical[k] != null) push('技术面', k, d.technical[k]);
    });
    push('回测', '策略', d.backtest.name);
    ['annual_return', 'max_dd', 'sharpe', 'trades', 'win_rate', 'exposure'].forEach((k) => {
      if (d.backtest.metrics[k] != null) push('回测', k, d.backtest.metrics[k]);
    });
    push('模拟', '60日上涨概率', d.monte.prob_up);
    push('模拟', '60日中位数目标', d.monte.term_p50);
    const csv = '\ufeff' + ['分类,指标,数值'].concat(rows.map((r) => r.map((x) => {
      const s = String(x == null ? '' : x);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(','))).join('\n');
    downloadBlob(`${d.meta.name}_${d.meta.code}_量化指标.csv`, csv, 'text/csv;charset=utf-8');
  });

  /* --------------------------- 实时报价刷新 --------------------------- */
  async function refreshQuote() {
    const d = state.data;
    if (!d || !$('#autoRefresh').checked) return;
    try {
      const body = await api('/api/quote', { q: d.meta.symbol });
      const q = body.quote || {};
      if (!isNum(q.price)) return;
      const chg = q.change_pct;
      const isUs = d.meta.currency === 'USD';
      const cur = isUs ? '$' : '';
      const mcapUnit = isUs ? ' 亿美元' : ' 亿';
      const hPrice = $('#hPrice');
      const hChg = $('#hChg');
      if (hPrice) hPrice.textContent = cur + nf(q.price, 2);
      if (hChg) {
        hChg.textContent = `${isNum(q.change) ? `${q.change >= 0 ? '+' : ''}${nf(q.change, 2)} ` : ''}${isNum(chg) ? `${chg >= 0 ? '+' : ''}${nf(chg, 2)}%` : ''}`;
        hChg.className = `chg ${isNum(chg) && chg > 0 ? 'up-text' : isNum(chg) && chg < 0 ? 'down-text' : 'flat-text'}`;
      }
      const map = {
        'q-open': cur + nf(q.open, 2),
        'q-high': cur + nf(q.high, 2),
        'q-low': cur + nf(q.low, 2),
        'q-prev': cur + nf(q.prev_close, 2),
        'q-amount': isNum(q.amount) ? `${cur}${money(q.amount)}` : '—',
        'q-turnover': isNum(q.turnover) ? `${nf(q.turnover, 2)}%` : '—',
        'q-pe': nf(q.pe, 2),
        'q-pb': nf(q.pb, 2),
        'q-mcap': isNum(q.total_mcap) ? `${isUs ? '' : cur}${nf(q.total_mcap, 0)}${mcapUnit}` : '—',
        'q-fmcap': isNum(q.float_mcap) ? `${isUs ? '' : cur}${nf(q.float_mcap, 0)}${mcapUnit}` : '—',
        'q-vr': nf(isNum(q.volume_ratio) ? q.volume_ratio : d.technical.vol_ratio, 2),
        'q-amp': isNum(q.amplitude) ? `${nf(q.amplitude, 2)}%` : '—',
      };
      Object.entries(map).forEach(([id, val]) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      });
    } catch (e) { /* 静默 */ }
  }
  setInterval(refreshQuote, 20000);

  /* --------------------------- 市场概览 --------------------------- */
  async function loadOverview() {
    try {
      const body = await api('/api/overview', {}, 20000);
      renderOverview(body.data);
    } catch (err) {
      const grid = $('#ovGrid');
      if (grid && !grid.dataset.loaded) grid.innerHTML = '<div class="ov-loading">市场概览暂时不可用</div>';
    }
  }

  function renderOverview(d) {
    const grid = $('#ovGrid');
    if (!grid) return;
    grid.dataset.loaded = '1';
    grid.innerHTML = d.indices.map((x) => {
      const cur = x.currency === 'USD' ? '$' : '';
      return `<button type="button" class="ov-item" data-symbol="${esc(x.symbol || x.analyze || '')}"
        title="点击分析 ${esc(x.name)}">
        <div class="ov-top"><span class="ov-name">${esc(x.name)}</span>
          <span class="ov-tag">${esc(x.sub || '')}</span></div>
        <div class="ov-value">${cur}${nf(x.value, 2)}</div>
        <div class="ov-chg ${cls(x.change_pct)}">${pctS(x.change_pct / 100, 2)}</div>
      </button>`;
    }).join('');

    const breadth = (el, b, label) => {
      if (!el) return;
      if (!b) {
        el.innerHTML = `<div class="b-head"><span>${label}</span></div>
          <div class="b-legend">统计中，稍后自动刷新…</div>`;
        return;
      }
      const total = b.total || 1;
      el.innerHTML = `<div class="b-head">
          <span>${label}${b.sample ? ' · 样本' : ''}</span><b>${nf(total, 0)} 只</b></div>
        <div class="b-bar">
          <i class="up" style="width:${(b.up / total * 100).toFixed(1)}%"></i>
          <i class="flat" style="width:${(b.flat / total * 100).toFixed(1)}%"></i>
          <i class="down" style="width:${(b.down / total * 100).toFixed(1)}%"></i>
        </div>
        <div class="b-legend">
          <span class="up-text">涨 ${nf(b.up, 0)}</span>
          <span>平 ${nf(b.flat, 0)}</span>
          <span class="down-text">跌 ${nf(b.down, 0)}</span>
        </div>`;
    };
    breadth($('#breadthCn'), d.breadth.cn, 'A 股涨平跌');
    breadth($('#breadthUs'), d.breadth.us, '美股涨平跌');
  }

  $('#ovGrid').addEventListener('click', (e) => {
    const item = e.target.closest('.ov-item');
    if (!item) return;
    const symbol = item.dataset.symbol;
    if (symbol) analyze(symbol);
  });

  loadOverview();
  setInterval(loadOverview, 60000);

  /* --------------------------- 手机访问 --------------------------- */
  const mobileModal = $('#mobileModal');
  const closeMobile = () => {
    mobileModal.hidden = true;
    document.body.classList.remove('modal-open');
  };
  $('#mobileBtn').addEventListener('click', async () => {
    mobileModal.hidden = false;
    document.body.classList.add('modal-open');
    try {
      const body = await api('/api/network', {}, 8000);
      $('#lanUrl').textContent = body.data.url;
      $('#qrImg').src = `/api/qr.png?t=${Date.now()}`;
    } catch (err) {
      $('#lanUrl').textContent = '获取地址失败，请确认服务已启动';
    }
  });
  $('#mobileClose').addEventListener('click', closeMobile);
  mobileModal.addEventListener('click', (e) => {
    if (e.target === mobileModal) closeMobile();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMobile();
  });
  $('#copyUrl').addEventListener('click', async () => {
    const text = $('#lanUrl').textContent || '';
    try {
      await navigator.clipboard.writeText(text);
      $('#copyUrl').textContent = '已复制';
      setTimeout(() => { $('#copyUrl').textContent = '复制'; }, 1500);
    } catch (err) { /* 忽略 */ }
  });

  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
  }

  /* --------------------------- 初始化 --------------------------- */
  function initFromHash() {
    const hash = window.location.hash.replace(/^#/, '');
    if (!hash) return;
    const params = new URLSearchParams(hash);
    const st = params.get('strategy');
    if (st) {
      showStrategy(st);
      return;
    }
    const qq = params.get('quality');
    if (qq) {
      showQuality(qq);
      return;
    }
    const pp = params.get('paper');
    if (pp) {
      showPaper(pp);
      return;
    }
    const fd = params.get('fundamental');
    if (fd) {
      showFundamental(fd);
      return;
    }
    const pf = params.get('portfolio');
    if (pf) {
      showPortfolio(pf);
      if (params.get('run') === '1') setTimeout(runPortfolio, 400);
      return;
    }
    const rs = params.get('research');
    if (rs) {
      showResearch(rs);
      if (params.get('run') === '1') setTimeout(runResearch, 400);
      return;
    }
    const sm = params.get('sim');
    if (sm) {
      showSim(sm);
      return;
    }
    const sc = params.get('score');
    if (sc) {
      showScoreboard(sc);
      return;
    }
    const rv = params.get('review');
    if (rv) {
      showReview(rv);
      return;
    }
    const pr = params.get('predict');
    if (pr) {
      showPredict(pr);
      return;
    }
    const rk = params.get('rank');
    if (rk) {
      showRank(rk);
      return;
    }
    const q = params.get('q');
    setTab(params.get('tab') || 'price');
    if (q) {
      $('#query').value = q;
      analyze(q);
    }
  }

  function goHome() {
    stopLive();
    if (paperStream) { paperStream.close(); paperStream = null; }
    showBack(false);
    nav.lastList = null;
    $('#content').hidden = true;
    $('#errorBox').hidden = true;
    $('#rankView').hidden = true;
    $('#scoreView').hidden = true;
    $('#simView').hidden = true;
    $('#researchView').hidden = true;
    $('#portfolioView').hidden = true;
    $('#fundamentalView').hidden = true;
    $('#paperView').hidden = true;
    $('#qualityView').hidden = true;
    $('#strategyView').hidden = true;
    $('#predictView').hidden = true;
    $('#reviewView').hidden = true;
    $('#welcome').hidden = false;
    state.data = null;
    $('#query').value = '';
    try { history.replaceState(null, '', window.location.pathname); } catch (err) { /* 忽略 */ }
  }

  $('#backBtn').addEventListener('click', () => {
    const last = nav.lastList;
    if (last && last.type === 'rank') return showRank(last.market);
    if (last && last.type === 'score') return showScoreboard(last.market);
    if (last && last.type === 'predict') return showPredict(last.market);
    if (last && last.type === 'review') return showReview(last.market);
    return goHome();
  });

  $('#brandLink').addEventListener('click', (e) => {
    e.preventDefault();
    goHome();
    $('#query').focus();
  });

  // 首屏给一个默认演示（不自动请求，避免打开即等待）
  initFromHash();
})();
