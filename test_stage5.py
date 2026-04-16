"""Dashboard locale stage 5 con grafo SVG interattivo verticale."""

from __future__ import annotations

import argparse
import json
import sqlite3
from html import escape
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_MINING_DIR = ROOT_DIR / "aggregated database" / "vaccini_mining"
ANAGRAFICHE_DB = ROOT_DIR / "aggregated database" / "anagrafiche_pazienti.sqlite"
ALLOWED_FILES = {
    "vaccini_sessioni_log.xes",
    "vaccini_sessioni_log.json",
    "vaccini_sessioni_log_summary.json",
    "vaccini_progressione_log.xes",
    "vaccini_progressione_log.json",
    "vaccini_progressione_log_summary.json",
    "vaccini_validation_report.json",
    "vaccini_sessioni_log_dfg.png",
    "vaccini_progressione_log_dfg.png",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dashboard locale per stage 5")
    parser.add_argument("--mining-dir", type=Path, default=DEFAULT_MINING_DIR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    return parser


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_for_script(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _render_stat_card(label: str, value: Any) -> str:
    return (
        '<div class="stat"><div class="label">'
        + escape(str(label))
        + '</div><div class="value">'
        + escape(str(value))
        + '</div></div>'
    )


def _render_links(mining_dir: Path) -> str:
    items = []
    for name in sorted(ALLOWED_FILES):
        if (mining_dir / name).exists():
            items.append(f'<li><a href="/files/{escape(name)}" target="_blank">{escape(name)}</a></li>')
    return "<ul>" + "".join(items) + "</ul>"


def _render_patient_options(patient_contexts: dict[str, dict[str, Any]]) -> str:
    options = ['<option value="__all__">Tutti i pazienti</option>']
    for patient_id in sorted(patient_contexts):
        patient = patient_contexts[patient_id]
        label = patient.get("nome_completo") or patient_id
        options.append(f'<option value="{escape(patient_id)}">{escape(label)} ({escape(patient_id)})</option>')
    return "".join(options)


def _render_transition_table(summary: dict[str, Any], limit: int = 12) -> str:
    rows = summary.get("transition_timing_days", [])[:limit]
    if not rows:
        return '<p class="empty">Nessuna transizione disponibile.</p>'
    body = "".join(
        f"<tr><td>{escape(row['from'])}</td><td>{escape(row['to'])}</td><td>{row['avg_gap_days']}</td><td>{row['count']}</td></tr>"
        for row in rows
    )
    return (
        '<table><thead><tr><th>Da</th><th>A</th><th>Gap medio (giorni)</th><th>Occorrenze</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _render_variants(summary: dict[str, Any], limit: int = 6) -> str:
    variants = summary.get("variants", [])[:limit]
    if not variants:
        return '<p class="empty">Nessuna variante.</p>'
    return "".join(
        '<div class="variant"><strong>'
        + str(item.get("count", 0))
        + ' case</strong><div>'
        + escape(" -> ".join(item.get("activities", [])))
        + '</div></div>'
        for item in variants
    )


def _load_patient_contexts(progression_log: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    patient_ids = sorted({item.get("patient_id") for item in progression_log if item.get("patient_id")})
    if not patient_ids or not ANAGRAFICHE_DB.exists():
        return {}
    conn = sqlite3.connect(ANAGRAFICHE_DB)
    conn.row_factory = sqlite3.Row
    try:
        contexts: dict[str, dict[str, Any]] = {}
        for patient_id in patient_ids:
            row = conn.execute(
                """
                SELECT codice_paziente, nome, cognome, nome_completo, codice_fiscale,
                       data_nascita, luogo_nascita, citta_residenza, indirizzo_residenza, data_rilevamento
                FROM anagrafiche_pazienti
                WHERE codice_paziente = ?
                ORDER BY data_rilevamento DESC
                LIMIT 1
                """,
                (patient_id,),
            ).fetchone()
            contexts[patient_id] = dict(row) if row else {"codice_paziente": patient_id}
        return contexts
    finally:
        conn.close()


def _render_html(mining_dir: Path) -> str:
    progression_summary = _load_json(mining_dir / "vaccini_progressione_log_summary.json")
    validation = _load_json(mining_dir / "vaccini_validation_report.json")
    session_log = _load_json(mining_dir / "vaccini_sessioni_log.json")
    progression_log = _load_json(mining_dir / "vaccini_progressione_log.json")
    patient_contexts = _load_patient_contexts(progression_log)

    template = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Value4Care Stage 5</title>
<style>
:root { --bg:#f3efe8; --paper:#fffaf2; --ink:#22303a; --muted:#6d736d; --line:#d7cdbd; --accent:#9f3d30; --blue:#274c5e; }
* { box-sizing:border-box; }
body { margin:0; background:linear-gradient(180deg,#f8f3eb 0%,#f0ebe2 100%); color:var(--ink); font-family:"Segoe UI",sans-serif; }
.shell { max-width:1650px; margin:0 auto; padding:28px; }
.hero,.grid,.graph-wrap { display:grid; gap:20px; }
.hero { grid-template-columns:1.2fr .8fr; }
.grid { grid-template-columns:1fr 1fr; margin-top:20px; }
.panel { background:var(--paper); border:1px solid var(--line); border-radius:22px; padding:22px; box-shadow:0 18px 40px rgba(35,29,20,.08); }
.full { grid-column:1/-1; }
.eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; color:var(--accent); font-weight:700; margin-bottom:10px; }
h1,h2,h3 { margin:0 0 10px; font-family:Georgia,serif; }
h1 { font-size:44px; line-height:1.03; }
p { color:var(--muted); line-height:1.5; margin:0; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:24px 0; }
.stat { background:var(--paper); border:1px solid var(--line); border-radius:18px; padding:16px; }
.label { font-size:11px; text-transform:uppercase; color:var(--muted); letter-spacing:.1em; margin-bottom:8px; }
.value { font-size:32px; font-weight:700; }
.graph-wrap { grid-template-columns:340px 1fr; align-items:start; margin-top:18px; }
.controls,.detail { border:1px solid var(--line); border-radius:18px; padding:14px; background:rgba(255,255,255,.55); }
.controls label,.controls select { display:block; width:100%; font-size:14px; }
.controls select { margin:8px 0 10px; padding:9px 10px; border-radius:12px; border:1px solid var(--line); background:white; }
.controls .option,.filter-item { display:flex; gap:8px; align-items:center; margin-top:8px; font-size:14px; }
.filter-list { max-height:240px; overflow:auto; margin-top:10px; padding-right:6px; }
.inline-actions { display:flex; gap:8px; margin-top:10px; }
button { padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:white; cursor:pointer; }
.detail { min-height:220px; margin-top:14px; }
#graph { width:100%; height:980px; display:block; border:1px solid var(--line); border-radius:20px; background:#fffdf9; cursor:grab; }
#graph.dragging { cursor:grabbing; }
table { width:100%; border-collapse:collapse; margin-top:10px; }
th,td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }
th { font-size:11px; text-transform:uppercase; color:var(--muted); }
.variant { border:1px solid var(--line); border-radius:16px; padding:12px 14px; margin-bottom:10px; background:rgba(255,255,255,.55); }
.empty { color:var(--muted); font-style:italic; }
summary { cursor:pointer; color:var(--blue); font-weight:600; }
pre { white-space:pre-wrap; word-break:break-word; font-size:12px; background:#f7f2ea; border:1px solid var(--line); border-radius:14px; padding:14px; }
.badges { margin-top:10px; }
.badge { display:inline-block; margin:0 6px 6px 0; padding:5px 8px; border-radius:999px; border:1px solid var(--line); background:#f4ecdf; font-size:12px; }
@media (max-width:1200px) { .hero,.grid,.graph-wrap,.stats { grid-template-columns:1fr; } #graph { height:760px; } }
</style>
</head>
<body>
<div class="shell">
  <section class="hero">
    <article class="panel">
      <div class="eyebrow">Stage 5 Locale</div>
      <h1>Vista verticale del percorso vaccinale</h1>
      <p>La progressione ora scorre dall'alto verso il basso nel tempo. Puoi nascondere i vaccini, cambiare l'ordine tra cronologico e alfabetico, e leggere la timeline in eta del soggetto.</p>
      <div class="badges">
        <span class="badge">Locale only</span>
        <span class="badge">SVG interattivo</span>
        <span class="badge">Nessuna dipendenza web esterna</span>
      </div>
    </article>
    <article class="panel">
      <div class="eyebrow">Contesto paziente</div>
      <h3 id="patient-name">Tutti i pazienti</h3>
      <p id="patient-meta">Vista aggregata locale del mining vaccinale.</p>
      <div class="eyebrow" style="margin-top:16px;">Artefatti</div>
      __LINKS__
    </article>
  </section>
  <section class="stats">
    __STAT_EVENTI_PROGRESSIONE__
    __STAT_ATTIVITA_PROGRESSIONE__
    __STAT_CASE_PROGRESSIONE__
    __STAT_WARNING__
  </section>
  <section class="grid">
    <article class="panel full">
      <div class="eyebrow">Grafo interattivo</div>
      <h2>Progressione verticale con selezione vaccini</h2>
      <p>Nel layout cronologico i vaccini sono ordinati in base alla prima comparsa nel tempo. Nel layout alfabetico li ordiniamo lessicalmente. La scala a sinistra mostra la data e, se disponibile, l'eta del soggetto.</p>
      <div class="graph-wrap">
        <div>
          <div class="controls">
            <label for="patient-mode">Paziente</label>
            <select id="patient-mode">__PATIENT_OPTIONS__</select>
            <label for="order-mode">Ordine colonne vaccini</label>
            <select id="order-mode">
              <option value="chronological">Cronologico</option>
              <option value="alphabetical">Alfabetico</option>
            </select>
            <label class="option"><input type="checkbox" id="show-bands" checked /> Mostra bande di sessione</label>
            <label class="option"><input type="checkbox" id="show-labels" checked /> Mostra i giorni sugli archi</label>
            <div class="inline-actions">
              <button type="button" id="select-all">Tutti</button>
              <button type="button" id="select-none">Nessuno</button>
            </div>
            <div class="eyebrow" style="margin-top:14px;">Vaccini visibili</div>
            <div id="vaccine-filters" class="filter-list"></div>
          </div>
          <div class="detail" id="detail-box"><h3>Dettaglio selezione</h3><p class="empty">Clicca un nodo o un arco per vedere i dettagli.</p></div>
        </div>
        <div>
          <div id="caption" class="eyebrow">Vista corrente: progressione per vaccino</div>
          <svg id="graph" viewBox="0 0 1600 980" aria-label="Grafo interattivo"></svg>
        </div>
      </div>
    </article>
    <article class="panel">
      <div class="eyebrow">Intervalli tra stazioni</div>
      <h2>Tempi tra una dose e la successiva</h2>
      <p>I passaggi qui sotto sono aggregati dalle transizioni osservate nel log progressione.</p>
      __TRANSITION_TABLE__
    </article>
    <article class="panel">
      <div class="eyebrow">Varianti</div>
      <h2>Prime varianti</h2>
      <p>Queste sequenze diventano piu interessanti appena aggiungiamo altri pazienti.</p>
      __VARIANTS__
    </article>
    <article class="panel full">
      <div class="eyebrow">Debug leggibile</div>
      <h2>JSON principali</h2>
      <details><summary>Summary progressione</summary><pre>__SUMMARY_PROGRESSIONE__</pre></details>
      <details><summary>Validation report</summary><pre>__VALIDATION_REPORT__</pre></details>
    </article>
  </section>
</div>
<script>
const PROGRESSION_SUMMARY = __PROGRESSION_SUMMARY__;
const VALIDATION = __VALIDATION__;
const SESSION_LOG = __SESSION_LOG__;
const PROGRESSION_LOG = __PROGRESSION_LOG__;
const PATIENT_CONTEXTS = __PATIENT_CONTEXTS__;
const svg = document.getElementById('graph');
const patientEl = document.getElementById('patient-mode');
const orderEl = document.getElementById('order-mode');
const bandsEl = document.getElementById('show-bands');
const labelsEl = document.getElementById('show-labels');
const detailBox = document.getElementById('detail-box');
const captionBox = document.getElementById('caption');
const filtersBox = document.getElementById('vaccine-filters');
const selectAllBtn = document.getElementById('select-all');
const selectNoneBtn = document.getElementById('select-none');
const allPatients = [...new Set(PROGRESSION_LOG.map((item) => item.patient_id).filter(Boolean))].sort();
const appState = { scale: 1, tx: 0, ty: 0, dragging: false, startX: 0, startY: 0, selectedVaccines: new Set(), selectedPatient: '__all__' };
function svgEl(name, attrs = {}, text) { const n = document.createElementNS('http://www.w3.org/2000/svg', name); Object.entries(attrs).forEach(([k,v]) => n.setAttribute(k, String(v))); if (text !== undefined) n.textContent = text; return n; }
function colorFor(text) { let h = 0; for (let i = 0; i < text.length; i += 1) h = ((h << 5) - h) + text.charCodeAt(i); return `hsl(${Math.abs(h)%360} 58% 72%)`; }
function asDate(value) { const d = new Date(value); return Number.isNaN(d.valueOf()) ? null : d; }
function daysBetween(a, b) { return Math.round((b - a) / 86400000); }
function setDetail(title, lines) { const body = lines.length ? '<ul>' + lines.map((line) => `<li>${line}</li>`).join('') + '</ul>' : '<p class="empty">Nessun dettaglio.</p>'; detailBox.innerHTML = `<h3>${title}</h3>${body}`; }
function clearGraph() { while (svg.firstChild) svg.removeChild(svg.firstChild); }
function viewport() { clearGraph(); const defs = svgEl('defs'); const marker = svgEl('marker', { id:'arrow', viewBox:'0 0 10 10', refX:9, refY:5, markerWidth:7, markerHeight:7, orient:'auto-start-reverse' }); marker.appendChild(svgEl('path', { d:'M 0 0 L 10 5 L 0 10 z', fill:'#6d6356' })); defs.appendChild(marker); svg.appendChild(defs); const root = svgEl('g', { id:'vp', transform:`translate(${appState.tx},${appState.ty}) scale(${appState.scale})` }); svg.appendChild(root); root.appendChild(svgEl('rect', { x:0, y:0, width:1600, height:980, fill:'#fffdf8' })); return root; }
function updateTransform() { const vp = document.getElementById('vp'); if (vp) vp.setAttribute('transform', `translate(${appState.tx},${appState.ty}) scale(${appState.scale})`); }
function currentPatientContext() {
  return appState.selectedPatient === '__all__' ? null : (PATIENT_CONTEXTS[appState.selectedPatient] || null);
}
function eventsForCurrentPatient() {
  return PROGRESSION_LOG.filter((item) => appState.selectedPatient === '__all__' || item.patient_id === appState.selectedPatient);
}
function vaccinesForCurrentPatient() {
  return [...new Set(eventsForCurrentPatient().map((item) => item.vaccine_type))];
}
function chronologicalVaccines(sourceEvents) {
  const grouped = new Map();
  sourceEvents.forEach((event) => {
    const key = event.vaccine_type;
    const dateObj = asDate(event['time:timestamp']);
    if (!grouped.has(key)) {
      grouped.set(key, {
        vaccine: key,
        firstDate: dateObj,
        lastDate: dateObj,
        firstDose: event.dose_number,
        lastDose: event.dose_number,
        timeline: [],
      });
    }
    const item = grouped.get(key);
    item.timeline.push({ dateObj, dose: event.dose_number });
    if (dateObj < item.firstDate || (dateObj.valueOf() === item.firstDate.valueOf() && event.dose_number < item.firstDose)) {
      item.firstDate = dateObj;
      item.firstDose = event.dose_number;
    }
    if (dateObj > item.lastDate || (dateObj.valueOf() === item.lastDate.valueOf() && event.dose_number > item.lastDose)) {
      item.lastDate = dateObj;
      item.lastDose = event.dose_number;
    }
  });
  const compareTimeline = (a, b) => {
    const aSteps = [...a.timeline].sort((x, y) => x.dateObj - y.dateObj || x.dose - y.dose);
    const bSteps = [...b.timeline].sort((x, y) => x.dateObj - y.dateObj || x.dose - y.dose);
    const length = Math.max(aSteps.length, bSteps.length);
    for (let i = 0; i < length; i += 1) {
      const left = aSteps[i];
      const right = bSteps[i];
      if (!left && right) return -1;
      if (left && !right) return 1;
      const byDate = left.dateObj - right.dateObj;
      if (byDate !== 0) return byDate;
      const byDose = left.dose - right.dose;
      if (byDose !== 0) return byDose;
    }
    return 0;
  };
  return [...grouped.values()]
    .sort((a, b) =>
      a.firstDate - b.firstDate ||
      a.lastDate - b.lastDate ||
      compareTimeline(a, b) ||
      a.firstDose - b.firstDose ||
      a.lastDose - b.lastDose ||
      a.vaccine.localeCompare(b.vaccine)
    )
    .map((item) => item.vaccine);
}
function orderedVaccines(sourceEvents) {
  const currentVaccines = vaccinesForCurrentPatient();
  const vaccines = orderEl.value === 'alphabetical' ? [...currentVaccines].sort() : chronologicalVaccines(sourceEvents);
  return vaccines.filter((item) => appState.selectedVaccines.has(item));
}
function renderFilterControls() {
  const sourceEvents = eventsForCurrentPatient();
  const currentVaccines = orderEl.value === 'alphabetical' ? [...vaccinesForCurrentPatient()].sort() : chronologicalVaccines(sourceEvents);
  filtersBox.innerHTML = currentVaccines.map((vaccine) => `<label class="filter-item"><input type="checkbox" data-vaccine="${vaccine}" ${appState.selectedVaccines.has(vaccine) ? 'checked' : ''} /> ${vaccine}</label>`).join('');
  filtersBox.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => { const vaccine = checkbox.dataset.vaccine; if (checkbox.checked) appState.selectedVaccines.add(vaccine); else appState.selectedVaccines.delete(vaccine); render(); });
  });
}
function updatePatientContext() {
  const patientNameBox = document.getElementById('patient-name');
  const patientMetaBox = document.getElementById('patient-meta');
  if (appState.selectedPatient === '__all__') {
    patientNameBox.textContent = 'Tutti i pazienti';
    patientMetaBox.textContent = `Vista aggregata locale del mining vaccinale (${allPatients.length} pazienti nel log).`;
    return;
  }
  const patient = currentPatientContext() || {};
  patientNameBox.textContent = patient.nome_completo || patient.codice_paziente || appState.selectedPatient;
  const bits = [];
  if (patient.codice_paziente) bits.push(`Codice: ${patient.codice_paziente}`);
  if (patient.data_nascita) bits.push(`Nato il ${patient.data_nascita}`);
  if (patient.citta_residenza) bits.push(`Residenza: ${patient.citta_residenza}`);
  patientMetaBox.textContent = bits.join(' | ') || 'Anagrafica non disponibile.';
}
function formatAgeDays(ageDays) {
  if (ageDays == null || Number.isNaN(ageDays)) return '-';
  const years = ageDays / 365.25;
  return `${years.toFixed(1)} anni`;
}
function buildProgression() {
  const patientContext = currentPatientContext();
  const sourceEvents = eventsForCurrentPatient();
  const baseEvents = sourceEvents
    .filter((item) => appState.selectedVaccines.has(item.vaccine_type))
    .map((item) => {
      const dateObj = asDate(item['time:timestamp']);
      const context = PATIENT_CONTEXTS[item.patient_id] || {};
      const birthDate = context.data_nascita ? asDate(context.data_nascita) : null;
      const ageDays = birthDate ? daysBetween(birthDate, dateObj) : null;
      return { ...item, dateObj, birthDate, ageDays };
    })
    .sort((a, b) => a.dateObj - b.dateObj || a.dose_number - b.dose_number || String(a.patient_id).localeCompare(String(b.patient_id)));

  const vaccines = orderedVaccines(sourceEvents);
  const columnMap = new Map(vaccines.map((name, idx) => [name, idx]));
  const cases = new Map();
  baseEvents.forEach((event) => {
    const key = event['case:concept:name'];
    if (!cases.has(key)) cases.set(key, []);
    cases.get(key).push(event);
  });
  cases.forEach((list) => list.sort((a, b) => a.dateObj - b.dateObj || a.dose_number - b.dose_number));

  const rawEdges = [];
  cases.forEach((list, caseId) => {
    for (let i = 0; i < list.length - 1; i += 1) {
      rawEdges.push({ caseId, from: list[i], to: list[i + 1], gapDays: daysBetween(list[i].dateObj, list[i + 1].dateObj) });
    }
  });

  const mean = (values) => values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
  const isAggregate = appState.selectedPatient === '__all__';

  if (isAggregate) {
    const aggregateEventMap = new Map();
    baseEvents.forEach((event) => {
      if (event.ageDays == null) return;
      const key = `${event.vaccine_type}::${event.dose_number}`;
      if (!aggregateEventMap.has(key)) {
        aggregateEventMap.set(key, {
          vaccine_type: event.vaccine_type,
          dose_number: event.dose_number,
          'concept:name': event['concept:name'],
          ageDaysList: [],
          patientIds: new Set(),
          notes: new Set(),
        });
      }
      const item = aggregateEventMap.get(key);
      item.ageDaysList.push(event.ageDays);
      item.patientIds.add(event.patient_id);
      if (event.note) item.notes.add(event.note);
    });

    const events = [...aggregateEventMap.values()]
      .map((item) => ({
        vaccine_type: item.vaccine_type,
        dose_number: item.dose_number,
        'concept:name': item['concept:name'],
        avgAgeDays: Math.round(mean(item.ageDaysList)),
        minAgeDays: Math.min(...item.ageDaysList),
        maxAgeDays: Math.max(...item.ageDaysList),
        patient_count: item.patientIds.size,
        note: [...item.notes].join(' | '),
      }))
      .sort((a, b) => a.avgAgeDays - b.avgAgeDays || a.dose_number - b.dose_number || a.vaccine_type.localeCompare(b.vaccine_type));

    const edgeMap = new Map();
    rawEdges.forEach((edge) => {
      if (edge.from.ageDays == null || edge.to.ageDays == null) return;
      const key = `${edge.from['concept:name']}||${edge.to['concept:name']}`;
      if (!edgeMap.has(key)) {
        edgeMap.set(key, {
          fromName: edge.from['concept:name'],
          toName: edge.to['concept:name'],
          fromVaccine: edge.from.vaccine_type,
          fromDose: edge.from.dose_number,
          toVaccine: edge.to.vaccine_type,
          toDose: edge.to.dose_number,
          fromAgeDays: [],
          toAgeDays: [],
          gapDays: [],
          caseIds: new Set(),
        });
      }
      const item = edgeMap.get(key);
      item.fromAgeDays.push(edge.from.ageDays);
      item.toAgeDays.push(edge.to.ageDays);
      item.gapDays.push(edge.gapDays);
      item.caseIds.add(edge.caseId);
    });

    const edges = [...edgeMap.values()].map((item) => ({
      from: item.fromName,
      to: item.toName,
      fromVaccine: item.fromVaccine,
      fromDose: item.fromDose,
      toVaccine: item.toVaccine,
      toDose: item.toDose,
      fromAvgAgeDays: Math.round(mean(item.fromAgeDays)),
      toAvgAgeDays: Math.round(mean(item.toAgeDays)),
      gapDays: Math.round(mean(item.gapDays)),
      minGapDays: Math.min(...item.gapDays),
      maxGapDays: Math.max(...item.gapDays),
      count: item.caseIds.size,
    }));

    const lastAgeDays = Math.max(365, ...events.map((item) => item.avgAgeDays));
    return {
      mode: 'aggregate',
      firstAgeDays: 0,
      lastAgeDays,
      vaccines,
      columnMap,
      events,
      edges,
      sessions: [],
      visiblePatients: allPatients,
    };
  }

  const birthDate = patientContext && patientContext.data_nascita ? asDate(patientContext.data_nascita) : null;
  const events = baseEvents.map((event) => ({ ...event, displayAgeDays: event.ageDays ?? 0 }));
  const sessions = SESSION_LOG
    .filter((item) => item.patient_id === appState.selectedPatient)
    .map((item) => {
      const dateObj = asDate(item['time:timestamp']);
      const ageDays = birthDate ? daysBetween(birthDate, dateObj) : null;
      return {
        ...item,
        dateObj,
        ageDays,
        vaccines: item.vaccines_list.split(' | ').filter((v) => appState.selectedVaccines.has(v)),
      };
    })
    .filter((item) => item.vaccines.length > 0 && item.ageDays != null)
    .sort((a, b) => a.ageDays - b.ageDays);

  const lastAgeDays = Math.max(365, ...events.map((item) => item.displayAgeDays || 0));
  return {
    mode: 'single',
    firstAgeDays: 0,
    lastAgeDays,
    vaccines,
    columnMap,
    events,
    edges: rawEdges,
    sessions,
    visiblePatients: [appState.selectedPatient],
  };
}
function drawAgeAxis(root, startAgeDays, endAgeDays, topY, bottomY, title = 'Eta') {
  if (startAgeDays == null || endAgeDays == null) return;
  const axisX = 110;
  const range = Math.max(1, endAgeDays - startAgeDays);
  const yForAge = (ageDays) => topY + (((ageDays - startAgeDays) / range) * (bottomY - topY));
  root.appendChild(svgEl('line', { x1:axisX, y1:topY, x2:axisX, y2:bottomY, stroke:'#6d6356', 'stroke-width':2 }));
  root.appendChild(svgEl('text', { x:axisX - 10, y:topY - 12, 'text-anchor':'end', 'font-size':12, fill:'#6d736d', 'font-weight':'700' }, title));
  const totalYears = Math.max(1, Math.ceil(endAgeDays / 365.25));
  const stepYears = totalYears > 40 ? 5 : totalYears > 20 ? 2 : 1;
  for (let year = 0; year <= totalYears; year += stepYears) {
    const ageDays = Math.round(year * 365.25);
    if (ageDays > endAgeDays) break;
    const y = yForAge(ageDays);
    root.appendChild(svgEl('line', { x1:axisX - 6, y1:y, x2:axisX + 6, y2:y, stroke:'#6d6356' }));
    root.appendChild(svgEl('text', { x:axisX - 12, y:y + 4, 'text-anchor':'end', 'font-size':11, fill:'#6d736d' }, `${year} anni`));
  }
}
function renderProgression() {
  captionBox.textContent = 'Vista corrente: progressione per vaccino';
  const model = buildProgression();
  const root = viewport();
  if (!model.events.length) { setDetail('Progressione', ['<strong>Filtro attivo:</strong> nessun vaccino selezionato.']); return; }
  const left = 220; const right = 1510; const top = 80; const bottom = 900;
  const columns = Math.max(1, model.vaccines.length - 1);
  const rangeAge = Math.max(1, model.lastAgeDays - model.firstAgeDays);
  const xBaseFor = (vaccine) => left + ((model.columnMap.get(vaccine) / Math.max(1, columns)) * (right - left));
  const yForAge = (ageDays) => top + (((ageDays - model.firstAgeDays) / rangeAge) * (bottom - top));
  drawAgeAxis(root, model.firstAgeDays, model.lastAgeDays, top, bottom, model.mode === 'aggregate' ? 'Eta media' : 'Eta');
  model.vaccines.forEach((vaccine) => {
    const x = xBaseFor(vaccine);
    root.appendChild(svgEl('line', { x1:x, y1:top - 18, x2:x, y2:bottom + 20, stroke:'#ece1d0', 'stroke-dasharray':'6 8' }));
    root.appendChild(svgEl('text', { x, y:42, 'text-anchor':'middle', 'font-size':13, fill:'#4b4b46', 'font-weight':'700' }, vaccine));
  });
  if (bandsEl.checked && model.mode === 'single') {
    model.sessions.forEach((session, idx) => {
      const y = yForAge(session.ageDays);
      root.appendChild(svgEl('rect', { x:left - 52, y:y - 16, width:(right - left) + 104, height:32, fill: idx % 2 === 0 ? 'rgba(39,76,94,.08)' : 'rgba(163,63,47,.08)', rx:16 }));
    });
  }
  model.edges.forEach((edge) => {
    const x1 = xBaseFor(model.mode === 'aggregate' ? edge.fromVaccine : edge.from.vaccine_type);
    const y1 = yForAge(model.mode === 'aggregate' ? edge.fromAvgAgeDays : edge.from.displayAgeDays) + 24;
    const x2 = xBaseFor(model.mode === 'aggregate' ? edge.toVaccine : edge.to.vaccine_type);
    const y2 = yForAge(model.mode === 'aggregate' ? edge.toAvgAgeDays : edge.to.displayAgeDays) - 24;
    const path = svgEl('path', { d:`M ${x1} ${y1} C ${x1} ${(y1+y2)/2}, ${x2} ${(y1+y2)/2}, ${x2} ${y2}`, fill:'none', stroke:'#6d6356', 'stroke-width':2.1, 'marker-end':'url(#arrow)' });
    path.style.cursor = 'pointer';
    if (model.mode === 'aggregate') {
      path.addEventListener('click', () => setDetail('Arco medio di progressione', [
        `<strong>Da:</strong> ${edge.from}`,
        `<strong>A:</strong> ${edge.to}`,
        `<strong>Gap medio:</strong> ${edge.gapDays} giorni`,
        `<strong>Range gap:</strong> ${edge.minGapDays} - ${edge.maxGapDays} giorni`,
        `<strong>Pazienti / case:</strong> ${edge.count}`,
      ]));
    } else {
      path.addEventListener('click', () => setDetail('Arco di progressione', [
        `<strong>Da:</strong> ${edge.from['concept:name']}`,
        `<strong>A:</strong> ${edge.to['concept:name']}`,
        `<strong>Gap:</strong> ${edge.gapDays} giorni`,
        `<strong>Sessioni:</strong> ${edge.from.sessione_id} -> ${edge.to.sessione_id}`,
      ]));
    }
    root.appendChild(path);
    if (labelsEl.checked && model.mode !== 'aggregate') root.appendChild(svgEl('text', { x:(x1 + x2) / 2 + 8, y:(y1 + y2) / 2, 'font-size':11, fill:'#7b5c3f', 'font-weight':'700' }, `${edge.gapDays}g`));
  });
  model.events.forEach((event) => {
    const x = xBaseFor(event.vaccine_type);
    const ageDays = model.mode === 'aggregate' ? event.avgAgeDays : event.displayAgeDays;
    const y = yForAge(ageDays);
    const group = svgEl('g', { transform:`translate(${x - 86},${y - 24})` });
    group.style.cursor = 'pointer';
    group.appendChild(svgEl('rect', { width:172, height:48, rx:12, fill:colorFor(event.vaccine_type), stroke:'#6d6356', 'stroke-width':1.2 }));
    group.appendChild(svgEl('text', { x:86, y:16, 'text-anchor':'middle', 'font-size':10.5, fill:'#45555d' }, formatAgeDays(ageDays)));
    group.appendChild(svgEl('text', { x:86, y:33, 'text-anchor':'middle', 'font-size':12.5, fill:'#1f2a2e', 'font-weight':'700' }, `Dose ${event.dose_number}`));
    if (model.mode === 'aggregate') {
      group.addEventListener('click', () => setDetail(event['concept:name'], [
        `<strong>Vista:</strong> media aggregata`,
        `<strong>Vaccino:</strong> ${event.vaccine_type}`,
        `<strong>Dose:</strong> ${event.dose_number}`,
        `<strong>Eta media:</strong> ${formatAgeDays(event.avgAgeDays)}`,
        `<strong>Range:</strong> ${formatAgeDays(event.minAgeDays)} - ${formatAgeDays(event.maxAgeDays)}`,
        `<strong>Pazienti:</strong> ${event.patient_count}`,
        event.note ? `<strong>Note aggregate:</strong> ${event.note}` : '<strong>Note aggregate:</strong> -',
      ]));
    } else {
      group.addEventListener('click', () => setDetail(event['concept:name'], [
        `<strong>Paziente:</strong> ${event.patient_id}`,
        `<strong>Vaccino:</strong> ${event.vaccine_type}`,
        `<strong>Eta:</strong> ${formatAgeDays(event.displayAgeDays)}`,
        `<strong>Data:</strong> ${event['time:timestamp']}`,
        `<strong>Dose:</strong> ${event.dose_number}`,
        `<strong>Sessione:</strong> ${event.sessione_id}`,
        event.note ? `<strong>Note:</strong> ${event.note}` : '<strong>Note:</strong> -',
      ]));
    }
    root.appendChild(group);
  });
  const orderLabel = orderEl.value === 'alphabetical' ? 'alfabetico' : 'cronologico per prima somministrazione, poi ultima e traiettoria';
  const axisLabel = model.mode === 'aggregate' ? 'eta media aggregata della coorte' : 'eta del paziente selezionato';
  const bandsLabel = model.mode === 'aggregate' ? 'disattivate in vista aggregata' : 'ogni banda orizzontale rappresenta una sessione vaccinale dello stesso giorno';
  setDetail('Vista progressione', [
    `<strong>Paziente attivo:</strong> ${appState.selectedPatient === '__all__' ? 'tutti' : appState.selectedPatient}`,
    `<strong>Asse verticale:</strong> ${axisLabel}.`,
    `<strong>Ordine colonne:</strong> ${orderLabel}.`,
    `<strong>Bande:</strong> ${bandsLabel}.`,
  ]);
}
function render() {
  updatePatientContext();
  renderFilterControls();
  appState.scale = 1; appState.tx = 0; appState.ty = 0;
  orderEl.disabled = false;
  bandsEl.disabled = appState.selectedPatient === '__all__';
  renderProgression();
  updateTransform();
}
svg.addEventListener('wheel', (event) => { event.preventDefault(); appState.scale = Math.min(4, Math.max(.45, appState.scale * (event.deltaY < 0 ? 1.08 : .92))); updateTransform(); });
svg.addEventListener('mousedown', (event) => { appState.dragging = true; appState.startX = event.clientX - appState.tx; appState.startY = event.clientY - appState.ty; svg.classList.add('dragging'); });
window.addEventListener('mouseup', () => { appState.dragging = false; svg.classList.remove('dragging'); });
window.addEventListener('mousemove', (event) => { if (!appState.dragging) return; appState.tx = event.clientX - appState.startX; appState.ty = event.clientY - appState.startY; updateTransform(); });
patientEl.addEventListener('change', () => { appState.selectedPatient = patientEl.value; appState.selectedVaccines = new Set(vaccinesForCurrentPatient()); render(); });
orderEl.addEventListener('change', render);
bandsEl.addEventListener('change', render);
labelsEl.addEventListener('change', render);
selectAllBtn.addEventListener('click', () => { appState.selectedVaccines = new Set(vaccinesForCurrentPatient()); render(); });
selectNoneBtn.addEventListener('click', () => { appState.selectedVaccines = new Set(); render(); });
appState.selectedVaccines = new Set(vaccinesForCurrentPatient());
updatePatientContext();
render();
</script>
</body>
</html>
"""

    return (
        template
        .replace("__PATIENT_OPTIONS__", _render_patient_options(patient_contexts))
        .replace("__LINKS__", _render_links(mining_dir))
                        .replace("__STAT_EVENTI_PROGRESSIONE__", _render_stat_card("Eventi progressione", progression_summary.get("event_count", 0)))
        .replace("__STAT_ATTIVITA_PROGRESSIONE__", _render_stat_card("Attivita distinte", progression_summary.get("activity_count", 0)))
        .replace("__STAT_CASE_PROGRESSIONE__", _render_stat_card("Case progressione", progression_summary.get("case_count", 0)))
        .replace("__STAT_WARNING__", _render_stat_card("Warning validazione", validation.get("issue_count", 0)))
        .replace("__TRANSITION_TABLE__", _render_transition_table(progression_summary))
        .replace("__VARIANTS__", _render_variants(progression_summary))
        .replace("__SUMMARY_PROGRESSIONE__", escape(json.dumps(progression_summary, ensure_ascii=False, indent=2)[:30000]))
                .replace("__VALIDATION_REPORT__", escape(json.dumps(validation, ensure_ascii=False, indent=2)[:20000]))
                .replace("__PROGRESSION_SUMMARY__", _json_for_script(progression_summary))
        .replace("__VALIDATION__", _json_for_script(validation))
        .replace("__SESSION_LOG__", _json_for_script(session_log))
        .replace("__PROGRESSION_LOG__", _json_for_script(progression_log))
        .replace("__PATIENT_CONTEXTS__", _json_for_script(patient_contexts))
    )


def build_app(mining_dir: Path) -> FastAPI:
    app = FastAPI(title="value4care-stage5-local")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        required = [
            mining_dir / "vaccini_progressione_log_summary.json",
            mining_dir / "vaccini_validation_report.json",
            mining_dir / "vaccini_sessioni_log.json",
            mining_dir / "vaccini_progressione_log.json",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            html = (
                "<h1>Output di stage 4 mancanti</h1>"
                "<p>Esegui prima <code>python test_stage4.py</code>.</p>"
                f"<pre>{escape(json.dumps(missing, ensure_ascii=False, indent=2))}</pre>"
            )
            return HTMLResponse(html, status_code=404)
        return HTMLResponse(_render_html(mining_dir))

    @app.get("/files/{filename}")
    def get_file(filename: str):
        if filename not in ALLOWED_FILES:
            raise HTTPException(status_code=404, detail="File non consentito")
        path = mining_dir / filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="File non trovato")
        return FileResponse(path)

    @app.get("/api/summary/{name}")
    def get_summary(name: str):
        mapping = {
            "progressione": mining_dir / "vaccini_progressione_log_summary.json",
            "validazione": mining_dir / "vaccini_validation_report.json",
        }
        path = mapping.get(name)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Summary non trovato")
        return JSONResponse(_load_json(path))

    return app


def main() -> int:
    args = _build_parser().parse_args()
    mining_dir = args.mining_dir.resolve()
    uvicorn.run(build_app(mining_dir), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

