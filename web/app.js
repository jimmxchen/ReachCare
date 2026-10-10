const $ = (s, p = document) => p.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => new Intl.NumberFormat('en-US').format(Math.round(v || 0));
const NS = 'http://www.w3.org/2000/svg';
const MILES_PER_MINUTE = 0.58; // keep in sync with planner/siting.py
const LAT0 = 35.2, KX = Math.cos(LAT0 * Math.PI / 180), SCALE = 118; // px per degree of latitude
const LON_MIN = -84.4, LAT_MAX = 36.62;
const W = 1000, H = 380;
const project = (lon, lat) => [(lon - LON_MIN) * KX * SCALE, (LAT_MAX - lat) * SCALE];
const unproject = (x, y) => [LON_MIN + x / (KX * SCALE), LAT_MAX - y / SCALE];
const milesToPx = miles => miles / 69.09 * SCALE;

const form = $('#plan-form');
let place = null, options, tracts = [], tractPaths = new Map(), landPath = '', lastPlan = null;

function show(msg) { const e = $('#error'); e.textContent = msg; e.classList.toggle('hidden', !msg); if (msg) e.scrollIntoView({block: 'nearest'}); }
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || 'Request could not complete');
  return data;
}
function el(name, attrs = {}, parent) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  parent?.appendChild(n);
  return n;
}
const ringPath = ring => ring.map(([lon, lat], i) => { const [x, y] = project(lon, lat); return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`; }).join('') + 'Z';

// ---------- Routing ----------
function route() {
  if ($('#results').open) $('#results').close();
  const docs = location.hash.startsWith('#/docs');
  $('#plan-view').classList.toggle('hidden', docs);
  $('#docs-view').classList.toggle('hidden', !docs);
  document.querySelectorAll('[data-nav]').forEach(a => {
    if (a.dataset.nav === (docs ? 'docs' : 'plan')) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (docs && !$('#docs-view').innerHTML) renderDocs();
  document.title = 'ReachCare';
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);

// ---------- Map drawing ----------
function drawBase(svg, withTracts) {
  svg.replaceChildren();
  el('path', {d: landPath, class: 'land'}, svg);
  if (withTracts) {
    const g = el('g', {}, svg);
    for (const [id, status, priority] of withTracts) {
      const d = tractPaths.get(id);
      if (!d) continue;
      el('path', {d, class: `tract ${status}`, 'fill-opacity': (0.35 + 0.65 * priority).toFixed(2)}, g);
    }
  }
}
function drawRing(svg, lat, lon, miles) {
  const [x, y] = project(lon, lat), r = milesToPx(miles);
  el('circle', {cx: x, cy: y, r, class: 'ring-fill'}, svg);
  el('circle', {cx: x, cy: y, r, class: 'ring'}, svg);
}
function drawHospital(svg, lat, lon, name, f = 1) {
  const [x, y] = project(lon, lat);
  const g = el('g', {class: 'hospital', transform: `translate(${x} ${y}) scale(${f})`}, svg);
  el('path', {d: 'M-3-9h6v6h6v6h-6v6h-6v-6h-6v-6h6z'}, g);
  if (name) { const t = el('text', {x: 14, y: -8, class: 'halo'}, g); t.textContent = name; }
}
// ---------- Results ----------
function renderResults(plan) {
  lastPlan = plan;
  const {hospital, clinics, summary, params} = plan;
  $('#results-title').textContent = `${plan.service_name} · ${hospital.name}`;
  $('#results-address').textContent = hospital.address || '';
  drawResultMap();
  const m = $('#metrics');
  m.replaceChildren();
  [[fmt(summary.adults_newly_served), `adults newly within ${params.travel_target} min`],
   [Math.round(summary.share_of_need_addressed * 100) + '%', 'of need-weighted demand reached'],
   [fmt(summary.total_capacity), `services per clinic day across ${clinics.length} site${clinics.length === 1 ? '' : 's'} (${fmt(summary.capacity_per_clinic_day)} each)`],
   ['$' + fmt(summary.estimated_cost), `estimated cost for ${clinics.length} clinic day${clinics.length === 1 ? '' : 's'}`]].forEach(([v, l]) => {
    const d = document.createElement('div'); d.className = 'metric';
    d.innerHTML = `<strong>${esc(v)}</strong><span>${esc(l)}</span>`; m.appendChild(d);
  });
  const list = $('#clinic-list');
  list.replaceChildren();
  clinics.forEach(c => {
    const li = document.createElement('li'); li.className = 'clinic';
    const when = new Date(c.date + 'T12:00:00').toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric'});
    li.innerHTML = `<span class="badge" aria-hidden="true">${c.rank}</span><h3>${esc(c.name)}</h3>
      <div class="addr">${esc([c.address, c.city].filter(Boolean).join(', '))} · ${esc(c.county)} County</div>
      <dl><div><dt>Suggested date</dt><dd>${esc(when)}</dd></div><div><dt>Adults newly in reach</dt><dd>${fmt(c.new_adults)}</dd></div>
      <div><dt>Capacity per day</dt><dd>${fmt(c.capacity)}</dd></div>
      <div><dt>${esc(plan.indicator_label)}</dt><dd>${c.avg_indicator ?? '—'}%</dd></div><div><dt>No-vehicle homes</dt><dd>${c.avg_no_vehicle ?? '—'}%</dd></div>
      <div><dt>Chosen in</dt><dd>${c.stability.chosen_in} of ${c.stability.of} priority mixes</dd></div></dl>`;
    list.appendChild(li);
  });
  $('#method-note').textContent = plan.method.note + ' Reach describes who lives near a site, not expected turnout.';
  $('#method-note').textContent += ` Each clinic day is costed at $${fmt(summary.cost_per_clinic_day)} (staff time plus $${plan.method.event_fixed_cost} for supplies and space).`;
  $('#why').innerHTML = `<p>${esc(templateWhy(plan))}</p><p class="src" id="why-src">Plain-language summary generated from the figures shown.</p>`;
  if (options?.gemini) explain(params);
  setupAnnounce(plan);
  setupChat(plan);
  const dlg = $('#results');
  if (!dlg.open) dlg.showModal();
  dlg.querySelector('.results-body').scrollTop = 0;
}
const CHAT_STARTERS = ['Why were these communities prioritized?', 'What if we cut the travel target to 15 minutes?',
  'Is a fourth clinic day worth it?', 'Which communities are left out?', 'How would more staff change capacity?'];
let chatHistory = [];
function setupChat(plan) {
  chatHistory = [];
  const log = $('#chat-log'), chips = $('#chat-chips'), form = $('#chat-form'), input = $('#chat-input'), send = $('#chat-send');
  log.replaceChildren(); chips.replaceChildren(); input.value = '';
  const enabled = !!options?.gemini;
  form.classList.toggle('hidden', !enabled);
  $('#chat-note').textContent = enabled ? "Answers come from this plan's data. Powered by Google Gemini." : 'Add GEMINI_API_KEY to .env to ask questions about this plan.';
  if (!enabled) return;
  const add = (cls, text) => { const d = document.createElement('div'); d.className = 'cmsg ' + cls; d.textContent = text; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; };
  async function ask(q) {
    q = q.trim(); if (!q || send.disabled) return;
    chips.replaceChildren(); input.value = '';
    add('me', q); chatHistory.push({role: 'user', text: q});
    send.disabled = true; input.disabled = true;
    const wait = add('ai', ''); wait.innerHTML = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>Looking at the plan…';
    try {
      const r = await api('/api/plan-chat', {params: plan.params, messages: chatHistory});
      wait.textContent = r.reply; chatHistory.push({role: 'assistant', text: r.reply});
    } catch (e) { wait.textContent = e.message; chatHistory.pop(); }
    finally { send.disabled = false; input.disabled = false; input.focus(); log.scrollTop = log.scrollHeight; }
  }
  CHAT_STARTERS.forEach(q => { const b = document.createElement('button'); b.type = 'button'; b.textContent = q; b.addEventListener('click', () => ask(q)); chips.appendChild(b); });
  form.onsubmit = e => { e.preventDefault(); ask(input.value); };
}

async function setupAnnounce(plan) {
  const box = $('#announce');
  box.replaceChildren();
  if (!plan.id) return;
  try {
    const r = await api('/api/bot/notify', {plan_id: plan.id, dry_run: true});
    const label = document.createElement('div');
    label.innerHTML = r.people
      ? `<b>${r.people} subscriber${r.people === 1 ? '' : 's'}</b> nearby asked for these services. Text them about these clinics?`
      : `<b>No subscribers near these clinics yet.</b> Share the <a href="/join.html" target="_blank" rel="noopener">sign-up page</a> to build the list.`;
    box.appendChild(label);
    if (r.people) {
      const row = document.createElement('div'); row.className = 'row';
      const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'button primary'; btn.textContent = 'Text subscribers';
      const status = document.createElement('span'); status.className = 'muted small'; status.setAttribute('role', 'status');
      btn.addEventListener('click', async () => {
        btn.disabled = true; status.textContent = 'Queuing…';
        try { const s = await api('/api/bot/notify', {plan_id: plan.id}); status.textContent = `Sending ${s.queued} text${s.queued === 1 ? '' : 's'} to ${s.people} ${s.people === 1 ? 'person' : 'people'}.`; status.classList.add('ok'); }
        catch (e) { status.textContent = e.message; btn.disabled = false; }
      });
      row.append(btn, status); box.appendChild(row);
    }
  } catch { /* announcements unavailable */ }
}
function templateWhy(plan) {
  const {clinics, summary, params} = plan;
  const first = clinics[0];
  return `Within a ${params.radius}-minute drive of ${plan.hospital.name} there are ${fmt(summary.adults_in_area)} adults. ` +
    `${clinics.length === 1 ? 'The best single host site is' : `The ${clinics.length} sites chosen together reach the most need-weighted adults, starting with`} ${first.name} in ${first.city || first.county + ' County'}, ` +
    `which brings ${fmt(first.new_adults)} adults within ${params.travel_target} minutes. Priority combines ${plan.indicator_label.toLowerCase()} rates and households without a vehicle, based on census-derived estimates.`;
}
async function explain(params) {
  try {
    const r = await api('/api/gemini/explain', {params});
    $('#why').innerHTML = `<p>${esc(r.text)}</p><p class="src">Explained by Gemini from the figures shown. Numbers come from census data, not from the model.</p>`;
  } catch { /* keep template summary */ }
}
const FULL = {x: 0, y: 0, w: W, h: H};
let view = {...FULL}, markers = [], serviceView = {...FULL};
function applyView() {
  const svg = $('#result-map');
  svg.setAttribute('viewBox', `${view.x.toFixed(1)} ${view.y.toFixed(1)} ${view.w.toFixed(1)} ${view.h.toFixed(1)}`);
  const f = view.w / W * 1.15;
  markers.forEach(m => m.g.setAttribute('transform', `translate(${m.x} ${m.y}) scale(${f})`));
}
function drawResultMap() {
  const plan = lastPlan, svg = $('#result-map');
  if (!plan) return;
  const {hospital, clinics} = plan;
  const [hx, hy] = project(hospital.lon, hospital.lat), r = milesToPx(plan.radius_miles) * 1.3 + 30;
  const sw = 2 * r, sh = sw * 0.62;
  serviceView = {x: hx - sw / 2, y: hy - sh / 2, w: sw, h: sh};
  view = {...serviceView};
  drawBase(svg, plan.tracts.map(([id, p, s]) => [id, s, p]));
  drawRing(svg, hospital.lat, hospital.lon, plan.radius_miles);
  markers = [];
  drawHospital(svg, hospital.lat, hospital.lon, hospital.name);
  markers.push({g: svg.lastChild, x: hx, y: hy});
  clinics.forEach(c => {
    const [x, y] = project(c.lon, c.lat);
    const g = el('g', {class: 'clinic'}, svg);
    el('circle', {r: 11}, g);
    el('text', {class: 'n', y: 4}, g).textContent = c.rank;
    el('title', {}, g).textContent = `${c.label}: ${c.name}`;
    el('text', {x: 16, y: 5, class: 'halo'}, g).textContent = c.name;
    markers.push({g, x, y});
  });
  applyView();
}
function zoomBy(factor, cx, cy) {
  const w = Math.min(Math.max(view.w * factor, W / 40), W * 1.2), k = w / view.w;
  cx ??= view.x + view.w / 2; cy ??= view.y + view.h / 2;
  view = {x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k, w, h: view.h * k};
  applyView();
}
function svgPoint(e) {
  const svg = $('#result-map'), pt = svg.createSVGPoint();
  pt.x = e.clientX; pt.y = e.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  return [p.x, p.y];
}
(() => {
  const svg = $('#result-map');
  let drag = null;
  svg.addEventListener('wheel', e => {
    e.preventDefault();
    const px = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
    const [x, y] = svgPoint(e);
    zoomBy(Math.exp(Math.max(-60, Math.min(60, px)) * 0.0012), x, y);
  }, {passive: false});
  svg.addEventListener('pointerdown', e => { drag = {px: e.clientX, py: e.clientY, v: {...view}}; svg.setPointerCapture(e.pointerId); svg.classList.add('dragging'); });
  svg.addEventListener('pointermove', e => {
    if (!drag) return;
    const s = view.w / svg.getBoundingClientRect().width;
    view.x = drag.v.x - (e.clientX - drag.px) * s; view.y = drag.v.y - (e.clientY - drag.py) * s;
    applyView();
  });
  const end = () => { drag = null; svg.classList.remove('dragging'); };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  $('#zoom-in').addEventListener('click', () => zoomBy(0.8));
  $('#zoom-out').addEventListener('click', () => zoomBy(1.25));
  $('#zoom-reset').addEventListener('click', () => { view = {...serviceView}; applyView(); });
})();
$('#close-results').addEventListener('click', () => $('#results').close());

// ---------- Hospital autocomplete ----------
const session = (crypto.randomUUID?.() || String(Date.now()) + Math.random().toString(16).slice(2)).replace(/-/g, '');
(() => {
  const input = $('#hospital-name'), list = $('#suggest-list'), status = $('#place-status');
  let items = [], active = -1, timer = 0, seq = 0;
  const close = () => { list.classList.add('hidden'); input.setAttribute('aria-expanded', 'false'); active = -1; };
  const mark = i => {
    active = i;
    [...list.children].forEach((li, n) => li.setAttribute('aria-selected', n === i ? 'true' : 'false'));
    if (i >= 0) input.setAttribute('aria-activedescendant', `sug-${i}`); else input.removeAttribute('aria-activedescendant');
  };
  const render = () => {
    list.replaceChildren();
    if (!items.length) { close(); return; }
    items.forEach((s, i) => {
      const li = document.createElement('li');
      li.id = `sug-${i}`; li.setAttribute('role', 'option'); li.setAttribute('aria-selected', 'false');
      li.innerHTML = `<b>${esc(s.name)}</b><span>${esc(s.detail)}</span>`;
      li.addEventListener('mousedown', e => { e.preventDefault(); choose(i); });
      list.appendChild(li);
    });
    list.classList.remove('hidden'); input.setAttribute('aria-expanded', 'true');
  };
  async function choose(i) {
    const s = items[i]; close();
    input.value = s.name; status.textContent = 'Locating…';
    try {
      const d = await api(`/api/places/details?id=${encodeURIComponent(s.id)}&session=${session}`);
      input.value = s.name;
      place = {text: s.name, lat: d.lat, lon: d.lon, address: d.address};
      status.textContent = d.address; status.classList.add('place-ok');
    } catch (err) { place = null; status.textContent = err.message; status.classList.remove('place-ok'); }
  }
  input.addEventListener('input', () => {
    place = null; status.classList.remove('place-ok');
    status.textContent = 'Choose a suggestion so we use the exact address.';
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 3) { items = []; render(); return; }
    timer = setTimeout(async () => {
      const mine = ++seq;
      try {
        const r = await api(`/api/places/suggest?q=${encodeURIComponent(q)}&session=${session}`);
        if (mine !== seq) return;
        items = r.suggestions; render();
      } catch (err) { if (mine === seq) { items = []; close(); status.textContent = err.message; } }
    }, 250);
  });
  input.addEventListener('keydown', e => {
    if (list.classList.contains('hidden')) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); mark((active + 1) % items.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); mark((active - 1 + items.length) % items.length); }
    else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); choose(active); }
    else if (e.key === 'Escape') close();
  });
  input.addEventListener('blur', () => setTimeout(close, 120));
})();

// ---------- Form ----------
function payload() {
  const f = new FormData(form), num = k => Number(f.get(k));
  const name = f.get('hospital_name').trim();
  const p = place && place.text === name ? {lat: place.lat, lon: place.lon, address: place.address} : {};
  return {...p, hospital_name: name, service: f.get('service'), radius: num('radius'),
    travel_target: num('travel_target'), date_start: f.get('date_start'), date_end: f.get('date_end'), days: num('days'),
    staff_count: num('staff_count'), clinical_hours: num('clinical_hours'), services_per_staff_hour: num('services_per_staff_hour'),
    use_existing_care: form.use_existing_care.checked};
}
function updateHints() {
  const staff = +form.staff_count.value, hours = +form.clinical_hours.value, rate = +form.services_per_staff_hour.value, days = +form.days.value;
  const perDay = staff * hours * rate, cost = staff * hours * 55 + 400;
  $('#capacity-hint').textContent = `About ${fmt(perDay)} services per clinic day, ${fmt(perDay * days)} over ${days} day${days === 1 ? '' : 's'}. Estimated cost $${fmt(cost * days)}.`;
}
form.addEventListener('input', updateHints);
form.addEventListener('submit', async e => {
  e.preventDefault(); show('');
  if (!form.hospital_name.value.trim()) { form.hospital_name.focus(); return show('Enter your hospital name.'); }
  const btn = $('#run'), lab = $('#run-label'); btn.disabled = true; lab.textContent = 'Locating hospital and simulating…';
  try { renderResults(await api('/api/siting', payload())); }
  catch (err) { show(err.message); }
  finally { btn.disabled = false; lab.textContent = 'Find optimal clinic location'; }
});

// ---------- Docs ----------
function renderDocs() {
  const radii = [15, 30, 45, 60].map(m => `<tr><td>${m} minutes</td><td>${(m * MILES_PER_MINUTE).toFixed(1)} miles</td></tr>`).join('');
  $('#docs-view').innerHTML = `<article class="docs">
  <p class="eyebrow">DOCUMENTATION</p>
  <h1>How the clinic recommendation works</h1>
  <p class="lead">The recommendation is a transparent optimization over census tracts. It is not a trained machine-learning model: every number can be traced back to a census estimate and a stated assumption.</p>
  <nav class="toc" aria-label="On this page"><a href="#/docs" data-jump="overview">Overview</a><a href="#/docs" data-jump="data">Data</a><a href="#/docs" data-jump="algorithm">Algorithm</a><a href="#/docs" data-jump="robust">Robustness</a><a href="#/docs" data-jump="limits">Limits</a><a href="#/docs" data-jump="gemini">Gemini</a></nav>

  <h2 id="overview">Overview</h2>
  <p>A hospital gives us its name (located with Google Maps), a driving radius, a service, a patient travel target, dates and the number of clinic days. We score 2,637 North Carolina census tracts (10 of the state's 2,647 lack the data needed) on how much they need the service, then pick library host sites so the most need falls within the travel target of a free clinic, one site per clinic day.</p>
  <pre>inputs → need score per tract → tracts in hospital radius → drop tracts with existing access
       → candidate host sites → greedy maximum-coverage pick → dates → robustness check</pre>

  <h2 id="data">Data</h2>
  <table><thead><tr><th>Signal</th><th>Source</th><th>Used for</th></tr></thead><tbody>
  <tr><td>Adults 18+</td><td>Census ACS 2024 5-year, B01001</td><td>Population weight</td></tr>
  <tr><td>Households with no vehicle</td><td>Census ACS 2024 5-year, B08201</td><td>Transport barrier</td></tr>
  <tr><td>Diagnosed high blood pressure, diabetes, COPD, current asthma</td><td>CDC PLACES 2025 (modeled tract estimates)</td><td>Health need, one indicator per service</td></tr>
  <tr><td>Host sites</td><td>IMLS public library outlets, FY2024 (397)</td><td>Candidate clinic locations</td></tr>
  <tr><td>Existing care</td><td>NCDHHS safety-net directory + OSRM road times</td><td>Baseline access</td></tr></tbody></table>
  <p class="callout">All census figures are read from files bundled with the app. They are never generated by an AI model.</p>

  <h2 id="algorithm">Algorithm</h2>
  <h3>1. Need score for each tract</h3>
  <pre>health_index  = clip((indicator_percent − low) / (high − low), 0, 1)
barrier_index = clip(no_vehicle_percent / 20, 0, 1)
priority      = w · health_index + (1 − w) · barrier_index
need_weight   = adults_18_plus × priority</pre>
  <p>The indicator and its fixed <code>low</code>–<code>high</code> benchmark depend on the service: blood pressure 15–55%, diabetes 4–24%, COPD 2–15%, asthma 8.5–14.5%. <code>w</code> is 70% health and 30% transport barriers. Fixed benchmark ranges mean a score does not change when you add or remove candidate sites.</p>
  <h3>2. Service area</h3>
  <p>Your hospital is located with Google Places (the suggestion you pick, or a text search of the name, with address geocoding as a fallback). Your driving radius is converted to distance at ${MILES_PER_MINUTE} miles per minute, which approximates rural North Carolina road speed and circuity. Only tracts whose center lies inside the circle count.</p>
  <table><thead><tr><th>Driving radius</th><th>Straight-line circle</th></tr></thead><tbody>${radii}</tbody></table>
  <h3>3. Existing access</h3>
  <p>A tract is treated as already served when one of the 27 geocoded sites in the NCDHHS safety-net directory is within the patient travel target by cached road drive time (a precomputed OSRM matrix). These tracts add nothing to a new clinic's score. You can switch this off.</p>
  <h3>4. Candidate sites and reach</h3>
  <p>Candidates are the library outlets inside your circle. A site <em>reaches</em> a tract when the tract center is within the travel target of the site (same minutes-to-miles conversion) and the tract is inside your hospital circle. Reach outside the circle is not counted.</p>
  <h3>5. Greedy maximum coverage</h3>
  <pre>covered = ∅
repeat k times:
    pick site s maximizing Σ need_weight(t) for t in reach(s) \\ covered
    stop if the gain is zero
    covered ← covered ∪ reach(s)</pre>
  <p><code>k</code> is your number of clinic days (up to 10), one site per day. An estimated clinic day costs <code>staff × hours × $55 + $400</code>. Daily capacity is <code>staff × hours × services per staff hour</code>. Greedy selection is a standard approximation for maximum coverage and is guaranteed to reach at least 63% (1 − 1/e) of the best possible total. Ties go to the lower site ID so results are repeatable.</p>
  <h3>6. Scheduling</h3>
  <p>Chosen clinics are spread evenly across your date window on weekdays, with the largest-reach site first. Confirm dates with each host before announcing.</p>

  <h2 id="robust">Robustness</h2>
  <p>The 70/30 health-versus-transport split is a judgment call, so we rerun the selection for seven mixes from 20% to 80% health. The card for each clinic shows how many of those seven runs picked it. A clinic chosen in 7 of 7 does not depend on that choice.</p>

  <h2 id="limits">What this does not do</h2>
  <ul>
  <li><strong>It does not predict turnout.</strong> Reach counts adults who live near a site; staffing and outreach decide who attends. Daily capacity (staff × hours × services per staff hour) is shown separately and is usually far smaller than reach.</li>
  <li>Health indicators are modeled estimates of <em>diagnosed</em> prevalence and miss undiagnosed cases. The existing-care directory is not service-specific, so a nearby clinic may not offer the service you chose.</li>
  <li>Hospital radius and clinic reach are straight-line approximations converted from minutes at a fixed speed, not real drive times from your address. Existing-clinic access does use real road times.</li>
  <li>The 397 library outlets are a candidate list, not a checked inventory. Confirm host approval, space, power and accessibility.</li>
  <li>The $55 per staff hour and $400 per clinic day are placeholder cost assumptions, shown as an estimate only.</li>
  <li>No patient-level data is used or stored.</li>
  </ul>

  <h2 id="gemini">Where Gemini is used</h2>
  <p>Gemini only writes the plain-language explanation of a finished plan. It never supplies census numbers or chooses sites. Set <code>GEMINI_API_KEY</code> in <code>.env</code> to enable it; without a key the app uses a built-in summary.</p>
  </article>`;
  $('#docs-view').addEventListener('click', e => {
    const a = e.target.closest('[data-jump]');
    if (a) { e.preventDefault(); document.getElementById(a.dataset.jump)?.scrollIntoView({behavior: 'smooth'}); }
  });
}

// ---------- Boot ----------
async function boot() {
  const d = new Date(), iso = n => { const x = new Date(d); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); };
  form.date_start.value = iso(7); form.date_end.value = iso(35);
  updateHints(); route();
  try {
    const [opts, geo] = await Promise.all([api('/api/clinic-options'), fetch('/data/geography.json').then(r => r.json())]);
    options = opts;
    form.service.replaceChildren(...Object.entries(opts.services).map(([k, v]) => new Option(v, k)));
    const parts = [];
    for (const f of geo.features) {
      const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
      const d = polys.map(p => p.map(ringPath).join('')).join('');
      tractPaths.set(f.properties.id, d); parts.push(d);
    }
    landPath = parts.join('');
  } catch (err) { show('Could not load the North Carolina data: ' + err.message); }
}
boot();
