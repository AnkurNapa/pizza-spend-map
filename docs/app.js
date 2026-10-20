(async function () {
  const V = window.ASSET_V;
  const [geo, comps, schools] = await Promise.all([V.data, V.comps, V.schools].map(u => fetch(u).then(r => r.json())));
  const meta = geo.meta, S = meta.sources;
  const feats = geo.features;
  const areaSel = document.getElementById("area");
  const DEFAULT_W = { demand: 40, spend: 30, gap: 30 };
  const RAMP = ["#E6ECFA", "#B9C9F0", "#7F9BE0", "#4A6FD3", "#2D56CD", "#16307E"];
  const state = { layer: "opportunity", area: "", inner: false, w: { ...DEFAULT_W }, selected: null };

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 0) => v == null ? "n/a" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d });
  const pct = v => v == null ? "n/a" : fmt(v * 100, 1) + "%";
  const gbp = v => v == null ? "n/a" : "£" + fmt(v);

  // ---------- scoring (sub-scores come from build.py; only the weights change here) ----------
  function opportunity(p) {
    const parts = [["demand", p.demand], ["spend", p.spend], ["gap", p.gap]].filter(([, v]) => v != null);
    const wsum = parts.reduce((a, [k]) => a + state.w[k], 0);
    return wsum ? parts.reduce((a, [k, v]) => a + v * state.w[k], 0) / wsum : null;
  }
  const value = p => state.layer === "opportunity" ? opportunity(p) : p[state.layer];
  const inView = p => (state.inner || p.ring !== "Inner London") && (!state.area || p.area === state.area);
  const colour = v => v == null ? "#CCCCCC" : RAMP[Math.min(5, Math.floor(v / (100 / 6)))];

  // ---------- map ----------
  // Same map behaviour as the Beverage-AI Radar: wheel zoom off so the page scrolls, a back button, resize-safe
  const map = L.map("map", { zoomSnap: 0.25, scrollWheelZoom: false });
  new ResizeObserver(() => map.invalidateSize()).observe(document.getElementById("map"));
  const back = L.control({ position: "topright" });
  back.onAdd = () => {
    const b = L.DomUtil.create("button", "map-back");
    b.type = "button"; b.textContent = "Back to all areas"; b.hidden = true;
    L.DomEvent.disableClickPropagation(b);
    b.addEventListener("click", () => { state.selected = null; state.area = ""; areaSel.value = ""; render(); fitView(); });
    return b;
  };
  // CARTO Voyager now needs an API key (keyless tiles return a placeholder since Oct 2026), so Esri's keyless light grey canvas is used
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_";
  L.tileLayer(ESRI + "Base/MapServer/tile/{z}/{y}/{x}", {
    attribution: 'Basemap &copy; <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, &copy; OpenStreetMap contributors', maxZoom: 16,
  }).addTo(map);
  map.createPane("areas").style.zIndex = 400;
  map.createPane("labels").style.zIndex = 450;
  map.getPane("labels").style.pointerEvents = "none";
  L.tileLayer(ESRI + "Reference/MapServer/tile/{z}/{y}/{x}", { pane: "labels", maxZoom: 16 }).addTo(map);
  const areaLayer = L.geoJSON(geo, {
    pane: "areas",
    style: () => ({}),
    onEachFeature: (f, layer) => {
      layer.on("click", () => select(f.properties.code));
      layer.bindTooltip(() => `<b>${esc(f.properties.name)}</b><br>${esc(label())}: ${fmt(value(f.properties), 1)}`, { sticky: true });
    },
  }).addTo(map);
  const byCode = {};
  areaLayer.eachLayer(l => { byCode[l.feature.properties.code] = l; });

  const legend = L.control({ position: "bottomleft" });
  legend.onAdd = () => L.DomUtil.create("div", "legend");
  legend.addTo(map);
  const label = () => ({ opportunity: "Opportunity", demand: "Demand", spend: "Spend", gap: "Gap" })[state.layer];

  // competitor pins
  const cluster = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 40 });
  comps.forEach(c => {
    const m = L.circleMarker([c.lat, c.lon], { radius: 6, weight: 1.5, color: "#fff", fillOpacity: 1, fillColor: c.chain ? "#BD6444" : "#0F182E" });
    const links = [c.fhrsid ? `<a href="https://ratings.food.gov.uk/business/${encodeURIComponent(c.fhrsid)}" target="_blank" rel="noopener">FSA record</a>` : "",
      c.osm ? `<a href="https://www.openstreetmap.org/${esc(c.osm)}" target="_blank" rel="noopener">OSM</a>` : ""].filter(Boolean).join(" · ");
    m.on("click", () => { if (c.msoa) { state.selected = c.msoa; render(); } });
    m.bindPopup(`<b>${esc(c.name)}</b><br>${c.chain ? esc(c.chain) + " (chain)" : "Independent"}<br>` +
      `${esc(c.address || "")}<br>Hygiene rating: ${esc(c.rating ?? "not on FSA")}${c.rating_date ? " (" + esc(c.rating_date) + ")" : ""}<br>` +
      `Source: ${esc(c.source)}<br>${links}`);
    cluster.addLayer(m);
  });
  cluster.addTo(map);
  back.addTo(map);

  // school zones
  const zoneLayer = L.layerGroup(schools.map(s => {
    const local = s.rule === "local", c = local ? "#B3261E" : "#C77700";
    return L.circle([s.lat, s.lon], { radius: s.radius, color: c, weight: 1, dashArray: "4 3", fillColor: c, fillOpacity: 0.08 })
      .bindPopup(`<b>${esc(s.name)}</b><br>${local
        ? `${esc(s.la)} has its own rule on takeaways near schools, on top of national policy HC5.`
        : `${esc(s.la)} has no local rule. National policy HC5 still says refuse within about 400 m unless in a designated town centre.`}`);
  }));
  zoneLayer.addTo(map);

  // ---------- render ----------
  function ranked() {
    return feats.map(f => f.properties).filter(inView).map(p => ({ p, v: opportunity(p) }))
      .filter(x => x.v != null).sort((a, b) => b.v - a.v);
  }

  function render() {
    areaLayer.eachLayer(l => {
      const p = l.feature.properties, on = inView(p), sel = p.code === state.selected;
      l.setStyle({ fillColor: colour(value(p)), fillOpacity: on ? 0.72 : 0.12, color: sel ? "#1B1B17" : "#fff", weight: sel ? 3 : 0.6, opacity: on ? 1 : 0.3 });
      if (sel) l.bringToFront();
    });
    back.getContainer().hidden = !(state.selected || state.area);
    legend.getContainer().innerHTML = `<b>${esc(label())} score</b><div class="ramp">${RAMP.map(c => `<i style="background:${c}"></i>`).join("")}</div><div class="ticks"><span>0</span><span>50</span><span>100</span></div>`;
    const top = ranked();
    const rankOf = Object.fromEntries(top.map((x, i) => [x.p.code, i + 1]));
    document.querySelector("#top tbody").innerHTML = top.slice(0, 20).map((x, i) => `
      <tr tabindex="0" data-code="${esc(x.p.code)}" class="${x.p.code === state.selected ? "sel" : ""}">
        <td>${i + 1}</td><td>${esc(x.p.name)}</td><td>${esc(x.p.area)}</td><td class="num">${fmt(x.v, 1)}</td>
        <td class="num">${fmt(x.p.population)}</td><td class="num">${gbp(x.p.net_income)}</td><td class="num">${fmt(x.p.outlets)}</td></tr>`).join("");
    const wsum = state.w.demand + state.w.spend + state.w.gap;
    document.getElementById("top-note").textContent = wsum === 0
      ? "All weights are zero, so there is nothing to rank. Move a slider."
      : `Ranked within ${state.area || (state.inner ? "all 15 boroughs and districts" : "outer London and Essex")} (${top.length} areas). Weights: demand ${state.w.demand}, spend ${state.w.spend}, gap ${state.w.gap}.`;
    renderPanel(rankOf, top.length);
  }

  function src(key) {
    const s = S[key];
    const name = esc(s && (s.short || s.label));
    return s ? `<small>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" title="${esc(s.label)}">${name}</a>` : name}, ${esc(s.date || "date unknown")}</small>` : "";
  }

  function renderPanel(rankOf, n) {
    const el = document.getElementById("panel");
    const p = state.selected && feats.find(f => f.properties.code === state.selected)?.properties;
    if (!p) {
      el.innerHTML = `<h3>Pick an area</h3><p class="sub">Click any area on the map or a row in the Top 20 to see every number behind its score, with the source of each one.</p>
        <p class="note">Outer London and Essex are shown by default. Tick "Include inner London" to add Newham, Tower Hamlets and Hackney.</p>`;
      return;
    }
    const rule = meta.rules.find(r => r.council === p.area);
    const outlets = comps.filter(c => c.msoa === p.code);
    const row = (k, v, s) => `<dt>${k}${src(s)}</dt><dd>${v}</dd>`;
    el.innerHTML = `
      <h3>${esc(p.name)}</h3>
      <p class="sub">${esc(p.area)}, ${esc(p.ring)} · ${esc(p.code)} · ${rankOf[p.code] ? `rank ${rankOf[p.code]} of ${n} in view` : "not in the current view"}</p>
      <div class="bigscore">
        <div class="main"><b>${fmt(opportunity(p), 1)}</b><span>Opportunity</span></div>
        <div><b>${fmt(p.demand, 0)}</b><span>Demand</span></div>
        <div><b>${fmt(p.spend, 0)}</b><span>Spend</span></div>
        <div><b>${fmt(p.gap, 0)}</b><span>Gap</span></div>
      </div>
      <dl class="facts">
        <dt class="head">Demand</dt>
        ${row("Population", fmt(p.population), "census")}
        ${row("Households", fmt(p.households), "census")}
        ${row("People per sq km", fmt(p.density), "census")}
        ${row("Aged 16 to 34", pct(p.age16_34), "census")}
        ${row("Full-time students (16+)", pct(p.students), "census")}
        ${row("Households with dependent children", pct(p.kids_hh), "census")}
        ${row("One-person households", pct(p.one_person_hh), "census")}
        ${row("Households in flats", pct(p.flats), "census")}
        ${row("Households renting", pct(p.renters), "census")}
        <dt class="head">Ability to spend</dt>
        ${row("Net annual household income", gbp(p.net_income), "income")}
        ${row("Income deprivation rate", pct(p.income_deprivation), "imd")}
        ${row("Regional takeaway multiplier", fmt(p.takeaway_multiplier, 3), "family_spending")}
        <dt class="head">Competition</dt>
        ${row("Pizza outlets in this area", fmt(p.outlets), "fsa")}
        ${row("of which chains", fmt(p.chains), "fsa")}
        ${row("of which independents", fmt(p.independents), "osm")}
        ${row("Weighted outlets per 1,000 residents", fmt(p.outlets_per_1000, 2), "fsa")}
        <dt class="head">Constraints</dt>
        ${row("Local or London Plan school rule", rule ? (rule.applies === true ? `Yes, ${esc(rule.distance_m ?? 400)} m` : rule.applies === false ? "No" : "Unclear") : "Not researched", "policy")}
        ${row("National rule (NPPF HC5)", "Yes, about 400 m", "nppf")}
        ${row("Share of area within 400 m of a secondary school", p.school_zone_share == null ? "n/a" : pct(p.school_zone_share), "schools")}
        ${row("Urban or rural (ONS)", esc(p.urban_rural || "n/a"), "boundaries")}
      </dl>
      ${rule ? `<p class="note">${esc(rule.note || "")} ${rule.source_url ? `<a href="${esc(rule.source_url)}" target="_blank" rel="noopener">Policy source</a>` : ""}</p>` : ""}
      ${outlets.length ? `<p class="label" style="margin-top:14px">Outlets here</p><ul class="outlet-list">${outlets.map(c => `<li>${esc(c.name)}${c.chain ? " (chain)" : ""}, rating ${esc(c.rating ?? "n/a")}</li>`).join("")}</ul>` : ""}`;
  }

  function select(code, zoom) {
    state.selected = code;
    render();
    if (zoom && byCode[code]) map.fitBounds(byCode[code].getBounds(), { maxZoom: 14, padding: [30, 30] });
    if (window.matchMedia("(max-width: 900px)").matches) document.getElementById("panel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function fitView() {
    const ls = Object.values(byCode).filter(l => inView(l.feature.properties));
    if (ls.length) map.fitBounds(L.featureGroup(ls).getBounds(), { padding: [10, 10] });
  }

  // ---------- controls ----------
  document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".seg button").forEach(x => x.setAttribute("aria-checked", x === b));
    state.layer = b.dataset.layer; render();
  }));
  Object.entries(meta.areas).forEach(([a, ring]) => areaSel.add(new Option(`${a} (${ring})`, a)));
  areaSel.addEventListener("change", () => {
    state.area = areaSel.value;
    if (state.area && meta.areas[state.area] === "Inner London") { state.inner = true; document.getElementById("inner").checked = true; }
    render(); fitView();
  });
  document.getElementById("inner").addEventListener("change", e => { state.inner = e.target.checked; render(); fitView(); });
  ["demand", "spend", "gap"].forEach(k => document.getElementById("w-" + k).addEventListener("input", e => {
    state.w[k] = +e.target.value; document.getElementById("o-" + k).value = e.target.value; render();
  }));
  document.getElementById("reset").addEventListener("click", () => {
    state.w = { ...DEFAULT_W };
    Object.entries(DEFAULT_W).forEach(([k, v]) => { document.getElementById("w-" + k).value = v; document.getElementById("o-" + k).value = v; });
    render();
  });
  document.getElementById("pins").addEventListener("change", e => e.target.checked ? cluster.addTo(map) : map.removeLayer(cluster));
  document.getElementById("zones").addEventListener("change", e => e.target.checked ? zoneLayer.addTo(map) : map.removeLayer(zoneLayer));
  const tbody = document.querySelector("#top tbody");
  tbody.addEventListener("click", e => { const tr = e.target.closest("tr"); if (tr) select(tr.dataset.code, true); });
  tbody.addEventListener("keydown", e => { const tr = e.target.closest("tr"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(tr.dataset.code, true); } });

  // ---------- footer ----------
  document.getElementById("built").textContent = meta.built;
  document.getElementById("sources").innerHTML = Object.values(S).map(s =>
    `<li>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>` : esc(s.label)}. Downloaded ${esc(s.date || "date unknown")}.${s.note ? ` <span class="warn">${esc(s.note)}</span>` : ""}</li>`).join("") +
    meta.rules.filter(r => r.source_url).map(r => `<li>${esc(r.council)} takeaway policy: <a href="${esc(r.source_url)}" target="_blank" rel="noopener">${esc(r.policy || "source")}</a>.</li>`).join("");

  render();
  fitView();
})().catch(err => {
  document.getElementById("panel").innerHTML = `<h3>Data failed to load</h3><p class="sub">${String(err).replace(/[<>&]/g, "")}</p>`;
});
