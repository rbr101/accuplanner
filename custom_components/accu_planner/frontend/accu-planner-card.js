/*
 * Accu Planner – charge schedule card
 * type: custom:accu-planner-card
 * entity: sensor.<name>_mode          (optional: the Accu Planner Mode sensor)
 * Optional: title, hours_back (4), hours_ahead (28) or hours_to_show + time_offset, height (230), show_soc (true)
 * Also: custom:accu-planner-plan-card (the charge plan as an overview)
 */
(() => {
  const VERSION = "1.13.0";
  const Q = 900000;
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const STOPS = [
    [0.0, [0, 169, 100]],
    [0.25, [139, 195, 74]],
    [0.5, [255, 202, 40]],
    [0.75, [255, 152, 0]],
    [1.0, [229, 57, 53]],
  ];
  const C_CHARGE = "#00a964";
  const C_DISCHARGE = "#e53935";
  const C_SOC = "#2196f3";
  const C_SELL = "#8e24aa";

  const pad = (n) => String(n).padStart(2, "0");
  const hm = (t) => { const d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); };
  const eur = (v) => (typeof v === "number" && isFinite(v) ? "€" + v.toFixed(3) : "?");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function colorFor(p, lo, hi, alpha) {
    const pos = hi > lo ? Math.min(1, Math.max(0, (p - lo) / (hi - lo))) : 0;
    for (let i = 0; i < STOPS.length - 1; i++) {
      const [a, ca] = STOPS[i];
      const [b, cb] = STOPS[i + 1];
      if (pos <= b) {
        const f = (pos - a) / (b - a);
        const c = ca.map((v, k) => Math.round(v + f * (cb[k] - v)));
        return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
      }
    }
    return `rgba(229,57,53,${alpha})`;
  }

  function niceStep(raw) {
    for (const s of [0.005, 0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25, 0.5, 1]) if (s >= raw) return s;
    return 1;
  }

  // The Accu Planner Mode sensor: the configured entity, otherwise found automatically.
  const isPlanner = (st) => st && st.attributes && st.attributes.series && "next_action" in st.attributes;
  function resolveEntity(hass, wanted) {
    if (!hass) return null;
    if (wanted && hass.states[wanted]) return hass.states[wanted];
    const id = Object.keys(hass.states).find((e) => e.startsWith("sensor.") && isPlanner(hass.states[e]));
    return id ? hass.states[id] : null;
  }
  function notFound(wanted) {
    return wanted
      ? "Entity not found: " + esc(wanted) + " — and no Accu Planner sensor found. Is the integration loaded?"
      : "No Accu Planner sensor found. Is the integration loaded?";
  }

  // ---------------------------------------------------------------- model
  // Time window: hours_back / hours_ahead, or like plotly-graph hours_to_show + time_offset
  function windowOf(cfg) {
    let back = parseFloat(cfg.hours_back), ahead = parseFloat(cfg.hours_ahead);
    if (cfg.hours_to_show !== undefined && cfg.hours_to_show !== null) {
      const show = parseFloat(cfg.hours_to_show);
      const off = cfg.time_offset !== undefined ? parseFloat(String(cfg.time_offset)) : 0;
      ahead = off;
      back = show - off;
    }
    return {
      back: Math.min(24, Math.max(0, isFinite(back) ? back : 4)),
      ahead: Math.min(72, Math.max(1, isFinite(ahead) ? ahead : 28)),
    };
  }

  function buildModel(attrs, cfg, nowMs) {
    const s = attrs && attrs.series;
    if (!s || !s.start || !Array.isArray(s.price) || !s.price.length) return null;
    const start = new Date(s.start).getTime();
    const nowIdx = s.now || 0;
    const win = windowOf(cfg);
    const t0 = Math.floor((nowMs - win.back * 3600000) / Q) * Q;
    const t1 = nowMs + win.ahead * 3600000;
    const plan = s.plan || "";
    const slots = [];
    for (let i = 0; i < s.price.length; i++) {
      const t = start + i * Q;
      if (t + Q <= t0 || t >= t1) continue;
      slots.push({
        t,
        p: s.price[i],
        past: i < nowIdx,
        act: plan[i] || "-",
        thr: s.threshold && typeof s.threshold[i] === "number" ? s.threshold[i] : null,
        soc: s.soc && typeof s.soc[i] === "number" ? s.soc[i] : null,
        socNext: s.soc && typeof s.soc[i + 1] === "number" ? s.soc[i + 1] : null,
      });
    }
    if (!slots.length) return null;
    let minS = slots[0], maxS = slots[0];
    for (const sl of slots) {
      if (sl.p < minS.p) minS = sl;
      if (sl.p > maxS.p) maxS = sl;
    }
    const dataEnd = slots[slots.length - 1].t + Q;
    return { slots, minS, maxS, attrs, t0: Math.min(t0, slots[0].t), t1: Math.max(t1, Math.min(dataEnd, t1)), dataEnd };
  }

  // ---------------------------------------------------------------- svg
  function renderSvg(model, cfg, W, H, nowMs) {
    const { slots, minS, maxS } = model;
    const m = { l: 34, r: cfg.show_soc ? 30 : 10, t: 10, b: 30 };
    const pw = Math.max(50, W - m.l - m.r);
    const ph = Math.max(50, H - m.t - m.b);
    const { t0, t1, dataEnd } = model;
    const X = (t) => m.l + ((t - t0) / (t1 - t0)) * pw;
    const qw = (pw * Q) / (t1 - t0);

    let rlo = minS.p, rhi = maxS.p;
    for (const s of slots) if (s.thr !== null) { rlo = Math.min(rlo, s.thr); rhi = Math.max(rhi, s.thr); }
    const span = Math.max(0.02, rhi - rlo);
    const yMin = rlo - span * 0.15;
    const yMax = rhi + span * 0.1;
    const plotBottom = m.t + ph;
    const Y = (p) => m.t + (1 - (p - yMin) / (yMax - yMin)) * ph;
    const S = (soc) => m.t + (1 - soc / 100) * ph;
    const strip = { y: plotBottom + 3, h: 5 };

    const o = [];
    o.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="var(--paper-font-body1_-_font-family, Roboto, sans-serif)" font-size="10">`);

    // horizontal grid lines + price labels
    const step = niceStep((yMax - yMin) / 4);
    for (let v = Math.ceil(yMin / step) * step; v <= yMax; v += step) {
      const y = Y(v).toFixed(1);
      o.push(`<line x1="${m.l}" x2="${m.l + pw}" y1="${y}" y2="${y}" stroke="var(--divider-color, #ccc)" stroke-width="0.5" opacity="0.6"/>`);
      o.push(`<text x="${m.l - 4}" y="${y}" dy="3" text-anchor="end" fill="var(--secondary-text-color, #888)">${v.toFixed(2)}</text>`);
    }

    // part of the window without known prices (e.g. tomorrow before ~13:00)
    if (dataEnd < t1 - Q / 2) {
      const x0 = X(dataEnd), x1 = m.l + pw;
      o.push(`<rect x="${x0.toFixed(1)}" y="${m.t}" width="${(x1 - x0).toFixed(1)}" height="${ph}" fill="var(--secondary-text-color, #888)" opacity="0.07"/>`);
      if (x1 - x0 > 90) {
        o.push(`<text x="${((x0 + x1) / 2).toFixed(1)}" y="${(m.t + ph / 2).toFixed(1)}" text-anchor="middle" fill="var(--secondary-text-color, #888)">prices not yet known</text>`);
      }
    }

    // SOC area (line is drawn over the bars later)
    let socLine = "";
    if (cfg.show_soc) {
      // measured (past, thinner) and expected (future); gaps in measurements are not bridged
      const segs = [];
      let cur = null, curPast = null;
      slots.forEach((s) => {
        if (s.soc === null) { cur = null; return; }
        if (!cur || curPast !== s.past) { cur = []; curPast = s.past; segs.push({ past: s.past, pts: cur }); }
        cur.push([X(s.t), S(s.soc)]);
      });
      const fut = segs.filter((g) => !g.past);
      const last = [...slots].reverse().find((s) => s.socNext !== null && !s.past);
      if (last && fut.length) fut[fut.length - 1].pts.push([X(last.t + Q), S(last.socNext)]);
      // connect past and future
      for (let k = 1; k < segs.length; k++) {
        if (segs[k - 1].past && !segs[k].past) segs[k - 1].pts.push(segs[k].pts[0]);
      }
      for (const g of segs) {
        if (g.pts.length < 2) continue;
        const line = g.pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join("");
        const lastX = g.pts[g.pts.length - 1][0].toFixed(1), firstX = g.pts[0][0].toFixed(1);
        o.push(`<path d="${line}L${lastX},${plotBottom}L${firstX},${plotBottom}Z" fill="${C_SOC}" opacity="${g.past ? 0.04 : 0.08}"/>`);
        socLine += `<path d="${line}" fill="none" stroke="${C_SOC}" stroke-width="${g.past ? 1.3 : 1.8}" opacity="${g.past ? 0.55 : 0.9}"/>`;
      }
      for (const v of [0, 50, 100]) {
        o.push(`<text x="${m.l + pw + 4}" y="${S(v).toFixed(1)}" dy="3" fill="${C_SOC}" opacity="0.8">${v}%</text>`);
      }
    }

    // price bars
    const gap = Math.min(1, qw * 0.12);
    slots.forEach((s) => {
      const y = Math.min(plotBottom, Y(s.p));
      o.push(`<rect x="${(X(s.t) + gap).toFixed(2)}" y="${y.toFixed(1)}" width="${Math.max(0.5, qw - 2 * gap).toFixed(2)}" height="${(plotBottom - y).toFixed(1)}" fill="${colorFor(s.p, minS.p, maxS.p, s.past ? 0.35 : 0.95)}"/>`);
    });

    // discharge price (steps)
    let d = "", prevY = null;
    slots.forEach((s) => {
      if (s.thr === null) { prevY = null; return; }
      const x = X(s.t).toFixed(1), y = Y(s.thr).toFixed(1);
      d += prevY === null ? `M${x},${y}` : `L${x},${prevY}L${x},${y}`;
      prevY = y;
    });
    if (d) {
      const lastThr = [...slots].reverse().find((s) => s.thr !== null);
      d += `L${X(lastThr.t + Q).toFixed(1)},${prevY}`;
      o.push(`<path d="${d}" fill="none" stroke="var(--primary-text-color, #444)" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.6"/>`);
    }

    o.push(socLine);

    // markers on the bars + action strip below
    const r = Math.max(2, Math.min(4, qw * 0.6));
    slots.forEach((s) => {
      if (s.act !== "C" && s.act !== "D" && s.act !== "S") return;
      const cx = X(s.t) + qw / 2, cy = Math.min(plotBottom, Y(s.p));
      const col = { C: C_CHARGE, D: C_DISCHARGE, S: C_SELL }[s.act];
      const op = s.past ? 0.45 : 1;
      o.push(`<g opacity="${op}">`);
      o.push(`<rect x="${X(s.t).toFixed(2)}" y="${strip.y}" width="${(qw + 0.3).toFixed(2)}" height="${strip.h}" fill="${col}"/>`);
      if (s.act === "C") {
        o.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="${colorFor(s.p, minS.p, maxS.p, 1)}" stroke="#000" stroke-width="0.7"/>`);
      } else if (s.act === "S") {
        const rs = r * 1.3;
        o.push(`<circle cx="${cx.toFixed(1)}" cy="${(cy - rs).toFixed(1)}" r="${rs.toFixed(1)}" fill="${C_SELL}" stroke="#000" stroke-width="0.7"/>`);
        if (rs >= 3.5) o.push(`<text x="${cx.toFixed(1)}" y="${(cy - rs).toFixed(1)}" dy="3" text-anchor="middle" font-size="${(rs * 1.5).toFixed(0)}" font-weight="bold" fill="#fff">$</text>`);
      } else {
        const h = r * 1.8;
        o.push(`<path d="M${(cx - r).toFixed(1)},${(cy - h).toFixed(1)}L${(cx + r).toFixed(1)},${(cy - h).toFixed(1)}L${cx.toFixed(1)},${cy.toFixed(1)}Z" fill="${colorFor(s.p, minS.p, maxS.p, 1)}" stroke="#000" stroke-width="0.7"/>`);
      }
      o.push(`</g>`);
    });

    // time axis: hours, midnight with day
    const hoursSpan = (t1 - t0) / 3600000;
    const every = pw / hoursSpan >= 28 ? 1 : pw / hoursSpan >= 14 ? 3 : 6;
    for (let t = Math.ceil(t0 / 3600000) * 3600000; t < t1; t += 3600000) {
      const dt = new Date(t);
      const x = X(t).toFixed(1);
      if (dt.getHours() === 0) {
        o.push(`<line x1="${x}" x2="${x}" y1="${m.t}" y2="${plotBottom}" stroke="var(--divider-color, #ccc)" stroke-width="1"/>`);
        o.push(`<text x="${x}" y="${H - 4}" text-anchor="middle" font-weight="bold" fill="var(--primary-text-color, #333)">${DAYS[dt.getDay()]}</text>`);
      } else if (dt.getHours() % every === 0) {
        o.push(`<text x="${x}" y="${H - 4}" text-anchor="middle" fill="var(--secondary-text-color, #888)">${pad(dt.getHours())}</text>`);
      }
    }

    // now line
    if (nowMs > t0 && nowMs < t1) {
      const x = X(nowMs).toFixed(1);
      o.push(`<line x1="${x}" x2="${x}" y1="${m.t - 4}" y2="${plotBottom + 8}" stroke="var(--primary-color, #03a9f4)" stroke-width="1.5"/>`);
    }

    o.push(`</svg>`);
    return { svg: o.join(""), geom: { m, pw, t0, t1 } };
  }

  // ---------------------------------------------------------------- card
  class AccuPlannerCard extends HTMLElement {
    setConfig(config) {
      this._config = { title: "Charge schedule", hours_back: 4, hours_ahead: 28, height: 230, show_soc: true, ...config };
      this._lastState = undefined;
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      const st = resolveEntity(hass, this._config.entity);
      if (st !== this._lastState) {
        this._lastState = st;
        this._render();
      }
    }

    connectedCallback() {
      this._ro = new ResizeObserver(() => this._render());
      this._ro.observe(this);
      this._timer = setInterval(() => this._render(), 60000);
    }

    disconnectedCallback() {
      if (this._ro) this._ro.disconnect();
      clearInterval(this._timer);
    }

    getCardSize() { return 5; }

    getGridOptions() { return { columns: "full", min_rows: 4 }; }

    static getStubConfig(hass) {
      const id = Object.keys(hass.states).find(
        (e) => e.startsWith("sensor.") && hass.states[e].attributes.series && "next_action" in hass.states[e].attributes);
      return { entity: id || "" };
    }

    _ensureDom() {
      if (this._root) return;
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `
        <style>
          ha-card { padding: 12px 12px 8px; position: relative; }
          .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
          .title { font-size: 16px; font-weight: 500; }
          .now { font-size: 13px; color: var(--secondary-text-color); }
          .chips { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; margin: 6px 0 4px; color: var(--primary-text-color); }
          .chart { position: relative; width: 100%; }
          .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
          .legend i { display: inline-block; vertical-align: middle; margin-right: 4px; }
          .tip { position: absolute; pointer-events: none; background: var(--card-background-color, #fff);
                 border: 1px solid var(--divider-color, #ccc); border-radius: 6px; padding: 4px 8px; font-size: 12px;
                 white-space: nowrap; box-shadow: 0 2px 6px rgba(0,0,0,.15); display: none; z-index: 2; }
          .msg { padding: 16px 0; color: var(--secondary-text-color); }
        </style>
        <ha-card>
          <div class="head"><span class="title"></span><span class="now"></span></div>
          <div class="chips"></div>
          <div class="chart"><div class="svg"></div><div class="tip"></div></div>
          <div class="legend">
            <span><i style="width:9px;height:9px;border-radius:50%;background:${C_CHARGE};border:1px solid #000"></i>Charge</span>
            <span><i style="width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:9px solid ${C_DISCHARGE}"></i>Discharge</span>
            <span><i style="width:16px;border-top:1.5px dashed var(--primary-text-color);opacity:.6"></i>Discharge price</span>
            <span class="sell-l" style="display:none"><i style="width:9px;height:9px;border-radius:50%;background:${C_SELL};border:1px solid #000"></i>Export</span>
            <span class="soc-l"><i style="width:16px;border-top:2px solid ${C_SOC}"></i>SOC</span>
          </div>
        </ha-card>`;
      this._root = this.shadowRoot;
      const chart = this._root.querySelector(".chart");
      const move = (ev) => this._tip(ev);
      chart.addEventListener("pointermove", move);
      chart.addEventListener("pointerdown", move);
      chart.addEventListener("pointerleave", () => { this._root.querySelector(".tip").style.display = "none"; });
    }

    _render() {
      if (!this._config) return;
      this._ensureDom();
      const root = this._root;
      root.querySelector(".title").textContent = this._config.title;
      root.querySelector(".soc-l").style.display = this._config.show_soc ? "" : "none";
      const svgBox = root.querySelector(".svg");
      const st = resolveEntity(this._hass, this._config.entity);
      const nowMs = Date.now();
      const model = st ? buildModel(st.attributes, this._config, nowMs) : null;
      if (!model) {
        root.querySelector(".now").textContent = "";
        root.querySelector(".chips").innerHTML = "";
        svgBox.innerHTML = `<div class="msg">${st ? "No plan available yet" : notFound(this._config.entity)}</div>`;
        this._model = null;
        return;
      }
      const a = model.attrs;
      const modeTxt = { charge: "charging" + (a.charge_power ? " " + a.charge_power + " W" : ""), discharge: "discharging", idle: "idle" }[st.state] || st.state;
      root.querySelector(".now").textContent =
        "Now " + eur(a.price_now) + " · " + modeTxt + (typeof a.soc === "number" ? " · " + a.soc.toFixed(0) + "%" : "");
      const chips = [
        "💶 Discharge price now " + eur(a.discharge_price),
        "🏷️ Cost " + eur(a.avg_cost),
        "🟢 " + eur(model.minS.p) + " (" + hm(model.minS.t) + ")",
        "🔴 " + eur(model.maxS.p) + " (" + hm(model.maxS.t) + ")",
      ];
      if (a.next_action && st.state === "idle") chips.unshift("⏭️ " + a.next_action);
      root.querySelector(".chips").innerHTML = chips.map((c) => `<span>${esc(c)}</span>`).join("");

      const W = Math.round(svgBox.clientWidth || this.clientWidth || 400);
      const { svg, geom } = renderSvg(model, this._config, W, this._config.height, nowMs);
      svgBox.innerHTML = svg;
      root.querySelector(".sell-l").style.display = model.slots.some((s) => s.act === "S") ? "" : "none";
      this._model = model;
      this._geom = geom;
    }

    _tip(ev) {
      const tip = this._root.querySelector(".tip");
      if (!this._model || !this._geom) return;
      const box = this._root.querySelector(".chart").getBoundingClientRect();
      const x = ev.clientX - box.left;
      const { m, pw, t0, t1 } = this._geom;
      const t = t0 + ((x - m.l) / pw) * (t1 - t0);
      const s = this._model.slots.find((sl) => t >= sl.t && t < sl.t + Q);
      if (!s) { tip.style.display = "none"; return; }
      const act = { C: "🔋 charge", D: "🔻 discharge", S: "💰 export" }[s.act];
      tip.innerHTML =
        `<b>${hm(s.t)}–${hm(s.t + Q)}</b> ${eur(s.p)}` +
        (act ? `<br>${act}` : "") +
        (s.thr !== null ? `<br>discharge price ${eur(s.thr)}` : "") +
        (s.soc !== null ? `<br>SOC ${s.soc.toFixed(0)}%${s.past ? " (measured)" : " (expected)"}` : "") +
        (s.past ? "<br><i>past</i>" : "");
      tip.style.display = "block";
      const tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(box.width - tw, x + 10)) + "px";
      tip.style.top = "4px";
    }
  }


  // ---------------------------------------------------------------- charge plan card
  const MODE_COL = { charge: C_CHARGE, discharge: C_DISCHARGE, export: C_SELL, grid: "var(--disabled-text-color, #aaa)", end: "var(--secondary-text-color, #888)" };

  class AccuPlannerPlanCard extends HTMLElement {
    setConfig(config) {
      this._config = { title: "Charge plan", max_steps: 12, ...config };
      this._lastState = undefined;
      this._expanded = false;
      this._render();
    }
    set hass(hass) {
      this._hass = hass;
      const st = resolveEntity(hass, this._config.entity);
      if (st !== this._lastState) { this._lastState = st; this._render(); }
    }
    getCardSize() { return 8; }
    static getStubConfig(hass) { return AccuPlannerCard.getStubConfig(hass); }

    _render() {
      if (!this._config) return;
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.innerHTML = `
          <style>
            ha-card { padding: 12px 16px 12px; }
            .title { font-size: 16px; font-weight: 500; margin-bottom: 6px; }
            .row { display: grid; grid-template-columns: 22px 1fr; gap: 0 6px; padding: 5px 0; border-top: 1px solid var(--divider-color, #eee); }
            .row:first-of-type { border-top: none; }
            .ic { font-size: 15px; line-height: 20px; }
            .t { font-size: 14px; line-height: 20px; }
            .sub { font-size: 12px; color: var(--secondary-text-color); margin-top: 2px; }
            .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
            .chip { font-size: 12px; padding: 1px 7px; border-radius: 10px; color: #fff; white-space: nowrap; }
            .steps { display: grid; grid-template-columns: auto 1fr auto; gap: 2px 8px; margin-top: 4px; font-size: 12px; align-items: center; }
            .bar { height: 6px; border-radius: 3px; background: var(--divider-color, #e0e0e0); position: relative; overflow: hidden; }
            .bar > i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; }
            .more { font-size: 12px; color: var(--primary-color); cursor: pointer; margin-top: 4px; display: inline-block; }
            .msg { color: var(--secondary-text-color); padding: 8px 0; }
            .lbl { color: var(--secondary-text-color); white-space: nowrap; }
          </style>
          <ha-card><div class="title"></div><div class="body"></div></ha-card>`;
        this.shadowRoot.querySelector(".body").addEventListener("click", (ev) => {
          if (ev.target.classList.contains("more")) { this._expanded = !this._expanded; this._render(); }
        });
      }
      const root = this.shadowRoot;
      root.querySelector(".title").textContent = this._config.title;
      const body = root.querySelector(".body");
      const st = resolveEntity(this._hass, this._config.entity);
      const report = st && st.attributes.report;
      if (!Array.isArray(report)) {
        body.innerHTML = `<div class="msg">${st ? "No charge plan available yet" : notFound(this._config.entity)}</div>`;
        return;
      }
      const steps = Array.isArray(st.attributes.soc_steps) ? st.attributes.soc_steps : [];
      const chipCol = { charge: C_CHARGE, discharge: C_DISCHARGE, sell: C_SELL };
      body.innerHTML = report.map((r) => {
        let extra = "";
        if (Array.isArray(r.items) && r.items.length) {
          extra += `<div class="chips">${r.items.map((it) => `<span class="chip" style="background:${chipCol[r.kind] || "#888"}">${esc(it)}</span>`).join("")}</div>`;
        }
        if (r.kind === "soc" && steps.length) {
          const max = this._config.max_steps;
          const shown = this._expanded || steps.length <= max ? steps : steps.slice(0, max - 1).concat([steps[steps.length - 1]]);
          extra += `<div class="steps">` + shown.map((s) =>
            `<span class="lbl">${esc(s.label)}</span>` +
            `<span class="bar"><i style="width:${Math.max(0, Math.min(100, s.soc))}%;background:${MODE_COL[s.mode] || C_SOC}"></i></span>` +
            `<span>${s.soc.toFixed(0)}% ${esc(s.mode)}</span>`).join("") + `</div>`;
          if (steps.length > max) extra += `<span class="more">${this._expanded ? "show less" : "show all " + steps.length + " changes"}</span>`;
        }
        if (r.sub) extra += `<div class="sub">${esc(r.sub)}</div>`;
        return `<div class="row"><span class="ic">${esc(r.icon || "")}</span><div><div class="t">${esc(r.title)}</div>${extra}</div></div>`;
      }).join("");
    }
  }

  if (!customElements.get("accu-planner-card")) {
    customElements.define("accu-planner-card", AccuPlannerCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "accu-planner-card",
      name: "Accu Planner – charge schedule",
      description: "Quarter-hour prices with planned charging/discharging, discharge price and expected SOC.",
      preview: true,
    });
    console.info(`%c ACCU-PLANNER-CARD %c ${VERSION} `, "background:#00a964;color:#fff", "");
  }
  if (!customElements.get("accu-planner-plan-card")) {
    customElements.define("accu-planner-plan-card", AccuPlannerPlanCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "accu-planner-plan-card",
      name: "Accu Planner – charge plan",
      description: "The charge plan as an overview: now, stored energy cost, charge/discharge times, SOC, discharge and sell price.",
      preview: true,
    });
  }
  // for tests
  window.__accuPlanner = { buildModel, renderSvg };
})();
