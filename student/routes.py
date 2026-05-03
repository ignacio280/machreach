"""

Student routes — all /student/* API endpoints and pages for MachReach Student.



This module exposes a `register_student_routes(app, csrf, limiter)` function

that app.py calls to mount everything.

"""

from __future__ import annotations



import json

import logging

import re

from datetime import datetime, timedelta



from flask import jsonify, redirect, request, session, url_for, render_template_string, send_file

from markupsafe import Markup



log = logging.getLogger(__name__)


# Pop-up modal that fires the first time a logged-in student opens any
# page after a weekly or monthly leaderboard period closes. Shows their
# rank in every scope (global / country / university / major) plus any
# coin prizes they won. One popup per period, dismissed by acking the
# server. Injected into `_s_render` for every authenticated page.
_PERIOD_POPUP_HTML = """
<div id="mr-period-modal" class="mr-period-hidden" aria-hidden="true">
  <div class="mr-period-back"></div>
  <div class="mr-period-card" role="dialog" aria-modal="true">
    <div class="mr-period-head">
      <div class="mr-period-eyebrow" id="mr-period-eyebrow">Resultados semanales</div>
      <div class="mr-period-title" id="mr-period-title">Ranking de la semana pasada</div>
      <div class="mr-period-sub" id="mr-period-sub"></div>
    </div>
    <div class="mr-period-prize" id="mr-period-prize"></div>
    <div class="mr-period-grid" id="mr-period-grid"></div>
    <div class="mr-period-foot">
      <button id="mr-period-next" class="mr-period-btn primary" type="button">Perfecto, entendido</button>
    </div>
  </div>
</div>
<style>
  #mr-period-modal { position: fixed; inset: 0; z-index: 99990; display: block; }
  #mr-period-modal.mr-period-hidden { display: none; }
  #mr-period-modal .mr-period-back {
    position: absolute; inset: 0; background: rgba(8, 11, 24, .72);
    backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
  }
  #mr-period-modal .mr-period-card {
    position: relative; max-width: 520px; width: calc(100% - 32px);
    margin: 8vh auto 0; background: var(--card, #0f172a);
    color: var(--text, #f8fafc);
    border: 1px solid var(--border, rgba(255,255,255,.08));
    border-radius: 18px; overflow: hidden;
    box-shadow: 0 24px 80px rgba(0,0,0,.55);
    animation: mrPeriodIn .25s cubic-bezier(.2,.9,.3,1.4);
  }
  @keyframes mrPeriodIn {
    from { transform: translateY(20px) scale(.97); opacity: 0; }
    to   { transform: translateY(0) scale(1);     opacity: 1; }
  }
  #mr-period-modal .mr-period-head {
    padding: 22px 22px 14px; text-align: center;
    background: linear-gradient(135deg, rgba(99,102,241,.18), rgba(139,92,246,.18));
    border-bottom: 1px solid var(--border, rgba(255,255,255,.06));
  }
  #mr-period-modal .mr-period-eyebrow {
    font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
    color: #c7d2fe; font-weight: 700; margin-bottom: 4px;
  }
  #mr-period-modal .mr-period-title { font-size: 22px; font-weight: 800; margin: 0; }
  #mr-period-modal .mr-period-sub {
    font-size: 12px; color: var(--text-muted, #94a3b8); margin-top: 4px;
  }
  #mr-period-modal .mr-period-prize {
    margin: 16px 18px 0; padding: 14px 16px;
    background: linear-gradient(135deg, #f59e0b, #ef4444);
    color: #fff; border-radius: 12px;
    font-weight: 700; text-align: center;
    box-shadow: 0 8px 24px rgba(245,158,11,.3);
  }
  #mr-period-modal .mr-period-prize.empty { display: none; }
  #mr-period-modal .mr-period-prize .big { font-size: 28px; line-height: 1; margin: 4px 0 6px; }
  #mr-period-modal .mr-period-prize .small { font-size: 12px; opacity: .92; font-weight: 500; }
  #mr-period-modal .mr-period-grid {
    display: grid; grid-template-columns: repeat(2, 1fr);
    gap: 8px; padding: 16px 18px 8px;
  }
  #mr-period-modal .mr-period-cell {
    padding: 10px 12px;
    background: rgba(255,255,255,.04);
    border: 1px solid var(--border, rgba(255,255,255,.06));
    border-radius: 10px;
  }
  #mr-period-modal .mr-period-cell .lbl {
    font-size: 10px; letter-spacing: .08em; text-transform: uppercase;
    color: var(--text-muted, #94a3b8); font-weight: 700;
  }
  #mr-period-modal .mr-period-cell .rank {
    font-size: 22px; font-weight: 800; margin-top: 2px; font-variant-numeric: tabular-nums;
  }
  #mr-period-modal .mr-period-cell .meta {
    font-size: 11px; color: var(--text-muted, #94a3b8); margin-top: 2px;
  }
  #mr-period-modal .mr-period-cell.win { background: linear-gradient(135deg, rgba(245,158,11,.18), rgba(239,68,68,.18)); border-color: rgba(245,158,11,.4); }
  #mr-period-modal .mr-period-cell.win .rank { color: #fbbf24; }
  #mr-period-modal .mr-period-cell.unranked .rank { color: var(--text-muted, #94a3b8); font-size: 14px; font-weight: 600; }
  #mr-period-modal .mr-period-foot { padding: 8px 18px 18px; text-align: center; }
  #mr-period-modal .mr-period-btn {
    background: linear-gradient(135deg,#6366f1,#8b5cf6); color: #fff;
    border: none; padding: 10px 22px; border-radius: 10px;
    font-weight: 700; font-size: 14px; cursor: pointer;
    box-shadow: 0 4px 14px rgba(99,102,241,.4);
  }
  #mr-period-modal .mr-period-btn:hover { filter: brightness(1.05); }
  @media (max-width: 480px) {
    #mr-period-modal .mr-period-card { margin-top: 4vh; }
    #mr-period-modal .mr-period-grid { grid-template-columns: 1fr; }
  }
</style>
<script>
(function(){
  if (window.__mrPeriodInit) return;
  window.__mrPeriodInit = true;
  var modal = document.getElementById('mr-period-modal');
  if (!modal) return;
  var queue = [];
  var SCOPES = [
    { key: 'global',     label: 'Global' },
    { key: 'country',    label: 'Country' },
    { key: 'university', label: 'University' },
    { key: 'major',      label: 'Major' },
  ];
  function show(p){
    var kindLabel = p.period_kind === 'week' ? 'Weekly' : 'Monthly';
    document.getElementById('mr-period-eyebrow').textContent = kindLabel + ' results';
    document.getElementById('mr-period-title').textContent =
      p.period_kind === 'week'
        ? "Last week's leaderboard"
        : "Last month's leaderboard";
    document.getElementById('mr-period-sub').textContent =
      'Period ' + (p.period_key || '') + ' is now closed.';
    var prizeBox = document.getElementById('mr-period-prize');
    if (p.total_coins_won > 0) {
      prizeBox.classList.remove('empty');
      prizeBox.innerHTML =
        '<div class="small">\\u{1F389} You won</div>' +
        '<div class="big">+' + p.total_coins_won + ' coins</div>' +
        '<div class="small">Ya acreditado en tu billetera</div>';
    } else {
      prizeBox.classList.add('empty');
      prizeBox.innerHTML = '';
    }
    var grid = document.getElementById('mr-period-grid');
    grid.innerHTML = '';
    SCOPES.forEach(function(s){
      var data = (p.scopes || {})[s.key];
      var prize = (p.prizes_by_scope || {})[s.key];
      var cell = document.createElement('div');
      var rankHtml, metaHtml = '';
      var winClass = '';
      if (!data) {
        cell.className = 'mr-period-cell unranked';
        rankHtml = 'Not ranked';
        metaHtml = 'No XP this period';
      } else {
        if (prize) {
          winClass = ' win';
          metaHtml = data.xp + ' XP · +' + prize.coins + ' coins';
        } else {
          metaHtml = data.xp + ' XP · of ' + data.total_in_bucket + ' players';
        }
        rankHtml = '#' + data.rank;
      }
      cell.className = 'mr-period-cell' + winClass;
      cell.innerHTML =
        '<div class="lbl">' + s.label + '</div>' +
        '<div class="rank">' + rankHtml + '</div>' +
        '<div class="meta">' + metaHtml + '</div>';
      grid.appendChild(cell);
    });
    modal.classList.remove('mr-period-hidden');
    modal.setAttribute('aria-hidden', 'false');
  }
  function ack(p){
    fetch('/api/student/period/ack', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ period_kind: p.period_kind, period_key: p.period_key })
    }).catch(function(){});
  }
  function next(){
    if (!queue.length) {
      modal.classList.add('mr-period-hidden');
      modal.setAttribute('aria-hidden', 'true');
      return;
    }
    var p = queue.shift();
    ack(p);
    show(p);
  }
  document.getElementById('mr-period-next').addEventListener('click', next);
  fetch('/api/student/period/results', { credentials: 'same-origin' })
    .then(function(r){ return r.ok ? r.json() : { periods: [] }; })
    .then(function(data){
      queue = (data && data.periods) || [];
      if (queue.length) next();
    }).catch(function(){});
})();
</script>
"""



def _gpa_planilla_html(lang: str = "en") -> str:
    """Return the GPA planilla HTML, localized for `lang` ('en' | 'es').

    The source template mixes Spanish (most labels) with a few English
    summary headers. We translate either direction here so callers get
    a fully-localized page.
    """
    html = _GPA_PLANILLA_HTML_ES
    if lang == "es":
        # Translate the few English summary labels back to Spanish.
        for src, dst in [
            (">Promedio del semestre<",   ">Promedio del semestre<"),
            (">Créditos del semestre<",   ">Créditos del semestre<"),
            (">Promedio de carrera<",     ">Promedio de la carrera<"),
            (">Créditos de carrera<",     ">Créditos de la carrera<"),
        ]:
            html = html.replace(src, dst)
        return html
    EN_REPL = [
        ("📊 Planilla de Notas", "📊 Grade Sheet"),
        ("Calcula tus promedios por semestre y la nota mínima que necesitas para aprobar — basado en la planilla que circula en la PUC.",
         "Calculate your semester averages and the minimum grade you need to pass — based on the spreadsheet that circulates at PUC."),
        (">⬇ Export<",  ">⬇ Export<"),
        (">⬆ Import<",  ">⬆ Import<"),
        (">🗑 Reset<",   ">🗑 Reset<"),
        (">Promedio del semestre<", ">Promedio del semestre<"),
        (">Créditos del semestre<", ">Créditos del semestre<"),
        (">Promedio de carrera<",   ">Promedio de carrera<"),
        (">Créditos de carrera<",   ">Créditos de carrera<"),
        ("<b>Tips:</b> Notas en escala chilena (1.0 – 7.0; 4.0 = aprobado). Las ponderaciones (%) deben sumar 100. La <b>NMPA</b> es la nota mínima que necesitas en lo que te falta para aprobar el ramo. Todo se guarda automáticamente en tu navegador.",
         "<b>Tips:</b> Chilean grading scale (1.0 – 7.0; 4.0 = passing). Weights (%) must sum to 100. <b>NMPA</b> is the minimum grade you need on what's left to pass the course. Everything saves automatically in your browser."),
        ("defaultCourse('Curso 1')", "defaultCourse('Course 1')"),
        ("defaultCourse('Curso 2')", "defaultCourse('Course 2')"),
        ("'Curso ' + n",             "'Course ' + n"),
        ("name: 'Prueba 1'", "name: 'Test 1'"),
        ("name: 'Prueba 2'", "name: 'Test 2'"),
        ("name: 'Examen'",   "name: 'Exam'"),
        ("'<span class=\"pl-ok\">Curso completado</span>'", "'<span class=\"pl-ok\">Curso completado</span>'"),
        ("'<span class=\"pl-ok\">Ya aprobado ✓</span>'",     "'<span class=\"pl-ok\">Already passed ✓</span>'"),
        ("'<span class=\"pl-warn\">Imposible aprobar</span>'", "'<span class=\"pl-warn\">Imposible aprobar</span>'"),
        ("'Necesitas <strong>' + nm.toFixed(1) + '</strong> en lo que falta para aprobar'",
         "'You need <strong>' + nm.toFixed(1) + '</strong> on what\\'s left to pass'"),
        ("placeholder=\"Evaluación\"", "placeholder=\"Evaluation\""),
        ("placeholder=\"Nombre del ramo\"", "placeholder=\"Course name\""),
        ("<span class=\"label\">Créd:</span>", "<span class=\"label\">Cred:</span>"),
        ("title=\"Copiar a otro semestre\"", "title=\"Copy to another semester\""),
        ("title=\"Eliminar ramo\"", "title=\"Delete course\""),
        ("title=\"Eliminar\"", "title=\"Delete\""),
        ("<th>Evaluación</th>", "<th>Evaluation</th>"),
        (">Nota</th>", ">Grade</th>"),
        (">+ Agregar evaluación<", ">+ Add evaluation<"),
        ("Promedio: <span", "Average: <span"),
        ("'No tienes ramos en este semestre todavía.<br><br><button class=\"pl-btn primary\" onclick=\"plAddCourse()\">+ Agregar primer ramo</button>'",
         "'No courses in this semester yet.<br><br><button class=\"pl-btn primary\" onclick=\"plAddCourse()\">+ Add first course</button>'"),
        (">+ Agregar ramo<", ">+ Add course<"),
        (">+ Semestre<", ">+ Semester<"),
        ("title=\"Agregar otro semestre\"", "title=\"Add another semester\""),
        ("'¿Eliminar este ramo?'", "'Delete this course?'"),
        ("'Copiar \"' + (src.name || 'ramo') + '\" a qué semestre?\\n\\n'",
         "'Copy \"' + (src.name || 'course') + '\" to which semester?\\n\\n'"),
        ("'\\n\\nEscribe el número (1-' + data.sems.length + '):'",
         "'\\n\\nEnter the number (1-' + data.sems.length + '):'"),
        ("' ramos)'", "' courses)'"),
        ("'Número inválido.'", "'Invalid number.'"),
        ("'Ese es el mismo semestre.'", "'That is the same semester.'"),
        ("'Esto borrará TODA la planilla. ¿Continuar?'", "'This will erase the WHOLE sheet. Continue?'"),
        ("'planilla_notas.json'", "'grade_sheet.json'"),
        ("'Archivo inválido.'", "'Invalid file.'"),
    ]
    for src, dst in EN_REPL:
        html = html.replace(src, dst)
    return html


_GPA_PLANILLA_HTML_ES = r"""
<style>
  .pl-wrap { max-width: 1200px; margin: 0 auto; }
  .pl-h { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:18px; }
  .pl-h h1 { margin:0; font-size:30px; }
  .pl-actions { display:flex; gap:8px; flex-wrap:wrap; }
  .pl-btn { background:var(--card); border:1px solid var(--border); color:var(--text); padding:8px 14px; border-radius:8px; cursor:pointer; font-size:13px; font-weight:600; transition: all .15s; }
  .pl-btn:hover { background:var(--border-light); }
  .pl-btn.primary { background: linear-gradient(135deg,#6366f1,#8b5cf6); color:#fff; border:none; }
  .pl-summary { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:18px; }
  .pl-card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px 18px; }
  .pl-card .lbl { font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.08em; font-weight:700; }
  .pl-card .val { font-size:28px; font-weight:800; margin-top:4px; font-variant-numeric: tabular-nums; }
  .pl-tabs { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:18px; border-bottom:1px solid var(--border); padding-bottom:0; }
  .pl-tab { padding:10px 16px; background:transparent; border:none; cursor:pointer; color:var(--text-muted); font-size:14px; font-weight:600; border-bottom:3px solid transparent; transition:all .12s; border-radius:6px 6px 0 0; }
  .pl-tab:hover { color:var(--text); background:var(--border-light); }
  .pl-tab.active { color:var(--primary); border-bottom-color:var(--primary); background:transparent; }
  .pl-grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:18px; }
  @media (max-width: 820px) { .pl-grid { grid-template-columns: 1fr; } }
  .pl-course { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
  .pl-course-h { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
  .pl-course-h input.cname { flex:1; min-width:0; background:transparent; border:none; color:var(--text); font-size:15px; font-weight:700; padding:4px 6px; border-bottom:2px solid transparent; outline:none; }
  .pl-course-h input.cname:focus { border-bottom-color: var(--primary); }
  .pl-course-h input.ccred { width:60px; background:var(--bg); border:1px solid var(--border); color:var(--text); font-size:13px; padding:4px 6px; border-radius:6px; text-align:center; }
  .pl-course-h .label { font-size:11px; color:var(--text-muted); }
  .pl-course-h .del { background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:16px; padding:4px 8px; border-radius:6px; }
  .pl-course-h .del:hover { background:rgba(239,68,68,.12); color:#ef4444; }
  .pl-course-h .copy { background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:16px; padding:4px 8px; border-radius:6px; }
  .pl-course-h .copy:hover { background:rgba(56,189,248,.12); color:#0ea5e9; }
  .pl-evals { width:100%; border-collapse:collapse; font-size:13px; }
  .pl-evals th { text-align:left; padding:6px 4px; font-size:11px; color:var(--text-muted); font-weight:600; text-transform:uppercase; letter-spacing:.04em; border-bottom:1px solid var(--border); }
  .pl-evals td { padding:4px 4px; }
  .pl-evals input { width:100%; background:transparent; border:1px solid transparent; border-radius:5px; color:var(--text); padding:5px 7px; font-size:13px; box-sizing:border-box; outline:none; }
  .pl-evals input:hover { background:var(--bg); }
  .pl-evals input:focus { background:var(--bg); border-color:var(--primary); }
  .pl-evals input.w-name { text-align:left; }
  .pl-evals input.w-pct, .pl-evals input.w-grade { text-align:center; font-variant-numeric: tabular-nums; }
  .pl-evals .delrow { background:none; border:none; color:var(--text-muted); cursor:pointer; padding:2px 6px; border-radius:4px; }
  .pl-evals .delrow:hover { background:rgba(239,68,68,.12); color:#ef4444; }
  .pl-add-eval { background:transparent; border:1px dashed var(--border); color:var(--text-muted); padding:6px 10px; border-radius:6px; cursor:pointer; font-size:12px; margin-top:6px; width:100%; }
  .pl-add-eval:hover { border-color:var(--primary); color:var(--primary); }
  .pl-foot { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; padding-top:10px; margin-top:10px; border-top:1px dashed var(--border); font-size:13px; }
  .pl-avg { font-weight:700; }
  .pl-avg .num { font-size:20px; font-variant-numeric: tabular-nums; }
  .pl-avg .num.pass { color:#10b981; }
  .pl-avg .num.fail { color:#ef4444; }
  .pl-avg .num.pending { color:var(--text-muted); }
  .pl-nmpa { color:var(--text-muted); font-size:12px; }
  .pl-nmpa strong { color:var(--text); }
  .pl-warn { color:#ef4444; font-size:11px; padding:2px 6px; background:rgba(239,68,68,.1); border-radius:4px; margin-left:6px; }
  .pl-ok { color:#10b981; font-size:11px; padding:2px 6px; background:rgba(16,185,129,.1); border-radius:4px; margin-left:6px; }
  .pl-add-course { display:flex; align-items:center; justify-content:center; min-height:140px; background:transparent; border:2px dashed var(--border); border-radius:12px; cursor:pointer; color:var(--text-muted); font-size:14px; font-weight:600; transition:all .12s; }
  .pl-add-course:hover { border-color:var(--primary); color:var(--primary); background:rgba(99,102,241,.04); }
  .pl-help { font-size:12px; color:var(--text-muted); margin-top:18px; padding:12px 14px; background:var(--card); border-radius:8px; border-left:3px solid var(--primary); }
  .pl-help b { color:var(--text); }
  .pl-empty { padding:40px; text-align:center; color:var(--text-muted); background:var(--card); border-radius:12px; border:1px dashed var(--border); }
</style>

<div class="pl-wrap">
  <div class="pl-h">
    <div>
      <h1>📊 Planilla de Notas</h1>
      <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Calcula tus promedios por semestre y la nota mínima que necesitas para aprobar — basado en la planilla que circula en la PUC.</p>
    </div>
    <div class="pl-actions">
      <button class="pl-btn" onclick="plExport()">⬇ Export</button>
      <button class="pl-btn" onclick="document.getElementById('pl-import').click()">⬆ Import</button>
      <input id="pl-import" type="file" accept="application/json" style="display:none" onchange="plImport(this.files[0])">
      <button class="pl-btn" onclick="plReset()">🗑 Reset</button>
    </div>
  </div>

  <div class="pl-summary">
    <div class="pl-card"><div class="lbl">Promedio del semestre</div><div class="val" id="pl-sem-avg">–</div></div>
    <div class="pl-card"><div class="lbl">Créditos del semestre</div><div class="val" id="pl-sem-cred">0</div></div>
    <div class="pl-card"><div class="lbl">Promedio de carrera</div><div class="val" id="pl-car-avg">–</div></div>
    <div class="pl-card"><div class="lbl">Créditos de carrera</div><div class="val" id="pl-car-cred">0</div></div>
  </div>

  <div class="pl-tabs" id="pl-tabs"></div>

  <div id="pl-body"></div>

  <div class="pl-help">
    <b>Tips:</b> Notas en escala chilena (1.0 – 7.0; 4.0 = aprobado). Las ponderaciones (%) deben sumar 100. La <b>NMPA</b> es la nota mínima que necesitas en lo que te falta para aprobar el ramo. Todo se guarda automáticamente en tu navegador.
  </div>
</div>

<script>
(function(){
  var CID = '__CID__';
  var KEY = 'mr_planilla_v1_' + CID;
  var SEM_LABELS = ['I','II','III','IV','V','VI','VII','VIII','IX','X'];
  var PASS = 4.0;        // Chilean passing grade
  var ROUND_PASS = 3.95; // weighted-average threshold that rounds to 4.0

  function uid(){ return 'e' + Math.random().toString(36).slice(2, 9); }

  function defaultData(){
    var sems = SEM_LABELS.map(function(lbl, i){
      return {
        label: lbl,
        active: i === 0,
        courses: i === 0 ? [defaultCourse('Curso 1'), defaultCourse('Curso 2')] : []
      };
    });
    sems[0].active = true;
    return { current: 0, sems: sems };
  }

  function defaultCourse(name){
    return {
      id: uid(), name: name, credits: 10,
      evals: [
        { id: uid(), name: 'Prueba 1',  pct: 30, grade: '' },
        { id: uid(), name: 'Prueba 2',  pct: 30, grade: '' },
        { id: uid(), name: 'Examen',    pct: 40, grade: '' }
      ]
    };
  }

  var data;
  try { data = JSON.parse(localStorage.getItem(KEY)) || defaultData(); }
  catch(e){ data = defaultData(); }
  if (!data || !data.sems) data = defaultData();

  function save(){ try { localStorage.setItem(KEY, JSON.stringify(data)); } catch(e){} }

  // Pull courses + exams from the server and merge into the localStorage
  // structure. Server data is the authoritative source for course names
  // and evaluation weights; the user's grade entries (typed numbers) stay
  // untouched. Each (semester_label, course_name) lands in the matching
  // slot — or appended to the last semester if the label isn't a tab yet.
  function findSemesterByLabel(label){
    if (!label) return -1;
    for (var i = 0; i < data.sems.length; i++){
      if ((data.sems[i].label || '') === label) return i;
    }
    return -1;
  }
  function ensureSemesterSlot(label){
    var idx = findSemesterByLabel(label);
    if (idx >= 0) return idx;
    data.sems.push({ label: label, active: false, courses: [] });
    return data.sems.length - 1;
  }
  function findCourseInSem(sem, name){
    var n = (name || '').trim().toLowerCase();
    if (!n) return -1;
    for (var i = 0; i < sem.courses.length; i++){
      if (((sem.courses[i].name || '').trim().toLowerCase()) === n) return i;
    }
    return -1;
  }
  function mergeServerCourses(payload){
    if (!payload || !payload.semesters) return;
    var sems = payload.semesters;
    Object.keys(sems).forEach(function(label){
      // '_unassigned' is the server's bucket for courses without a semester
      // tag — drop them onto whatever the user marked as current_semester
      // (or the active tab as a last resort).
      var targetLabel = (label === '_unassigned') ? (payload.current || data.sems[data.current].label) : label;
      var sIdx = ensureSemesterSlot(targetLabel);
      var sem = data.sems[sIdx];
      (sems[label] || []).forEach(function(srv){
        var cIdx = findCourseInSem(sem, srv.name);
        if (cIdx === -1){
          // Brand-new course → seed it with the server-side evaluations.
          var evs = (srv.exams || []).map(function(e){
            return { id: uid(), name: e.name || '', pct: e.weight_pct || '', grade: '' };
          });
          if (!evs.length){ evs.push({ id: uid(), name: 'Prueba 1', pct: 30, grade: '' }); }
          sem.courses.push({ id: uid(), name: srv.name || 'Curso', credits: 10, evals: evs });
          return;
        }
        // Existing course — patch in any server evaluations the user
        // doesn't already have (matched by case-insensitive name).
        var c = sem.courses[cIdx];
        (srv.exams || []).forEach(function(e){
          var en = (e.name || '').trim().toLowerCase();
          if (!en) return;
          var has = c.evals.some(function(ev){ return ((ev.name || '').trim().toLowerCase()) === en; });
          if (!has){
            c.evals.push({ id: uid(), name: e.name || '', pct: e.weight_pct || '', grade: '' });
          }
        });
      });
    });
    save();
  }
  fetch('/api/student/courses/by-semester', { credentials: 'same-origin' })
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(d){ if (d) { mergeServerCourses(d); rerender(); } })
    .catch(function(){ /* offline — localStorage data still works */ });

  function num(v){ var n = parseFloat(String(v).replace(',', '.')); return isFinite(n) ? n : NaN; }

  // Returns { avg, weightDone, weightedSum, hasAny, ok, allDone }
  function courseStats(c){
    var weightDone = 0, weightedSum = 0, totalWeight = 0, hasAny = false;
    c.evals.forEach(function(e){
      var p = num(e.pct);
      var g = num(e.grade);
      if (isFinite(p)) totalWeight += p;
      if (isFinite(p) && isFinite(g) && g > 0) {
        weightDone += p;
        weightedSum += (p / 100) * g;
        hasAny = true;
      }
    });
    var partial = weightDone > 0 ? (weightedSum * 100 / weightDone) : NaN;
    var allDone = (weightDone >= 99.5);
    return {
      hasAny: hasAny,
      weightDone: weightDone,
      weightedSum: weightedSum,
      totalWeight: totalWeight,
      partial: partial,
      allDone: allDone,
      okPct: Math.abs(totalWeight - 100) < 0.5
    };
  }

  function nmpa(c){
    var s = courseStats(c);
    if (s.allDone) return null;            // already graded
    var remaining = 100 - s.weightDone;
    if (remaining < 0.5) return null;
    // Need weightedSum (decimal scale) >= ROUND_PASS overall to pass
    // weightedSum_remaining_grade = (ROUND_PASS - weightedSum) / (remaining/100)
    var needed = (ROUND_PASS - s.weightedSum) / (remaining / 100);
    return needed;
  }

  function semStats(sem){
    var totalWeighted = 0, totalCred = 0, anyDone = false;
    sem.courses.forEach(function(c){
      var s = courseStats(c);
      var cr = num(c.credits) || 0;
      if (s.hasAny && cr > 0) {
        totalWeighted += s.partial * cr;
        totalCred += cr;
        anyDone = true;
      }
    });
    return { avg: anyDone ? (totalWeighted / totalCred) : null, credits: totalCred };
  }

  function careerStats(){
    var totalWeighted = 0, totalCred = 0, anyDone = false;
    data.sems.forEach(function(sem){
      sem.courses.forEach(function(c){
        var s = courseStats(c);
        var cr = num(c.credits) || 0;
        if (s.hasAny && cr > 0) {
          totalWeighted += s.partial * cr;
          totalCred += cr;
          anyDone = true;
        }
      });
    });
    return { avg: anyDone ? (totalWeighted / totalCred) : null, credits: totalCred };
  }

  function fmt(n){ return (n == null || !isFinite(n)) ? '–' : n.toFixed(2); }

  function escapeHtml(s){
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }

  function renderTabs(){
    var t = document.getElementById('pl-tabs');
    var tabs = data.sems.map(function(s, i){
      var hasAny = s.courses.some(function(c){ return courseStats(c).hasAny; });
      var dot = hasAny ? ' •' : '';
      return '<button class="pl-tab' + (i === data.current ? ' active' : '') +
             '" onclick="plSwitchSem(' + i + ')">Sem ' + s.label + dot + '</button>';
    }).join('');
    var addBtn = '<button class="pl-tab" style="opacity:.85;" onclick="plAddSemester()" title="Agregar otro semestre">+ Semestre</button>';
    t.innerHTML = tabs + addBtn;
  }

  function renderCourseCard(c, sIdx, cIdx){
    var st = courseStats(c);
    var avgClass = 'pending';
    if (st.hasAny) avgClass = (st.partial >= PASS) ? 'pass' : 'fail';
    var avgTxt = st.hasAny ? fmt(st.partial) : '–';
    var pctWarn = st.okPct ? '' :
      (st.totalWeight > 100 ? '<span class="pl-warn">Σ% > 100</span>' :
       st.totalWeight < 100 ? '<span class="pl-warn">Σ% &lt; 100 (' + st.totalWeight.toFixed(0) + '%)</span>' : '');

    var nm = nmpa(c);
    var nmpaTxt;
    if (st.allDone) nmpaTxt = '<span class="pl-ok">Curso completado</span>';
    else if (nm == null) nmpaTxt = '';
    else if (nm <= 1.0) nmpaTxt = '<span class="pl-ok">Ya aprobado ✓</span>';
    else if (nm > 7.0) nmpaTxt = '<span class="pl-warn">Imposible aprobar</span>';
    else nmpaTxt = 'Necesitas <strong>' + nm.toFixed(1) + '</strong> en lo que falta para aprobar';

    var rows = c.evals.map(function(e, eIdx){
      return '<tr>' +
        '<td><input class="w-name" placeholder="Evaluación" value="' + escapeHtml(e.name) + '" oninput="plSetEval(' + sIdx + ',' + cIdx + ',' + eIdx + ',\'name\',this.value)"></td>' +
        '<td style="width:70px;"><input class="w-pct" inputmode="decimal" placeholder="%" value="' + escapeHtml(e.pct) + '" oninput="plSetEval(' + sIdx + ',' + cIdx + ',' + eIdx + ',\'pct\',this.value)"></td>' +
        '<td style="width:70px;"><input class="w-grade" inputmode="decimal" placeholder="—" value="' + escapeHtml(e.grade) + '" oninput="plSetEval(' + sIdx + ',' + cIdx + ',' + eIdx + ',\'grade\',this.value)"></td>' +
        '<td style="width:32px;"><button class="delrow" title="Eliminar" onclick="plDelEval(' + sIdx + ',' + cIdx + ',' + eIdx + ')">✕</button></td>' +
      '</tr>';
    }).join('');

    return '<div class="pl-course">' +
      '<div class="pl-course-h">' +
        '<input class="cname" value="' + escapeHtml(c.name) + '" placeholder="Nombre del ramo" oninput="plSetCourse(' + sIdx + ',' + cIdx + ',\'name\',this.value)">' +
        '<span class="label">Créd:</span>' +
        '<input class="ccred" inputmode="numeric" value="' + escapeHtml(c.credits) + '" oninput="plSetCourse(' + sIdx + ',' + cIdx + ',\'credits\',this.value)">' +
        '<button class="copy" title="Copiar a otro semestre" onclick="plCopyCourse(' + sIdx + ',' + cIdx + ')">⧉</button>' +
        '<button class="del" title="Eliminar ramo" onclick="plDelCourse(' + sIdx + ',' + cIdx + ')">🗑</button>' +
      '</div>' +
      '<table class="pl-evals"><thead><tr>' +
        '<th>Evaluación</th><th style="width:70px;text-align:center;">%</th><th style="width:70px;text-align:center;">Nota</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' +
      '<button class="pl-add-eval" onclick="plAddEval(' + sIdx + ',' + cIdx + ')">+ Agregar evaluación</button>' +
      '<div class="pl-foot">' +
        '<div class="pl-avg">Promedio: <span class="num ' + avgClass + '">' + avgTxt + '</span>' + pctWarn + '</div>' +
        '<div class="pl-nmpa">' + nmpaTxt + '</div>' +
      '</div>' +
    '</div>';
  }

  function renderBody(){
    var sIdx = data.current;
    var sem = data.sems[sIdx];
    var body = document.getElementById('pl-body');
    if (!sem.courses.length) {
      body.innerHTML = '<div class="pl-empty">No tienes ramos en este semestre todavía.<br><br><button class="pl-btn primary" onclick="plAddCourse()">+ Agregar primer ramo</button></div>';
    } else {
      var grid = sem.courses.map(function(c, cIdx){ return renderCourseCard(c, sIdx, cIdx); }).join('');
      grid += '<button class="pl-add-course" onclick="plAddCourse()">+ Agregar ramo</button>';
      body.innerHTML = '<div class="pl-grid">' + grid + '</div>';
    }
    var ss = semStats(sem);
    var cs = careerStats();
    document.getElementById('pl-sem-avg').textContent = fmt(ss.avg);
    document.getElementById('pl-sem-cred').textContent = ss.credits || 0;
    document.getElementById('pl-car-avg').textContent = fmt(cs.avg);
    document.getElementById('pl-car-cred').textContent = cs.credits || 0;
  }

  function rerender(){
    // Preserve focus + cursor across full re-render so typing in any input
    // (especially course name on the GPA page) doesn't get interrupted.
    var act = document.activeElement;
    var snap = null;
    if (act && (act.tagName === 'INPUT' || act.tagName === 'TEXTAREA') && act.closest('.pl-wrap')) {
      var path = [];
      var el = act;
      while (el && el !== document.body) {
        var p = el.parentNode;
        if (!p) break;
        var idx = Array.prototype.indexOf.call(p.children, el);
        path.unshift(el.tagName.toLowerCase() + ':nth-child(' + (idx + 1) + ')');
        el = p;
      }
      snap = {
        sel: path.join(' > '),
        start: act.selectionStart,
        end: act.selectionEnd
      };
    }
    renderTabs(); renderBody(); save();
    if (snap) {
      try {
        var restored = document.querySelector(snap.sel);
        if (restored) {
          restored.focus();
          if (snap.start != null && restored.setSelectionRange) {
            try { restored.setSelectionRange(snap.start, snap.end); } catch(e){}
          }
        }
      } catch(e){}
    }
  }

  // ── Public actions (window-scoped so inline handlers work) ─────────
  window.plSwitchSem = function(i){ data.current = i; rerender(); };
  window.plSetCourse = function(s, c, key, val){ data.sems[s].courses[c][key] = val; rerender(); };
  window.plSetEval = function(s, c, e, key, val){ data.sems[s].courses[c].evals[e][key] = val; rerender(); };
  window.plAddEval = function(s, c){
    data.sems[s].courses[c].evals.push({ id: uid(), name: '', pct: '', grade: '' });
    rerender();
  };
  window.plDelEval = function(s, c, e){
    data.sems[s].courses[c].evals.splice(e, 1);
    rerender();
  };
  window.plAddCourse = function(){
    var s = data.current;
    var n = data.sems[s].courses.length + 1;
    data.sems[s].courses.push(defaultCourse('Curso ' + n));
    rerender();
  };
  window.plDelCourse = function(s, c){
    if (!confirm('¿Eliminar este ramo?')) return;
    data.sems[s].courses.splice(c, 1);
    rerender();
  };
  // Deep-clone a course (with fresh ids so edits don't bleed across semesters)
  // and append it to the chosen target semester.
  window.plCopyCourse = function(s, c){
    var src = data.sems[s] && data.sems[s].courses[c];
    if (!src) return;
    // Build a numbered list of all semesters except the source so the user
    // can pick where to paste.
    var opts = [];
    data.sems.forEach(function(sm, i){
      if (i === s) return;
      opts.push((i + 1) + ') Sem ' + sm.label + ' (' + sm.courses.length + ' ramos)');
    });
    var prompt_msg = 'Copiar "' + (src.name || 'ramo') + '" a qué semestre?\n\n' +
                     opts.join('\n') +
                     '\n\nEscribe el número (1-' + data.sems.length + '):';
    var raw = window.prompt(prompt_msg, String(s + 2 > data.sems.length ? 1 : s + 2));
    if (raw == null) return;
    var n = parseInt(raw, 10);
    if (!isFinite(n) || n < 1 || n > data.sems.length) {
      alert('Número inválido.');
      return;
    }
    var targetIdx = n - 1;
    if (targetIdx === s) {
      alert('Ese es el mismo semestre.');
      return;
    }
    var copy = JSON.parse(JSON.stringify(src));
    copy.id = uid();
    if (Array.isArray(copy.evals)) {
      copy.evals.forEach(function(e){ e.id = uid(); e.grade = ''; });
    }
    data.sems[targetIdx].courses.push(copy);
    rerender();
  };
  window.plAddSemester = function(){
    // Roman numeral for the next slot. Falls back to plain count if user
    // somehow gets past 30 semesters (unlikely on a 5-year career).
    var ROMAN = ['I','II','III','IV','V','VI','VII','VIII','IX','X',
                 'XI','XII','XIII','XIV','XV','XVI','XVII','XVIII','XIX','XX',
                 'XXI','XXII','XXIII','XXIV','XXV','XXVI','XXVII','XXVIII','XXIX','XXX'];
    var n = data.sems.length;
    var label = ROMAN[n] || String(n + 1);
    data.sems.push({ label: label, active: false, courses: [] });
    data.current = data.sems.length - 1;
    rerender();
  };
  window.plReset = function(){
    if (!confirm('Esto borrará TODA la planilla. ¿Continuar?')) return;
    data = defaultData();
    rerender();
  };
  window.plExport = function(){
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'planilla_notas.json';
    a.click();
    URL.revokeObjectURL(url);
  };
  window.plImport = function(file){
    if (!file) return;
    var fr = new FileReader();
    fr.onload = function(){
      try {
        var parsed = JSON.parse(fr.result);
        if (!parsed || !parsed.sems) throw new Error('Invalid file');
        data = parsed;
        if (typeof data.current !== 'number') data.current = 0;
        rerender();
      } catch(e){ alert('Archivo inválido.'); }
    };
    fr.readAsText(file);
  };

  rerender();
})();
</script>
"""




def register_student_routes(app, csrf, limiter):

    """Register all student routes on the Flask app."""



    # Import here to avoid circular imports at module level

    from student.canvas import CanvasClient, extract_text_from_pdf, extract_text_from_docx, normalize_canvas_url

    from student.analyzer import (analyze_course_material, generate_study_plan,

                                  generate_flashcards, generate_quiz, generate_notes,

                                  notes_from_transcript,

                                  flashcards_from_transcript,

                                  generate_practice_problems,

                                  analyze_essay, generate_cram_plan)

    from student import db as sdb
    from student.removed_features import DEPRECATED_STUDENT_PATHS, REMOVED_API_PREFIXES



    # ── helpers ─────────────────────────────────────────────

    def _esc(s) -> str:

        """HTML-escape a string."""

        import html as html_module

        return html_module.escape(str(s)) if s else ""



    def _logged_in() -> bool:

        return "client_id" in session



    def _cid() -> int:

        return session["client_id"]



    def _get_canvas(client_id: int) -> CanvasClient | None:

        tok = sdb.get_canvas_token(client_id)

        if not tok:

            return None

        return CanvasClient(tok["canvas_url"], tok["token"])



    # ── Deprecated pages: redirect away ─────────────────────

    # The Plan, Notes, AI Tutor, Schedule, and Panic features have been removed.

    # Any old links / bookmarks redirect back to the dashboard.

    def _deprecated_page():

        if not _logged_in():

            return redirect(url_for("login"))

        return redirect(url_for("student_dashboard_page"))

    for _path, _name in (

        ("/student/plan-removed",     "student_plan_removed"),

        ("/student/notes-removed",    "student_notes_removed"),

        ("/student/chat-removed",     "student_chat_removed"),

        ("/student/schedule-removed", "student_schedule_removed"),

        ("/student/panic-removed",    "student_panic_removed"),

    ):

        # We register placeholder routes; the real /student/plan etc. routes

        # below are kept registered too — they'll just no longer be linked from

        # the nav. (Removing them outright would break templates that still

        # reference url_for.)

        pass



    # ── Analytics page ──────────────────────────────────────



    @app.route("/student/analytics")

    def student_analytics_page():
        return _student_analytics_page_legacy()

    def _student_analytics_page_legacy():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        focus = sdb.get_focus_stats(cid) or {}

        courses = sdb.get_courses(cid) or []



        # Build {normalized course name -> display name} so we can match the

        # `notes` field which has the format "{mode}: {course_name}".

        def _norm_course(s: str) -> str:

            return (s or "").strip().lower()



        course_lookup = {_norm_course(c.get("name", "")): c.get("name", "Course") for c in courses}



        per_course_map: dict[str, int] = {}

        hour_hist = [0] * 24

        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        try:

            from outreach.db import get_db, _fetchall

            with get_db() as db:

                rows = _fetchall(

                    db,

                    "SELECT plan_date, COALESCE(focus_minutes,0) AS mins, COALESCE(notes,'') AS notes "

                    "FROM student_study_progress "

                    "WHERE client_id = %s AND COALESCE(focus_minutes,0) > 0 "

                    "ORDER BY plan_date DESC",

                    (cid,),

                )

                for r in rows:

                    mins = int(r.get("mins") or 0)

                    notes = (r.get("notes") or "").strip()

                    # Per-course bucket — "{mode}: {course_name}" → course_name; fallback to mode/Other

                    course_name = "Other / Unassigned"

                    if ":" in notes:

                        right = notes.split(":", 1)[1].strip()

                        if right:

                            course_name = course_lookup.get(_norm_course(right), right)

                    elif notes:

                        course_name = notes.title()

                    per_course_map[course_name] = per_course_map.get(course_name, 0) + mins

                    # Hour histogram (last 30 days only) — plan_date is "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DD"

                    pd = str(r.get("plan_date") or "")

                    if pd[:10] < cutoff:

                        continue

                    if len(pd) >= 13:

                        try:

                            h = int(pd[11:13])

                            if 0 <= h < 24:

                                hour_hist[h] += 1

                        except Exception:

                            pass

        except Exception:

            pass



        per_course = sorted(

            ({"name": k, "mins": v} for k, v in per_course_map.items()),

            key=lambda x: x["mins"],

            reverse=True,

        )



        rows_html = "".join(

            "<tr><td style='padding:10px 14px'>" + _esc(p["name"]) + "</td>"

            "<td style='padding:10px 14px;text-align:right;font-variant-numeric:tabular-nums'>"

            + str(p["mins"] // 60) + "h " + str(p["mins"] % 60) + "m</td></tr>"

            for p in per_course

        ) or "<tr><td colspan='2' style='padding:18px;color:var(--text-muted);text-align:center'>No study sessions yet — start a Focus session to begin tracking.</td></tr>"



        max_h = max(hour_hist) or 1

        bars = []

        for i in range(24):

            h_px = int((hour_hist[i] / max_h) * 130)

            bars.append(

                "<div title='" + f"{i:02d}" + ":00 — " + str(hour_hist[i]) + " sessions' "

                "style='flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px;'>"

                "<div style='width:100%;background:linear-gradient(180deg,#7C9CFF,#C084FC);"

                "border-radius:4px 4px 0 0;height:" + str(h_px) + "px;min-height:2px;'></div>"

                "<span style='font-size:10px;color:var(--text-muted)'>" + f"{i:02d}" + "</span></div>"

            )

        bars_html = "".join(bars)



        total_mins = int(focus.get("total_minutes", 0) or 0)

        total_sessions = int(focus.get("sessions", 0) or 0)

        streak = int(focus.get("streak_days", 0) or 0)

        avg_session = (total_mins // total_sessions) if total_sessions else 0



        # Rank + leaderboard position card

        rank_html = ""

        try:

            from student import academic as _ac

            total_xp = int(sdb.get_total_xp(cid) or 0)

            rank_info = sdb.get_study_rank(total_xp) or {}

            pos = _ac.my_rank("global", cid) or {}

            rname = _esc(rank_info.get("full_name") or "Unranked")

            rcolor = rank_info.get("color") or "#6366F1"

            pct = max(0, min(100, int(rank_info.get("progress_pct") or 0)))

            pos_rank = pos.get("rank")

            pos_total = pos.get("total")

            pos_text = (f"#{pos_rank} of {pos_total}" if pos_rank and pos_total else "Unranked")

            rank_html = (

                "<div class='card' style='padding:20px;margin-bottom:18px;display:flex;flex-wrap:wrap;align-items:center;gap:18px;border-left:4px solid " + rcolor + "'>"

                "<div style='flex:1;min-width:220px'>"

                "<div style='font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.1em;font-weight:700'>Rango actual</div>"

                "<div style='font-size:26px;font-weight:800;margin-top:4px;color:" + rcolor + "'>" + rname + "</div>"

                "<div style='font-size:13px;color:var(--text-muted);margin-top:2px'>" + str(total_xp) + " XP</div>"

                "<div style='background:var(--border);border-radius:8px;height:8px;margin-top:10px;overflow:hidden'>"

                "<div style='background:" + rcolor + ";height:8px;width:" + str(pct) + "%;transition:width .6s ease'></div>"

                "</div>"

                "</div>"

                "<div style='text-align:center;min-width:140px'>"

                "<div style='font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.1em;font-weight:700'>Posición global</div>"

                "<div style='font-size:28px;font-weight:800;margin-top:4px'>" + _esc(pos_text) + "</div>"

                "<a href='/student/leaderboard' style='font-size:12px;color:var(--primary);text-decoration:none'>View leaderboard &rarr;</a>"

                "</div>"

                "</div>"

            )

        except Exception:

            rank_html = ""



        body = """

        <h1 style='margin:0 0 8px;font-size:32px'>&#128202; Your study analytics</h1>

        <p style='color:var(--text-muted);margin:0 0 24px'>Hours, sessions, courses, and focus rhythm — at a glance.</p>

        __RANK__

        <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px'>

          <div class='card' style='padding:18px'><div style='font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em'>Tiempo total</div><div style='font-size:30px;font-weight:800;margin-top:6px'>__TOTAL__</div></div>

          <div class='card' style='padding:18px'><div style='font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em'>Sesiones</div><div style='font-size:30px;font-weight:800;margin-top:6px'>__SESSIONS__</div></div>

          <div class='card' style='padding:18px'><div style='font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em'>Promedio por sesión</div><div style='font-size:30px;font-weight:800;margin-top:6px'>__AVG__ min</div></div>

          <div class='card' style='padding:18px'><div style='font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em'>Racha 🔥</div><div style='font-size:30px;font-weight:800;margin-top:6px'>__STREAK__</div></div>

        </div>

        <div class='card' style='padding:18px;margin-bottom:18px'>

          <h3 style='margin:0 0 12px;font-size:16px'>When do you study? (last 30 days)</h3>

          <div style='display:flex;align-items:flex-end;gap:3px;height:160px;padding:8px 0'>__BARS__</div>

        </div>

        <div class='card' style='padding:0;overflow:hidden'>

          <h3 style='margin:0;padding:18px;border-bottom:1px solid var(--border);font-size:16px'>Tiempo por curso</h3>

          <table style='width:100%;border-collapse:collapse'>__ROWS__</table>

        </div>

        """

        body = (body

            .replace("__RANK__", rank_html)

            .replace("__TOTAL__", f"{total_mins//60}h {total_mins%60}m")

            .replace("__SESSIONS__", str(total_sessions))

            .replace("__AVG__", str(avg_session))

            .replace("__STREAK__", f"{streak} day" + ("s" if streak != 1 else ""))

            .replace("__BARS__", bars_html)

            .replace("__ROWS__", rows_html)

        )
        # Richer analytics view for the redesigned student shell.
        def _fmt_minutes(_mins: int) -> str:
            _mins = int(_mins or 0)
            if _mins >= 60:
                return f"{_mins // 60}h {_mins % 60}m"
            return f"{_mins}m"

        _today = datetime.now().date()
        _start_14 = _today - timedelta(days=13)
        _daily_map = {}
        _heat_map = {}
        try:
            from outreach.db import get_db, _fetchall
            with get_db() as db:
                _daily_rows = _fetchall(
                    db,
                    "SELECT plan_date, COALESCE(SUM(focus_minutes), 0) AS mins "
                    "FROM student_study_progress "
                    "WHERE client_id = %s AND plan_date >= %s "
                    "GROUP BY plan_date ORDER BY plan_date",
                    (cid, _start_14.isoformat()),
                )
                _heat_rows = _fetchall(
                    db,
                    "SELECT plan_date, COALESCE(SUM(focus_minutes), 0) AS mins "
                    "FROM student_study_progress "
                    "WHERE client_id = %s AND plan_date >= %s "
                    "GROUP BY plan_date ORDER BY plan_date",
                    (cid, (_today - timedelta(days=34)).isoformat()),
                )
            for _r in _daily_rows:
                _key = str(_r.get("plan_date") or "")[:10]
                _daily_map[_key] = _daily_map.get(_key, 0) + int(_r.get("mins") or 0)
            for _r in _heat_rows:
                _key = str(_r.get("plan_date") or "")[:10]
                _heat_map[_key] = _heat_map.get(_key, 0) + int(_r.get("mins") or 0)
        except Exception:
            pass

        _daily_vals = []
        for _i in range(14):
            _d = _start_14 + timedelta(days=_i)
            _daily_vals.append((_d, int(_daily_map.get(_d.isoformat(), 0) or 0)))
        _max_day = max([_v for _, _v in _daily_vals] or [1]) or 1
        _day_bars_html = "".join(
            "<div class='an-day' title='" + _d.strftime("%d/%m") + ": " + _fmt_minutes(_v) + "'>"
            "<div class='an-day-bar' style='height:" + str(max(6, int((_v / _max_day) * 150))) + "px'></div>"
            "<span>" + _d.strftime("%d") + "</span></div>"
            for _d, _v in _daily_vals
        )

        _max_course = max([int(_p.get("mins") or 0) for _p in per_course] or [1]) or 1
        _course_bars_html = "".join(
            "<div class='an-course-row'><div><strong>" + _esc(_p.get("name", "Curso")) + "</strong>"
            "<span>" + _fmt_minutes(int(_p.get("mins") or 0)) + "</span></div>"
            "<div class='an-track'><div style='width:" + str(max(4, int((int(_p.get("mins") or 0) / _max_course) * 100))) + "%'></div></div></div>"
            for _p in per_course[:7]
        ) or "<div class='an-empty'>Todavia no hay sesiones por curso. Entra a Enfoque y registra una sesion.</div>"

        _xp_history = []
        try:
            _xp_history = sdb.get_xp_history(cid, limit=14) or []
        except Exception:
            _xp_history = []
        _xp_vals = list(reversed([int(_x.get("xp") or 0) for _x in _xp_history]))
        _max_xp = max(_xp_vals or [1]) or 1
        _xp_bars_html = "".join(
            "<div class='an-xp' title='" + str(_v) + " XP'><div style='height:" + str(max(6, int((_v / _max_xp) * 120))) + "px'></div><span>" + str(_v) + "</span></div>"
            for _v in _xp_vals
        ) or "<div class='an-empty'>Cuando ganes XP, aca vas a ver el ritmo de progreso.</div>"

        _heat_start = _today - timedelta(days=34)
        _heat_html = ""
        for _i in range(35):
            _d = _heat_start + timedelta(days=_i)
            _m = int(_heat_map.get(_d.isoformat(), 0) or 0)
            _lvl = "l0"
            if _m >= 120:
                _lvl = "l4"
            elif _m >= 60:
                _lvl = "l3"
            elif _m >= 25:
                _lvl = "l2"
            elif _m > 0:
                _lvl = "l1"
            _heat_html += "<div class='an-heat " + _lvl + "' title='" + _d.strftime("%d/%m") + ": " + _fmt_minutes(_m) + "'></div>"

        _best_course = per_course[0]["name"] if per_course else "Sin curso dominante"
        _best_hour = max(range(24), key=lambda _h: hour_hist[_h]) if any(hour_hist) else None
        _best_hour_text = (f"{_best_hour:02d}:00" if _best_hour is not None else "Sin patron todavia")
        _focus_consistency = sum(1 for _, _v in _daily_vals if _v > 0)

        from outreach.i18n import t_dict as _t_dict
        _an = _t_dict("student_analytics")
        _an_json = {k: json.dumps(v, ensure_ascii=False) for k, v in _an.items()}

        body = f"""
        <style>
        .quiz-cd {{ --ink:#1A1A1F; --paper:#F4F1EA; --card:#FFFFFF; --line:#E2DCCC; --muted:#77756F; --accent:#FF7A3D; --pink:#EF5DA8; font-family:"Plus Jakarta Sans",system-ui,sans-serif;color:var(--ink); }}
        .quiz-cd h1 {{ font-family:"Fraunces",Georgia,serif!important;font-size:clamp(44px,6vw,76px)!important;line-height:.9!important;margin:0!important;font-weight:600!important;letter-spacing:-.055em!important; }}
        .quiz-cd .btn-pop {{ background:#1A1A1F;color:#FFF8E1;border:0;border-radius:999px;padding:12px 18px;font-weight:900;box-shadow:0 4px 0 rgba(0,0,0,.18);cursor:pointer;text-decoration:none; }}
        .quiz-cd .btn-pop.accent {{ background:#FF7A3D;color:#1A1A1F; }}
        .quiz-hero {{ background:linear-gradient(135deg,#FF7A3D,#EF5DA8);color:#fff;border-radius:22px;padding:26px;display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:18px;box-shadow:0 10px 30px rgba(255,122,61,.20); }}
        .qh-eye {{ font-size:12px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;opacity:.85; }}
        .qh-title {{ font-family:"Fraunces",Georgia,serif;font-size:34px;line-height:1;margin:6px 0;font-weight:600;letter-spacing:-.04em; }}
        .qh-sub {{ margin:0;font-size:14px;opacity:.9; }}
        .quiz-grid {{ display:grid;grid-template-columns:repeat(3,1fr);gap:14px; }}
        .qcard {{ background:#fff;border:1px solid #E2DCCC;border-radius:16px;padding:18px;position:relative;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04);display:flex;flex-direction:column;gap:10px;cursor:pointer;transition:transform .16s,box-shadow .16s; }}
        .qcard:hover {{ transform:translateY(-2px);box-shadow:0 6px 0 rgba(20,18,30,.05),0 18px 44px rgba(20,18,30,.08); }}
        .qcard.warn {{ border-color:#FF7A3D;background:linear-gradient(180deg,#FFF0E8 0%,#fff 52%); }}
        @media(max-width:1100px) {{ .quiz-grid {{ grid-template-columns:repeat(2,1fr); }} }}
        @media(max-width:700px) {{ .quiz-grid {{ grid-template-columns:1fr; }} .quiz-hero {{ align-items:flex-start;flex-direction:column; }} }}
        .an-page {{ display:flex; flex-direction:column; gap:22px; }}
        .an-hero {{ display:grid; grid-template-columns:minmax(0,1.5fr) minmax(240px,.6fr); gap:18px; align-items:stretch; }}
        .an-title-card {{ border:1px solid rgba(124,92,255,.18); border-radius:28px; padding:26px; background:linear-gradient(135deg,#ffffff 0%,#f7f3ff 48%,#eefbff 100%); box-shadow:0 18px 50px rgba(18,24,38,.08); }}
        .an-kicker {{ color:#7b61ff; font-size:12px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }}
        .an-title-card h1 {{ margin:8px 0 8px; font-size:clamp(32px,4vw,54px); line-height:1; color:#171720; letter-spacing:0; }}
        .an-title-card p {{ margin:0; max-width:720px; color:#6a6776; font-size:16px; line-height:1.55; }}
        .an-rank-wrap .card {{ height:100%; margin:0 !important; border-radius:28px !important; box-shadow:0 18px 45px rgba(18,24,38,.08); }}
        .an-stat-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }}
        .an-stat {{ border:1px solid rgba(20,20,28,.1); border-radius:24px; padding:20px; background:#fff; box-shadow:0 14px 38px rgba(18,24,38,.06); }}
        .an-stat .label {{ color:#85818f; font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
        .an-stat .num {{ margin-top:8px; color:#15151d; font-size:34px; line-height:1; font-weight:900; }}
        .an-stat .sub {{ margin-top:8px; color:#8a8794; font-size:13px; }}
        .an-grid {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(320px,.8fr); gap:18px; align-items:start; }}
        .an-card {{ border:1px solid rgba(20,20,28,.1); border-radius:26px; padding:22px; background:#fff; box-shadow:0 16px 45px rgba(18,24,38,.06); overflow:hidden; }}
        .an-card h2 {{ margin:0 0 4px; font-size:19px; color:#15151d; }}
        .an-card p {{ margin:0 0 18px; color:#777382; font-size:14px; }}
        .an-days {{ height:190px; display:grid; grid-template-columns:repeat(14,1fr); gap:8px; align-items:end; padding-top:10px; }}
        .an-day {{ min-width:0; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:8px; }}
        .an-day-bar {{ width:100%; max-width:42px; border-radius:14px 14px 8px 8px; background:linear-gradient(180deg,#7b61ff,#21c7a8); box-shadow:0 10px 24px rgba(124,97,255,.2); }}
        .an-day span,.an-xp span {{ color:#8b8794; font-size:11px; font-weight:800; }}
        .an-course-row {{ display:flex; flex-direction:column; gap:8px; margin-bottom:14px; }}
        .an-course-row > div:first-child {{ display:flex; justify-content:space-between; gap:14px; color:#171720; font-size:14px; }}
        .an-course-row span {{ color:#85818f; font-weight:800; }}
        .an-track {{ height:12px; border-radius:999px; background:#f0edf7; overflow:hidden; }}
        .an-track div {{ height:100%; border-radius:999px; background:linear-gradient(90deg,#111827,#7b61ff,#21c7a8); }}
        .an-xp-row {{ height:150px; display:flex; gap:8px; align-items:flex-end; }}
        .an-xp {{ flex:1; min-width:18px; display:flex; flex-direction:column; align-items:center; gap:7px; }}
        .an-xp div {{ width:100%; border-radius:12px 12px 5px 5px; background:linear-gradient(180deg,#ffbe3d,#ff6b6b); }}
        .an-heat-grid {{ display:grid; grid-template-columns:repeat(7,1fr); gap:8px; }}
        .an-heat {{ aspect-ratio:1; border-radius:9px; background:#efedf4; border:1px solid rgba(20,20,28,.06); }}
        .an-heat.l1 {{ background:#d8f6eb; }}
        .an-heat.l2 {{ background:#8de6c6; }}
        .an-heat.l3 {{ background:#35cfa2; }}
        .an-heat.l4 {{ background:#07936f; }}
        .an-insights {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
        .an-insight {{ border-radius:22px; padding:18px; background:#171720; color:#fff; min-height:118px; }}
        .an-insight span {{ color:#bdb7ff; font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
        .an-insight strong {{ display:block; margin-top:10px; font-size:22px; }}
        .an-insight p {{ margin:6px 0 0; color:#c9c6d2; font-size:13px; line-height:1.4; }}
        .an-empty {{ padding:18px; border-radius:18px; background:#f7f5fb; color:#777382; text-align:center; }}
        .an-table-wrap table {{ width:100%; border-collapse:collapse; }}
        .an-table-wrap tr + tr {{ border-top:1px solid #ece8f2; }}
        @media (max-width:980px) {{ .an-hero,.an-grid,.an-insights {{ grid-template-columns:1fr; }} .an-stat-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
        @media (max-width:560px) {{ .an-stat-grid {{ grid-template-columns:1fr; }} .an-title-card {{ padding:20px; border-radius:22px; }} .an-days {{ gap:4px; }} }}
        </style>
        <div class="an-page">
          <div class="an-hero">
            <section class="an-title-card">
              <div class="an-kicker">ANALYTICS DE ESTUDIO</div>
              <h1>Tu rendimiento, sin humo.</h1>
              <p>Minutos de enfoque, XP, cursos dominantes y consistencia real. Esto es para ver si estas estudiando de verdad o solo abriendo la app.</p>
            </section>
            <div class="an-rank-wrap">{rank_html}</div>
          </div>

          <div class="an-stat-grid">
            <div class="an-stat"><div class="label">Tiempo total</div><div class="num">{_fmt_minutes(total_mins)}</div><div class="sub">acumulado en enfoque</div></div>
            <div class="an-stat"><div class="label">Sesiones</div><div class="num">{total_sessions}</div><div class="sub">registros guardados</div></div>
            <div class="an-stat"><div class="label">Promedio</div><div class="num">{avg_session}m</div><div class="sub">por sesion</div></div>
            <div class="an-stat"><div class="label">Racha 🔥</div><div class="num">{streak}</div><div class="sub">dias seguidos</div></div>
          </div>

          <div class="an-insights">
            <div class="an-insight"><span>Curso fuerte</span><strong>{_esc(_best_course)}</strong><p>Es donde mas tiempo estas metiendo. Si no era tu prioridad, ajusta.</p></div>
            <div class="an-insight"><span>Hora activa</span><strong>{_best_hour_text}</strong><p>Tu patron mas repetido de estudio en los ultimos registros.</p></div>
            <div class="an-insight"><span>Consistencia</span><strong>{_focus_consistency}/14 dias</strong><p>Dias con al menos una sesion en las ultimas dos semanas.</p></div>
          </div>

          <div class="an-grid">
            <section class="an-card">
              <h2>Tendencia de enfoque</h2>
              <p>Minutos estudiados durante los ultimos 14 dias.</p>
              <div class="an-days">{_day_bars_html}</div>
            </section>
            <section class="an-card">
              <h2>Tiempo por curso</h2>
              <p>Donde se esta yendo tu energia.</p>
              {_course_bars_html}
            </section>
          </div>

          <div class="an-grid">
            <section class="an-card">
              <h2>Ritmo de XP</h2>
              <p>Ultimas ganancias registradas.</p>
              <div class="an-xp-row">{_xp_bars_html}</div>
            </section>
            <section class="an-card">
              <h2>Mapa de constancia</h2>
              <p>Ultimos 35 dias. Mas verde significa mas minutos.</p>
              <div class="an-heat-grid">{_heat_html}</div>
            </section>
          </div>

          <section class="an-card an-table-wrap">
            <h2>Detalle por curso</h2>
            <p>Resumen exacto de minutos acumulados.</p>
            <table>{rows_html}</table>
          </section>
        </div>
        """
        _focus_rows = []
        _focus_since = (datetime.now().date() - timedelta(days=7 * 26)).isoformat()
        try:
            from outreach.db import get_db, _fetchall
            with get_db() as db:
                _raw_focus_rows = _fetchall(
                    db,
                    "SELECT sp.plan_date, COALESCE(sp.focus_minutes, 0) AS mins, "
                    "COALESCE(sp.notes, '') AS notes, COALESCE(c.name, '') AS course_name "
                    "FROM student_study_progress sp "
                    "LEFT JOIN student_courses c ON c.id = sp.course_id "
                    "WHERE sp.client_id = %s AND COALESCE(sp.focus_minutes, 0) > 0 AND sp.plan_date >= %s "
                    "ORDER BY sp.plan_date",
                    (cid, _focus_since),
                )
            for _r in _raw_focus_rows:
                _course = (_r.get("course_name") or "").strip()
                if not _course:
                    _notes = (_r.get("notes") or "").strip()
                    if ":" in _notes:
                        _course = _notes.split(":", 1)[1].strip()
                    elif _notes:
                        _course = _notes.title()
                if not _course:
                    _course = _an.get("no_course", "No course")
                _focus_rows.append({
                    "date": str(_r.get("plan_date") or "")[:10],
                    "course": _course,
                    "minutes": int(_r.get("mins") or 0),
                })
        except Exception:
            _focus_rows = []
        _focus_json = json.dumps(_focus_rows, ensure_ascii=False).replace("<", "\\u003c")

        body = f"""
        <style>
        .wa-page {{ --warm-bg:#F4F1EA; --warm-card:#FFFFFF; --warm-line:#E2DCCC; --warm-ink:#1A1A1F; --warm-muted:#77756F; --warm-orange:#FF7A3D; --warm-green:#2E9266; display:flex; flex-direction:column; gap:18px; }}
        .wa-head {{ display:flex; justify-content:space-between; align-items:flex-end; gap:18px; flex-wrap:wrap; }}
        .wa-eye {{ color:var(--warm-orange); font-size:12px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }}
        .wa-head h1 {{ margin:6px 0 0; font-family:"Fraunces",Georgia,serif; font-size:clamp(38px,4vw,60px); line-height:.98; color:var(--warm-ink); letter-spacing:-.04em; font-weight:650; }}
        .wa-head p {{ margin:8px 0 0; color:var(--warm-muted); max-width:720px; line-height:1.5; }}
        .wa-week-nav {{ display:flex; align-items:center; gap:10px; background:var(--warm-card); border:1px solid var(--warm-line); border-radius:999px; padding:8px; box-shadow:0 14px 36px rgba(26,26,31,.08); }}
        .wa-week-nav button {{ border:1px solid var(--warm-line); border-radius:999px; background:#FFF8EE; color:var(--warm-ink); width:38px; height:38px; font-weight:900; cursor:pointer; }}
        .wa-week-nav button:disabled {{ opacity:.35; cursor:not-allowed; }}
        #wa-week-label {{ min-width:230px; text-align:center; font-weight:900; color:var(--warm-ink); }}
        .wa-stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }}
        .wa-stat,.wa-card {{ background:var(--warm-card); border:1px solid var(--warm-line); border-radius:24px; box-shadow:0 18px 48px rgba(26,26,31,.07); }}
        .wa-stat {{ padding:18px; }}
        .wa-stat span {{ color:var(--warm-muted); font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }}
        .wa-stat strong {{ display:block; margin-top:8px; font-family:"Fraunces",Georgia,serif; font-size:34px; line-height:1; color:var(--warm-ink); font-weight:650; }}
        .wa-grid {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(330px,.85fr); gap:18px; align-items:start; }}
        .wa-card {{ padding:22px; overflow:hidden; }}
        .wa-card h2 {{ margin:0; font-family:"Fraunces",Georgia,serif; font-size:24px; color:var(--warm-ink); font-weight:650; }}
        .wa-card p {{ margin:6px 0 18px; color:var(--warm-muted); font-size:14px; }}
        .wa-line-wrap {{ width:100%; min-height:300px; }}
        .wa-line-svg {{ width:100%; height:300px; overflow:visible; }}
        .wa-axis {{ stroke:#EEE6D6; stroke-width:1; }}
        .wa-line {{ fill:none; stroke:var(--warm-orange); stroke-width:4; stroke-linecap:round; stroke-linejoin:round; }}
        .wa-dot {{ fill:#FFF8EE; stroke:var(--warm-orange); stroke-width:4; }}
        .wa-label {{ fill:var(--warm-muted); font-size:12px; font-weight:800; }}
        .wa-value {{ fill:var(--warm-ink); font-size:12px; font-weight:900; opacity:0; pointer-events:none; transition:opacity .14s ease; }}
        .wa-point:hover .wa-value, .wa-point:focus-within .wa-value {{ opacity:1; }}
        .wa-hit {{ fill:transparent; cursor:help; }}
        .wa-course-list {{ display:flex; flex-direction:column; gap:12px; }}
        .wa-course-btn {{ display:block; width:100%; text-align:left; border:1px solid var(--warm-line); background:#FFFDF8; border-radius:18px; padding:13px; cursor:pointer; transition:all .15s ease; }}
        .wa-course-btn:hover,.wa-course-btn.active {{ border-color:var(--warm-orange); transform:translateY(-1px); box-shadow:0 12px 24px rgba(255,122,61,.14); }}
        .wa-course-top {{ display:flex; justify-content:space-between; gap:12px; font-weight:900; color:var(--warm-ink); }}
        .wa-course-track {{ margin-top:9px; height:12px; background:#F1EBDD; border-radius:999px; overflow:hidden; }}
        .wa-course-track div {{ height:100%; border-radius:999px; background:linear-gradient(90deg,var(--warm-orange),#FFB84D,var(--warm-green)); }}
        .wa-detail-bars {{ display:grid; grid-template-columns:repeat(7,1fr); align-items:end; gap:10px; height:220px; margin-top:12px; }}
        .wa-detail-day {{ display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:8px; min-width:0; }}
        .wa-detail-bar {{ width:100%; max-width:46px; min-height:7px; border-radius:14px 14px 7px 7px; background:linear-gradient(180deg,var(--warm-green),var(--warm-orange)); }}
        .wa-detail-day span {{ color:var(--warm-muted); font-size:12px; font-weight:900; }}
        .wa-empty {{ padding:24px; border-radius:20px; background:#FFF8EE; color:var(--warm-muted); text-align:center; }}
        @media (max-width:1000px) {{ .wa-grid {{ grid-template-columns:1fr; }} .wa-stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
        @media (max-width:580px) {{ .wa-stats {{ grid-template-columns:1fr; }} .wa-week-nav {{ width:100%; justify-content:space-between; }} #wa-week-label {{ min-width:0; }} }}
        </style>
        <div class="wa-page">
          <div class="wa-head">
            <div>
              <div class="wa-eye">{_esc(_an.get("kicker", "WEEKLY ANALYTICS"))}</div>
              <h1>{_esc(_an.get("hero", "Your study week."))}</h1>
              <p>{_esc(_an.get("subtitle", ""))}</p>
            </div>
            <div class="wa-week-nav">
              <button id="wa-prev" type="button" aria-label="Semana anterior">&lsaquo;</button>
              <div id="wa-week-label">{_esc(_an.get("current_week", "Current week"))}</div>
              <button id="wa-next" type="button" aria-label="Semana siguiente">&rsaquo;</button>
            </div>
          </div>

          <div class="wa-stats">
            <div class="wa-stat"><span>{_esc(_an.get("week_total", "Week total"))}</span><strong id="wa-total">0m</strong></div>
            <div class="wa-stat"><span>{_esc(_an.get("best_day", "Best day"))}</span><strong id="wa-best-day">-</strong></div>
            <div class="wa-stat"><span>{_esc(_an.get("active_courses", "Active courses"))}</span><strong id="wa-course-count">0</strong></div>
            <div class="wa-stat"><span>{_esc(_an.get("daily_average", "Daily average"))}</span><strong id="wa-average">0m</strong></div>
          </div>

          <div class="wa-grid">
            <section class="wa-card">
              <h2>{_esc(_an.get("minutes_per_day", "Minutes per day"))}</h2>
              <p>{_esc(_an.get("minutes_per_day_sub", ""))}</p>
              <div id="wa-line" class="wa-line-wrap"></div>
            </section>
            <section class="wa-card">
              <h2>{_esc(_an.get("hours_per_course", "Hours per course"))}</h2>
              <p>{_esc(_an.get("hours_per_course_sub", ""))}</p>
              <div id="wa-courses" class="wa-course-list"></div>
            </section>
          </div>

          <section class="wa-card">
            <h2 id="wa-detail-title">{_esc(_an.get("course_detail", "Daily detail by course"))}</h2>
            <p id="wa-detail-sub">{_esc(_an.get("course_detail_sub", ""))}</p>
            <div id="wa-detail" class="wa-detail-bars"></div>
          </section>
        </div>

        <script>
        (function(){{
          var rows = {_focus_json};
          var I18N = {{
            currentWeek: {_an_json.get("current_week", json.dumps("Current week"))},
            courseDetail: {_an_json.get("course_detail", json.dumps("Daily detail by course"))},
            courseDetailSub: {_an_json.get("course_detail_sub", json.dumps("Select a course to see how it was distributed during the week."))},
            noWeekSessions: {_an_json.get("no_week_sessions", json.dumps("No sessions recorded this week."))},
            noWeekData: {_an_json.get("no_week_data", json.dumps("No data for this week."))},
            courseDayDetail: {_an_json.get("course_day_detail", json.dumps("Minutes studied per day in the selected week."))}
          }};
          var selectedCourse = null;
          var weekOffset = 0;
          var dayNames = {json.dumps(["Lun","Mar","Mie","Jue","Vie","Sab","Dom"] if session.get("lang", "es") == "es" else ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"])};
          function pad(n) {{ return String(n).padStart(2,'0'); }}
          function key(d) {{ return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()); }}
          function minsText(m) {{ m = Math.round(m || 0); return m >= 60 ? (Math.floor(m/60) + 'h ' + (m%60) + 'm') : (m + 'm'); }}
          function monday(base) {{
            var d = new Date(base.getFullYear(), base.getMonth(), base.getDate());
            var day = d.getDay();
            var diff = day === 0 ? -6 : 1 - day;
            d.setDate(d.getDate() + diff + weekOffset * 7);
            return d;
          }}
          function renderLine(days, totals) {{
            var max = Math.max(30, ...totals);
            var w = 700, h = 250, padX = 42, padY = 28;
            var pts = totals.map(function(v,i){{
              var x = padX + i * ((w - padX*2) / 6);
              var y = h - padY - (v / max) * (h - padY*2);
              return {{x:x,y:y,v:v,i:i}};
            }});
            var path = pts.map(function(p,i){{ return (i?'L':'M') + p.x + ' ' + p.y; }}).join(' ');
            var grid = [0,.25,.5,.75,1].map(function(t){{
              var y = h - padY - t * (h - padY*2);
              return '<line class="wa-axis" x1="'+padX+'" x2="'+(w-padX)+'" y1="'+y+'" y2="'+y+'"></line>';
            }}).join('');
            var circles = pts.map(function(p){{
              var value = minsText(p.v);
              return '<g class="wa-point" tabindex="0"><title>'+value+'</title><circle class="wa-hit" cx="'+p.x+'" cy="'+p.y+'" r="20"></circle><circle class="wa-dot" cx="'+p.x+'" cy="'+p.y+'" r="7"></circle><text class="wa-value" x="'+p.x+'" y="'+(p.y-13)+'" text-anchor="middle">'+value+'</text></g>';
            }}).join('');
            var labels = pts.map(function(p){{
              return '<text class="wa-label" x="'+p.x+'" y="'+(h-4)+'" text-anchor="middle">'+dayNames[p.i]+'</text>';
            }}).join('');
            document.getElementById('wa-line').innerHTML = '<svg class="wa-line-svg" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none">'+grid+'<path class="wa-line" d="'+path+'"></path>'+circles+labels+'</svg>';
          }}
          function render() {{
            var start = monday(new Date());
            var days = Array.from({{length:7}}, function(_,i){{ var d = new Date(start); d.setDate(start.getDate()+i); return d; }});
            var dayKeys = days.map(key);
            var weekRows = rows.filter(function(r){{ return dayKeys.indexOf((r.date || '').slice(0,10)) !== -1; }});
            var totals = dayKeys.map(function(k){{ return weekRows.filter(function(r){{ return r.date === k; }}).reduce(function(a,r){{ return a + (parseInt(r.minutes)||0); }}, 0); }});
            var total = totals.reduce(function(a,b){{ return a+b; }},0);
            var bestIdx = totals.indexOf(Math.max(...totals));
            var courseTotals = {{}};
            weekRows.forEach(function(r){{ var c = r.course || 'Sin curso'; courseTotals[c] = (courseTotals[c] || 0) + (parseInt(r.minutes)||0); }});
            var courses = Object.keys(courseTotals).sort(function(a,b){{ return courseTotals[b] - courseTotals[a]; }});
            if (!selectedCourse || courses.indexOf(selectedCourse) === -1) selectedCourse = courses[0] || null;
            document.getElementById('wa-week-label').textContent = days[0].toLocaleDateString(undefined, {{day:'numeric', month:'short'}}) + ' - ' + days[6].toLocaleDateString(undefined, {{day:'numeric', month:'short'}});
            document.getElementById('wa-next').disabled = weekOffset >= 0;
            document.getElementById('wa-total').textContent = minsText(total);
            document.getElementById('wa-best-day').textContent = total ? dayNames[bestIdx] : '-';
            document.getElementById('wa-course-count').textContent = courses.length;
            document.getElementById('wa-average').textContent = minsText(total / 7);
            renderLine(days, totals);
            var maxCourse = Math.max(1, ...courses.map(function(c){{ return courseTotals[c]; }}));
            var list = document.getElementById('wa-courses');
            if (!courses.length) {{
              list.innerHTML = '<div class="wa-empty">' + I18N.noWeekSessions + '</div>';
            }} else {{
              list.innerHTML = courses.map(function(c){{
                var pct = Math.max(5, Math.round(courseTotals[c] / maxCourse * 100));
                return '<button type="button" class="wa-course-btn '+(c===selectedCourse?'active':'')+'" data-course="'+encodeURIComponent(c)+'"><div class="wa-course-top"><span>'+c+'</span><span>'+minsText(courseTotals[c])+'</span></div><div class="wa-course-track"><div style="width:'+pct+'%"></div></div></button>';
              }}).join('');
              list.querySelectorAll('.wa-course-btn').forEach(function(btn){{
                btn.addEventListener('click', function(){{ selectedCourse = decodeURIComponent(btn.getAttribute('data-course') || ''); render(); }});
              }});
            }}
            renderDetail(dayKeys, weekRows);
          }}
          function renderDetail(dayKeys, weekRows) {{
            var title = document.getElementById('wa-detail-title');
            var sub = document.getElementById('wa-detail-sub');
            var detail = document.getElementById('wa-detail');
            if (!selectedCourse) {{
              title.textContent = I18N.courseDetail;
              sub.textContent = I18N.courseDetailSub;
              detail.innerHTML = '<div class="wa-empty" style="grid-column:1/-1;">' + I18N.noWeekData + '</div>';
              return;
            }}
            var vals = dayKeys.map(function(k){{ return weekRows.filter(function(r){{ return r.date === k && r.course === selectedCourse; }}).reduce(function(a,r){{ return a + (parseInt(r.minutes)||0); }}, 0); }});
            var max = Math.max(15, ...vals);
            title.textContent = selectedCourse;
            sub.textContent = I18N.courseDayDetail;
            detail.innerHTML = vals.map(function(v,i){{
              var h = Math.max(7, Math.round(v / max * 180));
              return '<div class="wa-detail-day" title="'+dayNames[i]+': '+minsText(v)+'"><div class="wa-detail-bar" style="height:'+h+'px"></div><strong>'+minsText(v)+'</strong><span>'+dayNames[i]+'</span></div>';
            }}).join('');
          }}
          document.getElementById('wa-prev').addEventListener('click', function(){{ weekOffset--; render(); }});
          document.getElementById('wa-next').addEventListener('click', function(){{ if (weekOffset < 0) weekOffset++; render(); }});
          render();
        }})();
        </script>
        """

        return _s_render("Analytics", body, active_page="student_analytics")



    # ── Hard-block deprecated student pages ─────────────────

    # Plan, Notes, AI Tutor, Schedule, Panic, Practice, and Training have been removed from the product.

    # Their old route handlers still exist deeper in this file, but a

    # before_request guard intercepts them and redirects to the dashboard.

    @app.before_request

    def _block_deprecated_student_pages():

        p = request.path or ""

        for api_prefix, message in REMOVED_API_PREFIXES.items():
            if p == api_prefix or p.startswith(api_prefix + "/"):
                return jsonify({"error": message}), 410

        # Match exact path or sub-paths like /student/notes/123

        for dead in DEPRECATED_STUDENT_PATHS:

            if p == dead or p.startswith(dead + "/"):

                if not _logged_in():

                    return redirect(url_for("login"))

                return redirect(url_for("student_dashboard_page"))

        return None



    # ── Hard server-side gate: students MUST complete their academic profile
    # (country / university / major) before using any student feature.
    # API endpoints needed by the setup wizard itself are explicitly allowed.
    _SETUP_PATH = "/student/setup"
    _SETUP_ALLOWED_PREFIXES = (
        _SETUP_PATH,
        "/api/academic/",        # countries / universities / majors / profile
        "/static/",
        "/logout",
        "/login",
        "/register",
        "/verify-email",
        "/resend-verification",
        "/forgot-password",
        "/reset-password",
        "/api/csrf",
    )

    @app.before_request
    def _enforce_student_academic_setup():
        if not _logged_in():
            return None
        if session.get("account_type") != "student":
            return None
        p = request.path or ""
        # Only gate student-facing pages and APIs
        if not (p == "/student" or p.startswith("/student/") or p.startswith("/api/student/")):
            return None
        for ok in _SETUP_ALLOWED_PREFIXES:
            if p == ok or p.startswith(ok):
                return None
        try:
            from student import academic as _ac
            if not _ac.needs_setup(_cid()):
                return None
        except Exception:
            return None
        # Block — JSON for APIs, redirect for pages
        if p.startswith("/api/"):
            return jsonify(error="Complete your academic profile first.",
                           setup_required=True,
                           setup_url=_SETUP_PATH), 403
        return redirect(_SETUP_PATH)


    # Also kill the legacy plan-generation API and panic API.

    @app.route("/api/student/plan/generate-removed", methods=["POST"])

    def _student_plan_generate_removed():

        return jsonify({"error": "AI study plans have been removed"}), 410



    # ── Canvas connection ───────────────────────────────────



    @app.route("/api/student/canvas/connect", methods=["POST"])

    @limiter.limit("10 per minute")

    def student_canvas_connect():

        """Save Canvas URL + API token and test the connection."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        data = request.get_json(force=True)

        canvas_url = normalize_canvas_url(data.get("canvas_url") or "")

        token = (data.get("token") or "").strip()



        if not canvas_url or not token:

            return jsonify({"error": "canvas_url and token are required"}), 400



        # Test connection AND persist courses immediately. No AI/file analysis —
        # the connection exists purely so we can show the student's class list and
        # power class-level leaderboards.

        try:

            client = CanvasClient(canvas_url, token)

            courses = client.get_courses()

        except Exception as e:

            return jsonify({"error": f"Canvas connection failed: {e}"}), 400



        sdb.save_canvas_token(_cid(), canvas_url, token)



        # Persist every course immediately so /student/courses shows them right away.

        cid = _cid()

        saved = 0

        for c in courses:

            try:

                cid_canvas = int(c.get("id"))

                name = c.get("name") or c.get("course_code") or f"Course {cid_canvas}"

                code = c.get("course_code", "") or ""

                term = ""

                t = c.get("term") or {}

                if isinstance(t, dict):

                    term = t.get("name", "") or ""

                sdb.upsert_course(cid, cid_canvas, name, code, term)

                saved += 1

            except Exception:

                continue



        return jsonify({

            "message": "Canvas connected",

            "courses_found": len(courses),

            "courses_saved": saved,

            "courses": [{"id": c["id"], "name": c.get("name", "?")} for c in courses[:20]],

        })



    @app.route("/api/student/canvas/disconnect", methods=["POST"])

    def student_canvas_disconnect():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_canvas_token(_cid())

        return jsonify({"message": "Canvas disconnected"})



    @app.route("/api/student/canvas/status", methods=["GET"])

    def student_canvas_status():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        tok = sdb.get_canvas_token(_cid())

        if not tok:

            return jsonify({"connected": False})

        return jsonify({

            "connected": True,

            "canvas_url": tok["canvas_url"],

        })



    # ── Sync courses (background) ──────────────────────────



    import threading



    # In-memory sync status per client  {client_id: {status, progress, ...}}

    _sync_status: dict[int, dict] = {}



    def _kick_silent_canvas_resync(client_id: int) -> None:
        """Fire-and-forget Canvas course refresh on page visits.
        Skips if already running. No UI feedback — courses just appear."""
        try:
            tok = sdb.get_canvas_token(client_id)
        except Exception:
            return
        if not tok:
            return
        if _sync_status.get(client_id, {}).get("status") == "running":
            return

        def _bg():
            _sync_status[client_id] = {"status": "running", "started_at": datetime.now().isoformat()}
            try:
                canvas = CanvasClient(tok["canvas_url"], tok["token"])
                courses = canvas.get_courses()
                for c in courses:
                    try:
                        cid_canvas = int(c.get("id"))
                        name = c.get("name") or c.get("course_code") or f"Course {cid_canvas}"
                        code = c.get("course_code", "") or ""
                        sdb.upsert_course(client_id, cid_canvas, name, code)
                    except Exception:
                        continue
                _sync_status[client_id] = {"status": "done", "courses_done": len(courses)}
            except Exception as e:
                log.warning("silent canvas resync failed (client %s): %s", client_id, e)
                _sync_status[client_id] = {"status": "error", "error": str(e)}

        threading.Thread(target=_bg, daemon=True).start()



    @app.route("/api/student/sync", methods=["POST"])

    @limiter.limit("3 per minute")

    def student_sync_courses():

        """Kick off a background sync. Returns immediately."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        client_id = _cid()

        tok = sdb.get_canvas_token(client_id)

        if not tok:

            return jsonify({"error": "Canvas not connected"}), 400



        # Don't start if already running

        existing = _sync_status.get(client_id, {})

        if existing.get("status") == "running":

            return jsonify({"message": "Sync already in progress", "sync": existing})



        _sync_status[client_id] = {

            "status": "running",

            "progress": "Starting...",

            "courses_total": 0,

            "courses_done": 0,

            "files_downloaded": 0,

            "started_at": datetime.now().isoformat(),

        }



        def _do_sync():

            # Courses-only sync. No file downloads, no AI analysis. We just refresh

            # the user's class list so leaderboards & exam tracking stay current.

            try:

                canvas = CanvasClient(tok["canvas_url"], tok["token"])

                courses = canvas.get_courses()

                _sync_status[client_id]["courses_total"] = len(courses)

                synced = []

                for idx, c in enumerate(courses):

                    try:

                        cid_canvas = int(c.get("id"))

                        name = c.get("name") or c.get("course_code") or f"Course {cid_canvas}"

                        code = c.get("course_code", "") or ""

                        sdb.upsert_course(client_id, cid_canvas, name, code)

                        synced.append(name)

                        _sync_status[client_id]["courses_done"] = idx + 1

                        _sync_status[client_id]["progress"] = f"Synced {name}"

                    except Exception:

                        continue

                _sync_status[client_id] = {

                    "status": "done",

                    "progress": f"Synced {len(synced)} courses",

                    "courses_total": len(courses),

                    "courses_done": len(synced),

                    "files_downloaded": 0,

                    "courses": synced,

                    "warnings": [],

                    "no_syllabus": [],

                }

            except Exception as e:

                log.error("Background sync failed for client %s: %s", client_id, e)

                _sync_status[client_id] = {"status": "error", "progress": f"Sync failed: {e}", "courses_total": 0, "courses_done": 0, "files_downloaded": 0}

            return



        # Old AI-driven sync below is intentionally unreachable.

        def _do_sync_legacy():

            try:

                canvas = CanvasClient(tok["canvas_url"], tok["token"])

                courses = canvas.get_courses()

                _sync_status[client_id]["courses_total"] = len(courses)

                _sync_status[client_id]["progress"] = f"Found {len(courses)} courses"



                synced = []

                total_files = 0

                no_syllabus_courses = []



                for idx, c in enumerate(courses):

                    cid_canvas = c["id"]

                    name = c.get("name", "Unknown Course")

                    code = c.get("course_code", "")

                    _sync_status[client_id]["progress"] = f"Syncing {name} ({idx + 1}/{len(courses)})"



                    db_id = sdb.upsert_course(client_id, cid_canvas, name, code)



                    syllabus_html = ""

                    file_texts = []

                    assignments = []

                    found_syllabus = False



                    try:

                        syllabus_html = canvas.get_syllabus(cid_canvas)

                        if syllabus_html and len(syllabus_html.strip()) > 50:

                            found_syllabus = True

                    except Exception:

                        pass



                    # Download ONLY syllabus-related files (syllabus, programa, guía docente, etc.)

                    try:

                        syllabus_files = canvas.find_syllabus_files(cid_canvas)

                        for sf in syllabus_files:

                            fname = sf.get("display_name", sf.get("filename", ""))

                            fl = fname.lower()

                            if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc")):

                                continue

                            if sf.get("size", 0) > 15 * 1024 * 1024:

                                continue

                            try:

                                _sync_status[client_id]["progress"] = f"{name}: downloading {fname}"

                                content = canvas.get_file_content(sf)

                                text = ""

                                if fl.endswith(".pdf"):

                                    text = extract_text_from_pdf(content)

                                elif fl.endswith((".docx", ".doc")):

                                    text = extract_text_from_docx(content)

                                if text and len(text.strip()) > 50:

                                    file_texts.append({"filename": fname, "text": text[:8000]})

                                    total_files += 1

                                    found_syllabus = True

                                    _sync_status[client_id]["files_downloaded"] = total_files

                            except Exception:

                                pass

                    except Exception:

                        pass



                    # Include manually-uploaded files

                    try:

                        uploaded = sdb.get_course_files(client_id, db_id)

                        for uf in uploaded:

                            if uf.get("extracted_text") and len(uf["extracted_text"].strip()) > 50:

                                file_texts.append({

                                    "filename": uf["original_name"],

                                    "text": uf["extracted_text"][:8000],

                                })

                                found_syllabus = True

                    except Exception:

                        pass



                    try:

                        assignments = canvas.get_assignments(cid_canvas)

                    except Exception:

                        pass



                    if not found_syllabus:

                        no_syllabus_courses.append(name)



                    # AI analysis

                    _sync_status[client_id]["progress"] = f"{name}: AI analyzing {len(file_texts)} files..."

                    analysis = analyze_course_material(

                        course_name=name,

                        syllabus_html=syllabus_html,

                        file_texts=file_texts,

                        assignments=assignments,

                    )



                    sdb.update_course_analysis(db_id, analysis)

                    if analysis.get("exams"):

                        sdb.save_exams(client_id, db_id, analysis["exams"])



                    _sync_status[client_id]["courses_done"] = idx + 1

                    synced.append(name)



                # Build final status with syllabus warnings

                warnings = []

                if no_syllabus_courses:

                    for cn in no_syllabus_courses:

                        warnings.append(

                            f"Could not find a syllabus/programa for \"{cn}\". "

                            f"Please upload it manually on the course page so the AI can create the best study plan."

                        )



                _sync_status[client_id] = {

                    "status": "done",

                    "progress": f"Synced {len(synced)} courses, {total_files} syllabus files downloaded",

                    "courses_total": len(courses),

                    "courses_done": len(courses),

                    "files_downloaded": total_files,

                    "courses": synced,

                    "warnings": warnings,

                    "no_syllabus": no_syllabus_courses,

                }

            except Exception as e:

                log.error("Background sync failed for client %s: %s", client_id, e)

                _sync_status[client_id] = {

                    "status": "error",

                    "progress": f"Sync failed: {e}",

                    "courses_total": 0,

                    "courses_done": 0,

                    "files_downloaded": 0,

                }



        thread = threading.Thread(target=_do_sync, daemon=True)

        thread.start()



        return jsonify({"message": "Sync started", "sync": _sync_status[client_id]})



    @app.route("/api/student/sync/status", methods=["GET"])

    def student_sync_status():

        """Poll sync progress."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        status = _sync_status.get(_cid(), {"status": "idle", "progress": "No sync running"})

        return jsonify(status)



    # ── Courses ─────────────────────────────────────────────



    @app.route("/api/student/courses", methods=["GET"])

    def student_get_courses():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        courses = sdb.get_courses(_cid())

        result = []

        for c in courses:

            c = dict(c)

            c["analysis_json"] = json.loads(c["analysis_json"]) if isinstance(c["analysis_json"], str) else c["analysis_json"]

            result.append(c)

        return jsonify({"courses": result})



    @app.route("/api/student/courses/manual", methods=["POST"])

    def student_create_manual_course():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) or {}

        name = (data.get("name") or "").strip()

        if not name:

            return jsonify({"error": "name required"}), 400

        code = (data.get("code") or "").strip()

        term = (data.get("term") or "").strip()

        try:

            course_id = sdb.create_manual_course(_cid(), name, code, term)

            return jsonify({"ok": True, "course_id": course_id})

        except Exception as e:

            log.exception("create_manual_course failed")

            return jsonify({"error": str(e)}), 500



    @app.route("/api/student/courses/<int:course_id>", methods=["GET"])

    def student_get_course(course_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        course = dict(course)

        course["analysis_json"] = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else course["analysis_json"]

        return jsonify(course)



    # ── File uploads ────────────────────────────────────────



    @app.route("/api/student/courses/<int:course_id>/files", methods=["GET"])

    def student_get_course_files(course_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Not found"}), 404

        files = sdb.get_course_files(_cid(), course_id)

        return jsonify({"files": [dict(f) for f in files]})



    @app.route("/api/student/courses/<int:course_id>/upload", methods=["POST"])

    def student_upload_file(course_id):

        # File uploads are no longer supported. Courses are populated from Canvas

        # automatically and exams are tracked manually.

        return jsonify({"error": "File uploads to courses are disabled"}), 410



    @app.route("/api/student/files/<int:file_id>", methods=["GET"])

    def student_get_file_text(file_id):

        """Get the extracted text of an uploaded file for preview."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        cid = _cid()

        from student.db import get_db, _fetchone

        with get_db() as db:

            f = _fetchone(db, "SELECT id, original_name, file_type, extracted_text, uploaded_at FROM student_course_files WHERE id = %s AND client_id = %s",

                          (file_id, cid),

                          "SELECT id, original_name, file_type, extracted_text, uploaded_at FROM student_course_files WHERE id = ? AND client_id = ?")

        if not f:

            return jsonify({"error": "Not found"}), 404

        return jsonify({"id": f["id"], "name": f["original_name"], "type": f["file_type"], "text": f.get("extracted_text", ""), "uploaded_at": str(f.get("uploaded_at", ""))})



    @app.route("/api/student/files/<int:file_id>", methods=["DELETE"])

    def student_delete_file(file_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_course_file(file_id, _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/courses/<int:course_id>", methods=["DELETE"])

    def student_delete_course(course_id):

        """Remove a course and all its related data."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Not found"}), 404

        sdb.delete_course(course_id, _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/courses/<int:course_id>", methods=["PUT"])

    def student_update_course(course_id):

        """Update course info (name, code, grading, schedule, tips)."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Not found"}), 404

        data = request.get_json(force=True)

        sdb.update_course_info(

            course_id, _cid(),

            name=data.get("name", course["name"]),

            code=data.get("code", course.get("code", "")),

            grading=data.get("grading", {}),

            weekly_schedule=data.get("weekly_schedule", []),

            study_tips=data.get("study_tips", []),

        )

        return jsonify({"ok": True})



    # ── Exam CRUD ───────────────────────────────────────────



    @app.route("/api/student/courses/<int:course_id>/exams", methods=["GET"])

    def student_get_course_exams(course_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Not found"}), 404

        exams = sdb.get_course_exams(course_id)

        result = []

        for e in exams:

            e = dict(e)

            e["topics"] = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []

            result.append(e)

        return jsonify({"exams": result})



    @app.route("/api/student/courses/<int:course_id>/exams", methods=["POST"])

    def student_add_exam(course_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Not found"}), 404

        data = request.get_json(force=True)

        name = (data.get("name") or "").strip()

        if not name:

            return jsonify({"error": "Exam name is required"}), 400

        exam_id = sdb.upsert_exam(

            _cid(), course_id, None,

            name=name,

            exam_date=data.get("exam_date") or None,

            weight_pct=int(data.get("weight_pct", 0)),

            topics=data.get("topics", []),

        )

        return jsonify({"ok": True, "id": exam_id})



    @app.route("/api/student/exams/<int:exam_id>", methods=["PUT"])

    def student_update_exam(exam_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        name = (data.get("name") or "").strip()

        if not name:

            return jsonify({"error": "Exam name is required"}), 400

        sdb.upsert_exam(

            _cid(), data.get("course_id", 0), exam_id,

            name=name,

            exam_date=data.get("exam_date") or None,

            weight_pct=int(data.get("weight_pct", 0)),

            topics=data.get("topics", []),

        )

        return jsonify({"ok": True})



    @app.route("/api/student/exams/<int:exam_id>", methods=["DELETE"])

    def student_delete_exam(exam_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_exam(exam_id, _cid())

        return jsonify({"ok": True})



    # ── Exams (global list) ─────────────────────────────────



    @app.route("/api/student/exams", methods=["GET"])

    def student_get_exams():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        upcoming_only = request.args.get("upcoming", "true").lower() == "true"

        if upcoming_only:

            exams = sdb.get_upcoming_exams(_cid())

        else:

            exams = sdb.get_exams(_cid())



        result = []

        for e in exams:

            e = dict(e)

            e["topics"] = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []

            result.append(e)



        return jsonify({"exams": result})



    # ── Study plan (background) ──────────────────────────────



    _plan_status: dict[int, dict] = {}



    @app.route("/api/student/plan/generate", methods=["POST"])

    @limiter.limit("3 per minute")

    def student_generate_plan():

        """Kick off background study plan generation. Returns immediately."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        client_id = _cid()

        data = request.get_json(force=True) if request.is_json else {}

        preferences = data.get("preferences", {})



        # Gather all course analyses

        courses = sdb.get_courses(client_id)

        courses_data = []

        course_difficulties = {}

        for c in courses:

            analysis = json.loads(c["analysis_json"]) if isinstance(c["analysis_json"], str) else c["analysis_json"]

            if analysis and analysis.get("course_name"):

                # Override stale analysis exams with live student_exams (canonical source)

                # AND attach the FULL extracted text of every uploaded file per exam,

                # so the AI plans coverage of the entire study material — not just the topic list.

                try:

                    live_exams = sdb.get_course_exams(c["id"]) or []

                    merged_exams = []

                    for ex in live_exams:

                        topics = json.loads(ex["topics_json"]) if isinstance(ex.get("topics_json"), str) else (ex.get("topics_json") or [])

                        # Pull every file uploaded for THIS exam and concatenate its text

                        materials_text = ""

                        material_files = []

                        try:

                            ex_files = sdb.get_course_files(client_id, c["id"], exam_id=ex["id"]) or []

                            chunks = []

                            for f in ex_files:

                                txt = (f.get("extracted_text") or "").strip()

                                if not txt:

                                    continue

                                material_files.append(f.get("original_name", ""))

                                # Send only METADATA (page count + short preview) — the AI only needs the page count

                                # to assign reading segments. Sending the full PDF blows the context window.

                                import re as _re

                                m = _re.search(r"TOTAL PAGES:\s*(\d+)", txt)

                                page_count = m.group(1) if m else None

                                # Take the first 2000 chars as a topical preview (table of contents, intro)

                                preview = txt[:2000]

                                meta_block = f"=== FILE: {f.get('original_name','')} ==="

                                if page_count:

                                    meta_block += f"\nTOTAL PAGES: {page_count}"

                                else:

                                    # Estimate from char count if no markers (DOCX, etc.)

                                    est_pages = max(1, len(txt) // 3000)

                                    meta_block += f"\nESTIMATED PAGES: {est_pages} (no markers, estimated from text length)"

                                meta_block += f"\n--- FIRST 2000 CHARS PREVIEW (for topic/section context only) ---\n{preview}"

                                chunks.append(meta_block)

                            materials_text = "\n\n".join(chunks)

                        except Exception as _fe:

                            print(f"[plan] failed to load files for exam {ex.get('id')}: {_fe}")

                        merged_exams.append({

                            "id": ex.get("id"),

                            "name": ex.get("name", "Exam"),

                            "date": ex.get("exam_date"),

                            "weight_pct": ex.get("weight_pct", 0),

                            "topics": topics,

                            "material_files": material_files,

                            "materials": materials_text,

                        })

                    analysis["exams"] = merged_exams

                except Exception as _e:

                    print(f"[plan] failed to merge live exams for course {c['id']}: {_e}")

                courses_data.append(analysis)

                diff = c.get("difficulty", 3) or 3

                course_difficulties[analysis["course_name"]] = diff



        if not courses_data:

            return jsonify({"error": "No courses synced. Run /api/student/sync first."}), 400



        # Gather schedule settings

        schedule_settings = sdb.get_schedule_settings(client_id)

        schedule_list = []

        if schedule_settings:

            for s in schedule_settings:

                schedule_list.append({

                    "day": s["day_of_week"],

                    "hours": s["available_hours"],

                    "free": bool(s["is_free_day"]),

                })



        # Per-date availability overrides (next 30 days)

        from datetime import timedelta as _td

        _today_d = datetime.now().date()

        try:

            date_overrides_raw = sdb.get_date_overrides(

                client_id,

                _today_d.isoformat(),

                (_today_d + _td(days=30)).isoformat(),

            )

        except Exception:

            date_overrides_raw = []

        date_overrides_list = []

        for o in date_overrides_raw:

            date_overrides_list.append({

                "date": o.get("override_date"),

                "hours": o.get("available_hours", 0),

                "free": bool(o.get("is_free_day")),

                "note": o.get("note", "") or "",

            })



        # Gather incomplete assignments from previous days

        today_str = datetime.now().strftime("%Y-%m-%d")

        incomplete = sdb.get_incomplete_assignments(client_id, today_str)



        existing = _plan_status.get(client_id, {})

        if existing.get("status") == "running":

            return jsonify({"message": "Plan generation already in progress", "plan_status": existing})



        _plan_status[client_id] = {

            "status": "running",

            "progress": "Generating your study plan with AI...",

        }



        def _do_plan():

            try:

                # Pull Google Calendar events so the AI plans around real commitments
                try:
                    from outreach import gcal as _gcal
                    gcal_summary = _gcal.events_summary_for_ai(client_id, days_ahead=14)
                except Exception:
                    gcal_summary = ""

                plan = generate_study_plan(

                    courses_data, preferences,

                    schedule_settings=schedule_list or None,

                    course_difficulties=course_difficulties or None,

                    incomplete_assignments=incomplete or None,

                    calendar_events=gcal_summary,

                    date_overrides=date_overrides_list or None,

                )

                if not plan.get("daily_plan"):

                    raise ValueError("AI returned an empty plan")

                plan_id = sdb.save_study_plan(client_id, plan, preferences)

                _plan_status[client_id] = {

                    "status": "done",

                    "progress": "Study plan generated!",

                    "plan_id": plan_id,

                }

            except Exception as e:

                log.error("Plan generation failed for client %s: %s", client_id, e)

                _plan_status[client_id] = {

                    "status": "error",

                    "progress": f"Plan generation failed: {e}",

                }



        thread = threading.Thread(target=_do_plan, daemon=True)

        thread.start()



        return jsonify({"message": "Plan generation started", "plan_status": _plan_status[client_id]})



    @app.route("/api/student/plan/status", methods=["GET"])

    def student_plan_status():

        """Poll plan generation progress."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        status = _plan_status.get(_cid(), {"status": "idle"})

        return jsonify(status)



    @app.route("/api/student/plan", methods=["GET"])

    def student_get_plan():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        plan = sdb.get_latest_plan(_cid())

        if not plan:

            return jsonify({"error": "No plan generated yet"}), 404



        return jsonify(plan)



    @app.route("/api/student/plan/today", methods=["GET"])

    def student_get_today():

        """Get today's study sessions from the latest plan."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        plan_row = sdb.get_latest_plan(_cid())

        if not plan_row:

            return jsonify({"error": "No plan generated yet"}), 404



        today = datetime.now().strftime("%Y-%m-%d")

        plan_data = plan_row["plan_json"]

        daily_plan = plan_data.get("daily_plan", [])



        for day in daily_plan:

            if day.get("date") == today:

                return jsonify({"today": day, "upcoming_exams": plan_data.get("upcoming_exams", [])})



        return jsonify({"today": None, "message": "No study sessions scheduled for today"})



    # ── Progress ────────────────────────────────────────────



    @app.route("/api/student/progress/complete", methods=["POST"])

    def student_mark_complete():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        data = request.get_json(force=True) if request.is_json else {}

        plan_date = data.get("date", datetime.now().strftime("%Y-%m-%d"))

        notes = data.get("notes", "")



        sdb.mark_day_complete(_cid(), plan_date, notes)

        return jsonify({"message": f"Marked {plan_date} as complete"})



    @app.route("/api/student/stats", methods=["GET"])

    def student_get_stats():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        stats = sdb.get_study_stats(_cid())

        return jsonify(stats)



    # ── Focus / Pomodoro ────────────────────────────────────



    @app.route("/api/student/focus/save", methods=["POST"])
    def student_save_focus():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        cid = _cid()

        mode = data.get("mode", "pomodoro")

        try:
            minutes = int(data.get("minutes", 0))
        except (TypeError, ValueError):
            minutes = 0
        try:
            pages = int(data.get("pages", 0))
        except (TypeError, ValueError):
            pages = 0
        course_name = (data.get("course_name") or "").strip()
        # Optional structured tags — what the student is studying *for*
        # (course) and *which test* within that course (exam, optional).
        try:
            course_id = int(data.get("course_id") or 0) or None
        except (TypeError, ValueError):
            course_id = None
        try:
            exam_id = int(data.get("exam_id") or 0) or None
        except (TypeError, ValueError):
            exam_id = None
        phase_id = re.sub(r"[^A-Za-z0-9_.:-]", "", str(data.get("phase_id") or ""))[:80]

        # Backwards-compat: older clients still send only course_name. If we
        # have the name but not the id, look the id up so stats queries stay
        # exact (joins on course_id beat fragile note-string parsing).
        if course_name and not course_id:
            for _c in sdb.get_courses(cid):
                if _c["name"] == course_name:
                    course_id = int(_c["id"])
                    break

        # If course_id was resolved but the client didn't send a name, fill it
        # in from the DB so the `notes` string stays informative in the legacy
        # analytics widget that still reads it.
        if course_id and not course_name:
            for _c in sdb.get_courses(cid):
                if int(_c["id"]) == course_id:
                    course_name = _c["name"] or ""
                    break

        # Course is mandatory — "general study" was retired.
        if not course_name:
            return jsonify({"ok": False, "error": "Pick a course before starting the timer."}), 400

        # Defensive caps (real customers reported phantom hours from stale
        # localStorage / abandoned timers). Anything above 8h in a single save
        # is almost certainly a bug, not a real study session.
        if minutes < 0:
            return jsonify({"ok": False, "error": "Invalid minutes"}), 400
        if pages < 0:
            pages = 0
        if minutes > 480:
            minutes = 480

        # Drop empty saves entirely so we don't pollute the day.
        if minutes <= 0 and pages <= 0:
            return jsonify({"ok": True, "saved": False, "reason": "empty"})

        if phase_id:
            try:
                with sdb.get_db() as db:
                    already = sdb._fetchval(
                        db,
                        "SELECT id FROM student_xp WHERE client_id = %s AND action = 'focus_session' AND detail LIKE %s LIMIT 1",
                        (cid, f"%phase:{phase_id}%"),
                    )
                if already:
                    local_date = (request.args.get("local_date") or "").strip() or None
                    return jsonify({
                        "ok": True,
                        "saved": False,
                        "reason": "duplicate-phase",
                        "stats": sdb.get_focus_stats_today(cid, local_date=local_date),
                        "minutes_saved": 0,
                        "xp_awarded": 0,
                    })
            except Exception:
                pass

        # save_focus_session itself will further clamp by per-day total and
        # may return 0 if the day is already maxed out.
        saved_id = sdb.save_focus_session(
            cid, mode=mode, minutes=minutes, pages=pages,
            course_name=course_name, course_id=course_id, exam_id=exam_id,
        )
        if not saved_id:
            return jsonify({"ok": True, "saved": False, "reason": "daily-cap-or-empty"})

        saved_row = sdb.get_focus_session_entry(saved_id, cid) or {}
        try:
            minutes_saved = int(saved_row.get("focus_minutes") or minutes or 0)
        except (TypeError, ValueError):
            minutes_saved = minutes
        try:
            pages_saved = int(saved_row.get("pages_read") or pages or 0)
        except (TypeError, ValueError):
            pages_saved = pages



        # Daily-quest progress (focus minutes + sessions + pages)

        try:

            if minutes_saved > 0:

                sdb.progress_quests_by_metric(cid, "focus_minutes", minutes_saved)

                sdb.progress_quests_by_metric(cid, "sessions_completed", 1)

            if pages_saved > 0:

                sdb.progress_quests_by_metric(cid, "pages_read", pages_saved)

        except Exception:

            pass



        # Focus is the ONLY XP source now. 5 XP per 10 minutes of study.
        # Quizzes / flashcards / training give coins instead of XP, so the
        # only way to climb the ranks is to actually study.
        # Breaks aren't counted — `minutes` arrives study-only because the
        # frontend reports phaseWorkMinutes=0 for break phases.
        # No coins from focus on purpose: coins come from quizzes/flashcards
        # and the cosmetic grind, XP comes from focus. Two separate loops.

        _focus_xp_awarded = 0

        if minutes_saved > 0:

            xp = (minutes_saved * 5) // 10  # 25 min -> 12 XP, 50 min -> 25 XP

            detail = f"{mode.title()} {minutes_saved}min"

            if course_name:

                detail += f" — {course_name}"

            if phase_id:

                detail += f" · phase:{phase_id}"

            if xp > 0:

                sdb.award_xp(cid, "focus_session", xp, detail)

                _focus_xp_awarded = xp
            elif phase_id:

                # 0-XP micro phases still need a marker so phase-id dedupe
                # works on retries/refreshes. This does not affect total XP.
                sdb.award_xp(cid, "focus_session", 0, detail)



        _page_xp_awarded = 0

        if pages_saved > 0:

            page_xp = max(1, pages_saved)

            sdb.award_xp(cid, "pages_read", page_xp, f"Read {pages_saved} pages")

            _page_xp_awarded = page_xp



        # Auto-award focus badges

        stats = sdb.get_focus_stats(cid)

        total_hours = stats.get("total_hours", 0)

        if isinstance(total_hours, str):

            total_hours = float(total_hours.replace("h", "").replace(",", ""))

        else:

            total_hours = float(total_hours)

        if total_hours >= 1:

            sdb.earn_badge(cid, "focus_1h")

        if total_hours >= 10:

            sdb.earn_badge(cid, "focus_10h")

        if total_hours >= 50:

            sdb.earn_badge(cid, "focus_50h")

        if total_hours >= 100:

            sdb.earn_badge(cid, "focus_100h")

        total_pages = stats.get("total_pages", 0)

        if isinstance(total_pages, str):

            total_pages = int(total_pages)

        if total_pages >= 100:

            sdb.earn_badge(cid, "page_100")

        if total_pages >= 500:

            sdb.earn_badge(cid, "page_500")

        if total_pages >= 1000:

            sdb.earn_badge(cid, "page_1000")



        # Early bird / Night owl badges based on current time

        from datetime import datetime

        hour_now = datetime.now().hour

        if minutes_saved > 0:

            if hour_now < 7:

                sdb.earn_badge(cid, "early_bird")

            if hour_now >= 23:

                sdb.earn_badge(cid, "night_owl")



        # Promotion / demotion toasts intentionally disabled.
        # Leagues are XP-monotonic (no demotions) and the celebration toast
        # was distracting. Keep the response key for client back-compat.
        promotion = None



        # Frontend cards say "Horas hoy" / "Sesiones hoy" — return
        # today-scoped stats, not lifetime totals (lifetime `stats` above is
        # only used for badge thresholds).
        local_date = (request.args.get("local_date") or "").strip() or None
        today_stats = sdb.get_focus_stats_today(cid, local_date=local_date)

        return jsonify({
            "ok": True,
            "saved": True,
            "session_id": saved_id,
            "minutes_saved": minutes_saved,
            "pages_saved": pages_saved,
            "xp_awarded": _focus_xp_awarded + _page_xp_awarded,
            "stats": today_stats,
            "promotion": promotion,
        })


    @app.route("/api/student/focus/claim-adjust", methods=["POST"])
    def student_focus_claim_adjust():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) or {}

        cid = _cid()

        claim_id = re.sub(r"[^A-Za-z0-9_.:-]", "", str(data.get("claim_id") or ""))[:120]

        try:
            total_minutes = int(data.get("total_minutes") or 0)
        except (TypeError, ValueError):
            total_minutes = 0

        try:
            already_awarded = int(data.get("already_awarded") or 0)
        except (TypeError, ValueError):
            already_awarded = 0

        if not claim_id:
            return jsonify({"ok": False, "error": "Missing claim id"}), 400

        if total_minutes < 0:
            total_minutes = 0
        if total_minutes > 16 * 60:
            total_minutes = 16 * 60
        if already_awarded < 0:
            already_awarded = 0

        expected_xp = (total_minutes * 5) // 10
        extra_xp = max(0, expected_xp - already_awarded)
        detail = f"Claim total {total_minutes}min · claim:{claim_id}"

        try:
            with sdb.get_db() as db:
                existing = sdb._fetchval(
                    db,
                    "SELECT id FROM student_xp WHERE client_id = %s AND action = 'focus_claim_adjustment' AND detail LIKE %s LIMIT 1",
                    (cid, f"%claim:{claim_id}%"),
                )
            if existing:
                local_date = (request.args.get("local_date") or "").strip() or None
                return jsonify({
                    "ok": True,
                    "saved": False,
                    "reason": "duplicate-claim-adjustment",
                    "xp_awarded": 0,
                    "stats": sdb.get_focus_stats_today(cid, local_date=local_date),
                })
        except Exception:
            pass

        if extra_xp > 0:
            sdb.award_xp(cid, "focus_claim_adjustment", extra_xp, detail)

        local_date = (request.args.get("local_date") or "").strip() or None
        return jsonify({
            "ok": True,
            "saved": bool(extra_xp),
            "xp_awarded": extra_xp,
            "expected_xp": expected_xp,
            "stats": sdb.get_focus_stats_today(cid, local_date=local_date),
        })



    @app.route("/api/student/focus/stats", methods=["GET"])

    def student_focus_stats():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        local_date = (request.args.get("local_date") or "").strip() or None

        return jsonify(sdb.get_focus_stats_today(_cid(), local_date=local_date))


    # ── Study-time breakdown (dashboard bar chart + drill-downs) ──
    #
    # Three endpoints back the interactive "time per course" card:
    #   GET /api/student/stats/per_course
    #     → one bar per course, lifetime minutes.
    #
    #   GET /api/student/stats/course_detail?course_id=X[&week_offset=N]
    #     → picked a course from the chart:
    #         · per-day-of-week bars for the ISO week at `week_offset`
    #         · per-exam bars (lifetime minutes for each exam of this course)
    #
    #   GET /api/student/stats/exam_detail?exam_id=X[&week_offset=N]
    #     → picked an exam from the drill-down: per-day-of-week bars.
    #
    # week_offset: 0 = current ISO week, -1 = last, etc. Future offsets clamped.

    @app.route("/api/student/stats/per_course", methods=["GET"])
    def student_stats_per_course():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        cid = _cid()
        rows = sdb.get_time_per_course(cid)
        return jsonify({
            "courses": [
                {"course_id": int(r["course_id"]),
                 "name": r["course_name"],
                 "minutes": int(r["minutes"] or 0),
                 "sessions": int(r["sessions"] or 0)}
                for r in rows
            ],
        })

    @app.route("/api/student/stats/course_detail", methods=["GET"])
    def student_stats_course_detail():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        cid = _cid()
        try:
            course_id = int(request.args.get("course_id") or 0)
        except (TypeError, ValueError):
            course_id = 0
        if not course_id:
            return jsonify({"error": "course_id required"}), 400
        try:
            week_offset = int(request.args.get("week_offset") or 0)
        except (TypeError, ValueError):
            week_offset = 0
        if week_offset > 0:
            week_offset = 0

        course = sdb.get_course(course_id)
        if not course or int(course["client_id"]) != int(cid):
            return jsonify({"error": "Not found"}), 404

        week = sdb.get_course_week(cid, course_id, week_offset=week_offset)
        exams = sdb.get_time_per_exam(cid, course_id)
        return jsonify({
            "course": {"id": int(course["id"]), "name": course.get("name", "")},
            "week_offset": week_offset,
            "week_start": week["week_start"],
            "week_end": week["week_end"],
            "days": week["days"],
            "exams": [
                {"exam_id": int(e["exam_id"]),
                 "name": e["exam_name"],
                 "exam_date": e.get("exam_date") or "",
                 "minutes": int(e["minutes"] or 0),
                 "sessions": int(e["sessions"] or 0)}
                for e in exams
            ],
        })

    @app.route("/api/student/stats/exam_detail", methods=["GET"])
    def student_stats_exam_detail():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        cid = _cid()
        try:
            exam_id = int(request.args.get("exam_id") or 0)
        except (TypeError, ValueError):
            exam_id = 0
        if not exam_id:
            return jsonify({"error": "exam_id required"}), 400
        try:
            week_offset = int(request.args.get("week_offset") or 0)
        except (TypeError, ValueError):
            week_offset = 0
        if week_offset > 0:
            week_offset = 0

        from outreach.db import get_db, _fetchone
        with get_db() as db:
            erow = _fetchone(
                db,
                "SELECT e.id, e.name, e.exam_date, e.course_id, c.name AS course_name "
                "FROM student_exams e JOIN student_courses c ON c.id = e.course_id "
                "WHERE e.id = %s AND e.client_id = %s",
                (exam_id, cid),
            )
        if not erow:
            return jsonify({"error": "Not found"}), 404

        week = sdb.get_exam_week(cid, exam_id, week_offset=week_offset)
        return jsonify({
            "exam": {
                "id": int(erow["id"]),
                "name": erow.get("name") or "Exam",
                "exam_date": erow.get("exam_date") or "",
                "course_id": int(erow["course_id"]),
                "course_name": erow.get("course_name") or "",
            },
            "week_offset": week_offset,
            "week_start": week["week_start"],
            "week_end": week["week_end"],
            "days": week["days"],
        })



    # ── Dashboard data (single call for the frontend) ──────



    @app.route("/api/student/dashboard", methods=["GET"])

    def student_dashboard():

        """All-in-one endpoint for the student dashboard."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401



        # Lazy: run weekly/monthly leaderboard payouts if a boundary just passed.

        try:

            from student.leaderboard_prizes import run_payouts_if_due

            run_payouts_if_due()

        except Exception:

            pass



        cid = _cid()

        canvas_tok = sdb.get_canvas_token(cid)

        courses = sdb.get_courses(cid)

        exams = sdb.get_upcoming_exams(cid)

        stats = sdb.get_study_stats(cid)

        plan_row = sdb.get_latest_plan(cid)



        # Today's plan

        today_plan = None

        if plan_row:

            today_str = datetime.now().strftime("%Y-%m-%d")

            for day in plan_row["plan_json"].get("daily_plan", []):

                if day.get("date") == today_str:

                    today_plan = day

                    break



        # Parse exam topics

        exam_list = []

        for e in exams:

            e = dict(e)

            e["topics"] = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []

            days_until = None

            if e.get("exam_date"):

                try:

                    ed = datetime.strptime(e["exam_date"], "%Y-%m-%d").date()

                    days_until = (ed - datetime.now().date()).days

                except ValueError:

                    pass

            e["days_until"] = days_until

            exam_list.append(e)



        return jsonify({

            "canvas_connected": bool(canvas_tok),

            "courses": [{"id": c["id"], "name": c["name"], "code": c.get("code", "")} for c in courses],

            "upcoming_exams": exam_list,

            "stats": stats,

            "today": today_plan,

            "has_plan": plan_row is not None,

            "recommendations": plan_row["plan_json"].get("recommendations", []) if plan_row else [],

        })



    # ── Leaderboard pop-up endpoints ────────────────────────

    @app.route("/api/student/period/results", methods=["GET"])
    def student_period_results():
        """Return any closed-period leaderboard summaries the user
        hasn't acknowledged yet (week + month). One popup per period."""
        if not _logged_in():
            return jsonify({"periods": []}), 401
        try:
            from student.leaderboard_prizes import get_pending_period_results
            periods = get_pending_period_results(_cid())
        except Exception:
            periods = []
        return jsonify({"periods": periods})

    @app.route("/api/student/period/ack", methods=["POST"])
    def student_period_ack():
        """Mark one (period_kind, period_key) summary as seen so the
        popup never shows again for this user/period."""
        if not _logged_in():
            return jsonify({"ok": False}), 401
        try:
            data = request.get_json(silent=True) or {}
            kind = (data.get("period_kind") or "").strip()
            key = (data.get("period_key") or "").strip()
            if kind not in ("week", "month") or not key:
                return jsonify({"ok": False, "error": "bad period"}), 400
            from student.leaderboard_prizes import mark_period_seen
            mark_period_seen(_cid(), kind, key)
        except Exception:
            return jsonify({"ok": False}), 500
        return jsonify({"ok": True})


    # ── Grade-Sheet semester sync ────────────────────────────

    @app.route("/api/student/semester/current", methods=["GET", "POST"])
    def student_semester_current():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        cid = _cid()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            label = (data.get("label") or "").strip()
            if not label:
                return jsonify({"error": "label required"}), 400
            sdb.set_current_semester(cid, label)
            return jsonify({"ok": True, "current": sdb.get_current_semester(cid)})
        return jsonify({"current": sdb.get_current_semester(cid)})


    @app.route("/api/student/courses/by-semester", methods=["GET"])
    def student_courses_by_semester():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({
            "current": sdb.get_current_semester(_cid()),
            "semesters": sdb.get_courses_by_semester(_cid()),
        })


    # ── Frontend pages ──────────────────────────────────────



    def _s_render(title, content_html, active_page="student_dashboard"):

        """Render a student page using MachReach's LAYOUT."""

        from app import ADMIN_EMAILS, LAYOUT

        from outreach.db import get_client

        from outreach.i18n import SPANISH_TO_EN_VISIBLE, t, t_dict, translate_student_html_fragment

        flashed = list(session.pop("_flashes", []) if "_flashes" in session else [])

        nav = t_dict("nav")
        student_ui = t_dict("student_ui")

        is_admin = False

        if _logged_in():

            c = get_client(session["client_id"])

            email = (c.get("email") or "").strip().lower() if c else ""

            owner_emails = {e.strip().lower() for e in ADMIN_EMAILS}
            owner_emails.add("ignaciomachuca2005@gmail.com")
            is_admin = bool(c and c.get("is_admin")) or email in owner_emails

        # End-of-week / end-of-month leaderboard results popup. Shows once
        # per period to every authenticated student.
        period_popup_html = _PERIOD_POPUP_HTML if _logged_in() else ""
        lang = session.get("lang", "es")
        rendered_content = translate_student_html_fragment(period_popup_html + content_html, lang)

        return render_template_string(

            LAYOUT,

            title=f"Student — {title}",

            content=Markup(rendered_content),

            logged_in=_logged_in(),

            messages=flashed,

            active_page=active_page,

            client_name=session.get("client_name", ""),

            wide=False,

            nav=nav,
            student_ui=student_ui,
            student_en_visible=SPANISH_TO_EN_VISIBLE,
            tr=t,

            lang=lang,

            is_admin=is_admin,

            account_type="student",

        )


    @app.route("/dev/cosmetics")
    def dev_cosmetics_preview_page():
        """Local-only preview of every profile banner and leaderboard flag."""
        host = (request.host or "").split(":")[0].lower()
        remote = (request.remote_addr or "").lower()
        if host not in {"127.0.0.1", "localhost", "::1"} and remote not in {"127.0.0.1", "::1"}:
            return "Local preview only", 403

        banners = [
            {
                "key": key,
                "name": cfg.get("name", key),
                "css": cfg.get("css", "transparent"),
                "animated": bool(cfg.get("animated")),
                "anim_class": cfg.get("anim_class", "") if cfg.get("animated") else "",
                "price": cfg.get("price_coins", 0),
                "xp": cfg.get("xp_required", 0),
                "plus": bool(cfg.get("plus_only")),
            }
            for key, cfg in sdb.BANNERS.items()
        ]
        flags = [
            {
                "key": key,
                "name": cfg.get("name", key),
                "css": cfg.get("css", "transparent"),
                "animated": bool(cfg.get("animated")),
                "anim_class": cfg.get("anim_class", "") if cfg.get("animated") else "",
                "price": cfg.get("price_coins", 0),
                "xp": cfg.get("xp_required", 0),
                "plus": bool(cfg.get("plus_only")),
            }
            for key, cfg in sdb.FLAGS.items()
        ]
        return render_template_string("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MachReach Cosmetics Preview</title>
  <style>{{ anim_css|safe }}</style>
  <style>
    :root { --bg:#F4F1EA; --paper:#FFFDF8; --ink:#1A1A1F; --muted:#77756F; --line:#E2DCCC; --orange:#FF7A3D; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:"Plus Jakarta Sans",system-ui,-apple-system,Segoe UI,sans-serif; }
    .wrap { max-width:1280px; margin:0 auto; padding:34px 28px 80px; }
    .top { display:flex; justify-content:space-between; gap:20px; align-items:end; flex-wrap:wrap; margin-bottom:26px; }
    .eyebrow { color:var(--orange); font-size:12px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; margin-bottom:8px; }
    h1 { margin:0; font-family:Georgia,serif; font-size:clamp(44px,6vw,78px); line-height:.92; letter-spacing:-.05em; font-weight:600; }
    .sub { color:var(--muted); margin:12px 0 0; max-width:720px; line-height:1.55; }
    .tabs { display:flex; gap:10px; position:sticky; top:0; background:rgba(244,241,234,.9); backdrop-filter:blur(10px); padding:12px 0; z-index:5; }
    .tabs a { text-decoration:none; color:var(--ink); border:1px solid var(--line); background:#fff; padding:10px 16px; border-radius:999px; font-weight:900; }
    section { margin-top:30px; }
    h2 { font-family:Georgia,serif; font-size:34px; letter-spacing:-.035em; margin:0 0 16px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; }
    .card { background:#fff; border:1px solid var(--line); border-radius:18px; overflow:hidden; box-shadow:0 1px 0 rgba(20,18,30,.04),0 12px 32px rgba(20,18,30,.06); }
    .banner-preview { height:118px; position:relative; overflow:hidden; background:#111827; }
    .flag-preview { height:84px; position:relative; overflow:hidden; background:#111827; }
    .flag-fade { position:absolute; inset:0; -webkit-mask-image:linear-gradient(to right, rgba(0,0,0,.95) 0%, rgba(0,0,0,.65) 44%, rgba(0,0,0,.18) 76%, transparent 100%); mask-image:linear-gradient(to right, rgba(0,0,0,.95) 0%, rgba(0,0,0,.65) 44%, rgba(0,0,0,.18) 76%, transparent 100%); }
    .meta { padding:13px 14px 15px; }
    .name { font-weight:950; margin-bottom:5px; }
    .key { color:var(--muted); font-size:12px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; word-break:break-all; }
    .chips { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
    .chip { border:1px solid var(--line); border-radius:999px; padding:4px 8px; color:var(--muted); font-size:11px; font-weight:850; background:var(--paper); }
    .chip.hot { color:#9A3412; border-color:#FDBA74; background:#FFF7ED; }
  </style>
</head>
<body>
  <main class="wrap">
    <div class="top">
      <div>
        <div class="eyebrow">Local preview</div>
        <h1>Cosmetics<br>gallery.</h1>
        <p class="sub">Every MachReach profile banner and leaderboard flag, using the real CSS and animation classes from the app.</p>
      </div>
      <div class="tabs">
        <a href="#banners">Banners · {{ banners|length }}</a>
        <a href="#flags">Flags · {{ flags|length }}</a>
      </div>
    </div>

    <section id="banners">
      <h2>Profile banners</h2>
      <div class="grid">
        {% for item in banners %}
        <article class="card">
          <div class="banner-preview bnr-anim-host {{ item.anim_class }}" style="background:{{ item.css }};"></div>
          <div class="meta">
            <div class="name">{{ item.name }}</div>
            <div class="key">{{ item.key }}</div>
            <div class="chips">
              {% if item.animated %}<span class="chip hot">animated</span>{% endif %}
              {% if item.plus %}<span class="chip hot">PLUS</span>{% endif %}
              <span class="chip">{{ item.price }} coins</span>
              <span class="chip">{{ item.xp }} XP</span>
            </div>
          </div>
        </article>
        {% endfor %}
      </div>
    </section>

    <section id="flags">
      <h2>Leaderboard flags</h2>
      <div class="grid">
        {% for item in flags %}
        <article class="card">
          <div class="flag-preview">
            <div class="flag-fade {{ item.anim_class }}" style="background:{{ item.css }};"></div>
          </div>
          <div class="meta">
            <div class="name">{{ item.name }}</div>
            <div class="key">{{ item.key }}</div>
            <div class="chips">
              {% if item.animated %}<span class="chip hot">animated</span>{% endif %}
              {% if item.plus %}<span class="chip hot">PLUS</span>{% endif %}
              <span class="chip">{{ item.price }} coins</span>
              <span class="chip">{{ item.xp }} XP</span>
            </div>
          </div>
        </article>
        {% endfor %}
      </div>
    </section>
  </main>
</body>
</html>
        """, banners=banners, flags=flags, anim_css=Markup(sdb.BANNER_ANIM_CSS + "\n" + sdb.FLAG_ANIM_CSS))



    @app.route("/student/setup")
    def student_setup_page():
        """Mandatory onboarding — students cannot use any feature
        until they pick country / university / major."""
        if not _logged_in():
            return redirect(url_for("login"))
        if session.get("account_type") != "student":
            return redirect(url_for("dashboard"))
        try:
            from student import academic as _ac
            if not _ac.needs_setup(_cid()):
                return redirect(url_for("student_dashboard_page"))
        except Exception:
            pass
        body = """
        <style>
          .ss-wrap { max-width: 560px; margin: 60px auto; }
          .ss-card { background: var(--card, #fff); border: 1px solid var(--border, #e5e7eb); border-radius: 18px; padding: 32px; }
          .ss-h1 { font-size: 26px; font-weight: 800; margin: 0 0 6px; }
          .ss-sub { color: var(--text-muted, #6b7280); font-size: 14px; margin: 0 0 20px; }
          .ss-step { display: none; }
          .ss-step.active { display: block; }
          .ss-label { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--text-muted, #6b7280); font-weight: 700; margin-bottom: 6px; }
          .ss-input, .ss-select { width: 100%; padding: 12px 14px; border: 1px solid var(--border, #e5e7eb); border-radius: 10px; background: var(--bg, #fff); color: var(--text, #111827); font-size: 14px; box-sizing: border-box; }
          .ss-list { max-height: 240px; overflow-y: auto; border: 1px solid var(--border, #e5e7eb); border-radius: 10px; margin-top: 8px; background: var(--bg, #fff); }
          .ss-item { padding: 10px 14px; border-bottom: 1px solid var(--border, #e5e7eb); cursor: pointer; font-size: 14px; }
          .ss-item:last-child { border-bottom: none; }
          .ss-item:hover { background: rgba(99,102,241,.08); }
          .ss-item.create { color: var(--primary, #6366f1); font-weight: 600; }
          .ss-actions { display: flex; justify-content: space-between; gap: 10px; margin-top: 22px; }
          .ss-pill { display: inline-block; padding: 4px 10px; background: rgba(99,102,241,.1); color: var(--primary, #6366f1); border-radius: 999px; font-size: 12px; font-weight: 600; margin-bottom: 14px; }
          .ss-err { color: #ef4444; font-size: 12px; margin-top: 6px; min-height: 16px; }
        </style>
        <div class="ss-wrap">
          <div class="ss-card">
            <span class="ss-pill" id="ss-progress">Step 1 of 3</span>
            <h1 class="ss-h1">Bienvenido a MachReach Student</h1>
            <p class="ss-sub">We need three quick things so we can rank you on the right leaderboards and tailor your study plan. This is required.</p>

            <div class="ss-step active" id="ss-step-0">
              <div class="ss-label">Tu país</div>
              <select class="ss-select" id="ss-country">
                <option value="">- Pick your country -</option>
              </select>
            </div>

            <div class="ss-step" id="ss-step-1">
              <div class="ss-label">Tu universidad</div>
              <input class="ss-input" id="ss-univ-q" type="text" placeholder="Search universities..." autocomplete="off">
              <div class="ss-list" id="ss-univ-list"></div>
            </div>

            <div class="ss-step" id="ss-step-2">
              <div class="ss-label">Tu carrera</div>
              <input class="ss-input" id="ss-major-q" type="text" placeholder="Search majors..." autocomplete="off">
              <div class="ss-list" id="ss-major-list"></div>
            </div>

            <div class="ss-err" id="ss-err"></div>
            <div class="ss-actions">
              <button class="btn btn-outline" id="ss-back" disabled>Back</button>
              <button class="btn btn-primary" id="ss-next">Next</button>
            </div>
          </div>
        </div>

        <script>
        (function() {
          const state = { step: 0, country_iso: '', university_id: null, university_name: '', major_id: null, major_name: '' };
          const stepEls = [0,1,2].map(i => document.getElementById('ss-step-' + i));
          const progress = document.getElementById('ss-progress');
          const back = document.getElementById('ss-back');
          const next = document.getElementById('ss-next');
          const err  = document.getElementById('ss-err');

          function escapeHtml(s) {
            return String(s||'').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
          }
          function show(i) {
            state.step = i;
            stepEls.forEach((el, idx) => el.classList.toggle('active', idx === i));
            progress.textContent = 'Step ' + (i + 1) + ' of 3';
            back.disabled = (i === 0);
            next.textContent = (i === 2) ? 'Finish' : 'Next';
            err.textContent = '';
          }

          async function loadCountries() {
            try {
              const r = await fetch('/api/academic/countries').then(r=>r.json());
              const sel = document.getElementById('ss-country');
              (r.countries || []).forEach(c => {
                const o = document.createElement('option');
                const iso = c.iso_code || c.iso || '';
                const flag = c.flag_emoji || c.flag || '';
                o.value = iso;
                o.textContent = (flag ? (flag + ' ') : '') + (c.name || iso);
                sel.appendChild(o);
              });
              sel.addEventListener('change', () => { state.country_iso = sel.value; });
            } catch(e) { err.textContent = 'Could not load countries.'; }
          }

          let univTimer = null;
          document.getElementById('ss-univ-q').addEventListener('input', e => {
            clearTimeout(univTimer);
            const q = e.target.value.trim();
            univTimer = setTimeout(() => searchUniv(q), 220);
          });
          async function searchUniv(q) {
            const list = document.getElementById('ss-univ-list');
            if (!state.country_iso) { list.innerHTML = '<div class="ss-item">Pick a country first.</div>'; return; }
            const r = await fetch('/api/academic/universities?country=' + state.country_iso + '&q=' + encodeURIComponent(q)).then(r=>r.json());
            const items = (r.universities || []).map(u =>
              '<div class="ss-item" data-id="' + u.id + '" data-name="' + escapeHtml(u.name) + '">' + escapeHtml(u.name) + '</div>'
            ).join('');
            const create = q.length >= 2 ? '<div class="ss-item create" data-create="' + escapeHtml(q) + '">+ Add &quot;' + escapeHtml(q) + '&quot;</div>' : '';
            list.innerHTML = items + create || '<div class="ss-item">Type to search...</div>';
            list.querySelectorAll('.ss-item[data-id]').forEach(el => {
              el.addEventListener('click', () => pickUniv(parseInt(el.dataset.id), el.dataset.name));
            });
            list.querySelectorAll('.ss-item[data-create]').forEach(el => {
              el.addEventListener('click', () => createUniv(el.dataset.create));
            });
          }
          function pickUniv(id, name) {
            state.university_id = id; state.university_name = name;
            document.getElementById('ss-univ-q').value = name;
            document.getElementById('ss-univ-list').innerHTML = '<div class="ss-item">&#10003; ' + escapeHtml(name) + '</div>';
          }
          async function createUniv(name) {
            const r = await fetch('/api/academic/universities', {
              method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({name: name, country_iso: state.country_iso})
            }).then(r=>r.json());
            if (!r.ok) { err.textContent = r.error || 'Could not create university.'; return; }
            pickUniv(r.university.id, r.university.name);
          }

          let majTimer = null;
          document.getElementById('ss-major-q').addEventListener('input', e => {
            clearTimeout(majTimer);
            const q = e.target.value.trim();
            majTimer = setTimeout(() => searchMajor(q), 220);
          });
          async function searchMajor(q) {
            const list = document.getElementById('ss-major-list');
            const url = '/api/academic/majors?q=' + encodeURIComponent(q) + (state.university_id ? '&university_id=' + state.university_id : '');
            const r = await fetch(url).then(r=>r.json());
            const items = (r.majors || []).map(m =>
              '<div class="ss-item" data-id="' + m.id + '" data-name="' + escapeHtml(m.name) + '">' + escapeHtml(m.name) + '</div>'
            ).join('');
            const create = q.length >= 2 ? '<div class="ss-item create" data-create="' + escapeHtml(q) + '">+ Add &quot;' + escapeHtml(q) + '&quot;</div>' : '';
            list.innerHTML = items + create || '<div class="ss-item">Type to search...</div>';
            list.querySelectorAll('.ss-item[data-id]').forEach(el => {
              el.addEventListener('click', () => pickMajor(parseInt(el.dataset.id), el.dataset.name));
            });
            list.querySelectorAll('.ss-item[data-create]').forEach(el => {
              el.addEventListener('click', () => createMajor(el.dataset.create));
            });
          }
          function pickMajor(id, name) {
            state.major_id = id; state.major_name = name;
            document.getElementById('ss-major-q').value = name;
            document.getElementById('ss-major-list').innerHTML = '<div class="ss-item">&#10003; ' + escapeHtml(name) + '</div>';
          }
          async function createMajor(name) {
            const r = await fetch('/api/academic/majors', {
              method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({name: name, university_id: state.university_id})
            }).then(r=>r.json());
            if (!r.ok) { err.textContent = r.error || 'Could not create major.'; return; }
            pickMajor(r.major.id, r.major.name);
          }

          back.addEventListener('click', () => { if (state.step > 0) show(state.step - 1); });
          next.addEventListener('click', async () => {
            if (state.step === 0 && !state.country_iso) { err.textContent = 'Pick your country.'; return; }
            if (state.step === 1 && !state.university_id) { err.textContent = 'Pick or create your university.'; return; }
            if (state.step === 2 && !state.major_id) { err.textContent = 'Pick or create your major.'; return; }
            if (state.step < 2) { show(state.step + 1); return; }
            next.disabled = true; next.textContent = 'Saving...';
            const r = await fetch('/api/academic/profile', {
              method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({
                country_iso: state.country_iso,
                university_id: state.university_id,
                major_id: state.major_id,
              }),
            }).then(r=>r.json());
            if (!r.ok) { err.textContent = r.error || 'Save failed.'; next.disabled = false; next.textContent = 'Finish'; return; }
            mrGo('/student');
          });

          loadCountries();
          show(0);
        })();
        </script>
        """
        return _s_render("Set up your account", body, active_page="student_setup")


    @app.route("/student")

    def student_dashboard_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        _lang = session.get("lang", "es")

        canvas_tok = sdb.get_canvas_token(cid)

        courses = sdb.get_courses(cid)

        exams = sdb.get_upcoming_exams(cid)

        stats = sdb.get_study_stats(cid)

        focus_stats = sdb.get_focus_stats(cid)

        plan_row = sdb.get_latest_plan(cid)



        # Today's plan

        today_plan = None

        today_sessions_html = ""

        today_assignment_progress = {}

        if plan_row:

            today_str = datetime.now().strftime("%Y-%m-%d")

            for day in plan_row["plan_json"].get("daily_plan", []):

                if day.get("date") == today_str:

                    today_plan = day

                    break

            if today_plan:

                ap = sdb.get_assignment_progress(cid, today_str)

                today_assignment_progress = {r["session_index"]: bool(r["completed"]) for r in ap}



        if today_plan:

            sessions = today_plan.get("sessions", [])

            for idx, s in enumerate(sessions):

                prio_map = {

                    "high": ("var(--red)", "var(--red-light)", "#991B1B"),

                    "medium": ("var(--yellow)", "var(--yellow-light)", "#92400E"),

                    "low": ("var(--green)", "var(--green-light)", "var(--green-dark)"),

                }

                pc, pc_bg, pc_fg = prio_map.get(s.get("priority", "medium"), ("var(--text-muted)", "var(--border-light)", "var(--text-secondary)"))

                checked = "checked" if today_assignment_progress.get(idx, False) else ""

                strike_cls = "strike-done" if today_assignment_progress.get(idx, False) else ""

                today_sessions_html += f"""

                <div class="{strike_cls}" style="background:var(--card);border:1px solid var(--border);border-left:4px solid {pc};border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;transition:all 0.25s;" id="dash-session-{idx}">

                  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">

                    <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;">

                      <input type="checkbox" {checked} onchange="toggleDashAssignment({idx},this.checked)"

                        style="width:18px;height:18px;cursor:pointer;accent-color:var(--primary);flex-shrink:0;">

                      <span style="font-weight:700;color:var(--text);">{_esc(s.get('course',''))}</span>

                      <span style="color:var(--text-muted);font-size:13px;">{s.get('hours',0)}h &middot; {s.get('type','study')}</span>

                    </div>

                    <span style="background:{pc_bg};color:{pc_fg};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700;">{s.get('priority','').upper()}</span>

                  </div>

                  <div style="color:var(--text-secondary);font-size:14px;margin-top:6px;margin-left:28px;">{_esc(s.get('topic',''))}</div>

                  {"<div style='color:var(--text-muted);font-size:12px;margin-top:4px;margin-left:28px;font-style:italic;'>" + _esc(s.get('reason','')) + "</div>" if s.get('reason') else ""}

                </div>"""

        else:

            today_sessions_html = """

            <div class="empty-state compact reveal">

              <div class="empty-icon">&#128218;</div>

              <h3>Aún no hay sesiones registradas hoy</h3>

              <p>Start a Focus session to log study time — your dashboard will show the breakdown.</p>

              <div class="empty-actions">

                <a href="/student/focus" class="primary">&#127919; Start Focus session</a>

              </div>

            </div>"""



        # Upcoming exams HTML

        exams_html = ""

        for e in exams[:5]:

            days_until = None

            if e.get("exam_date"):

                try:

                    ed = datetime.strptime(e["exam_date"], "%Y-%m-%d").date()

                    days_until = (ed - datetime.now().date()).days

                except ValueError:

                    pass

            urg_map = {

                "red": ("var(--red)", "var(--red-light)", "#991B1B"),

                "yellow": ("var(--yellow)", "var(--yellow-light)", "#92400E"),

                "green": ("var(--green)", "var(--green-light)", "var(--green-dark)"),

            }

            if days_until is not None and days_until <= 7:

                u_border, u_bg, u_fg = urg_map["red"]

            elif days_until is not None and days_until <= 14:

                u_border, u_bg, u_fg = urg_map["yellow"]

            else:

                u_border, u_bg, u_fg = urg_map["green"]

            topics = json.loads(e["topics_json"]) if isinstance(e.get("topics_json"), str) else []

            topics_str = ", ".join(topics[:3]) + ("..." if len(topics) > 3 else "") if topics else "No topics listed"

            exams_html += f"""

            <div style="background:var(--card);border:1px solid var(--border);border-left:3px solid {u_border};border-radius:var(--radius-sm);padding:14px 18px;margin-bottom:10px;transition:all 0.25s;" onmouseover="this.style.transform='translateX(3px)';this.style.boxShadow='var(--shadow-md)'" onmouseout="this.style.transform='';this.style.boxShadow=''">

              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">

                <div>

                  <span style="font-weight:700;">{_esc(e.get('course_name',''))}</span>

                  <span style="color:var(--text-muted);font-size:13px;margin-left:6px;">&middot; {_esc(e.get('name','Exam'))}</span>

                </div>

                <span style="background:{u_bg};color:{u_fg};padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;">

                  {str(days_until) + 'd' if days_until is not None else '?'} left

                </span>

              </div>

              <div style="color:var(--text-muted);font-size:13px;margin-top:4px;">

                {e.get('exam_date', 'TBD')} &middot; {e.get('weight_pct', 0)}% of grade &middot; {_esc(topics_str)}

              </div>

            </div>"""



        if not exams_html:

            exams_html = """<div class="empty-state compact reveal"><div class="empty-icon">&#128221;</div><h3>Sin pruebas próximas</h3><p>Agrega una evaluación desde cualquier curso y aparecerá aquí, ordenada por urgencia.</p><div class="empty-actions"><a href="/student/exams" class="primary">&#128221; Administrar pruebas</a></div></div>"""



        canvas_status = "Conectado" if canvas_tok else "Sin conectar"

        canvas_color = "#10B981" if canvas_tok else "#EF4444"



        # Gamification

        total_xp = sdb.get_total_xp(cid)

        level_name, level_floor, level_ceil = sdb.get_level(total_xp)

        xp_pct = min(100, int(100 * (total_xp - level_floor) / max(1, level_ceil - level_floor)))

        streak_days = sdb.get_streak_days(cid)

        # Auto-award login badge

        sdb.earn_badge(cid, "first_login")



        # Compact analytics for dashboard

        _total_mins = int((focus_stats or {}).get("total_minutes", 0) or 0)

        _total_sessions = int((focus_stats or {}).get("sessions", 0) or 0)

        _avg_session = (_total_mins // _total_sessions) if _total_sessions else 0

        _streak_focus = int((focus_stats or {}).get("streak_days", 0) or 0)

        # ── Claude Design "Inicio" data prep ────────────────────
        # Daily quests
        try:
            _mr_quests = sdb.get_or_create_daily_quests(cid) or []
        except Exception:
            _mr_quests = []

        # Friends + leaderboard preview
        try:
            _mr_friends_data = sdb.list_friends(cid) or {}
        except Exception:
            _mr_friends_data = {}
        _mr_friends = (_mr_friends_data.get("friends") or [])[:4]

        try:
            _mr_lb_rows = sdb.get_leaderboard(limit=50) or []
        except Exception:
            _mr_lb_rows = []
        try:
            _mr_my_rank = int(sdb.get_student_rank(cid) or 0)
        except Exception:
            _mr_my_rank = 0

        _mr_palette = ["#FF7B95", "#7DA0FF", "#A593FF", "#5DE3B0",
                       "#FFB37A", "#9CD9F0", "#FFC857", "#FF89B5"]

        def _mr_initials(name: str) -> str:
            parts = (name or "").strip().split()
            if not parts:
                return "··"
            if len(parts) == 1:
                return parts[0][:2].upper()
            return (parts[0][:1] + parts[-1][:1]).upper()

        # Build small board: top 5 if I'm there, else 2 above me + me + 2 below
        _mr_me_idx = next((i for i, r in enumerate(_mr_lb_rows)
                           if int(r.get("client_id") or 0) == cid), -1)
        if _mr_me_idx < 0:
            _mr_board = _mr_lb_rows[:5]
            _mr_board_offset = 0
        elif _mr_me_idx < 5:
            _mr_board = _mr_lb_rows[:5]
            _mr_board_offset = 0
        else:
            _start = max(0, _mr_me_idx - 2)
            _end = min(len(_mr_lb_rows), _start + 5)
            _mr_board = _mr_lb_rows[_start:_end]
            _mr_board_offset = _start

        # Heatmap: focus minutes per day for the last 35 days
        from datetime import date as _date_cls, timedelta as _td_cls
        _mr_today = _date_cls.today()
        _mr_heat_start = _mr_today - _td_cls(days=34)
        _mr_heat = {}
        try:
            with sdb.get_db() as _hdb:
                _heat_rows = sdb._fetchall(
                    _hdb,
                    "SELECT plan_date, COALESCE(SUM(focus_minutes), 0) AS m "
                    "FROM student_study_progress "
                    "WHERE client_id = %s AND plan_date >= %s "
                    "GROUP BY plan_date",
                    (cid, _mr_heat_start.isoformat()),
                ) or []
            for _r in _heat_rows:
                _ds = (_r.get("plan_date") or "")[:10]
                if not _ds:
                    continue
                try:
                    _dd = datetime.strptime(_ds, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                _mr_heat[_dd] = int(_r.get("m") or 0)
        except Exception:
            _mr_heat = {}

        # Course tile data: progress = past_exams / total_exams
        _mr_course_tiles = []
        for _c in (courses or [])[:4]:
            _ce = []
            try:
                _ce = sdb.get_course_exams(_c["id"]) or []
            except Exception:
                _ce = []
            _total_e = len(_ce)
            _past_e = 0
            _next_label = ""
            _next_days = None
            for _ex in _ce:
                _ed = (_ex.get("exam_date") or "")[:10]
                if not _ed:
                    continue
                try:
                    _ed_d = datetime.strptime(_ed, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if _ed_d < _mr_today:
                    _past_e += 1
                else:
                    _di = (_ed_d - _mr_today).days
                    if _next_days is None or _di < _next_days:
                        _next_days = _di
                        _next_label = _ex.get("name") or "Próxima evaluación"
            _pct = int(100 * _past_e / _total_e) if _total_e > 0 else 0
            _mr_course_tiles.append({
                "id": _c.get("id"),
                "name": _c.get("name") or "Curso",
                "code": _c.get("code") or "—",
                "pct": _pct,
                "next_label": _next_label,
                "next_days": _next_days,
            })

        # XP earned today (for the topbar pill / one of the stat cards)
        try:
            _xp_history = sdb.get_xp_history(cid, limit=200) or []
        except Exception:
            _xp_history = []
        _today_iso = _mr_today.isoformat()
        _mr_xp_today = 0
        for _row in _xp_history:
            _e = (_row.get("earned_at") or "")[:10]
            if _e == _today_iso:
                _mr_xp_today += int(_row.get("xp") or 0)

        analytics_strip_html = ""  # legacy placeholder, no longer rendered



        # =============================================================
        # Claude Design "Inicio" dashboard. The CSS below is self-contained
        # so colors match the mockup regardless of the active LAYOUT theme.
        # All numbers and lists come from real Flask data.
        # =============================================================
        _mr_css = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&display=swap');

  .mr-home { font-family: "Plus Jakarta Sans", system-ui, -apple-system, sans-serif; color: #1A1A1F; }
  .mr-home .serif, .mr-home h1, .mr-home h2 { font-family: "Fraunces", Georgia, serif; letter-spacing: -0.02em; }
  .mr-home, .mr-home * { box-sizing: border-box; }
  .mr-home button { font: inherit; color: inherit; cursor: pointer; }
  .mr-home a { color: inherit; text-decoration: none; }

  /* page bg */
  body:has(.mr-home) .content { background: #F4F1EA !important; }
  :root[data-theme="dark"] body:has(.mr-home) .content { background: #0E0D14 !important; }

  .mr-layout { display: grid; grid-template-columns: 1fr 320px; gap: 24px; max-width: 1400px; margin: 0 auto; padding: 6px 0 80px; }
  @media (max-width: 1100px) { .mr-layout { grid-template-columns: 1fr; } }

  /* ── HERO MISSION ── */
  .mr-mission {
    background: linear-gradient(135deg, #FFF1E2 0%, #FFE3CB 50%, #FBD3B0 100%);
    border: 1px solid #F1C896;
    border-radius: 24px;
    padding: 28px;
    position: relative;
    overflow: hidden;
    color: #3A1A06;
    margin-bottom: 20px;
  }
  .mr-mission::before {
    content: ""; position: absolute; right: -40px; top: -40px;
    width: 220px; height: 220px;
    background: radial-gradient(circle, rgba(255,255,255,.5), transparent 70%);
    border-radius: 50%; pointer-events: none;
  }
  .mr-mission-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; position: relative; z-index: 1; }
  .mr-mission-eye { font-size: 11px; font-weight: 800; letter-spacing: 0.1em; text-transform: uppercase; opacity: 0.7; margin-bottom: 6px; }
  .mr-mission-title { font-weight: 600; font-size: 38px; line-height: 1.05; margin: 0; color: #3A1A06; }
  .mr-mission-title em { font-style: italic; color: #B33C00; }
  .mr-mission-cta {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 14px 22px;
    background: #1A1A1F; color: #FFF8E1;
    border-radius: 14px;
    font-weight: 800; font-size: 15px;
    border: none;
    box-shadow: 0 4px 0 rgba(0,0,0,.25), 0 8px 20px rgba(58,26,6,.25);
    transition: transform .12s;
    text-decoration: none;
  }
  .mr-mission-cta:hover { transform: translateY(-1px); }
  .mr-mission-cta:active { transform: translateY(2px); box-shadow: 0 2px 0 rgba(0,0,0,.25); }

  .mr-quests-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; position: relative; z-index: 1; }
  @media (max-width: 720px) { .mr-quests-row { grid-template-columns: 1fr; } }
  .mr-quest {
    background: rgba(255,255,255,0.7);
    border: 1px solid rgba(255,255,255,0.9);
    border-radius: 16px;
    padding: 14px;
    transition: transform .15s;
  }
  .mr-quest:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(58,26,6,.18); }
  .mr-quest.done { opacity: 0.65; }
  .mr-quest.done .mr-quest-title { text-decoration: line-through; }
  .mr-quest-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .mr-quest-icon {
    width: 32px; height: 32px;
    border-radius: 10px;
    display: grid; place-items: center;
    font-size: 18px;
    background: #FFD08C;
  }
  .mr-quest-icon.q1 { background: #FFD08C; }
  .mr-quest-icon.q2 { background: #BFE0FF; }
  .mr-quest-icon.q3 { background: #FFB9D6; }
  .mr-quest-reward { font-size: 11px; font-weight: 800; color: #C98A0E; }
  .mr-quest-title { font-weight: 700; font-size: 13px; line-height: 1.3; margin-bottom: 8px; color: #3A1A06; }
  .mr-quest-prog { display: flex; align-items: center; gap: 8px; font-size: 11px; }
  .mr-quest-bar { flex: 1; height: 6px; background: rgba(0,0,0,0.08); border-radius: 999px; overflow: hidden; }
  .mr-quest-fill { height: 100%; background: linear-gradient(90deg, #FF7A3D, #FFB37A); border-radius: 999px; }
  .mr-quest.done .mr-quest-fill { background: #2E9266; }
  .mr-quest-num { font-weight: 700; min-width: 36px; text-align: right; font-variant-numeric: tabular-nums; color: #3A1A06; }
  .mr-quest-check { color: #2E9266; font-size: 18px; }

  /* ── STAT GRID ── */
  .mr-stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
  @media (max-width: 900px) { .mr-stats-grid { grid-template-columns: repeat(2, 1fr); } }
  .mr-stat-card { background: #FFFFFF; border: 1px solid #E2DCCC; border-radius: 18px; padding: 18px; position: relative; overflow: hidden; }
  .mr-stat-card.tilted { background: linear-gradient(135deg, #5B4694, #7A65BA); color: #fff; border: none; }
  .mr-stat-card.tilted .mr-stat-label { color: rgba(255,255,255,0.8); }
  .mr-stat-card.tilted .mr-stat-sub { color: rgba(255,255,255,0.85); }
  .mr-stat-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: #94939C; }
  .mr-stat-value { font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 36px; letter-spacing: -0.03em; line-height: 1; margin-top: 8px; color: inherit; }
  .mr-stat-sub { font-size: 12px; color: #5C5C66; margin-top: 8px; }
  .mr-stat-sub .up { color: #2E9266; font-weight: 700; }
  .mr-stat-deco { position: absolute; right: -10px; bottom: -10px; font-size: 64px; opacity: 0.12; pointer-events: none; }

  /* ── GENERIC CARD ── */
  .mr-card { background: #FFFFFF; border: 1px solid #E2DCCC; border-radius: 18px; padding: 22px; margin-bottom: 20px; }
  .mr-card-h { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .mr-card-title { font-weight: 700; font-size: 15px; display: flex; align-items: center; gap: 8px; color: #1A1A1F; }
  .mr-card-link { font-size: 12px; color: #94939C; font-weight: 600; }
  .mr-card-link:hover { color: #5B4694; }

  /* ── SCHEDULE / TODAY PLAN ── */
  .mr-sched-list { display: flex; flex-direction: column; gap: 10px; }
  .mr-sess { display: flex; align-items: center; gap: 14px; padding: 14px; background: #FBF8F0; border: 1px solid #E2DCCC; border-radius: 14px; transition: transform .15s; }
  .mr-sess:hover { transform: translateX(3px); }
  .mr-sess.done { opacity: 0.55; }
  .mr-sess.done .mr-sess-topic { text-decoration: line-through; }
  .mr-sess-check { width: 22px; height: 22px; border: 2px solid #E2DCCC; border-radius: 7px; flex-shrink: 0; display: grid; place-items: center; background: #FFFFFF; cursor: pointer; transition: all .15s; padding: 0; }
  .mr-sess.done .mr-sess-check { background: #2E9266; border-color: #2E9266; color: #fff; }
  .mr-sess-time { font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 18px; color: #5C5C66; width: 56px; flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .mr-sess-body { flex: 1; min-width: 0; }
  .mr-sess-course { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.06em; padding: 2px 8px; border-radius: 999px; margin-bottom: 4px; background: #ECE6FB; color: #5B4694; }
  .mr-sess-topic { font-weight: 700; font-size: 14px; color: #1A1A1F; }
  .mr-sess-meta { font-size: 12px; color: #94939C; margin-top: 2px; }
  .mr-sess-prio { flex-shrink: 0; font-size: 10px; font-weight: 800; padding: 3px 8px; border-radius: 999px; text-transform: uppercase; }
  .mr-sess-prio.high { background: #FBDADA; color: #E04A4A; }
  .mr-sess-prio.med  { background: #FFEFCC; color: #C98A0E; }
  .mr-sess-prio.low  { background: #D7EDDF; color: #2E9266; }

  .mr-empty { padding: 20px; text-align: center; color: #94939C; font-size: 13px; background: #FBF8F0; border-radius: 14px; border: 1px dashed #E2DCCC; }
  .mr-empty .icon { font-size: 28px; display: block; margin-bottom: 8px; }
  .mr-empty .cta { display: inline-block; margin-top: 10px; padding: 8px 14px; border-radius: 10px; background: #1A1A1F; color: #FFF8E1; font-weight: 700; font-size: 12px; }

  /* ── COURSES TILES ── */
  .mr-courses-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  @media (max-width: 900px) { .mr-courses-row { grid-template-columns: repeat(2, 1fr); } }
  .mr-course-tile { background: #FBF8F0; border: 1px solid #E2DCCC; border-radius: 14px; padding: 14px; position: relative; overflow: hidden; transition: transform .15s; display: block; color: inherit; }
  .mr-course-tile:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(20,18,30,.06); }
  .mr-course-tile::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: #5B4694; }
  .mr-course-tile.c0::before { background: #3B6FE6; }
  .mr-course-tile.c1::before { background: #E85B9C; }
  .mr-course-tile.c2::before { background: #2E9266; }
  .mr-course-tile.c3::before { background: #FF7A3D; }
  .mr-course-code { font-size: 11px; font-weight: 800; color: #94939C; letter-spacing: 0.06em; }
  .mr-course-name { font-weight: 700; font-size: 14px; margin-top: 2px; line-height: 1.2; color: #1A1A1F; }
  .mr-course-prog { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 11px; }
  .mr-course-bar { flex: 1; height: 6px; background: rgba(0,0,0,0.06); border-radius: 999px; overflow: hidden; }
  .mr-course-fill { height: 100%; border-radius: 999px; }
  .mr-course-tile.c0 .mr-course-fill { background: #3B6FE6; }
  .mr-course-tile.c1 .mr-course-fill { background: #E85B9C; }
  .mr-course-tile.c2 .mr-course-fill { background: #2E9266; }
  .mr-course-tile.c3 .mr-course-fill { background: #FF7A3D; }
  .mr-course-pct { font-weight: 700; color: #5C5C66; }
  .mr-course-next { font-size: 11px; color: #94939C; margin-top: 6px; }
  .mr-course-next.urgent { color: #E04A4A; font-weight: 700; }

  /* ── EXAMS ── */
  .mr-exams { display: flex; flex-direction: column; gap: 10px; }
  .mr-exam { display: flex; align-items: center; gap: 14px; padding: 14px; border-radius: 14px; border: 1px solid #E2DCCC; background: #FBF8F0; transition: transform .15s; }
  .mr-exam:hover { transform: translateY(-2px); box-shadow: 0 1px 0 rgba(20,18,30,.04), 0 2px 6px rgba(20,18,30,.04); }
  .mr-exam-day { width: 56px; flex-shrink: 0; border-radius: 12px; background: #1A1A1F; color: #FFF8E1; text-align: center; padding: 8px 4px; line-height: 1; }
  .mr-exam-day.urgent { background: #E04A4A; }
  .mr-exam-day.warn { background: #FF7A3D; }
  .mr-exam-day .d-num { font-family: "Fraunces", Georgia, serif; font-size: 26px; font-weight: 600; }
  .mr-exam-day .d-mon { font-size: 10px; text-transform: uppercase; font-weight: 700; opacity: 0.75; margin-top: 2px; letter-spacing: 0.05em; }
  .mr-exam-body { flex: 1; min-width: 0; }
  .mr-exam-title { font-weight: 700; font-size: 14px; color: #1A1A1F; }
  .mr-exam-meta { font-size: 12px; color: #94939C; margin-top: 2px; }
  .mr-exam-cd { flex-shrink: 0; font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 18px; color: #1A1A1F; text-align: right; line-height: 1; }
  .mr-exam-cd.urgent { color: #E04A4A; }
  .mr-exam-cd small { display: block; font-size: 10px; color: #94939C; font-family: "Plus Jakarta Sans", sans-serif; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; margin-top: 2px; }

  /* ── RIGHT COLUMN ── */
  .mr-right { display: flex; flex-direction: column; gap: 18px; }

  /* league */
  .mr-league-card { background: linear-gradient(160deg, #5B4694 0%, #4A3470 100%); color: #fff; border-radius: 18px; padding: 22px; position: relative; overflow: hidden; }
  .mr-league-card::before { content: ""; position: absolute; left: -30px; bottom: -30px; width: 180px; height: 180px; background: radial-gradient(circle, rgba(255,255,255,0.15), transparent 70%); border-radius: 50%; pointer-events: none; }
  .mr-league-eye { font-size: 11px; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; opacity: 0.7; }
  .mr-league-name { font-family: "Fraunces", Georgia, serif; font-size: 24px; font-weight: 600; margin-top: 2px; display: flex; align-items: center; gap: 8px; color: #fff; }
  .mr-league-rank { display: flex; align-items: baseline; gap: 6px; margin-top: 14px; position: relative; z-index: 1; }
  .mr-league-rank-num { font-family: "Fraunces", Georgia, serif; font-size: 44px; font-weight: 600; line-height: 1; color: #fff; }
  .mr-league-rank-of { font-size: 13px; opacity: 0.7; }
  .mr-league-promo { margin-top: 12px; padding: 8px 12px; background: rgba(255,255,255,0.15); border-radius: 10px; font-size: 12px; display: flex; justify-content: space-between; gap: 8px; position: relative; z-index: 1; }
  .mr-mini-board { margin-top: 14px; display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 1; }
  .mr-lmrow { display: flex; align-items: center; gap: 10px; padding: 8px 10px; background: rgba(255,255,255,0.08); border-radius: 10px; font-size: 13px; }
  .mr-lmrow.me { background: rgba(255,255,255,0.22); border: 1px solid rgba(255,255,255,0.3); }
  .mr-lmrow .lm-rank { font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 14px; width: 22px; text-align: center; opacity: 0.9; }
  .mr-lmrow .lm-rank.gold { color: #F4B73A; }
  .mr-lmrow .lm-av { width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0; display: grid; place-items: center; font-weight: 800; font-size: 11px; color: #fff; }
  .mr-lmrow .lm-name { flex: 1; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mr-lmrow .lm-xp { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 12px; opacity: 0.95; }

  /* streak */
  .mr-streak-num { font-family: "Fraunces", Georgia, serif; font-size: 42px; font-weight: 600; color: #FF7A3D; line-height: 1; letter-spacing: -0.03em; }
  .mr-heat-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; margin-top: 10px; }
  .mr-heat-cell { aspect-ratio: 1; border-radius: 5px; background: #EDE7DA; }
  .mr-heat-cell.l1 { background: #F8E2C9; }
  .mr-heat-cell.l2 { background: #F4B886; }
  .mr-heat-cell.l3 { background: #FF7A3D; }
  .mr-heat-cell.l4 { background: #C8501F; }
  .mr-heat-cell.today { outline: 2px solid #1A1A1F; outline-offset: 1px; }
  .mr-week-labels { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; margin-top: 6px; }
  .mr-week-labels span { font-size: 9px; color: #94939C; text-align: center; font-weight: 700; }
  .mr-streak-row { display: flex; gap: 16px; margin-top: 14px; padding-top: 14px; border-top: 1px solid #E2DCCC; }
  .mr-streak-row > div { flex: 1; }
  .mr-srn { font-family: "Fraunces", Georgia, serif; font-size: 22px; font-weight: 600; line-height: 1; color: #FF7A3D; }
  .mr-srl { font-size: 10px; color: #94939C; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; margin-top: 4px; }

  /* friends */
  .mr-friends-list { display: flex; flex-direction: column; gap: 10px; }
  .mr-friend-row { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 10px; }
  .mr-friend-row:hover { background: #FBF8F0; }
  .mr-friend-av { width: 36px; height: 36px; border-radius: 10px; flex-shrink: 0; display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 13px; position: relative; }
  .mr-friend-av .pres { position: absolute; right: -2px; bottom: -2px; width: 12px; height: 12px; border-radius: 50%; border: 2px solid #fff; background: #94939C; }
  .mr-friend-av .pres.online { background: #2E9266; }
  .mr-friend-info { flex: 1; min-width: 0; }
  .mr-friend-name { font-weight: 700; font-size: 13px; color: #1A1A1F; }
  .mr-friend-status { font-size: 11px; color: #94939C; }
  .mr-friend-status.online { color: #2E9266; font-weight: 700; }
  .mr-friend-act { font-size: 11px; font-weight: 700; padding: 5px 10px; border-radius: 8px; background: #FBF8F0; color: #5C5C66; border: 1px solid #E2DCCC; cursor: pointer; }
  .mr-friend-act:hover { background: #5B4694; color: #fff; border-color: #5B4694; }

  @keyframes mrPop { 0% { opacity: 0; transform: translateY(8px); } 100% { opacity: 1; transform: none; } }
  .mr-pop-1 { animation: mrPop .5s .05s both; }
  .mr-pop-2 { animation: mrPop .5s .12s both; }
  .mr-pop-3 { animation: mrPop .5s .19s both; }
  .mr-pop-4 { animation: mrPop .5s .26s both; }
</style>
"""

        # ── greeting copy ───────────────────────────────────────
        _lang = session.get("lang", "es")
        _is_en = (_lang == "en")
        _first_name = (session.get("client_name", "") or ("student" if _is_en else "estudiante")).split()[0] or ("student" if _is_en else "estudiante")
        _dows_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        _months_es_long = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                           "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        _dows_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        _months_en_long = ["January", "February", "March", "April", "May", "June",
                           "July", "August", "September", "October", "November", "December"]
        if _is_en:
            _today_human = f"{_dows_en[_mr_today.weekday()]} {_months_en_long[_mr_today.month - 1]} {_mr_today.day}"
        else:
            _today_human = f"{_dows_es[_mr_today.weekday()]} {_mr_today.day} de {_months_es_long[_mr_today.month - 1]}"
        _semester = ""
        try:
            _semester = sdb.get_current_semester(cid) or ""
        except Exception:
            _semester = ""

        # ── mission card with quests ────────────────────────────
        # Build quest chips from real daily quests (fallback to placeholders).
        _quest_icons = ["q1", "q2", "q3"]
        _quest_emojis = ["⏱", "🎯", "🔥"]
        _quest_chips_html = ""
        for _i, _q in enumerate((_mr_quests or [])[:3]):
            _icon = _quest_icons[_i % 3]
            _emoji = _quest_emojis[_i % 3]
            # Map quest_key -> label using student.db.QUEST_POOL
            _label = next((p["label"] for p in sdb.QUEST_POOL if p["key"] == _q.get("quest_key")), _q.get("quest_key", "Mission" if _is_en else "Misión"))
            if _is_en:
                try:
                    from outreach.i18n import _translate_visible_text
                    _label = _translate_visible_text(str(_label)) or str(_label)
                except Exception:
                    _label = str(_label)
            _target = int(_q.get("target") or 1)
            _progress = int(_q.get("progress") or 0)
            _xp_reward = int(_q.get("xp_reward") or 0)
            _completed = bool(_q.get("completed_at"))
            _pct = min(100, int(100 * _progress / max(1, _target)))
            _done_cls = " done" if _completed else ""
            if _completed:
                _right = '<div class="mr-quest-check">✓</div>'
            else:
                _right = f'<div class="mr-quest-reward">+{_xp_reward} XP</div>'
            _quest_chips_html += (
                f'<div class="mr-quest{_done_cls}">'
                f'  <div class="mr-quest-head">'
                f'    <div class="mr-quest-icon {_icon}">{_emoji}</div>'
                f'    {_right}'
                f'  </div>'
                f'  <div class="mr-quest-title">{_esc(_label)}</div>'
                f'  <div class="mr-quest-prog">'
                f'    <div class="mr-quest-bar"><div class="mr-quest-fill" style="width:{_pct}%"></div></div>'
                f'    <div class="mr-quest-num">{_progress}/{_target}</div>'
                f'  </div>'
                f'</div>'
            )
        if not _quest_chips_html:
            _empty_quest = "Start your first focus session to unlock missions." if _is_en else "Empieza tu primera sesión de enfoque para desbloquear misiones."
            _quest_chips_html = (
                '<div class="mr-quest" style="grid-column:1/-1;">'
                '  <div class="mr-quest-head">'
                '    <div class="mr-quest-icon q1">⏱</div>'
                '    <div class="mr-quest-reward">+ XP</div>'
                '  </div>'
                f'  <div class="mr-quest-title">{_empty_quest}</div>'
                '</div>'
            )

        # Mission headline copy: built from real plan if present.
        _today_session_count = len((today_plan or {}).get("sessions", []) or []) if today_plan else 0
        _today_total_min = sum(int((s.get("hours") or 0) * 60) for s in ((today_plan or {}).get("sessions") or []))
        if _today_session_count > 0:
            _hh, _mm = divmod(_today_total_min, 60)
            _time_str = (f"{_hh}h {_mm}min" if _hh else f"{_mm}min")
            if _is_en:
                _headline = (
                    f'{_today_session_count} {"sessions" if _today_session_count != 1 else "session"}, '
                    f'<em>{_time_str}</em><br/>and you are on track.'
                )
            else:
                _headline = (
                    f'{_today_session_count} {"sesiones" if _today_session_count != 1 else "sesi\u00f3n"}, '
                    f'<em>{_time_str}</em><br/>y vas con todo.'
                )
        else:
            _headline = 'Today is a good day to<br/><em>start</em> studying.' if _is_en else 'Hoy es un buen d\u00eda para<br/><em>empezar</em> a estudiar.'


        _mission_eye = f"TODAY'S MISSION - {_today_human}" if _is_en else f"Misi\u00f3n de hoy - {_today_human}"
        _hello = "Hi" if _is_en else "\u00a1Hola"
        _focus_cta = "Start focus - 25 min" if _is_en else "Empezar enfoque - 25 min"
        _mr_mission_html = (
            '<section class="mr-mission mr-pop-1">'
            '  <div class="mr-mission-head">'
            '    <div>'
            f'      <div class="mr-mission-eye">{_esc(_mission_eye)}</div>'
            f'      <h1 class="mr-mission-title serif">{_hello}, {_esc(_first_name)}! {_headline}</h1>'
            '    </div>'
            f'    <a class="mr-mission-cta" href="/student/focus">{_focus_cta}</a>'
            '  </div>'
            f'  <div class="mr-quests-row">{_quest_chips_html}</div>'
            '</section>'
        )

        # ?? 4 stat cards ???????????????????????????????????????
        _hours_total = round(_total_mins / 60.0, 1)
        # Best day-of-week label (last 30 days). Use Python to compute.
        _best_dow = "—"
        _best_dow_min = 0
        if _mr_heat:
            _by_dow = {}
            for _d, _m in _mr_heat.items():
                _by_dow.setdefault(_d.weekday(), 0)
                _by_dow[_d.weekday()] += int(_m or 0)
            if _by_dow:
                _bdw = max(_by_dow.items(), key=lambda kv: kv[1])
                _dow_es = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
                _best_dow = _dow_es[_bdw[0]]
                _best_dow_min = _bdw[1]
        _bdw_h, _bdw_m = divmod(int(_best_dow_min), 60)
        _bdw_label = (f"{_bdw_h}h {_bdw_m}min" if _bdw_h else f"{_bdw_m}min")

        _xp_to_next = max(0, level_ceil - total_xp)

        _mr_stats_html = (
            '<div class="mr-stats-grid">'
            '  <div class="mr-stat-card mr-pop-2">'
            '    <div class="mr-stat-label">Total estudiado</div>'
            f'   <div class="mr-stat-value">{_hours_total}h</div>'
            f'   <div class="mr-stat-sub">{_total_sessions} sesiones</div>'
            '    <div class="mr-stat-deco">📚</div>'
            '  </div>'
            '  <div class="mr-stat-card mr-pop-2">'
            '    <div class="mr-stat-label">Mejor día</div>'
            f'   <div class="mr-stat-value">{_esc(_best_dow)}</div>'
            f'   <div class="mr-stat-sub">{_bdw_label} en últimos 35d</div>'
            '    <div class="mr-stat-deco">⚡</div>'
            '  </div>'
            '  <div class="mr-stat-card mr-pop-3">'
            '    <div class="mr-stat-label">Racha 🔥</div>'
            f'   <div class="mr-stat-value">{streak_days}</div>'
            f'   <div class="mr-stat-sub"><span class="up">{streak_days} día' + ("s" if streak_days != 1 else "") + ' seguidos</span></div>'
            '    <div class="mr-stat-deco">🔥</div>'
            '  </div>'
            '  <div class="mr-stat-card tilted mr-pop-3">'
            '    <div class="mr-stat-label">XP de hoy</div>'
            f'   <div class="mr-stat-value">{_mr_xp_today}</div>'
            f'   <div class="mr-stat-sub">↑ subes a siguiente nivel en {_xp_to_next} XP</div>'
            '    <div class="mr-stat-deco" style="opacity:.2;color:#fff;">★</div>'
            '  </div>'
            '</div>'
        )

        # ── Today plan card ────────────────────────────────────
        _today_rows_html = ""
        if today_plan:
            _sessions = today_plan.get("sessions", []) or []
            for _idx, _s in enumerate(_sessions):
                _prio = (_s.get("priority") or "medium").lower()
                _prio_cls = {"high": "high", "medium": "med", "low": "low"}.get(_prio, "med")
                _prio_label = {"high": "Urgente", "medium": "Media", "low": "Baja"}.get(_prio, "Media")
                _is_done = bool(today_assignment_progress.get(_idx, False))
                _done_cls = " done" if _is_done else ""
                _check_inner = "✓" if _is_done else ""
                _course = _s.get("course", "") or ""
                _topic = _s.get("topic", "") or ""
                _hours = _s.get("hours", 0) or 0
                _hours_min = int(float(_hours) * 60)
                _start_t = _s.get("start_time", "") or ""
                if not _start_t:
                    _start_t = ["08:30", "11:00", "14:00", "17:30", "20:00"][_idx] if _idx < 5 else "—"
                _meta = f'{_hours_min} min · {_s.get("type", "estudio")}'
                _today_rows_html += (
                    f'<div class="mr-sess{_done_cls}" id="mr-sess-{_idx}">'
                    f'  <button type="button" class="mr-sess-check" onclick="mrToggleSess({_idx})" title="Marcar como hecho">{_check_inner}</button>'
                    f'  <div class="mr-sess-time">{_esc(_start_t)}</div>'
                    f'  <div class="mr-sess-body">'
                    f'    <div class="mr-sess-course">{_esc(_course)}</div>'
                    f'    <div class="mr-sess-topic">{_esc(_topic)}</div>'
                    f'    <div class="mr-sess-meta">{_esc(_meta)}</div>'
                    f'  </div>'
                    f'  <span class="mr-sess-prio {_prio_cls}">{_prio_label}</span>'
                    f'</div>'
                )
        if not _today_rows_html:
            _today_rows_html = (
                '<div class="mr-empty">'
                '  <span class="icon">📚</span>'
                '  Aún no hay sesiones para hoy.'
                '  <br/><a class="cta" href="/student/focus">🎯 Empezar enfoque</a>'
                '</div>'
            )

        _mr_today_card = (
            '<section class="mr-card mr-pop-2">'
            '  <div class="mr-card-h">'
            '    <div class="mr-card-title">🎯 Plan de hoy</div>'
            '    <a class="mr-card-link" href="/student/schedule">Ver semana →</a>'
            '  </div>'
            f' <div class="mr-sched-list">{_today_rows_html}</div>'
            '</section>'
        )

        # ── Courses tiles ──────────────────────────────────────
        _courses_tiles_html = ""
        for _i, _ct in enumerate(_mr_course_tiles):
            _next = ""
            _next_cls = ""
            if _ct["next_days"] is not None:
                if _ct["next_days"] <= 7:
                    _next_cls = " urgent"
                _next = f'⏳ {_esc(_ct["next_label"])} en {_ct["next_days"]} días'
            else:
                _next = "Sin pruebas próximas"
            _courses_tiles_html += (
                f'<a class="mr-course-tile c{_i % 4}" href="/student/courses/{_ct["id"]}">'
                f'  <div class="mr-course-code">{_esc(_ct["code"])}</div>'
                f'  <div class="mr-course-name">{_esc(_ct["name"])}</div>'
                f'  <div class="mr-course-prog">'
                f'    <div class="mr-course-bar"><div class="mr-course-fill" style="width:{_ct["pct"]}%"></div></div>'
                f'    <span class="mr-course-pct">{_ct["pct"]}%</span>'
                f'  </div>'
                f'  <div class="mr-course-next{_next_cls}">{_next}</div>'
                f'</a>'
            )
        if not _courses_tiles_html:
            _courses_tiles_html = (
                '<div class="mr-empty" style="grid-column:1/-1;">'
                '  <span class="icon">📚</span>'
                '  No tienes cursos todavía.'
                '  <br/><a class="cta" href="/student/canvas">🔗 Conectar Canvas</a>'
                '</div>'
            )

        _mr_courses_card = (
            '<section class="mr-card mr-pop-3">'
            '  <div class="mr-card-h">'
            f'   <div class="mr-card-title">📘 Mis cursos{(" · " + _esc(_semester)) if _semester else ""}</div>'
            '    <a class="mr-card-link" href="/student/courses">Ver todos →</a>'
            '  </div>'
            f' <div class="mr-courses-row">{_courses_tiles_html}</div>'
            '</section>'
        )

        # ── Upcoming exams (rebuild with new style) ────────────
        _exam_rows_html = ""
        _months_es = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
        _months_en = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        for _e in (exams or [])[:5]:
            _ed = (_e.get("exam_date") or "")[:10]
            _days = None
            _day_num = "—"
            _mon = "—"
            if _ed:
                try:
                    _ed_d = datetime.strptime(_ed, "%Y-%m-%d").date()
                    _days = (_ed_d - _mr_today).days
                    _day_num = _ed_d.strftime("%d")
                    _mon = (_months_en if _is_en else _months_es)[_ed_d.month - 1]
                except (ValueError, TypeError):
                    pass
            _badge_cls = ""
            _cd_cls = ""
            if _days is not None and _days <= 7:
                _badge_cls = " urgent"
                _cd_cls = " urgent"
            elif _days is not None and _days <= 14:
                _badge_cls = " warn"
            _topics_raw = _e.get("topics_json") or "[]"
            try:
                _topics = json.loads(_topics_raw) if isinstance(_topics_raw, str) else (_topics_raw or [])
            except Exception:
                _topics = []
            _topic_str = ""
            if _topics:
                _topic_str = ", ".join(_topics[:3])
                if len(_topics) > 3:
                    _topic_str += "…"
            _meta_parts = []
            if _e.get("weight_pct"):
                _meta_parts.append(f'{_e.get("weight_pct")}% nota')
            if _topic_str:
                _meta_parts.append(_esc(_topic_str))
            _meta_str = " · ".join(_meta_parts) if _meta_parts else "Sin temas listados"
            _cd_text = (f"{_days}d" if _days is not None else "?")
            if _is_en:
                _cd_sub = "left" if (_days is not None and _days >= 0) else "overdue"
            else:
                _cd_sub = "faltan" if (_days is not None and _days >= 0) else "atrasada"
            _exam_rows_html += (
                f'<a class="mr-exam" href="/student/exams">'
                f'  <div class="mr-exam-day{_badge_cls}">'
                f'    <div class="d-num">{_day_num}</div>'
                f'    <div class="d-mon">{_mon}</div>'
                f'  </div>'
                f'  <div class="mr-exam-body">'
                f'    <div class="mr-exam-title">{_esc(_e.get("name", "Evaluación"))} — {_esc(_e.get("course_name", ""))}</div>'
                f'    <div class="mr-exam-meta">{_meta_str}</div>'
                f'  </div>'
                f'  <div class="mr-exam-cd{_cd_cls}">{_cd_text}<small>{_cd_sub}</small></div>'
                f'</a>'
            )
        if not _exam_rows_html:
            _exam_rows_html = (
                '<div class="mr-empty">'
                '  <span class="icon">📝</span>'
                '  Sin pruebas próximas.'
                '  <br/><a class="cta" href="/student/exams">📝 Administrar pruebas</a>'
                '</div>'
            )

        _mr_exams_card = (
            '<section class="mr-card mr-pop-4">'
            '  <div class="mr-card-h">'
            '    <div class="mr-card-title">📝 Próximas evaluaciones</div>'
            '    <a class="mr-card-link" href="/student/exams">Calendario completo →</a>'
            '  </div>'
            f' <div class="mr-exams">{_exam_rows_html}</div>'
            '</section>'
        )

        # ── Right column: League card ──────────────────────────
        _board_rows_html = ""
        for _bi, _br in enumerate(_mr_board):
            _abs_rank = _mr_board_offset + _bi + 1
            _is_me = int(_br.get("client_id") or 0) == cid
            _name = _br.get("name") or ("Student" if _is_en else "Estudiante")
            _xp = int(_br.get("total_xp") or 0)
            _color = _mr_palette[(_abs_rank - 1) % len(_mr_palette)]
            _ini = _mr_initials(_name)
            _rank_cls = " gold" if _abs_rank <= 2 else ""
            _row_cls = " me" if _is_me else ""
            _disp_name = (("You · " if _is_en else "Tú · ") + _name.split()[0]) if _is_me else _name
            _board_rows_html += (
                f'<div class="mr-lmrow{_row_cls}">'
                f'  <div class="lm-rank{_rank_cls}">{_abs_rank}</div>'
                f'  <div class="lm-av" style="background:{_color};">{_esc(_ini)}</div>'
                f'  <div class="lm-name">{_esc(_disp_name)}</div>'
                f'  <div class="lm-xp">{_xp:,}</div>'
                f'</div>'
            )
        _league_label = _esc(level_name)
        _my_rank_disp = f"#{_mr_my_rank}" if _mr_my_rank > 0 else "—"
        _empty_board = "<div style='opacity:.7;font-size:12px;text-align:center;padding:8px;'>Be the first to earn XP</div>" if _is_en else "<div style='opacity:.7;font-size:12px;text-align:center;padding:8px;'>S? el primero en sumar XP</div>"
        _mr_league_card = (
            '<section class="mr-league-card mr-pop-1">'
            + ('  <div class="mr-league-eye">WEEKLY LEAGUE</div>' if _is_en else '  <div class="mr-league-eye">Liga semanal</div>')
            + f' <div class="mr-league-name serif">&#127942; {_league_label}</div>'
            + '  <div class="mr-league-rank">'
            + f'   <span class="mr-league-rank-num">{_my_rank_disp}</span>'
            + f'   <span class="mr-league-rank-of">{"of" if _is_en else "de"} {len(_mr_lb_rows)}</span>'
            + '  </div>'
            + '  <div class="mr-league-promo">'
            + f'   <span>{_xp_to_next} XP {"to rank up" if _is_en else "para subir"}</span>'
            + f'   <span style="font-weight:800;">{xp_pct}%</span>'
            + '  </div>'
            + f' <div class="mr-mini-board">{_board_rows_html or _empty_board}</div>'
            + '</section>'
        )

        # ?? Right column: Streak heatmap ????????????????????????
        _heat_html = ""
        _hd = _mr_heat_start
        for _i in range(35):
            _m = _mr_heat.get(_hd, 0)
            if _m <= 0:
                _lvl = ""
            elif _m < 25:
                _lvl = "l1"
            elif _m < 60:
                _lvl = "l2"
            elif _m < 120:
                _lvl = "l3"
            else:
                _lvl = "l4"
            _today_cls = " today" if _hd == _mr_today else ""
            _heat_html += f'<div class="mr-heat-cell {_lvl}{_today_cls}" title="{_hd.isoformat()}: {_m}m"></div>'
            _hd += _td_cls(days=1)

        # Frozen (insurance) status
        try:
            _freeze = sdb.get_freeze_status(cid) or {}
            _freezes_avail = int(_freeze.get("available") or 0) if isinstance(_freeze, dict) else 0
        except Exception:
            _freezes_avail = 0

        _attendance_pct = 0
        _active_days = sum(1 for _v in _mr_heat.values() if _v > 0)
        if _mr_heat:
            _attendance_pct = int(round(100 * _active_days / 35))

        _mr_streak_card = (
            '<section class="mr-card mr-pop-2">'
            + '  <div class="mr-card-h">'
            + ('    <div class="mr-card-title">&#128293; Your streak</div>' if _is_en else '    <div class="mr-card-title">&#128293; Tu racha</div>')
            + ('    <a class="mr-card-link" href="/student/analytics">Details &rarr;</a>' if _is_en else '    <a class="mr-card-link" href="/student/analytics">Detalle &rarr;</a>')
            + '  </div>'
            + '  <div style="display:flex; align-items:baseline; gap:10px;">'
            + f'   <div class="mr-streak-num">{streak_days}</div>'
            + f'   <div style="font-size:13px; color:#5C5C66;">{"days in a row" if _is_en else "d\u00edas seguidos"}<br/><span style="color:#94939C;font-size:11px;">{"last 35 days" if _is_en else "\u00faltimos 35 d\u00edas"}</span></div>'
            + '  </div>'
            + f' <div class="mr-heat-grid">{_heat_html}</div>'
            + '  <div class="mr-week-labels">'
            + ('    <span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span><span>S</span>' if _is_en else '    <span>L</span><span>M</span><span>M</span><span>J</span><span>V</span><span>S</span><span>D</span>')
            + '  </div>'
            + '  <div class="mr-streak-row">'
            + f'   <div><div class="mr-srn">{_active_days}</div><div class="mr-srl">{"Active days" if _is_en else "D\u00edas activos"}</div></div>'
            + f'   <div><div class="mr-srn">{_freezes_avail}</div><div class="mr-srl">{"Freezes" if _is_en else "Congeladores"}</div></div>'
            + f'   <div><div class="mr-srn">{_attendance_pct}%</div><div class="mr-srl">{"Attendance" if _is_en else "Asistencia"}</div></div>'
            + '  </div>'
            + '</section>'
        )

        # ?? Right column: Friends list ??????????????????????????
        _friend_rows_html = ""
        for _fi, _f in enumerate(_mr_friends):
            _fname = _f.get("name") or "Amigo"
            _online = bool(_f.get("online"))
            _color = _mr_palette[_fi % len(_mr_palette)]
            _ini = _mr_initials(_fname)
            _pres_cls = " online" if _online else ""
            _status = ("en línea" if _online else "offline")
            _status_cls = " online" if _online else ""
            _friend_rows_html += (
                f'<div class="mr-friend-row">'
                f'  <div class="mr-friend-av" style="background:{_color};">{_esc(_ini)}<div class="pres{_pres_cls}"></div></div>'
                f'  <div class="mr-friend-info">'
                f'    <div class="mr-friend-name">{_esc(_fname)}</div>'
                f'    <div class="mr-friend-status{_status_cls}">{_status}</div>'
                f'  </div>'
                f'  <a class="mr-friend-act" href="/student/friends">Ver →</a>'
                f'</div>'
            )
        if not _friend_rows_html:
            _friend_rows_html = (
                '<div class="mr-empty">'
                '  <span class="icon">👥</span>'
                '  Aún no tienes amigos.'
                '  <br/><a class="cta" href="/student/friends">+ Agregar amigos</a>'
                '</div>'
            )

        _mr_friends_card = (
            '<section class="mr-card mr-pop-3">'
            '  <div class="mr-card-h">'
            '    <div class="mr-card-title">👥 Tus amigos</div>'
            '    <a class="mr-card-link" href="/student/friends">Ver todos →</a>'
            '  </div>'
            f' <div class="mr-friends-list">{_friend_rows_html}</div>'
            '</section>'
        )

        # ── final assembly ─────────────────────────────────────
        _mr_scripts = """
<script>
  // Toggle a session row's "done" state. Mirrors the existing
  // /api/student/assignments/toggle endpoint that the legacy dashboard used.
  window.mrToggleSess = async function(idx) {
    const row = document.getElementById('mr-sess-' + idx);
    if (!row) return;
    const isDone = row.classList.toggle('done');
    const btn = row.querySelector('.mr-sess-check');
    if (btn) btn.textContent = isDone ? '✓' : '';
    try {
      const today = new Date().toISOString().split('T')[0];
      await fetch('/api/student/assignments/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ date: today, session_index: idx, completed: isDone })
      });
    } catch (e) { /* offline-tolerant */ }
  };
</script>
"""

        _mr_html = (
            _mr_css
            + '<div class="mr-home">'
            + '<div class="mr-layout">'
            + '<div class="mr-left">'
            + _mr_mission_html
            + _mr_stats_html
            + _mr_courses_card
            + _mr_exams_card
            + '</div>'
            + '<aside class="mr-right">'
            + _mr_league_card
            + _mr_streak_card
            + _mr_friends_card
            + '</aside>'
            + '</div>'
            + '</div>'
            + _mr_scripts
        )

        return _s_render("Dashboard", _mr_html, active_page="student_dashboard")




    @app.route("/student/courses")

    def student_courses_page():

        if not _logged_in():

            return redirect(url_for("login"))

        # No more "Sync Canvas" button — kick a silent background refresh on
        # every visit so the list stays current. Idempotent + non-blocking.
        try:
            _kick_silent_canvas_resync(_cid())
        except Exception as _e:
            log.debug("silent resync kick failed: %s", _e)

        courses = sdb.get_courses(_cid())

        rows = ""

        for c in courses:

            try:

                n_exams = len(sdb.get_course_exams(c["id"]) or [])

            except Exception:

                n_exams = 0

            cname_esc = _esc(c['name'])

            rows += f"""<tr class="course-row" data-course-id="{c['id']}">

              <td style="font-weight:600;"><button onclick="toggleCourse({c['id']})" style="background:none;border:none;color:var(--primary);text-decoration:none;cursor:pointer;font-weight:600;font-size:14px;padding:0;text-align:left;">{cname_esc}</button></td>

              <td>{_esc(c.get('code',''))}</td>

              <td><span id="exam-count-{c['id']}">{n_exams}</span></td>

              <td><button onclick="deleteCourse({c['id']},'{_esc(c['name'][:30])}')" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;padding:4px 8px;" title="Remove course">&#128465;</button></td>

            </tr>

            <tr class="course-detail" id="detail-{c['id']}" style="display:none;">

              <td colspan="4" style="background:var(--bg);padding:14px 18px;">

                <div id="exams-panel-{c['id']}" style="font-size:13px;color:var(--text-muted);">Cargando evaluaciones…</div>

              </td>

            </tr>"""

        if not rows:

            rows = """<tr><td colspan="4" style="text-align:center;padding:32px;color:var(--text-muted);">

              <div style="font-size:36px;margin-bottom:10px;">&#128218;</div>

              Aún no hay cursos. <a href="/student/canvas-settings" style="color:var(--primary);">Conecta Canvas</a> y tus cursos aparecerán aquí automáticamente.

            </td></tr>"""



        current_sem = sdb.get_current_semester(_cid())
        sem_options = ""
        for _lbl in ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII"]:
            sel = " selected" if _lbl == current_sem else ""
            sem_options += f'<option value="{_lbl}"{sel}>{_lbl}</option>'

        try:
            _time_by_course = {int(x.get("course_id") or 0): x for x in (sdb.get_time_per_course(_cid()) or [])}
        except Exception:
            _time_by_course = {}
        _course_classes = ["cs", "mat", "fil", "fis"]
        course_cards_html = ""
        _total_exams = 0
        for _i, c in enumerate(courses):
            _course_id = int(c["id"])
            try:
                _exams = sdb.get_course_exams(_course_id) or []
            except Exception:
                _exams = []
            _total_exams += len(_exams)
            _stats = _time_by_course.get(_course_id) or {}
            _minutes = int(_stats.get("minutes") or 0)
            _sessions = int(_stats.get("sessions") or 0)
            _pct = max(8, min(100, int((_minutes / 600) * 100))) if _minutes else 8
            _state_cls = " warn" if _pct < 40 else (" good" if _pct >= 75 else "")
            _next_label = "Sin evaluaciones próximas"
            _urgent = ""
            _today_d = datetime.now().date()
            _future = []
            for _e in _exams:
                _ed = (_e.get("exam_date") or "")[:10]
                if not _ed:
                    continue
                try:
                    _future.append((datetime.strptime(_ed, "%Y-%m-%d").date(), _e))
                except Exception:
                    pass
            _future.sort(key=lambda x: x[0])
            if _future:
                _date_obj, _e = _future[0]
                _days = (_date_obj - _today_d).days
                _next_label = f"{_esc(_e.get('name') or 'Evaluación')} · {_date_obj.strftime('%d/%m')}"
                if _days >= 0:
                    _next_label += f" ({_days}d)"
                if 0 <= _days <= 7:
                    _urgent = " urgent"
            _cls = _course_classes[_i % len(_course_classes)]
            course_cards_html += f"""
            <article class="ccard {_cls}">
              <div class="ccard-head">
                <div class="ccard-code">{_esc(c.get("code") or f"CURSO {_i+1}")}</div>
                <button onclick="event.stopPropagation();deleteCourse({_course_id},'{_esc((c.get("name") or "")[:30])}')" class="ccard-menu" title="Eliminar curso">&#8942;</button>
              </div>
              <h2 class="ccard-name">{_esc(c.get("name") or "Curso")}</h2>
              <div class="ccard-prof">Canvas / manual · {_sessions} sesiones registradas</div>
              <div class="ccard-stats">
                <div class="ccs{_state_cls}"><div class="ccs-n">{_pct}%</div><div class="ccs-l">Avance</div></div>
                <div class="ccs"><div class="ccs-n">{_minutes//60}h {_minutes%60}m</div><div class="ccs-l">Estudiado</div></div>
                <div class="ccs"><div class="ccs-n">{len(_exams)}</div><div class="ccs-l">Evaluaciones</div></div>
              </div>
              <div class="ccard-bar"><div class="ccard-fill{_state_cls}" style="width:{_pct}%"></div></div>
              <div class="ccard-foot">
                <span class="ccard-next{_urgent}">↗ {_next_label}</span>
                <button class="ccard-go{_state_cls}" onclick="toggleCourse({_course_id})" type="button">Ver detalles →</button>
              </div>
              <div class="course-detail-card" id="detail-{_course_id}" style="display:none;">
                <div id="exams-panel-{_course_id}" class="course-exams-panel">Cargando evaluaciones...</div>
              </div>
            </article>
            """
        if not course_cards_html:
            course_cards_html = """
            <div class="course-empty">
              <div class="deck-add-icon">+</div>
              <div class="deck-add-l">Aún no hay cursos</div>
              <div class="deck-add-s">Conecta Canvas o agrega uno manualmente.</div>
            </div>
            """

        return _s_render("Mis Cursos", f"""

        <div class="page-head-cd">
          <div>
            <div class="page-eyebrow-cd">Semestre {current_sem or "actual"} · {len(courses)} cursos · {_total_exams} evaluaciones</div>
            <h1 class="page-title-cd">Mis cursos</h1>
          </div>
          <div class="page-actions-cd">
            <select id="cur-sem-select" onchange="setCurrentSemester(this.value)" style="padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font-weight:600;">
              <option value="" {'selected' if not current_sem else ''}>—</option>
              {sem_options}
            </select>
          </div>
        </div>
        <div class="canvas-banner">
          <div class="cb-icon">⌬</div>
          <div class="cb-body">
            <div class="cb-title">Canvas conectado</div>
            <div class="cb-meta">Cursos sincronizados automáticamente · los ramos nuevos caen en el semestre activo</div>
          </div>
          <a class="cb-btn" href="/student/canvas">Configurar</a>
        </div>
        <div class="course-cards">{course_cards_html}</div>
        <div style="display:none">

          <h1>&#128218; Mis Cursos</h1>

          <div style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text-muted);">

            <span>Semestre actual:</span>

            <select id="cur-sem-select" onchange="setCurrentSemester(this.value)" style="padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font-weight:600;">

              <option value="" {'selected' if not current_sem else ''}>—</option>

              {sem_options}

            </select>

          </div>

        </div>

        <div style="display:none;font-size:12px;color:var(--text-muted);margin-bottom:14px;">


        </div>

        <div class="card" style="display:none;">

          <table>

            <thead><tr><th>Curso</th><th>Código</th><th>Pruebas</th><th></th></tr></thead>

            <tbody></tbody>

          </table>

        </div>

        </div>

        <style>
        .page-head-cd {{ display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap;margin-bottom:18px; }}
        .page-eyebrow-cd {{ font-size:12px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#7B61FF; }}
        .page-title-cd {{ margin:4px 0 0;font-family:Fraunces,Georgia,serif;font-size:48px;font-weight:600;letter-spacing:-.03em;color:#1A1A1F; }}
        .page-actions-cd {{ display:flex;align-items:center;gap:10px;flex-wrap:wrap; }}
        .btn-pop-cd {{ background:#1A1A1F;color:#FFF8E1;border:0;padding:10px 16px;border-radius:999px;font-weight:800;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center; }}
        .canvas-banner {{ background:linear-gradient(135deg,#F0ECFF,#FBF8F0);border:1px solid #E2DCCC;border-radius:16px;padding:16px 20px;display:flex;align-items:center;gap:16px;margin-bottom:18px;box-shadow:0 8px 24px rgba(20,18,30,.05); }}
        .cb-icon {{ width:44px;height:44px;border-radius:12px;background:#7B61FF;color:#fff;display:grid;place-items:center;font-size:22px;font-weight:900; }}
        .cb-body {{ flex:1; }}
        .cb-title {{ font-weight:900;font-size:14px;color:#1A1A1F; }}
        .cb-meta {{ font-size:12px;color:#94939C;margin-top:2px; }}
        .cb-btn {{ background:#fff;border:1px solid #E2DCCC;padding:8px 14px;border-radius:999px;font-weight:800;font-size:12px;cursor:pointer;color:#1A1A1F;text-decoration:none; }}
        .course-cards {{ display:grid;grid-template-columns:repeat(2,1fr);gap:16px;align-items:start; }}
        @media (max-width:900px) {{ .course-cards {{ grid-template-columns:1fr; }} .page-title-cd{{font-size:38px;}} }}
        .ccard {{ background:#fff;border:1px solid #E2DCCC;border-radius:18px;padding:22px;box-shadow:0 12px 32px rgba(20,18,30,.06);position:relative;overflow:hidden; }}
        .ccard::before {{ content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:#7B61FF; }}
        .ccard.cs::before {{ background:#3B82F6; }}.ccard.mat::before {{ background:#EC4899; }}.ccard.fil::before {{ background:#10B981; }}.ccard.fis::before {{ background:#FF7A3D; }}
        .ccard-head {{ display:flex;justify-content:space-between;align-items:center; }}
        .ccard-code {{ font-size:11px;font-weight:900;color:#94939C;letter-spacing:.08em;text-transform:uppercase; }}
        .ccard-menu {{ background:transparent;border:0;cursor:pointer;color:#94939C;font-size:20px;line-height:1; }}
        .ccard-name {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:22px;margin:6px 0 4px;line-height:1.15;letter-spacing:-.015em;color:#1A1A1F; }}
        .ccard-prof {{ font-size:12px;color:#94939C;margin-bottom:16px; }}
        .ccard-stats {{ display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px; }}
        .ccs {{ background:#FBF8F0;border-radius:12px;padding:10px 8px;text-align:center;min-width:0; }}
        .ccs-n {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:18px;letter-spacing:-.02em;color:#1A1A1F;white-space:nowrap; }}
        .ccs-l {{ font-size:10px;color:#94939C;margin-top:2px; }}
        .ccs.warn .ccs-n {{ color:#FF7A3D; }}.ccs.good .ccs-n {{ color:#10B981; }}
        .ccard-bar {{ height:6px;background:#FBF8F0;border-radius:999px;overflow:hidden;margin-bottom:14px; }}
        .ccard-fill {{ height:100%;border-radius:999px;background:#7B61FF; }}
        .ccard-fill.warn {{ background:#FF7A3D; }}.ccard-fill.good {{ background:#10B981; }}
        .ccard.cs .ccard-fill {{ background:#3B82F6; }}.ccard.mat .ccard-fill {{ background:#EC4899; }}.ccard.fil .ccard-fill {{ background:#10B981; }}.ccard.fis .ccard-fill {{ background:#FF7A3D; }}
        .ccard-foot {{ display:flex;justify-content:space-between;align-items:center;gap:12px; }}
        .ccard-next {{ font-size:12px;font-weight:700;color:#5C5C66; }}
        .ccard-next.urgent {{ color:#FF7A3D;font-weight:900; }}
        .ccard-go {{ background:#1A1A1F;color:#FFF8E1;border:0;padding:8px 14px;border-radius:999px;font-weight:800;font-size:12px;cursor:pointer;white-space:nowrap; }}
        .ccard-go.warn {{ background:#FF7A3D;color:#fff; }}.ccard-go.good {{ background:#10B981;color:#fff; }}
        .course-detail-card {{ margin-top:16px;padding-top:14px;border-top:1px dashed #E2DCCC; }}
        .course-empty {{ grid-column:1/-1;border:2px dashed #E2DCCC;border-radius:18px;padding:32px;text-align:center;color:#94939C; }}
        .mr-modal {{ position:fixed;inset:0;background:rgba(26,26,31,.36);display:flex;align-items:center;justify-content:center;z-index:1000;padding:18px; }}
        .mr-modal-card {{ width:min(420px,100%);background:#fff;border:1px solid #E2DCCC;border-radius:18px;padding:20px;box-shadow:0 24px 80px rgba(20,18,30,.18); }}
        .mr-modal-card label {{ display:block;margin:10px 0 5px;font-size:12px;font-weight:900;color:#5C5C66;text-transform:uppercase;letter-spacing:.08em; }}

        .ex-input {{ padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);font-size:13px; }}

        .ex-input:focus {{ border-color:var(--primary);outline:none; }}

        </style>

        <script>

        function _esc(s) {{ return (s==null?'':String(s)).replace(/[&<>"']/g, function(c){{ return ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]; }}); }}

        async function setCurrentSemester(label) {{
          if (label === '') return;  // ignore the placeholder
          var csrfToken = document.querySelector('meta[name=\"csrf-token\"]');
          var headers = {{'Content-Type':'application/json'}};
          if (csrfToken) headers['X-CSRFToken'] = csrfToken.content;
          try {{
            await fetch('/api/student/semester/current', {{
              method: 'POST', headers: headers,
              body: JSON.stringify({{ label: label }})
            }});
          }} catch(e) {{ alert('No se pudo guardar el semestre.'); }}
        }}

        async function toggleCourse(courseId) {{

          var row = document.getElementById('detail-' + courseId);

          if (!row) return;

          if (row.style.display === 'none') {{

            row.style.display = '';

            await loadCourseExams(courseId);

          }} else {{

            row.style.display = 'none';

          }}

        }}

        async function loadCourseExams(courseId) {{

          var panel = document.getElementById('exams-panel-' + courseId);

          if (!panel) return;

          panel.innerHTML = 'Cargando evaluaciones…';

          try {{

            var r = await fetch('/api/student/courses/' + courseId + '/exams');

            var d = await r.json();

            renderExamsPanel(courseId, d.exams || []);

          }} catch(e) {{

            panel.innerHTML = '<span style="color:var(--red);">No se pudieron cargar las evaluaciones.</span>';

          }}

        }}

        function renderExamsPanel(courseId, exams) {{

          var panel = document.getElementById('exams-panel-' + courseId);

          if (!panel) return;

          var rowsHtml = '';

          for (var i=0;i<exams.length;i++) {{

            var e = exams[i];

            rowsHtml += examRowHtml(e.id, e.name||'', e.exam_date||'', e.weight_pct||0);

          }}

          panel.innerHTML =

            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px;">'

            + '<b style="color:var(--text);">&#128221; Pruebas y Evaluaciones</b>'

            + '<div style="display:flex;gap:6px;flex-wrap:wrap;">'

            + '<button class="btn btn-outline btn-sm" onclick="addExamRow(' + courseId + ')">+ Agregar evaluación</button>'

            + '<button class="btn btn-primary btn-sm" onclick="saveAllExams(' + courseId + ', this)">&#128190; Guardar</button>'

            + '</div>'

            + '</div>'

            + '<div id="exams-list-' + courseId + '">'

            + (rowsHtml || '<div style="color:var(--text-muted);font-size:13px;padding:8px 0;">Sin evaluaciones todavía. Aprieta <b>+ Agregar evaluación</b> para crear una.</div>')

            + '</div>';

          var countEl = document.getElementById('exam-count-' + courseId);
          if (countEl) countEl.textContent = exams.length;

        }}

        function examRowHtml(examId, name, date, weight) {{

          var idAttr = examId ? examId : 'new';

          return '<div class="ex-row" data-exam-id="' + idAttr + '" style="display:grid;grid-template-columns:2fr 1fr 90px auto;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);">'

            + '<input type="text" class="ex-input" data-field="name" value="' + _esc(name) + '" placeholder="Nombre evaluación">'

            + '<input type="date" class="ex-input" data-field="exam_date" value="' + _esc(date) + '">'

            + '<span style="display:flex;align-items:center;gap:4px;"><input type="number" class="ex-input" data-field="weight_pct" value="' + (weight||0) + '" min="0" max="100" style="width:60px;">%</span>'

            + '<button class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;padding:2px 8px;" onclick="deleteExamInline(this)" title="Eliminar">&#128465;</button>'

            + '</div>';

        }}

        function addExamRow(courseId) {{

          var list = document.getElementById('exams-list-' + courseId);

          if (!list) return;

          // Clear "no exams" placeholder

          if (list.children.length === 1 && !list.children[0].classList.contains('ex-row')) {{

            list.innerHTML = '';

          }}

          var div = document.createElement('div');

          div.innerHTML = examRowHtml('', '', '', 0);

          var node = div.firstChild;

          node.dataset.courseId = courseId;

          list.appendChild(node);

        }}

        async function saveAllExams(courseId, btnEl) {{

          var list = document.getElementById('exams-list-' + courseId);

          if (!list) return;

          var rows = list.querySelectorAll('.ex-row');

          if (!rows.length) {{ alert('No hay evaluaciones que guardar.'); return; }}

          var csrfToken = document.querySelector('meta[name=\"csrf-token\"]');

          var headers = {{'Content-Type':'application/json'}};

          if (csrfToken) headers['X-CSRFToken'] = csrfToken.content;

          btnEl.disabled = true;

          var origLabel = btnEl.innerHTML;

          btnEl.innerHTML = '&#9203; Guardando...';

          var ok = 0, fail = 0;

          for (var i = 0; i < rows.length; i++) {{

            var row = rows[i];

            var name = (row.querySelector('[data-field=\"name\"]').value || '').trim();

            var exam_date = row.querySelector('[data-field=\"exam_date\"]').value || '';

            var weight_pct = parseInt(row.querySelector('[data-field=\"weight_pct\"]').value) || 0;

            if (!name) {{ continue; }}  // skip empty rows quietly

            var examId = row.dataset.examId;

            var url, method;

            if (examId && examId !== 'new') {{ url = '/api/student/exams/' + examId; method = 'PUT'; }}

            else {{ url = '/api/student/courses/' + courseId + '/exams'; method = 'POST'; }}

            try {{

              var r = await fetch(url, {{

                method: method, headers: headers,

                body: JSON.stringify({{ name: name, exam_date: exam_date, weight_pct: weight_pct, topics: [], course_id: parseInt(courseId)||0 }})

              }});

              if (r.ok) {{

                var d = await r.json();

                if (d && d.id) {{ row.dataset.examId = d.id; }}

                ok++;

              }} else {{ fail++; }}

            }} catch(e) {{ fail++; }}

          }}

          btnEl.innerHTML = '&#10003; Guardado';

          setTimeout(function(){{ btnEl.innerHTML = origLabel; btnEl.disabled = false; }}, 1200);

          if (fail > 0) alert('Se guardaron ' + ok + ' evaluaciones. Fallaron ' + fail + '.');

          loadCourseExams(parseInt(courseId)||0);

        }}

        async function deleteExamInline(btnEl) {{

          var row = btnEl.closest('.ex-row');

          if (!row) return;

          var examId = row.dataset.examId;

          if (!examId || examId === 'new') {{ row.remove(); return; }}

          if (!confirm('Delete this exam?')) return;

          try {{

            await fetch('/api/student/exams/' + examId, {{method:'DELETE'}});

            var listEl = row.parentElement;

            row.remove();

            if (listEl) {{

              var match = listEl.id.match(/exams-list-(\\d+)/);

              if (match) {{ var cid = parseInt(match[1])||0; if (cid) loadCourseExams(cid); }}

            }}

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); }}

        }}

        async function deleteCourse(courseId, name) {{

          if (!confirm('Remove \"' + name + '\"? This will delete all its exams.')) return;

          try {{

            var r = await fetch('/api/student/courses/' + courseId, {{method:'DELETE'}});

            if (r.ok) {{ mrReload(); }}

            else {{ var d = await _safeJson(r); alert(d.error || 'No se pudo eliminar el curso'); }}

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); }}

        }}

        </script>

        """, active_page="student_courses")



    @app.route("/student/courses/<int:course_id>")
    def student_course_detail_page(course_id):
        # Per-course edit page removed; everything happens inline on the
        # courses list page. Keep the route so old bookmarks redirect.
        return redirect(url_for("student_courses_page"))



    @app.route("/student/exams")

    def student_exams_page():

        # Claude-style exams hub built from the user's saved course evaluations.

        if not _logged_in():
            return redirect(url_for("login"))

        cid = _cid()
        courses = sdb.get_courses(cid) or []
        today = datetime.now().date()
        exams = []
        for course in courses:
            for exam in (sdb.get_course_exams(int(course["id"])) or []):
                exam = dict(exam)
                raw_topics = exam.get("topics_json") or []
                if isinstance(raw_topics, str):
                    try:
                        raw_topics = json.loads(raw_topics)
                    except Exception:
                        raw_topics = []
                raw_date = exam.get("exam_date") or ""
                date_obj = None
                if raw_date:
                    try:
                        date_obj = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
                    except Exception:
                        date_obj = None
                days_left = (date_obj - today).days if date_obj else None
                exams.append({
                    "id": int(exam.get("id") or 0),
                    "name": exam.get("name") or "Evaluación",
                    "date_obj": date_obj,
                    "days_left": days_left,
                    "weight": int(exam.get("weight_pct") or 0),
                    "topics": raw_topics[:5] if isinstance(raw_topics, list) else [],
                    "course_id": int(course["id"]),
                    "course_name": course.get("name") or "Curso",
                    "course_code": course.get("code") or "",
                })

        exams.sort(key=lambda e: (e["date_obj"] is None, e["date_obj"] or today + timedelta(days=9999), e["course_name"]))
        this_week = [e for e in exams if e["days_left"] is not None and 0 <= e["days_left"] <= 7]
        next_two = [e for e in exams if e["days_left"] is not None and 8 <= e["days_left"] <= 14]
        later = [e for e in exams if e["days_left"] is not None and e["days_left"] > 14]
        no_date = [e for e in exams if e["days_left"] is None]

        def month_name(d):
            months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"]
            return months[d.month - 1] if d else "sin fecha"

        def date_label(e):
            if e["days_left"] is None:
                return "Sin fecha"
            if e["days_left"] == 0:
                return "Hoy"
            if e["days_left"] == 1:
                return "Mañana"
            if e["days_left"] > 0:
                return f"en {e['days_left']} días"
            return "vencida"

        def exam_card(e):
            d = e["date_obj"]
            topics = "".join(f'<span class="topic">{_esc(str(t))}</span>' for t in (e["topics"] or []))
            if not topics:
                topics = '<span class="topic muted">Sin temas guardados</span>'
            urgency = "urgent" if e["days_left"] is not None and e["days_left"] <= 7 else ("soon" if e["days_left"] is not None and e["days_left"] <= 14 else "")
            prep_pct = 0
            if e["days_left"] is not None:
                prep_pct = max(12, min(100, 100 - (e["days_left"] * 4))) if e["days_left"] >= 0 else 100
            return f"""
            <article class="exam-big {urgency}">
              <div class="eb-date"><div class="eb-day">{_esc(str(d.day if d else "—"))}</div><div class="eb-mon">{_esc(month_name(d))}</div></div>
              <div class="eb-main">
                <div class="eb-cd">{_esc(e["course_code"] or e["course_name"])} · {_esc(date_label(e))}</div>
                <h3 class="eb-title">{_esc(e["name"])}</h3>
                <div class="eb-meta">{_esc(e["course_name"])} · {e["weight"]}% de la nota</div>
                <div class="eb-topics">{topics}</div>
                <div class="eb-progress"><div class="ebp-row"><span>Preparación estimada</span><strong>{prep_pct}%</strong></div><div class="ebp-bar"><div class="ebp-fill" style="width:{prep_pct}%"></div></div></div>
              </div>
              <div class="eb-actions">
                <a href="/student/focus?course_id={e["course_id"]}&exam_id={e["id"]}">Estudiar</a>
                <a href="/student/quizzes">Quiz</a>
                <a href="/student/flashcards">Tarjetas</a>
              </div>
            </article>
            """

        def group(title, items):
            cards = "".join(exam_card(e) for e in items) or '<div class="exam-empty">Nada pendiente aquí.</div>'
            return f'<section class="tl-group"><h2>{_esc(title)}</h2>{cards}</section>'

        empty_state = """
          <div class="exam-empty big">
            <strong>No tienes pruebas guardadas todavía.</strong>
            <span>Agrega evaluaciones desde Mis cursos para que aparezcan acá con calendario y prioridad.</span>
            <a href="/student/courses">Ir a Mis cursos</a>
          </div>
        """ if not exams else ""

        content = f"""
        <style>
        .exams-cd {{ max-width:1180px;margin:0 auto 80px;font-family:"Plus Jakarta Sans",system-ui,sans-serif;color:#1A1A1F; }}
        .ex-head {{ display:flex;justify-content:space-between;align-items:flex-end;gap:18px;margin-bottom:22px; }}
        .ex-kicker {{ font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:#009B72;margin-bottom:8px; }}
        .ex-title {{ font-family:"Fraunces",Georgia,serif;font-size:clamp(42px,6vw,72px);line-height:.92;margin:0;font-weight:600;letter-spacing:-.05em; }}
        .ex-title em {{ color:#FF7A3D;font-style:italic; }}
        .ex-sub {{ color:#6E6A60;font-size:15px;margin:12px 0 0;max-width:620px;line-height:1.6; }}
        .ex-head a {{ background:#1A1A1F;color:#FFF8E1;text-decoration:none;border-radius:999px;padding:12px 18px;font-weight:800;box-shadow:0 4px 0 rgba(0,0,0,.16);white-space:nowrap; }}
        .urg-strip {{ display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 26px; }}
        .urg-card {{ background:#fff;border:1px solid #E2DCCC;border-radius:18px;padding:18px;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 8px rgba(20,18,30,.04); }}
        .urg-card .lab {{ font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:#77756F;margin-bottom:10px; }}
        .urg-card .num {{ font-family:"Fraunces",Georgia,serif;font-size:42px;line-height:1;font-weight:600; }}
        .urg-card.hot {{ background:#2C211E;color:#FFF8E1;border-color:#2C211E; }}
        .urg-card.hot .lab {{ color:#FFB199; }}
        .exam-timeline {{ display:grid;gap:18px; }}
        .tl-group {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:22px;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04); }}
        .tl-group h2 {{ font-family:"Plus Jakarta Sans",system-ui,sans-serif;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#77756F;margin:0 0 16px;font-weight:800; }}
        .exam-big {{ display:grid;grid-template-columns:78px 1fr auto;gap:18px;align-items:center;border:1px solid #E2DCCC;background:#FBF8F0;border-radius:18px;padding:16px;margin-top:12px; }}
        .exam-big:first-of-type {{ margin-top:0; }}
        .exam-big.urgent {{ border-color:#FF7A3D;box-shadow:inset 5px 0 0 #FF7A3D; }}
        .exam-big.soon {{ border-color:#F4B73A;box-shadow:inset 5px 0 0 #F4B73A; }}
        .eb-date {{ background:#1A1A1F;color:#FFF8E1;border-radius:16px;min-height:76px;display:grid;place-items:center;text-align:center;padding:10px; }}
        .eb-day {{ font-family:"Fraunces",Georgia,serif;font-size:30px;line-height:1;font-weight:600; }}
        .eb-mon {{ text-transform:uppercase;font-size:11px;letter-spacing:.14em;font-weight:800;color:#F4B73A;margin-top:4px; }}
        .eb-cd {{ font-size:11px;color:#009B72;font-weight:800;letter-spacing:.12em;text-transform:uppercase;margin-bottom:5px; }}
        .eb-title {{ font-family:"Fraunces",Georgia,serif;font-size:28px;margin:0 0 4px;font-weight:600;letter-spacing:-.03em; }}
        .eb-meta {{ color:#6E6A60;font-size:13px;font-weight:700;margin-bottom:12px; }}
        .eb-topics {{ display:flex;flex-wrap:wrap;gap:7px;margin-bottom:12px; }}
        .topic {{ border:1px solid #D8D0BE;background:#fff;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:700;color:#3D3932; }}
        .topic.muted {{ color:#94939C; }}
        .ebp-row {{ display:flex;justify-content:space-between;font-size:12px;color:#6E6A60;margin-bottom:6px;font-weight:800; }}
        .ebp-bar {{ height:8px;background:#EDE7DA;border-radius:999px;overflow:hidden; }}
        .ebp-fill {{ height:100%;background:linear-gradient(90deg,#FF7A3D,#F4B73A);border-radius:999px; }}
        .eb-actions {{ display:flex;flex-direction:column;gap:8px; }}
        .eb-actions a {{ color:#1A1A1F;background:#fff;border:1px solid #D8D0BE;border-radius:999px;padding:9px 12px;text-decoration:none;font-size:12px;font-weight:800;text-align:center;white-space:nowrap; }}
        .eb-actions a:first-child {{ background:#1A1A1F;color:#FFF8E1;border-color:#1A1A1F; }}
        .exam-empty {{ border:1px dashed #D8D0BE;border-radius:16px;padding:18px;color:#77756F;background:#FBF8F0; }}
        .exam-empty.big {{ display:grid;gap:10px;text-align:center;padding:46px 22px; }}
        .exam-empty.big strong {{ font-family:"Fraunces",Georgia,serif;font-size:28px;color:#1A1A1F; }}
        .exam-empty.big a {{ justify-self:center;background:#1A1A1F;color:#FFF8E1;text-decoration:none;border-radius:999px;padding:11px 16px;font-weight:800; }}
        @media (max-width:800px) {{ .urg-strip {{ grid-template-columns:repeat(2,1fr); }} .ex-head {{ align-items:flex-start;flex-direction:column; }} .exam-big {{ grid-template-columns:1fr; }} .eb-actions {{ flex-direction:row;flex-wrap:wrap; }} }}
        </style>
        <div class="exams-cd">
          <div class="ex-head">
            <div>
              <div class="ex-kicker">Calendario académico</div>
              <h1 class="ex-title">Pruebas <em>y entregas.</em></h1>
              <p class="ex-sub">Una vista clara de lo que viene, cuánto pesa y qué deberías atacar primero.</p>
            </div>
            <a href="/student/courses">+ Agregar en Mis cursos</a>
          </div>
          <div class="urg-strip">
            <div class="urg-card hot"><div class="lab">Esta semana</div><div class="num">{len(this_week)}</div></div>
            <div class="urg-card"><div class="lab">Próximas 2 semanas</div><div class="num">{len(next_two)}</div></div>
            <div class="urg-card"><div class="lab">Más adelante</div><div class="num">{len(later)}</div></div>
            <div class="urg-card"><div class="lab">Sin fecha</div><div class="num">{len(no_date)}</div></div>
          </div>
          {empty_state}
          <div class="exam-timeline">
            {group("Esta semana", this_week) if exams else ""}
            {group("Próximas 2 semanas", next_two) if exams else ""}
            {group("Más adelante", later) if exams else ""}
            {group("Sin fecha", no_date) if exams else ""}
          </div>
        </div>
        """

        return _s_render("Exámenes", content, active_page="student_exams")



    @app.route("/student/plan")

    def student_plan_page():

        if not _logged_in():

            return redirect(url_for("login"))

        plan_row = sdb.get_latest_plan(_cid())

        progress = sdb.get_progress(_cid())

        completed_dates = set(p["plan_date"] for p in progress if p.get("completed"))



        # Gather assignment-level progress for all plan dates

        all_assignment_progress = {}

        if plan_row:

            for day in plan_row["plan_json"].get("daily_plan", []):

                d = day.get("date", "")

                if d:

                    ap = sdb.get_assignment_progress(_cid(), d)

                    all_assignment_progress[d] = {r["session_index"]: bool(r["completed"]) for r in ap}



        content = ""

        if not plan_row:

            content = """

            <div style="text-align:center;padding:60px 20px;">

              <div style="font-size:48px;margin-bottom:16px;">&#129302;</div>

              <h2>Aún no tienes plan de estudio</h2>

              <p style="color:var(--text-muted);margin:12px 0 24px;">Sync your Canvas courses first, then generate an AI study plan.</p>

              <button onclick="generatePlan()" class="btn btn-primary" id="plan-btn">Generar plan de estudio</button>

            </div>"""

        else:

            daily = plan_row["plan_json"].get("daily_plan", [])

            days_html = ""

            for day in daily[:30]:

                d = day.get("date", "")

                sessions = day.get("sessions", [])

                ap = all_assignment_progress.get(d, {})



                # Check if all sessions are complete

                all_done = len(sessions) > 0 and all(ap.get(i, False) for i in range(len(sessions)))

                is_free = len(sessions) == 0



                if is_free:

                    icon = "&#127947;"

                    bg = "var(--card)"

                    border_c = "var(--border)"

                elif all_done:

                    icon = "&#10003;"

                    bg = "var(--green-light, #D1FAE5)"

                    border_c = "var(--green, #10B981)"

                else:

                    icon = "&#9744;"

                    bg = "var(--card)"

                    border_c = "var(--border)"



                sessions_html = ""

                if is_free:

                    sessions_html = "<div style='font-size:13px;color:var(--text-muted);font-style:italic;padding:4px 0;'>Free day — no study scheduled</div>"

                else:

                    for idx, s in enumerate(sessions):

                        checked = "checked" if ap.get(idx, False) else ""

                        prio_colors = {"high": "#EF4444", "medium": "#F59E0B", "low": "#10B981"}

                        pc = prio_colors.get(s.get("priority", "medium"), "#94A3B8")

                        strike = "text-decoration:line-through;opacity:0.6;" if ap.get(idx, False) else ""

                        sessions_html += f"""

                        <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);{strike}" id="session-{d}-{idx}">

                          <input type="checkbox" {checked} onchange="toggleAssignment('{d}',{idx},this.checked)"

                            style="width:18px;height:18px;cursor:pointer;accent-color:var(--primary);">

                          <span style="font-weight:600;color:var(--text);">{_esc(s.get('course',''))}</span>

                          <span style="color:var(--text-secondary);font-size:13px;">{_esc(s.get('topic',''))}</span>

                          <span style="margin-left:auto;font-size:12px;color:var(--text-muted);">{s.get('hours',0)}h</span>

                          <span style="background:{pc}18;color:{pc};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;">{s.get('priority','').upper()}</span>

                        </div>"""



                completed_count = sum(1 for i in range(len(sessions)) if ap.get(i, False))

                progress_text = f"{completed_count}/{len(sessions)}" if sessions else ""



                days_html += f"""

                <div style="background:{bg};border:1px solid {border_c};border-radius:var(--radius-sm);padding:12px 16px;margin-bottom:8px;">

                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:{'8px' if sessions else '0'};">

                    <span style="font-weight:700;">{icon} {day.get('day_name','')} {d}</span>

                    <div style="display:flex;align-items:center;gap:8px;">

                      <span style="font-size:12px;color:var(--text-muted);">{progress_text}</span>

                      <span style="font-size:13px;color:var(--text-muted);">{day.get('total_hours', 0)}h</span>

                    </div>

                  </div>

                  {sessions_html}

                </div>"""

            content = f"""

            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">

              <span style="font-size:13px;color:var(--text-muted);">Generated: {str(plan_row.get('generated_at','?'))[:16]}</span>

              <div style="display:flex;gap:8px;">

                <a href="/student/schedule" class="btn btn-outline btn-sm">&#128337; Schedule & Difficulty</a>

                <button onclick="generatePlan()" class="btn btn-outline btn-sm" id="plan-btn">&#128260; Regenerate</button>

              </div>

            </div>

            <div style="background:var(--bg);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:16px;font-size:13px;color:var(--text-muted);">

              &#128161; Check off each assignment as you complete it. Incomplete assignments will be rolled over when the plan regenerates at midnight.

            </div>

            {days_html}"""



        return _s_render("Study Plan", f"""

        <h1 style="margin-bottom:20px;">&#128197; Study Plan</h1>

        <div class="card">{content}</div>

        <script>

        async function toggleAssignment(date, idx, completed) {{

          var row = document.getElementById('session-' + date + '-' + idx);

          if (completed) {{

            row.style.textDecoration = 'line-through';

            row.style.opacity = '0.6';

          }} else {{

            row.style.textDecoration = '';

            row.style.opacity = '1';

          }}

          try {{

            await fetch('/api/student/assignments/toggle', {{

              method: 'POST',

              headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ date: date, session_index: idx, completed: completed }})

            }});

          }} catch(e) {{ console.error('Failed to save assignment progress:', e); }}

        }}

        async function generatePlan() {{

          var btn = document.getElementById('plan-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';

          try {{

            var r = await fetch('/api/student/plan/generate', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{preferences:{{hours_per_day:5}}}}) }});

            var d = await _safeJson(r);

            if (!r.ok) {{ alert(d.error || 'Failed'); btn.disabled = false; btn.innerHTML = '&#128260; Regenerate'; return; }}

            var iv = setInterval(async function() {{

              try {{

                var r2 = await fetch('/api/student/plan/status');

                var s = await r2.json();

                if (s.status === 'done') {{

                  clearInterval(iv);

                  if (window.showToast) window.showToast('Study plan generated!', 'success'); else alert('Study plan generated!');

                  if (window.confettiBurst) window.confettiBurst(60);

                  setTimeout(function(){{ mrReload(); }}, 900);

                }} else if (s.status === 'error') {{

                  clearInterval(iv);

                  alert(s.progress || 'Plan generation failed');

                  btn.disabled = false; btn.innerHTML = '&#128260; Regenerate';

                }}

              }} catch(e) {{}}

            }}, 2000);

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); btn.disabled = false; btn.innerHTML = '&#128260; Regenerate'; }}

        }}

        </script>

        """, active_page="student_plan")



    # ── Focus / Pomodoro page ───────────────────────────────



    @app.route("/student/focus")

    def student_focus_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        focus_stats = sdb.get_focus_stats_today(_cid())

        # Use a Windows-safe date format (no %-d / %#d) so it renders the same everywhere.
        _today_label = ""  # rendered client-side from browser's local date (timezone-correct)

        # Build a course→exams map so the exam dropdown can be filtered in JS
        # without a round-trip each time the student picks a different course.
        course_exams_map = {}
        course_options = ""
        for c in courses:
            course_options += (
                f'<option value="{int(c["id"])}" data-name="{_esc(c["name"])}">'
                f'{_esc(c["name"])}</option>'
            )
            try:
                cexams = sdb.get_course_exams(int(c["id"]))
            except Exception:
                cexams = []
            course_exams_map[int(c["id"])] = [
                {"id": int(e["id"]), "name": e.get("name") or "Exam",
                 "date": e.get("exam_date") or ""}
                for e in cexams
            ]

        # JSON-encoded for safe injection into an f-string `{_json_course_exams}`.
        # Keys are coerced to strings so JS can look them up by the select value.
        _json_course_exams = json.dumps({str(k): v for k, v in course_exams_map.items()},
                                        ensure_ascii=False).replace("<", "\\u003c")

        _focus_is_en = session.get("lang", "es") == "en"
        _start_btn_label = "Start" if _focus_is_en else "Empezar"
        _pause_btn_label = "Pause" if _focus_is_en else "Pausar"
        _reset_btn_label = "Reset" if _focus_is_en else "Reiniciar"
        _skip_btn_label = "Skip" if _focus_is_en else "Saltar"
        _timer_ready_label = "Ready to focus" if _focus_is_en else "Listo para enfocarte"
        _pomo_count_label = "Session 1 of 4" if _focus_is_en else "Sesi&oacute;n 1 de 4"
        _fg_inactive_label = "Inactive" if _focus_is_en else "Inactivo"
        _fg_active_label = "Active — sites blocked" if _focus_is_en else "Activo — sitios bloqueados"
        _focus_js_i18n = {
            "breakTime": "☕ Break time!" if _focus_is_en else "☕ ¡Hora de descanso!",
            "focusTime": "🔥 Focus!" if _focus_is_en else "🔥 ¡Enfoque!",
            "longBreak": "🎉 Long break" if _focus_is_en else "🎉 Descanso largo",
            "break": "☕ Break" if _focus_is_en else "☕ Descanso",
            "sessionOne": "Session 1 of 4" if _focus_is_en else "Sesión 1 de 4",
            "sessionPrefix": "Session " if _focus_is_en else "Sesión ",
            "completedPrefix": "Completed " if _focus_is_en else "Completadas ",
            "ofFour": " of 4" if _focus_is_en else " de 4",
            "nextSession": "🔥 Starting the next session..." if _focus_is_en else "🔥 Empezando la siguiente sesión...",
            "sessionCompleted": "✓ Session completed!" if _focus_is_en else "✓ ¡Sesión completada!",
            "longBreakUnlocked": "🎉 Long break unlocked! Claim or lose everything" if _focus_is_en else "🎉 ¡Descanso largo desbloqueado! Reclama o pierdes todo",
            "claimWindowHelp": "You have 30 min to claim. Otherwise, everything accumulated is lost." if _focus_is_en else "Tienes 30 min para reclamar. Si no, todo lo acumulado se pierde.",
            "claimNow": "🎁 Claim now" if _focus_is_en else "🎁 Reclamar ahora",
            "pendingRewards": "Pending rewards" if _focus_is_en else "Recompensas pendientes",
            "claimRestartHelp": "Claiming finishes your session and restarts the timer." if _focus_is_en else "Reclamar termina tu sesión y reinicia el temporizador.",
            "claimRestart": "🎁 Claim and restart" if _focus_is_en else "🎁 Reclamar y reiniciar",
            "claiming": "Claiming..." if _focus_is_en else "Reclamando...",
            "retryClaim": "Retry claim" if _focus_is_en else "Reintentar reclamo",
            "longBreakClaimRewards": "🎉 Long break! Claim your rewards" if _focus_is_en else "🎉 ¡Descanso largo! Reclama tus recompensas",
            "claimThirty": "⏳ 30 min to claim — otherwise you lose everything" if _focus_is_en else "⏳ 30 min para reclamar — si no, pierdes todo",
            "sessionLost": "⌛ Session lost — you did not claim in time" if _focus_is_en else "⌛ Sesión perdida — no reclamaste a tiempo",
            "claimedPrefix": "✓ Claimed: +" if _focus_is_en else "✓ Reclamado: +",
            "minutesSavedSuffix": " min saved" if _focus_is_en else " min guardados",
            "unsavedPrefix": "Could not save " if _focus_is_en else "No se guardaron ",
            "unsavedSuffix": " session(s). Retry so you do not lose them." if _focus_is_en else " sesión(es). Reintenta para no perderlas.",
            "focusDoneTitle": "Focus session completed" if _focus_is_en else "Sesión de focus completada",
            "xpGranted": "Good work — XP granted!" if _focus_is_en else "¡Buen trabajo — XP otorgado!",
            "longBreakUnlockedSave": "Long break unlocked. Claim to save." if _focus_is_en else "Descanso largo desbloqueado. Reclama para guardar.",
            "activeBreak": "Active break before the next session." if _focus_is_en else "Descanso activo antes de la siguiente sesion.",
            "sessionInProgressSuffix": " in progress." if _focus_is_en else " en progreso.",
            "sessionsReadySuffix": " sessions ready to claim." if _focus_is_en else " sesiones listas para reclamar.",
            "startCycle": "Start a session to activate the cycle." if _focus_is_en else "Empieza una sesion para activar el ciclo.",
        }



        return _s_render("Focus Mode", f"""

        <style>
        .focus-page-head {{ display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:22px; }}
        .focus-eye {{ font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#FF7A3D; }}
        .focus-title {{ margin:0;font-family:Fraunces,Georgia,serif;font-size:48px;font-weight:600;letter-spacing:-.03em;color:#1A1A1F; }}
        .page-stats {{ display:flex;gap:10px;flex-wrap:wrap; }}
        .ps {{ background:#fff;border:1px solid #E2DCCC;border-radius:14px;padding:10px 14px;min-width:95px;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 6px rgba(20,18,30,.04); }}
        .ps-n {{ font-family:Fraunces,Georgia,serif;font-size:24px;font-weight:600;line-height:1;color:#1A1A1F; }}
        .ps-l {{ font-size:11px;font-weight:800;color:#94939C;text-transform:uppercase;letter-spacing:.08em;margin-top:3px; }}
        .focus-grid {{ display:grid;grid-template-columns:1.4fr 1fr;gap:18px; }}
        @media (max-width:1100px) {{ .focus-grid {{ grid-template-columns:1fr; }} }}
        .focus-timer-card {{ padding:26px;display:flex;flex-direction:column;gap:22px; }}
        .ft-tabs {{ display:none; }}
        .ft-tab {{ background:#EDE7DA;border:0;padding:8px 14px;border-radius:999px;font-weight:700;font-size:12px;color:#5C5C66;cursor:pointer; }}
        .ft-tab.active {{ background:#1A1A1F;color:#FFF8E1; }}
        .ft-stage {{ display:grid;place-items:center;padding:8px 0; }}
        .ft-ring-wrap {{ position:relative;width:min(320px,80vw);height:min(320px,80vw); }}
        .ft-ring {{ width:100%;height:100%; }}
        .ft-time {{ position:absolute;inset:0;display:grid;place-items:center;text-align:center; }}
        #timer-display {{ font-family:Fraunces,Georgia,serif !important;font-weight:500 !important;font-size:80px !important;letter-spacing:-.04em !important;line-height:1 !important;color:#1A1A1F !important; }}
        #timer-label {{ font-size:13px !important;color:#94939C !important;margin-top:8px !important;font-weight:700; }}
        #pomo-count {{ font-size:12px !important;color:#94939C !important;margin-top:4px !important;font-weight:700; }}
        .ft-controls {{ display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap; }}
        .ft-btn-main,.ft-controls #start-btn {{ background:#FF7A3D !important;color:#fff !important;border:0 !important;padding:14px 32px !important;border-radius:999px !important;font-weight:800 !important;font-size:16px !important;box-shadow:0 2px 0 rgba(20,18,30,.04),0 8px 22px rgba(20,18,30,.06) !important; }}
        .ft-btn-sec,.ft-controls #reset-btn,.ft-controls #pause-btn,.ft-controls #skip-btn {{ min-width:110px !important;height:44px !important;border-radius:999px !important;background:#EDE7DA !important;border:1px solid #E2DCCC !important;color:#1A1A1F !important;padding:0 16px !important;display:inline-flex !important;align-items:center !important;justify-content:center !important;gap:7px !important;font-weight:800 !important;white-space:nowrap !important;font-size:13px !important;box-shadow:none !important; }}
        .ft-context {{ border-top:1px solid #E2DCCC;padding-top:18px; }}
        .ft-lab {{ font-size:11px;font-weight:800;color:#94939C;text-transform:uppercase;letter-spacing:.1em;display:block;margin-bottom:8px; }}
        .ft-select,.ft-input {{ width:100%;padding:10px 12px;border-radius:12px;border:1px solid #E2DCCC;background:#FBF8F0;font-size:13px;font-weight:700;color:#1A1A1F; }}
        .focus-side {{ display:flex;flex-direction:column;gap:14px; }}
        .ses-pills {{ display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px; }}
        .sp {{ background:#EDE7DA;border:1px solid #E2DCCC;border-radius:12px;padding:10px 6px;text-align:center;font-weight:800;font-size:16px;color:#94939C;transition:all .18s ease; }}
        .sp small {{ display:block;font-size:10px;font-weight:700;color:#94939C;margin-top:2px; }}
        .sp.done {{ background:#D7EDDF;color:#2E9266;border-color:#2E9266; }}
        .sp.active {{ background:#FF7A3D;color:#fff;border-color:#FF7A3D; }}
        .break-row {{ display:flex;align-items:center;gap:10px;margin-top:10px;font-size:11px;color:#94939C; }}
        .break-bar {{ flex:1;height:6px;background:#EDE7DA;border-radius:999px;overflow:hidden;opacity:1; }}
        .break-bar span {{ display:block;height:100%;width:0%;background:linear-gradient(90deg,#FF7A3D,#F4B73A);border-radius:999px;transition:width .25s ease; }}
        .amb-grid {{ display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px; }}
        .amb {{ background:#FBF8F0;border:1px solid #E2DCCC;border-radius:12px;padding:12px 8px;text-align:center;cursor:pointer;transition:all .15s; }}
        .amb:hover {{ transform:translateY(-2px); }}
        .amb-ic {{ font-size:22px; }}
        .amb-n {{ font-size:11px;font-weight:700;margin-top:4px;color:#5C5C66; }}
        .amb.on {{ background:#ECE6FB;border-color:#5B4694; }}
        .vol-row {{ display:flex;align-items:center;gap:10px;margin-top:12px;font-size:14px; }}
        .vol-bar {{ flex:1;height:6px;background:#EDE7DA;border-radius:999px;overflow:hidden; }}
        .vol-fill {{ height:100%;background:#1A1A1F;border-radius:999px;width:45%; }}
        .block-list {{ margin-top:8px;display:flex;flex-direction:column;gap:4px; }}
        .block-item {{ display:flex;justify-content:space-between;padding:8px 10px;background:#FBF8F0;border-radius:10px;font-size:12px;font-weight:700; }}
        .muted {{ color:#94939C; }}
        </style>

        <div class="focus-page-head">
          <div class="focus-side">
            <div class="focus-eye">Estudia con foco</div>
            <h1 class="focus-title">Modo Enfoque</h1>
            <p style="margin:6px 0 0;color:#94939C;font-size:13px;">Mostrando lo que has estudiado <b style="color:#1A1A1F;">hoy</b> &middot; <span id="focus-today-label">{_today_label}</span></p>
          </div>
          <div class="page-stats">
            <div class="ps"><div class="ps-n" id="stat-hours">{focus_stats['total_hours']}</div><div class="ps-l">Hoy</div></div>
            <div class="ps"><div class="ps-n" id="stat-sessions">{focus_stats['sessions']}</div><div class="ps-l">Sesiones</div></div>
            <div class="ps"><div class="ps-n" id="stat-streak">{focus_stats['streak_days']}</div><div class="ps-l">Racha 🔥</div></div>
          </div>
        </div>

        <script>
        // Refresh today's stats with the BROWSER's local date so users in
        // negative-UTC timezones don't see ghost sessions belonging to the
        // server's tomorrow.
        (function(){{
          try {{
            var ld = new Date();
            var ldStr = ld.getFullYear()+'-'+String(ld.getMonth()+1).padStart(2,'0')+'-'+String(ld.getDate()).padStart(2,'0');
            fetch('/api/student/focus/stats?local_date=' + encodeURIComponent(ldStr))
              .then(function(r){{ return r.ok ? r.json() : null; }})
              .then(function(s){{
                if (!s) return;
                var h = document.getElementById('stat-hours');
                var ss = document.getElementById('stat-sessions');
                var st = document.getElementById('stat-streak');
                if (h)  h.textContent  = s.total_hours;
                if (ss) ss.textContent = s.sessions;
                if (st) st.textContent = s.streak_days;
              }})
              .catch(function(){{}});
          }} catch(e) {{}}
        }})();
        </script>



        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">

          <!-- Timer card -->

          <div class="card">

            <div style="display:none;" class="card-header"><h2>&#9201; Temporizador de estudio</h2></div>



            <!-- Mode tabs -->

            <div class="ft-tabs">

              <button onclick="setMode('pomodoro')" class="ft-tab mode-btn active" id="mode-pomodoro">Pomodoro &middot; 25</button>

            </div>



            <!-- Course selector -->

            <div class="form-group" style="margin-bottom:12px;">

              <label style="font-size:12px;">Estudiando para: <span style="color:#ef4444;">*</span></label>

              <select id="focus-course" class="edit-input" required onchange="onFocusCourseChange()">

                <option value="">— Elige un curso para empezar —</option>

                {course_options}

              </select>

              <div id="focus-course-warn" style="display:none;font-size:12px;color:#ef4444;margin-top:6px;">

                Elige un curso antes de empezar el temporizador.

              </div>

            </div>

            <!-- Exam selector (optional — only shows once a course is picked) -->

            <div class="form-group" id="focus-exam-group" style="margin-bottom:12px;display:none;">

              <label style="font-size:12px;">&iquest;Estudiando para qu&eacute; prueba? <span style="color:var(--text-muted);font-weight:400;">(opcional)</span></label>

              <select id="focus-exam" class="edit-input">

                <option value="">— Estudio general del curso —</option>

              </select>

              <div id="focus-exam-empty" style="display:none;font-size:12px;color:var(--text-muted);margin-top:6px;">

                A&uacute;n no hay pruebas para este curso. Agr&eacute;galas en <a href="/student/exams" style="color:var(--primary);">la pesta&ntilde;a de cursos</a>.

              </div>

            </div>



            <!-- Pomodoro settings -->

            <div id="settings-pomodoro">

              <div style="display:flex;gap:10px;margin-bottom:12px;">

                <div class="form-group" style="flex:1;">

                  <label style="font-size:12px;">Trabajo (min)</label>

                  <input type="number" id="pomo-work" value="25" min="5" max="120" class="edit-input">

                </div>

                <div class="form-group" style="flex:1;">

                  <label style="font-size:12px;">Descanso (min)</label>

                  <input type="number" id="pomo-break" value="5" min="1" max="30" class="edit-input">

                </div>

                <div class="form-group" style="flex:1;">

                  <label style="font-size:12px;">Descanso largo (min)</label>

                  <input type="number" id="pomo-long" value="15" min="5" max="60" class="edit-input">

                </div>

              </div>

              <p style="font-size:12px;color:var(--text-muted);margin:0;">Descanso largo cada 4 sesiones.</p>

            </div>



            <!-- Page method settings (removed) -->

            <div id="settings-pages" style="display:none;" hidden>

              <div class="form-group" style="margin-bottom:12px;">

                <label style="font-size:12px;">Páginas objetivo</label>

                <input type="number" id="page-target" value="20" min="1" max="500" class="edit-input" onchange="updatePageProgress()">

              </div>

              <!-- Big page counter display -->

              <div style="text-align:center;margin:12px 0;">

                <div id="page-counter-display" style="font-size:48px;font-weight:800;color:var(--primary);">0</div>

                <div id="page-status" style="font-size:14px;color:var(--text-muted);">0 / 20 pages</div>

              </div>

              <div style="background:var(--bg);border-radius:8px;height:12px;overflow:hidden;margin-bottom:16px;">

                <div id="page-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:0%;transition:width 0.5s ease;border-radius:8px;"></div>

              </div>

              <!-- Big satisfying page-done button -->

              <button id="page-done-btn" onclick="clickPage()" style="

                display:block;width:100%;padding:20px;font-size:22px;font-weight:700;

                background:linear-gradient(135deg,var(--primary),#8B5CF6);color:#fff;

                border:none;border-radius:16px;cursor:pointer;

                transition:transform 0.15s ease,box-shadow 0.15s ease;

                box-shadow:0 4px 16px rgba(139,92,246,0.3);

                user-select:none;

              " onmousedown="this.style.transform='scale(0.95)'" onmouseup="this.style.transform='scale(1)'" onmouseleave="this.style.transform='scale(1)'">

                &#128214; Page Completed!

              </button>

              <div style="display:flex;gap:8px;margin-top:10px;justify-content:center;">

                <button onclick="undoPage()" class="btn btn-ghost btn-sm" style="font-size:12px;">&#8630; Undo last</button>

                <button onclick="resetPages()" class="btn btn-ghost btn-sm" style="font-size:12px;">&#128260; Reset</button>

              </div>

            </div>



            <!-- Custom timer settings -->

            <div id="settings-custom" style="display:none;">

              <div class="form-group" style="margin-bottom:12px;">

                <label style="font-size:12px;">Duraci&oacute;n (min)</label>

                <input type="number" id="custom-mins" value="45" min="5" max="300" class="edit-input">

              </div>

            </div>



            <!-- Timer display -->

            <div class="ft-stage"><div class="ft-ring-wrap"><svg class="ft-ring" viewBox="0 0 200 200"><circle cx="100" cy="100" r="92" fill="none" stroke="#E2DCCC" stroke-width="6"/><circle id="ft-ring-progress" cx="100" cy="100" r="92" fill="none" stroke="#FF7A3D" stroke-width="6" stroke-linecap="round" stroke-dasharray="578" stroke-dashoffset="0" transform="rotate(-90 100 100)"/></svg><div class="ft-time"><div>

              <div id="timer-display" style="font-size:64px;font-weight:800;font-family:monospace;color:var(--text);letter-spacing:2px;">25:00</div>

              <div id="timer-label" style="font-size:14px;color:var(--text-muted);margin-top:4px;">{_timer_ready_label}</div>

              <div id="pomo-count" style="font-size:12px;color:var(--text-muted);margin-top:4px;">{_pomo_count_label}</div>

            </div></div></div></div>



            <!-- Controls -->

            <div class="ft-controls">

              <button onclick="startTimer()" id="start-btn" class="btn btn-primary">&#9654; {_start_btn_label}</button>

              <button onclick="pauseTimer()" id="pause-btn" class="btn btn-outline" style="display:none;"><span>&#10074;&#10074;</span><span>{_pause_btn_label}</span></button>

              <button onclick="resetTimer()" id="reset-btn" class="btn btn-outline"><span>&#8635;</span><span>{_reset_btn_label}</span></button>

              <button onclick="skipPhase()" id="skip-btn" class="btn btn-ghost btn-sm" style="display:none;"><span>{_skip_btn_label}</span><span>&raquo;</span></button>

            </div>

            <!-- Reward claim card (pending XP / minutes / sessions) -->

            <div id="claim-counter" style="display:none;margin-top:14px;padding:14px 16px;border:2px solid var(--primary);background:rgba(139,92,246,0.08);border-radius:12px;">

              <div id="claim-headline" style="font-weight:700;color:var(--text);margin-bottom:10px;text-align:center;font-size:13px;">

                Recompensas pendientes

              </div>

              <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center;margin-bottom:12px;">

                <div>

                  <div id="claim-minutes" style="font-size:22px;font-weight:800;color:var(--primary);">0</div>

                  <div style="font-size:11px;color:var(--text-muted);">min estudiados</div>

                </div>

                <div>

                  <div id="claim-sessions" style="font-size:22px;font-weight:800;color:var(--primary);">0</div>

                  <div style="font-size:11px;color:var(--text-muted);">sesiones</div>

                </div>

                <div>

                  <div id="claim-xp" style="font-size:22px;font-weight:800;color:var(--primary);">0</div>

                  <div style="font-size:11px;color:var(--text-muted);">XP</div>

                </div>

              </div>

              <button id="claim-btn" onclick="claimNow()" class="btn btn-primary" style="width:100%;font-weight:700;">

                &#127873; Reclamar y reiniciar

              </button>

              <p id="claim-help" style="font-size:11px;color:var(--text-muted);text-align:center;margin:8px 0 0;">

                Reclamar termina tu sesi&oacute;n y reinicia el temporizador.

              </p>

            </div>

            <p style="font-size:11px;color:var(--text-muted);text-align:center;margin-top:10px;">

              Atajos: <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">Espacio</kbd> iniciar/pausar

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">R</kbd> reiniciar

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">S</kbd> saltar

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">P</kbd> p&aacute;gina lista

            </p>

          </div>



          <!-- Right column -->

          <div>

            <div class="card">
              <div class="card-header"><h2>&#9881;&#65039; Sesi&oacute;n de hoy</h2></div>
              <div class="ses-pills" id="today-session-pills">
                <div class="sp">1<small>25:00</small></div>
                <div class="sp">2<small>25:00</small></div>
                <div class="sp">3<small>25:00</small></div>
                <div class="sp">4<small>25:00</small></div>
              </div>
              <div class="break-row"><div class="break-bar"><span id="today-session-progress"></span></div><span id="today-session-caption">Empieza una sesi&oacute;n para activar el ciclo.</span></div>
            </div>

            <div class="card">
              <div class="card-header"><h2>&#127807; Ambiente</h2></div>
              <div class="amb-grid">
                <div class="amb on" onclick="setAmbience(this,'off')"><div class="amb-ic">&#128263;</div><div class="amb-n">Silencio</div></div>
                <div class="amb" onclick="setAmbience(this,'rain')"><div class="amb-ic">&#127783;</div><div class="amb-n">Lluvia suave</div></div>
                <div class="amb" onclick="setAmbience(this,'ocean')"><div class="amb-ic">&#127754;</div><div class="amb-n">Olas</div></div>
                <div class="amb" onclick="setAmbience(this,'forest')"><div class="amb-ic">&#127794;</div><div class="amb-n">Bosque</div></div>
                <div class="amb" onclick="setAmbience(this,'fire')"><div class="amb-ic">&#128293;</div><div class="amb-n">Fuego</div></div>
                <div class="amb" onclick="setAmbience(this,'brown')"><div class="amb-ic">&#127769;</div><div class="amb-n">Ruido bajo</div></div>
              </div>
              <div class="vol-row"><span>&#128264;</span><div class="vol-bar"><div class="vol-fill" id="amb-vol-fill"></div></div><span>&#128266;</span></div>
              <audio id="amb-audio" loop preload="none"></audio>
            </div>

            <!-- Spotify card removed from UI -->

            <div class="card" style="display:none;margin-bottom:16px;">

              <div class="card-header"><h2>&#127925; M&uacute;sica para estudiar</h2></div>

              <div style="margin-bottom:12px;">

                <label style="font-size:12px;color:var(--text-muted);">Pega un enlace de playlist o &aacute;lbum de Spotify:</label>

                <div style="display:flex;gap:8px;margin-top:4px;">

                  <input type="text" id="spotify-url" class="edit-input" placeholder="https://open.spotify.com/playlist/..."

                    value="https://open.spotify.com/playlist/0vvXsWCC9xrXsKd4FyS8kM">

                  <button onclick="loadSpotify()" class="btn btn-outline btn-sm">Cargar</button>

                </div>

                <small style="display:block;margin-top:6px;color:var(--text-muted);font-size:11px;">La música suena mientras estás en esta página. Para música sin interrupciones usa la app de escritorio de Spotify o una pestaña separada.</small>

              </div>

              <div id="spotify-embed">

                <iframe id="spotify-iframe" style="border-radius:12px;width:100%;height:352px;border:0;"

                  src="https://open.spotify.com/embed/playlist/0vvXsWCC9xrXsKd4FyS8kM?utm_source=generator&theme=0"

                  allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>

              </div>

              <div style="margin-top:8px;">

                <p style="font-size:11px;color:var(--text-muted);">Selecci&oacute;n r&aacute;pida:</p>

                <div style="display:flex;flex-wrap:wrap;gap:6px;">

                  <button onclick="setPlaylist('0vvXsWCC9xrXsKd4FyS8kM')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127911; Lo-fi Beats</button>

                  <button onclick="setPlaylist('37i9dQZF1DWWQRwui0ExPn')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127926; Lo-Fi</button>

                  <button onclick="setPlaylist('37i9dQZF1DX8Uebhn9wzrS')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127764; Chill Study</button>

                  <button onclick="setPlaylist('37i9dQZF1DX9sIqqvKsjG8')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#127793; Deep Focus</button>

                  <button onclick="setPlaylist('37i9dQZF1DWZeKCadgRdKQ')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#9749; Coffee Jazz</button>

                </div>

              </div>

            </div>



            <!-- Focus Guard card -->

            <div class="card" id="focus-guard-card" style="position:relative;overflow:hidden;">

              <div aria-hidden="true" style="position:absolute;inset:0;background:radial-gradient(1200px 220px at -10% -20%, rgba(139,92,246,.12), transparent 60%), radial-gradient(900px 200px at 120% 120%, rgba(99,102,241,.10), transparent 60%);pointer-events:none;"></div>

              <div class="card-header" style="position:relative;z-index:1;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">

                <h2 style="margin:0;">&#128737;&#65039; Focus Guard</h2>

                <span id="fg-status" style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px;background:rgba(148,163,184,.12);color:var(--text-muted);border:1px solid rgba(148,163,184,.2);">

                  <span id="fg-dot" style="width:8px;height:8px;border-radius:50%;background:#64748b;display:inline-block;"></span>

                  <span id="fg-label">{_fg_inactive_label}</span>

                </span>

              </div>

              <div style="position:relative;z-index:1;">

                <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;line-height:1.55;">

                  Bloquea Instagram, TikTok, Twitter/X y otras distracciones automáticamente durante la sesión.

                  <b style="color:var(--text);">YouTube sigue permitido</b> — porque podr&iacute;as estar estudiando de verdad.

                </p>



                <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;">

                  <span class="fg-chip">Instagram</span>

                  <span class="fg-chip">TikTok</span>

                  <span class="fg-chip">Twitter/X</span>

                  <span class="fg-chip">Facebook</span>

                  <span class="fg-chip">Reddit</span>

                  <span class="fg-chip">Snapchat</span>

                  <span class="fg-chip">Twitch</span>

                  <span class="fg-chip">Netflix</span>

                  <span class="fg-chip fg-chip-allow">YouTube &#10003;</span>

                </div>



                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">

                  <a href="/download/focus-guard.zip" class="btn btn-primary btn-sm" download>&#11015; Descargar extensi&oacute;n</a>

                  <button onclick="document.getElementById('fg-how').style.display='block';this.style.display='none';" class="btn btn-outline btn-sm">Cómo instalar</button>

                </div>



                <div id="fg-how" style="display:none;background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px 14px;font-size:12px;color:var(--text-muted);line-height:1.7;">

                  <b style="color:var(--text);">Instala en 30 segundos:</b>

                  <ol style="margin:6px 0 0 18px;padding:0;">

                    <li>Descomprime el archivo descargado.</li>

                    <li>Abre <code style="background:rgba(139,92,246,.12);padding:1px 5px;border-radius:3px;">chrome://extensions</code> (o <code style="background:rgba(139,92,246,.12);padding:1px 5px;border-radius:3px;">edge://extensions</code>).</li>

                    <li>Activa <b>Modo desarrollador</b> (arriba a la derecha).</li>

                    <li>Haz clic en <b>Cargar sin empaquetar</b> y selecciona la carpeta <code style="background:rgba(139,92,246,.12);padding:1px 5px;border-radius:3px;">focus-guard</code>.</li>

                    <li>Inicia un temporizador aqu&iacute; — las distracciones externas se bloquean autom&aacute;ticamente.</li>

                  </ol>

                </div>

              </div>

            </div>



          </div>

        </div>



        <style>

        .fg-chip {{ font-size:11px;padding:3px 8px;border-radius:999px;background:rgba(239,68,68,.10);color:#fca5a5;border:1px solid rgba(239,68,68,.2);font-weight:600; }}

        .fg-chip-allow {{ background:rgba(34,197,94,.10);color:#86efac;border-color:rgba(34,197,94,.25); }}

        #fg-status.active {{ background:linear-gradient(135deg,rgba(139,92,246,.18),rgba(99,102,241,.18));color:#C7D2FE;border-color:rgba(139,92,246,.35); }}

        #fg-status.active #fg-dot {{ background:#22c55e;box-shadow:0 0 10px #22c55e;animation:fgPulse 1.4s infinite; }}

        @keyframes fgPulse {{ 50% {{ opacity:.4; }} }}

        .mode-btn.active {{ background:var(--primary);color:#fff;border-color:var(--primary); }}

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        #page-done-btn:hover {{ box-shadow:0 6px 24px rgba(139,92,246,0.45); }}

        #page-done-btn:active {{ transform:scale(0.93) !important; }}

        @keyframes pagePop {{ 0%{{transform:scale(1);}} 50%{{transform:scale(1.15);}} 100%{{transform:scale(1);}} }}

        @keyframes confettiFade {{ 0%{{opacity:1;transform:translateY(0) rotate(0deg);}} 100%{{opacity:0;transform:translateY(-60px) rotate(180deg);}} }}

        .page-confetti {{ position:absolute;pointer-events:none;font-size:20px;animation:confettiFade 0.8s ease-out forwards; }}

        </style>



        <script>

        /* === Background-safe alarm =========================================

           We use BOTH:
           1) A pre-loaded HTML5 <audio> element with a base64 WAV. HTML5 audio
              continues to work in background tabs (unlike WebAudio which is
              suspended). It must be primed by a user gesture (start button)
              once per session so browsers allow background playback.
           2) WebAudio chime as a fallback for when the page IS focused.
           3) Desktop Notification when permission has been granted, so the
              user gets an alert even if the tab is hidden and audio is muted.
        */

        var alarmCtx = null;
        var alarmAudio = null;          // HTML5 <audio> element (background-safe)
        var keepaliveAudio = null;      // silent looping audio — keeps tab UNTHROTTLED
        var alarmPrimed = false;
        var notifPermission = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';

        function buildAlarmWavDataUri() {{
          // Generate a short 3-tone bell as a 16-bit PCM WAV, encode as data URI.
          // (~1.2s @ 22050Hz mono = ~26KB base64.)
          var sampleRate = 22050;
          var duration = 1.4;
          var n = Math.floor(sampleRate * duration);
          var buffer = new ArrayBuffer(44 + n * 2);
          var view = new DataView(buffer);
          function writeStr(off, s){{ for(var i=0;i<s.length;i++) view.setUint8(off+i, s.charCodeAt(i)); }}
          writeStr(0,'RIFF'); view.setUint32(4, 36 + n*2, true);
          writeStr(8,'WAVEfmt '); view.setUint32(16,16,true); view.setUint16(20,1,true);
          view.setUint16(22,1,true); view.setUint32(24,sampleRate,true);
          view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
          writeStr(36,'data'); view.setUint32(40, n*2, true);
          var freqs = [523.25, 659.25, 783.99];  // C5 E5 G5
          for (var i=0; i<n; i++) {{
            var t = i / sampleRate;
            // Three staggered chimes
            var s = 0;
            for (var k=0; k<3; k++) {{
              var start = k * 0.35;
              if (t >= start && t < start + 0.6) {{
                var local = t - start;
                var env = Math.exp(-local * 5);
                s += Math.sin(2 * Math.PI * freqs[k] * local) * env * 0.3;
              }}
            }}
            var v = Math.max(-1, Math.min(1, s));
            view.setInt16(44 + i*2, v * 0x7FFF, true);
          }}
          // Convert to base64
          var bytes = new Uint8Array(buffer);
          var binary = '';
          for (var j=0; j<bytes.length; j++) binary += String.fromCharCode(bytes[j]);
          return 'data:audio/wav;base64,' + btoa(binary);
        }}

        function buildSilenceWavDataUri() {{
          // 1-second mono silent 16-bit PCM WAV (very small).
          // Looping this in an <audio> element prevents Chrome from suspending
          // / throttling the tab — tabs marked "playing audio" are never throttled.
          var sampleRate = 8000;
          var n = sampleRate;
          var buffer = new ArrayBuffer(44 + n * 2);
          var view = new DataView(buffer);
          function w(off, s){{ for(var i=0;i<s.length;i++) view.setUint8(off+i, s.charCodeAt(i)); }}
          w(0,'RIFF'); view.setUint32(4, 36+n*2, true);
          w(8,'WAVEfmt '); view.setUint32(16,16,true); view.setUint16(20,1,true);
          view.setUint16(22,1,true); view.setUint32(24,sampleRate,true);
          view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
          w(36,'data'); view.setUint32(40, n*2, true);
          // body is already zeroed = silence
          var bytes = new Uint8Array(buffer);
          var binary = '';
          for (var j=0; j<bytes.length; j++) binary += String.fromCharCode(bytes[j]);
          return 'data:audio/wav;base64,' + btoa(binary);
        }}

        function startKeepalive() {{
          // Keep tab unthrottled by playing a silent looping audio track.
          try {{
            if (!keepaliveAudio) {{
              keepaliveAudio = new Audio(buildSilenceWavDataUri());
              keepaliveAudio.loop = true;
              keepaliveAudio.preload = 'auto';
              keepaliveAudio.volume = 0.001; // effectively silent but counts as "playing audio"
            }}
            keepaliveAudio.currentTime = 0;
            var p = keepaliveAudio.play();
            if (p && p.catch) p.catch(function(){{}});
          }} catch(e) {{}}
        }}

        function stopKeepalive() {{
          try {{
            if (keepaliveAudio) {{
              keepaliveAudio.pause();
              keepaliveAudio.currentTime = 0;
            }}
          }} catch(e) {{}}
        }}

        function primeAlarm() {{
          // MUST be called from a user-gesture handler (e.g. Start button) to
          // unlock both WebAudio and HTML5 audio for later background playback.
          if (alarmPrimed) return;
          try {{
            if (!alarmAudio) {{
              alarmAudio = new Audio(buildAlarmWavDataUri());
              alarmAudio.preload = 'auto';
              alarmAudio.volume = 0.7;
              // Play silently to unlock autoplay policy
              alarmAudio.muted = true;
              var p = alarmAudio.play();
              if (p && p.then) p.then(function(){{
                alarmAudio.pause();
                alarmAudio.currentTime = 0;
                alarmAudio.muted = false;
              }}).catch(function(){{
                alarmAudio.muted = false;
              }});
            }}
            if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (alarmCtx.state === 'suspended') alarmCtx.resume().catch(function(){{}});
          }} catch(e) {{}}
          // Ask for notification permission once, so we can alert when hidden
          if (typeof Notification !== 'undefined' && Notification.permission === 'default') {{
            Notification.requestPermission().then(function(p){{ notifPermission = p; }}).catch(function(){{}});
          }}
          alarmPrimed = true;
        }}

        function showNotification(title, body) {{
          try {{
            if (typeof Notification === 'undefined') return;
            if (Notification.permission !== 'granted') return;
            var n = new Notification(title, {{ body: body, silent: false, tag: 'machreach-focus' }});
            n.onclick = function(){{ window.focus(); n.close(); }};
          }} catch(e) {{}}
        }}

        function playAlarm() {{
          // 1) HTML5 audio (works in background)
          try {{
            if (alarmAudio) {{
              alarmAudio.currentTime = 0;
              var p = alarmAudio.play();
              if (p && p.catch) p.catch(function(){{}});
            }}
          }} catch(e) {{}}
          // 2) WebAudio (richer sound when tab is focused)
          try {{
            if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
            var ctx = alarmCtx;
            if (ctx.state === 'suspended' && ctx.resume) {{
              ctx.resume().then(function(){{ playAlarmTones(ctx); }}).catch(function(){{}});
            }} else {{
              playAlarmTones(ctx);
            }}
          }} catch(e) {{}}
        }}

        function playAlarmTones(ctx) {{

          try {{

            var now = ctx.currentTime;

            // Gentle 3-chime bell sound

            var freqs = [523.25, 659.25, 783.99]; // C5, E5, G5 major chord

            freqs.forEach(function(freq, i) {{

              var osc = ctx.createOscillator();

              var gain = ctx.createGain();

              osc.type = 'sine';

              osc.frequency.value = freq;

              gain.gain.setValueAtTime(0, now + i * 0.5);

              gain.gain.linearRampToValueAtTime(0.25, now + i * 0.5 + 0.05);

              gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.5 + 1.5);

              osc.connect(gain);

              gain.connect(ctx.destination);

              osc.start(now + i * 0.5);

              osc.stop(now + i * 0.5 + 1.8);

            }});

            // Final gentle chord

            [523.25, 659.25, 783.99].forEach(function(freq) {{

              var osc = ctx.createOscillator();

              var gain = ctx.createGain();

              osc.type = 'sine';

              osc.frequency.value = freq;

              gain.gain.setValueAtTime(0, now + 1.8);

              gain.gain.linearRampToValueAtTime(0.15, now + 1.9);

              gain.gain.exponentialRampToValueAtTime(0.001, now + 3.5);

              osc.connect(gain);

              gain.connect(ctx.destination);

              osc.start(now + 1.8);

              osc.stop(now + 3.8);

            }});

          }} catch(e) {{}}

        }}



        /* === Timer state === */

        var timerInterval = null;

        var timeLeft = 25 * 60;

        var totalTime = 25 * 60;

        var isRunning = false;

        var isBreak = false;

        var pomoCount = 0;

        var phaseEndAt = null;        // wall-clock end time of current countdown phase

        var phaseEnded = false;       // guard so onTimerEnd fires only once per phase

        var currentMode = 'pomodoro';
        var focusText = {json.dumps(_focus_js_i18n, ensure_ascii=False)};

        var totalFocusSeconds = 0;

        var sessionStarted = false;

        var pageDone = 0;

        var phaseStartFocusSeconds = 0;

        // True between phase-start and phase-end (kept across pauses). Lets us
        // tell "fresh phase start" from "resume after pause" so we don't reset
        // phaseStartFocusSeconds on resume — that bug made paused sessions
        // credit only the post-resume minutes.
        var __phaseOpen = false;

        // Mandatory-claim countdown end (Date.now() + 30min). Kept in sync
        // with localStorage so a tab reload restores the timer.
        var __mandatoryEndAt = null;
        var __mandatoryInterval = null;

        // course_id → [{{id,name,date}}]. Serialized server-side so the exam
        // dropdown can filter client-side without an extra round trip.
        var FOCUS_COURSE_EXAMS = {_json_course_exams};

        function onFocusCourseChange() {{
          var sel = document.getElementById('focus-course');
          var examGroup = document.getElementById('focus-exam-group');
          var examSel = document.getElementById('focus-exam');
          var emptyMsg = document.getElementById('focus-exam-empty');
          if (!sel || !examGroup || !examSel) return;
          var cid = sel.value;
          if (!cid) {{
            examGroup.style.display = 'none';
            examSel.value = '';
            return;
          }}
          var exams = FOCUS_COURSE_EXAMS[cid] || [];
          // Wipe and rebuild options.
          while (examSel.options.length > 1) examSel.remove(1);
          exams.forEach(function(e){{
            var o = document.createElement('option');
            o.value = e.id;
            var dateTxt = e.date ? (' · ' + e.date) : '';
            o.textContent = e.name + dateTxt;
            examSel.appendChild(o);
          }});
          examGroup.style.display = '';
          emptyMsg.style.display = exams.length ? 'none' : 'block';
        }}

        function saveFocusTimerState() {{

          localStorage.setItem('focus_timer_state', JSON.stringify({{

            currentMode: currentMode, isBreak: isBreak, pomoCount: pomoCount,

            totalFocusSeconds: totalFocusSeconds, phaseStartFocusSeconds: phaseStartFocusSeconds,

            totalTime: totalTime,
            course: document.getElementById('focus-course').value,
            exam: document.getElementById('focus-exam') ? document.getElementById('focus-exam').value : ''

          }}));

        }}

        function clearFocusTimerState() {{

          localStorage.removeItem('focus_timer_state');

        }}



        /* === Mode switching === */

        function setMode(mode) {{

          if (mode === 'pages') {{ mode = 'pomodoro'; }}

          currentMode = mode;

          document.querySelectorAll('.ft-tab').forEach(function(b) {{ b.classList.remove('active'); }});
          document.querySelectorAll('.mode-btn').forEach(function(b) {{ b.classList.remove('active'); b.classList.add('btn-outline'); }});

          var modeBtn = document.getElementById('mode-' + mode);

          if (modeBtn) {{ modeBtn.classList.add('active'); modeBtn.classList.remove('btn-outline'); }}

          document.getElementById('settings-pomodoro').style.display = mode === 'pomodoro' ? '' : 'none';

          document.getElementById('settings-custom').style.display = mode === 'custom' ? '' : 'none';

          document.getElementById('pomo-count').style.display = mode === 'pomodoro' ? '' : 'none';

          resetTimer();

          if (mode === 'pomodoro') {{

            timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;

          }} else {{

            timeLeft = parseInt(document.getElementById('custom-mins').value) * 60;

          }}

          totalTime = timeLeft;

          updateDisplay();

        }}

        function setDeepMode(mins, btn) {{
          currentMode = 'custom';
          document.querySelectorAll('.ft-tab').forEach(function(b) {{ b.classList.remove('active'); }});
          if (btn) btn.classList.add('active');
          var custom = document.getElementById('custom-mins');
          if (custom) custom.value = mins;
          document.getElementById('settings-pomodoro').style.display = 'none';
          document.getElementById('settings-custom').style.display = 'none';
          document.getElementById('pomo-count').style.display = 'none';
          resetTimer();
          timeLeft = mins * 60;
          totalTime = timeLeft;
          updateDisplay();
        }}



        function updateDisplay() {{

          var m = Math.floor(timeLeft / 60);

          var s = timeLeft % 60;

          document.getElementById('timer-display').textContent = String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
          var ring = document.getElementById('ft-ring-progress');
          if (ring && totalTime) {{
            var pct = Math.max(0, Math.min(1, timeLeft / totalTime));
            ring.style.strokeDashoffset = String(578 * (1 - pct));
          }}
          updateTodaySessionCard();

        }}

        function updateTodaySessionCard() {{
          try {{
            var wrap = document.getElementById('today-session-pills');
            if (!wrap) return;
            var workMins = parseInt(document.getElementById('pomo-work').value, 10) || 25;
            var completed = 0;
            try {{ completed = getPendingPhases().length || 0; }} catch(e) {{ completed = pomoCount || 0; }}
            if (pomoCount > completed) completed = pomoCount;
            var cycleCompleted = completed % 4;
            var activeIndex = isBreak ? -1 : Math.min(3, cycleCompleted);
            var pills = wrap.querySelectorAll('.sp');
            for (var i = 0; i < pills.length; i++) {{
              pills[i].classList.remove('done','active');
              pills[i].innerHTML = (i + 1) + '<small>' + workMins + ':00</small>';
              if (i < cycleCompleted) pills[i].classList.add('done');
              if (i === activeIndex && (isRunning || sessionStarted)) pills[i].classList.add('active');
            }}
            var progress = document.getElementById('today-session-progress');
            var caption = document.getElementById('today-session-caption');
            var pct = 0;
            if (isRunning && totalTime > 0) pct = Math.max(0, Math.min(100, Math.round((1 - (timeLeft / totalTime)) * 100)));
            else pct = cycleCompleted * 25;
            if (progress) progress.style.width = pct + '%';
            if (caption) {{
              if (isBreak) caption.textContent = (cycleCompleted === 0 ? focusText.longBreakUnlockedSave : focusText.activeBreak);
              else if (isRunning) caption.textContent = focusText.sessionPrefix + (activeIndex + 1) + focusText.sessionInProgressSuffix;
              else if (completed > 0) caption.textContent = completed + focusText.sessionsReadySuffix;
              else caption.textContent = focusText.startCycle;
            }}
          }} catch(e) {{}}
        }}

        function setAmbience(el, type) {{
          document.querySelectorAll('.amb').forEach(function(a) {{ a.classList.remove('on'); }});
          if (el) el.classList.add('on');
          try {{
            var audioMap = {{
              rain: '/static/audio/rain.mp3',
              ocean: '/static/audio/beach.mp3',
              forest: '/static/audio/forest.mp3',
              fire: '/static/audio/fire-crackling.mp3'
            }};
            var audioEl = document.getElementById('amb-audio');
            if (window.__ambStop) window.__ambStop();
            if (audioEl) {{
              try {{ audioEl.pause(); audioEl.currentTime = 0; }} catch(e) {{}}
            }}
            if (type === 'off') {{
              window.__ambStop = null;
              var vf0 = document.getElementById('amb-vol-fill');
              if (vf0) vf0.style.width = '0%';
              return;
            }}
            if (audioEl && audioMap[type]) {{
              audioEl.src = audioMap[type];
              audioEl.loop = true;
              audioEl.volume = 0.34;
              var p = audioEl.play();
              if (p && p.catch) p.catch(function(){{}});
              var vfMp3 = document.getElementById('amb-vol-fill');
              if (vfMp3) vfMp3.style.width = '42%';
              window.__ambStop = function() {{
                try {{ audioEl.pause(); audioEl.currentTime = 0; }} catch(e) {{}}
              }};
              return;
            }}
            if (!window.__ambCtx) window.__ambCtx = new (window.AudioContext || window.webkitAudioContext)();
            var ctx = window.__ambCtx;
            var gain = ctx.createGain();
            gain.gain.value = 0.018;
            gain.connect(ctx.destination);
            var nodes = [];
            function filter(freq, q, kind) {{
              var f = ctx.createBiquadFilter();
              f.type = kind || 'lowpass';
              f.frequency.value = freq;
              f.Q.value = q || 0.7;
              f.connect(gain);
              nodes.push(f);
              return f;
            }}
            function noise(target, smooth) {{
              var buffer = ctx.createBuffer(1, ctx.sampleRate * 4, ctx.sampleRate);
              var data = buffer.getChannelData(0);
              var last = 0;
              for (var i=0;i<data.length;i++) {{
                var white = Math.random() * 2 - 1;
                last = (last * (smooth || 0.94)) + (white * (1 - (smooth || 0.94)));
                data[i] = last;
              }}
              var src = ctx.createBufferSource();
              src.buffer = buffer; src.loop = true; src.connect(target || gain); src.start(); nodes.push(src);
            }}
            if (type === 'rain') {{ noise(filter(1600, 0.4, 'lowpass'), 0.72); }}
            if (type === 'ocean') {{ noise(filter(420, 0.9, 'lowpass'), 0.96); }}
            if (type === 'forest') {{ noise(filter(2600, 1.2, 'bandpass'), 0.9); }}
            if (type === 'fire') {{ noise(filter(900, 0.8, 'lowpass'), 0.82); }}
            if (type === 'brown') {{ noise(filter(320, 0.5, 'lowpass'), 0.985); }}
            var vf = document.getElementById('amb-vol-fill');
            if (vf) vf.style.width = '26%';
            window.__ambStop = function() {{ nodes.forEach(function(n) {{ try {{ n.stop(); }} catch(e) {{}} }}); try {{ gain.disconnect(); }} catch(e) {{}} }};
          }} catch(e) {{}}
        }}



        /* === Page method click === */

        function clickPage() {{

          if (!isRunning) {{ startTimer(); }}

          pageDone++;

          document.getElementById('page-counter-display').textContent = pageDone;

          document.getElementById('page-counter-display').style.animation = 'none';

          void document.getElementById('page-counter-display').offsetWidth;

          document.getElementById('page-counter-display').style.animation = 'pagePop 0.3s ease';

          updatePageProgress();

          // Confetti burst

          var btn = document.getElementById('page-done-btn');

          var rect = btn.getBoundingClientRect();

          var emojis = ['&#127881;','&#11088;','&#128214;','&#127942;','&#10024;','&#128640;'];

          for (var i = 0; i < 6; i++) {{

            var span = document.createElement('span');

            span.className = 'page-confetti';

            span.innerHTML = emojis[i % emojis.length];

            span.style.left = (rect.left + Math.random() * rect.width) + 'px';

            span.style.top = (rect.top + window.scrollY - 10) + 'px';

            document.body.appendChild(span);

            setTimeout(function(s){{ s.remove(); }}, 900, span);

          }}

        }}

        function undoPage() {{

          if (pageDone > 0) {{

            pageDone--;

            document.getElementById('page-counter-display').textContent = pageDone;

            updatePageProgress();

          }}

        }}

        function resetPages() {{

          pageDone = 0;

          document.getElementById('page-counter-display').textContent = '0';

          updatePageProgress();

        }}

        function updatePageProgress() {{

          var target = parseInt(document.getElementById('page-target').value) || 1;

          var pct = Math.min(100, Math.round(pageDone / target * 100));

          document.getElementById('page-bar').style.width = pct + '%';

          document.getElementById('page-status').textContent = pageDone + ' / ' + target + ' pages (' + pct + '%)';

          if (pageDone >= target && sessionStarted) {{

            playAlarm();

            saveFocusSession();

            document.getElementById('timer-label').textContent = '&#127881; Page goal reached!';

          }}

        }}



        /* === Timer controls === */

        function startTimer(isRestore) {{

          if (isRunning) return;

          // Block the timer entirely if no course is picked. Mandatory
          // since "general study" was removed.
          if (!isRestore) {{
            var __courseSel = document.getElementById('focus-course');
            var __warn = document.getElementById('focus-course-warn');
            if (__courseSel && !(__courseSel.value || '').trim()) {{
              if (__warn) __warn.style.display = 'block';
              try {{ __courseSel.focus(); }} catch (_) {{}}
              return;
            }}
            if (__warn) __warn.style.display = 'none';
          }}

          // Prime the alarm INSIDE the user-gesture handler so audio can play
          // later even when the tab is hidden.
          primeAlarm();

          // Start a silent looping audio track so Chrome treats the tab as
          // "playing audio" and does NOT throttle/freeze it in the background.
          // This is what makes the timer fire on time and the alarm play even
          // when the user is on another tab for 25+ minutes.
          startKeepalive();

          isRunning = true;

          sessionStarted = true;

          document.getElementById('start-btn').style.display = 'none';

          document.getElementById('pause-btn').style.display = '';

          document.getElementById('skip-btn').style.display = currentMode === 'pomodoro' ? '' : 'none';



          // Only reset the phase boundary when starting a FRESH work phase.
          // Resuming after a pause keeps __phaseOpen=true, so we leave
          // phaseStartFocusSeconds alone and credit the full phase, not just
          // the post-resume slice. (isRestore = restored from localStorage.)
          if (!isBreak && !isRestore && !__phaseOpen) {{
            phaseStartFocusSeconds = totalFocusSeconds;
            __phaseOpen = true;
          }}

          saveFocusTimerState();



          if (currentMode === 'pages') {{

            document.getElementById('timer-label').textContent = '📖 Lectura — pulsa el bot&oacute;n grande por cada p&aacute;gina';

            var swStart = Date.now();

            var swInitial = totalFocusSeconds;

            var _pgcSel = document.getElementById('focus-course');
            var _pgeSel = document.getElementById('focus-exam');
            var _pgCourseName = '';
            if (_pgcSel && _pgcSel.selectedOptions && _pgcSel.selectedOptions[0]) {{
              _pgCourseName = _pgcSel.selectedOptions[0].getAttribute('data-name') || _pgcSel.selectedOptions[0].textContent || '';
            }}
            localStorage.setItem('focus_float', JSON.stringify({{

              active:true, mode:'stopwatch', startAt: isRestore ? (Date.now() - totalFocusSeconds * 1000) : swStart, label:'📖 Lectura',

              originalMode:'pages', course: _pgCourseName,
              courseId: _pgcSel ? (parseInt(_pgcSel.value, 10) || null) : null,
              examId: _pgeSel ? (parseInt(_pgeSel.value, 10) || null) : null

            }}));

            showFloatWidget();

            timerInterval = setInterval(function() {{

              var elapsed = Math.floor((Date.now() - swStart) / 1000);

              totalFocusSeconds = swInitial + elapsed;

              updateDisplay();

              updateFloatFromLocal();

            }}, 1000);

          }} else {{

            document.getElementById('timer-label').textContent = isBreak ? focusText.breakTime : focusText.focusTime;

            var phaseStart = Date.now();

            var initialTimeLeft = timeLeft;

            var focusAtStart = totalFocusSeconds;

            var endAt = phaseStart + initialTimeLeft * 1000;

            phaseEndAt = endAt;

            phaseEnded = false;

            var label = isBreak ? '☕ Descanso' : '🔥 Enfoque';

            var _cSel = document.getElementById('focus-course');
            var _eSel = document.getElementById('focus-exam');
            var focusCourseId = _cSel ? (parseInt(_cSel.value, 10) || null) : null;
            var focusExamId = _eSel ? (parseInt(_eSel.value, 10) || null) : null;
            var courseName = '';
            if (_cSel && _cSel.selectedOptions && _cSel.selectedOptions[0]) {{
              courseName = _cSel.selectedOptions[0].getAttribute('data-name') || _cSel.selectedOptions[0].textContent || '';
            }}

            // Minutes of WORK this phase will credit when it ends (0 for breaks).

            var phaseWorkMinutes = isBreak ? 0 : Math.round(initialTimeLeft / 60);

            // Unique id so we never double-credit a phase across page/global controllers.

            var phaseId = 'p_' + Date.now() + '_' + Math.floor(Math.random()*1e9);

            var nextPhase = null;

            if (currentMode === 'pomodoro') {{

              if (!isBreak) {{

                var nextPomoCount = pomoCount + 1;

                var nextBreakMins = (nextPomoCount % 4 === 0)

                  ? parseInt(document.getElementById('pomo-long').value)

                  : parseInt(document.getElementById('pomo-break').value);

                var followingWorkMins = parseInt(document.getElementById('pomo-work').value);

                nextPhase = {{

                  active:true, mode:'countdown',

                  endAt: endAt + nextBreakMins*60*1000,

                  label: (nextPomoCount % 4 === 0) ? focusText.longBreak : focusText.break,

                  originalMode:'pomodoro', isBreak:true, course:courseName, workMinutes:0,

                  phaseId: 'p_' + (Date.now()+1) + '_' + Math.floor(Math.random()*1e9),

                  nextPhase: {{

                    active:true, mode:'countdown',

                    endAt: endAt + nextBreakMins*60*1000 + followingWorkMins*60*1000,

                    label: '🔥 Enfoque',

                    originalMode:'pomodoro', isBreak:false, course:courseName, workMinutes: followingWorkMins,

                    phaseId: 'p_' + (Date.now()+2) + '_' + Math.floor(Math.random()*1e9),

                    nextPhase: null

                  }}

                }};

              }} else {{

                var workMins = parseInt(document.getElementById('pomo-work').value);

                nextPhase = {{

                  active:true, mode:'countdown',

                  endAt: endAt + workMins*60*1000,

                  label: '🔥 Focus',

                  originalMode:'pomodoro', isBreak:false, course:courseName, workMinutes: workMins,

                  phaseId: 'p_' + (Date.now()+1) + '_' + Math.floor(Math.random()*1e9),

                  nextPhase: null

                }};

              }}

            }}

            localStorage.setItem('focus_float', JSON.stringify({{

              active:true, mode:'countdown', endAt:endAt, label:label, nextPhase:nextPhase,

              originalMode: currentMode, isBreak: isBreak, course: courseName,
              courseId: focusCourseId, examId: focusExamId,

              workMinutes: phaseWorkMinutes, phaseId: phaseId

            }}));

            showFloatWidget();

            // ─── Hard-end timer (fires once at exact phase end) ───
            // setTimeout(0) is more reliably scheduled than setInterval ticks
            // when the tab is throttled, but is ALSO throttled when hidden.
            // Combined with the visibilitychange handler below this gives us
            // belt-and-suspenders coverage.
            if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); }}
            window.__focusEndTimeout = setTimeout(function() {{
              if (phaseEnded) return;
              phaseEnded = true;
              if (timerInterval) {{ clearInterval(timerInterval); timerInterval = null; }}
              isRunning = false;
              timeLeft = 0;
              if (!isBreak) totalFocusSeconds = focusAtStart + initialTimeLeft;
              updateDisplay();
              onTimerEnd();
            }}, Math.max(0, endAt - Date.now()));

            timerInterval = setInterval(function() {{

              var elapsed = Math.floor((Date.now() - phaseStart) / 1000);

              timeLeft = Math.max(0, initialTimeLeft - elapsed);

              if (!isBreak) totalFocusSeconds = focusAtStart + elapsed;

              updateDisplay();

              if (timeLeft <= 0 && !phaseEnded) {{

                phaseEnded = true;

                clearInterval(timerInterval);

                isRunning = false;

                if (!isBreak) totalFocusSeconds = focusAtStart + initialTimeLeft;

                onTimerEnd();

              }}

            }}, 1000);

          }}

        }}

        // === Background-tab safety net ===

        // Browsers throttle setInterval on hidden tabs (often to >=1 min).

        // When the tab becomes visible again, force a real-time check so the timer

        // completes (sound, XP, save) even if it expired while in the background.

        document.addEventListener('visibilitychange', function() {{

          if (document.hidden) return;

          if (!isRunning || phaseEnded || phaseEndAt == null) return;

          var nowMs = Date.now();

          if (nowMs >= phaseEndAt) {{

            phaseEnded = true;

            if (timerInterval) {{ clearInterval(timerInterval); timerInterval = null; }}

            isRunning = false;

            timeLeft = 0;

            updateDisplay();

            onTimerEnd();

          }} else {{

            // Tab is back early — refresh the displayed countdown immediately

            timeLeft = Math.max(0, Math.floor((phaseEndAt - nowMs) / 1000));

            updateDisplay();

          }}

        }});



        function showFloatWidget() {{

          var el = document.getElementById('focus-float');

          if (el) el.style.display = 'block';

        }}

        function updateFloatFromLocal() {{

          // Update the float on this page too

          var el = document.getElementById('focus-float');

          if (!el) return;

          var ff = JSON.parse(localStorage.getItem('focus_float')||'null');

          if (!ff || !ff.active) {{ el.style.display='none'; return; }}

          el.style.display='block';

          var ffTime = document.getElementById('ff-time');

          var ffLabel = document.getElementById('ff-label');

          if (ff.mode==='countdown') {{

            var left = ff.endAt - Date.now();

            if (left<0) left=0;

            var m=Math.floor(left/60000), s=Math.floor((left%60000)/1000);

            ffTime.textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');

          }} else {{

            var elapsed = Math.floor((Date.now()-ff.startAt)/1000);

            var m=Math.floor(elapsed/60), s=elapsed%60;

            ffTime.textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');

          }}

          ffLabel.textContent = ff.label||'Focus';

        }}



        function pauseTimer() {{

          clearInterval(timerInterval);

          if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); window.__focusEndTimeout = null; }}

          stopKeepalive();

          isRunning = false;

          document.getElementById('start-btn').style.display = '';

          document.getElementById('pause-btn').style.display = 'none';

          document.getElementById('timer-label').textContent = 'Pausado';

          localStorage.removeItem('focus_float');

          clearFocusTimerState();

          var el = document.getElementById('focus-float');

          if (el) el.style.display = 'none';

        }}



        function resetTimer() {{

          clearInterval(timerInterval);

          if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); window.__focusEndTimeout = null; }}

          stopKeepalive();

          // Reset DISCARDS unsaved progress — do NOT call saveFocusSession.
          // Users who want to save their work should pause/finish the timer.

          isRunning = false;

          isBreak = false;

          totalFocusSeconds = 0;

          phaseStartFocusSeconds = 0;

          __phaseOpen = false;

          sessionStarted = false;

          pomoCount = 0;

          pageDone = 0;

          // Reset DISCARDS unsaved pending phases. Use Reclamar to save them.

          setPendingPhases([]);

          if (typeof exitMandatoryClaimMode === 'function') exitMandatoryClaimMode();

          if (typeof refreshClaimCounter === 'function') refreshClaimCounter();

          document.getElementById('start-btn').style.display = '';

          document.getElementById('pause-btn').style.display = 'none';

          document.getElementById('skip-btn').style.display = 'none';

          localStorage.removeItem('focus_float');

          try {{ localStorage.removeItem('focus_pending_credit'); }} catch(e) {{}}

          if (typeof hidePendingCreditUI === 'function') hidePendingCreditUI();

          clearFocusTimerState();

          var el = document.getElementById('focus-float');

          if (el) el.style.display = 'none';

          if (currentMode === 'pomodoro') {{

            timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;

            document.getElementById('pomo-count').textContent = focusText.sessionOne;

          }} else if (currentMode === 'custom') {{

            timeLeft = parseInt(document.getElementById('custom-mins').value) * 60;

          }} else {{

            timeLeft = 0;

            document.getElementById('page-counter-display').textContent = '0';

          }}

          totalTime = timeLeft;

          updateDisplay();

          document.getElementById('timer-label').textContent = 'Listo para enfocarte';
          updateTodaySessionCard();

        }}



        function skipPhase() {{

          clearInterval(timerInterval);

          isRunning = false;

          onTimerEnd();

        }}



        function onTimerEnd() {{

          stopKeepalive();

          playAlarm();

          // Desktop notification works even when tab is hidden / muted
          if (currentMode === 'pomodoro' && !isBreak) {{
            showNotification(focusText.focusDoneTitle, 'Time for a break!');
          }} else if (currentMode === 'pomodoro' && isBreak) {{
            showNotification('Break over', 'Back to focus!');
          }} else {{
            showNotification(focusText.focusDoneTitle, focusText.xpGranted);
          }}

          if (currentMode === 'pomodoro') {{

            if (!isBreak) {{

              // Work phase ended → push to pending. Nothing is saved server-
              // side until the user clicks "Reclamar". After every 4th work
              // phase a mandatory 30-min claim window opens; if the user
              // doesn't claim in time, all pending phases are forfeited.

              var phaseMinutes = Math.round((totalFocusSeconds - phaseStartFocusSeconds) / 60);

              if (phaseMinutes < 0) phaseMinutes = 0;

              if (phaseMinutes > 480) phaseMinutes = 480;

              __phaseOpen = false;

              var courseSelW = document.getElementById('focus-course');

              var examSelW = document.getElementById('focus-exam');

              var pendingCourseId = courseSelW ? (parseInt(courseSelW.value, 10) || null) : null;

              var pendingExamId = examSelW ? (parseInt(examSelW.value, 10) || null) : null;

              var pendingCourseName = '';

              if (courseSelW && courseSelW.selectedOptions && courseSelW.selectedOptions[0]) {{

                pendingCourseName = courseSelW.selectedOptions[0].getAttribute('data-name') || courseSelW.selectedOptions[0].textContent || '';

              }}

              if (phaseMinutes > 0) {{
                var currentPhaseId = null;
                try {{
                  var currentFloat = JSON.parse(localStorage.getItem('focus_float') || 'null');
                  currentPhaseId = currentFloat && currentFloat.phaseId ? currentFloat.phaseId : null;
                }} catch(e) {{}}

                addPendingPhase({{

                  minutes: phaseMinutes,

                  courseId: pendingCourseId,

                  examId: pendingExamId,

                  courseName: pendingCourseName,

                  mode: 'pomodoro',

                  ts: Date.now(),

                  phaseId: currentPhaseId

                }});

              }}

              pomoCount++;

              refreshClaimCounter();

              var pcLabelEnd = document.getElementById('pomo-count');

              if (pcLabelEnd) pcLabelEnd.textContent = focusText.completedPrefix + pomoCount + focusText.ofFour;

              if (pomoCount > 0 && pomoCount % 4 === 0) {{

                // Long-break boundary: mandatory claim within 30 min.

                enterMandatoryClaimMode();

                return;

              }}

              // Otherwise auto-advance into a short break.

              isBreak = true;

              timeLeft = parseInt(document.getElementById('pomo-break').value) * 60;

              totalTime = timeLeft;

              updateDisplay();

              saveFocusTimerState();

              setTimeout(function() {{ startTimer(); }}, 2000);

              return;

            }} else {{

              timeLeft = parseInt(document.getElementById('pomo-work').value) * 60;

              document.getElementById('timer-label').textContent = focusText.nextSession;

              isBreak = false;

            }}

            totalTime = timeLeft;

            updateDisplay();

            saveFocusTimerState();

            // Auto-start next phase after 2 seconds (only after a break ends)

            setTimeout(function() {{

              startTimer();

            }}, 2000);

          }} else {{

            saveFocusSession();

            document.getElementById('timer-label').textContent = focusText.sessionCompleted;

            document.getElementById('start-btn').style.display = '';

            document.getElementById('pause-btn').style.display = 'none';

            localStorage.removeItem('focus_float');

            clearFocusTimerState();

            var el = document.getElementById('focus-float');

            if (el) el.style.display = 'none';

          }}

        }}



        // ── Pending-phase claim system ─────────────────────────────────
        // Completed work phases accumulate in localStorage as
        // `focus_pending_phases`. Nothing is saved server-side until the
        // user clicks "Reclamar y reiniciar". Anti-cheat: after every 4th
        // work phase a mandatory 30-min claim window opens; if it expires
        // without a click, every pending phase is forfeited.

        function getPendingPhases() {{

          try {{ return JSON.parse(localStorage.getItem('focus_pending_phases') || '[]') || []; }}

          catch(e) {{ return []; }}

        }}

        function setPendingPhases(arr) {{

          try {{ localStorage.setItem('focus_pending_phases', JSON.stringify(arr || [])); }} catch(e) {{}}

        }}

        function addPendingPhase(phase) {{

          var arr = getPendingPhases();

          arr.push(phase);

          setPendingPhases(arr);
          updateTodaySessionCard();

        }}

        function pendingTotals() {{

          var arr = getPendingPhases();

          var minutes = 0;

          for (var i = 0; i < arr.length; i++) minutes += (parseInt(arr[i].minutes) || 0);

          var xp = Math.floor(minutes * 5 / 10);  // mirrors server-side formula

          return {{ minutes: minutes, sessions: arr.length, xp: xp }};

        }}

        function refreshClaimCounter() {{

          var t = pendingTotals();

          var box = document.getElementById('claim-counter');

          if (!box) return;

          var mEl = document.getElementById('claim-minutes');

          var sEl = document.getElementById('claim-sessions');

          var xEl = document.getElementById('claim-xp');

          if (mEl) mEl.textContent = t.minutes;

          if (sEl) sEl.textContent = t.sessions;

          if (xEl) xEl.textContent = t.xp;

          box.style.display = (t.sessions > 0 || __mandatoryEndAt) ? '' : 'none';
          updateTodaySessionCard();

        }}

        function setClaimMandatoryStyling(isMandatory) {{

          var box = document.getElementById('claim-counter');

          var headline = document.getElementById('claim-headline');

          var help = document.getElementById('claim-help');

          var btn = document.getElementById('claim-btn');

          if (!box) return;

          if (isMandatory) {{

            box.style.borderColor = '#f59e0b';

            box.style.background = 'rgba(245,158,11,0.10)';

            if (headline) {{ headline.textContent = focusText.longBreakUnlocked; headline.style.color = '#92400e'; }}

            if (help) help.textContent = focusText.claimWindowHelp;

            if (btn) btn.textContent = focusText.claimNow;

          }} else {{

            box.style.borderColor = 'var(--primary)';

            box.style.background = 'rgba(139,92,246,0.08)';

            if (headline) {{ headline.textContent = focusText.pendingRewards; headline.style.color = ''; }}

            if (help) help.textContent = focusText.claimRestartHelp;

            if (btn) btn.textContent = focusText.claimRestart;

          }}

        }}

        async function claimNow() {{

          var phases = getPendingPhases();

          if (!phases.length) {{

            // Mandatory window with no phases (edge case) — just exit it.

            exitMandatoryClaimMode();

            resetTimer();

            return;

          }}

          var btn = document.getElementById('claim-btn');

          if (btn) {{ btn.disabled = true; btn.textContent = focusText.claiming; }}

          var __ld_save = new Date();

          var __ldStr_save = __ld_save.getFullYear()+'-'+String(__ld_save.getMonth()+1).padStart(2,'0')+'-'+String(__ld_save.getDate()).padStart(2,'0');

          // One save call per pending phase so the server counts each as a
          // distinct session (sessions_completed quest, focus stats, etc.).
          var failed = [];
          var savedCount = 0;
          var xpAwarded = 0;
          var minutesSaved = 0;
          var savedPhaseKeys = [];

          for (var i = 0; i < phases.length; i++) {{

            var p = phases[i];

            var payload = {{

              mode: p.mode || 'pomodoro',

              minutes: parseInt(p.minutes) || 0,

              pages: 0,

              course_name: p.courseName || '',

              course_id: p.courseId || null,

              exam_id: p.examId || null,

              phase_id: p.phaseId || null

            }};

            if (payload.minutes <= 0) continue;

            try {{

              var saveResp = await fetch('/api/student/focus/save?local_date=' + encodeURIComponent(__ldStr_save), {{

                method: 'POST',

                headers: {{ 'Content-Type': 'application/json' }},

                keepalive: true,

                body: JSON.stringify(payload)

              }});
              var result = null;
              try {{ result = await saveResp.json(); }} catch(_) {{}}
              if (!saveResp.ok || !result || result.ok === false) {{
                failed.push(p);
                continue;
              }}
              if (result.saved !== false) {{
                savedCount += 1;
                xpAwarded += parseInt(result.xp_awarded || 0, 10) || 0;
                minutesSaved += parseInt(result.minutes_saved || payload.minutes || 0, 10) || 0;
                savedPhaseKeys.push(String(p.phaseId || (payload.mode + ':' + payload.minutes + ':' + i)));
              }}

            }} catch(e) {{
              failed.push(p);
            }}

          }}

          if (minutesSaved > 0 && savedPhaseKeys.length) {{
            var expectedClaimXp = Math.floor(minutesSaved * 5 / 10);
            if (expectedClaimXp > xpAwarded) {{
              try {{
                var adjustId = savedPhaseKeys.join(':').replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, 120);
                var adjustResp = await fetch('/api/student/focus/claim-adjust?local_date=' + encodeURIComponent(__ldStr_save), {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  keepalive: true,
                  body: JSON.stringify({{
                    claim_id: adjustId,
                    total_minutes: minutesSaved,
                    already_awarded: xpAwarded
                  }})
                }});
                if (adjustResp.ok) {{
                  var adjust = await adjustResp.json();
                  if (adjust && adjust.ok !== false) {{
                    xpAwarded += parseInt(adjust.xp_awarded || 0, 10) || 0;
                  }}
                }}
              }} catch(e) {{}}
            }}
          }}

          // Refresh today's stat tiles in one call.

          try {{

            var r = await fetch('/api/student/focus/stats?local_date=' + encodeURIComponent(__ldStr_save));

            if (r.ok) {{

              var s = await r.json();

              if (s) {{

                var hh = document.getElementById('stat-hours');

                var ss2 = document.getElementById('stat-sessions');

                var st = document.getElementById('stat-streak');

                if (hh) hh.textContent = s.total_hours;

                if (ss2) ss2.textContent = s.sessions;

                if (st) st.textContent = s.streak_days;

              }}

            }}

          }} catch(e) {{}}

          setPendingPhases(failed);
          refreshClaimCounter();

          if (failed.length) {{
            if (btn) {{ btn.disabled = false; btn.textContent = focusText.retryClaim; }}
            var lblFail = document.getElementById('timer-label');
            if (lblFail) lblFail.textContent = focusText.unsavedPrefix + failed.length + focusText.unsavedSuffix;
            return;
          }}

          exitMandatoryClaimMode();

          if (btn) {{ btn.disabled = false; }}

          resetTimer();

          var lblDone = document.getElementById('timer-label');

          if (lblDone) lblDone.textContent = focusText.claimedPrefix + xpAwarded + ' XP · ' + minutesSaved + focusText.minutesSavedSuffix;

        }}

        function enterMandatoryClaimMode() {{

          // Stop any running timer; the big display now shows a 30-min
          // countdown, and the only way out is to claim or forfeit.

          if (timerInterval) {{ clearInterval(timerInterval); timerInterval = null; }}

          if (window.__focusEndTimeout) {{ clearTimeout(window.__focusEndTimeout); window.__focusEndTimeout = null; }}

          stopKeepalive();

          isRunning = false;

          isBreak = false;

          __phaseOpen = false;

          __mandatoryEndAt = Date.now() + 30 * 60 * 1000;

          try {{ localStorage.setItem('focus_mandatory_until', String(__mandatoryEndAt)); }} catch(e) {{}}

          var startBtn = document.getElementById('start-btn');

          var pauseBtn = document.getElementById('pause-btn');

          var skipBtn  = document.getElementById('skip-btn');

          if (startBtn) startBtn.style.display = 'none';

          if (pauseBtn) pauseBtn.style.display = 'none';

          if (skipBtn)  skipBtn.style.display = 'none';

          var lbl = document.getElementById('timer-label');

          if (lbl) lbl.textContent = focusText.longBreakClaimRewards;

          var pc = document.getElementById('pomo-count');

          if (pc) pc.textContent = focusText.claimThirty;

          setClaimMandatoryStyling(true);

          refreshClaimCounter();

          startMandatoryCountdown();

          // The current focus_float (a chained "next break" phase) is no
          // longer relevant — we're forcing the user to claim before the
          // chain can continue.

          try {{ localStorage.removeItem('focus_float'); }} catch(e) {{}}

          var ffEl = document.getElementById('focus-float');

          if (ffEl) ffEl.style.display = 'none';

        }}

        function exitMandatoryClaimMode() {{

          stopMandatoryCountdown();

          __mandatoryEndAt = null;

          try {{ localStorage.removeItem('focus_mandatory_until'); }} catch(e) {{}}

          setClaimMandatoryStyling(false);

          var startBtn = document.getElementById('start-btn');

          var pauseBtn = document.getElementById('pause-btn');

          var skipBtn  = document.getElementById('skip-btn');

          if (startBtn) startBtn.style.display = '';

          if (pauseBtn) pauseBtn.style.display = 'none';

          if (skipBtn)  skipBtn.style.display = 'none';

        }}

        function startMandatoryCountdown() {{

          stopMandatoryCountdown();

          function tick() {{

            var msLeft = (__mandatoryEndAt || 0) - Date.now();

            if (msLeft <= 0) {{

              forfeitPending();

              return;

            }}

            var sec = Math.floor(msLeft / 1000);

            var m = Math.floor(sec / 60), s = sec % 60;

            var disp = document.getElementById('timer-display');

            if (disp) disp.textContent = String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');

          }}

          tick();

          __mandatoryInterval = setInterval(tick, 1000);

        }}

        function stopMandatoryCountdown() {{

          if (__mandatoryInterval) {{ clearInterval(__mandatoryInterval); __mandatoryInterval = null; }}

        }}

        function forfeitPending() {{

          // 30-min window expired without a claim — drop everything.

          setPendingPhases([]);

          exitMandatoryClaimMode();

          resetTimer();

          var lbl = document.getElementById('timer-label');

          if (lbl) lbl.textContent = focusText.sessionLost;

          refreshClaimCounter();

        }}

        // On page load: restore the pending counter, and re-enter the
        // mandatory window if it was open and hasn't expired (or forfeit if
        // it has).

        (function checkPendingOnLoad() {{

          try {{

            var until = parseInt(localStorage.getItem('focus_mandatory_until') || '0', 10);

            var hasPending = getPendingPhases().length > 0;

            if (until && Date.now() < until) {{

              __mandatoryEndAt = until;

              setTimeout(function() {{

                // Replay enterMandatoryClaimMode UI without resetting the deadline.

                isRunning = false;

                var startBtn = document.getElementById('start-btn');

                var pauseBtn = document.getElementById('pause-btn');

                var skipBtn  = document.getElementById('skip-btn');

                if (startBtn) startBtn.style.display = 'none';

                if (pauseBtn) pauseBtn.style.display = 'none';

                if (skipBtn)  skipBtn.style.display = 'none';

                var lbl = document.getElementById('timer-label');

                if (lbl) lbl.textContent = focusText.longBreakClaimRewards;

                var pc = document.getElementById('pomo-count');

                if (pc) pc.textContent = focusText.claimThirty;

                setClaimMandatoryStyling(true);

                refreshClaimCounter();

                startMandatoryCountdown();

              }}, 150);

              return;

            }}

            if (until && Date.now() >= until) {{

              // Window expired while user was away — forfeit silently.

              setPendingPhases([]);

              try {{ localStorage.removeItem('focus_mandatory_until'); }} catch(e) {{}}

              setTimeout(function() {{ refreshClaimCounter(); }}, 150);

              return;

            }}

            if (hasPending) {{

              setTimeout(function() {{

                pomoCount = getPendingPhases().length;

                var pc2 = document.getElementById('pomo-count');

                if (pc2) pc2.textContent = focusText.completedPrefix + pomoCount + focusText.ofFour;

                refreshClaimCounter();

              }}, 150);

            }}

          }} catch(e) {{}}

        }})();

        updateTodaySessionCard();



        async function saveFocusSession(overrideMinutes) {{

          var minutes = overrideMinutes !== undefined ? overrideMinutes : Math.round(totalFocusSeconds / 60);

          // Defensive cap. If a stopwatch was left running for hours / days
          // (e.g. browser sleep, abandoned tab) the raw value can be absurd.
          // Server also caps at 480, but we don't want to even send garbage.
          if (!minutes || minutes < 0 || isNaN(minutes)) minutes = 0;
          if (minutes > 480) minutes = 480;

          var pages = currentMode === 'pages' ? pageDone : 0;

          var courseSel = document.getElementById('focus-course');
          var examSel = document.getElementById('focus-exam');
          var courseId = courseSel ? (parseInt(courseSel.value, 10) || null) : null;
          var examId = examSel ? (parseInt(examSel.value, 10) || null) : null;
          var courseName = '';
          if (courseSel && courseSel.selectedOptions && courseSel.selectedOptions[0]) {{
            courseName = courseSel.selectedOptions[0].getAttribute('data-name') || courseSel.selectedOptions[0].textContent || '';
          }}
          var course = courseName; // legacy name used in payload below

          // Dedupe by phaseId so the global widget controller doesn't also credit the same phase.

          try {{

            var ff = JSON.parse(localStorage.getItem('focus_float')||'null');

            if (ff && ff.phaseId) {{

              var saved = JSON.parse(localStorage.getItem('focus_saved_phases')||'[]');

              if (saved.indexOf(ff.phaseId) !== -1) {{

                // Already credited by global controller — just refresh stats.

                try {{

                  var __ld = new Date(); var __ldStr = __ld.getFullYear()+'-'+String(__ld.getMonth()+1).padStart(2,'0')+'-'+String(__ld.getDate()).padStart(2,'0');
                  var r2 = await fetch('/api/student/focus/stats?local_date=' + encodeURIComponent(__ldStr));

                  if (r2.ok) {{

                    var s = await r2.json();

                    // Endpoint returns the stats object directly (today-scoped).
                    if (s) {{

                      document.getElementById('stat-hours').textContent = s.total_hours;

                      document.getElementById('stat-sessions').textContent = s.sessions;

                      document.getElementById('stat-pages').textContent = s.total_pages;

                      document.getElementById('stat-streak').textContent = s.streak_days;

                    }}

                  }}

                }} catch(e) {{}}

                return;

              }}

              saved.push(ff.phaseId);

              if (saved.length > 200) saved = saved.slice(-200);

              localStorage.setItem('focus_saved_phases', JSON.stringify(saved));

            }}

          }} catch(e) {{}}

          var payload = {{ mode: currentMode, minutes: minutes, pages: pages,
                          course_name: course, course_id: courseId, exam_id: examId }};

          // Pass the BROWSER's local date so the response stats reflect the
          // student's calendar day (the server might be on UTC and disagree
          // about which day a 23:00 Chile session belongs to).
          var __ld_save = new Date();
          var __ldStr_save = __ld_save.getFullYear()+'-'+String(__ld_save.getMonth()+1).padStart(2,'0')+'-'+String(__ld_save.getDate()).padStart(2,'0');

          // Use a single keepalive fetch — it survives navigation/tab close and
          // returns the updated stats. Using sendBeacon AND fetch at the same
          // time caused duplicate rows in student_study_progress.
          try {{

            var resp = await fetch('/api/student/focus/save?local_date=' + encodeURIComponent(__ldStr_save), {{

              method: 'POST',

              headers: {{'Content-Type':'application/json'}},

              keepalive: true,

              body: JSON.stringify(payload)

            }});

            if (resp.ok) {{

              var result = await resp.json();

              if (result.stats) {{

                if (window.popNumber) {{

                  window.popNumber(document.getElementById('stat-hours'), result.stats.total_hours);

                  window.popNumber(document.getElementById('stat-sessions'), result.stats.sessions);

                  window.popNumber(document.getElementById('stat-streak'), result.stats.streak_days);

                }} else {{

                  document.getElementById('stat-hours').textContent = result.stats.total_hours;

                  document.getElementById('stat-sessions').textContent = result.stats.sessions;

                  document.getElementById('stat-streak').textContent = result.stats.streak_days;

                }}

              }}

              if (result.promotion && window.showPromotionToast) {{
                /* promotion toasts disabled */
              }}

            }}

          }} catch(e) {{}}

        }}



        // Render today's date from the BROWSER (timezone-correct, not server UTC)
        (function() {{
          var el = document.getElementById('focus-today-label');
          if (!el) return;
          try {{
            var d = new Date();
            el.textContent = d.toLocaleDateString(undefined, {{ weekday: 'long', month: 'long', day: 'numeric' }});
          }} catch(e) {{}}
        }})();



        function loadSpotify() {{

          var url = document.getElementById('spotify-url').value.trim();

          var match = url.match(/open\\.spotify\\.com\\/(playlist|album|track|episode|show)\\/([a-zA-Z0-9]+)/);

          if (!match) {{ alert('Pega un enlace válido de Spotify'); return; }}

          setPlaylist(match[2], match[1]);

        }}



        function setPlaylist(id, type) {{

          type = type || 'playlist';

          var fullUrl = 'https://open.spotify.com/' + type + '/' + id;

          var embed = 'https://open.spotify.com/embed/' + type + '/' + id + '?utm_source=generator&theme=0';

          var inp = document.getElementById('spotify-url');

          if (inp) inp.value = fullUrl;

          try {{ localStorage.setItem('focus_input_spotify-url', fullUrl); }} catch(e) {{}}

          var ifr = document.getElementById('spotify-iframe');

          if (ifr) ifr.src = embed;

        }}



        // Keyboard shortcuts

        document.addEventListener('keydown', function(e) {{

          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

          if (e.code === 'Space') {{ e.preventDefault(); if (isRunning) pauseTimer(); else startTimer(); }}

          if (e.code === 'KeyR' && !e.ctrlKey) {{ e.preventDefault(); resetTimer(); }}

          if (e.code === 'KeyS' && !e.ctrlKey) {{ e.preventDefault(); skipPhase(); }}

          if (e.code === 'KeyP' && currentMode === 'pages') {{ e.preventDefault(); clickPage(); }}

        }});



        // Persist input field values across navigation + live-update timer display when idle

        (function wireFocusInputs() {{

          var ids = ['pomo-work','pomo-break','pomo-long','custom-mins','page-target','focus-course','spotify-url'];

          // Restore saved values first

          ids.forEach(function(id) {{

            var el = document.getElementById(id);

            if (!el) return;

            try {{

              var saved = localStorage.getItem('focus_input_' + id);

              if (saved !== null && saved !== '') el.value = saved;

            }} catch(e) {{}}

          }});

          // Recompute timer display from current inputs (only when idle)

          function liveUpdate() {{

            if (isRunning || sessionStarted) return;

            if (isBreak) return;

            var mins = null;

            if (currentMode === 'pomodoro') {{

              mins = parseInt(document.getElementById('pomo-work').value, 10);

            }} else if (currentMode === 'custom') {{

              var c = document.getElementById('custom-mins');

              if (c) mins = parseInt(c.value, 10);

            }}

            if (mins && mins > 0) {{

              timeLeft = mins * 60;

              totalTime = timeLeft;

              updateDisplay();

            }}

          }}

          ids.forEach(function(id) {{

            var el = document.getElementById(id);

            if (!el) return;

            el.addEventListener('input', function() {{

              try {{ localStorage.setItem('focus_input_' + id, el.value); }} catch(e) {{}}

              liveUpdate();

            }});

            el.addEventListener('change', function() {{

              try {{ localStorage.setItem('focus_input_' + id, el.value); }} catch(e) {{}}

              liveUpdate();

            }});

          }});

          // Apply restored values to timer display on load

          liveUpdate();

        }})();



        // Restore timer if it was running when user navigated away or switched tabs

        (function restoreTimer() {{

          var ff = JSON.parse(localStorage.getItem('focus_float') || 'null');

          var ts = JSON.parse(localStorage.getItem('focus_timer_state') || 'null');

          if (!ff || !ff.active || !ts) return;

          // Abandonment guard: if the saved timer state is more than 12h old
          // (e.g. user closed the laptop overnight, started a stopwatch and
          // forgot, etc.) treat it as abandoned. Crediting it would log
          // phantom hours — exactly the bug real users have reported.
          var ABANDON_MS = 12 * 60 * 60 * 1000;
          var refTs = 0;
          if (ff.mode === 'stopwatch' && ff.startAt) {{
            refTs = ff.startAt;
          }} else if (ff.mode === 'countdown' && ff.endAt) {{
            // Approx start = endAt - workMinutes
            var w = (ff.workMinutes && ff.workMinutes > 0) ? ff.workMinutes : 25;
            refTs = ff.endAt - w * 60 * 1000;
          }}
          if (refTs && (Date.now() - refTs) > ABANDON_MS) {{
            try {{
              localStorage.removeItem('focus_float');
              localStorage.removeItem('focus_timer_state');
              var fel = document.getElementById('focus-float');
              if (fel) fel.style.display = 'none';
            }} catch(e) {{}}
            return;
          }}



          // Restore mode UI without calling setMode (which resets timer)

          currentMode = ts.currentMode;

          document.querySelectorAll('.mode-btn').forEach(function(b) {{ b.classList.remove('active'); b.classList.add('btn-outline'); }});

          var modeBtn = document.getElementById('mode-' + currentMode);

          if (modeBtn) {{ modeBtn.classList.add('active'); modeBtn.classList.remove('btn-outline'); }}

          document.getElementById('settings-pomodoro').style.display = currentMode === 'pomodoro' ? '' : 'none';

          document.getElementById('settings-pages').style.display = currentMode === 'pages' ? '' : 'none';

          document.getElementById('settings-custom').style.display = currentMode === 'custom' ? '' : 'none';

          document.getElementById('pomo-count').style.display = currentMode === 'pomodoro' ? '' : 'none';



          isBreak = ts.isBreak;

          pomoCount = ts.pomoCount;

          phaseStartFocusSeconds = ts.phaseStartFocusSeconds;

          totalTime = ts.totalTime;

          sessionStarted = true;

          if (ts.course) {{
            document.getElementById('focus-course').value = ts.course;
            onFocusCourseChange();
            if (ts.exam) {{
              var _esel = document.getElementById('focus-exam');
              if (_esel) _esel.value = ts.exam;
            }}
          }}

          // The global focus controller may have advanced the phase while the

          // user was on another tab — sync local state from focus_float.

          if (typeof ff.isBreak === 'boolean') isBreak = ff.isBreak;

          if (ff.courseId) {{
            document.getElementById('focus-course').value = String(ff.courseId);
            onFocusCourseChange();
            if (ff.examId) {{
              var _erestore = document.getElementById('focus-exam');
              if (_erestore) _erestore.value = String(ff.examId);
            }}
          }}



          if (ff.mode === 'countdown') {{

            var msLeft = ff.endAt - Date.now();

            if (msLeft > 0) {{

              timeLeft = Math.ceil(msLeft / 1000);

              var elapsedInPhase = ts.totalTime - timeLeft;

              if (!isBreak) {{

                totalFocusSeconds = ts.phaseStartFocusSeconds + elapsedInPhase;

              }} else {{

                totalFocusSeconds = ts.totalFocusSeconds;

              }}

              if (currentMode === 'pomodoro') {{

                document.getElementById('pomo-count').textContent = isBreak

                  ? focusText.completedPrefix + pomoCount + focusText.ofFour

                  : focusText.sessionPrefix + (pomoCount + 1) + focusText.ofFour;

              }}

              updateDisplay();

              startTimer(true);

            }} else {{

              // Timer ended while user was away

              if (!isBreak) {{

                totalFocusSeconds = ts.phaseStartFocusSeconds + ts.totalTime;

              }} else {{

                totalFocusSeconds = ts.totalFocusSeconds;

              }}

              timeLeft = 0;

              updateDisplay();

              onTimerEnd();

            }}

          }} else if (ff.mode === 'stopwatch') {{

            var elapsed = Math.floor((Date.now() - ff.startAt) / 1000);

            // Hard cap at 8h — anything longer is an abandoned session.
            if (elapsed > 8 * 3600) {{
              try {{
                localStorage.removeItem('focus_float');
                localStorage.removeItem('focus_timer_state');
                var fel2 = document.getElementById('focus-float');
                if (fel2) fel2.style.display = 'none';
              }} catch(e) {{}}
              return;
            }}

            totalFocusSeconds = elapsed;

            updateDisplay();

            startTimer(true);

          }}

        }})();



        /* === Focus Guard status badge === */

        (function() {{

          function readActive() {{

            try {{

              var ff = localStorage.getItem('focus_float');

              if (!ff) return false;

              var d = JSON.parse(ff);

              return !!(d && d.active);

            }} catch(e) {{ return false; }}

          }}

          function render() {{

            var s = document.getElementById('fg-status');

            var l = document.getElementById('fg-label');

            if (!s || !l) return;

            if (readActive()) {{

              s.classList.add('active');

              l.textContent = {json.dumps(_fg_active_label)};

            }} else {{

              s.classList.remove('active');

              l.textContent = {json.dumps(_fg_inactive_label)};

            }}

          }}

          render();

          setInterval(render, 1500);

          window.addEventListener('storage', function(e) {{ if (e.key === 'focus_float') render(); }});

        }})();

        </script>

        """, active_page="student_focus")



    # ── GPA Calculator page ─────────────────────────────────



    @app.route("/student/gpa")
    def student_gpa_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        lang = (session.get("lang") or "es")
        title = "Planilla de Notas" if lang == "es" else "Grade Sheet"
        html = _gpa_planilla_html(lang).replace("__CID__", str(cid))
        return _s_render(title, html, active_page="student_gpa")



    # ── Practice Problems (STEM/Math) ───────────────────────



    @app.route("/student/practice")

    def student_practice_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        course_options = '<option value="">Selecciona un curso...</option>'

        for c in courses:

            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'



        return _s_render("Practice Problems", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">

          <div>

            <h1 style="margin:0;">&#128736; Practice Problems</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">AI-generated problems with step-by-step solutions — perfect for math &amp; STEM</p>

          </div>

          <button onclick="document.getElementById('gen-form').style.display=document.getElementById('gen-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Problems</button>

        </div>



        </div>

        <div id="gen-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">

          <h3 style="margin:0 0 14px;">Generar problemas de práctica</h3>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">

            <div class="form-group">

              <label>Course</label>

              <select id="prac-course" class="edit-input">{course_options}</select>

            </div>

            <div class="form-group">

              <label>Topic (optional)</label>

              <input type="text" id="prac-topic" class="edit-input" placeholder="e.g. Improper integrals, derivatives...">

            </div>

            <div class="form-group">

              <label>Difficulty</label>

              <select id="prac-diff" class="edit-input">

                <option value="easy">Easy</option>

                <option value="medium" selected>Medium</option>

                <option value="hard">Hard</option>

              </select>

            </div>

            <div class="form-group">

              <label>Número de problemas</label>

              <select id="prac-count" class="edit-input">

                <option value="3">3</option>

                <option value="5" selected>5</option>

                <option value="8">8</option>

                <option value="10">10</option>

              </select>

            </div>

          </div>

          <button onclick="genProblems()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="gen-btn">&#10024; Generate</button>

        </div>



        <div id="problems-area"></div>



        <style>

        .mp-calendar {{ margin-top:14px; }}
        .mp-head,.mp-grid {{ display:grid;grid-template-columns:repeat(7,1fr);gap:8px; }}
        .mp-dow {{ text-align:center;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:#94939C;padding:8px 0; }}
        .mp-day {{ min-height:120px;background:#FBF8F0;border:1px solid #E2DCCC;border-radius:16px;padding:10px;cursor:pointer;transition:transform .15s,box-shadow .15s,border-color .15s;display:flex;flex-direction:column;gap:6px; }}
        .mp-day:hover {{ transform:translateY(-2px);box-shadow:0 10px 24px rgba(20,18,30,.08);border-color:#5B4694; }}
        .mp-empty {{ background:transparent;border:none;cursor:default; }}
        .mp-empty:hover {{ transform:none;box-shadow:none; }}
        .mp-num {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:22px;line-height:1;color:#1A1A1F; }}
        .mp-today {{ box-shadow:0 0 0 2px #FF7A3D inset; }}
        .mp-task {{ border-radius:10px;padding:5px 7px;background:#ECE6FB;color:#5B4694;font-size:11px;font-weight:800;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
        .mp-task.high {{ background:#FBDADA;color:#E04A4A; }}
        .mp-task.low {{ background:#D7EDDF;color:#2E9266; }}
        .mp-more {{ color:#94939C;font-size:11px;font-weight:700;margin-top:auto; }}
        .mp-row {{ display:grid;grid-template-columns:82px 1fr 82px 110px 34px;gap:8px;align-items:center; }}
        .mp-row input,.mp-row select {{ width:100%;padding:10px 11px;border:1px solid #D8D0BE;border-radius:12px;background:#FBF8F0;color:#1A1A1F; }}
        .mp-del {{ width:34px;height:34px;border-radius:10px;border:1px solid #E2DCCC;background:#fff;cursor:pointer;color:#E04A4A;font-weight:900; }}
        @media (max-width: 780px) {{
          .mp-head,.mp-grid {{ gap:5px; }}
          .mp-day {{ min-height:86px;padding:7px; }}
          .mp-task {{ font-size:10px;padding:4px 6px; }}
          .mp-row {{ grid-template-columns:1fr; }}
        }}

        .fc-page-head {{ display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap;margin-bottom:18px; }}
        .fc-eyebrow {{ font-size:12px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#7B61FF; }}
        .fc-page-title {{ margin:4px 0 0;font-family:Fraunces,Georgia,serif;font-size:48px;font-weight:600;letter-spacing:-.03em;color:#1A1A1F; }}
        .fc-today {{ background:linear-gradient(135deg,#1A1A1F,#2A2440);color:#F4F1EA;border-radius:22px;padding:26px;display:flex;align-items:center;gap:24px;margin-bottom:18px;box-shadow:0 18px 42px rgba(20,18,30,.18); }}
        .fct-l {{ flex:1; }}
        .fct-eye {{ font-size:11px;font-weight:900;letter-spacing:.12em;opacity:.8;text-transform:uppercase; }}
        .fct-title {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:26px;margin:6px 0 12px;letter-spacing:-.02em; }}
        .fct-pills {{ display:flex;gap:8px;flex-wrap:wrap; }}
        .fct-pill {{ background:rgba(255,255,255,.12);padding:5px 12px;border-radius:999px;font-size:12px;font-weight:800; }}
        .fct-pill.new {{ background:#7B61FF; }}.fct-pill.due {{ background:#10B981; }}.fct-pill.late {{ background:#EF4444; }}
        .btn-pop-cd {{ background:#1A1A1F;color:#FFF8E1;border:0;padding:10px 16px;border-radius:999px;font-weight:800;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center; }}
        .btn-pop-cd.accent {{ background:#FF7A3D;color:#fff; }}.btn-pop-cd.dark {{ background:#F4F1EA;color:#1A1A1F; }}
        .fc-preview-row {{ display:grid;grid-template-columns:1.2fr;gap:18px;margin-bottom:18px; }}
        .fc-card-flip {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:30px;min-height:240px;box-shadow:0 18px 42px rgba(20,18,30,.08);display:flex;flex-direction:column; }}
        .fc-tag {{ font-size:11px;font-weight:800;color:#94939C;display:inline-flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.06em; }}
        .dot {{ width:9px;height:9px;border-radius:50%;display:inline-block; }}
        .fc-q {{ font-family:Fraunces,Georgia,serif;font-weight:500;font-size:28px;line-height:1.25;letter-spacing:-.02em;flex:1;display:grid;place-items:center;text-align:center;padding:16px;color:#1A1A1F; }}
        .fc-foot {{ display:flex;justify-content:space-between;font-size:11px;color:#94939C;gap:12px; }}
        .fc-decks-card {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:18px;box-shadow:0 12px 34px rgba(20,18,30,.06); }}
        .fc-card-h {{ display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px; }}
        .fc-card-title {{ font-weight:900;color:#1A1A1F; }}
        .fc-card-link {{ background:transparent;border:0;color:#7B61FF;font-size:13px;font-weight:900;cursor:pointer; }}
        .deck-grid {{ display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:8px; }}
        @media (max-width:1100px) {{ .deck-grid {{ grid-template-columns:repeat(2,1fr); }} }}
        @media (max-width:700px) {{ .deck-grid {{ grid-template-columns:1fr; }} .fc-today {{ flex-direction:column;align-items:flex-start; }} .fc-page-title {{ font-size:38px; }} }}
        .deck {{ background:#FBF8F0;border:1px solid #E2DCCC;border-radius:14px;padding:14px;cursor:pointer;transition:all .15s ease; }}
        .deck:hover {{ transform:translateY(-2px);box-shadow:0 14px 28px rgba(20,18,30,.08); }}
        .deck-head {{ display:flex;align-items:center;gap:6px; }}
        .deck-tag {{ font-size:11px;font-weight:900;color:#94939C;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
        .deck-delete {{ margin-left:auto;background:transparent;border:0;color:#EF4444;cursor:pointer;font-size:13px; }}
        .deck-name {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:16px;margin:6px 0 4px;line-height:1.15;color:#1A1A1F; }}
        .deck-meta {{ font-size:11px;color:#94939C; }}
        .deck-bar {{ height:5px;background:#fff;border-radius:999px;overflow:hidden;margin:10px 0 6px; }}
        .deck-fill {{ height:100%;border-radius:999px; }}
        .deck-due {{ font-size:11px;font-weight:800;color:#FF7A3D; }}
        .deck-due.urgent {{ color:#EF4444; }}.deck-due.muted {{ color:#94939C;font-weight:700; }}
        .deck.add {{ border:2px dashed #E2DCCC;background:transparent;display:grid;place-items:center;text-align:center;min-height:145px; }}
        .deck-add-icon {{ font-size:32px;color:#94939C; }}
        .deck-add-l {{ font-weight:900;font-size:13px;color:#1A1A1F; }}
        .deck-add-s {{ font-size:11px;color:#94939C; }}
        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        .problem-card {{ background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:16px; }}

        .problem-card h3 {{ margin:0 0 12px;font-size:16px;color:var(--primary); }}

        .problem-text {{ font-size:15px;line-height:1.7;color:var(--text); }}

        .solution-area {{ margin-top:12px;padding:16px;background:var(--bg);border-radius:var(--radius-sm);border-left:4px solid var(--primary); }}

        .solution-area h4 {{ margin:0 0 8px;font-size:14px;color:var(--primary); }}

        .solution-text {{ font-size:14px;line-height:1.8;color:var(--text);white-space:pre-wrap; }}

        .answer-box {{ display:inline-block;margin-top:8px;padding:6px 14px;background:var(--primary);color:#fff;border-radius:var(--radius-sm);font-weight:600;font-size:14px; }}

        </style>

        <script>

        async function genProblems() {{

          var courseId = document.getElementById('prac-course').value;

          if (!courseId) {{ alert('Select a course'); return; }}

          var btn = document.getElementById('gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating problems...';

          try {{

            var r = await fetch('/api/student/practice/generate', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{

                course_id: parseInt(courseId),

                topic: document.getElementById('prac-topic').value,

                difficulty: document.getElementById('prac-diff').value,

                count: parseInt(document.getElementById('prac-count').value)

              }})

            }});

            var d = await _safeJson(r);

            if (r.ok && d.problems) {{

              renderProblems(d.problems);

            }} else {{ alert(d.error || 'Error al generar'); }}

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); }}

          btn.disabled = false; btn.innerHTML = '&#10024; Generate';

        }}



        function renderProblems(problems) {{

          var html = '';

          problems.forEach(function(p, i) {{

            html += '<div class="problem-card">'

              + '<h3>Problem ' + (i+1) + '</h3>'

              + '<div class="problem-text">' + escHtml(p.problem) + '</div>'

              + '<button onclick="toggleSol(' + i + ')" class="btn btn-outline btn-sm" style="margin-top:12px;" id="sol-btn-' + i + '">&#128161; Show Solution</button>'

              + '<div id="sol-' + i + '" style="display:none;">'

              + '<div class="solution-area">'

              + '<h4>Solución paso a paso</h4>'

              + '<div class="solution-text">' + escHtml(p.solution) + '</div>'

              + '</div>'

              + '<div style="margin-top:8px;"><span class="answer-box">Answer: ' + escHtml(p.answer) + '</span></div>'

              + '</div></div>';

          }});

          document.getElementById('problems-area').innerHTML = html;

          // Render math

          if (typeof renderMathInElement === 'function') {{

            renderMathInElement(document.getElementById('problems-area'), {{

              delimiters: [

                {{left:'$$',right:'$$',display:true}},

                {{left:'$',right:'$',display:false}},

                {{left:'\\\\(',right:'\\\\)',display:false}},

                {{left:'\\\\[',right:'\\\\]',display:true}}

              ], throwOnError: false

            }});

          }}

        }}



        function toggleSol(i) {{

          var el = document.getElementById('sol-' + i);

          var btn = document.getElementById('sol-btn-' + i);

          if (el.style.display === 'none') {{

            el.style.display = 'block';

            btn.innerHTML = '&#128064; Hide Solution';

            if (typeof renderMathInElement === 'function') {{

              renderMathInElement(el, {{

                delimiters: [

                  {{left:'$$',right:'$$',display:true}},

                  {{left:'$',right:'$',display:false}}

                ], throwOnError: false

              }});

            }}

          }} else {{

            el.style.display = 'none';

            btn.innerHTML = '&#128161; Show Solution';

          }}

        }}



        function escHtml(t) {{ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }}

        </script>

        """, active_page="student_practice")



    @app.route("/api/student/practice/generate", methods=["POST"])

    @limiter.limit("5 per minute")

    def student_generate_practice():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        course_id = data.get("course_id")

        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        source_text = ""

        files = sdb.get_course_files(_cid(), course_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

        notes = sdb.get_notes(_cid(), course_id)

        for n in notes:

            if n.get("content_html"):

                source_text += n["content_html"] + "\n\n"



        if not source_text.strip():

            return jsonify({"error": "No files uploaded for this course. Please upload your study material first."}), 400



        problems = generate_practice_problems(

            course_name=course["name"],

            topic=data.get("topic", ""),

            difficulty=data.get("difficulty", "medium"),

            count=min(int(data.get("count", 5)), 10),

            source_text=source_text,

        )

        if not problems:

            return jsonify({"error": "Failed to generate problems. Try again."}), 500

        return jsonify({"problems": problems})



    @app.route("/student/canvas-settings")

    def student_canvas_settings_page():

        if not _logged_in():

            return redirect(url_for("login"))

        tok = sdb.get_canvas_token(_cid())

        connected = bool(tok)

        url_val = tok["canvas_url"] if tok else ""



        return _s_render("Canvas Settings", f"""

        <style>
          .canvas-settings-grid {{
            display:grid;
            grid-template-columns:minmax(320px,560px) minmax(420px,760px);
            gap:28px;
            align-items:start;
          }}
          @media (max-width: 900px) {{
            .canvas-settings-grid {{ grid-template-columns:1fr; }}
          }}
        </style>

        <h1 style="margin-bottom:20px;">&#128279; Conexión a Canvas</h1>

        <div class="canvas-settings-grid">

        <div class="card" style="padding:18px;">

          <div style="margin-bottom:20px;">

            <span style="display:inline-block;padding:4px 12px;border-radius:12px;font-size:13px;font-weight:600;background:{'#D1FAE5' if connected else '#FEE2E2'};color:{'#065F46' if connected else '#991B1B'};">

              {'&#10003; Conectado' if connected else '&#10007; No conectado'}

            </span>

            {'<span style="color:var(--text-muted);font-size:13px;margin-left:10px;">' + _esc(url_val) + '</span>' if connected else ''}

          </div>



          <form onsubmit="connectCanvas(event)" autocomplete="off">

            <div class="form-group">

              <label>URL de Canvas</label>

              <input id="canvas-url" name="canvas_school_url_manual" type="url" placeholder="https://yourschool.instructure.com" value="{_esc(url_val)}" required autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" inputmode="url" data-lpignore="true" data-form-type="other">

            </div>

            <div class="form-group">

              <label>Token de acceso API</label>

              <input id="canvas-token" name="canvas_api_token_manual" type="text" placeholder="Paste your Canvas access token" {'value="********"' if connected else ''} required autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" data-lpignore="true" data-form-type="other" style="-webkit-text-security:disc;text-security:disc;">

              <p style="font-size:12px;color:var(--text-muted);margin-top:6px;">

                Go to Canvas &rarr; Account &rarr; Settings &rarr; <b>+ New Access Token</b>

              </p>

            </div>

            <div style="display:flex;gap:10px;">

              <button type="submit" class="btn btn-primary" id="connect-btn">{'Update' if connected else 'Conectar Canvas'}</button>

              {'<button type="button" onclick="disconnectCanvas()" class="btn btn-outline" style="color:var(--red);border-color:var(--red);">Desconectar</button>' if connected else ''}

            </div>

            <div id="canvas-connect-status" style="display:none;margin-top:12px;font-size:13px;font-weight:600;"></div>

          </form>

        </div>

        <div class="card">

          <div class="card-header" style="margin-bottom:14px;">
            <h2 style="font-size:18px;margin:0;">Tutorial de conexión</h2>
          </div>

          <video controls loop preload="metadata" playsinline style="width:100%;display:block;border-radius:8px;border:1px solid var(--border);background:#000;">
            <source src="/static/tutorials/canvas-connection-tutorial.mp4" type="video/mp4">
          </video>

        </div>

        </div>

        <script>

        function canvasConnectStatus(msg, ok) {{
          var el = document.getElementById('canvas-connect-status');
          if (!el) return;
          el.style.display = msg ? 'block' : 'none';
          el.style.color = ok ? 'var(--green)' : 'var(--red)';
          el.textContent = msg || '';
        }}

        function normalizeCanvasUrlInput(raw) {{
          var value = (raw || '').trim();
          if (!value) return value;
          try {{
            if (!new RegExp('^https?://', 'i').test(value)) value = 'https://' + value;
            var u = new URL(value);
            return u.origin;
          }} catch(e) {{
            while (value.endsWith('/')) value = value.slice(0, -1);
            return value;
          }}
        }}

        async function connectCanvas(e) {{

          e.preventDefault();

          var btn = document.getElementById('connect-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Connecting...';
          canvasConnectStatus('', false);

          try {{
            var urlEl = document.getElementById('canvas-url');
            var tokenEl = document.getElementById('canvas-token');
            var canvasUrl = normalizeCanvasUrlInput(urlEl.value);
            urlEl.value = canvasUrl;

            var r = await fetch('/api/student/canvas/connect', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{canvas_url:canvasUrl,token:tokenEl.value}})}});

            var d = await _safeJson(r);

            if (r.ok) {{
              canvasConnectStatus('Canvas conectado. Cursos encontrados: ' + d.courses_found + '.', true);
              if (typeof showToast === 'function') showToast('Canvas conectado. Cursos encontrados: ' + d.courses_found + '.', 'success');
              setTimeout(function(){{ mrReload(); }}, 250);
            }}

            else {{
              canvasConnectStatus(d.error || 'No se pudo conectar Canvas. Revisa la URL y el token.', false);
              btn.disabled = false; btn.innerHTML = 'Conectar Canvas';
            }}

          }} catch(e) {{
            canvasConnectStatus('No se pudo contactar el servidor de MachReach. Inténtalo de nuevo.', false);
            btn.disabled = false; btn.innerHTML = 'Conectar Canvas';
            console.error('Canvas connect failed', e);
          }}


        }}

        async function disconnectCanvas() {{

          if (!confirm('Desconectar Canvas?')) return;

          await fetch('/api/student/canvas/disconnect', {{method:'POST'}});

          mrReload();

        }}

        </script>

        """, active_page="student_canvas")



    # ── Schedule settings (per-day availability) ────────────



    @app.route("/api/student/schedule", methods=["GET"])

    def student_get_schedule():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        settings = sdb.get_schedule_settings(_cid())

        # Build a full week map (0=Mon..6=Sun), unset days = full free day

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        result = []

        settings_map = {s["day_of_week"]: s for s in settings}

        for i in range(7):

            if i in settings_map:

                s = settings_map[i]

                result.append({

                    "day": i,

                    "day_name": days[i],

                    "hours": s["available_hours"],

                    "free": bool(s["is_free_day"]),

                })

            else:

                # Unset = full free day

                result.append({

                    "day": i,

                    "day_name": days[i],

                    "hours": 0,

                    "free": True,

                })

        return jsonify({"schedule": result})



    @app.route("/api/student/schedule", methods=["PUT"])
    def student_set_schedule():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        try:

            data = request.get_json(force=True) or {}

            settings = data.get("schedule", [])

            if not isinstance(settings, list):

                return jsonify({"error": "schedule must be a list"}), 400

            cleaned = []

            for s in settings:

                day = int(s.get("day", -1))

                if day < 0 or day > 6:

                    continue

                cleaned.append({

                    "day": day,

                    "hours": max(0, min(24, float(s.get("hours", 0)))),

                    "free": bool(s.get("free", False)),

                })

            sdb.save_schedule_settings(_cid(), cleaned)

            return jsonify({"ok": True})

        except Exception as e:

            import logging, traceback

            logging.getLogger("student.routes").error("schedule save failed: %s\n%s", e, traceback.format_exc())

            return jsonify({"error": f"Could not save schedule: {str(e)[:120]}"}), 500



    # ── Per-date availability overrides ─────────────────────

    @app.route("/api/student/date-overrides", methods=["GET"])
    def student_get_date_overrides():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        start = request.args.get("start") or None
        end = request.args.get("end") or None
        try:
            rows = sdb.get_date_overrides(_cid(), start, end)
            return jsonify({"overrides": rows})
        except Exception as e:
            return jsonify({"error": str(e)[:200]}), 500

    @app.route("/api/student/date-overrides", methods=["POST"])
    def student_save_date_override():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            data = request.get_json(force=True) or {}
            d = (data.get("date") or "").strip()[:10]
            if not d or len(d) != 10:
                return jsonify({"error": "Valid 'date' (YYYY-MM-DD) required"}), 400
            from datetime import date as _date
            try:
                _date.fromisoformat(d)
            except Exception:
                return jsonify({"error": "Invalid date format"}), 400
            hours = max(0.0, min(24.0, float(data.get("hours", 0))))
            is_free = bool(data.get("free", False))
            note = (data.get("note", "") or "")[:200]
            sdb.save_date_override(_cid(), d, hours, is_free, note)
            return jsonify({"ok": True})
        except Exception as e:
            import logging, traceback
            logging.getLogger("student.routes").error("date-override save failed: %s\n%s", e, traceback.format_exc())
            return jsonify({"error": str(e)[:200]}), 500

    @app.route("/api/student/date-overrides/<date_str>", methods=["DELETE"])
    def student_delete_date_override(date_str):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        try:
            sdb.delete_date_override(_cid(), date_str[:10])
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)[:200]}), 500



    # ── Course difficulty ───────────────────────────────────



    @app.route("/api/student/courses/<int:course_id>/difficulty", methods=["PUT"])

    def student_set_difficulty(course_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Not found"}), 404

        data = request.get_json(force=True)

        difficulty = int(data.get("difficulty", 3))

        sdb.set_course_difficulty(_cid(), course_id, difficulty)

        return jsonify({"ok": True, "difficulty": max(1, min(5, difficulty))})



    # ── Assignment-level completion ─────────────────────────



    @app.route("/api/student/assignments/toggle", methods=["POST"])

    def student_toggle_assignment():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        plan_date = data.get("date")

        session_index = data.get("session_index")

        completed = data.get("completed", True)

        if plan_date is None or session_index is None:

            return jsonify({"error": "date and session_index are required"}), 400

        sdb.toggle_assignment_complete(_cid(), plan_date, int(session_index), bool(completed))

        return jsonify({"ok": True})



    @app.route("/api/student/assignments/progress", methods=["GET"])

    def student_assignment_progress():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        plan_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))

        progress = sdb.get_assignment_progress(_cid(), plan_date)

        return jsonify({"progress": progress, "date": plan_date})



    @app.route("/api/student/assignments/incomplete", methods=["GET"])

    def student_incomplete_assignments():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        today = datetime.now().strftime("%Y-%m-%d")

        incomplete = sdb.get_incomplete_assignments(_cid(), today)

        return jsonify({"incomplete": incomplete})


    @app.route("/api/student/manual-plan", methods=["GET"])
    def student_manual_plan_get():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        plan_row = sdb.get_latest_plan(_cid()) or {}
        plan = plan_row.get("plan_json") or {}
        return jsonify({"daily_plan": plan.get("daily_plan") or []})


    @app.route("/api/student/manual-plan", methods=["POST"])
    def student_manual_plan_save():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        data = request.get_json(force=True) or {}
        day = (data.get("date") or "").strip()[:10]
        sessions = data.get("sessions") or []
        if len(day) != 10 or not isinstance(sessions, list):
            return jsonify({"error": "Invalid plan payload"}), 400

        clean_sessions = []
        for item in sessions[:12]:
            topic = (item.get("topic") or "").strip()[:160]
            if not topic:
                continue
            clean_sessions.append({
                "start_time": (item.get("start_time") or "").strip()[:8],
                "course": (item.get("course") or "General").strip()[:80],
                "topic": topic,
                "hours": max(0.25, min(8.0, float(item.get("hours") or 0.5))),
                "type": (item.get("type") or "estudio").strip()[:40],
                "priority": (item.get("priority") or "medium").strip()[:16],
            })

        latest = sdb.get_latest_plan(_cid()) or {}
        plan = latest.get("plan_json") or {}
        daily = [d for d in (plan.get("daily_plan") or []) if (d.get("date") or "")[:10] != day]
        if clean_sessions:
            daily.append({"date": day, "sessions": clean_sessions})
        daily.sort(key=lambda d: d.get("date") or "")
        plan.update({"source": "manual_calendar", "daily_plan": daily})
        sdb.save_study_plan(_cid(), plan, {"source": "manual_calendar"})
        return jsonify({"ok": True, "date": day, "sessions": clean_sessions, "daily_plan": daily})



    # ── Schedule settings page ──────────────────────────────



    @app.route("/student/schedule")

    def student_schedule_page():

        if not _logged_in():

            return redirect(url_for("login"))

        settings = sdb.get_schedule_settings(_cid())

        settings_map = {s["day_of_week"]: s for s in settings}

        courses = sdb.get_courses(_cid())



        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        day_rows = ""

        for i in range(7):

            s = settings_map.get(i, {})

            hours = s.get("available_hours", 0) if s else 0

            is_free = s.get("is_free_day", True) if not s else bool(s.get("is_free_day", 0))

            # If no settings saved at all, default: unset = free

            if not settings:

                is_free = True

                hours = 0

            checked = "checked" if is_free else ""

            day_rows += f"""

            <div class="schedule-row" style="display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:8px;">

              <span style="width:100px;font-weight:600;color:var(--text);">{days[i]}</span>

              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;min-width:100px;">

                <input type="checkbox" class="free-day-check" data-day="{i}" {checked} onchange="toggleFreeDay({i},this.checked)">

                <span style="font-size:13px;color:var(--text-muted);">Día libre</span>

              </label>

              <div id="hours-group-{i}" style="display:flex;align-items:center;gap:8px;{'opacity:0.3;pointer-events:none;' if is_free else ''}">

                <label style="font-size:12px;color:var(--text-muted);">Study hours:</label>

                <input type="number" id="hours-{i}" value="{hours}" min="0" max="24" step="0.5" class="edit-input" style="width:70px;" onchange="updateSchedule()">

              </div>

            </div>"""



        # Course difficulty section

        diff_rows = ""

        for c in courses:

            diff = c.get("difficulty", 3) or 3

            stars = ""

            for star in range(1, 6):

                active = "color:var(--primary);font-weight:700;" if star <= diff else "color:var(--border);"

                stars += f'<span class="diff-star" data-course="{c["id"]}" data-val="{star}" style="cursor:pointer;font-size:20px;{active}" onclick="setDiff({c["id"]},{star})">&#9733;</span>'

            diff_rows += f"""

            <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);margin-bottom:8px;">

              <span style="font-weight:600;">{_esc(c['name'])}</span>

              <div style="display:flex;align-items:center;gap:4px;">

                {stars}

                <span id="diff-label-{c['id']}" style="font-size:12px;color:var(--text-muted);margin-left:8px;min-width:60px;">

                  {['','Very Easy','Easy','Medium','Hard','Very Hard'][diff]}

                </span>

              </div>

            </div>"""



        if not diff_rows:

            diff_rows = "<p style='color:var(--text-muted);text-align:center;padding:16px;'>Sin cursos sincronizados todavía.</p>"



        return _s_render("Study Schedule", f"""

        <div class="page-head" style="display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap;margin-bottom:18px;">
          <div>
            <div class="page-eyebrow" style="font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:#FF7A3D;">Planifica tu semana</div>
            <h1 class="page-title" style="margin:0;font-family:Fraunces,Georgia,serif;font-size:44px;font-weight:600;">Calendario</h1>
            <p style="margin:6px 0 0;color:var(--text-muted);">Crea tu plan de estudio por d&iacute;a. Lo que pongas aqu&iacute; aparece en el Plan de hoy del dashboard.</p>
          </div>
          <button onclick="mpOpen(new Date())" class="btn btn-primary">&#10010; Agregar tarea</button>
        </div>

        <div class="card mr-calendar-card" style="margin-bottom:20px;">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
            <h2 style="margin:0;">&#128197; Plan de estudio</h2>
            <div style="display:flex;align-items:center;gap:8px;">
              <button onclick="mpMove(-1)" class="btn btn-outline btn-sm">&#9664;</button>
              <strong id="mp-month-label" style="min-width:160px;text-align:center;">&mdash;</strong>
              <button onclick="mpMove(1)" class="btn btn-outline btn-sm">&#9654;</button>
            </div>
          </div>
          <div id="mp-calendar" class="mp-calendar"></div>
        </div>

        <div id="mp-modal" style="display:none;position:fixed;inset:0;background:rgba(20,18,30,.55);z-index:10000;align-items:center;justify-content:center;padding:20px;">
          <div style="background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:24px;max-width:560px;width:100%;box-shadow:0 22px 60px rgba(20,18,30,.18);">
            <h3 style="margin:0 0 4px;font-family:Fraunces,Georgia,serif;font-size:28px;">Editar d&iacute;a</h3>
            <p id="mp-date-label" style="margin:0 0 16px;color:#94939C;font-weight:700;"></p>
            <div id="mp-task-list" style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px;"></div>
            <button onclick="mpAddRow()" class="btn btn-outline btn-sm" type="button">&#10010; Agregar bloque</button>
            <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px;">
              <button onclick="mpClose()" class="btn btn-outline">Cancelar</button>
              <button onclick="mpSave()" class="btn btn-primary" id="mp-save-btn">Guardar plan</button>
            </div>
          </div>
        </div>



        <div class="focus-grid">

          <div class="card focus-timer-card">

            <div class="card-header">

              <h2>&#128337; Weekly Availability</h2>

              <p style="font-size:13px;color:var(--text-muted);margin:4px 0 0;">Set your available study hours for each day. Days left unconfigured are treated as free days.</p>

            </div>

            {day_rows}

            <button onclick="saveSchedule()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="save-sched-btn">Guardar horario</button>

          </div>



          <div class="card">

            <div class="card-header">

              <h2>&#9733; Course Difficulty</h2>

              <p style="font-size:13px;color:var(--text-muted);margin:4px 0 0;">Rate difficulty (1-5 stars). Harder courses get more study time in the AI plan.</p>

            </div>

            {diff_rows}

          </div>

        </div>



        <div class="card" style="margin-top:20px;">

          <div class="card-header" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">

            <div>

              <h2 style="margin:0;">&#128197; Per-Date Availability</h2>

              <p style="font-size:13px;color:var(--text-muted);margin:4px 0 0;">Click any date to set custom hours for that specific day. Overrides your weekly schedule. The AI plan respects these as hard limits.</p>

            </div>

            <div style="display:flex;align-items:center;gap:6px;">

              <button onclick="ovPrevMonth()" class="btn btn-outline btn-sm" style="padding:4px 10px;">&#9664;</button>

              <span id="ov-month-label" style="font-weight:700;min-width:160px;text-align:center;">&mdash;</span>

              <button onclick="ovNextMonth()" class="btn btn-outline btn-sm" style="padding:4px 10px;">&#9654;</button>

            </div>

          </div>

          <div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--text-muted);margin-top:8px;">

            <span><span style="display:inline-block;width:12px;height:12px;background:var(--bg);border:1px solid var(--border);border-radius:3px;vertical-align:middle;margin-right:4px;"></span>Default (weekly)</span>

            <span><span style="display:inline-block;width:12px;height:12px;background:rgba(99,102,241,0.25);border:1px solid var(--primary);border-radius:3px;vertical-align:middle;margin-right:4px;"></span>Horas personalizadas</span>

            <span><span style="display:inline-block;width:12px;height:12px;background:rgba(239,68,68,0.25);border:1px solid #ef4444;border-radius:3px;vertical-align:middle;margin-right:4px;"></span>Día libre</span>

            <span><span style="display:inline-block;width:12px;height:12px;border:2px solid var(--primary);border-radius:3px;vertical-align:middle;margin-right:4px;"></span>Hoy</span>

          </div>

          <div id="ov-calendar" class="ov-cal" style="margin-top:14px;"></div>

        </div>

        <!-- Override editor modal -->

        <div id="ov-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:9999;align-items:center;justify-content:center;padding:20px;">

          <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:24px;max-width:420px;width:100%;box-shadow:0 12px 40px rgba(0,0,0,0.4);">

            <h3 style="margin:0 0 4px;">&#128197; <span id="ov-modal-date">Definir disponibilidad</span></h3>

            <p id="ov-modal-weekday" style="font-size:13px;color:var(--text-muted);margin:0 0 16px;"></p>

            <div style="display:flex;flex-direction:column;gap:12px;">

              <div>

                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);">

                  <input type="radio" name="ov-mode" value="default" id="ov-mode-default">

                  <span><b>Usar predeterminado semanal</b><br><span style="font-size:12px;color:var(--text-muted);">Quitar override de esta fecha</span></span>

                </label>

              </div>

              <div>

                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);">

                  <input type="radio" name="ov-mode" value="hours" id="ov-mode-hours" checked>

                  <span style="flex:1;"><b>Horas personalizadas</b>

                    <div style="margin-top:6px;display:flex;align-items:center;gap:6px;">

                      <input type="number" id="ov-hours" min="0" max="24" step="0.5" value="2" class="edit-input" style="width:90px;">

                      <span style="font-size:13px;color:var(--text-muted);">hours of study</span>

                    </div>

                  </span>

                </label>

              </div>

              <div>

                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);">

                  <input type="radio" name="ov-mode" value="free" id="ov-mode-free">

                  <span><b>Día libre</b><br><span style="font-size:12px;color:var(--text-muted);">Sin estudio programado este día</span></span>

                </label>

              </div>

              <div>

                <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;">Note (optional)</label>

                <input type="text" id="ov-note" placeholder="e.g. Doctor appointment" maxlength="200" class="edit-input">

              </div>

            </div>

            <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px;">

              <button onclick="ovCloseModal()" class="btn btn-outline btn-sm">Cancelar</button>

              <button onclick="ovSaveModal()" class="btn btn-primary btn-sm" id="ov-save-btn">Guardar</button>

            </div>

          </div>

        </div>



        <div class="card" style="margin-top:20px;">

          <div style="background:var(--bg);border-radius:var(--radius-sm);padding:14px 18px;">

            <p style="font-size:13px;color:var(--text-muted);margin:0;">

              &#128161; <b>How it works:</b> When you generate a study plan, the AI will respect your weekly schedule, your per-date overrides (which take priority for the dates you specify), and course difficulty. Per-date overrides are HARD limits &mdash; the plan will never exceed them.

            </p>

          </div>

        </div>



        <style>

        .fc-page-head {{ display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap;margin-bottom:18px; }}
        .fc-eyebrow {{ font-size:12px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#7B61FF; }}
        .fc-page-title {{ margin:4px 0 0;font-family:Fraunces,Georgia,serif;font-size:48px;font-weight:600;letter-spacing:-.03em;color:#1A1A1F; }}
        .fc-today {{ background:linear-gradient(135deg,#1A1A1F,#2A2440);color:#F4F1EA;border-radius:22px;padding:26px;display:flex;align-items:center;gap:24px;margin-bottom:18px;box-shadow:0 18px 42px rgba(20,18,30,.18); }}
        .fct-l {{ flex:1; }}
        .fct-eye {{ font-size:11px;font-weight:900;letter-spacing:.12em;opacity:.8;text-transform:uppercase; }}
        .fct-title {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:26px;margin:6px 0 12px;letter-spacing:-.02em; }}
        .fct-pills {{ display:flex;gap:8px;flex-wrap:wrap; }}
        .fct-pill {{ background:rgba(255,255,255,.12);padding:5px 12px;border-radius:999px;font-size:12px;font-weight:800; }}
        .fct-pill.new {{ background:#7B61FF; }}.fct-pill.due {{ background:#10B981; }}.fct-pill.late {{ background:#EF4444; }}
        .btn-pop-cd {{ background:#1A1A1F;color:#FFF8E1;border:0;padding:10px 16px;border-radius:999px;font-weight:800;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center; }}
        .btn-pop-cd.accent {{ background:#FF7A3D;color:#fff; }}.btn-pop-cd.dark {{ background:#F4F1EA;color:#1A1A1F; }}
        .page-actions-cd {{ display:flex;align-items:center;gap:10px;flex-wrap:wrap; }}
        .fc-preview-row {{ display:grid;grid-template-columns:1.2fr;gap:18px;margin-bottom:18px; }}
        .fc-card-flip {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:30px;min-height:240px;box-shadow:0 18px 42px rgba(20,18,30,.08);display:flex;flex-direction:column; }}
        .fc-tag {{ font-size:11px;font-weight:800;color:#94939C;display:inline-flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.06em; }}
        .dot {{ width:9px;height:9px;border-radius:50%;display:inline-block; }}
        .fc-q {{ font-family:Fraunces,Georgia,serif;font-weight:500;font-size:28px;line-height:1.25;letter-spacing:-.02em;flex:1;display:grid;place-items:center;text-align:center;padding:16px;color:#1A1A1F; }}
        .fc-foot {{ display:flex;justify-content:space-between;font-size:11px;color:#94939C;gap:12px; }}
        .fc-decks-card {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:18px;box-shadow:0 12px 34px rgba(20,18,30,.06); }}
        .fc-card-h {{ display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px; }}
        .fc-card-title {{ font-weight:900;color:#1A1A1F; }}
        .fc-card-link {{ background:transparent;border:0;color:#7B61FF;font-size:13px;font-weight:900;cursor:pointer; }}
        .deck-grid {{ display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:8px; }}
        @media (max-width:1100px) {{ .deck-grid {{ grid-template-columns:repeat(2,1fr); }} }}
        @media (max-width:700px) {{ .deck-grid {{ grid-template-columns:1fr; }} .fc-today {{ flex-direction:column;align-items:flex-start; }} .fc-page-title {{ font-size:38px; }} }}
        .deck {{ background:#FBF8F0;border:1px solid #E2DCCC;border-radius:14px;padding:14px;cursor:pointer;transition:all .15s ease; }}
        .deck:hover {{ transform:translateY(-2px);box-shadow:0 14px 28px rgba(20,18,30,.08); }}
        .deck-head {{ display:flex;align-items:center;gap:6px; }}
        .deck-tag {{ font-size:11px;font-weight:900;color:#94939C;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
        .deck-delete {{ margin-left:auto;background:transparent;border:0;color:#EF4444;cursor:pointer;font-size:13px; }}
        .deck-name {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:16px;margin:6px 0 4px;line-height:1.15;color:#1A1A1F; }}
        .deck-meta {{ font-size:11px;color:#94939C; }}
        .deck-bar {{ height:5px;background:#fff;border-radius:999px;overflow:hidden;margin:10px 0 6px; }}
        .deck-fill {{ height:100%;border-radius:999px; }}
        .deck-due {{ font-size:11px;font-weight:800;color:#FF7A3D; }}
        .deck-due.urgent {{ color:#EF4444; }}.deck-due.muted {{ color:#94939C;font-weight:700; }}
        .deck.add {{ border:2px dashed #E2DCCC;background:transparent;display:grid;place-items:center;text-align:center;min-height:145px; }}
        .deck-add-icon {{ font-size:32px;color:#94939C; }}
        .deck-add-l {{ font-weight:900;font-size:13px;color:#1A1A1F; }}
        .deck-add-s {{ font-size:11px;color:#94939C; }}

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        .diff-star:hover {{ transform:scale(1.2); }}

        /* Calendar */
        .ov-cal {{ width:100%; }}
        .ov-cal-head {{ display:grid; grid-template-columns:repeat(7,1fr); gap:6px; margin-bottom:6px; }}
        .ov-cal-dow {{ font-size:11px; color:var(--text-muted); text-align:center; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; padding:6px 0; }}
        .ov-cal-grid {{ display:grid; grid-template-columns:repeat(7,1fr); gap:6px; }}
        .ov-cal-cell {{ min-height:78px; border:1px solid var(--border); border-radius:var(--radius-sm); padding:6px 8px; background:var(--card); cursor:pointer; transition:transform .12s ease, box-shadow .12s ease, border-color .12s ease; display:flex; flex-direction:column; gap:2px; position:relative; }}
        .ov-cal-cell:hover {{ transform:translateY(-1px); border-color:var(--primary); box-shadow:0 4px 12px rgba(99,102,241,0.15); }}
        .ov-cal-empty {{ background:transparent; border:none; cursor:default; }}
        .ov-cal-empty:hover {{ transform:none; box-shadow:none; }}
        .ov-cal-day {{ font-weight:700; color:var(--text); font-size:14px; }}
        .ov-cal-hours {{ font-size:12px; color:var(--text-muted); margin-top:auto; font-weight:600; }}
        .ov-cal-note {{ font-size:10px; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }}
        .ov-default {{ background:var(--bg); }}
        .ov-default .ov-cal-hours {{ color:var(--text-muted); opacity:0.7; }}
        .ov-custom {{ background:rgba(99,102,241,0.18); border-color:var(--primary); }}
        .ov-custom .ov-cal-hours {{ color:var(--primary); }}
        .ov-free {{ background:rgba(239,68,68,0.18); border-color:#ef4444; }}
        .ov-free .ov-cal-hours {{ color:#ef4444; }}
        .ov-today {{ box-shadow:0 0 0 2px var(--primary) inset; }}
        @media (max-width: 720px) {{
          .ov-cal-cell {{ min-height:62px; padding:4px 5px; }}
          .ov-cal-day {{ font-size:12px; }}
          .ov-cal-hours {{ font-size:11px; }}
          .ov-cal-note {{ display:none; }}
        }}

        </style>

        <script>

        var mpState = {{ year:new Date().getFullYear(), month:new Date().getMonth(), daily:[], editing:null }};
        function mpPad(n) {{ return n < 10 ? '0' + n : '' + n; }}
        function mpIso(y,m,d) {{ return y + '-' + mpPad(m+1) + '-' + mpPad(d); }}
        function mpEsc(s) {{ return String(s||'').replace(/[&<>"']/g, function(c) {{ return ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]; }}); }}
        function mpDay(date) {{ return (mpState.daily || []).find(function(d) {{ return (d.date||'').slice(0,10) === date; }}) || {{ date:date, sessions:[] }}; }}
        function mpMonthLabel() {{
          return new Date(mpState.year, mpState.month, 1).toLocaleDateString('es-CL', {{ month:'long', year:'numeric' }});
        }}
        async function mpLoad() {{
          try {{
            var r = await fetch('/api/student/manual-plan');
            var j = await r.json();
            mpState.daily = j.daily_plan || [];
          }} catch(e) {{ mpState.daily = []; }}
          mpRender();
        }}
        function mpRender() {{
          var label = document.getElementById('mp-month-label');
          if (label) label.textContent = mpMonthLabel();
          var first = new Date(mpState.year, mpState.month, 1).getDay();
          var leading = (first + 6) % 7;
          var days = new Date(mpState.year, mpState.month + 1, 0).getDate();
          var today = new Date();
          var todayIso = mpIso(today.getFullYear(), today.getMonth(), today.getDate());
          var html = '<div class="mp-head">' + ['Lun','Mar','Mi&eacute;','Jue','Vie','S&aacute;b','Dom'].map(function(d) {{ return '<div class="mp-dow">'+d+'</div>'; }}).join('') + '</div><div class="mp-grid">';
          for (var i=0;i<leading;i++) html += '<div class="mp-day mp-empty"></div>';
          for (var d=1; d<=days; d++) {{
            var iso = mpIso(mpState.year, mpState.month, d);
            var day = mpDay(iso);
            var sessions = day.sessions || [];
            html += '<div class="mp-day '+(iso===todayIso?'mp-today':'')+'" onclick="mpOpenDate(\\''+iso+'\\')"><div class="mp-num">'+d+'</div>';
            sessions.slice(0,3).forEach(function(s) {{
              html += '<div class="mp-task '+mpEsc(s.priority||'medium')+'">'+mpEsc((s.start_time ? s.start_time+' · ' : '') + (s.topic||''))+'</div>';
            }});
            if (sessions.length > 3) html += '<div class="mp-more">+'+(sessions.length-3)+' m&aacute;s</div>';
            if (!sessions.length) html += '<div class="mp-more">Agregar plan</div>';
            html += '</div>';
          }}
          html += '</div>';
          var cal = document.getElementById('mp-calendar');
          if (cal) cal.innerHTML = html;
        }}
        function mpMove(delta) {{
          mpState.month += delta;
          if (mpState.month < 0) {{ mpState.month = 11; mpState.year--; }}
          if (mpState.month > 11) {{ mpState.month = 0; mpState.year++; }}
          mpRender();
        }}
        function mpOpen(dateObj) {{
          var d = dateObj || new Date();
          mpState.year = d.getFullYear(); mpState.month = d.getMonth();
          mpOpenDate(mpIso(d.getFullYear(), d.getMonth(), d.getDate()));
        }}
        function mpOpenDate(iso) {{
          mpState.editing = iso;
          document.getElementById('mp-date-label').textContent = new Date(iso + 'T12:00:00').toLocaleDateString('es-CL', {{ weekday:'long', day:'numeric', month:'long' }});
          var sessions = (mpDay(iso).sessions || []).slice();
          var list = document.getElementById('mp-task-list');
          list.innerHTML = '';
          if (!sessions.length) sessions = [{{ start_time:'09:00', course:'General', topic:'', hours:0.5, type:'estudio', priority:'medium' }}];
          sessions.forEach(function(s) {{ mpAddRow(s); }});
          document.getElementById('mp-modal').style.display = 'flex';
        }}
        function mpAddRow(s) {{
          s = s || {{ start_time:'', course:'General', topic:'', hours:0.5, type:'estudio', priority:'medium' }};
          var row = document.createElement('div');
          row.className = 'mp-row';
          row.innerHTML =
            '<input class="mp-time" type="time" value="'+mpEsc(s.start_time||'')+'">' +
            '<input class="mp-topic" placeholder="Qu&eacute; vas a estudiar" value="'+mpEsc(s.topic||'')+'">' +
            '<input class="mp-hours" type="number" min="0.25" max="8" step="0.25" value="'+mpEsc(s.hours||0.5)+'">' +
            '<select class="mp-priority"><option value="high">Urgente</option><option value="medium">Normal</option><option value="low">Suave</option></select>' +
            '<button class="mp-del" onclick="this.parentElement.remove()" type="button">&times;</button>';
          row.querySelector('.mp-priority').value = s.priority || 'medium';
          document.getElementById('mp-task-list').appendChild(row);
        }}
        function mpClose() {{ document.getElementById('mp-modal').style.display = 'none'; mpState.editing = null; }}
        async function mpSave() {{
          var rows = Array.from(document.querySelectorAll('#mp-task-list .mp-row'));
          var sessions = rows.map(function(r) {{
            return {{
              start_time: r.querySelector('.mp-time').value,
              course: 'General',
              topic: r.querySelector('.mp-topic').value.trim(),
              hours: parseFloat(r.querySelector('.mp-hours').value || '0.5'),
              type: 'estudio',
              priority: r.querySelector('.mp-priority').value
            }};
          }}).filter(function(s) {{ return s.topic; }});
          var btn = document.getElementById('mp-save-btn');
          btn.disabled = true; btn.textContent = 'Guardando...';
          try {{
            var r = await fetch('/api/student/manual-plan', {{
              method:'POST',
              headers:{{'Content-Type':'application/json'}},
              body:JSON.stringify({{date:mpState.editing, sessions:sessions}})
            }});
            var j = await r.json();
            if (!r.ok) throw new Error(j.error || 'No se pudo guardar');
            mpState.daily = j.daily_plan || [];
            mpClose();
            mpRender();
          }} catch(e) {{
            alert(e.message || 'No se pudo guardar');
          }}
          btn.disabled = false; btn.textContent = 'Guardar plan';
        }}
        var mpModal = document.getElementById('mp-modal');
        if (mpModal) mpModal.addEventListener('click', function(e) {{ if (e.target === mpModal) mpClose(); }});
        mpLoad();

        function toggleFreeDay(day, free) {{

          var g = document.getElementById('hours-group-' + day);

          if (free) {{

            g.style.opacity = '0.3';

            g.style.pointerEvents = 'none';

            document.getElementById('hours-' + day).value = 0;

          }} else {{

            g.style.opacity = '1';

            g.style.pointerEvents = '';

            if (parseFloat(document.getElementById('hours-' + day).value) === 0) {{

              document.getElementById('hours-' + day).value = 4;

            }}

          }}

        }}



        async function saveSchedule() {{

          var btn = document.getElementById('save-sched-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Saving...';

          var schedule = [];

          for (var i = 0; i < 7; i++) {{

            var free = document.querySelector('.free-day-check[data-day="' + i + '"]').checked;

            var hours = parseFloat(document.getElementById('hours-' + i).value) || 0;

            schedule.push({{ day: i, hours: free ? 0 : hours, free: free }});

          }}

          try {{

            var csrfMeta = document.querySelector('meta[name="csrf-token"]');

            var csrfTok = csrfMeta ? csrfMeta.content : '';

            var r = await fetch('/api/student/schedule', {{

              method: 'PUT',

              headers: {{'Content-Type':'application/json', 'X-CSRFToken': csrfTok}},

              body: JSON.stringify({{ schedule: schedule }})

            }});

            if (r.ok) {{

              btn.innerHTML = '&#10003; Saved!';

              setTimeout(function() {{ btn.disabled = false; btn.innerHTML = 'Save Schedule'; }}, 1500);

            }} else {{

              var errTxt = 'Failed to save';

              try {{ var j = await r.json(); if (j.error) errTxt = j.error; }} catch(e) {{}}

              alert(errTxt); btn.disabled = false; btn.innerHTML = 'Save Schedule';

            }}

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); btn.disabled = false; btn.innerHTML = 'Save Schedule'; }}

        }}



        var diffLabels = ['', 'Very Easy', 'Easy', 'Medium', 'Hard', 'Very Hard'];

        async function setDiff(courseId, val) {{

          // Update stars visually

          document.querySelectorAll('.diff-star[data-course="' + courseId + '"]').forEach(function(star) {{

            var sv = parseInt(star.dataset.val);

            star.style.color = sv <= val ? 'var(--primary)' : 'var(--border)';

            star.style.fontWeight = sv <= val ? '700' : '400';

          }});

          document.getElementById('diff-label-' + courseId).textContent = diffLabels[val];

          try {{

            await fetch('/api/student/courses/' + courseId + '/difficulty', {{

              method: 'PUT',

              headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ difficulty: val }})

            }});

          }} catch(e) {{}}

        }}

        // ── Per-date overrides (calendar UI) ──
        function escHtml(s) {{ return String(s||'').replace(/[&<>"']/g, function(c){{ return ({{ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;" }})[c]; }}); }}
        var ovState = {{
          year: new Date().getFullYear(),
          month: new Date().getMonth(), // 0-indexed
          overrides: {{}}, // {{ 'YYYY-MM-DD': {{ available_hours, is_free_day, note }} }}
          weekly: {{}},   // {{ 0..6: {{ hours, free }} }} from saved schedule
          editingDate: null
        }};
        // Read weekly schedule from the inputs at the top so the calendar can show it
        function ovReadWeekly() {{
          ovState.weekly = {{}};
          for (var i = 0; i < 7; i++) {{
            var freeEl = document.querySelector('.free-day-check[data-day="' + i + '"]');
            var hrsEl = document.getElementById('hours-' + i);
            if (!freeEl || !hrsEl) continue;
            ovState.weekly[i] = {{ free: freeEl.checked, hours: parseFloat(hrsEl.value) || 0 }};
          }}
        }}
        function ovPad(n) {{ return n < 10 ? '0' + n : '' + n; }}
        function ovIso(y, m, d) {{ return y + '-' + ovPad(m+1) + '-' + ovPad(d); }}
        function ovMonthLabel(y, m) {{
          var names = ['January','February','March','April','May','June','July','August','September','October','November','December'];
          return names[m] + ' ' + y;
        }}
        function ovWeekdayName(jsDay) {{
          // jsDay: 0=Sun..6=Sat. We display Mon-first.
          return ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][jsDay];
        }}
        async function ovLoadOverrides() {{
          try {{
            // Fetch a wide range so navigating months doesn't refetch
            var r = await fetch('/api/student/date-overrides');
            var j = await r.json();
            ovState.overrides = {{}};
            (j.overrides || []).forEach(function(o) {{
              ovState.overrides[o.override_date] = {{
                hours: o.available_hours,
                free: !!o.is_free_day,
                note: o.note || ''
              }};
            }});
          }} catch(e) {{}}
          ovRender();
        }}
        function ovRender() {{
          ovReadWeekly();
          document.getElementById('ov-month-label').textContent = ovMonthLabel(ovState.year, ovState.month);
          var firstDow = new Date(ovState.year, ovState.month, 1).getDay(); // 0=Sun
          // Convert to Mon-first index: Mon=0..Sun=6
          var leading = (firstDow + 6) % 7;
          var daysInMonth = new Date(ovState.year, ovState.month + 1, 0).getDate();
          var todayIso = (function() {{ var d = new Date(); return ovIso(d.getFullYear(), d.getMonth(), d.getDate()); }})();
          var html = '<div class="ov-cal-head">';
          ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(function(n) {{
            html += '<div class="ov-cal-dow">' + n + '</div>';
          }});
          html += '</div><div class="ov-cal-grid">';
          for (var i = 0; i < leading; i++) html += '<div class="ov-cal-cell ov-cal-empty"></div>';
          for (var d = 1; d <= daysInMonth; d++) {{
            var iso = ovIso(ovState.year, ovState.month, d);
            var ov = ovState.overrides[iso];
            // Compute weekday (Mon=0..Sun=6) for default
            var jsDay = new Date(ovState.year, ovState.month, d).getDay();
            var monIdx = (jsDay + 6) % 7;
            var def = ovState.weekly[monIdx] || {{ free: true, hours: 0 }};
            var cls = 'ov-cal-cell';
            var label = '';
            var noteHtml = '';
            if (ov) {{
              if (ov.free) {{ cls += ' ov-free'; label = 'Free'; }}
              else {{ cls += ' ov-custom'; label = ov.hours + 'h'; }}
              if (ov.note) noteHtml = '<div class="ov-cal-note" title="'+escHtml(ov.note)+'">' + escHtml(ov.note) + '</div>';
            }} else {{
              if (def.free) label = 'Free';
              else label = def.hours + 'h';
              cls += ' ov-default';
            }}
            if (iso === todayIso) cls += ' ov-today';
            html += '<div class="' + cls + '" onclick="ovOpenModal(\\''+iso+'\\')">' +
              '<div class="ov-cal-day">' + d + '</div>' +
              '<div class="ov-cal-hours">' + label + '</div>' +
              noteHtml + '</div>';
          }}
          html += '</div>';
          document.getElementById('ov-calendar').innerHTML = html;
        }}
        function ovPrevMonth() {{
          ovState.month--;
          if (ovState.month < 0) {{ ovState.month = 11; ovState.year--; }}
          ovRender();
        }}
        function ovNextMonth() {{
          ovState.month++;
          if (ovState.month > 11) {{ ovState.month = 0; ovState.year++; }}
          ovRender();
        }}
        function ovOpenModal(iso) {{
          ovState.editingDate = iso;
          var parts = iso.split('-').map(function(x){{ return parseInt(x,10); }});
          var dt = new Date(parts[0], parts[1]-1, parts[2]);
          document.getElementById('ov-modal-date').textContent = iso;
          document.getElementById('ov-modal-weekday').textContent = ovWeekdayName(dt.getDay());
          var ov = ovState.overrides[iso];
          if (ov) {{
            if (ov.free) {{
              document.getElementById('ov-mode-free').checked = true;
            }} else {{
              document.getElementById('ov-mode-hours').checked = true;
              document.getElementById('ov-hours').value = ov.hours;
            }}
            document.getElementById('ov-note').value = ov.note || '';
          }} else {{
            document.getElementById('ov-mode-hours').checked = true;
            // Pre-fill from weekly default
            var monIdx = (dt.getDay() + 6) % 7;
            var def = ovState.weekly[monIdx];
            document.getElementById('ov-hours').value = (def && !def.free) ? def.hours : 2;
            document.getElementById('ov-note').value = '';
          }}
          document.getElementById('ov-modal').style.display = 'flex';
        }}
        function ovCloseModal() {{
          document.getElementById('ov-modal').style.display = 'none';
          ovState.editingDate = null;
        }}
        async function ovSaveModal() {{
          var iso = ovState.editingDate;
          if (!iso) return;
          var btn = document.getElementById('ov-save-btn');
          btn.disabled = true; btn.textContent = 'Saving...';
          var mode = document.querySelector('input[name="ov-mode"]:checked').value;
          try {{
            if (mode === 'default') {{
              // Delete the override
              await fetch('/api/student/date-overrides/' + encodeURIComponent(iso), {{ method: 'DELETE' }});
              delete ovState.overrides[iso];
            }} else {{
              var hours = mode === 'free' ? 0 : (parseFloat(document.getElementById('ov-hours').value) || 0);
              var free = mode === 'free';
              var note = document.getElementById('ov-note').value || '';
              var r = await fetch('/api/student/date-overrides', {{
                method: 'POST', headers: {{'Content-Type':'application/json'}},
                body: JSON.stringify({{ date: iso, hours: hours, free: free, note: note }})
              }});
              if (r.ok) {{
                ovState.overrides[iso] = {{ hours: hours, free: free, note: note }};
              }} else {{
                var j = {{}}; try {{ j = await r.json(); }} catch(e){{}}
                alert(j.error || 'Failed to save');
              }}
            }}
          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); }}
          btn.disabled = false; btn.textContent = 'Save';
          ovCloseModal();
          ovRender();
        }}
        // Close modal on backdrop click
        document.getElementById('ov-modal').addEventListener('click', function(e) {{
          if (e.target === this) ovCloseModal();
        }});
        ovLoadOverrides();
        </script>

        """, active_page="student_schedule")



    # ── Flashcard API routes ────────────────────────────────



    @app.route("/api/student/flashcards/generate", methods=["POST"])

    @limiter.limit("5 per minute")

    def student_generate_flashcards():

        """Generate AI flashcards for a course."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        from student import subscription as _sub
        _ok, _why = _sub.can_generate_flashcards_today(_cid())
        if not _ok:
            return jsonify({"error": _why, "upgrade_required": True}), 402

        data = request.get_json(force=True)

        course_id = data.get("course_id")

        ad_hoc_source = (data.get("source_text") or "").strip()

        ad_hoc_title = (data.get("title") or "").strip()

        topics = data.get("topics", [])

        exam_id = data.get("exam_id")

        count = min(int(data.get("count", 15)), 100)

        # Apply per-tier card cap (free = 30 max).
        count = _sub.cap_cards(_cid(), count)



        # Drag-and-drop / paste path: caller supplied raw source text — no course needed.

        if ad_hoc_source:

            course_name = ad_hoc_title or "Custom Material"

            cards = generate_flashcards(

                course_name=course_name,

                topics=topics or None,

                source_text=ad_hoc_source,

                count=count,

            )

            if not cards:

                return jsonify({"error": "Failed to generate flashcards. Try again."}), 500

            title = ad_hoc_title or f"Flashcards: {course_name}"

            deck_id = sdb.create_flashcard_deck(_cid(), title, course_id=course_id or None,

                                                exam_id=exam_id, source_type="drop")

            sdb.add_flashcards(deck_id, cards)

            try:
                from student import subscription as _sub
                _sub.record_generation(_cid(), "flashcards_generated")
            except Exception:
                pass

            return jsonify({"deck_id": deck_id, "card_count": len(cards)})



        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        # Gather source material — ONLY from student's uploaded files

        source_text = ""

        files = sdb.get_course_files(_cid(), course_id, exam_id=exam_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

        # Also include AI-generated notes for this course

        notes = sdb.get_notes(_cid(), course_id)

        for n in notes:

            if n.get("content_html"):

                source_text += n["content_html"] + "\n\n"



        if not source_text.strip():

            return jsonify({"error": "No files uploaded for this course/exam. Please upload your study material first."}), 400



        cards = generate_flashcards(

            course_name=course["name"],

            topics=topics or None,

            source_text=source_text,

            count=count,

        )

        if not cards:

            return jsonify({"error": "Failed to generate flashcards. Try again."}), 500



        title = ad_hoc_title or f"Flashcards: {course['name']}"

        deck_id = sdb.create_flashcard_deck(_cid(), title, course_id=course_id, exam_id=exam_id)

        sdb.add_flashcards(deck_id, cards)

        try:
            from student import subscription as _sub
            _sub.record_generation(_cid(), "flashcards_generated")
        except Exception:
            pass

        return jsonify({"deck_id": deck_id, "card_count": len(cards)})



    @app.route("/api/student/flashcards/decks", methods=["GET"])

    def student_get_flashcard_decks():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course_id = request.args.get("course_id", type=int)

        decks = sdb.get_flashcard_decks(_cid(), course_id)

        return jsonify({"decks": [dict(d) for d in decks]})



    @app.route("/api/student/flashcards/decks/<int:deck_id>", methods=["GET"])

    def student_get_flashcard_deck(deck_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        deck = sdb.get_flashcard_deck(deck_id, _cid())

        if not deck:

            return jsonify({"error": "Not found"}), 404

        cards = sdb.get_flashcards(deck_id)

        return jsonify({"deck": dict(deck), "cards": [dict(c) for c in cards]})



    @app.route("/api/student/flashcards/decks/<int:deck_id>", methods=["DELETE"])

    def student_delete_flashcard_deck(deck_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_flashcard_deck(deck_id, _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/flashcards/progress", methods=["POST"])

    def student_flashcard_progress():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        quality = data.get("quality")  # 0-5 for SRS mode

        correct = data.get("correct", False)

        if quality is not None:

            quality = max(0, min(5, int(quality)))

            correct = quality >= 3

        sdb.update_flashcard_progress(data["card_id"], correct, quality=quality)

        # Coins only — flashcards give 1 coin per correct, no XP.
        # We still log a 0-XP row so the badge counter (which queries
        # student_xp WHERE action='flashcard_review') keeps working.
        cid = _cid()
        if correct:
            try:
                sdb.award_xp(cid, "flashcard_review", 0, "Flashcard correct")
                sdb.add_coins(cid, 1, "flashcard_correct")
            except Exception:
                pass

        # Check flashcard badges

        from outreach.db import _fetchval, get_db

        with get_db() as db:

            fc_count = _fetchval(db, "SELECT COUNT(*) FROM student_xp WHERE client_id = %s AND action = 'flashcard_review'", (cid,)) or 0

        for key, threshold in [("flashcard_fan", 100), ("flashcard_500", 500), ("flashcard_1000", 1000)]:

            if fc_count >= threshold:

                sdb.earn_badge(cid, key)

        return jsonify({"ok": True})



    @app.route("/api/student/flashcards/<int:card_id>", methods=["PUT"])

    def student_update_flashcard(card_id):

        """Edit a single flashcard's front/back text."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        front = (data.get("front") or "").strip()

        back = (data.get("back") or "").strip()

        deck_id = data.get("deck_id")

        if not front or not back or not deck_id:

            return jsonify({"error": "front, back, and deck_id required"}), 400

        deck = sdb.get_flashcard_deck(deck_id, _cid())

        if not deck:

            return jsonify({"error": "Not found"}), 404

        sdb.update_flashcard(card_id, deck_id, front, back)

        return jsonify({"ok": True})



    @app.route("/api/student/flashcards/add", methods=["POST"])

    def student_add_flashcard():

        """Add a new flashcard to an existing deck."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        front = (data.get("front") or "").strip()

        back = (data.get("back") or "").strip()

        deck_id = data.get("deck_id")

        if not front or not back or not deck_id:

            return jsonify({"error": "front, back, and deck_id required"}), 400

        deck = sdb.get_flashcard_deck(deck_id, _cid())

        if not deck:

            return jsonify({"error": "Not found"}), 404

        card_id = sdb.add_flashcard(deck_id, front, back)

        return jsonify({"ok": True, "card_id": card_id})



    @app.route("/api/student/flashcards/<int:card_id>", methods=["DELETE"])

    def student_delete_flashcard(card_id):

        """Delete a single flashcard."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        deck_id = request.args.get("deck_id", type=int)

        if not deck_id:

            return jsonify({"error": "deck_id required"}), 400

        deck = sdb.get_flashcard_deck(deck_id, _cid())

        if not deck:

            return jsonify({"error": "Not found"}), 404

        sdb.delete_flashcard(card_id, deck_id)

        return jsonify({"ok": True})



    # ── Quiz API routes ─────────────────────────────────────



    _quiz_gen_status: dict[int, dict] = {}


    @app.route("/api/student/quizzes/generate", methods=["POST"])
    @limiter.limit("5 per minute")

    def student_generate_quiz():

        """Generate AI quiz for a course."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        from student import subscription as _sub
        _ok, _why = _sub.can_generate_quiz_today(_cid())
        if not _ok:
            return jsonify({"error": _why, "upgrade_required": True}), 402

        data = request.get_json(force=True)

        course_id = data.get("course_id")

        ad_hoc_source = (data.get("source_text") or "").strip()

        ad_hoc_title = (data.get("title") or "").strip()

        topics = data.get("topics", [])

        exam_id = data.get("exam_id")

        difficulty = data.get("difficulty", "medium")

        if difficulty not in ("easy", "medium", "hard"):

            difficulty = "medium"

        try:

            count = int(data.get("count", 10))

        except (TypeError, ValueError):

            count = 10

        count = max(1, min(count, 100))  # hard ceiling — generation batches under the hood

        # Apply per-tier question cap (free = 30 max).
        from student import subscription as _sub
        count = _sub.cap_questions(_cid(), count)



        # Drag-and-drop / paste path: caller supplied raw source text — no course needed.

        if ad_hoc_source:

            course_name = ad_hoc_title or "Custom Material"

            questions = generate_quiz(

                course_name=course_name,

                topics=topics or None,

                source_text=ad_hoc_source,

                difficulty=difficulty,

                count=count,

            )

            if not questions:

                return jsonify({"error": "Failed to generate quiz. Try again."}), 500

            title = ad_hoc_title or f"Quiz: {course_name} ({difficulty})"

            quiz_id = sdb.create_quiz(_cid(), title, difficulty, course_id=course_id or None, exam_id=exam_id)

            sdb.add_quiz_questions(quiz_id, questions)

            try:
                from student import subscription as _sub
                _sub.record_generation(_cid(), "quiz_generated")
            except Exception:
                pass

            return jsonify({

                "quiz_id": quiz_id,

                "question_count": len(questions),

                "requested": count,

                "short": len(questions) < count,

            })



        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        # Gather source material — ONLY from student's uploaded files

        source_text = ""

        files = sdb.get_course_files(_cid(), course_id, exam_id=exam_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"

        # Also include AI-generated notes

        notes = sdb.get_notes(_cid(), course_id)

        for n in notes:

            if n.get("content_html"):

                source_text += n["content_html"] + "\n\n"



        if not source_text.strip():

            return jsonify({"error": "No files uploaded for this course/exam. Please upload your study material first."}), 400



        questions = generate_quiz(

            course_name=course["name"],

            topics=topics or None,

            source_text=source_text,

            difficulty=difficulty,

            count=count,

        )

        if not questions:

            return jsonify({"error": "Failed to generate quiz. Try again."}), 500



        title = data.get("title", f"Quiz: {course['name']} ({difficulty})")

        quiz_id = sdb.create_quiz(_cid(), title, difficulty, course_id=course_id, exam_id=exam_id)

        sdb.add_quiz_questions(quiz_id, questions)



        return jsonify({

            "quiz_id": quiz_id,

            "question_count": len(questions),

            "requested": count,

            "short": len(questions) < count,

        })



    @app.route("/api/student/quizzes/generate-async", methods=["POST"])
    @limiter.limit("5 per minute")

    def student_generate_quiz_async():

        """Start quiz generation in the background so long AI calls do not time out the browser."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        client_id = _cid()

        from student import subscription as _sub
        _ok, _why = _sub.can_generate_quiz_today(client_id)
        if not _ok:
            return jsonify({"error": _why, "upgrade_required": True}), 402

        existing = _quiz_gen_status.get(client_id, {})
        if existing.get("status") == "running":
            return jsonify({"queued": True, "quiz_status": existing})

        data = request.get_json(force=True) if request.is_json else {}
        _quiz_gen_status[client_id] = {"status": "running", "progress": "Generating your quiz..."}

        def _do_quiz():
            try:
                course_id = data.get("course_id")
                ad_hoc_source = (data.get("source_text") or "").strip()
                ad_hoc_title = (data.get("title") or "").strip()
                topics = data.get("topics", [])
                exam_id = data.get("exam_id")
                difficulty = data.get("difficulty", "medium")
                if difficulty not in ("easy", "medium", "hard"):
                    difficulty = "medium"
                try:
                    count = int(data.get("count", 10))
                except (TypeError, ValueError):
                    count = 10
                count = _sub.cap_questions(client_id, max(1, min(count, 100)))

                if ad_hoc_source:
                    course_name = ad_hoc_title or "Custom Material"
                    questions = generate_quiz(course_name=course_name, topics=topics or None, source_text=ad_hoc_source, difficulty=difficulty, count=count)
                    if not questions:
                        raise ValueError("Failed to generate quiz. Try again.")
                    title = ad_hoc_title or f"Quiz: {course_name} ({difficulty})"
                    quiz_id = sdb.create_quiz(client_id, title, difficulty, course_id=course_id or None, exam_id=exam_id)
                    sdb.add_quiz_questions(quiz_id, questions)
                else:
                    if not course_id:
                        raise ValueError("course_id required")
                    course = sdb.get_course(course_id)
                    if not course or course["client_id"] != client_id:
                        raise ValueError("Course not found")
                    source_text = ""
                    for f in sdb.get_course_files(client_id, course_id, exam_id=exam_id):
                        if f.get("extracted_text"):
                            source_text += f"--- {f.get('original_name','')} ---\n{f['extracted_text']}\n\n"
                    for n in sdb.get_notes(client_id, course_id):
                        if n.get("content_html"):
                            source_text += n["content_html"] + "\n\n"
                    if not source_text.strip():
                        raise ValueError("No files uploaded for this course/exam. Please upload your study material first.")
                    questions = generate_quiz(course_name=course["name"], topics=topics or None, source_text=source_text, difficulty=difficulty, count=count)
                    if not questions:
                        raise ValueError("Failed to generate quiz. Try again.")
                    title = data.get("title", f"Quiz: {course['name']} ({difficulty})")
                    quiz_id = sdb.create_quiz(client_id, title, difficulty, course_id=course_id, exam_id=exam_id)
                    sdb.add_quiz_questions(quiz_id, questions)

                try:
                    _sub.record_generation(client_id, "quiz_generated")
                except Exception:
                    pass

                _quiz_gen_status[client_id] = {
                    "status": "done",
                    "progress": "Quiz generated!",
                    "quiz_id": quiz_id,
                    "question_count": len(questions),
                    "requested": count,
                    "short": len(questions) < count,
                }
            except Exception as e:
                log.error("Quiz generation failed for client %s: %s", client_id, e)
                _quiz_gen_status[client_id] = {"status": "error", "progress": str(e), "error": str(e)}

        threading.Thread(target=_do_quiz, daemon=True).start()

        return jsonify({"queued": True, "quiz_status": _quiz_gen_status[client_id]})


    @app.route("/api/student/quizzes/generate/status", methods=["GET"])

    def student_generate_quiz_status():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        return jsonify(_quiz_gen_status.get(_cid(), {"status": "idle"}))


    @app.route("/api/student/quizzes", methods=["GET"])

    def student_get_quizzes():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course_id = request.args.get("course_id", type=int)

        quizzes = sdb.get_quizzes(_cid(), course_id)

        return jsonify({"quizzes": [dict(q) for q in quizzes]})



    @app.route("/api/student/quizzes/<int:quiz_id>", methods=["GET"])

    def student_get_quiz(quiz_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        quiz = sdb.get_quiz(quiz_id, _cid())

        if not quiz:

            return jsonify({"error": "Not found"}), 404

        questions = sdb.get_quiz_questions(quiz_id)

        from student import subscription as _sub
        is_plus = _sub.has_unlimited_ai(_cid())
        clean_questions = []
        for q in questions:
            qd = dict(q)
            if not is_plus:
                qd["explanation"] = ""
            clean_questions.append(qd)

        return jsonify({"quiz": dict(quiz), "questions": clean_questions})



    @app.route("/api/student/quizzes/<int:quiz_id>/score", methods=["POST"])
    def student_submit_quiz_score(quiz_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        score = int(data.get("score", 0))

        sdb.update_quiz_score(quiz_id, score)

        # Quizzes give COINS only, no XP. Focus sessions are the only XP
        # source. Coins are intentionally low so the cosmetics still take
        # real grind. We log a 0-XP action row so badge queries keep working.
        cid = _cid()
        try:
            coins = max(1, score // 25)      # 50% -> 2, 80% -> 3, 100% -> 4
            if score >= 90:
                coins += 2                   # small bonus for excellence
            sdb.award_xp(cid, "quiz_score", 0, f"Quiz {quiz_id}: {score}%")
            sdb.add_coins(cid, coins, f"quiz_{quiz_id}_{score}pct")
        except Exception:
            pass

        if score == 100:

            sdb.earn_badge(cid, "quiz_master")

        if not sdb.get_badges(cid) or not any(b["badge_key"] == "first_quiz" for b in sdb.get_badges(cid)):

            sdb.earn_badge(cid, "first_quiz")

        # Check quiz_10 badge

        from outreach.db import _fetchval, get_db

        with get_db() as db:

            quiz_count = _fetchval(db, "SELECT COUNT(*) FROM student_quizzes WHERE client_id = %s AND attempts > 0", (cid,)) or 0

        if quiz_count >= 10:

            sdb.earn_badge(cid, "quiz_10")

        if quiz_count >= 25:

            sdb.earn_badge(cid, "quiz_25")

        if quiz_count >= 50:

            sdb.earn_badge(cid, "quiz_50")

        return jsonify({"ok": True})



    @app.route("/api/student/quizzes/<int:quiz_id>", methods=["DELETE"])
    def student_delete_quiz(quiz_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_quiz(quiz_id, _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/quizzes/<int:quiz_id>/analyze", methods=["POST"])
    @limiter.limit("10 per minute")

    def student_analyze_quiz(quiz_id):

        """

        Deep post-quiz AI analysis. Takes the user's per-question answers +

        per-question time-taken and returns topic breakdown, strengths,

        weaknesses, mistake patterns, and a prioritized action plan.

        """

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        quiz = sdb.get_quiz(quiz_id, _cid())

        if not quiz:

            return jsonify({"error": "Not found"}), 404

        from student import subscription as _sub
        is_plus = _sub.has_unlimited_ai(_cid())



        data = request.get_json(force=True) or {}

        answers = data.get("answers", [])  # [{q_id, selected, time}]

        if not answers:

            return jsonify({"error": "No answers provided"}), 400



        questions = {q["id"]: dict(q) for q in sdb.get_quiz_questions(quiz_id)}



        # Build structured payload for the AI

        items = []

        topic_stats: dict[str, dict] = {}

        correct_count = 0

        total_time = 0.0

        times: list[float] = []



        for a in answers:

            q = questions.get(int(a.get("q_id", -1)))

            if not q:

                continue

            sel = (a.get("selected") or "").lower()

            is_correct = sel == q["correct"]

            if is_correct:

                correct_count += 1

            t = float(a.get("time", 0) or 0)

            total_time += t

            times.append(t)

            topic = (q.get("topic") or "General").strip() or "General"

            ts = topic_stats.setdefault(topic, {"correct": 0, "total": 0, "time": 0.0})

            ts["total"] += 1

            ts["time"] += t

            if is_correct:

                ts["correct"] += 1

            items.append({

                "topic": topic,

                "question": q["question"],

                "your_answer": sel.upper() if sel else "—",

                "your_answer_text": q.get(f"option_{sel}", "") if sel in ("a", "b", "c", "d") else "",

                "correct_answer": q["correct"].upper(),

                "correct_answer_text": q.get(f"option_{q['correct']}", ""),

                "explanation": q.get("explanation", "") if is_plus else "",

                "is_correct": is_correct,

                "seconds": round(t, 1),

            })



        total = len(items)

        score_pct = round(100 * correct_count / total) if total else 0

        avg_time = round(total_time / total, 1) if total else 0.0



        # Topic breakdown (deterministic, not AI)

        breakdown = []

        for topic, s in topic_stats.items():

            pct = round(100 * s["correct"] / s["total"]) if s["total"] else 0

            breakdown.append({

                "topic": topic,

                "correct": s["correct"],

                "total": s["total"],

                "percent": pct,

                "avg_time": round(s["time"] / s["total"], 1) if s["total"] else 0,

            })

        breakdown.sort(key=lambda x: (x["percent"], -x["total"]))



        # AI narrative analysis

        try:

            import json as _json

            from outreach.ai import _ai

            wrong_items = [i for i in items if not i["is_correct"]]

            right_items = [i for i in items if i["is_correct"]]

            wrong_sample = wrong_items[:25]

            right_sample = right_items[:10]

            prompt = f"""You are an elite tutor analyzing a student's quiz results.

Course: {quiz.get('course_name') or 'General'}

Quiz: {quiz.get('title') or ''}

Difficulty: {quiz.get('difficulty') or 'medium'}

Overall score: {score_pct}% ({correct_count}/{total})

Average time per question: {avg_time}s



TOPIC BREAKDOWN (what they got in each concept):

{_json.dumps(breakdown, indent=2)}



QUESTIONS THEY GOT WRONG:

{_json.dumps(wrong_sample, indent=2, default=str)}



QUESTIONS THEY GOT RIGHT (sample):

{_json.dumps(right_sample, indent=2, default=str)}



Produce a student-facing analysis that is blunt, specific, and actionable — BETTER than Gemini's quiz feedback.

Return ONLY valid JSON with this exact shape:

{{

  "headline": "One sharp sentence summarizing performance (no fluff).",

  "verdict": "mastery" | "solid" | "shaky" | "struggling",

  "strengths": [

    {{"topic": "Short concept name", "detail": "Why they clearly understand this, citing a question if useful"}}

  ],

  "weaknesses": [

    {{"topic": "Short concept name", "detail": "What exactly they're missing — specific misconception, not generic advice",

      "fix": "One concrete fix: re-read X, practice Y, watch Z-type resource"}}

  ],

  "mistake_patterns": ["Pattern 1 (e.g. 'confuses mitosis phases', 'guesses on calculations')"],

  "time_insight": "Short note on their pacing — too fast/slow, where they rushed, etc.",

  "next_actions": [

    "Concrete step 1 (e.g. 'Redo questions 3, 7, 12 — all about X')",

    "Concrete step 2",

    "Concrete step 3"

  ],

  "study_plan_30min": [

    {{"minutes": 10, "task": "Specific focused task"}},

    {{"minutes": 15, "task": "Specific focused task"}},

    {{"minutes": 5,  "task": "Specific focused task"}}

  ],

  "encouragement": "One honest, specific, non-patronizing sentence."

}}



RULES:

- 3-6 strengths max. 3-6 weaknesses max. Skip sections if genuinely empty (empty arrays fine).

- Quote specific topics / question numbers when relevant.

- Never say "review the material" — be concrete.

- Keep it in the same language as the quiz content.

No markdown, no code fences. ONLY JSON.

"""

            try:

                resp = _ai().chat.completions.create(

                    model="gpt-4o-mini",

                    messages=[{"role": "user", "content": prompt}],

                    temperature=0.4,

                    max_tokens=2000,

                    response_format={"type": "json_object"},

                )

            except Exception:

                resp = _ai().chat.completions.create(

                    model="gpt-4o-mini",

                    messages=[{"role": "user", "content": prompt}],

                    temperature=0.4,

                    max_tokens=2000,

                )

            raw = (resp.choices[0].message.content or "").strip()

            raw = re.sub(r"^```json?\s*", "", raw, flags=re.IGNORECASE)

            raw = re.sub(r"\s*```$", "", raw)

            try:

                ai = _json.loads(raw)

            except Exception:

                ai = {"headline": "Quiz analyzed.", "verdict": "solid", "strengths": [],

                      "weaknesses": [], "mistake_patterns": [], "time_insight": "",

                      "next_actions": [], "study_plan_30min": [], "encouragement": ""}

        except Exception as e:

            log.error("Quiz analysis failed: %s", e)

            ai = {"headline": "Analysis unavailable.", "verdict": "solid", "strengths": [],

                  "weaknesses": [], "mistake_patterns": [], "time_insight": "",

                  "next_actions": [], "study_plan_30min": [], "encouragement": ""}



        # Simple pacing stats

        pacing = {

            "avg_time": avg_time,

            "fastest": round(min(times), 1) if times else 0,

            "slowest": round(max(times), 1) if times else 0,

            "total_time": round(total_time, 1),

        }

        weak_topics = []
        for b in breakdown:
            if int(b.get("percent") or 0) < 70:
                weak_topics.append({
                    "topic": b.get("topic") or "General",
                    "score": b.get("percent") or 0,
                    "correct": b.get("correct") or 0,
                    "total": b.get("total") or 0,
                    "avg_time": b.get("avg_time") or 0,
                })
        weak_topics = weak_topics[:5]
        plus_report = {
            "unlocked": is_plus,
            "weak_topics": weak_topics if is_plus else [],
            "weaknesses": ai.get("weaknesses", []) if is_plus else [],
            "next_actions": ai.get("next_actions", []) if is_plus else [],
            "study_plan_30min": ai.get("study_plan_30min", []) if is_plus else [],
        }

        if not is_plus:
            ai = dict(ai)
            ai["weaknesses"] = []
            ai["next_actions"] = []
            ai["study_plan_30min"] = []



        return jsonify({

            "score": score_pct,

            "correct": correct_count,

            "total": total,

            "breakdown": breakdown,

            "pacing": pacing,

            "items": items,

            "ai": ai,

            "plus_report": plus_report,

        })



    # ── Notes API routes ────────────────────────────────────



    @app.route("/api/student/notes/generate", methods=["POST"])

    @limiter.limit("5 per minute")

    def student_generate_notes():

        """Generate AI study notes for a course."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        course_id = data.get("course_id")

        ad_hoc_source = (data.get("source_text") or "").strip()

        ad_hoc_title = (data.get("title") or "").strip()

        topics = data.get("topics", [])



        # Drag-and-drop / paste path: caller supplied raw source text — no course required.

        if ad_hoc_source:

            course_name = ad_hoc_title or "Custom Material"

            result = generate_notes(

                course_name=course_name,

                topics=topics or None,

                source_text=ad_hoc_source,

            )

            if not result.get("content_html"):

                return jsonify({"error": "Failed to generate notes. Try again."}), 500

            title = ad_hoc_title or result.get("title") or f"Notes: {course_name}"

            note_id = sdb.create_note(_cid(), title, result["content_html"],

                                      course_id=course_id or None,

                                      source_type="drop")

            return jsonify({"note_id": note_id, "title": title})



        if not course_id:

            return jsonify({"error": "course_id required"}), 400

        course = sdb.get_course(course_id)

        if not course or course["client_id"] != _cid():

            return jsonify({"error": "Course not found"}), 404



        analysis = json.loads(course["analysis_json"]) if isinstance(course["analysis_json"], str) else (course["analysis_json"] or {})



        source_text = ""

        files = sdb.get_course_files(_cid(), course_id)

        for f in files:

            if f.get("extracted_text"):

                source_text += f["extracted_text"] + "\n\n"



        if not topics and analysis.get("weekly_schedule"):

            topics = []

            for w in analysis["weekly_schedule"]:

                topics.extend(w.get("topics", []))



        result = generate_notes(

            course_name=course["name"],

            topics=topics or None,

            source_text=source_text,

        )

        if not result.get("content_html"):

            return jsonify({"error": "Failed to generate notes. Try again."}), 500



        note_id = sdb.create_note(_cid(), result["title"], result["content_html"],

                                  course_id=course_id)

        return jsonify({"note_id": note_id, "title": result["title"]})



    @app.route("/api/student/notes/upload-pdf", methods=["POST"])

    @limiter.limit("10 per minute")

    def student_upload_pdf_note():

        """Upload a PDF/DOCX and create a note from extracted text."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        if "file" not in request.files:

            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]

        if not f.filename:

            return jsonify({"error": "Empty filename"}), 400

        fname = f.filename

        fl = fname.lower()

        if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc")):

            return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

        content = f.read(50 * 1024 * 1024 + 1)

        if len(content) > 50 * 1024 * 1024:

            return jsonify({"error": "File too large (max 50MB)"}), 400

        text = ""

        try:

            if fl.endswith(".pdf"):

                text = extract_text_from_pdf(content)

            elif fl.endswith((".docx", ".doc")):

                text = extract_text_from_docx(content)

        except Exception as e:

            return jsonify({"error": f"Could not extract text: {e}"}), 400

        if not text or len(text.strip()) < 20:

            return jsonify({"error": "Could not extract enough readable text from this file"}), 400

        title = fname.rsplit(".", 1)[0]

        import re as _re

        # Clean null bytes and collapse extra whitespace within lines

        text = text.replace('\x00', '')

        text = _re.sub(r'[^\S\n]+', ' ', text)

        # Collapse 3+ consecutive blank lines to 2

        text = _re.sub(r'\n{3,}', '\n\n', text)

        # Build HTML with structure detection

        html = "<h1>" + _esc(title) + "</h1>\n"

        lines = text.split("\n")

        for line in lines:

            p = line.strip()

            if not p:

                continue

            # Detect heading-like lines

            if len(p) < 120 and not p.endswith('.') and (p[0].isupper() or p[0].isdigit()):

                words = p.split()

                if len(words) <= 14 and any(c.isalpha() for c in p):

                    if _re.match(r'^(\d+[\.\)]\s*|Cap[ií]tulo|Secci[oó]n|Definici[oó]n|Teorema|Proposici[oó]n|Ejemplo|Lema|Corolario|Observaci[oó]n)', p, _re.IGNORECASE):

                        html += "<h2>" + _esc(p) + "</h2>\n"

                        continue

                    elif _re.match(r'^\d+\.\d+', p):

                        html += "<h3>" + _esc(p) + "</h3>\n"

                        continue

            html += "<p>" + _esc(p) + "</p>\n"

        note_id = sdb.create_note(_cid(), title, html, source_type="pdf-upload")

        return jsonify({"note_id": note_id, "title": title})



    @app.route("/api/student/notes", methods=["GET"])

    def student_get_notes():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        course_id = request.args.get("course_id", type=int)

        notes = sdb.get_notes(_cid(), course_id)

        return jsonify({"notes": [dict(n) for n in notes]})



    @app.route("/api/student/notes/<int:note_id>", methods=["GET"])

    def student_get_note(note_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        note = sdb.get_note(note_id, _cid())

        if not note:

            return jsonify({"error": "Not found"}), 404

        return jsonify(dict(note))



    @app.route("/api/student/notes/<int:note_id>", methods=["PUT"])

    def student_update_note(note_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        sdb.update_note(note_id, _cid(), data.get("content_html", ""))

        return jsonify({"ok": True})



    @app.route("/api/student/notes/<int:note_id>", methods=["DELETE"])

    def student_delete_note(note_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_note(note_id, _cid())

        return jsonify({"ok": True})



    @app.route("/api/student/notes/<int:note_id>/pdf")

    def student_export_note_pdf(note_id):

        """Export a note as PDF."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        note = sdb.get_note(note_id, _cid())

        if not note:

            return jsonify({"error": "Not found"}), 404

        from io import BytesIO

        import re as _re

        from fpdf import FPDF



        title = note.get("title", "Note")

        html = note.get("content_html", "")

        # Strip HTML tags for clean text, preserve structure

        def strip_html(s):

            s = _re.sub(r'<br\s*/?>', '\n', s)

            s = _re.sub(r'</p>|</div>|</li>|</h[1-6]>', '\n', s)

            s = _re.sub(r'<[^>]+>', '', s)

            s = s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')

            s = s.replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')

            s = s.replace('&middot;', '·')

            return s.strip()



        # Extract structured blocks from HTML

        blocks = []

        parts = _re.split(r'(<h[1-3][^>]*>.*?</h[1-3]>)', html, flags=_re.DOTALL)

        for part in parts:

            part = part.strip()

            if not part:

                continue

            if _re.match(r'<h1[^>]*>', part):

                blocks.append(('h1', strip_html(part)))

            elif _re.match(r'<h2[^>]*>', part):

                blocks.append(('h2', strip_html(part)))

            elif _re.match(r'<h3[^>]*>', part):

                blocks.append(('h3', strip_html(part)))

            else:

                text = strip_html(part)

                if text:

                    blocks.append(('p', text))



        pdf = FPDF()

        pdf.set_auto_page_break(auto=True, margin=20)

        pdf.add_page()

        pdf.set_font("Helvetica", "B", 20)

        pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 9)

        pdf.set_text_color(120, 120, 120)

        pdf.cell(0, 6, f"Exported from MachReach  -  {str(note.get('created_at',''))[:10]}", new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(0, 0, 0)

        pdf.ln(6)



        for btype, text in blocks:

            if btype == 'h1':

                pdf.set_font("Helvetica", "B", 18)

                pdf.ln(4)

                pdf.multi_cell(0, 8, text)

                pdf.ln(2)

            elif btype == 'h2':

                pdf.set_font("Helvetica", "B", 15)

                pdf.ln(4)

                pdf.multi_cell(0, 7, text)

                pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 170, pdf.get_y())

                pdf.ln(3)

            elif btype == 'h3':

                pdf.set_font("Helvetica", "B", 13)

                pdf.ln(3)

                pdf.multi_cell(0, 7, text)

                pdf.ln(2)

            else:

                pdf.set_font("Helvetica", "", 11)

                for line in text.split('\n'):

                    line = line.strip()

                    if line:

                        pdf.multi_cell(0, 6, line)

                        pdf.ln(1)



        buf = BytesIO(pdf.output())

        buf.seek(0)

        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:80] or "note"

        return send_file(buf, mimetype="application/pdf",

                         as_attachment=True,

                         download_name=f"{safe_title}.pdf")



    # ── Shared file-extract endpoint (used by drag-drop on

    #    quizzes / flashcards / essays / tutor / notes) ──────

    @app.route("/api/student/extract-file", methods=["POST"])

    @limiter.limit("20 per minute")

    def student_extract_file():

        """Accept a PDF/DOCX/TXT upload, return extracted plain text."""

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        if "file" not in request.files:

            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]

        if not f.filename:

            return jsonify({"error": "Empty filename"}), 400

        fname = f.filename

        fl = fname.lower()

        if not (fl.endswith(".pdf") or fl.endswith(".docx") or fl.endswith(".doc") or fl.endswith(".txt")):

            return jsonify({"error": "Only PDF, DOCX, and TXT files are supported"}), 400

        content = f.read(50 * 1024 * 1024 + 1)

        if len(content) > 50 * 1024 * 1024:

            return jsonify({"error": "File too large (max 50MB)"}), 400

        text = ""

        try:

            if fl.endswith(".pdf"):

                text = extract_text_from_pdf(content)

            elif fl.endswith((".docx", ".doc")):

                text = extract_text_from_docx(content)

            else:

                try:

                    text = content.decode("utf-8", errors="ignore")

                except Exception:

                    text = ""

        except Exception as e:

            return jsonify({"error": f"Could not extract text: {e}"}), 400

        if not text or len(text.strip()) < 10:

            return jsonify({"error": "Could not extract enough readable text from this file"}), 400

        text = text.replace("\x00", "")

        return jsonify({

            "text": text,

            "filename": fname,

            "title": fname.rsplit(".", 1)[0],

            "char_count": len(text),

        })



    # ── Flashcards Frontend Page ────────────────────────────



    @app.route("/student/flashcards")

    def student_flashcards_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        decks = sdb.get_flashcard_decks(_cid())



        course_options = '<option value="">Selecciona un curso...</option>'

        for c in courses:

            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'



        decks_html = ""
        total_cards = sum(int(d.get("card_count") or 0) for d in decks)
        due_total = 0
        mastered_est = 0
        _deck_classes = ["mat", "cs", "fil", "fis"]
        _deck_colors = ["#EC4899", "#3B82F6", "#10B981", "#FF7A3D"]

        for _i, d in enumerate(decks):

            due = sdb.count_due_flashcards(d["id"])
            due_total += due
            card_count = int(d.get("card_count") or 0)
            mastered = max(0, card_count - due)
            mastered_est += mastered
            pct = int((mastered / card_count) * 100) if card_count else 0
            _cls = _deck_classes[_i % len(_deck_classes)]
            _color = _deck_colors[_i % len(_deck_colors)]
            _due_cls = " urgent" if due >= 3 else ("" if due > 0 else " muted")
            _due_text = f"⚠ {due} atrasadas" if due >= 3 else (f"⏰ {due} cartas hoy" if due > 0 else "Sin pendientes")

            decks_html += f"""
            <div class="deck {_cls}" onclick="window.location='/student/flashcards/{d['id']}'">
              <div class="deck-head">
                <span class="dot" style="background:{_color};"></span>
                <span class="deck-tag">{_esc(d.get('course_name','Sin curso'))}</span>
                <button onclick="event.stopPropagation();deleteDeck({d['id']})" class="deck-delete" title="Eliminar">&#128465;</button>
              </div>
              <div class="deck-name">{_esc(d.get('title','Untitled'))}</div>
              <div class="deck-meta">{card_count} cartas · {mastered} dominadas</div>
              <div class="deck-bar"><div class="deck-fill" style="width:{pct}%;background:{_color};"></div></div>
              <div class="deck-due{_due_cls}">{_due_text}</div>
            </div>"""

        if not decks_html:

            decks_html = ""



        existing_deck_ids = [int(d["id"]) for d in decks if d.get("id") is not None]
        sample_front = "Elige un mazo para empezar a estudiar."
        sample_course = "Flashcards"
        if decks:
            sample_course = decks[0].get("course_name") or "Mazo"
            try:
                _sample_cards = sdb.get_flashcards(int(decks[0]["id"])) or []
                if _sample_cards:
                    sample_front = _sample_cards[0].get("front") or sample_front
            except Exception:
                pass

        return _s_render("Flashcards", f"""

        <div class="fc-page-head">
          <div>
            <div class="fc-eyebrow">{total_cards} cartas · {mastered_est} dominadas · {due_total} para hoy</div>
            <h1 class="fc-page-title">Flashcards</h1>
          </div>
          <div class="page-actions-cd">
            <button onclick="document.getElementById('gen-form').style.display=document.getElementById('gen-form').style.display==='none'?'block':'none'" class="btn-pop-cd accent">⚡ Crear mazo</button>
          </div>
        </div>

        <div class="fc-today">
          <div class="fct-l">
            <div class="fct-eye">REPASO ESPACIADO · HOY</div>
            <div class="fct-title">{due_total} cartas listas para repasar</div>
            <div class="fct-pills">
              <span class="fct-pill new">{max(0, total_cards - mastered_est)} nuevas</span>
              <span class="fct-pill due">{due_total} pendientes</span>
              <span class="fct-pill late">{max(0, due_total - 2)} atrasadas</span>
            </div>
          </div>
          <div class="fct-r">
            <a class="btn-pop-cd dark" href="{('/student/flashcards/' + str(decks[0]['id'])) if decks else '#'}">▶ Empezar repaso · {max(3, min(20, due_total or 8))} min</a>
          </div>
        </div>

        <div class="fc-preview-row">
          <div class="fc-card-flip">
            <div class="fc-tag"><span class="dot" style="background:#EC4899;"></span> {_esc(sample_course)} · Vista previa</div>
            <div class="fc-q">{_esc(sample_front)}</div>
            <div class="fc-foot">
              <span class="muted">Entra al mazo para girar y responder</span>
              <span class="muted">{1 if decks else 0}/{max(len(decks), 1)}</span>
            </div>
          </div>
        </div>

        <div style="display:none;">

          <div>

            <h1 style="margin:0;">&#127183; AI Flashcards</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Smart spaced repetition &middot; Generated from your course materials</p>

          </div>

          <button onclick="document.getElementById('gen-form').style.display=document.getElementById('gen-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Flashcards</button>

        </div>



        <div id="gen-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">

          <h3 style="margin:0 0 14px;">Generar tarjetas con IA</h3>



          <!-- Drag-and-drop zone -->

          <div id="fc-drop" class="dropzone" ondragover="event.preventDefault();this.classList.add('drag')" ondragleave="this.classList.remove('drag')" ondrop="fcHandleDrop(event)" onclick="document.getElementById('fc-file').click()">

            <div style="font-size:32px;">&#128206;</div>

            <div style="font-weight:600;margin-top:6px;">Drop a PDF / DOCX / TXT here</div>

            <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">or click to browse &middot; we'll generate flashcards directly from the file (no course needed)</div>

            <input type="file" id="fc-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="fcHandleFile(this.files[0])">

            <div id="fc-file-info" style="margin-top:8px;font-size:13px;color:var(--primary);"></div>

          </div>



          <div style="text-align:center;color:var(--text-muted);font-size:12px;margin:12px 0;">— or pick from your courses —</div>



          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">

            <div class="form-group">

              <label>Course</label>

              <select id="fc-course" class="edit-input" onchange="loadExams(this.value,'fc-exam')">{course_options}</select>

            </div>

            <div class="form-group">

              <label>Exam (optional)</label>

              <select id="fc-exam" class="edit-input"><option value="">Todos los temas</option></select>

            </div>

            <div class="form-group">

              <label>Cantidad de tarjetas</label>

              <input type="number" id="fc-count" value="30" min="5" max="100" class="edit-input">

            </div>

            <div class="form-group">

              <label>Custom title (optional)</label>

              <input type="text" id="fc-title" class="edit-input" placeholder="Auto-generated if empty">

            </div>

          </div>

          <button onclick="genFlashcards()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="fc-gen-btn">&#10024; Generate</button>

        </div>



        <div class="fc-decks-card">
          <div class="fc-card-h">
            <div class="fc-card-title"><span class="badge-emoji">🗃</span> Tus mazos</div>
            <button onclick="document.getElementById('gen-form').style.display='block'" class="fc-card-link" type="button">Importar →</button>
          </div>
          <div class="deck-grid">
            {decks_html}
            <div class="deck add" onclick="document.getElementById('gen-form').style.display='block'">
              <div>
                <div class="deck-add-icon">+</div>
                <div class="deck-add-l">Crear mazo nuevo</div>
                <div class="deck-add-s">Genera con IA · sube PDF</div>
              </div>
            </div>
          </div>
        </div>



        <style>

        .fc-page-head {{ display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap;margin-bottom:18px; }}
        .fc-eyebrow {{ font-size:12px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#7B61FF; }}
        .fc-page-title {{ margin:4px 0 0;font-family:Fraunces,Georgia,serif;font-size:48px;font-weight:600;letter-spacing:-.03em;color:#1A1A1F; }}
        .fc-today {{ background:linear-gradient(135deg,#1A1A1F,#2A2440);color:#F4F1EA;border-radius:22px;padding:26px;display:flex;align-items:center;gap:24px;margin-bottom:18px;box-shadow:0 18px 42px rgba(20,18,30,.18); }}
        .fct-l {{ flex:1; }}
        .fct-eye {{ font-size:11px;font-weight:900;letter-spacing:.12em;opacity:.8;text-transform:uppercase; }}
        .fct-title {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:26px;margin:6px 0 12px;letter-spacing:-.02em; }}
        .fct-pills {{ display:flex;gap:8px;flex-wrap:wrap; }}
        .fct-pill {{ background:rgba(255,255,255,.12);padding:5px 12px;border-radius:999px;font-size:12px;font-weight:800; }}
        .fct-pill.new {{ background:#7B61FF; }}.fct-pill.due {{ background:#10B981; }}.fct-pill.late {{ background:#EF4444; }}
        .btn-pop-cd {{ background:#1A1A1F;color:#FFF8E1;border:0;padding:10px 16px;border-radius:999px;font-weight:800;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center; }}
        .btn-pop-cd.accent {{ background:#FF7A3D;color:#fff; }}.btn-pop-cd.dark {{ background:#F4F1EA;color:#1A1A1F; }}
        .page-actions-cd {{ display:flex;align-items:center;gap:10px;flex-wrap:wrap; }}
        .fc-preview-row {{ display:grid;grid-template-columns:1.2fr;gap:18px;margin-bottom:18px; }}
        .fc-card-flip {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:30px;min-height:240px;box-shadow:0 18px 42px rgba(20,18,30,.08);display:flex;flex-direction:column; }}
        .fc-tag {{ font-size:11px;font-weight:800;color:#94939C;display:inline-flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.06em; }}
        .dot {{ width:9px;height:9px;border-radius:50%;display:inline-block; }}
        .fc-q {{ font-family:Fraunces,Georgia,serif;font-weight:500;font-size:28px;line-height:1.25;letter-spacing:-.02em;flex:1;display:grid;place-items:center;text-align:center;padding:16px;color:#1A1A1F; }}
        .fc-foot {{ display:flex;justify-content:space-between;font-size:11px;color:#94939C;gap:12px; }}
        .fc-decks-card {{ background:#fff;border:1px solid #E2DCCC;border-radius:22px;padding:18px;box-shadow:0 12px 34px rgba(20,18,30,.06); }}
        .fc-card-h {{ display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px; }}
        .fc-card-title {{ font-weight:900;color:#1A1A1F; }}
        .fc-card-link {{ background:transparent;border:0;color:#7B61FF;font-size:13px;font-weight:900;cursor:pointer; }}
        .deck-grid {{ display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:8px; }}
        @media (max-width:1100px) {{ .deck-grid {{ grid-template-columns:repeat(2,1fr); }} }}
        @media (max-width:700px) {{ .deck-grid {{ grid-template-columns:1fr; }} .fc-today {{ flex-direction:column;align-items:flex-start; }} .fc-page-title {{ font-size:38px; }} }}
        .deck {{ background:#FBF8F0;border:1px solid #E2DCCC;border-radius:14px;padding:14px;cursor:pointer;transition:all .15s ease; }}
        .deck:hover {{ transform:translateY(-2px);box-shadow:0 14px 28px rgba(20,18,30,.08); }}
        .deck-head {{ display:flex;align-items:center;gap:6px; }}
        .deck-tag {{ font-size:11px;font-weight:900;color:#94939C;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
        .deck-delete {{ margin-left:auto;background:transparent;border:0;color:#EF4444;cursor:pointer;font-size:13px; }}
        .deck-name {{ font-family:Fraunces,Georgia,serif;font-weight:600;font-size:16px;margin:6px 0 4px;line-height:1.15;color:#1A1A1F; }}
        .deck-meta {{ font-size:11px;color:#94939C; }}
        .deck-bar {{ height:5px;background:#fff;border-radius:999px;overflow:hidden;margin:10px 0 6px; }}
        .deck-fill {{ height:100%;border-radius:999px; }}
        .deck-due {{ font-size:11px;font-weight:800;color:#FF7A3D; }}
        .deck-due.urgent {{ color:#EF4444; }}.deck-due.muted {{ color:#94939C;font-weight:700; }}
        .deck.add {{ border:2px dashed #E2DCCC;background:transparent;display:grid;place-items:center;text-align:center;min-height:145px; }}
        .deck-add-icon {{ font-size:32px;color:#94939C; }}
        .deck-add-l {{ font-weight:900;font-size:13px;color:#1A1A1F; }}
        .deck-add-s {{ font-size:11px;color:#94939C; }}

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        .dropzone {{ border:2px dashed var(--border); border-radius:12px; padding:18px; text-align:center; cursor:pointer; transition:all .2s; background:var(--bg); }}

        .dropzone.drag {{ border-color:var(--primary); background:var(--card); }}

        </style>

        <script>

        var fcDropText = "";
        var FC_KNOWN_DECK_IDS = new Set({json.dumps(existing_deck_ids)});

        async function waitForGeneratedDeck() {{
          for (var i = 0; i < 18; i++) {{
            try {{
              await new Promise(function(resolve) {{ setTimeout(resolve, i === 0 ? 1200 : 2000); }});
              var r = await fetch('/api/student/flashcards/decks', {{ credentials: 'same-origin' }});
              var d = await _safeJson(r);
              if (!r.ok) continue;
              var decks = d.decks || [];
              for (var j = 0; j < decks.length; j++) {{
                var id = parseInt(decks[j].id, 10);
                if (id && !FC_KNOWN_DECK_IDS.has(id)) return id;
              }}
            }} catch (_) {{}}
          }}
          return null;
        }}

        async function fcHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.classList.remove('drag');

          if (e.dataTransfer.files.length) await fcHandleFile(e.dataTransfer.files[0]);

        }}

        async function fcHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('Only PDF, DOCX, and TXT files'); return; }}

          if (file.size > 50*1024*1024) {{ alert('File too large (max 50MB)'); return; }}

          var info = document.getElementById('fc-file-info');

          info.textContent = '⏳ Extracting text from ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await _safeJson(r);

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Failed'); return; }}

            fcDropText = d.text;

            info.innerHTML = '✅ ' + d.filename + ' — ' + d.char_count.toLocaleString() + ' chars ready';

            if (!document.getElementById('fc-title').value) document.getElementById('fc-title').value = 'Flashcards: ' + d.title;

          }} catch(e) {{ info.textContent = '❌ Error de red'; }}

        }}

        async function loadExams(courseId, selectId) {{

          var sel = document.getElementById(selectId);

          sel.innerHTML = '<option value="">Todos los temas</option>';

          if (!courseId) return;

          try {{

            var r = await fetch('/api/student/courses/' + courseId + '/exams');

            var d = await _safeJson(r);

            (d.exams || []).forEach(function(e) {{

              sel.innerHTML += '<option value="' + e.id + '">' + e.name + '</option>';

            }});

          }} catch(e) {{}}

        }}

        async function genFlashcards() {{

          var courseId = document.getElementById('fc-course').value;

          if (!courseId && !fcDropText) {{ alert('Drop a file or select a course'); return; }}

          var btn = document.getElementById('fc-gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating...';

          try {{

            var body = {{

              count: parseInt(document.getElementById('fc-count').value),

              title: document.getElementById('fc-title').value || undefined

            }};

            if (fcDropText) {{

              body.source_text = fcDropText;

            }} else {{

              body.course_id = parseInt(courseId);

              body.exam_id = document.getElementById('fc-exam').value ? parseInt(document.getElementById('fc-exam').value) : null;

            }}

            var r = await fetch('/api/student/flashcards/generate', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify(body)

            }});

            var d = await _safeJson(r);

            if (r.ok) {{

              alert('Generated ' + d.card_count + ' flashcards!');

              mrGo('/student/flashcards/' + d.deck_id);

            }} else {{ alert(d.error || 'Error al generar'); }}

          }} catch(e) {{
            btn.innerHTML = '&#9203; Finalizing...';
            var recoveredDeckId = await waitForGeneratedDeck();
            if (recoveredDeckId) {{
              mrGo('/student/flashcards/' + recoveredDeckId);
              return;
            }}
            mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.');
          }}

          btn.disabled = false; btn.innerHTML = '&#10024; Generate';

        }}

        async function deleteDeck(id) {{

          if (!confirm('Delete this flashcard deck?')) return;

          await fetch('/api/student/flashcards/decks/' + id, {{method:'DELETE'}});

          mrReload();

        }}

        </script>

        """, active_page="student_flashcards")



    @app.route("/student/flashcards/<int:deck_id>")

    def student_flashcard_study_page(deck_id):

        """Interactive flashcard study page with flip animation, SRS, and edit mode."""

        if not _logged_in():

            return redirect(url_for("login"))

        deck = sdb.get_flashcard_deck(deck_id, _cid())

        if not deck:

            return redirect(url_for("student_flashcards_page"))



        # Load all cards for cycling study

        cards = sdb.get_flashcards(deck_id)



        cards_json = json.dumps([{"id": c["id"], "front": c["front"], "back": c["back"],

                                  "times_seen": c.get("times_seen", 0),

                                  "times_correct": c.get("times_correct", 0)} for c in cards],

                                ensure_ascii=False)



        return _s_render(f"Study: {deck.get('title','')}", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">

          <div>

            <a href="/student/flashcards" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Decks</a>

            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(deck.get('title',''))}</h1>

            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">{_esc(deck.get('course_name',''))} &middot; <span id="card-count-txt">{len(cards)}</span> cards</p>

          </div>

          <div style="display:flex;gap:8px;">

            <button onclick="switchMode('study')" class="btn btn-primary btn-sm" id="mode-study-btn">&#127183; Study</button>

            <button onclick="switchMode('edit')" class="btn btn-outline btn-sm" id="mode-edit-btn">&#9999;&#65039; Edit Cards</button>

          </div>

        </div>



        <!-- SRS toggle -->

        <div style="display:none;"></div>



        <!-- ===== STUDY MODE ===== -->

        <div id="study-mode">

          <div id="progress-txt" style="font-size:14px;color:var(--text-muted);text-align:right;margin-bottom:8px;">1 / {len(cards)}</div>

          <div style="background:var(--bg);border-radius:8px;height:8px;margin-bottom:24px;overflow:hidden;">

            <div id="fc-progress-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:{100/max(len(cards),1):.1f}%;transition:width 0.4s ease;border-radius:8px;"></div>

          </div>



          <div style="max-width:600px;margin:0 auto;">

            <div id="fc-card" onclick="flipCard()" style="

              min-height:250px;background:var(--card);border:2px solid var(--border);border-radius:16px;

              padding:40px 32px;text-align:center;cursor:pointer;display:flex;align-items:center;

              justify-content:center;flex-direction:column;user-select:none;

              transition:transform 0.5s ease,box-shadow 0.3s ease;

              box-shadow:0 4px 20px rgba(0,0,0,0.08);position:relative;

            ">

              <div id="fc-side-label" style="position:absolute;top:16px;left:20px;font-size:11px;font-weight:700;color:var(--primary);text-transform:uppercase;letter-spacing:1px;">Question</div>

              <div id="fc-content" style="font-size:20px;line-height:1.5;color:var(--text);"></div>

              <div style="position:absolute;bottom:16px;font-size:12px;color:var(--text-muted);">Haz clic para girar</div>

            </div>



            <div style="display:flex;justify-content:center;gap:16px;margin-top:24px;" id="answer-btns">

              <button onclick="markCard(false)" class="btn" style="min-width:140px;background:#EF4444;color:#fff;border:none;font-size:15px;padding:12px 24px;">&#10007; Incorrect</button>

              <button onclick="markCard(true)" class="btn" style="min-width:140px;background:#10B981;color:#fff;border:none;font-size:15px;padding:12px 24px;">&#10003; Correct</button>

            </div>



            <p style="text-align:center;font-size:11px;color:var(--text-muted);margin-top:12px;">

              <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">Space</kbd> flip

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">1</kbd> incorrect

              &middot; <kbd style="background:var(--bg);padding:1px 5px;border-radius:3px;border:1px solid var(--border);font-size:10px;">2</kbd> correct

            </p>

            <div id="round-info" style="text-align:center;font-size:13px;color:var(--text-muted);margin-top:8px;"></div>



            <div id="fc-summary" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:30px;text-align:center;margin-top:24px;">

              <div style="font-size:48px;margin-bottom:12px;">&#127881;</div>

              <h2 style="margin:0 0 8px;">¡Sesión completada!</h2>

              <div id="fc-score" style="font-size:28px;font-weight:700;color:var(--primary);"></div>

              <div id="fc-score-detail" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>

              <button onclick="restartStudy()" class="btn btn-primary" style="margin-top:16px;">&#128260; Study Again</button>

            </div>

          </div>

        </div>



        <!-- ===== EDIT MODE ===== -->

        <div id="edit-mode" style="display:none;">

          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">

            <p style="color:var(--text-muted);font-size:14px;margin:0;">Click any card to edit. Changes save automatically.</p>

            <button onclick="addCard()" class="btn btn-primary btn-sm">&#10133; Add Card</button>

          </div>

          <div id="card-list"></div>

        </div>



        <style>

        .fc-edit-card {{ background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;margin-bottom:10px;transition:border-color 0.2s; }}

        .fc-edit-card:hover {{ border-color:var(--primary); }}

        .fc-edit-input {{ width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:14px;resize:vertical; }}

        .fc-edit-input:focus {{ border-color:var(--primary);outline:none; }}

        .fc-label {{ font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px; }}

        </style>

        <script>

        var cards = {cards_json};

        var deckId = {deck_id};

        var idx = 0, flipped = false, correct = 0, seen = 0;

        var currentMode = 'study';

        var srsEnabled = false;

        // Cycling logic: remaining = cards not yet correct this round

        var remaining = cards.map(function(c) {{ return c; }});

        var roundNum = 1;

        var totalCorrectThisRound = 0;



        function updateRoundInfo() {{

          document.getElementById('round-info').textContent = 'Round ' + roundNum + ' \u2014 ' + remaining.length + ' cards remaining';

        }}



        function switchMode(mode) {{

          currentMode = mode;

          document.getElementById('study-mode').style.display = mode === 'study' ? '' : 'none';

          document.getElementById('edit-mode').style.display = mode === 'edit' ? '' : 'none';

          document.getElementById('mode-study-btn').className = mode === 'study' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';

          document.getElementById('mode-edit-btn').className = mode === 'edit' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';

          if (mode === 'edit') renderCardList();

          if (mode === 'study') {{ idx = 0; correct = 0; seen = 0; remaining = cards.map(function(c){{return c;}}); roundNum = 1; totalCorrectThisRound = 0; document.getElementById('fc-card').style.display = 'flex'; document.getElementById('fc-summary').style.display = 'none'; renderCard(); }}

        }}



        // ── Study functions (cycling) ──

        function renderCard() {{

          if (remaining.length === 0) {{ showSummary(); return; }}

          if (idx >= remaining.length) {{

            // End of round: all remaining cards were incorrect

            idx = 0;

            roundNum++;

            totalCorrectThisRound = 0;

          }}

          var c = remaining[idx];

          document.getElementById('fc-content').innerHTML = escHtml(c.front);

          document.getElementById('fc-side-label').textContent = 'Question';

          document.getElementById('fc-side-label').style.color = 'var(--primary)';

          document.getElementById('fc-card').style.borderColor = 'var(--border)';

          document.getElementById('progress-txt').textContent = (idx + 1) + ' / ' + remaining.length;

          document.getElementById('fc-progress-bar').style.width = ((cards.length - remaining.length) / cards.length * 100) + '%';

          flipped = false;

          updateRoundInfo();

          if (typeof renderMathInElement === 'function') renderMathInElement(document.getElementById('fc-content'), {{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}],throwOnError:false}});

        }}



        function flipCard() {{

          if (remaining.length === 0) return;

          flipped = !flipped;

          var c = remaining[idx];

          document.getElementById('fc-content').innerHTML = flipped ? escHtml(c.back) : escHtml(c.front);

          document.getElementById('fc-side-label').textContent = flipped ? 'Answer' : 'Question';

          document.getElementById('fc-side-label').style.color = flipped ? '#10B981' : 'var(--primary)';

          document.getElementById('fc-card').style.borderColor = flipped ? '#10B981' : 'var(--border)';

          document.getElementById('fc-card').style.transform = 'scale(0.97)';

          setTimeout(function() {{

            document.getElementById('fc-card').style.transform = 'scale(1)';

            if (typeof renderMathInElement === 'function') renderMathInElement(document.getElementById('fc-content'), {{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}],throwOnError:false}});

          }}, 150);

        }}



        function markCard(isCorrect) {{

          if (remaining.length === 0) return;

          if (!flipped) flipCard();

          seen++;

          fetch('/api/student/flashcards/progress', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ card_id: remaining[idx].id, correct: isCorrect }})

          }}).catch(function(){{}});

          if (isCorrect) {{

            correct++;

            // Remove card from remaining — it's correct for this cycle

            remaining.splice(idx, 1);

            if (remaining.length === 0) {{

              // All cards correct! Cycle complete

              showSummary();

              return;

            }}

            // Don't increment idx since we removed the element

            if (idx >= remaining.length) idx = 0;

          }} else {{

            // Card stays, move to next

            idx++;

            if (idx >= remaining.length) {{

              // Finished this round of remaining cards, loop back

              idx = 0;

              roundNum++;

            }}

          }}

          renderCard();

        }}



        function showSummary() {{

          document.getElementById('fc-card').style.display = 'none';

          document.getElementById('fc-summary').style.display = 'block';

          document.getElementById('answer-btns').style.display = 'none';

          document.getElementById('round-info').style.display = 'none';

          document.getElementById('fc-score').textContent = cards.length + ' / ' + cards.length + ' correct!';

          document.getElementById('fc-score-detail').textContent = 'Completed in ' + roundNum + ' round' + (roundNum > 1 ? 's' : '') + ' (' + seen + ' total reviews)';

          document.getElementById('fc-progress-bar').style.width = '100%';

        }}



        function restartStudy() {{

          idx = 0; correct = 0; seen = 0;

          remaining = cards.map(function(c){{return c;}});

          roundNum = 1; totalCorrectThisRound = 0;

          document.getElementById('fc-card').style.display = 'flex';

          document.getElementById('fc-summary').style.display = 'none';

          document.getElementById('answer-btns').style.display = 'flex';

          document.getElementById('round-info').style.display = '';

          renderCard();

        }}



        // ── Edit functions ──

        function renderCardList() {{

          var html = '';

          cards.forEach(function(c, i) {{

            html += '<div class="fc-edit-card" id="ec-' + c.id + '">'

              + '<div style="display:flex;justify-content:space-between;align-items:start;gap:12px;">'

              + '<div style="flex:1;">'

              + '<div class="fc-label">Front (Question)</div>'

              + '<textarea class="fc-edit-input" rows="2" data-id="' + c.id + '" data-side="front" onblur="saveCard(' + c.id + ')">' + escHtml(c.front) + '</textarea>'

              + '</div>'

              + '<div style="flex:1;">'

              + '<div class="fc-label">Back (Answer)</div>'

              + '<textarea class="fc-edit-input" rows="2" data-id="' + c.id + '" data-side="back" onblur="saveCard(' + c.id + ')">' + escHtml(c.back) + '</textarea>'

              + '</div>'

              + '<button onclick="removeCard(' + c.id + ',' + i + ')" style="background:none;border:none;color:#EF4444;cursor:pointer;font-size:16px;padding:4px;margin-top:16px;" title="Delete card">&#128465;</button>'

              + '</div></div>';

          }});

          if (!cards.length) html = '<div style="text-align:center;padding:40px;color:var(--text-muted);"><p>No cards yet. Click "Add Card" to create one.</p></div>';

          document.getElementById('card-list').innerHTML = html;

        }}



        function escHtml(t) {{ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }}



        var saveTimers = {{}};

        async function saveCard(cardId) {{

          var frontEl = document.querySelector('[data-id="' + cardId + '"][data-side="front"]');

          var backEl = document.querySelector('[data-id="' + cardId + '"][data-side="back"]');

          if (!frontEl || !backEl) return;

          var front = frontEl.value.trim(), back = backEl.value.trim();

          if (!front || !back) return;

          // Update local

          var c = cards.find(function(x) {{ return x.id === cardId; }});

          if (c) {{ c.front = front; c.back = back; }}

          // Save to server

          try {{

            var el = document.getElementById('ec-' + cardId);

            el.style.borderColor = '#F59E0B';

            await fetch('/api/student/flashcards/' + cardId, {{

              method: 'PUT', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ deck_id: deckId, front: front, back: back }})

            }});

            el.style.borderColor = '#10B981';

            setTimeout(function() {{ el.style.borderColor = 'var(--border)'; }}, 1000);

          }} catch(e) {{ }}

        }}



        async function addCard() {{

          try {{

            var r = await fetch('/api/student/flashcards/add', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ deck_id: deckId, front: 'New question', back: 'Answer' }})

            }});

            var d = await _safeJson(r);

            if (d.ok) {{

              cards.push({{ id: d.card_id, front: 'New question', back: 'Answer', times_seen: 0, times_correct: 0 }});

              document.getElementById('card-count-txt').textContent = cards.length;

              renderCardList();

              // Scroll to new card

              var last = document.getElementById('card-list').lastElementChild;

              if (last) last.scrollIntoView({{ behavior: 'smooth' }});

              // Focus the front input

              var inputs = last.querySelectorAll('textarea');

              if (inputs.length) {{ inputs[0].focus(); inputs[0].select(); }}

            }}

          }} catch(e) {{ alert('Failed to add card'); }}

        }}



        async function removeCard(cardId, index) {{

          if (!confirm('Delete this card?')) return;

          try {{

            await fetch('/api/student/flashcards/' + cardId + '?deck_id=' + deckId, {{ method: 'DELETE' }});

            cards.splice(index, 1);

            document.getElementById('card-count-txt').textContent = cards.length;

            renderCardList();

          }} catch(e) {{ alert('Failed to delete'); }}

        }}



        document.addEventListener('keydown', function(e) {{

          if (currentMode !== 'study') return;

          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

          if (e.code === 'Space') {{ e.preventDefault(); flipCard(); }}

          if (e.code === 'Digit1' || e.code === 'Numpad1') {{ e.preventDefault(); markCard(false); }}

          if (e.code === 'Digit2' || e.code === 'Numpad2') {{ e.preventDefault(); markCard(true); }}

        }});



        renderCard();

        </script>

        """, active_page="student_flashcards")



    # ── Quiz Frontend Page ──────────────────────────────────



    @app.route("/student/quizzes")

    def student_quizzes_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        quizzes = sdb.get_quizzes(_cid())



        course_options = '<option value="">Select a course...</option>'

        for c in courses:

            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'



        quizzes_html = ""

        for q in quizzes:

            diff_color = {"easy": "#10B981", "medium": "#F59E0B", "hard": "#EF4444"}.get(q.get("difficulty", "medium"), "#94A3B8")

            score_txt = f"{q.get('best_score',0)}%" if q.get("attempts", 0) > 0 else "Sin intentar"

            quizzes_html += f"""

            <article class="qcard {'warn' if q.get('difficulty') == 'hard' else ''}" onclick="window.location='/student/quizzes/{q['id']}'">

              <div style="display:flex;justify-content:space-between;align-items:center;">

                <div>

                  <h3 style="margin:0;font-size:22px;">{_esc(q.get('title','Untitled'))}</h3>

                  <span style="font-size:13px;color:var(--text-muted);">{_esc(q.get('course_name',''))} &middot; {q.get('question_count',0)} preguntas</span>

                </div>

                <div style="display:flex;gap:10px;align-items:center;">

                  <span style="background:{diff_color}18;color:{diff_color};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">{q.get('difficulty','medium').upper()}</span>

                  <span style="font-size:13px;font-weight:600;color:var(--text);">{score_txt}</span>

                  <span style="font-size:12px;color:var(--text-muted);">{q.get('attempts',0)} intentos</span>

                  <button onclick="event.stopPropagation();window.location='/student/exam-sim/{q['id']}'" class="btn btn-ghost btn-sm" style="color:var(--primary);font-size:12px;" title="Exam Simulator">&#9889;</button>

                  <button onclick="event.stopPropagation();deleteQuiz({q['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>

                </div>

              </div>

            </article>"""

        if not quizzes_html:

            quizzes_html = """<div style="text-align:center;padding:40px;color:var(--text-muted);">

              <div style="font-size:48px;margin-bottom:12px;">&#128221;</div>

              <p>Aún no hay quizzes. Genera tu primer quiz desde un curso o archivo.</p>

            </div>"""



        existing_quiz_ids = [int(q["id"]) for q in quizzes if q.get("id") is not None]

        return _s_render("Quizzes", f"""

        <div class="quiz-cd">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">

          <div>

            <div class="page-eyebrow">87% precisión global · quizzes resueltos</div>
            <h1 style="margin:0;">Quizzes</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Elige de dónde vienen tus preguntas &mdash; una prueba oficial o tus propios apuntes.</p>

          </div>

          <button onclick="document.getElementById('qz-form').style.display=document.getElementById('qz-form').style.display==='none'?'block':'none'" class="btn-pop accent">&#10024; Generar quiz</button>

        </div>



        <div id="qz-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">

          <h3 style="margin:0 0 6px;">Generar quiz con IA</h3>

          <p style="color:var(--text-muted);font-size:13px;margin:0 0 14px;">Dos formas de crear un quiz. Elige una.</p>



          <div class="qz-mode-row">

            <button type="button" class="qz-mode active" data-mode="test" onclick="qzSetMode('test')">

              <div class="qz-mode-ic">&#128221;</div>

              <div class="qz-mode-t">Prueba oficial</div>

              <div class="qz-mode-s">Sube una prueba pasada (PDF / DOCX). La transcribimos literal. Funciona mejor con selección múltiple.</div>

            </button>

            <button type="button" class="qz-mode" data-mode="notes" onclick="qzSetMode('notes')">

              <div class="qz-mode-ic">&#128214;</div>

              <div class="qz-mode-t">Apuntes</div>

              <div class="qz-mode-s">Sube tus apuntes (PDF / DOCX / TXT) y generamos preguntas desde ese material.</div>

            </button>

          </div>



          <div id="qz-drop" class="dropzone" ondragover="event.preventDefault();this.classList.add('drag')" ondragleave="this.classList.remove('drag')" ondrop="qzHandleDrop(event)" onclick="document.getElementById('qz-file').click()">

            <div style="font-size:32px;">&#128206;</div>

            <div style="font-weight:600;margin-top:6px;" id="qz-drop-t">Suelta un PDF / DOCX / TXT aquí</div>

            <div style="font-size:12px;color:var(--text-muted);margin-top:2px;" id="qz-drop-s">o haz clic para buscar</div>

            <input type="file" id="qz-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="qzHandleFile(this.files[0])">

            <div id="qz-file-info" style="margin-top:8px;font-size:13px;color:var(--primary);"></div>

          </div>



          <div style="text-align:center;color:var(--text-muted);font-size:12px;margin:12px 0;">&mdash; o elige un curso sincronizado desde Canvas &mdash;</div>



          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">

            <div class="form-group">

              <label>Curso</label>

              <select id="qz-course" class="edit-input" onchange="loadExams(this.value,'qz-exam')">{course_options}</select>

            </div>

            <div class="form-group">

              <label>Evaluación (opcional)</label>

              <select id="qz-exam" class="edit-input"><option value="">Todos los temas</option></select>

            </div>

            <div class="form-group" style="grid-column:1 / -1;">

              <label>Cantidad de preguntas</label>

              <input type="number" id="qz-count" value="10" min="5" max="100" class="edit-input">

              <small style="display:block;color:var(--text-muted);font-size:11px;margin-top:4px;">Hasta 100. Los quizzes grandes se generan por partes; dale unos segundos.</small>

            </div>

          </div>

          <button onclick="genQuiz()" class="btn btn-primary btn-sm" style="margin-top:12px;" id="qz-gen-btn">&#10024; Generar</button>

        </div>



        <div class="quiz-grid">
        {quizzes_html}
        </div>
        </div>



        <style>

        .quiz-cd {{ --ink:#1A1A1F; --paper:#F4F1EA; --card:#FFFFFF; --line:#E2DCCC; --muted:#77756F; --accent:#FF7A3D; --pink:#EF5DA8; font-family:"Plus Jakarta Sans",system-ui,sans-serif; color:var(--ink); }}
        .quiz-cd > div:first-child {{ margin-bottom: 18px !important; }}
        .quiz-cd .page-eyebrow {{ font-size:12px!important; font-weight:800!important; letter-spacing:.16em!important; text-transform:uppercase!important; color:#009B72!important; margin-bottom:6px!important; }}
        .quiz-cd h1 {{ font-family:"Fraunces",Georgia,serif!important; font-size:clamp(44px,6vw,76px)!important; line-height:.9!important; margin:0!important; font-weight:600!important; letter-spacing:-.055em!important; }}
        .quiz-cd h1::before {{ content:""; }}
        .quiz-cd > div:first-child p {{ color:#8A98AD!important; font-weight:700; }}
        .quiz-cd .btn-pop {{ background:#1A1A1F!important; color:#FFF8E1!important; border:0!important; border-radius:999px!important; padding:12px 18px!important; font-weight:900!important; box-shadow:0 4px 0 rgba(0,0,0,.18),0 16px 34px rgba(20,18,30,.12)!important; cursor:pointer; text-decoration:none; }}
        .quiz-cd .btn-pop.accent {{ background:#1A1A1F!important; color:#FFF8E1!important; }}
        .quiz-hero {{ background:linear-gradient(135deg,#FF7A3D,#EF5DA8)!important; color:#fff!important; border-radius:22px!important; padding:26px!important; display:flex!important; align-items:center!important; justify-content:space-between!important; gap:24px!important; margin-bottom:18px!important; box-shadow:0 10px 30px rgba(255,122,61,.20)!important; }}
        .qh-eye {{ font-size:12px!important; font-weight:900!important; letter-spacing:.14em!important; text-transform:uppercase!important; opacity:.85!important; }}
        .qh-title {{ font-family:"Fraunces",Georgia,serif!important; font-size:34px!important; line-height:1!important; margin:6px 0!important; font-weight:600!important; letter-spacing:-.04em!important; color:#fff!important; }}
        .qh-sub {{ margin:0!important; font-size:14px!important; opacity:.9!important; color:#fff!important; }}
        .quiz-grid {{ display:grid!important; grid-template-columns:repeat(3,1fr)!important; gap:14px!important; }}
        .qcard {{ background:#fff!important; border:1px solid #E2DCCC!important; border-radius:16px!important; padding:18px!important; position:relative!important; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04)!important; display:flex!important; flex-direction:column!important; gap:10px!important; cursor:pointer!important; transition:transform .16s,box-shadow .16s!important; }}
        .qcard:hover {{ transform:translateY(-2px)!important; box-shadow:0 6px 0 rgba(20,18,30,.05),0 18px 44px rgba(20,18,30,.08)!important; }}
        .qcard.warn {{ border-color:#FF7A3D!important; background:linear-gradient(180deg,#FFF0E8 0%,#fff 52%)!important; }}
        @media(max-width:1100px) {{ .quiz-grid {{ grid-template-columns:repeat(2,1fr)!important; }} }}
        @media(max-width:700px) {{ .quiz-grid {{ grid-template-columns:1fr!important; }} .quiz-hero {{ align-items:flex-start!important; flex-direction:column!important; }} }}

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        .dropzone {{ border:2px dashed var(--border); border-radius:12px; padding:18px; text-align:center; cursor:pointer; transition:all .2s; background:var(--bg); }}

        .dropzone.drag {{ border-color:var(--primary); background:var(--card); }}

        .qz-mode-row {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:14px; }}

        .qz-mode {{ text-align:left; padding:14px 16px; border:1px solid var(--border); border-radius:12px; background:var(--bg); color:var(--text); cursor:pointer; transition:all .15s; }}

        .qz-mode:hover {{ border-color:var(--primary); }}

        .qz-mode.active {{ border-color:var(--primary); background:var(--card); box-shadow:0 0 0 2px rgba(99,102,241,.15); }}

        .qz-mode-ic {{ font-size:20px; }}

        .qz-mode-t {{ font-weight:700; margin-top:4px; }}

        .qz-mode-s {{ font-size:12px; color:var(--text-muted); margin-top:4px; line-height:1.4; }}

        @media (max-width: 640px) {{ .qz-mode-row {{ grid-template-columns:1fr; }} }}

        </style>

        <script>

        var qzDropText = "";
        var QZ_KNOWN_QUIZ_IDS = new Set({json.dumps(existing_quiz_ids)});

        async function waitForGeneratedQuiz() {{
          for (var i = 0; i < 18; i++) {{
            try {{
              await new Promise(function(resolve) {{ setTimeout(resolve, i === 0 ? 1200 : 2000); }});
              var r = await fetch('/api/student/quizzes', {{ credentials: 'same-origin' }});
              var d = await _safeJson(r);
              if (!r.ok) continue;
              var quizzes = d.quizzes || [];
              for (var j = 0; j < quizzes.length; j++) {{
                var id = parseInt(quizzes[j].id, 10);
                if (id && !QZ_KNOWN_QUIZ_IDS.has(id)) return id;
              }}
            }} catch (_) {{}}
          }}
          return null;
        }}

        async function pollQuizGeneration() {{
          for (var i = 0; i < 180; i++) {{
            await new Promise(function(resolve) {{ setTimeout(resolve, 2000); }});
            try {{
              var sr = await fetch('/api/student/quizzes/generate/status', {{ credentials: 'same-origin' }});
              var sd = await _safeJson(sr);
              if (sr.ok && sd.status === 'done' && sd.quiz_id) return sd;
              if (sr.ok && sd.status === 'error') {{
                var genErr = new Error(sd.error || sd.progress || 'Quiz generation failed.');
                genErr.isGenerationError = true;
                throw genErr;
              }}
            }} catch (statusErr) {{
              if (statusErr && statusErr.isGenerationError) throw statusErr;
            }}
            var qr = await fetch('/api/student/quizzes', {{ credentials: 'same-origin' }});
            var qd = await _safeJson(qr);
            if (qr.ok) {{
              var quizzes = qd.quizzes || [];
              for (var j = 0; j < quizzes.length; j++) {{
                var id = parseInt(quizzes[j].id, 10);
                if (id && !QZ_KNOWN_QUIZ_IDS.has(id)) {{
                  return {{ quiz_id: id, question_count: quizzes[j].question_count || 0, requested: quizzes[j].question_count || 0, short: false }};
                }}
              }}
            }}
          }}
          throw new Error('El quiz sigue generándose. Refresca esta página en un momento.');
        }}

        var qzMode = 'test';

        function qzSetMode(m) {{

          qzMode = m;

          var nodes = document.querySelectorAll('.qz-mode');

          for (var i = 0; i < nodes.length; i++) {{

            if (nodes[i].getAttribute('data-mode') === m) nodes[i].classList.add('active');

            else nodes[i].classList.remove('active');

          }}

          var t = document.getElementById('qz-drop-t');

          var s = document.getElementById('qz-drop-s');

          if (m === 'test') {{

            t.innerHTML = 'Suelta una prueba oficial (PDF / DOCX)';

            s.innerHTML = 'Recuerda: las pruebas de selección múltiple se transcriben mejor.';

            document.getElementById('qz-file').accept = '.pdf,.docx,.doc';

          }} else {{

            t.innerHTML = 'Suelta tus apuntes (PDF / DOCX / TXT)';

            s.innerHTML = "Generaremos preguntas desde ese material.";

            document.getElementById('qz-file').accept = '.pdf,.docx,.doc,.txt';

          }}

        }}

        async function qzHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.classList.remove('drag');

          if (e.dataTransfer.files.length) await qzHandleFile(e.dataTransfer.files[0]);

        }}

        async function qzHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('Solo archivos PDF, DOCX y TXT'); return; }}

          if (file.size > 50*1024*1024) {{ alert('Archivo demasiado grande (máx. 50MB)'); return; }}

          var info = document.getElementById('qz-file-info');

          info.textContent = '⏳ Extrayendo texto de ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await _safeJson(r);

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Falló'); return; }}

            qzDropText = d.text;

            qzDropTitle = d.title;

            info.innerHTML = '✅ ' + d.filename + ' — ' + d.char_count.toLocaleString() + ' caracteres listos';

          }} catch(e) {{ info.textContent = '❌ Error de red'; }}

        }}

        var qzDropTitle = '';

        async function loadExams(courseId, selectId) {{

          var sel = document.getElementById(selectId);

          sel.innerHTML = '<option value="">Todos los temas</option>';

          if (!courseId) return;

          try {{

            var r = await fetch('/api/student/courses/' + courseId + '/exams');

            var d = await _safeJson(r);

            (d.exams || []).forEach(function(e) {{

              sel.innerHTML += '<option value="' + e.id + '">' + e.name + '</option>';

            }});

          }} catch(e) {{}}

        }}

        async function genQuiz() {{

          var courseId = document.getElementById('qz-course').value;

          if (!courseId && !qzDropText) {{ alert('Suelta un archivo o selecciona un curso'); return; }}

          var btn = document.getElementById('qz-gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generando...';

          try {{

            var body = {{

              count: parseInt(document.getElementById('qz-count').value)

            }};

            if (qzDropText) {{

              body.source_text = qzDropText;

              body.title = 'Quiz: ' + qzDropTitle;

            }} else {{

              body.course_id = parseInt(courseId);

              body.exam_id = document.getElementById('qz-exam').value ? parseInt(document.getElementById('qz-exam').value) : null;

            }}

            var r = await fetch('/api/student/quizzes/generate-async', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify(body)

            }});

            var d = await _safeJson(r);

            if (r.ok && d.queued) {{

              btn.innerHTML = '&#9203; Creando quiz...';

              d = await pollQuizGeneration();

              var msg = 'Se generaron ' + d.question_count + ' preguntas.';

              if (d.short) {{ msg += '\\n(Pediste ' + d.requested + ', pero el material solo alcanzó para ' + d.question_count + ' preguntas únicas.)'; }}

              alert(msg);

              mrGo('/student/quizzes/' + d.quiz_id);

              return;

            }} else if (r.ok) {{

              var msg = 'Se generaron ' + d.question_count + ' preguntas.';

              if (d.short) {{ msg += '\\n(Pediste ' + d.requested + ', pero el material solo alcanzó para ' + d.question_count + ' preguntas únicas.)'; }}

              alert(msg);

              mrGo('/student/quizzes/' + d.quiz_id);

            }} else {{ alert(d.error || 'Error al generar'); }}

          }} catch(e) {{
            btn.innerHTML = '&#9203; Finalizando...';
            var recoveredQuizId = await waitForGeneratedQuiz();
            if (recoveredQuizId) {{
              mrGo('/student/quizzes/' + recoveredQuizId);
              return;
            }}
            var msg = e && e.message ? e.message : 'No se pudo generar el quiz.';
            if (typeof showToast === 'function') showToast('No se pudo generar el quiz: ' + msg, 'error');
            else alert('No se pudo generar el quiz: ' + msg);
          }}

          btn.disabled = false; btn.innerHTML = '&#10024; Generar';

        }}

        async function deleteQuiz(id) {{

          if (!confirm('¿Eliminar este quiz?')) return;

          await fetch('/api/student/quizzes/' + id, {{method:'DELETE'}});

          mrReload();

        }}

        </script>

        """, active_page="student_quizzes")



    @app.route("/student/quizzes/<int:quiz_id>")

    def student_quiz_take_page(quiz_id):

        """Interactive quiz-taking page."""

        if not _logged_in():

            return redirect(url_for("login"))

        quiz = sdb.get_quiz(quiz_id, _cid())

        if not quiz:

            return redirect(url_for("student_quizzes_page"))

        questions = sdb.get_quiz_questions(quiz_id)

        from student import subscription as _sub
        _is_plus = _sub.has_unlimited_ai(_cid())

        questions_json = json.dumps([{

            "id": q["id"], "question": q["question"],

            "option_a": q.get("option_a", ""), "option_b": q.get("option_b", ""),

            "option_c": q.get("option_c", ""), "option_d": q.get("option_d", ""),

            "correct": q["correct"],

            # Wrong-answer explanations are PLUS-gated. Strip them for free
            # users so they cannot inspect via DevTools. Frontend shows
            # an upsell box in their place.
            "explanation": (q.get("explanation", "") if _is_plus else ""),

            "topic": q.get("topic", "") or ""

        } for q in questions], ensure_ascii=False)

        _is_plus_js = "true" if _is_plus else "false"



        diff_color = {"easy": "#10B981", "medium": "#F59E0B", "hard": "#EF4444"}.get(quiz.get("difficulty", "medium"), "#94A3B8")



        return _s_render(f"Quiz: {quiz.get('title','')}", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;gap:12px;flex-wrap:wrap;">

          <div>

            <a href="/student/quizzes" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Quizzes</a>

            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(quiz.get('title',''))}</h1>

            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">

              {_esc(quiz.get('course_name',''))} &middot;

              <span style="color:{diff_color};font-weight:600;">{quiz.get('difficulty','').upper()}</span> &middot;

              {quiz.get('question_count',0)} questions

            </p>

          </div>

          <div style="display:flex;align-items:center;gap:14px;">

            <div id="qz-timer-wrap" style="display:none;text-align:right;">

              <div id="qz-timer" style="font-size:22px;font-weight:700;font-family:monospace;letter-spacing:1px;color:var(--text);">00:00</div>

              <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;text-transform:uppercase;">Remaining</div>

            </div>

            <div id="qz-progress-txt" style="font-size:14px;color:var(--text-muted);">Question 1 of {len(questions)}</div>

          </div>

        </div>



        <!-- Progress bar -->

        <div style="background:var(--bg);border-radius:8px;height:8px;margin-bottom:24px;overflow:hidden;">

          <div id="qz-bar" style="height:100%;background:linear-gradient(90deg,var(--primary),#8B5CF6);width:0%;transition:width 0.4s ease;border-radius:8px;"></div>

        </div>



        <div style="max-width:700px;margin:0 auto;">

          <!-- Pre-start setup -->

          <div id="qz-setup" class="card" style="padding:28px;">

            <h2 style="margin:0 0 6px;font-size:20px;">&#9889; Ready to start?</h2>

            <p style="color:var(--text-muted);font-size:13px;margin:0 0 18px;">{len(questions)} questions coming up. You can add a timer to simulate exam pressure.</p>



            <div style="border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:14px;background:var(--bg);">

              <label style="display:flex;align-items:center;gap:10px;cursor:pointer;font-weight:600;">

                <input type="checkbox" id="qz-timer-toggle" style="width:18px;height:18px;cursor:pointer;">

                <span>&#9201;&#65039; Enable timer</span>

              </label>

              <div id="qz-timer-config" style="display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--border);">

                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px;">

                  <div>

                    <label style="font-size:12px;color:var(--text-muted);font-weight:600;">Mode</label>

                    <select id="qz-timer-mode" class="edit-input">

                      <option value="total">Tiempo total para el quiz completo</option>

                      <option value="per">Tiempo por pregunta</option>

                    </select>

                  </div>

                  <div>

                    <label style="font-size:12px;color:var(--text-muted);font-weight:600;">

                      <span id="qz-timer-unit-label">Minutos totales</span>

                    </label>

                    <input type="number" id="qz-timer-minutes" value="{max(5, len(questions) * 1)}" min="1" max="300" class="edit-input">

                  </div>

                </div>

                <div style="display:flex;gap:6px;flex-wrap:wrap;">

                  <button type="button" onclick="setQuizPreset(60,'per')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#128165; 60s / question</button>

                  <button type="button" onclick="setQuizPreset(90,'per')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#9203; 90s / question</button>

                  <button type="button" onclick="setQuizPreset(120,'per')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#128336; 2m / question</button>

                  <button type="button" onclick="setQuizPreset({max(5,len(questions)*2)},'total')" class="btn btn-ghost btn-sm" style="font-size:11px;">&#128221; Realistic exam</button>

                </div>

                <p style="font-size:11px;color:var(--text-muted);margin:10px 0 0;">When the timer runs out, the quiz auto-finishes with whatever's answered.</p>

              </div>

            </div>



            <button onclick="beginQuiz()" class="btn btn-primary" style="padding:10px 26px;font-size:15px;">&#9654; Start Quiz</button>

          </div>



          <!-- Question card -->

          <div id="qz-card" class="card" style="padding:30px;display:none;">

            <div id="qz-question" style="font-size:18px;font-weight:600;margin-bottom:20px;line-height:1.5;"></div>

            <div id="qz-options"></div>

            <div id="qz-explanation" style="display:none;margin-top:16px;padding:14px;border-radius:var(--radius-sm);font-size:14px;line-height:1.5;"></div>

            <div style="display:flex;justify-content:flex-end;margin-top:20px;">

              <button id="qz-next-btn" onclick="nextQuestion()" class="btn btn-primary" style="display:none;">Next &rarr;</button>

            </div>

          </div>



          <!-- Summary (hidden until done) -->

          <div id="qz-summary" style="display:none;">

            <!-- Hero -->

            <div class="card" style="position:relative;overflow:hidden;text-align:center;padding:32px 24px;margin-bottom:18px;">

              <div aria-hidden="true" style="position:absolute;inset:0;background:radial-gradient(800px 200px at 50% -20%,rgba(139,92,246,.18),transparent 70%);pointer-events:none;"></div>

              <div style="position:relative;z-index:1;">

                <div style="font-size:56px;margin-bottom:8px;" id="qz-emoji">&#127881;</div>

                <div id="qz-verdict" style="display:inline-block;font-size:11px;letter-spacing:2px;text-transform:uppercase;font-weight:700;padding:4px 12px;border-radius:999px;background:rgba(99,102,241,.15);color:#A78BFA;margin-bottom:10px;">Analyzing...</div>

                <h2 style="margin:0 0 4px;font-size:18px;font-weight:600;color:var(--text-muted);">Quiz completado</h2>

                <div id="qz-final-score" style="font-size:64px;font-weight:800;line-height:1;background:linear-gradient(135deg,#6366F1,#8B5CF6,#EC4899);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;"></div>

                <div id="qz-final-detail" style="font-size:14px;color:var(--text-muted);margin-top:6px;"></div>

                <div id="qz-headline" style="font-size:15px;margin-top:14px;max-width:560px;margin-left:auto;margin-right:auto;line-height:1.5;"></div>

              </div>

            </div>



            <!-- Pacing stats -->

            <div class="card" style="padding:18px;margin-bottom:18px;">

              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;">

                <div style="text-align:center;"><div id="qz-pace-total" style="font-size:20px;font-weight:700;">0:00</div><div style="font-size:11px;color:var(--text-muted);">Tiempo total</div></div>

                <div style="text-align:center;"><div id="qz-pace-avg" style="font-size:20px;font-weight:700;">0s</div><div style="font-size:11px;color:var(--text-muted);">Avg / question</div></div>

                <div style="text-align:center;"><div id="qz-pace-fast" style="font-size:20px;font-weight:700;color:#10B981;">0s</div><div style="font-size:11px;color:var(--text-muted);">Fastest</div></div>

                <div style="text-align:center;"><div id="qz-pace-slow" style="font-size:20px;font-weight:700;color:#EF4444;">0s</div><div style="font-size:11px;color:var(--text-muted);">Slowest</div></div>

              </div>

              <div id="qz-pace-insight" style="margin-top:12px;padding:10px 14px;background:var(--bg);border-radius:8px;font-size:13px;color:var(--text-muted);display:none;"></div>

            </div>



            <!-- Topic breakdown -->

            <div class="card" style="padding:20px;margin-bottom:18px;">

              <h3 style="margin:0 0 14px;font-size:16px;">&#128202; Topic breakdown</h3>

              <div id="qz-topics"></div>

            </div>



            <!-- AI analysis grid -->

            <div id="qz-ai-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;">

              <div class="card" style="padding:18px;">

                <h3 style="margin:0 0 10px;font-size:15px;color:#10B981;">&#9989; Strengths</h3>

                <div id="qz-strengths" style="font-size:13px;color:var(--text-muted);">Analyzing...</div>

              </div>

              <div class="card" style="padding:18px;">

                <h3 style="margin:0 0 10px;font-size:15px;color:#EF4444;">&#10060; Needs work</h3>

                <div id="qz-weaknesses" style="font-size:13px;color:var(--text-muted);">Analyzing...</div>

              </div>

            </div>



            <!-- Mistake patterns -->

            <div class="card" id="qz-patterns-card" style="padding:18px;margin-bottom:18px;display:none;">

              <h3 style="margin:0 0 10px;font-size:15px;">&#128269; Mistake patterns</h3>

              <div id="qz-patterns" style="font-size:13px;"></div>

            </div>



            <!-- Action plan -->

            <div class="card" style="padding:20px;margin-bottom:18px;border-left:4px solid #8B5CF6;">

              <h3 style="margin:0 0 10px;font-size:16px;">&#127919; Do this next</h3>

              <div id="qz-actions" style="font-size:14px;line-height:1.7;">Analyzing...</div>

            </div>



            <!-- 30-min study plan -->

            <div class="card" id="qz-plan-card" style="padding:20px;margin-bottom:18px;display:none;">

              <h3 style="margin:0 0 10px;font-size:16px;">&#9201; 30-minute follow-up plan</h3>

              <div id="qz-plan"></div>

            </div>



            <!-- Per-question review (collapsible) -->

            <div class="card" style="padding:20px;margin-bottom:18px;">

              <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;" onclick="toggleReview()">

                <h3 style="margin:0;font-size:16px;">&#128214; Question-by-question review</h3>

                <span id="qz-review-toggle" style="font-size:12px;color:var(--text-muted);">Show &#9660;</span>

              </div>

              <div id="qz-review-body" style="display:none;margin-top:14px;"></div>

            </div>



            <!-- Actions row -->

            <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-bottom:40px;">

              <button onclick="restartQuiz()" class="btn btn-primary">&#128260; Retake quiz</button>

              <button id="qz-retake-wrong" onclick="retakeWrong()" class="btn btn-outline" style="display:none;">&#9998; Retake wrong only</button>

              <a href="/student/quizzes" class="btn btn-outline">Volver a quizzes</a>

            </div>



            <div id="qz-encouragement" style="text-align:center;font-size:13px;color:var(--text-muted);font-style:italic;margin-bottom:20px;"></div>

          </div>

        </div>



        <style>

        .qz-option {{

          display:block;width:100%;text-align:left;padding:14px 18px;margin-bottom:10px;

          background:var(--bg);border:2px solid var(--border);border-radius:12px;

          cursor:pointer;font-size:15px;color:var(--text);transition:all 0.2s ease;

        }}

        .qz-option:hover {{ border-color:var(--primary);background:var(--card); }}

        .qz-option.selected {{ border-color:var(--primary);background:var(--primary-light,#EDE9FE); }}

        .qz-option.correct {{ border-color:#10B981;background:#D1FAE5;color:#065F46; }}

        .qz-option.wrong {{ border-color:#EF4444;background:#FEE2E2;color:#991B1B; }}

        .qz-option.disabled {{ pointer-events:none;opacity:0.7; }}

        .qz-topic-row {{ display:flex;align-items:center;gap:10px;margin-bottom:10px;font-size:13px; }}

        .qz-topic-name {{ flex:0 0 32%;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }}

        .qz-topic-bar {{ flex:1;background:var(--bg);border-radius:6px;height:10px;overflow:hidden;border:1px solid var(--border); }}

        .qz-topic-fill {{ height:100%;border-radius:6px;transition:width .6s ease; }}

        .qz-topic-pct {{ flex:0 0 72px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums; }}

        .qz-review-item {{ padding:14px;margin-bottom:10px;border-radius:10px;border-left:4px solid;background:var(--bg); }}

        .qz-review-item.correct {{ border-color:#10B981; }}

        .qz-review-item.wrong {{ border-color:#EF4444; }}

        .qz-ai-item {{ padding:10px 0;border-bottom:1px solid var(--border);font-size:13px;line-height:1.55; }}

        .qz-ai-item:last-child {{ border-bottom:none; }}

        .qz-ai-topic {{ font-weight:700;color:var(--text);display:block;margin-bottom:2px; }}

        .qz-ai-fix {{ display:block;margin-top:4px;color:#A78BFA;font-size:12px; }}

        @media (max-width: 700px) {{ #qz-ai-grid {{ grid-template-columns:1fr; }} }}

        </style>



        <script>

        var questions = {questions_json};

        var qIdx = 0, score = 0, answered = false;

        var answerLog = [];        // [{{q_id, selected, time, is_correct}}]

        var questionStart = 0;

        var quizStarted = Date.now();



        /* ───── Optional timer ───── */

        var qzTimerEnabled = false;

        var qzTimerInterval = null;

        var qzTimerRemaining = 0;

        var qzTimerTotal = 0;

        var qzTimerPerQuestion = false;



        (function bindSetup() {{

          var toggle = document.getElementById('qz-timer-toggle');

          var cfg = document.getElementById('qz-timer-config');

          var modeSel = document.getElementById('qz-timer-mode');

          var minsInput = document.getElementById('qz-timer-minutes');

          var unitLabel = document.getElementById('qz-timer-unit-label');

          toggle.addEventListener('change', function() {{

            cfg.style.display = toggle.checked ? 'block' : 'none';

          }});

          modeSel.addEventListener('change', function() {{

            if (modeSel.value === 'per') {{

              unitLabel.textContent = 'Seconds per question';

              minsInput.value = 90; minsInput.min = 10; minsInput.max = 600;

            }} else {{

              unitLabel.textContent = 'Minutes total';

              minsInput.value = Math.max(5, questions.length * 1);

              minsInput.min = 1; minsInput.max = 300;

            }}

          }});

        }})();



        window.setQuizPreset = function(val, mode) {{

          document.getElementById('qz-timer-toggle').checked = true;

          document.getElementById('qz-timer-config').style.display = 'block';

          document.getElementById('qz-timer-mode').value = mode;

          var label = document.getElementById('qz-timer-unit-label');

          var input = document.getElementById('qz-timer-minutes');

          if (mode === 'per') {{

            label.textContent = 'Seconds per question';

            input.min = 10; input.max = 600; input.value = val;

          }} else {{

            label.textContent = 'Minutes total';

            input.min = 1; input.max = 300; input.value = val;

          }}

        }};



        function formatMMSS(totalSec) {{

          totalSec = Math.max(0, Math.floor(totalSec));

          var m = Math.floor(totalSec / 60);

          var s = totalSec % 60;

          return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;

        }}



        function tickQuizTimer() {{

          qzTimerRemaining--;

          var el = document.getElementById('qz-timer');

          if (!el) return;

          el.textContent = formatMMSS(qzTimerRemaining);

          // Color shift

          var frac = qzTimerRemaining / (qzTimerTotal || 1);

          if (qzTimerRemaining <= 10) el.style.color = '#EF4444';

          else if (frac <= 0.25) el.style.color = '#F59E0B';

          else el.style.color = 'var(--text)';

          if (qzTimerRemaining <= 0) {{

            clearInterval(qzTimerInterval);

            qzTimerInterval = null;

            // Auto-finish: log a blank for current question if not answered yet

            if (!answered && qIdx < questions.length) {{

              answerLog.push({{ q_id: questions[qIdx].id, selected: '', time: (Date.now() - questionStart) / 1000, is_correct: false }});

            }}

            showResults();

          }}

        }}



        function beginQuiz() {{

          qzTimerEnabled = document.getElementById('qz-timer-toggle').checked;

          if (qzTimerEnabled) {{

            var mode = document.getElementById('qz-timer-mode').value;

            var val = parseInt(document.getElementById('qz-timer-minutes').value, 10) || 0;

            if (mode === 'per') {{

              qzTimerPerQuestion = true;

              qzTimerTotal = Math.max(10, val) * questions.length;

            }} else {{

              qzTimerPerQuestion = false;

              qzTimerTotal = Math.max(1, val) * 60;

            }}

            qzTimerRemaining = qzTimerTotal;

            document.getElementById('qz-timer-wrap').style.display = 'block';

            document.getElementById('qz-timer').textContent = formatMMSS(qzTimerRemaining);

            qzTimerInterval = setInterval(tickQuizTimer, 1000);

          }}

          document.getElementById('qz-setup').style.display = 'none';

          document.getElementById('qz-card').style.display = '';

          quizStarted = Date.now();

          renderQuestion();

        }}



        function renderQuestion() {{

          if (qIdx >= questions.length) {{ showResults(); return; }}

          var q = questions[qIdx];

          answered = false;

          document.getElementById('qz-question').textContent = 'Q' + (qIdx + 1) + '. ' + q.question;

          document.getElementById('qz-progress-txt').textContent = 'Question ' + (qIdx + 1) + ' of ' + questions.length;

          document.getElementById('qz-bar').style.width = (qIdx / questions.length * 100) + '%';

          document.getElementById('qz-explanation').style.display = 'none';

          document.getElementById('qz-next-btn').style.display = 'none';

          var opts = document.getElementById('qz-options');

          opts.innerHTML = '';

          ['a','b','c','d'].forEach(function(key) {{

            var btn = document.createElement('button');

            btn.className = 'qz-option';

            btn.textContent = key.toUpperCase() + '. ' + q['option_' + key];

            btn.dataset.key = key;

            btn.onclick = function() {{ selectAnswer(key); }};

            opts.appendChild(btn);

          }});

          questionStart = Date.now();

        }}



        function selectAnswer(key) {{

          if (answered) return;

          answered = true;

          var q = questions[qIdx];

          var isCorrect = key === q.correct;

          var timeSpent = (Date.now() - questionStart) / 1000;

          if (isCorrect) score++;

          answerLog.push({{ q_id: q.id, selected: key, time: timeSpent, is_correct: isCorrect }});



          document.querySelectorAll('.qz-option').forEach(function(btn) {{

            btn.classList.add('disabled');

            if (btn.dataset.key === q.correct) btn.classList.add('correct');

            if (btn.dataset.key === key && !isCorrect) btn.classList.add('wrong');

          }});



          var exp = document.getElementById('qz-explanation');

          exp.style.display = 'block';

          var IS_PLUS = {_is_plus_js};

          if (!isCorrect && !IS_PLUS) {{

            // Free user got it wrong → show PLUS upsell instead of the explanation.
            exp.style.background = 'linear-gradient(135deg, rgba(245,158,11,.14), rgba(236,72,153,.10))';
            exp.style.color = '#FBBF24';
            exp.style.border = '1px solid rgba(245,158,11,.35)';
            exp.innerHTML = '&#128274; <b>Ver por qué fallaste</b> &mdash; upgrade to <b>PLUS</b> to unlock per-question explanations on every quiz. <a href="/student/shop" style="color:#FBBF24;text-decoration:underline;font-weight:600;">Upgrade &rarr;</a>';

          }} else {{

            exp.style.background = isCorrect ? '#D1FAE5' : '#FEE2E2';
            exp.style.color = isCorrect ? '#065F46' : '#991B1B';
            exp.style.border = '';
            exp.innerHTML = (isCorrect ? '&#10003; Correct! ' : '&#10007; Incorrect. ') + (q.explanation || '');

          }}



          document.getElementById('qz-next-btn').style.display = '';

          document.getElementById('qz-next-btn').textContent = qIdx === questions.length - 1 ? 'See Results' : 'Next \\u2192';

        }}



        function nextQuestion() {{ qIdx++; renderQuestion(); }}



        function fmtTime(s) {{

          s = Math.round(s);

          if (s < 60) return s + 's';

          return Math.floor(s/60) + 'm ' + (s%60) + 's';

        }}

        function escH(t) {{ var d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }}



        function showResults() {{

          if (qzTimerInterval) {{ clearInterval(qzTimerInterval); qzTimerInterval = null; }}

          document.getElementById('qz-timer-wrap').style.display = 'none';

          document.getElementById('qz-card').style.display = 'none';

          document.getElementById('qz-summary').style.display = 'block';

          var pct = Math.round(score / questions.length * 100);

          document.getElementById('qz-final-score').textContent = pct + '%';

          document.getElementById('qz-final-detail').textContent = score + ' of ' + questions.length + ' correct';

          document.getElementById('qz-bar').style.width = '100%';

          document.getElementById('qz-emoji').innerHTML = pct >= 90 ? '&#127942;' : pct >= 70 ? '&#127881;' : pct >= 50 ? '&#128170;' : '&#128218;';

          if (pct >= 80 && window.confettiBurst) {{ window.confettiBurst(pct >= 95 ? 80 : 50); }}



          fetch('/api/student/quizzes/{quiz_id}/score', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ score: pct }})

          }}).catch(function(){{}});



          // Per-question review (always available immediately)

          renderReview();

          // Show retake-wrong if any wrong

          if (answerLog.some(function(a){{ return !a.is_correct; }})) {{

            document.getElementById('qz-retake-wrong').style.display = '';

          }}



          // Rich analytics (AI)

          fetch('/api/student/quizzes/{quiz_id}/analyze', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ answers: answerLog }})

          }})

          .then(function(r) {{ return r.json(); }})

          .then(renderAnalytics)

          .catch(function() {{

            document.getElementById('qz-headline').textContent = 'Detailed analysis unavailable — try again later.';

            document.getElementById('qz-strengths').textContent = '—';

            document.getElementById('qz-weaknesses').textContent = '—';

            document.getElementById('qz-actions').textContent = '—';

          }});

        }}



        function verdictStyle(v) {{

          var m = {{

            mastery: {{ label:'Mastery', bg:'rgba(16,185,129,.15)', color:'#10B981' }},

            solid:   {{ label:'Solid', bg:'rgba(99,102,241,.15)', color:'#A78BFA' }},

            shaky:   {{ label:'Shaky', bg:'rgba(245,158,11,.15)', color:'#F59E0B' }},

            struggling:{{ label:'Struggling', bg:'rgba(239,68,68,.15)', color:'#EF4444' }},

          }};

          return m[v] || m.solid;

        }}



        function renderAnalytics(data) {{

          if (!data) return;

          var ai = data.ai || {{}};



          // Verdict pill

          var vs = verdictStyle(ai.verdict);

          var vp = document.getElementById('qz-verdict');

          vp.textContent = vs.label;

          vp.style.background = vs.bg;

          vp.style.color = vs.color;



          // Headline

          if (ai.headline) document.getElementById('qz-headline').textContent = ai.headline;



          // Pacing

          if (data.pacing) {{

            document.getElementById('qz-pace-total').textContent = fmtTime(data.pacing.total_time);

            document.getElementById('qz-pace-avg').textContent = fmtTime(data.pacing.avg_time);

            document.getElementById('qz-pace-fast').textContent = fmtTime(data.pacing.fastest);

            document.getElementById('qz-pace-slow').textContent = fmtTime(data.pacing.slowest);

          }}

          if (ai.time_insight) {{

            var pi = document.getElementById('qz-pace-insight');

            pi.style.display = 'block';

            pi.textContent = ai.time_insight;

          }}



          // Topic breakdown

          var topicsEl = document.getElementById('qz-topics');

          topicsEl.innerHTML = '';

          (data.breakdown || []).forEach(function(t) {{

            var color = t.percent >= 80 ? '#10B981' : t.percent >= 50 ? '#F59E0B' : '#EF4444';

            var row = document.createElement('div');

            row.className = 'qz-topic-row';

            row.innerHTML =

              '<span class="qz-topic-name" title="' + escH(t.topic) + '">' + escH(t.topic) + '</span>'

              + '<span class="qz-topic-bar"><span class="qz-topic-fill" style="width:' + t.percent + '%;background:' + color + ';"></span></span>'

              + '<span class="qz-topic-pct" style="color:' + color + ';">' + t.percent + '% <span style="font-weight:400;color:var(--text-muted);font-size:11px;">(' + t.correct + '/' + t.total + ')</span></span>';

            topicsEl.appendChild(row);

          }});

          if (!topicsEl.children.length) {{

            topicsEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px;">No topic data.</div>';

          }}



          // Strengths

          var sEl = document.getElementById('qz-strengths');

          if ((ai.strengths || []).length) {{

            sEl.innerHTML = ai.strengths.map(function(s) {{

              return '<div class="qz-ai-item"><span class="qz-ai-topic">&#10003; ' + escH(s.topic || '') + '</span>' + escH(s.detail || '') + '</div>';

            }}).join('');

          }} else {{

            sEl.innerHTML = '<span style="font-style:italic;">Nothing stood out yet. Keep building.</span>';

          }}



          // Plus weak-topic report

          var wEl = document.getElementById('qz-weaknesses');
          var plusReport = data.plus_report || {{}};

          if (!plusReport.unlocked) {{

            wEl.innerHTML =
              '<div class="qz-ai-item" style="border-bottom:none;">'
              + '<span class="qz-ai-topic">&#128274; Plus weakness report</span>'
              + 'Unlock the exact weak topics, fixes, next actions, and a 30-minute recovery plan for this quiz.'
              + '<span class="qz-ai-fix"><a href="/student/shop" style="color:#FBBF24;text-decoration:underline;font-weight:700;">Upgrade to PLUS &rarr;</a></span>'
              + '</div>';

          }} else if ((plusReport.weak_topics || []).length || (plusReport.weaknesses || []).length) {{

            var weakRows = (plusReport.weak_topics || []).map(function(t) {{
              return '<div class="qz-ai-item"><span class="qz-ai-topic">&#128202; ' + escH(t.topic || '') + '</span>'
                + 'Score: ' + (t.score || 0) + '% (' + (t.correct || 0) + '/' + (t.total || 0) + ')'
                + '<span class="qz-ai-fix">&#8594; Prioritize this before the next attempt.</span></div>';
            }}).join('');
            var fixRows = (plusReport.weaknesses || []).map(function(w) {{
              return '<div class="qz-ai-item"><span class="qz-ai-topic">&#9888;&#65039; ' + escH(w.topic || '') + '</span>'
                + escH(w.detail || '')
                + (w.fix ? '<span class="qz-ai-fix">&#8594; ' + escH(w.fix) + '</span>' : '')
                + '</div>';
            }}).join('');
            wEl.innerHTML = weakRows + fixRows;

          }} else if ((ai.weaknesses || []).length) {{

            wEl.innerHTML = ai.weaknesses.map(function(w) {{

              return '<div class="qz-ai-item"><span class="qz-ai-topic">&#9888;&#65039; ' + escH(w.topic || '') + '</span>'

                + escH(w.detail || '')

                + (w.fix ? '<span class="qz-ai-fix">&#8594; ' + escH(w.fix) + '</span>' : '')

                + '</div>';

            }}).join('');

          }} else {{

            wEl.innerHTML = '<span style="font-style:italic;color:#10B981;">Clean sweep \u2014 no clear weak spots. Try a harder quiz.</span>';

          }}



          // Mistake patterns

          if ((ai.mistake_patterns || []).length) {{

            document.getElementById('qz-patterns-card').style.display = '';

            document.getElementById('qz-patterns').innerHTML = ai.mistake_patterns.map(function(p) {{

              return '<div style="padding:6px 0;color:var(--text-muted);">&#8226; ' + escH(p) + '</div>';

            }}).join('');

          }}



          // Actions

          var aEl = document.getElementById('qz-actions');

          if ((ai.next_actions || []).length) {{

            aEl.innerHTML = '<ol style="margin:0;padding-left:20px;">' + ai.next_actions.map(function(x) {{

              return '<li style="margin-bottom:6px;">' + escH(x) + '</li>';

            }}).join('') + '</ol>';

          }} else {{

            aEl.textContent = 'Take another quiz on the same material to reinforce what just clicked.';

          }}



          // 30-min plan

          if ((ai.study_plan_30min || []).length) {{

            document.getElementById('qz-plan-card').style.display = '';

            document.getElementById('qz-plan').innerHTML = ai.study_plan_30min.map(function(step) {{

              return '<div style="display:flex;gap:12px;align-items:flex-start;padding:8px 0;border-bottom:1px dashed var(--border);">'

                + '<div style="flex:0 0 56px;font-weight:700;color:#A78BFA;">' + (step.minutes || 0) + ' min</div>'

                + '<div style="font-size:13px;line-height:1.5;">' + escH(step.task || '') + '</div>'

                + '</div>';

            }}).join('');

          }}



          // Encouragement

          if (ai.encouragement) document.getElementById('qz-encouragement').textContent = ai.encouragement;

        }}



        function renderReview() {{

          var body = document.getElementById('qz-review-body');

          body.innerHTML = answerLog.map(function(a, i) {{

            var q = questions[i];

            var sel = a.selected || '';

            return '<div class="qz-review-item ' + (a.is_correct ? 'correct' : 'wrong') + '">'

              + '<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">'

              +   '<div><strong>Q' + (i+1) + '.</strong> ' + escH(q.question) + '</div>'

              +   '<span style="font-size:11px;color:var(--text-muted);white-space:nowrap;">' + fmtTime(a.time) + '</span>'

              + '</div>'

              + (q.topic ? '<div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Topic: ' + escH(q.topic) + '</div>' : '')

              + '<div style="font-size:13px;margin-top:8px;">Your answer: <b>' + sel.toUpperCase() + '.</b> ' + escH(q['option_' + sel] || '')

              +   (a.is_correct ? ' &#10003;' : ' &#10007;')

              + '</div>'

              + (!a.is_correct ? '<div style="font-size:13px;margin-top:4px;color:#10B981;">Correct: <b>' + q.correct.toUpperCase() + '.</b> ' + escH(q['option_' + q.correct] || '') + '</div>' : '')

              + (q.explanation
                  ? '<div style="font-size:12px;margin-top:6px;color:var(--text-muted);font-style:italic;">' + escH(q.explanation) + '</div>'
                  : (!a.is_correct && !{_is_plus_js}
                      ? '<div style="font-size:12px;margin-top:6px;color:#FBBF24;">&#128274; Explanation locked &mdash; <a href="/student/shop" style="color:#FBBF24;text-decoration:underline;">upgrade to PLUS</a></div>'
                      : ''))

              + '</div>';

          }}).join('');

        }}



        function toggleReview() {{

          var b = document.getElementById('qz-review-body');

          var t = document.getElementById('qz-review-toggle');

          if (b.style.display === 'none') {{ b.style.display = 'block'; t.innerHTML = 'Hide &#9650;'; }}

          else {{ b.style.display = 'none'; t.innerHTML = 'Show &#9660;'; }}

        }}



        function restartQuiz() {{

          if (qzTimerInterval) {{ clearInterval(qzTimerInterval); qzTimerInterval = null; }}

          qIdx = 0; score = 0; answerLog = [];

          // Show setup again so user can re-configure timer

          document.getElementById('qz-setup').style.display = '';

          document.getElementById('qz-card').style.display = 'none';

          document.getElementById('qz-summary').style.display = 'none';

          document.getElementById('qz-timer-wrap').style.display = 'none';

          document.getElementById('qz-bar').style.width = '0%';

        }}



        function retakeWrong() {{

          var wrongIdx = answerLog.map(function(a,i){{return a.is_correct?null:i;}}).filter(function(x){{return x!==null;}});

          if (!wrongIdx.length) return;

          questions = wrongIdx.map(function(i) {{ return questions[i]; }});

          restartQuiz();

        }}



        document.addEventListener('keydown', function(e) {{

          if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

          if (document.getElementById('qz-setup').style.display !== 'none') return; // still on setup

          if (!answered) {{

            if (e.code === 'KeyA' || e.code === 'Digit1') {{ e.preventDefault(); selectAnswer('a'); }}

            if (e.code === 'KeyB' || e.code === 'Digit2') {{ e.preventDefault(); selectAnswer('b'); }}

            if (e.code === 'KeyC' || e.code === 'Digit3') {{ e.preventDefault(); selectAnswer('c'); }}

            if (e.code === 'KeyD' || e.code === 'Digit4') {{ e.preventDefault(); selectAnswer('d'); }}

          }} else {{

            if (e.code === 'Enter' || e.code === 'Space') {{ e.preventDefault(); nextQuestion(); }}

          }}

        }});



        // Quiz starts from the setup screen via beginQuiz(). No auto-start.

        </script>

        """, active_page="student_quizzes")



    # ── Exam Simulator Mode ────────────────────────────────



    @app.route("/student/exam-sim/<int:quiz_id>")

    def student_exam_simulator_page(quiz_id):

        """Exam simulator — timed, no going back, pressure UI, analytics."""

        if not _logged_in():

            return redirect(url_for("login"))

        quiz = sdb.get_quiz(quiz_id, _cid())

        if not quiz:

            return redirect(url_for("student_quizzes_page"))

        questions = sdb.get_quiz_questions(quiz_id)

        from student import subscription as _sub
        _is_plus = _sub.has_unlimited_ai(_cid())

        questions_json = json.dumps([{

            "id": q["id"], "question": q["question"],

            "option_a": q.get("option_a", ""), "option_b": q.get("option_b", ""),

            "option_c": q.get("option_c", ""), "option_d": q.get("option_d", ""),

            "correct": q["correct"],

            # Wrong-answer explanations are PLUS-gated; stripped for free users.
            "explanation": (q.get("explanation", "") if _is_plus else ""),

            "topic": q.get("topic", "") or ""

        } for q in questions], ensure_ascii=False)

        _is_plus_js = "true" if _is_plus else "false"



        # Default: 2 min per question

        default_minutes = max(5, len(questions) * 2)



        return _s_render(f"Exam Simulator: {quiz.get('title','')}", f"""

        <!-- Setup screen -->

        <div id="exam-setup" style="max-width:500px;margin:40px auto;text-align:center;">

          <div style="font-size:64px;margin-bottom:16px;">&#128221;</div>

          <h1 style="margin:0;">Simulador de prueba</h1>

          <p style="color:var(--text-muted);margin:8px 0 0;">{_esc(quiz.get('title',''))}</p>

          <p style="color:var(--text-muted);font-size:13px;">{len(questions)} questions &middot; {quiz.get('difficulty','').upper()}</p>



          <div class="card" style="padding:24px;margin:24px 0;text-align:left;">

            <h3 style="margin:0 0 16px;">&#9881; Settings</h3>

            <div class="form-group" style="margin-bottom:16px;">

              <label style="font-weight:600;font-size:14px;">Time Limit (minutes)</label>

              <input type="number" id="exam-time" value="{default_minutes}" min="1" max="180" class="edit-input" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:15px;margin-top:6px;">

            </div>

            <div style="background:var(--bg);border-radius:var(--radius-sm);padding:14px;font-size:13px;color:var(--text-muted);">

              <p style="margin:0 0 6px;"><strong style="color:var(--text);">&#9888;&#65039; Exam Rules:</strong></p>

              <ul style="margin:0;padding-left:18px;line-height:1.8;">

                <li>You <strong>cannot go back</strong> to previous questions</li>

                <li>Timer runs continuously — no pausing</li>

                <li>Las respuestas son finales una vez enviadas</li>

                <li>Análisis detallado al finalizar</li>

              </ul>

            </div>

          </div>



          <button onclick="startExam()" class="btn btn-primary" style="padding:12px 40px;font-size:16px;">&#9889; Start Exam</button>

          <p style="margin-top:12px;"><a href="/student/quizzes" style="color:var(--text-muted);font-size:13px;">&larr; Back to Quizzes</a></p>

        </div>



        <!-- Active exam -->

        <div id="exam-active" style="display:none;">

          <!-- Timer bar -->

          <div style="position:sticky;top:60px;z-index:50;background:var(--bg);padding:8px 0;margin-bottom:16px;">

            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">

              <span style="font-size:14px;font-weight:600;color:var(--text);" id="exam-q-txt">Question 1 / {len(questions)}</span>

              <span style="font-size:20px;font-weight:700;font-family:monospace;" id="exam-timer" style="color:var(--text);">00:00</span>

            </div>

            <div style="background:var(--border);border-radius:8px;height:6px;overflow:hidden;">

              <div id="exam-timer-bar" style="height:100%;background:linear-gradient(90deg,#10B981,#3B82F6);width:100%;transition:width 1s linear;border-radius:8px;"></div>

            </div>

          </div>



          <div style="max-width:700px;margin:0 auto;">

            <div id="exam-card" class="card" style="padding:30px;">

              <div id="exam-question" style="font-size:18px;font-weight:600;margin-bottom:20px;line-height:1.5;"></div>

              <div id="exam-options"></div>

              <div style="display:flex;justify-content:flex-end;margin-top:20px;">

                <button id="exam-submit-btn" onclick="submitAnswer()" class="btn btn-primary" disabled>Lock In Answer &rarr;</button>

              </div>

            </div>

          </div>

        </div>



        <!-- Results -->

        <div id="exam-results" style="display:none;max-width:800px;margin:0 auto;">

          <div class="card" style="text-align:center;padding:30px;margin-bottom:20px;">

            <div id="exam-emoji" style="font-size:64px;margin-bottom:12px;">&#127942;</div>

            <h1 style="margin:0 0 8px;">¡Prueba completada!</h1>

            <div id="exam-final-score" style="font-size:48px;font-weight:800;color:var(--primary);"></div>

            <div id="exam-final-detail" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>

            <div id="exam-time-taken" style="font-size:14px;color:var(--text-muted);margin-top:4px;"></div>

          </div>



          <!-- Analytics -->

          <div class="card" style="padding:24px;margin-bottom:20px;">

            <h2 style="margin:0 0 16px;">&#128202; Analytics</h2>

            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;">

              <div style="text-align:center;padding:16px;background:var(--bg);border-radius:var(--radius-sm);">

                <div id="anal-avg-time" style="font-size:24px;font-weight:700;color:var(--primary);">0s</div>

                <div style="font-size:12px;color:var(--text-muted);">Promedio por pregunta</div>

              </div>

              <div style="text-align:center;padding:16px;background:var(--bg);border-radius:var(--radius-sm);">

                <div id="anal-fastest" style="font-size:24px;font-weight:700;color:#10B981;">0s</div>

                <div style="font-size:12px;color:var(--text-muted);">Respuesta más rápida</div>

              </div>

              <div style="text-align:center;padding:16px;background:var(--bg);border-radius:var(--radius-sm);">

                <div id="anal-slowest" style="font-size:24px;font-weight:700;color:#EF4444;">0s</div>

                <div style="font-size:12px;color:var(--text-muted);">Respuesta más lenta</div>

              </div>

            </div>

          </div>



          <!-- Per-question review -->

          <div class="card" style="padding:24px;">

            <h2 style="margin:0 0 16px;">&#128214; Question Review</h2>

            <div id="exam-review"></div>

          </div>



          <div style="display:flex;gap:12px;justify-content:center;margin:24px 0;">

            <button onclick="mrReload()" class="btn btn-primary">&#128260; Retake Exam</button>

            <a href="/student/quizzes" class="btn btn-outline">Volver a quizzes</a>

          </div>

        </div>



        <style>

        .exam-opt {{

          display:block;width:100%;text-align:left;padding:14px 18px;margin-bottom:10px;

          background:var(--bg);border:2px solid var(--border);border-radius:12px;

          cursor:pointer;font-size:15px;color:var(--text);transition:all 0.2s ease;

        }}

        .exam-opt:hover {{ border-color:var(--primary);background:var(--card); }}

        .exam-opt.selected {{ border-color:var(--primary);background:var(--primary-light,#EDE9FE);font-weight:600; }}

        .review-q {{ padding:16px;margin-bottom:12px;border-radius:var(--radius-sm);border-left:4px solid; }}

        .review-q.correct {{ border-color:#10B981;background:#D1FAE520; }}

        .review-q.wrong {{ border-color:#EF4444;background:#FEE2E220; }}

        </style>



        <script>

        var questions = {questions_json};

        var eIdx = 0, eScore = 0, eSelected = null;

        var timePerQuestion = [];

        var answers = [];

        var questionStartTime = 0;

        var timerInterval = null;

        var totalSeconds = 0;

        var elapsedSeconds = 0;



        function startExam() {{

          totalSeconds = parseInt(document.getElementById('exam-time').value) * 60;

          if (totalSeconds < 60) totalSeconds = 60;

          elapsedSeconds = 0;

          document.getElementById('exam-setup').style.display = 'none';

          document.getElementById('exam-active').style.display = '';

          questionStartTime = Date.now();

          timerInterval = setInterval(tickTimer, 1000);

          renderExamQ();

        }}



        function tickTimer() {{

          elapsedSeconds++;

          var remaining = totalSeconds - elapsedSeconds;

          if (remaining <= 0) {{

            clearInterval(timerInterval);

            // Auto-submit current and force end

            if (eSelected) answers.push({{ q: eIdx, selected: eSelected, time: (Date.now() - questionStartTime) / 1000 }});

            finishExam();

            return;

          }}

          var m = Math.floor(remaining / 60);

          var s = remaining % 60;

          var timerEl = document.getElementById('exam-timer');

          timerEl.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;

          timerEl.style.color = remaining <= 60 ? '#EF4444' : remaining <= 300 ? '#F59E0B' : 'var(--text)';

          document.getElementById('exam-timer-bar').style.width = (remaining / totalSeconds * 100) + '%';

          if (remaining <= 60) document.getElementById('exam-timer-bar').style.background = '#EF4444';

          else if (remaining <= 300) document.getElementById('exam-timer-bar').style.background = '#F59E0B';

        }}



        function renderExamQ() {{

          if (eIdx >= questions.length) {{ finishExam(); return; }}

          var q = questions[eIdx];

          eSelected = null;

          document.getElementById('exam-submit-btn').disabled = true;

          document.getElementById('exam-q-txt').textContent = 'Question ' + (eIdx + 1) + ' / ' + questions.length;

          document.getElementById('exam-question').textContent = 'Q' + (eIdx + 1) + '. ' + q.question;

          var opts = document.getElementById('exam-options');

          opts.innerHTML = '';

          ['a','b','c','d'].forEach(function(key) {{

            var btn = document.createElement('button');

            btn.className = 'exam-opt';

            btn.textContent = key.toUpperCase() + '. ' + q['option_' + key];

            btn.dataset.key = key;

            btn.onclick = function() {{

              document.querySelectorAll('.exam-opt').forEach(function(b) {{ b.classList.remove('selected'); }});

              btn.classList.add('selected');

              eSelected = key;

              document.getElementById('exam-submit-btn').disabled = false;

            }};

            opts.appendChild(btn);

          }});

          questionStartTime = Date.now();

        }}



        function submitAnswer() {{

          if (!eSelected) return;

          var q = questions[eIdx];

          var timeTaken = (Date.now() - questionStartTime) / 1000;

          var isCorrect = eSelected === q.correct;

          if (isCorrect) eScore++;

          answers.push({{ q: eIdx, selected: eSelected, correct: q.correct, isCorrect: isCorrect, time: timeTaken, explanation: q.explanation }});

          eIdx++;

          renderExamQ();

        }}



        function finishExam() {{

          clearInterval(timerInterval);

          document.getElementById('exam-active').style.display = 'none';

          document.getElementById('exam-results').style.display = '';

          var pct = Math.round(eScore / questions.length * 100);

          document.getElementById('exam-final-score').textContent = pct + '%';

          document.getElementById('exam-final-detail').textContent = eScore + ' of ' + questions.length + ' correct';

          document.getElementById('exam-time-taken').textContent = 'Time: ' + Math.floor(elapsedSeconds / 60) + 'm ' + (elapsedSeconds % 60) + 's';

          document.getElementById('exam-emoji').innerHTML = pct >= 90 ? '&#127942;' : pct >= 70 ? '&#127881;' : pct >= 50 ? '&#128170;' : '&#128218;';

          if (pct >= 80 && window.confettiBurst) {{ window.confettiBurst(pct >= 95 ? 100 : 60); }}



          // Analytics

          var times = answers.map(function(a) {{ return a.time; }});

          var avg = times.length ? (times.reduce(function(s,t) {{ return s + t; }}, 0) / times.length) : 0;

          document.getElementById('anal-avg-time').textContent = Math.round(avg) + 's';

          document.getElementById('anal-fastest').textContent = Math.round(Math.min.apply(null, times)) + 's';

          document.getElementById('anal-slowest').textContent = Math.round(Math.max.apply(null, times)) + 's';



          // Review

          var reviewHtml = '';

          answers.forEach(function(a) {{

            var q = questions[a.q];

            reviewHtml += '<div class="review-q ' + (a.isCorrect ? 'correct' : 'wrong') + '">'

              + '<div style="display:flex;justify-content:space-between;align-items:start;">'

              + '<strong>Q' + (a.q + 1) + '. ' + escH(q.question) + '</strong>'

              + '<span style="font-size:12px;color:var(--text-muted);white-space:nowrap;">' + Math.round(a.time) + 's</span>'

              + '</div>'

              + '<div style="font-size:13px;margin-top:6px;">Your answer: <strong>' + a.selected.toUpperCase() + '</strong>'

              + (a.isCorrect ? ' &#10003;' : ' &#10007; (Correct: ' + a.correct.toUpperCase() + ')') + '</div>'

              + (a.explanation
                  ? '<div style="font-size:13px;margin-top:6px;color:var(--text-muted);font-style:italic;">' + escH(a.explanation) + '</div>'
                  : (!a.isCorrect && !{_is_plus_js}
                      ? '<div style="font-size:12px;margin-top:6px;color:#FBBF24;">&#128274; Explanation locked &mdash; <a href="/student/shop" style="color:#FBBF24;text-decoration:underline;">upgrade to PLUS</a></div>'
                      : ''))

              + '</div>';

          }});

          document.getElementById('exam-review').innerHTML = reviewHtml;



          // Submit score

          fetch('/api/student/quizzes/{quiz_id}/score', {{

            method: 'POST', headers: {{'Content-Type':'application/json'}},

            body: JSON.stringify({{ score: pct }})

          }}).catch(function(){{}});

        }}



        function escH(t) {{ var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }}



        document.addEventListener('keydown', function(e) {{

          if (document.getElementById('exam-active').style.display === 'none') return;

          if (!eSelected || eSelected === null) {{

            if (e.code === 'KeyA' || e.code === 'Digit1') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="a"]').click(); }}

            if (e.code === 'KeyB' || e.code === 'Digit2') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="b"]').click(); }}

            if (e.code === 'KeyC' || e.code === 'Digit3') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="c"]').click(); }}

            if (e.code === 'KeyD' || e.code === 'Digit4') {{ e.preventDefault(); document.querySelector('.exam-opt[data-key="d"]').click(); }}

          }}

          if (e.code === 'Enter' && eSelected) {{ e.preventDefault(); submitAnswer(); }}

        }});

        </script>

        """, active_page="student_quizzes")



    # ── Notes Frontend Page ─────────────────────────────────



    @app.route("/student/notes")

    def student_notes_page():

        if not _logged_in():

            return redirect(url_for("login"))

        courses = sdb.get_courses(_cid())

        notes = sdb.get_notes(_cid())



        course_options = '<option value="">Select a course...</option>'

        for c in courses:

            course_options += f'<option value="{c["id"]}">{_esc(c["name"])}</option>'



        notes_html = ""

        for n in notes:

            notes_html += f"""

            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/notes/{n['id']}'">

              <div style="display:flex;justify-content:space-between;align-items:center;">

                <div>

                  <h3 style="margin:0;font-size:16px;">{_esc(n.get('title','Untitled'))}</h3>

                  <span style="font-size:13px;color:var(--text-muted);">{_esc(n.get('course_name',''))} &middot; {_esc(n.get('source_type','ai'))} &middot; {str(n.get('created_at',''))[:10]}</span>

                </div>

                <div style="display:flex;gap:6px;align-items:center;">

                  <button onclick="event.stopPropagation();deleteNote({n['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>

                </div>

              </div>

            </div>"""

        if not notes_html:

            notes_html = """<div style="text-align:center;padding:40px;color:var(--text-muted);">

              <div style="font-size:48px;margin-bottom:12px;">&#128221;</div>

              <p>No notes yet. Generate AI study notes from your course materials!</p>

            </div>"""



        return _s_render("Notes", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px;">

          <div>

            <h1 style="margin:0;">&#128221; AI Study Notes</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Apuntes completos generados a partir de tu material de curso</p>

          </div>

          <button onclick="document.getElementById('note-form').style.display=document.getElementById('note-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#10024; Generate Notes</button>

        </div>



        <div id="note-form" style="display:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px;">

          <h3 style="margin:0 0 14px;">Generar apuntes con IA</h3>

          <div class="form-group">

            <label>Course</label>

            <select id="note-course" class="edit-input">{course_options}</select>

          </div>

          <p style="font-size:12px;color:var(--text-muted);margin:8px 0 12px;">The AI will analyze your uploaded files and syllabus to create comprehensive study notes.</p>

          <button onclick="genNotes()" class="btn btn-primary btn-sm" id="note-gen-btn">&#10024; Generate</button>

        </div>



        <!-- Drag-drop PDF upload zone -->

        <div id="pdf-drop-zone" style="border:2px dashed var(--border);border-radius:var(--radius-sm);padding:32px;text-align:center;margin-bottom:8px;cursor:pointer;transition:all 0.3s ease;background:var(--card);"

          ondragover="event.preventDefault();this.style.borderColor='var(--primary)';this.style.background='rgba(139,92,246,0.05)'"

          ondragleave="this.style.borderColor='var(--border)';this.style.background='var(--card)'"

          ondrop="event.preventDefault();this.style.borderColor='var(--border)';this.style.background='var(--card)';handlePDFDrop(event.dataTransfer.files)"

          onclick="document.getElementById('pdf-file-input').click()">

          <div style="font-size:36px;margin-bottom:8px;">&#128196;</div>

          <p style="margin:0;font-weight:600;color:var(--text);">Drag & drop PDF or DOCX files here</p>

          <p style="margin:4px 0 0;font-size:13px;color:var(--text-muted);">or click to browse &middot; multi-chapter PDFs fully supported</p>

          <input type="file" id="pdf-file-input" style="display:none;" accept=".pdf,.docx,.doc,.txt" multiple onchange="handlePDFDrop(this.files)">

        </div>

        <div id="pdf-upload-status" style="display:none;margin-bottom:16px;padding:12px;border-radius:var(--radius-sm);background:var(--card);border:1px solid var(--border);text-align:center;color:var(--text-muted);font-size:14px;"></div>



        {notes_html}



        <style>

        .edit-input {{ width:100%; padding:6px 10px; border:1px solid var(--border); border-radius:var(--radius-sm); background:var(--bg); color:var(--text); font-size:13px; }}

        .edit-input:focus {{ border-color:var(--primary); outline:none; }}

        </style>

        <script>

        async function genNotes() {{

          var courseId = document.getElementById('note-course').value;

          if (!courseId) {{ alert('Select a course'); return; }}

          var btn = document.getElementById('note-gen-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Generating (may take ~15s)...';

          try {{

            var r = await fetch('/api/student/notes/generate', {{

              method: 'POST', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ course_id: parseInt(courseId) }})

            }});

            var d = await _safeJson(r);

            if (r.ok) {{

              mrGo('/student/notes/' + d.note_id);

            }} else {{ alert(d.error || 'Error al generar'); }}

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); }}

          btn.disabled = false; btn.innerHTML = '&#10024; Generate';

        }}

        async function deleteNote(id) {{

          if (!confirm('Delete this note?')) return;

          await fetch('/api/student/notes/' + id, {{method:'DELETE'}});

          mrReload();

        }}

        async function handlePDFDrop(files) {{

          if (!files || files.length === 0) return;

          var valid = [];

          for (var i = 0; i < files.length; i++) {{

            var name = files[i].name.toLowerCase();

            if (!name.endsWith('.pdf') && !name.endsWith('.docx') && !name.endsWith('.doc') && !name.endsWith('.txt')) continue;

            if (files[i].size > 50 * 1024 * 1024) {{ alert(files[i].name + ' es demasiado grande (máx 50MB)'); continue; }}

            valid.push(files[i]);

          }}

          if (valid.length === 0) {{ alert('Only PDF, DOCX, and TXT files are supported'); return; }}

          var status = document.getElementById('pdf-upload-status');

          status.style.display = 'block';

          var lastNoteId = null;

          var ok = 0, fail = 0;

          var lastError = '';

          for (var i = 0; i < valid.length; i++) {{

            var file = valid[i];

            status.innerHTML = '&#129504; AI-summarizing ' + (i+1) + '/' + valid.length + ': <b>' + file.name + '</b> (multi-chapter, may take 1-3 min)...';

            try {{

                // 1) extract text

                var fd1 = new FormData(); fd1.append('file', file);

                var rx = await fetch('/api/student/extract-file', {{ method:'POST', body: fd1 }});

                var dx = await _safeJson(rx);

                if (!rx.ok) {{ lastError = 'Extract failed (' + rx.status + '): ' + (dx.error || 'unknown'); fail++; continue; }}

                // 2) AI generate notes from extracted text

                var rg = await fetch('/api/student/notes/generate', {{

                  method:'POST', headers:{{'Content-Type':'application/json'}},

                  body: JSON.stringify({{ source_text: dx.text, title: dx.title }})

                }});

                var dg = await _safeJson(rg);

                if (rg.ok && dg.note_id) {{ lastNoteId = dg.note_id; ok++; }}

                else {{ lastError = 'AI notes failed (' + rg.status + '): ' + (dg.error || 'timeout or server error'); fail++; }}

            }} catch(e) {{ lastError = 'Network: ' + (e && e.message ? e.message : e); fail++; }}

          }}

          if (ok > 0 && valid.length === 1) {{ mrGo('/student/notes/' + lastNoteId); }}

          else if (ok > 0) {{ status.innerHTML = '&#9989; ' + ok + ' notes created' + (fail ? ', ' + fail + ' failed' : '') + '. Reloading...'; setTimeout(function(){{ mrReload(); }}, 1200); }}

          else {{ status.innerHTML = '&#10060; All uploads failed' + (lastError ? '<br><span style="font-size:12px;color:var(--text-muted);">' + lastError + '</span>' : ''); }}

        }}

        </script>

        """, active_page="student_notes")



    @app.route("/student/notes/<int:note_id>")

    def student_note_view_page(note_id):

        """View and edit a study note."""

        if not _logged_in():

            return redirect(url_for("login"))

        note = sdb.get_note(note_id, _cid())

        if not note:

            return redirect(url_for("student_notes_page"))



        return _s_render(f"Note: {note.get('title','')}", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">

          <div>

            <a href="/student/notes" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Back to Notes</a>

            <h1 style="margin:4px 0 0;font-size:24px;">{_esc(note.get('title',''))}</h1>

            <p style="color:var(--text-muted);margin:2px 0 0;font-size:13px;">Created {str(note.get('created_at',''))[:10]} &middot; {_esc(note.get('source_type','ai'))}</p>

          </div>

          <div style="display:flex;gap:8px;">

            <button onclick="toggleEdit()" class="btn btn-outline btn-sm" id="edit-toggle">&#9999;&#65039; Edit</button>

            <a href="/api/student/notes/{note_id}/pdf" class="btn btn-outline btn-sm">&#128196; Export PDF</a>

            <button onclick="printNote()" class="btn btn-outline btn-sm">&#128424; Print</button>

          </div>

        </div>



        <div class="card" style="padding:30px;">

          <div id="note-view">{note.get('content_html','')}</div>

          <div id="note-edit" style="display:none;">

            <!-- Formatting toolbar -->

            <div id="editor-toolbar" style="display:flex;gap:4px;flex-wrap:wrap;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-bottom:none;border-radius:var(--radius-sm) var(--radius-sm) 0 0;">

              <button type="button" onclick="fmt('bold')" class="tb" title="Bold"><b>B</b></button>

              <button type="button" onclick="fmt('italic')" class="tb" title="Italic"><i>I</i></button>

              <button type="button" onclick="fmt('underline')" class="tb" title="Underline"><u>U</u></button>

              <span style="width:1px;background:var(--border);margin:0 4px;"></span>

              <button type="button" onclick="fmt('formatBlock','<h2>')" class="tb" title="Heading 2">H2</button>

              <button type="button" onclick="fmt('formatBlock','<h3>')" class="tb" title="Heading 3">H3</button>

              <button type="button" onclick="fmt('formatBlock','<p>')" class="tb" title="Paragraph">P</button>

              <span style="width:1px;background:var(--border);margin:0 4px;"></span>

              <button type="button" onclick="fmt('insertUnorderedList')" class="tb" title="Bullet list">&#8226; List</button>

              <button type="button" onclick="fmt('insertOrderedList')" class="tb" title="Numbered list">1. List</button>

              <span style="width:1px;background:var(--border);margin:0 4px;"></span>

              <button type="button" onclick="fmt('removeFormat')" class="tb" title="Clear formatting">&#10005; Clear</button>

            </div>

            <!-- WYSIWYG editor -->

            <div id="note-editor" contenteditable="true" style="

              min-height:350px;padding:20px;border:1px solid var(--border);border-radius:0 0 var(--radius-sm) var(--radius-sm);

              background:var(--card);color:var(--text);font-size:15px;line-height:1.7;outline:none;overflow-y:auto;

            ">{note.get('content_html','')}</div>

            <div style="display:flex;gap:8px;margin-top:12px;">

              <button onclick="saveNote()" class="btn btn-primary btn-sm" id="save-note-btn">&#128190; Guardar</button>

              <button onclick="toggleEdit()" class="btn btn-outline btn-sm">Cancelar</button>

            </div>

          </div>

        </div>



        <style>

        .tb {{ background:var(--card);border:1px solid var(--border);border-radius:4px;padding:4px 10px;cursor:pointer;font-size:13px;color:var(--text);transition:all 0.15s; }}

        .tb:hover {{ background:var(--primary-light);color:var(--primary); }}

        #note-view h2, #note-editor h2 {{ color:var(--text);margin:24px 0 12px;font-size:20px;border-bottom:2px solid var(--border);padding-bottom:6px; }}

        #note-view h3, #note-editor h3 {{ color:var(--text);margin:18px 0 8px;font-size:17px; }}

        #note-view p, #note-editor p {{ color:var(--text-secondary);line-height:1.7;margin:8px 0; }}

        #note-view ul, #note-view ol, #note-editor ul, #note-editor ol {{ color:var(--text-secondary);line-height:1.8;padding-left:24px; }}

        #note-view strong, #note-editor strong {{ color:var(--text); }}

        @media print {{

          body * {{ visibility: hidden; }}

          #note-view, #note-view * {{ visibility: visible; }}

          #note-view {{ position: absolute; left: 0; top: 0; width: 100%; }}

        }}

        </style>

        <script>

        var editing = false;

        function fmt(cmd, val) {{

          document.execCommand(cmd, false, val || null);

          document.getElementById('note-editor').focus();

        }}

        function toggleEdit() {{

          editing = !editing;

          document.getElementById('note-view').style.display = editing ? 'none' : '';

          document.getElementById('note-edit').style.display = editing ? '' : 'none';

          document.getElementById('edit-toggle').innerHTML = editing ? '&#128065; View' : '&#9999;&#65039; Edit';

          if (editing) {{

            document.getElementById('note-editor').innerHTML = document.getElementById('note-view').innerHTML;

            document.getElementById('note-editor').focus();

          }}

        }}

        async function saveNote() {{

          var btn = document.getElementById('save-note-btn');

          btn.disabled = true; btn.innerHTML = '&#9203; Saving...';

          var html = document.getElementById('note-editor').innerHTML;

          try {{

            var r = await fetch('/api/student/notes/{note_id}', {{

              method: 'PUT', headers: {{'Content-Type':'application/json'}},

              body: JSON.stringify({{ content_html: html }})

            }});

            if (r.ok) {{

              document.getElementById('note-view').innerHTML = html;

              toggleEdit();

            }} else {{ alert('Save failed'); }}

          }} catch(e) {{ mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.'); }}

          btn.disabled = false; btn.innerHTML = '&#128190; Guardar';

        }}

        function printNote() {{ window.print(); }}

        // Re-render math in notes

        if (typeof renderMathInElement === 'function') {{

          renderMathInElement(document.getElementById('note-view'), {{

            delimiters: [

              {{left: '$$', right: '$$', display: true}},

              {{left: '$', right: '$', display: false}},

              {{left: '\\\\(', right: '\\\\)', display: false}},

              {{left: '\\\\[', right: '\\\\]', display: true}}

            ], throwOnError: false

          }});

        }}

        </script>

        """, active_page="student_notes")



    # ================================================================

    #  FEATURE 4 — Gamification (XP / Badges / Streaks / Level)

    # ================================================================



    @app.route("/api/student/gamification")

    def student_gamification_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        total_xp = sdb.get_total_xp(cid)

        level_name, level_floor, level_ceil = sdb.get_level(total_xp)

        streak = sdb.get_streak_days(cid)

        badges = sdb.get_badges(cid)

        history = sdb.get_xp_history(cid)



        # Auto-award streak badges

        for key, threshold in [("streak_3", 3), ("streak_7", 7), ("streak_14", 14), ("streak_30", 30), ("streak_60", 60), ("streak_100", 100)]:

            if streak >= threshold:

                sdb.earn_badge(cid, key)

        for key, threshold in [("xp_100", 100), ("xp_500", 500), ("xp_1000", 1000), ("xp_2500", 2500), ("xp_5000", 5000)]:

            if total_xp >= threshold:

                sdb.earn_badge(cid, key)

        # Course badges

        courses = sdb.get_courses(cid)

        n_courses = len(courses) if courses else 0

        if n_courses >= 1:

            sdb.earn_badge(cid, "first_course")

        if n_courses >= 5:

            sdb.earn_badge(cid, "five_courses")

        # Perfect week = streak >= 7

        if streak >= 7:

            sdb.earn_badge(cid, "perfect_week")



        return jsonify(

            total_xp=total_xp,

            level=level_name,

            level_floor=level_floor,

            level_ceil=level_ceil,

            streak=streak,

            badges=[{"badge_key": b["badge_key"], "emoji": b.get("emoji","🏅"),

                     "name": b.get("name",""), "desc": b.get("desc","")} for b in badges],

            history=[{"action": h["action"], "xp": h["xp"], "detail": h.get("detail",""),

                      "date": str(h.get("created_at",""))[:10]} for h in history],

        )



    # ── Marketplace ─────────────────────────────────────────



    @app.route("/student/marketplace")

    def student_marketplace_page():

        """Marketplace — buy & sell study files for coins."""

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        search = (request.args.get("q") or "").strip()

        subject = (request.args.get("subject") or "").strip()

        items = sdb.marketplace_browse(viewer_id=cid, search=search, subject=subject, limit=80)

        wallet = sdb.get_wallet(cid)

        coins = int(wallet.get("coins") or 0)



        try:

            from student.subscription import get_tier as _get_tier

            tier = _get_tier(cid)

        except Exception:

            tier = "free"

        is_ultimate = (tier == "ultimate")



        cards_html = ""

        for it in items:

            owned = bool(it.get("owned"))

            price = int(it.get("price_coins") or 0)

            size_kb = max(1, int((it.get("file_size") or 0) / 1024))

            ext = (it.get("file_ext") or "").upper() or "FILE"

            preview = (it.get("preview") or it.get("description") or "")[:240]

            badge = ""

            if is_ultimate:

                badge = '<span style="background:#7c3aed;color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">GRATIS ULTIMATE</span>'

            elif owned:

                badge = '<span style="background:var(--green);color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">COMPRADO</span>'

            cards_html += f"""

            <div class="card" style="margin-bottom:12px;cursor:pointer;" onclick="window.location='/student/marketplace/{it['id']}'">

              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">

                <div style="flex:1;min-width:0;">

                  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">

                    <h3 style="margin:0;font-size:16px;">{_esc(it.get('title','Sin título'))}</h3>

                    {badge}

                  </div>

                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">

                    por {_esc(it.get('seller_name','Desconocido'))} &middot; {_esc(it.get('subject') or 'General')} &middot; {ext} &middot; {size_kb} KB &middot; {int(it.get('downloads') or 0)} descargas

                  </div>

                  <p style="margin:8px 0 0;font-size:13px;color:var(--text-muted);">{_esc(preview)}</p>

                </div>

                <div style="text-align:right;white-space:nowrap;">

                  <div style="font-size:18px;font-weight:700;color:#f59e0b;">&#129689; {price}</div>

                  <div style="font-size:11px;color:var(--text-muted);">monedas</div>

                </div>

              </div>

            </div>"""

        if not cards_html:

            cards_html = '<div class="card" style="text-align:center;color:var(--text-muted);padding:24px;">Todavía no hay publicaciones. Sé el primero en compartir.</div>'



        ultimate_note = ""

        if is_ultimate:

            ultimate_note = (

                '<div class="card" style="background:linear-gradient(90deg,#7c3aed22,#a855f722);'

                'border:1px solid #7c3aed;margin-bottom:12px;">'

                '<strong style="color:#a855f7;">&#10024; Beneficio ULTIMATE:</strong> '

                'Tienes acceso gratis a todos los archivos del mercado.</div>'

            )



        return _s_render("Mercado", f"""

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:12px;">

          <div>

            <h1 style="margin:0;">&#128722; Mercado</h1>

            <p style="color:var(--text-muted);margin:4px 0 0;">Compra y vende materiales de estudio con monedas.</p>

          </div>

          <div style="display:flex;gap:8px;align-items:center;">

            <span style="padding:6px 12px;background:var(--bg-elev);border-radius:8px;font-weight:700;color:#f59e0b;">&#129689; {coins} monedas</span>

            <a href="/student/marketplace/my" class="btn btn-outline btn-sm">&#128193; Mis publicaciones</a>

            <button class="btn btn-primary btn-sm" onclick="document.getElementById('upload-modal').style.display='flex'">&#10133; Vender archivo</button>

          </div>

        </div>



        {ultimate_note}



        <form method="GET" action="/student/marketplace" style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">

          <input type="text" name="q" placeholder="Buscar por título o descripción..." value="{_esc(search)}" style="flex:1;min-width:200px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);">

          <input type="text" name="subject" placeholder="Filtrar por materia..." value="{_esc(subject)}" style="width:180px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);">

          <button class="btn btn-outline btn-sm" type="submit">Buscar</button>

        </form>



        {cards_html}



        <div id="upload-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;align-items:center;justify-content:center;padding:20px;" onclick="if(event.target===this)this.style.display='none'">

          <div class="card" style="max-width:540px;width:100%;max-height:90vh;overflow:auto;">

            <h2 style="margin-top:0;">&#128722; Vender archivo de estudio</h2>

            <p style="color:var(--text-muted);font-size:13px;">Sube un archivo de estudio. Los compradores solo ven el título, la materia y la vista previa; desbloquean el archivo completo pagando tu precio en monedas.</p>

            <form id="upload-form" enctype="multipart/form-data">

              <label style="display:block;margin-top:12px;font-size:13px;">Título *</label>

              <input name="title" type="text" required maxlength="200" style="width:100%;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);">

              <label style="display:block;margin-top:12px;font-size:13px;">Materia (opcional)</label>

              <input name="subject" type="text" maxlength="120" placeholder="Ej. Cálculo, Biología" style="width:100%;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);">

              <label style="display:block;margin-top:12px;font-size:13px;">Descripción corta (opcional)</label>

              <textarea name="description" rows="2" maxlength="500" style="width:100%;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);"></textarea>

              <label style="display:block;margin-top:12px;font-size:13px;">Vista previa (lo que ven antes de pagar)</label>

              <textarea name="preview" rows="3" maxlength="1500" placeholder="Muestra un adelanto: primer párrafo, índice, resumen, etc." style="width:100%;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);"></textarea>

              <label style="display:block;margin-top:12px;font-size:13px;">Precio (monedas) *</label>

              <input name="price_coins" type="number" min="1" max="5000" value="20" required style="width:100%;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);">

              <label style="display:block;margin-top:12px;font-size:13px;">Archivo * (máx. 25 MB)</label>

              <input name="file" type="file" required style="width:100%;padding:6px 0;">

              <div id="upload-msg" style="margin-top:10px;font-size:13px;"></div>

              <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end;">

                <button type="button" class="btn btn-outline btn-sm" onclick="document.getElementById('upload-modal').style.display='none'">Cancelar</button>

                <button type="submit" class="btn btn-primary btn-sm">Publicar</button>

              </div>

            </form>

          </div>

        </div>



        <script>

        document.getElementById('upload-form').addEventListener('submit', async function(ev){{

          ev.preventDefault();

          var msg = document.getElementById('upload-msg');

          msg.textContent = 'Subiendo...';

          msg.style.color = 'var(--text-muted)';

          var fd = new FormData(ev.target);

          try {{

            var r = await fetch('/api/student/marketplace/upload', {{ method:'POST', body: fd }});

            var j = await r.json();

            if (j && j.ok) {{

              msg.textContent = 'Publicado.';

              msg.style.color = 'var(--green)';

              setTimeout(function(){{ mrGo('/student/marketplace/' + j.item_id); }}, 600);

            }} else {{

              msg.textContent = (j && j.error) || 'No se pudo subir.';

              msg.style.color = 'var(--red)';

            }}

          }} catch(e) {{

            msg.textContent = 'Error de red.';

            msg.style.color = 'var(--red)';

          }}

        }});

        </script>

        """, active_page="student_marketplace")



    @app.route("/student/marketplace/my")

    def student_marketplace_my_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        listings = sdb.marketplace_my_listings(cid)

        purchases = sdb.marketplace_my_purchases(cid)



        listings_html = ""

        for it in listings:

            size_kb = max(1, int((it.get("file_size") or 0) / 1024))

            ext = (it.get("file_ext") or "").upper() or "FILE"

            listings_html += f"""

            <div class="card" style="margin-bottom:10px;">

              <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">

                <div style="flex:1;min-width:0;">

                  <a href="/student/marketplace/{it['id']}" style="font-weight:700;color:var(--text);text-decoration:none;">{_esc(it.get('title','Sin título'))}</a>

                  <div style="font-size:12px;color:var(--text-muted);">{ext} &middot; {size_kb} KB &middot; {int(it.get('downloads') or 0)} descargas &middot; {int(it.get('price_coins') or 0)} monedas</div>

                </div>

                <button class="btn btn-ghost btn-sm" style="color:var(--red);" onclick="deleteListing({it['id']})">&#128465;</button>

              </div>

            </div>"""

        if not listings_html:

            listings_html = '<div class="card" style="color:var(--text-muted);">Todavía no has publicado nada.</div>'



        purchases_html = ""

        for it in purchases:

            ext = (it.get("file_ext") or "").upper() or "FILE"

            purchases_html += f"""

            <div class="card" style="margin-bottom:10px;">

              <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">

                <div style="flex:1;min-width:0;">

                  <a href="/student/marketplace/{it['id']}" style="font-weight:700;color:var(--text);text-decoration:none;">{_esc(it.get('title','Sin título'))}</a>

                  <div style="font-size:12px;color:var(--text-muted);">por {_esc(it.get('seller_name','Desconocido'))} &middot; {ext} &middot; pagado {int(it.get('price_paid') or 0)} monedas</div>

                </div>

                <a class="btn btn-outline btn-sm" href="/api/student/marketplace/{it['id']}/download">&#11015; Descargar</a>

              </div>

            </div>"""

        if not purchases_html:

            purchases_html = '<div class="card" style="color:var(--text-muted);">Todavía no tienes compras.</div>'



        return _s_render("Mi mercado", f"""

        <a href="/student/marketplace" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Volver al mercado</a>

        <h1>&#128193; Mi mercado</h1>



        <h2 style="margin-top:24px;font-size:18px;">Mis publicaciones</h2>

        {listings_html}



        <h2 style="margin-top:24px;font-size:18px;">Mis compras</h2>

        {purchases_html}



        <script>

        async function deleteListing(id) {{

          if (!confirm('¿Eliminar esta publicación? Los compradores que ya pagaron mantienen el acceso.')) return;

          var r = await fetch('/api/student/marketplace/' + id + '/delete', {{ method:'DELETE' }});

          var j = await r.json();

          if (j && j.ok) mrReload();

          else alert((j && j.error) || 'No se pudo eliminar.');

        }}

        </script>

        """, active_page="student_marketplace")



    @app.route("/student/marketplace/<int:item_id>")

    def student_marketplace_item_page(item_id):

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        item = sdb.marketplace_get(item_id)

        if not item:

            return redirect(url_for("student_marketplace_page"))

        has_access = sdb.marketplace_has_access(cid, item)

        is_seller = (int(item.get("seller_id") or 0) == cid)

        try:

            from student.subscription import get_tier as _get_tier

            tier = _get_tier(cid)

        except Exception:

            tier = "free"

        is_ultimate = (tier == "ultimate")



        wallet = sdb.get_wallet(cid)

        coins = int(wallet.get("coins") or 0)

        price = int(item.get("price_coins") or 0)

        ext = (item.get("file_ext") or "").upper() or "FILE"

        size_kb = max(1, int((item.get("file_size") or 0) / 1024))



        if has_access:

            if is_seller:

                cta = '<div style="padding:12px;background:var(--bg-elev);border-radius:8px;text-align:center;color:var(--text-muted);">Esta es tu publicación.</div>'

            elif is_ultimate and not has_access:

                cta = '<div style="padding:12px;background:#7c3aed22;border:1px solid #7c3aed;border-radius:8px;text-align:center;">&#10024; Acceso Ultimate: descarga gratis.</div>'

            else:

                cta = '<div style="padding:12px;background:var(--green);color:#fff;border-radius:8px;text-align:center;font-weight:700;">&#10003; Ya tienes este archivo.</div>'

            cta += f'<a class="btn btn-primary" style="display:block;text-align:center;margin-top:10px;" href="/api/student/marketplace/{item_id}/download">&#11015; Descargar archivo completo</a>'

        elif is_ultimate:

            cta = (

                '<div style="padding:12px;background:#7c3aed22;border:1px solid #7c3aed;border-radius:8px;text-align:center;">'

                '<strong style="color:#a855f7;">&#10024; Beneficio ULTIMATE:</strong> Descarga gratis para ti.</div>'

                f'<a class="btn btn-primary" style="display:block;text-align:center;margin-top:10px;" href="/api/student/marketplace/{item_id}/download">&#11015; Descargar archivo completo</a>'

            )

        else:

            disabled = "disabled" if coins < price else ""

            label = ("Comprar por " + str(price) + " monedas") if coins >= price else f"Te faltan {price - coins} monedas"

            cta = (

                f'<div style="text-align:center;margin-bottom:8px;">Tu saldo: <strong style="color:#f59e0b;">&#129689; {coins}</strong></div>'

                f'<button class="btn btn-primary" style="width:100%;" id="buy-btn" {disabled} onclick="buyItem({item_id})">&#129689; {label}</button>'

                '<div id="buy-msg" style="margin-top:10px;text-align:center;font-size:13px;"></div>'

            )



        preview_html = _esc(item.get("preview") or "(No hay vista previa.)").replace("\n", "<br>")

        desc_html = _esc(item.get("description") or "").replace("\n", "<br>")



        return _s_render(item.get("title", "Mercado"), f"""

        <a href="/student/marketplace" style="color:var(--text-muted);font-size:13px;text-decoration:none;">&larr; Volver al mercado</a>

        <h1 style="margin:8px 0;">{_esc(item.get('title','Sin título'))}</h1>

        <div style="color:var(--text-muted);font-size:13px;margin-bottom:16px;">

          por <strong>{_esc(item.get('seller_name','Desconocido'))}</strong> &middot; {_esc(item.get('subject') or 'General')} &middot; {ext} &middot; {size_kb} KB &middot; {int(item.get('downloads') or 0)} descargas

        </div>



        <div style="display:grid;grid-template-columns:1fr 280px;gap:16px;align-items:start;">

          <div>

            {('<div class="card"><h3 style="margin-top:0;font-size:14px;color:var(--text-muted);">Descripción</h3><div>' + desc_html + '</div></div>') if item.get('description') else ''}

            <div class="card">

              <h3 style="margin-top:0;font-size:14px;color:var(--text-muted);">Vista previa</h3>

              <div style="white-space:pre-wrap;line-height:1.5;">{preview_html}</div>

              {'' if has_access else '<div style="margin-top:12px;padding:8px;background:var(--bg-elev);border-radius:6px;font-size:12px;color:var(--text-muted);text-align:center;">&#128274; El archivo completo se desbloquea después de comprarlo.</div>'}

            </div>

          </div>

          <div class="card" style="position:sticky;top:12px;">

            <div style="text-align:center;margin-bottom:12px;">

              <div style="font-size:32px;font-weight:800;color:#f59e0b;">&#129689; {price}</div>

              <div style="font-size:12px;color:var(--text-muted);">monedas</div>

            </div>

            {cta}

          </div>

        </div>



        <script>

        async function buyItem(id) {{

          var btn = document.getElementById('buy-btn');

          var msg = document.getElementById('buy-msg');

          if (btn) btn.disabled = true;

          msg.textContent = 'Procesando...';

          msg.style.color = 'var(--text-muted)';

          try {{

            var r = await fetch('/api/student/marketplace/' + id + '/buy', {{

              method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'

            }});

            var j = await r.json();

            if (j && j.ok) {{

              msg.textContent = 'Comprado. Actualizando...';

              msg.style.color = 'var(--green)';

              setTimeout(function(){{ mrReload(); }}, 500);

            }} else {{

              msg.textContent = (j && j.error) || 'No se pudo comprar.';

              msg.style.color = 'var(--red)';

              if (btn) btn.disabled = false;

            }}

          }} catch(e) {{

            msg.textContent = 'Error de red.';

            msg.style.color = 'var(--red)';

            if (btn) btn.disabled = false;

          }}

        }}

        </script>

        """, active_page="student_marketplace")



    # ── Marketplace API ─────────────────────────────────────



    @app.route("/api/student/marketplace/upload", methods=["POST"])

    def student_marketplace_upload():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        cid = _cid()

        if "file" not in request.files:

            return jsonify({"ok": False, "error": "No file uploaded."}), 400

        f = request.files["file"]

        if not f or not (f.filename or "").strip():

            return jsonify({"ok": False, "error": "No file selected."}), 400



        title = (request.form.get("title") or "").strip()

        description = (request.form.get("description") or "").strip()

        preview = (request.form.get("preview") or "").strip()

        subject = (request.form.get("subject") or "").strip()

        try:

            price = int(request.form.get("price_coins") or 0)

        except (TypeError, ValueError):

            price = 0



        import tempfile as _tempfile

        import os as _os2

        fd, tmp_path = _tempfile.mkstemp(prefix="mkpl_", suffix="_" + (f.filename[-40:] if f.filename else "upload"))

        _os2.close(fd)

        try:

            f.save(tmp_path)

            file_size = _os2.path.getsize(tmp_path)

            res = sdb.marketplace_create_listing(

                seller_id=cid,

                title=title,

                description=description,

                preview=preview,

                subject=subject,

                src_file_path=tmp_path,

                original_filename=f.filename or "upload",

                file_size=file_size,

                price_coins=price,

            )

        finally:

            try:

                if _os2.path.isfile(tmp_path):

                    _os2.remove(tmp_path)

            except Exception:

                pass

        if not res.get("ok"):

            return jsonify(res), 400

        return jsonify(res)



    @app.route("/api/student/marketplace/<int:item_id>/buy", methods=["POST"])

    def student_marketplace_buy(item_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        res = sdb.marketplace_purchase(item_id, _cid())

        if not res.get("ok"):

            return jsonify(res), 400

        return jsonify(res)



    @app.route("/api/student/marketplace/<int:item_id>/download")

    def student_marketplace_download(item_id):

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        item = sdb.marketplace_get(item_id)

        if not item:

            return jsonify({"error": "Not found"}), 404

        if not sdb.marketplace_has_access(cid, item):

            return jsonify({"error": "Purchase required"}), 403

        path = sdb.marketplace_file_path(item)

        import os as _os3

        if not _os3.path.isfile(path):

            return jsonify({"error": "File missing"}), 404

        return send_file(path, as_attachment=True, download_name=item.get("file_name") or "download")



    @app.route("/api/student/marketplace/<int:item_id>/delete", methods=["DELETE"])

    def student_marketplace_delete(item_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        res = sdb.marketplace_delete(item_id, _cid())

        if not res.get("ok"):

            return jsonify(res), 400

        return jsonify(res)



    @app.route("/student/achievements")

    def student_achievements_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        total_xp = sdb.get_total_xp(cid)

        rank_info = sdb.get_study_rank(total_xp)

        streak = sdb.get_streak_days(cid)

        badges = sdb.get_badges(cid)

        history = sdb.get_xp_history(cid, limit=20)



        # Auto-award streak/XP badges

        for key, threshold in [("streak_3", 3), ("streak_7", 7), ("streak_14", 14), ("streak_30", 30), ("streak_60", 60), ("streak_100", 100)]:

            if streak >= threshold:

                sdb.earn_badge(cid, key)

        for key, threshold in [("xp_100", 100), ("xp_500", 500), ("xp_1000", 1000), ("xp_2500", 2500), ("xp_5000", 5000)]:

            if total_xp >= threshold:

                sdb.earn_badge(cid, key)

        badges = sdb.get_badges(cid)



        pct = int(rank_info.get("progress_pct", 0) or 0)

        rank_color = rank_info.get("color", "#6366f1") or "#6366f1"

        rank_floor = int(rank_info.get("xp_floor", 0) or 0)

        rank_ceil = int(rank_info.get("xp_ceil", max(rank_floor + 1, total_xp + 1)) or (rank_floor + 1))

        rank_full_name = rank_info.get("full_name", "Unranked")

        rank_translations = {
            "Initiates": "Iniciados",
            "Apprentices": "Aprendices",
            "Scholars": "Estudiosos",
            "Researchers": "Investigadores",
            "Academics": "Académicos",
            "Masterminds": "Mentes maestras",
            "Grand Scholars": "Grandes estudiosos",
            "Legends": "Leyendas",
            "Arch Scholars": "Archisabios",
            "High Sages": "Grandes sabios",
            "Oracles of Knowledge": "Oráculos del conocimiento",
            "Unranked": "Sin rango",
        }
        for src, dst in sorted(rank_translations.items(), key=lambda item: len(item[0]), reverse=True):
            rank_full_name = rank_full_name.replace(src, dst)



        badges_html = ""

        for b in badges:

            earned_date = str(b.get("earned_at", ""))[:10]

            badges_html += f"""

            <div class="badge-card" style="text-align:center;padding:16px 12px;background:var(--card);border-radius:var(--radius);

                        border:1px solid var(--border);min-width:110px;flex:1;max-width:160px;

                        transition:transform 0.2s,box-shadow 0.2s;cursor:default;position:relative"

                 onmouseover="this.style.transform='translateY(-3px)';this.style.boxShadow='var(--shadow-md)';this.querySelector('.badge-tooltip').style.opacity='1';this.querySelector('.badge-tooltip').style.visibility='visible'"

                 onmouseout="this.style.transform='';this.style.boxShadow='';this.querySelector('.badge-tooltip').style.opacity='0';this.querySelector('.badge-tooltip').style.visibility='hidden'">

              <div style="font-size:2.2em;margin-bottom:4px">{b.get('emoji','🏅')}</div>

              <div style="font-weight:700;font-size:13px;color:var(--text)">{_esc(b.get('name',''))}</div>

              <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{_esc(b.get('desc',''))}</div>

              <div class="badge-tooltip" style="position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%);

                          background:var(--text);color:var(--bg);padding:8px 12px;border-radius:8px;font-size:12px;

                          white-space:nowrap;opacity:0;visibility:hidden;transition:opacity 0.2s;z-index:10;

                          pointer-events:none;box-shadow:0 4px 12px rgba(0,0,0,0.2)">

                <div style="font-weight:700;margin-bottom:2px">{_esc(b.get('name',''))}</div>

                <div>{_esc(b.get('desc',''))}</div>

                <div style="opacity:0.7;margin-top:3px">Conseguido: {earned_date}</div>

                <div style="position:absolute;top:100%;left:50%;transform:translateX(-50%);border:6px solid transparent;border-top-color:var(--text)"></div>

              </div>

            </div>"""



        # All possible badges with tooltips

        all_badges_html = ""

        for key, info in sdb.BADGE_DEFS.items():

            earned = any(b["badge_key"] == key for b in badges)

            opacity = "1" if earned else "0.25"

            border = "var(--primary)" if earned else "var(--border)"

            status_text = "Conseguido" if earned else "Aún no conseguido"

            status_color = "#22c55e" if earned else "#94a3b8"

            all_badges_html += f"""

            <div style="text-align:center;padding:10px 8px;opacity:{opacity};min-width:90px;flex:1;max-width:120px;

                        border:1px solid {border};border-radius:var(--radius-sm);background:var(--card);

                        transition:all 0.2s;cursor:default;position:relative"

                 onmouseover="this.style.opacity='1';this.querySelector('.badge-tooltip').style.opacity='1';this.querySelector('.badge-tooltip').style.visibility='visible'"

                 onmouseout="this.style.opacity='{opacity}';this.querySelector('.badge-tooltip').style.opacity='0';this.querySelector('.badge-tooltip').style.visibility='hidden'">

              <div style="font-size:1.6em">{info['emoji']}</div>

              <div style="font-size:11px;font-weight:600;color:var(--text);margin-top:2px">{_esc(info['name'])}</div>

              <div class="badge-tooltip" style="position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%);

                          background:var(--text);color:var(--bg);padding:8px 12px;border-radius:8px;font-size:12px;

                          white-space:nowrap;opacity:0;visibility:hidden;transition:opacity 0.2s;z-index:10;

                          pointer-events:none;box-shadow:0 4px 12px rgba(0,0,0,0.2)">

                <div style="font-weight:700;margin-bottom:2px">{_esc(info['name'])}</div>

                <div>{_esc(info['desc'])}</div>

                <div style="color:{status_color};margin-top:3px;font-weight:600">{status_text}</div>

                <div style="position:absolute;top:100%;left:50%;transform:translateX(-50%);border:6px solid transparent;border-top-color:var(--text)"></div>

              </div>

            </div>"""



        def _fmt_xp_ts(raw):
            # student_xp.created_at is either a datetime (Postgres) or an
            # ISO-ish string (SQLite 'YYYY-MM-DD HH:MM:SS'). Postgres NOW()
            # is UTC on Render — convert to the user's local tz before
            # rendering so they don't see "today's quiz" stamped with a
            # time that's hours off from their phone clock.
            if raw is None:
                return ("", "")
            try:
                from datetime import datetime as _dt, timezone as _tz
                if hasattr(raw, "strftime"):
                    dt_obj = raw
                else:
                    s = str(raw).strip()
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
                        try:
                            dt_obj = _dt.strptime(s.split("+")[0].split("Z")[0], fmt)
                            break
                        except Exception:
                            continue
                    else:
                        return (s[:10], s[11:16] if len(s) >= 16 else "")
                # Treat naive timestamps as UTC (the server stores in UTC),
                # then convert to the user's IANA tz from their profile.
                try:
                    from zoneinfo import ZoneInfo
                    from student.timezones import tz_for_country
                    from outreach.db import _fetchone as _fo
                    user_tz_name = "America/Santiago"
                    try:
                        with get_db() as _db:
                            _row = _fo(_db, "SELECT country_iso FROM clients WHERE id = %s", (cid,))
                        iso = (dict(_row).get("country_iso") if _row else "") or ""
                        if iso:
                            user_tz_name = tz_for_country(iso)
                    except Exception:
                        pass
                    if dt_obj.tzinfo is None:
                        dt_obj = dt_obj.replace(tzinfo=_tz.utc)
                    dt_obj = dt_obj.astimezone(ZoneInfo(user_tz_name))
                except Exception:
                    pass
                return (dt_obj.strftime("%Y-%m-%d"), dt_obj.strftime("%H:%M"))
            except Exception:
                return (str(raw)[:10], "")

        history_html = ""

        for h in history:

            _d, _t = _fmt_xp_ts(h.get("created_at"))
            ts_html = ""
            if _d:
                ts_html = (
                    f'<div style="color:var(--text-muted);font-size:11px;margin-top:2px">'
                    f'{_esc(_d)}{" &middot; " + _esc(_t) if _t else ""}'
                    f'</div>'
                )

            history_html += f"""

            <div style="display:flex;justify-content:space-between;align-items:flex-start;padding:8px 0;

                        border-bottom:1px solid var(--border);font-size:14px;color:var(--text);gap:12px">

              <div style="min-width:0;flex:1">
                <div style="overflow:hidden;text-overflow:ellipsis">{_esc(h.get('detail','') or h['action'])}</div>
                {ts_html}
              </div>

              <span style="color:#22c55e;font-weight:600;white-space:nowrap">+{h['xp']} XP</span>

            </div>"""



        return _s_render("Logros", f"""
        <style>
          .achievements-cd {{ max-width:900px;margin:0 auto 80px;font-family:"Plus Jakarta Sans",system-ui,sans-serif;color:#1A1A1F; }}
          .achievements-cd h2 {{ font-family:"Fraunces",Georgia,serif;font-size:34px;font-weight:600;letter-spacing:-.03em;color:#1A1A1F; }}
          .achievements-cd .ach-rank-card {{ background:linear-gradient(135deg,#FFFFFF,#F4F1EA)!important;color:#1A1A1F!important;border:1px solid #E2DCCC!important;border-radius:24px!important;box-shadow:0 1px 0 rgba(20,18,30,.04),0 18px 44px rgba(20,18,30,.08)!important; }}
          .achievements-cd .ach-stat-card,.achievements-cd .ach-badge-card,.achievements-cd .ach-activity-card {{ background:#FFFFFF!important;color:#1A1A1F!important;border:1px solid #E2DCCC!important;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04)!important; }}
          .achievements-cd [style*="background:var(--card)"] {{ background:#FFFFFF!important;color:#1A1A1F!important;border-color:#E2DCCC!important; }}
          .achievements-cd [style*="background:#0f172a"], .achievements-cd [style*="background:#1e293b"], .achievements-cd [style*="background:#111827"] {{ background:#FFFFFF!important;color:#1A1A1F!important;border:1px solid #E2DCCC!important; }}
          .achievements-cd .ach-stat-card .big {{ color:#FF7A3D!important; }}
          .achievements-cd .ach-activity-card [style*="color:#fff"], .achievements-cd [style*="color:#fff"] {{ color:#1A1A1F!important; }}
        </style>

        <div class="achievements-cd">

          <h2 style="margin-bottom:20px"><span style="font-size:1.3em">🏆</span> Logros y progreso</h2>



          <!-- Rank & XP Bar -->

          <div class="ach-rank-card" style="background:linear-gradient(135deg,{rank_color} 0%,{rank_color}cc 60%,{rank_color}99 100%);color:#fff;

                      border-radius:var(--radius);padding:28px 32px;margin-bottom:24px;text-align:center;

                      box-shadow:0 8px 32px {rank_color}55;position:relative;overflow:hidden">

            <div style="position:absolute;top:-20px;right:-20px;font-size:120px;opacity:0.08">🏆</div>

            <div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1.5px;font-weight:600">Posición</div>

            <div style="font-size:2.2em;font-weight:800;margin:6px 0;letter-spacing:-1px">{_esc(rank_full_name)}</div>

            <div style="font-size:1.4em;font-weight:600;opacity:0.95;color:#FF7A3D;">{total_xp} XP</div>

            <div style="background:rgba(255,255,255,0.2);border-radius:8px;height:10px;margin:14px auto;max-width:320px">

              <div style="background:#fff;border-radius:8px;height:10px;width:{pct}%;transition:width 0.6s ease;box-shadow:0 0 12px rgba(255,255,255,0.3)"></div>

            </div>

            <div style="font-size:13px;opacity:0.75">{total_xp - rank_floor} / {max(1, rank_ceil - rank_floor)} XP para el siguiente rango</div>

          </div>



          <!-- Streak & Badges Count -->

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:28px">

            <div class="ach-stat-card" style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);

                        padding:20px;text-align:center;transition:transform 0.2s"

                 onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform=''">

              <div style="font-size:2.2em">🔥</div>

              <div style="font-size:2.4em;font-weight:800;color:#ea580c;margin:4px 0">{streak}</div>

              <div style="font-size:13px;color:var(--text-muted);font-weight:500">Day Streak</div>

            </div>

            <div class="ach-stat-card" style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);

                        padding:20px;text-align:center;transition:transform 0.2s"

                 onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform=''">

              <div style="font-size:2.2em">🏅</div>

              <div style="font-size:2.4em;font-weight:800;color:#16a34a;margin:4px 0">{len(badges)}</div>

              <div style="font-size:13px;color:var(--text-muted);font-weight:500">Badges Earned</div>

            </div>

          </div>



          <!-- Earned Badges -->

          <h3 style="color:var(--text);margin-bottom:12px">🏅 Your Badges</h3>

          <div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:28px">

            {badges_html if badges_html else '<p style="color:var(--text-muted)">No badges yet — keep studying!</p>'}

          </div>



          <!-- All Badges -->

          <h3 style="color:var(--text);margin-bottom:12px">🎖 All Badges</h3>

          <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:28px">

            {all_badges_html}

          </div>



          <!-- XP History -->

          <h3 style="color:var(--text);margin-bottom:12px">📊 Recent Activity</h3>

          <div class="ach-activity-card" style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px">

            {history_html if history_html else '<p style="color:var(--text-muted)">No activity yet.</p>'}

          </div>

        </div>

        """, active_page="student_achievements")



    # ================================================================

    #  FEATURE 5 — Email Preferences (for daily study email)

    # ================================================================



    @app.route("/api/student/email-prefs", methods=["GET", "POST"])
    def student_email_prefs_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        if request.method == "GET":

            prefs = sdb.get_email_prefs(cid)

            if not prefs:

                return jsonify(daily_email=True, email_hour=7, timezone="America/Mexico_City",

                               university="", field_of_study="")

            return jsonify(

                daily_email=bool(prefs.get("daily_email")),

                email_hour=prefs.get("email_hour", 7),

                timezone=prefs.get("timezone", "America/Mexico_City"),

                university=prefs.get("university", ""),

                field_of_study=prefs.get("field_of_study", ""),

            )

        try:

            data = request.get_json(force=True) or {}

            sdb.upsert_email_prefs(

                cid,

                daily_email=bool(data.get("daily_email", True)),

                email_hour=int(data.get("email_hour", 7)),

                timezone=str(data.get("timezone", "America/Mexico_City"))[:64],

                university=str(data.get("university", ""))[:120],

                field_of_study=str(data.get("field_of_study", ""))[:120],

                lang=session.get("lang", "es"),

            )

            return jsonify(ok=True)

        except Exception as e:

            import logging, traceback

            logging.getLogger("student.routes").error("email-prefs save failed: %s\n%s", e, traceback.format_exc())

            return jsonify(error=f"Could not save preferences: {str(e)[:120]}"), 500



    # ================================================================

    #  FRIENDS / DUELS / DAILY QUESTS

    # ================================================================



    @app.route("/api/student/quests/today")

    def student_quests_today_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        quests = sdb.get_or_create_daily_quests(cid)

        # Attach human label from QUEST_POOL

        labels = {q["key"]: q["label"] for q in sdb.QUEST_POOL}

        out = []

        for q in quests:

            out.append({

                "id":           q["id"],

                "key":          q["quest_key"],

                "label":        labels.get(q["quest_key"], q["quest_key"]),

                "target":       int(q["target"]),

                "progress":     int(q["progress"]),

                "xp_reward":    int(q["xp_reward"]),

                "completed":    bool(q.get("completed_at")),

            })

        return jsonify(quests=out, bundle_bonus_xp=sdb.QUEST_BUNDLE_BONUS_XP)



    @app.route("/api/student/streak/status")

    def student_streak_status_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        return jsonify(

            streak=sdb.get_streak_days(cid),

            freeze=sdb.get_freeze_status(cid),

        )



    # ── Friends ─────────────────────────────────────────────



    @app.route("/student/friends")

    def student_friends_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        _lang = session.get("lang", "es")
        _fr = {
            "kicker": "SOCIAL STUDY" if _lang == "en" else "ESTUDIO SOCIAL",
            "title": "Friends<br/>and duels." if _lang == "en" else "Amigos<br/>y duelos.",
            "subtitle": "Find classmates, challenge friends, and turn focus time into pressure that actually helps." if _lang == "en" else "Encuentra compañeros, desafía amigos y convierte el tiempo de estudio en presión que de verdad ayuda.",
            "your_id": "Your ID" if _lang == "en" else "Tu ID",
            "search_ph": "Search by name, email, or #ID" if _lang == "en" else "Buscar por nombre, correo o #ID",
            "search": "Search" if _lang == "en" else "Buscar",
            "requests": "Friend requests" if _lang == "en" else "Solicitudes de amistad",
            "pending": "Pending" if _lang == "en" else "Pendientes",
            "friends": "Your friends" if _lang == "en" else "Tus amigos",
            "squad": "Squad" if _lang == "en" else "Equipo",
            "quiz_duels": "Quiz duels" if _lang == "en" else "Duelos de quiz",
            "live": "Live" if _lang == "en" else "En vivo",
            "marathons": "Study marathons" if _lang == "en" else "Maratones de estudio",
            "days7": "7 days" if _lang == "en" else "7 días",
            "active_duels": "Active duels" if _lang == "en" else "Duelos activos",
            "now": "Now" if _lang == "en" else "Ahora",
            "history": "Duel history" if _lang == "en" else "Historial de duelos",
            "archive": "Archive" if _lang == "en" else "Archivo",
            "loading": "Loading..." if _lang == "en" else "Cargando...",
            "no_pending": "No pending invitations." if _lang == "en" else "Sin invitaciones pendientes.",
            "no_active": "No active duels." if _lang == "en" else "Sin duelos activos.",
            "no_history": "No completed duels yet." if _lang == "en" else "Aún no hay duelos completados.",
            "no_matches": "No matches." if _lang == "en" else "Sin resultados.",
            "no_name": "(no name)" if _lang == "en" else "(sin nombre)",
            "add_friend": "Add friend" if _lang == "en" else "Agregar amigo",
            "friends_done": "Friends!" if _lang == "en" else "¡Amigos!",
            "requested": "Requested" if _lang == "en" else "Solicitado",
            "already": "Already" if _lang == "en" else "Ya agregado",
            "self": "Self" if _lang == "en" else "Eres tú",
            "remove_friend": "Remove this friend?" if _lang == "en" else "¿Eliminar este amigo?",
            "user": "User" if _lang == "en" else "Usuario",
            "challenge": "Challenge" if _lang == "en" else "Desafiar",
            "remove": "Remove" if _lang == "en" else "Eliminar",
            "accept": "Accept" if _lang == "en" else "Aceptar",
            "decline": "Decline" if _lang == "en" else "Rechazar",
            "cancel": "Cancel" if _lang == "en" else "Cancelar",
            "send_invite": "Send invite" if _lang == "en" else "Enviar invitación",
            "pick_format": "Pick a duel format" if _lang == "en" else "Elige un formato de duelo",
            "quiz_duel": "Quiz Duel" if _lang == "en" else "Duelo de quiz",
            "online": "online" if _lang == "en" else "en línea",
            "online_now": "Online now" if _lang == "en" else "En línea ahora",
            "offline": "offline" if _lang == "en" else "desconectado",
            "offline_unavailable": "offline — unavailable" if _lang == "en" else "desconectado — no disponible",
            "quiz_duel_desc": "Upload a study file. AI builds 10 questions. Both must be online — first to finish at the highest score wins. Tab-switch = instant loss." if _lang == "en" else "Sube un archivo de estudio. La IA crea 10 preguntas. Ambos deben estar en línea: gana quien termine primero con mejor puntaje. Cambiar de pestaña = derrota instantánea.",
            "quiz_duel_reward": "Win: +5 XP · +50 coins" if _lang == "en" else "Ganar: +5 XP · +50 coins",
            "marathon_title": "Study Marathon (7 days)" if _lang == "en" else "Maratón de estudio (7 días)",
            "marathon_desc": "Most focus minutes over the next 7 days wins. Asynchronous — they don't need to be online; they just need to accept on their friends tab to start the clock." if _lang == "en" else "Gana quien acumule más minutos de enfoque en los próximos 7 días. Es asincrónico: no necesitan estar en línea, solo aceptar en su pestaña de amigos para iniciar el reloj.",
            "marathon_reward": "Win: +8 XP · +70 coins · Tie: +3 XP · +25 coins" if _lang == "en" else "Ganar: +8 XP · +70 coins · Empate: +3 XP · +25 coins",
            "upload_quiz_file": "Upload a PDF, DOCX or TXT (max 8 MB). The AI will generate 10 multiple-choice questions." if _lang == "en" else "Sube un PDF, DOCX o TXT (máx. 8 MB). La IA generará 10 preguntas de selección múltiple.",
            "topic_ph": "Topic (optional, e.g. Cell Biology Ch. 4)" if _lang == "en" else "Tema (opcional, ej. Biología celular cap. 4)",
            "send_marathon_confirm": "Send a 7-day Study Marathon invite to " if _lang == "en" else "¿Enviar una invitación de maratón de estudio de 7 días a ",
            "send_marathon_suffix": "? They have to accept it on their friends tab before the clock starts." if _lang == "en" else "? Debe aceptarla en su pestaña de amigos antes de que empiece el reloj.",
            "marathon_sent": "Marathon invite sent! It will start once they accept on their friends tab." if _lang == "en" else "¡Invitación de maratón enviada! Empezará cuando la acepte en su pestaña de amigos.",
            "offline_quiz": "That friend is offline. Quiz duels need both players online — try a Study Marathon instead." if _lang == "en" else "Ese amigo está desconectado. Los duelos de quiz necesitan a ambos jugadores en línea; prueba una maratón de estudio.",
            "pick_file": "Pick a file." if _lang == "en" else "Elige un archivo.",
            "generating_quiz": "Generating quiz..." if _lang == "en" else "Generando quiz...",
            "failed": "Failed." if _lang == "en" else "Falló.",
            "network_error": "Network error." if _lang == "en" else "Error de red.",
            "no_friends": "No friends yet. Search above to add someone." if _lang == "en" else "Aún no tienes amigos. Busca arriba para agregar a alguien.",
            "challenged_marathon": "challenged you to a 7-day Study Marathon." if _lang == "en" else "te desafió a una maratón de estudio de 7 días.",
            "marathon_invite_meta": "Most focus minutes over the next 7 days wins. Clock starts when you accept." if _lang == "en" else "Gana quien acumule más minutos de enfoque en los próximos 7 días. El reloj empieza cuando aceptas.",
            "waiting_on": "Waiting on" if _lang == "en" else "Esperando a",
            "accept_marathon": "to accept your marathon invite." if _lang == "en" else "para que acepte tu invitación de maratón.",
            "clock_not_started": "Clock hasn't started. They need to accept on their friends tab." if _lang == "en" else "El reloj aún no empieza. Debe aceptar en su pestaña de amigos.",
            "ends": "ends" if _lang == "en" else "termina",
            "you": "You" if _lang == "en" else "Tú",
            "them": "Them" if _lang == "en" else "Rival",
            "tie": "TIE" if _lang == "en" else "EMPATE",
            "win": "WIN" if _lang == "en" else "VICTORIA",
            "loss": "LOSS" if _lang == "en" else "DERROTA",
            "challenged_you": "challenged you" if _lang == "en" else "te desafió",
            "no_topic": "No topic" if _lang == "en" else "Sin tema",
            "accept_play": "Accept & play" if _lang == "en" else "Aceptar y jugar",
            "waiting_status": "waiting on opponent" if _lang == "en" else "esperando al rival",
            "ready_status": "ready" if _lang == "en" else "listo",
            "progress_status": "in progress" if _lang == "en" else "en progreso",
            "open": "Open" if _lang == "en" else "Abrir",
            "no_quiz_duels": "No active quiz duels. Challenge a friend to start one." if _lang == "en" else "No hay duelos de quiz activos. Desafía a un amigo para empezar uno.",
            "could_not_accept": "Could not accept." if _lang == "en" else "No se pudo aceptar.",
            "decline_quiz_confirm": "Decline this quiz duel?" if _lang == "en" else "¿Rechazar este duelo de quiz?",
            "could_not_decline": "Could not decline." if _lang == "en" else "No se pudo rechazar.",
            "decline_marathon_confirm": "Decline this marathon invite?" if _lang == "en" else "¿Rechazar esta invitación de maratón?",
            "cancel_marathon_confirm": "Cancel your marathon invite?" if _lang == "en" else "¿Cancelar tu invitación de maratón?",
            "could_not_cancel": "Could not cancel." if _lang == "en" else "No se pudo cancelar.",
        }

        return _s_render("Friends and Duels" if _lang == "en" else "Amigos y Duelos", f"""

        <style>
          .friends-cd {{ max-width:1180px;margin:0 auto;padding:4px 0 42px;--ink:#1A1A1F;--muted:#6E6A60;--line:#E2DCCC;--paper:#FFFDF8;--cream:#F4F1EA;--orange:#FF7A3D;font-family:"Plus Jakarta Sans",system-ui,sans-serif;color:var(--ink); }}
          .friends-cd .serif {{ font-family:"Fraunces",Georgia,serif;font-weight:600;letter-spacing:-.045em; }}
          .fr-hero {{ display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:22px; }}
          .fr-eye {{ font-size:12px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;color:var(--orange);margin-bottom:8px; }}
          .fr-title {{ margin:0;font-size:clamp(44px,7vw,76px);line-height:.92;color:var(--ink); }}
          .fr-sub {{ margin:10px 0 0;color:var(--muted);font-size:15px;max-width:620px;line-height:1.55; }}
          .fr-id {{ display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:10px 14px;font-weight:900;box-shadow:0 1px 0 rgba(20,18,30,.04),0 10px 28px rgba(20,18,30,.06); }}
          .fr-id span {{ color:var(--orange);font-family:"Fraunces",Georgia,serif;font-size:22px;line-height:1; }}
          .fr-grid {{ display:grid;grid-template-columns:minmax(0,1.35fr) minmax(320px,.65fr);gap:18px;align-items:start; }}
          @media(max-width:980px) {{ .fr-grid {{ grid-template-columns:1fr; }} }}
          .fr-panel {{ background:#fff;border:1px solid var(--line);border-radius:22px;box-shadow:0 1px 0 rgba(20,18,30,.04),0 18px 46px rgba(20,18,30,.07);padding:24px;margin-bottom:18px;overflow:hidden; }}
          .fr-panel.alt {{ background:linear-gradient(180deg,#FFFFFF 0%,#FFF8EE 100%); }}
          .fr-panel h3 {{ margin:0;font-family:"Fraunces",Georgia,serif;font-size:25px;font-weight:600;color:var(--ink);letter-spacing:-.035em; }}
          .fr-panel-top {{ display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:14px; }}
          .fr-count {{ font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:900;color:var(--orange); }}
          .fr-search-card {{ padding:0;margin-bottom:18px; }}
          .fr-search-inner {{ padding:22px;background:linear-gradient(135deg,#FFE5D2 0%,#FFF8EE 60%,#FFFFFF 100%);border:1px solid #FFBD94;border-radius:22px; }}
          .fr-search-row {{ display:flex;gap:10px;align-items:center;flex-wrap:wrap; }}
          .fr-input {{ flex:1;min-width:240px;height:48px;border:1px solid var(--line);border-radius:999px;background:#FFFDF8;color:var(--ink);padding:0 18px;font-weight:750;outline:none;box-sizing:border-box; }}
          .fr-input:focus {{ border-color:var(--orange);box-shadow:0 0 0 3px rgba(255,122,61,.16); }}
          .fr-btn {{ border:0;border-radius:999px;background:#1A1A1F;color:#FFF8E1;padding:0 18px;height:44px;font-weight:900;box-shadow:0 4px 0 rgba(0,0,0,.16),0 18px 28px rgba(20,18,30,.16);cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:7px;white-space:nowrap; }}
          .fr-btn.orange {{ background:var(--orange);color:#fff; }}
          .fr-btn.ghost {{ background:#FFFDF8;color:#1A1A1F;border:1px solid var(--line);box-shadow:none; }}
          .fr-btn.small {{ height:34px;padding:0 12px;font-size:12px; }}
          .fr-list {{ display:flex;flex-direction:column;gap:10px; }}
          .fr-row {{ display:flex;align-items:center;justify-content:space-between;gap:14px;border:1px solid var(--line);background:#FFFDF8;border-radius:18px;padding:14px; }}
          .fr-person {{ display:flex;align-items:center;gap:12px;min-width:0; }}
          .fr-avatar {{ width:44px;height:44px;border-radius:14px;background:linear-gradient(135deg,#FF7A3D,#FFB36B);display:grid;place-items:center;color:#fff;font-weight:900;box-shadow:inset 0 1px 0 rgba(255,255,255,.3);flex-shrink:0; }}
          .fr-name {{ font-weight:900;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }}
          .fr-meta {{ color:var(--muted);font-size:12px;margin-top:3px; }}
          .fr-actions {{ display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end; }}
          .fr-empty {{ border:1px dashed var(--line);border-radius:18px;background:#FFFDF8;padding:22px;color:var(--muted);font-size:14px;text-align:center; }}
          .fr-dot {{ display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle;background:#94a3b8; }}
          .fr-dot.on {{ background:#16a34a;box-shadow:0 0 0 3px rgba(22,163,74,.14); }}
          .fr-note {{ color:var(--muted);font-size:13px;line-height:1.5; }}
          .fr-duel-stat {{ display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:850;color:var(--muted); }}
          .fr-modal-card {{ background:#fff;border:1px solid var(--line);border-radius:26px;max-width:560px;width:92%;padding:26px;box-shadow:0 28px 90px rgba(20,18,30,.22); }}
          .fr-chal-card {{ text-align:left;padding:18px;border:1px solid var(--line);border-radius:18px;background:#FFFDF8;cursor:pointer;color:var(--ink); }}
          .fr-chal-card:hover {{ border-color:var(--orange);box-shadow:0 12px 28px rgba(255,122,61,.13);transform:translateY(-1px); }}
        </style>

        <div class="friends-cd">

          <header class="fr-hero">
            <div>
              <div class="fr-eye">{_fr["kicker"]}</div>
              <h1 class="fr-title serif">{_fr["title"]}</h1>
              <p class="fr-sub">{_fr["subtitle"]}</p>
            </div>
            <div class="fr-id">{_fr["your_id"]} <span>#{cid}</span></div>
          </header>

          <section class="fr-search-card">
            <div class="fr-search-inner">
              <div class="fr-search-row">
                <input id="fr-search" class="fr-input" placeholder="{_fr["search_ph"]}">
                <button class="fr-btn" onclick="frSearch()">{_fr["search"]}</button>
              </div>
              <div id="fr-results" class="fr-list" style="margin-top:14px"></div>
            </div>
          </section>

          <div class="fr-grid">
            <main>
              <section id="fr-incoming-wrap" class="fr-panel alt" style="display:none">
                <div class="fr-panel-top"><h3>{_fr["requests"]}</h3><span class="fr-count">{_fr["pending"]}</span></div>
                <div id="fr-incoming" class="fr-list"></div>
              </section>

              <section class="fr-panel">
                <div class="fr-panel-top"><h3>{_fr["friends"]}</h3><span class="fr-count">{_fr["squad"]}</span></div>
                <div id="fr-friends" class="fr-list"><div class="fr-empty">{_fr["loading"]}</div></div>
              </section>

              <section class="fr-panel">
                <div class="fr-panel-top"><h3>{_fr["quiz_duels"]}</h3><span class="fr-count">{_fr["live"]}</span></div>
                <div id="fr-quiz-duels" class="fr-list"><div class="fr-empty">{_fr["loading"]}</div></div>
              </section>
            </main>

            <aside>
              <section class="fr-panel alt">
                <div class="fr-panel-top"><h3>{_fr["marathons"]}</h3><span class="fr-count">{_fr["days7"]}</span></div>
                <div id="fr-marathon-pending" class="fr-list"><div class="fr-empty">{_fr["no_pending"]}</div></div>
              </section>

              <section class="fr-panel">
                <div class="fr-panel-top"><h3>{_fr["active_duels"]}</h3><span class="fr-count">{_fr["now"]}</span></div>
                <div id="fr-active-duels" class="fr-list"><div class="fr-empty">{_fr["no_active"]}</div></div>
              </section>

              <section class="fr-panel">
                <div class="fr-panel-top"><h3>{_fr["history"]}</h3><span class="fr-count">{_fr["archive"]}</span></div>
                <div id="fr-history" class="fr-list"><div class="fr-empty">{_fr["no_history"]}</div></div>
              </section>
            </aside>
          </div>

        </div>

        <script>

        const ME_CID = {cid};
        const FR = {json.dumps(_fr, ensure_ascii=False)};

        function esc(s) {{ return (s||'').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]); }}
        function initials(name) {{
          const parts = String(name || 'MR').trim().split(/\s+/).filter(Boolean);
          return esc(((parts[0] || 'M')[0] || 'M') + ((parts[1] || parts[0] || 'R')[0] || 'R')).toUpperCase();
        }}

        async function frSearch() {{

          const q = document.getElementById('fr-search').value.trim();

          if (!q) return;

          const r = await fetch('/api/student/friends/search?q=' + encodeURIComponent(q)).then(r=>r.json());

          const box = document.getElementById('fr-results');

          if (!r.results || !r.results.length) {{ box.innerHTML = `<div class="fr-empty">${{FR.no_matches}}</div>`; return; }}

          box.innerHTML = r.results.map(u =>

            `<div class="fr-row">

              <div class="fr-person"><div class="fr-avatar">${{initials(u.name)}}</div><div><div class="fr-name">${{esc(u.name) || FR.no_name}}</div><div class="fr-meta">#${{u.id}}</div></div></div>

              <button class="fr-btn small ghost" onclick="frAdd(${{u.id}}, this)">${{FR.add_friend}}</button>

            </div>`).join('');

        }}

        async function frAdd(uid, btn) {{

          btn.disabled = true; btn.textContent = '...';

          const r = await fetch('/api/student/friends/add', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{friend_id: uid}})}}).then(r=>r.json());

          btn.textContent = r.status === 'accepted' ? FR.friends_done : (r.status === 'requested' ? FR.requested : (r.status === 'already' ? FR.already : FR.self));

          loadAll();

        }}

        async function frAccept(uid) {{ await frAdd(uid, {{disabled:false,textContent:''}}); }}

        async function frRemove(uid) {{

          if (!confirm(FR.remove_friend)) return;

          await fetch('/api/student/friends/remove', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{friend_id: uid}})}});

          loadAll();

        }}

        async function frChallenge(uid, uname, isOnline) {{

          openChallengeModal(uid, uname || (FR.user + ' #' + uid), isOnline);

        }}

        // ── Challenge modal (Quiz Duel vs Study Marathon) ──────────
        function openChallengeModal(uid, uname, isOnline) {{
          window.__chalOnline = !!isOnline;
          let m = document.getElementById('chal-modal');
          if (!m) {{
            m = document.createElement('div');
            m.id = 'chal-modal';
            m.style.cssText = 'position:fixed;inset:0;background:rgba(20,18,30,.42);display:flex;align-items:center;justify-content:center;z-index:9999;backdrop-filter:blur(8px);';
            document.body.appendChild(m);
          }}
          m.innerHTML = `
            <div class="fr-modal-card">
              <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:14px">
                <div>
                  <h2 class="serif" style="margin:0;font-size:30px;letter-spacing:-.04em">${{FR.challenge}} ${{esc(uname)}}</h2>
                  <div class="fr-note" style="margin-top:2px">${{FR.pick_format}}</div>
                </div>
                <button onclick="closeChallengeModal()" class="fr-btn small ghost" style="width:34px;padding:0">×</button>
              </div>

              <div id="chal-pick" style="display:flex;flex-direction:column;gap:10px">
                <button class="fr-chal-card" onclick="pickQuiz()" ${{window.__chalOnline ? '' : 'disabled style="opacity:.55;cursor:not-allowed"'}}>
                  <div style="font-size:16px;font-weight:700">🥊 ${{FR.quiz_duel}} ${{window.__chalOnline ? '<span style=&quot;font-size:11px;color:#22c55e;font-weight:600;margin-left:6px&quot;>● ' + FR.online + '</span>' : '<span style=&quot;font-size:11px;color:#94a3b8;font-weight:600;margin-left:6px&quot;>● ' + FR.offline_unavailable + '</span>'}}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px">${{FR.quiz_duel_desc}}</div>
                  <div style="font-size:11px;color:#22c55e;margin-top:6px">${{FR.quiz_duel_reward}}</div>
                </button>
                <button class="fr-chal-card" onclick="pickMarathon()">
                  <div style="font-size:16px;font-weight:700">📅 ${{FR.marathon_title}}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px">${{FR.marathon_desc}}</div>
                  <div style="font-size:11px;color:#22c55e;margin-top:6px">${{FR.marathon_reward}}</div>
                </button>
              </div>

              <div id="chal-quiz" style="display:none">
                <div style="font-size:13px;color:var(--text-muted);margin-bottom:10px">${{FR.upload_quiz_file}}</div>
                <input id="chal-topic" class="fr-input" type="text" placeholder="${{FR.topic_ph}}" style="width:100%;margin-bottom:10px;border-radius:14px">
                <input id="chal-file" type="file" accept=".pdf,.docx,.txt,.md" style="width:100%;margin-bottom:14px">
                <div id="chal-err" style="color:#ef4444;font-size:12px;margin-bottom:8px"></div>
                <div style="display:flex;gap:8px;justify-content:flex-end">
                  <button class="fr-btn small ghost" onclick="closeChallengeModal()">${{FR.cancel}}</button>
                  <button class="fr-btn small orange" id="chal-go" onclick="sendQuizDuel(${{uid}})">${{FR.send_invite}}</button>
                </div>
              </div>
            </div>`;
          window.__chalUid = uid;
          window.__chalName = uname;
        }}
        function closeChallengeModal() {{
          const m = document.getElementById('chal-modal');
          if (m) m.remove();
        }}
        function pickMarathon() {{
          if (!confirm(FR.send_marathon_confirm + window.__chalName + FR.send_marathon_suffix)) return;
          fetch('/api/student/duels/start', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{opponent_id: window.__chalUid}})}})
            .then(r=>r.json()).then(r => {{
              if (r.error) {{ alert(r.error); return; }}
              closeChallengeModal();
              alert(FR.marathon_sent);
              loadAll();
            }});
        }}
        function pickQuiz() {{
          if (!window.__chalOnline) {{ alert(FR.offline_quiz); return; }}
          document.getElementById('chal-pick').style.display = 'none';
          document.getElementById('chal-quiz').style.display = 'block';
        }}
        async function sendQuizDuel(uid) {{
          const fileEl = document.getElementById('chal-file');
          const topic = document.getElementById('chal-topic').value.trim();
          const errEl = document.getElementById('chal-err');
          errEl.textContent = '';
          if (!fileEl.files || !fileEl.files[0]) {{ errEl.textContent = FR.pick_file; return; }}
          const fd = new FormData();
          fd.append('opponent_id', uid);
          fd.append('topic', topic);
          fd.append('file', fileEl.files[0]);
          const btn = document.getElementById('chal-go');
          btn.disabled = true; btn.textContent = FR.generating_quiz;
          try {{
            const r = await fetch('/api/student/duels/quiz/create', {{method:'POST', body: fd}}).then(r=>r.json());
            if (!r.ok) {{ errEl.textContent = r.error || FR.failed; btn.disabled = false; btn.textContent = FR.send_invite; return; }}
            closeChallengeModal();
            // Take the challenger straight into the play page (it auto-starts when opponent accepts)
            mrGo('/student/duels/quiz/' + r.duel_id + '/play');
          }} catch(e) {{
            errEl.textContent = FR.network_error;
            btn.disabled = false; btn.textContent = FR.send_invite;
          }}
        }}

        let __loadingAll = false;
        async function loadAll() {{

          if (__loadingAll) return;
          __loadingAll = true;
          try {{
          const f = await fetch('/api/student/friends/list').then(r=>r.json());

          const inc = document.getElementById('fr-incoming');

          const incWrap = document.getElementById('fr-incoming-wrap');

          if (f.incoming && f.incoming.length) {{

            incWrap.style.display = 'block';

            inc.innerHTML = f.incoming.map(u =>

              `<div class="fr-row">

                <div class="fr-person"><div class="fr-avatar">${{initials(u.name)}}</div><div><div class="fr-name">${{esc(u.name)}}</div><div class="fr-meta">#${{u.id}}</div></div></div>

                <button class="fr-btn small orange" onclick="frAccept(${{u.id}})">${{FR.accept}}</button>

              </div>`).join('');

          }} else {{ incWrap.style.display = 'none'; }}

          const fl = document.getElementById('fr-friends');

          if (f.friends && f.friends.length) {{

            // Build to a local string first so concurrent loads can't interleave appends.
            // Dedupe by id in case the API ever returns duplicates.
            const seen = new Set();
            const uniq = f.friends.filter(u => {{ if (seen.has(u.id)) return false; seen.add(u.id); return true; }});
            const parts = await Promise.all(uniq.map(async u => {{
              const h2h = await fetch('/api/student/duels/h2h?friend_id=' + u.id).then(r=>r.json());
              const onlineDot = u.online
                ? `<span class="fr-dot on" title="${{FR.online_now}}"></span>`
                : `<span class="fr-dot" title="${{FR.offline}}"></span>`;
              const onlineLabel = u.online
                ? `<span style="color:#22c55e;font-size:11px;font-weight:600;margin-left:6px">${{FR.online}}</span>`
                : `<span style="color:#94a3b8;font-size:11px;margin-left:6px">${{FR.offline}}</span>`;
              return `<div class="fr-row">

                <div class="fr-person"><div class="fr-avatar">${{initials(u.name)}}</div><div>

                  <div class="fr-name">${{onlineDot}}${{esc(u.name)}} <span class="fr-meta">#${{u.id}}</span>${{onlineLabel}}</div>

                  <div class="fr-meta"><span class="fr-duel-stat">${{h2h.wins}}W</span> <span class="fr-duel-stat">${{h2h.losses}}L</span> <span class="fr-duel-stat">${{h2h.ties}}T</span></div>

                </div></div>

                <div class="fr-actions">

                  <button class="fr-btn small orange" data-uid="${{u.id}}" data-uname="${{esc(u.name||'')}}" data-online="${{u.online ? '1' : '0'}}" onclick="frChallenge(this.dataset.uid, this.dataset.uname, this.dataset.online === '1')">${{FR.challenge}}</button>

                  <button class="fr-btn small ghost" onclick="frRemove(${{u.id}})">${{FR.remove}}</button>

                </div>

              </div>`;
            }}));
            fl.innerHTML = parts.join('');

          }} else {{ fl.innerHTML = `<div class="fr-empty">${{FR.no_friends}}</div>`; }}

          // ── Marathon invites (pending) ────────────────────────
          try {{
            const mp = await fetch('/api/student/duels/marathon/pending').then(r=>r.json());
            const mbox = document.getElementById('fr-marathon-pending');
            const inc = (mp.incoming || []).map(d => `
              <div class="fr-row">
                <div>
                  <b>${{esc(d.challenger_name)}}</b> ${{FR.challenged_marathon}}
                  <div class="fr-meta">${{FR.marathon_invite_meta}}</div>
                </div>
                <div class="fr-actions">
                  <button class="fr-btn small orange" onclick="mAccept(${{d.id}})">${{FR.accept}}</button>
                  <button class="fr-btn small ghost" onclick="mDecline(${{d.id}})">${{FR.decline}}</button>
                </div>
              </div>`).join('');
            const out = (mp.outgoing || []).map(d => `
              <div class="fr-row">
                <div>
                  ${{FR.waiting_on}} <b>${{esc(d.opponent_name)}}</b> ${{FR.accept_marathon}}
                  <div class="fr-meta">${{FR.clock_not_started}}</div>
                </div>
                <button class="fr-btn small ghost" onclick="mCancel(${{d.id}})">${{FR.cancel}}</button>
              </div>`).join('');
            mbox.innerHTML = (inc + out) || `<div class="fr-empty">${{FR.no_pending}}</div>`;
          }} catch(e) {{}}

          const d = await fetch('/api/student/duels/list').then(r=>r.json());

          const ad = document.getElementById('fr-active-duels');

          if (d.active && d.active.length) {{

            ad.innerHTML = d.active.map(x => {{

              const meIsChall = x.challenger_id === ME_CID;

              const myMin = meIsChall ? x.challenger_minutes : x.opponent_minutes;

              const themMin = meIsChall ? x.opponent_minutes : x.challenger_minutes;

              const themName = meIsChall ? x.opponent_name : x.challenger_name;

              return `<div class="fr-row">

                <div><div class="fr-name">vs ${{esc(themName)}}</div><div class="fr-meta">${{FR.ends}} ${{esc(String(x.ends_at).slice(0,16))}}</div></div>

                <div class="fr-actions"><span class="fr-duel-stat">${{FR.you}}: ${{myMin}} min</span><span class="fr-duel-stat">${{FR.them}}: ${{themMin}} min</span></div>

              </div>`;

            }}).join('');

          }} else {{ ad.innerHTML = `<div class="fr-empty">${{FR.no_active}}</div>`; }}

          const hist = document.getElementById('fr-history');

          if (d.history && d.history.length) {{

            hist.innerHTML = d.history.map(x => {{

              const meIsChall = x.challenger_id === ME_CID;

              const themName = meIsChall ? x.opponent_name : x.challenger_name;

              const won = x.winner_id === ME_CID;

              const tie = !x.winner_id;

              const tag = tie ? `<span style="color:#94a3b8">${{FR.tie}}</span>` : (won ? `<span style="color:#22c55e">${{FR.win}}</span>` : `<span style="color:#ef4444">${{FR.loss}}</span>`);

              return `<div class="fr-row">

                <div class="fr-name">vs ${{esc(themName)}}</div><div>${{tag}}</div></div>`;

            }}).join('');

          }} else {{ hist.innerHTML = `<div class="fr-empty">${{FR.no_history}}</div>`; }}

          // Quiz duels (v2 — file-upload + AI)
          try {{
            const qd = await fetch('/api/student/duels/quiz/pending').then(r=>r.json());
            const qbox = document.getElementById('fr-quiz-duels');
            const incoming = (qd.pending||[]).map(x =>
              `<div class="fr-row">
                <div>
                  <div class="fr-name">${{esc(x.challenger_name)}} ${{FR.challenged_you}}</div>
                  <div class="fr-meta">${{esc(x.topic||FR.no_topic)}} · ${{esc(x.file_name||'')}}</div>
                </div>
                <div class="fr-actions">
                  <button class="fr-btn small orange" onclick="qdAccept(${{x.id}})">${{FR.accept_play}}</button>
                  <button class="fr-btn small ghost" onclick="qdDecline(${{x.id}})">${{FR.decline}}</button>
                </div>
              </div>`).join('');
            const playable = (qd.playable||[]).map(x => {{
              const meIsChall = x.challenger_id === ME_CID;
              const themName = meIsChall ? x.opponent_name : x.challenger_name;
              const labelStatus = x.status === 'pending' ? FR.waiting_status : (x.status === 'ready' ? FR.ready_status : FR.progress_status);
              return `<div class="fr-row">
                <div>
                  <div class="fr-name">vs ${{esc(themName)}}</div>
                  <div class="fr-meta">${{esc(x.topic||'')}} · ${{labelStatus}}</div>
                </div>
                <a class="fr-btn small orange" href="/student/duels/quiz/${{x.id}}/play">${{FR.open}}</a>
              </div>`;
            }}).join('');
            const inner = (incoming + playable);
            qbox.innerHTML = inner || `<div class="fr-empty">${{FR.no_quiz_duels}}</div>`;
          }} catch(e) {{}}

          }} finally {{ __loadingAll = false; }}
        }}

        async function qdAccept(id) {{
          const r = await fetch('/api/student/duels/quiz/' + id + '/accept', {{method:'POST'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || FR.could_not_accept); return; }}
          mrGo('/student/duels/quiz/' + id + '/play');
        }}
        async function qdDecline(id) {{
          if (!confirm(FR.decline_quiz_confirm)) return;
          const r = await fetch('/api/student/duels/quiz/' + id + '/decline', {{method:'POST'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || FR.could_not_decline); return; }}
          loadAll();
        }}

        async function mAccept(id) {{
          const r = await fetch('/api/student/duels/marathon/' + id + '/accept', {{method:'POST'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || FR.could_not_accept); return; }}
          loadAll();
        }}
        async function mDecline(id) {{
          if (!confirm(FR.decline_marathon_confirm)) return;
          const r = await fetch('/api/student/duels/marathon/' + id + '/decline', {{method:'POST'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || FR.could_not_decline); return; }}
          loadAll();
        }}
        async function mCancel(id) {{
          if (!confirm(FR.cancel_marathon_confirm)) return;
          const r = await fetch('/api/student/duels/marathon/' + id + '/decline', {{method:'POST'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || FR.could_not_cancel); return; }}
          loadAll();
        }}

        // Presence heartbeat — keeps the user marked online while the friends tab is open
        fetch('/api/student/presence/heartbeat', {{method:'POST'}}).catch(()=>{{}});
        setInterval(() => {{ fetch('/api/student/presence/heartbeat', {{method:'POST'}}).catch(()=>{{}}); }}, 30000);

        loadAll();
        // Auto-refresh quiz-duel pending so users see invites quickly
        setInterval(loadAll, 8000);

        </script>

        """, active_page="student_friends")



    @app.route("/api/student/friends/search")

    def student_friends_search_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        q = (request.args.get("q") or "").strip()

        results = sdb.search_users(q, exclude_client_id=cid, limit=20)

        return jsonify(results=results)



    @app.route("/api/student/friends/list")

    def student_friends_list_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        return jsonify(**sdb.list_friends(cid))


    @app.route("/api/student/presence/heartbeat", methods=["POST"])
    def student_presence_heartbeat_api():
        """Friend tab pings this every few seconds while open so others can
        see them as online. The before_request hook also touches presence on
        any authenticated request (throttled), so this is just an extra signal
        for users idling on the friends/duels page."""
        if not _logged_in():
            return jsonify(ok=False), 401
        sdb.touch_presence(_cid())
        return jsonify(ok=True)



    @app.route("/api/student/friends/add", methods=["POST"])

    def student_friends_add_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        data = request.get_json(silent=True) or {}

        try:

            fid = int(data.get("friend_id"))

        except Exception:

            return jsonify(error="Invalid friend_id"), 400

        status = sdb.add_friend(cid, fid)

        return jsonify(status=status)



    @app.route("/api/student/friends/remove", methods=["POST"])

    def student_friends_remove_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        data = request.get_json(silent=True) or {}

        try:

            fid = int(data.get("friend_id"))

        except Exception:

            return jsonify(error="Invalid friend_id"), 400

        sdb.remove_friend(cid, fid)

        return jsonify(ok=True)



    @app.route("/api/student/duels/start", methods=["POST"])
    def student_duels_start_api():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        cid = _cid()
        data = request.get_json(silent=True) or {}
        try:
            opp = int(data.get("opponent_id"))
        except Exception:
            return jsonify(error="Invalid opponent_id"), 400
        if opp == cid:
            return jsonify(error="Cannot duel yourself"), 400
        # Must be friends
        f = sdb.list_friends(cid)
        if not any(x["id"] == opp for x in f["friends"]):
            return jsonify(error="You must be friends to start a duel"), 400
        # Cap active duels with same opponent
        active = sdb.get_active_duels(cid)
        if any((d["challenger_id"] == opp or d["opponent_id"] == opp) for d in active):
            return jsonify(error="You already have an active duel with this user"), 400
        # Cap pending invites with same opponent (in either direction)
        pending = sdb.list_pending_marathons_for(cid)
        all_pending = (pending.get("incoming") or []) + (pending.get("outgoing") or [])
        if any((d["challenger_id"] == opp or d["opponent_id"] == opp) for d in all_pending):
            return jsonify(error="There's already a pending marathon invite with this user"), 400
        did = sdb.start_duel(cid, opp)
        return jsonify(ok=True, duel_id=did, status="pending")


    @app.route("/api/student/duels/marathon/pending")
    def student_duels_marathon_pending_api():
        if not _logged_in():
            return jsonify(error="Login required"), 401
        out = sdb.list_pending_marathons_for(_cid())
        # Stringify timestamps for JSON consumers
        for k in ("incoming", "outgoing"):
            for d in out.get(k, []):
                d["started_at"] = str(d.get("started_at") or "")
                d["ends_at"] = str(d.get("ends_at") or "")
        return jsonify(**out)


    @app.route("/api/student/duels/marathon/<int:duel_id>/accept", methods=["POST"])
    def student_duels_marathon_accept_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.accept_marathon_duel(duel_id, _cid()))


    @app.route("/api/student/duels/marathon/<int:duel_id>/decline", methods=["POST"])
    def student_duels_marathon_decline_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.decline_marathon_duel(duel_id, _cid()))



    @app.route("/api/student/duels/list")

    def student_duels_list_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        # Settle any past-due duels first

        try:

            sdb.settle_due_duels()

        except Exception:

            pass

        active = sdb.get_active_duels(cid)

        history = sdb.get_duel_history(cid, limit=20)

        # Live update minutes for active duels

        out_active = []

        for d in active:

            c_min = sdb._focus_minutes_between(d["challenger_id"], d["started_at"], d["ends_at"])

            o_min = sdb._focus_minutes_between(d["opponent_id"],   d["started_at"], d["ends_at"])

            d2 = dict(d)

            d2["challenger_minutes"] = c_min

            d2["opponent_minutes"]   = o_min

            d2["started_at"] = str(d2.get("started_at", ""))

            d2["ends_at"]    = str(d2.get("ends_at", ""))

            out_active.append(d2)

        out_history = []

        for d in history:

            d2 = dict(d)

            d2["started_at"] = str(d2.get("started_at", ""))

            d2["ends_at"]    = str(d2.get("ends_at", ""))

            d2["settled_at"] = str(d2.get("settled_at", ""))

            out_history.append(d2)

        return jsonify(active=out_active, history=out_history)



    @app.route("/api/student/duels/h2h")

    def student_duels_h2h_api():

        if not _logged_in():

            return jsonify(error="Login required"), 401

        cid = _cid()

        try:

            fid = int(request.args.get("friend_id"))

        except Exception:

            return jsonify(error="Invalid friend_id"), 400

        return jsonify(**sdb.get_head_to_head(cid, fid))



    # ================================================================

    #  LEADERBOARD / RANKINGS

    # ================================================================



    @app.route("/student/leaderboard")
    def student_leaderboard_page():
        if not _logged_in():
            return redirect(url_for("login"))
        # Premium hierarchical leaderboards (global / country / university / major)
        # rendered INSIDE the main MachReach student shell — same nav, same chrome.
        # Data comes from /api/academic/ranks and /api/academic/leaderboard.
        content = """
<style>
  #mr-lb-page { --lb-panel: #10172A; --lb-panel-2: #141C36; --lb-border: rgba(148,163,184,.12);
    --lb-text: #E5EAF5; --lb-muted: #8B93A7; --lb-accent: #7C9CFF; --lb-accent-2: #C084FC; }
  #mr-lb-page .lb-hero {
    background: linear-gradient(135deg, rgba(124,156,255,.12), rgba(192,132,252,.08));
    border: 1px solid var(--border); border-radius: 20px; padding: 28px 32px; margin-bottom: 24px;
    position: relative; overflow: hidden;
  }
  #mr-lb-page .lb-hero::after {
    content:""; position:absolute; inset:auto -50px -80px auto; width:300px; height:300px;
    background: radial-gradient(circle, rgba(124,156,255,.35), transparent 70%);
    filter: blur(10px); pointer-events:none;
  }
  #mr-lb-page .lb-hero h2 { margin:0 0 8px; font-size: 28px; letter-spacing:-.03em; }
  #mr-lb-page .lb-hero p  { margin:0; color: var(--text-muted); }
  #mr-lb-page .lb-rank-strip {
    display:grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 20px; position:relative; z-index:1;
  }
  #mr-lb-page .lb-rank-card {
    background: rgba(255,255,255,.02); border:1px solid var(--border);
    border-radius: 14px; padding: 14px 16px;
  }
  #mr-lb-page .lb-rank-card .label { color: var(--text-muted); font-size:12px; text-transform:uppercase; letter-spacing:.1em;}
  #mr-lb-page .lb-rank-card .rank-big { font-size: 28px; font-weight:700; letter-spacing:-.02em; margin-top:4px;}
  #mr-lb-page .lb-rank-card .of { color: var(--text-muted); font-size: 14px;}
  #mr-lb-page .lb-tabs {
    display:flex; gap:6px; background: var(--card);
    border:1px solid var(--border); border-radius: 14px; padding:6px; margin-bottom:16px;
  }
  #mr-lb-page .lb-tab {
    flex:1; padding: 10px 14px; border-radius: 10px; text-align:center; cursor:pointer;
    color: var(--text-muted); font-weight:500; transition: all .18s; user-select:none;
  }
  #mr-lb-page .lb-tab:hover { color: var(--text); }
  #mr-lb-page .lb-tab.active {
    background: linear-gradient(135deg, rgba(124,156,255,.25), rgba(192,132,252,.2));
    color: var(--text);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.08);
  }
  #mr-lb-page .lb-board {
    background: var(--card); border:1px solid var(--border); border-radius:18px; overflow:hidden;
  }
  #mr-lb-page .lb-row {
    display:grid; grid-template-columns: 56px 1fr 110px 110px;
    align-items:center; padding: 14px 20px; border-top:1px solid var(--border);
    transition: background .15s; position: relative; overflow: hidden;
  }
  /* Leaderboard flag (cosmetic). Wide stripe on the right portion of the row,
     fades in from ~25% so the rank/name on the left stay clean. No pill, no label. */
  #mr-lb-page .lb-flag-bg {
    position: absolute; inset: 0; pointer-events: none; z-index: 0;
    -webkit-mask-image: linear-gradient(to right, transparent 0%, transparent 22%, rgba(0,0,0,.6) 36%, rgba(0,0,0,1) 50%, rgba(0,0,0,1) 100%);
            mask-image: linear-gradient(to right, transparent 0%, transparent 22%, rgba(0,0,0,.6) 36%, rgba(0,0,0,1) 50%, rgba(0,0,0,1) 100%);
    opacity: .28;
  }
  #mr-lb-page .lb-row > *:not(.lb-flag-bg) { position: relative; z-index: 1; }
  @media (max-width: 600px) {
    #mr-lb-page .lb-flag-bg { opacity: .18; }
  }
  #mr-lb-page a.lb-row { display:grid; }
  #mr-lb-page .lb-row:first-child { border-top:none;}
  #mr-lb-page .lb-row:hover { background: rgba(255,255,255,.02);}
  #mr-lb-page .lb-row.me {
    background: linear-gradient(90deg, rgba(124,156,255,.15), transparent);
    border-left: 3px solid #7C9CFF;
  }
  #mr-lb-page .lb-medal { font-size: 22px; text-align:center;}
  #mr-lb-page .lb-pos { font-weight:700; text-align:center; color: var(--text-muted);}
  #mr-lb-page .lb-who { display:flex; align-items:center; gap:10px;}
  #mr-lb-page .lb-avatar {
    width:38px; height:38px; border-radius:50%;
    background: linear-gradient(135deg, #3B4A7A, #5B4694); display:flex;
    align-items:center; justify-content:center; font-weight:600; color:#fff;
  }
  #mr-lb-page .lb-xp { font-variant-numeric: tabular-nums; color: var(--text); font-weight:600;}
  #mr-lb-page .lb-pill {
    display:inline-block; padding: 3px 9px; font-size:11px; border-radius:999px;
    font-weight:600; letter-spacing:.02em;
  }
  /* Prize chip shown next to leaderboard rank when a payout is on the line. */
  #mr-lb-page .lb-prize {
    display:inline-flex; align-items:center; gap:3px;
    margin-top:4px; padding: 2px 7px; font-size:10px; line-height:1.2;
    background: linear-gradient(135deg, rgba(250,204,21,.18), rgba(234,179,8,.10));
    color: #FCD34D; border: 1px solid rgba(250,204,21,.35);
    border-radius: 999px; font-weight:700; letter-spacing:.02em;
    font-variant-numeric: tabular-nums;
  }
  #mr-lb-page .lb-medal-cell { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0; }
  #mr-lb-page .lb-podium {
    display:grid; grid-template-columns:1fr 1.22fr 1fr; gap:22px; align-items:end;
    margin: 22px 0 28px; padding: 56px 28px 32px;
    border:1px solid rgba(148,163,184,.20); border-radius:28px;
    background:
      radial-gradient(ellipse at 50% -10%, rgba(250,204,21,.30), transparent 42%),
      radial-gradient(circle at 10% 20%, rgba(124,156,255,.22), transparent 32%),
      radial-gradient(circle at 90% 28%, rgba(192,132,252,.20), transparent 32%),
      linear-gradient(160deg, rgba(15,23,42,.96), rgba(30,41,59,.82));
    position:relative; overflow:hidden;
    box-shadow:0 30px 80px rgba(2,6,23,.42), inset 0 1px 0 rgba(255,255,255,.06);
  }
  #mr-lb-page .lb-podium::before {
    content:""; position:absolute; inset:-50% -20% auto; height:80%;
    background:linear-gradient(115deg, transparent 15%, rgba(255,255,255,.12) 45%, transparent 75%);
    transform:rotate(-4deg); opacity:.55; pointer-events:none;
    animation: lbPodiumShine 9s ease-in-out infinite;
  }
  @keyframes lbPodiumShine {
    0%, 100% { transform: translateX(-8%) rotate(-4deg); opacity:.4; }
    50%      { transform: translateX(8%)  rotate(-4deg); opacity:.65; }
  }
  #mr-lb-page .lb-podium::after {
    content:""; position:absolute; left:6%; right:6%; bottom:18px; height:2px;
    background:linear-gradient(90deg, transparent, rgba(250,204,21,.85), rgba(124,156,255,.75), transparent);
    filter:blur(.4px); opacity:.95;
  }
  #mr-lb-page .lb-podium-card {
    position:relative; z-index:1; min-height:230px; display:flex; flex-direction:column;
    justify-content:flex-end; border-radius:22px; overflow:hidden; color:#fff;
    border:1px solid rgba(255,255,255,.18);
    box-shadow:0 20px 50px rgba(0,0,0,.34), inset 0 1px 0 rgba(255,255,255,.08);
    background:linear-gradient(180deg, rgba(255,255,255,.14), rgba(255,255,255,.04));
    transition: transform .25s var(--ease), box-shadow .25s var(--ease), border-color .25s var(--ease);
  }
  #mr-lb-page .lb-podium-card:hover { transform:translateY(-6px); }
  #mr-lb-page .lb-podium-card.place-1 {
    min-height:310px; transform:translateY(-14px);
    border-color:rgba(250,204,21,.65);
    background:
      radial-gradient(ellipse at 50% 0%, rgba(250,204,21,.22), transparent 60%),
      linear-gradient(180deg, rgba(255,255,255,.16), rgba(255,255,255,.04));
    box-shadow:0 30px 80px rgba(250,204,21,.22), 0 20px 50px rgba(0,0,0,.34), inset 0 1px 0 rgba(255,255,255,.14);
    animation: lbCrownFloat 4s ease-in-out infinite;
  }
  @keyframes lbCrownFloat {
    0%, 100% { transform: translateY(-14px); }
    50%      { transform: translateY(-19px); }
  }
  #mr-lb-page .lb-podium-card.place-1:hover { transform:translateY(-22px); }
  #mr-lb-page .lb-podium-card.place-2 {
    min-height:258px; border-color:rgba(226,232,240,.45);
    background:
      radial-gradient(ellipse at 50% 0%, rgba(226,232,240,.16), transparent 60%),
      linear-gradient(180deg, rgba(255,255,255,.13), rgba(255,255,255,.035));
    box-shadow:0 22px 55px rgba(148,163,184,.16), 0 16px 40px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.10);
  }
  #mr-lb-page .lb-podium-card.place-3 {
    min-height:228px; border-color:rgba(251,146,60,.55);
    background:
      radial-gradient(ellipse at 50% 0%, rgba(251,146,60,.18), transparent 60%),
      linear-gradient(180deg, rgba(255,255,255,.13), rgba(255,255,255,.035));
    box-shadow:0 20px 50px rgba(251,146,60,.18), 0 14px 36px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.10);
  }
  #mr-lb-page .lb-podium-flag { position:absolute; inset:0; opacity:.28; z-index:0; }
  #mr-lb-page .lb-podium-card::after {
    content:""; position:absolute; inset:0; z-index:0;
    background:linear-gradient(180deg, rgba(2,6,23,0) 0%, rgba(2,6,23,.85) 78%);
  }
  #mr-lb-page .lb-podium-body { position:relative; z-index:2; padding:22px 18px 18px; text-align:center; }
  #mr-lb-page .lb-crown {
    position:absolute; top:-26px; left:50%; transform:translateX(-50%); z-index:3;
    font-size:42px; filter:drop-shadow(0 8px 18px rgba(250,204,21,.55)) drop-shadow(0 2px 4px rgba(0,0,0,.5));
    animation: lbCrownBob 2.4s ease-in-out infinite;
  }
  @keyframes lbCrownBob {
    0%, 100% { transform: translate(-50%, 0) rotate(-3deg); }
    50%      { transform: translate(-50%, -4px) rotate(3deg); }
  }
  #mr-lb-page .lb-podium-medal { font-size:34px; line-height:1; margin-bottom:10px; filter:drop-shadow(0 4px 10px rgba(0,0,0,.45)); }
  #mr-lb-page .place-1 .lb-podium-medal { font-size:42px; }
  #mr-lb-page .lb-podium-avatar {
    width:72px; height:72px; border-radius:50%; margin:0 auto 12px;
    display:flex; align-items:center; justify-content:center; font-weight:800; font-size:24px;
    background:linear-gradient(135deg,#334155,#7c3aed); border:3px solid rgba(255,255,255,.28);
    box-shadow:0 0 0 5px rgba(255,255,255,.05), 0 8px 22px rgba(0,0,0,.30);
  }
  #mr-lb-page .place-1 .lb-podium-avatar {
    width:92px; height:92px; font-size:30px;
    border-color:rgba(250,204,21,.85);
    box-shadow:0 0 0 6px rgba(250,204,21,.12), 0 0 38px rgba(250,204,21,.55), 0 10px 24px rgba(0,0,0,.34);
  }
  #mr-lb-page .place-2 .lb-podium-avatar {
    width:78px; height:78px;
    border-color:rgba(226,232,240,.70);
    box-shadow:0 0 0 5px rgba(226,232,240,.10), 0 0 26px rgba(226,232,240,.30), 0 8px 22px rgba(0,0,0,.30);
  }
  #mr-lb-page .place-3 .lb-podium-avatar {
    width:74px; height:74px;
    border-color:rgba(251,146,60,.80);
    box-shadow:0 0 0 5px rgba(251,146,60,.10), 0 0 24px rgba(251,146,60,.32), 0 8px 22px rgba(0,0,0,.30);
  }
  #mr-lb-page .lb-podium-name {
    font-size:17px; font-weight:800; letter-spacing:-.02em; line-height:1.2;
    text-shadow:0 2px 8px rgba(0,0,0,.45);
  }
  #mr-lb-page .place-1 .lb-podium-name { font-size:23px; }
  #mr-lb-page .lb-podium-xp {
    margin-top:8px; font-size:13.5px; color:rgba(255,255,255,.82);
    font-variant-numeric:tabular-nums;
  }
  #mr-lb-page .place-1 .lb-podium-xp { font-size:14.5px; }
  #mr-lb-page .lb-podium-prize {
    display:inline-flex; align-items:center; justify-content:center; gap:5px;
    margin-top:14px; padding:7px 14px; border-radius:999px;
    background:linear-gradient(135deg, rgba(250,204,21,.28), rgba(245,158,11,.18));
    color:#fde68a; border:1px solid rgba(250,204,21,.55);
    font-size:12px; font-weight:900; box-shadow:0 0 26px rgba(250,204,21,.18), 0 4px 14px rgba(0,0,0,.20);
  }
  #mr-lb-page .lb-podium-prize.empty { color:rgba(255,255,255,.66); border-color:rgba(255,255,255,.16); background:rgba(255,255,255,.06); box-shadow:none; }
  /* Podium step / pedestal — gives the cards a real "podium" base */
  #mr-lb-page .lb-podium-step {
    position:relative; z-index:2; margin-top:18px; padding:12px 12px;
    border-radius:14px 14px 0 0;
    font-size:13px; font-weight:900; letter-spacing:.20em;
    text-transform:uppercase; color:rgba(15,23,42,.92);
    text-shadow:0 1px 0 rgba(255,255,255,.45);
    display:flex; align-items:center; justify-content:center;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.55), inset 0 -8px 18px rgba(0,0,0,.18);
  }
  #mr-lb-page .place-1 .lb-podium-step { height:72px; background:linear-gradient(180deg,#fef3c7 0%,#fde68a 35%,#f59e0b 100%); }
  #mr-lb-page .place-2 .lb-podium-step { height:54px; background:linear-gradient(180deg,#f8fafc 0%,#e2e8f0 35%,#94a3b8 100%); }
  #mr-lb-page .place-3 .lb-podium-step { height:42px; background:linear-gradient(180deg,#fed7aa 0%,#fdba74 35%,#c2410c 100%); }
  /* "ME" highlight — outline the user's own podium card */
  #mr-lb-page .lb-podium-card.me {
    outline: 2px solid rgba(124,156,255,.85);
    outline-offset: 2px;
  }
  #mr-lb-page .lb-empty, #mr-lb-page .lb-loading { padding: 36px 20px; text-align:center; color: var(--text-muted);}
  #mr-lb-page .lb-skeleton { display:flex; flex-direction:column; gap: 10px; padding: 14px 20px;}
  #mr-lb-page .lb-sk-row { height: 48px; background: linear-gradient(90deg, rgba(148,163,184,.06), rgba(148,163,184,.14), rgba(148,163,184,.06));
    background-size: 200% 100%; border-radius: 10px; animation: lbShimmer 1.6s infinite linear;}
  @keyframes lbShimmer { from { background-position: 0 0;} to { background-position: -200% 0;}}
  @media (max-width: 600px) {
    #mr-lb-page .lb-rank-strip { grid-template-columns: repeat(2, 1fr); }
    #mr-lb-page .lb-podium { grid-template-columns:1fr; padding:36px 18px 22px; gap:14px; }
    #mr-lb-page .lb-podium-card, #mr-lb-page .lb-podium-card.place-1, #mr-lb-page .lb-podium-card.place-2, #mr-lb-page .lb-podium-card.place-3 { min-height:220px; transform:none; animation:none; }
    #mr-lb-page .lb-podium-card.place-1:hover, #mr-lb-page .lb-podium-card:hover { transform:translateY(-3px); }
    #mr-lb-page .lb-crown { font-size:34px; top:-22px; }
    #mr-lb-page .place-1 .lb-podium-name { font-size:20px; }
    #mr-lb-page .lb-row { grid-template-columns: 40px 1fr 80px; }
    #mr-lb-page .lb-row .lb-pill-col { display:none;}
  }
  /* Claude-inspired Ranking polish: same warm paper system as the rest of the product. */
  #mr-lb-page { --lb-panel:#FFFFFF; --lb-panel-2:#FBF8F0; --lb-border:#E2DCCC; --lb-text:#1A1A1F; --lb-muted:#77756F; --lb-accent:#5B4694; --lb-accent-2:#FF7A3D; font-family:"Plus Jakarta Sans",system-ui,sans-serif; color:#1A1A1F; }
  #mr-lb-page .lb-hero { background:#FFFFFF; border-color:#E2DCCC; border-radius:28px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04); }
  #mr-lb-page .lb-hero::after { background:linear-gradient(135deg,rgba(255,122,61,.16),rgba(0,155,114,.10)); filter:blur(18px); }
  #mr-lb-page .lb-hero h2 { font-family:"Fraunces",Georgia,serif; font-size:clamp(42px,5vw,70px); line-height:.94; font-weight:600; color:#1A1A1F; }
  #mr-lb-page .lb-rank-card, #mr-lb-page .lb-controls, #mr-lb-page .lb-row { background:#FFFFFF; border-color:#E2DCCC; color:#1A1A1F; box-shadow:0 1px 0 rgba(20,18,30,.04); }
  #mr-lb-page .lb-rank-card .rank-big, #mr-lb-page .lb-xp { color:#1A1A1F; }
  #mr-lb-page .lb-tab { background:#FBF8F0; border-color:#E2DCCC; color:#5C5C66; border-radius:999px; font-weight:800; }
  #mr-lb-page .lb-tab.active { background:#1A1A1F; color:#FFF8E1; border-color:#1A1A1F; box-shadow:0 4px 0 rgba(0,0,0,.16); }
  #mr-lb-page .lb-podium { background:linear-gradient(135deg,#FFFFFF,#F4F1EA); border-color:#E2DCCC; border-radius:28px; box-shadow:0 1px 0 rgba(20,18,30,.04),0 18px 44px rgba(20,18,30,.08); }
  #mr-lb-page .lb-podium::before { opacity:.25; background:radial-gradient(circle at 50% 0%, rgba(255,122,61,.25), transparent 45%), radial-gradient(circle at 10% 100%, rgba(0,155,114,.16), transparent 40%); }
  #mr-lb-page .lb-podium-card { background:#FFFFFF; color:#1A1A1F; border-color:#E2DCCC; box-shadow:0 6px 0 rgba(20,18,30,.06),0 14px 30px rgba(20,18,30,.08); }
  #mr-lb-page .lb-podium-card.place-1 { background:linear-gradient(180deg,#FFF8E1,#FFE0A3); }
  #mr-lb-page .lb-podium-card.place-2 { background:linear-gradient(180deg,#FFFFFF,#EDE7DA); }
  #mr-lb-page .lb-podium-card.place-3 { background:linear-gradient(180deg,#FFE7D8,#FFB199); }
  #mr-lb-page .lb-podium-card::after { display:none; }
  #mr-lb-page .lb-podium-flag { opacity:.14; }
  #mr-lb-page .lb-podium-name, #mr-lb-page .lb-podium-xp { color:#1A1A1F; text-shadow:none; }
  #mr-lb-page .lb-row:hover { background:#FBF8F0; }
</style>

<div id="mr-lb-page">
  <section class="lb-hero">
    <h2>🏆 Sube en el ranking.</h2>
    <p>Tu XP se cuenta en vivo contra todos los demás estudiantes — en tu país, universidad y carrera.</p>
    <div class="lb-rank-strip" id="lbRankStrip">
      <div class="lb-rank-card"><div class="label">País</div><div class="rank-big" id="lb_r_country">—</div></div>
      <div class="lb-rank-card"><div class="label">Universidad</div><div class="rank-big" id="lb_r_university">—</div></div>
      <div class="lb-rank-card"><div class="label">Carrera</div><div class="rank-big" id="lb_r_major">—</div></div>
    </div>
  </section>

  <div class="lb-tabs" id="lbTabs">
    <div class="lb-tab active" data-scope="country">🏳️ País</div>
    <div class="lb-tab" data-scope="university">🎓 Universidad</div>
    <div class="lb-tab" data-scope="major">📚 Carrera</div>
    <div class="lb-tab" data-scope="retirement" title="Solo egresados">🏖️ Egresados</div>
  </div>

  <div class="lb-tabs" id="lbPeriodTabs" style="margin-top:-8px;">
    <div class="lb-tab active" data-period="all">🏛️ Histórico</div>
    <div class="lb-tab" data-period="month">📅 Mensual</div>
    <div class="lb-tab" data-period="week">⚡ Semanal</div>
  </div>

  <div class="lb-podium" id="lbPodium" style="display:none;"></div>

  <div class="lb-board" id="lbBoard">
    <div class="lb-skeleton">
      <div class="lb-sk-row"></div><div class="lb-sk-row"></div><div class="lb-sk-row"></div>
      <div class="lb-sk-row"></div><div class="lb-sk-row"></div>
    </div>
  </div>
</div>

<script>
(function(){
  const medal = (rank) => rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : '';
  const initials = (name) => (name||'?').split(/\\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase();
  function escapeHtml(s){return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

  // Currently-selected scope + period. Period starts at "all" = all-time.
  // Global scope is disabled while only Chile is active — defaults to country.
  const lbState = { scope: 'country', period: 'all' };

  // Monthly prize table (weekly = monthly // 2). Mirrors student/leaderboard_prizes.py.
  // Retirement scope has no payouts — left out intentionally.
  const PRIZES_MONTHLY = {
    global:     {1:500, 2:300, 3:200, 4:100, 5:50},
    country:    {1:300, 2:200, 3:100, 4: 60, 5:30},
    university: {1:150, 2:100, 3: 60, 4: 40, 5:20},
    major:      {1: 80, 2: 50, 3: 30, 4: 20, 5:10},
  };
  function prizeFor(scope, rank, period) {
    if (period !== 'week' && period !== 'month') return 0;
    const base = (PRIZES_MONTHLY[scope] || {})[rank] || 0;
    if (!base) return 0;
    return period === 'week' ? Math.floor(base/2) : base;
  }
  function t(en, es) { return document.documentElement.lang === 'es' ? es : en; }
  function leagueName(name) {
    const map = {
      'Initiate':'Iniciado',
      'Apprentice':'Aprendiz',
      'Adept':'Competente',
      'Scholar':'Académico',
      'Specialist':'Especialista',
      'Erudite':'Erudito',
      'Master':'Maestro',
      'Grandmaster':'Gran maestro',
      'Legend':'Leyenda'
    };
    return document.documentElement.lang === 'es' ? (map[name] || name) : name;
  }
  function podiumCard(r) {
    const flagBg = r.flag_css
      ? `<div class="lb-podium-flag ${r.flag_anim_class||''}" style="background:${r.flag_css};"></div>`
      : '';
    const crown = r.rank === 1 ? '<div class="lb-crown">&#128081;</div>' : '';
    const title = r.rank === 1 ? t('Champion', 'Campeón') : (r.rank === 2 ? t('Runner-up', 'Segundo lugar') : t('Third place', 'Tercer lugar'));
    const prize = prizeFor(lbState.scope, r.rank, lbState.period);
    const prizeHtml = prize > 0
      ? `<div class="lb-podium-prize" title="${t('Prize if this position holds when the period closes', 'Premio si esta posición se mantiene al cierre del período')}">&#129689; +${prize} ${t('coins', 'monedas')}</div>`
      : `<div class="lb-podium-prize empty">${t('All-time glory', 'Gloria histórica')}</div>`;
    const translatedLeague = leagueName(r.league_name);
    return `
      <a class="lb-podium-card place-${r.rank} ${r.is_you?'me':''}" href="/student/profile/${r.client_id}" style="text-decoration:none;">
        ${flagBg}
        ${crown}
        <div class="lb-podium-body">
          <div class="lb-podium-medal">${medal(r.rank)}</div>
          <div class="lb-podium-avatar">${initials(r.name)}</div>
          <div class="lb-podium-name">${r.badge_left_emoji?`<span title="${escapeHtml(r.badge_left_name||'')}" style="margin-right:5px;">${r.badge_left_emoji}</span>`:''}${escapeHtml(r.name)}${r.badge_right_emoji?`<span title="${escapeHtml(r.badge_right_name||'')}" style="margin-left:5px;">${r.badge_right_emoji}</span>`:''}</div>
          <div class="lb-podium-xp">${r.xp.toLocaleString()} XP &middot; <span style="color:${r.league_color};font-weight:800;">${escapeHtml(translatedLeague)}</span></div>
          ${prizeHtml}
        </div>
        <div class="lb-podium-step">#${r.rank} ${title}</div>
      </a>`;
  }

  async function loadRanks() {
    try {
      const r = await fetch('/api/academic/ranks?period=' + encodeURIComponent(lbState.period));
      if (!r.ok) return;
      const j = await r.json();
      const fmt = (obj) => obj ? `#${obj.rank} <span class="of">/ ${obj.total}</span>` : '—';
      document.getElementById('lb_r_country').innerHTML = fmt(j.ranks.country);
      document.getElementById('lb_r_university').innerHTML = fmt(j.ranks.university);
      document.getElementById('lb_r_major').innerHTML = fmt(j.ranks.major);
    } catch(e) { console.error(e); }
  }

  async function loadBoard() {
    const board = document.getElementById('lbBoard');
    board.innerHTML = '<div class="lb-skeleton"><div class="lb-sk-row"></div><div class="lb-sk-row"></div><div class="lb-sk-row"></div><div class="lb-sk-row"></div></div>';
    try {
      const url = '/api/academic/leaderboard?scope=' + encodeURIComponent(lbState.scope) +
                  '&period=' + encodeURIComponent(lbState.period);
      const r = await fetch(url);
      const j = await r.json();
      const rows = j.rows || [];
      if (!rows.length) {
        const emptyCopy = lbState.period === 'week'
            ? 'No XP earned in the last 7 days in this scope yet — do a focus block to land on the board!'
            : (lbState.period === 'month'
                ? 'No XP earned in the last 30 days in this scope yet.'
                : 'No one to compare against yet in this scope. Invite some friends!');
        board.innerHTML = `<div class="lb-empty">${emptyCopy}</div>`;
        document.getElementById('lbPodium').style.display = 'none';
        return;
      }
      const podium = document.getElementById('lbPodium');
      const top = rows.filter(r => r.rank <= 3);
      if (top.length) {
        const byRank = {};
        top.forEach(r => { byRank[r.rank] = r; });
        const ordered = [byRank[2], byRank[1], byRank[3]].filter(Boolean);
        podium.innerHTML = ordered.map(podiumCard).join('');
        podium.style.display = 'grid';
      } else {
        podium.style.display = 'none';
      }
      const tableRows = rows.filter(r => r.rank > 3);
      if (!tableRows.length) {
        board.innerHTML = `<div class="lb-empty">${t('The rest of the leaderboard starts at #4. No other ranked students yet.', 'El resto del ranking empieza en el #4. Todavía no hay más estudiantes rankeados.')}</div>`;
        return;
      }
      board.innerHTML = tableRows.map(r => {
        const flagBg = r.flag_css
          ? `<div class="lb-flag-bg ${r.flag_anim_class||''}" style="background:${r.flag_css};"></div>`
          : '';
        const prize = prizeFor(lbState.scope, r.rank, lbState.period);
        const prizeChip = prize > 0
          ? `<div class="lb-prize" title="Prize for finishing #${r.rank} this ${lbState.period}">🪙 +${prize}</div>`
          : '';
        return `
        <a class="lb-row ${r.is_you?'me':''}" href="/student/profile/${r.client_id}" style="color:inherit;text-decoration:none;cursor:pointer;">
          ${flagBg}
          <div class="lb-medal-cell">
            <div class="${r.rank<=3?'lb-medal':'lb-pos'}">${r.rank<=3 ? medal(r.rank) : '#'+r.rank}</div>
            ${prizeChip}
          </div>
          <div class="lb-who">
            <div class="lb-avatar">${initials(r.name)}</div>
            <div><div>${r.badge_left_emoji?`<span title="${escapeHtml(r.badge_left_name||'')}" style="margin-right:4px;">${r.badge_left_emoji}</span>`:''}${escapeHtml(r.name)}${r.badge_right_emoji?`<span title="${escapeHtml(r.badge_right_name||'')}" style="margin-left:4px;">${r.badge_right_emoji}</span>`:''}${r.is_you?' <span style="color:#7C9CFF;font-size:12px;">(you)</span>':''}</div>
                 <div class="lb-pill-col"><span class="lb-pill" style="background:${r.league_color}22;color:${r.league_color};">${escapeHtml(leagueName(r.league_name))}</span></div></div>
          </div>
          <div class="lb-xp">${r.xp.toLocaleString()} XP</div>
          <div class="lb-pill-col"><span class="lb-pill" style="background:${r.league_color}22;color:${r.league_color};">${escapeHtml(leagueName(r.league_name))}</span></div>
        </a>
      `;
      }).join('');
    } catch(e) {
      board.innerHTML = `<div class="lb-empty">No se pudo cargar. ${e}</div>`;
    }
  }

  document.getElementById('lbTabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.lb-tab');
    if (!tab) return;
    document.querySelectorAll('#lbTabs .lb-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    lbState.scope = tab.dataset.scope;
    loadBoard();
  });
  document.getElementById('lbPeriodTabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.lb-tab');
    if (!tab) return;
    document.querySelectorAll('#lbPeriodTabs .lb-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    lbState.period = tab.dataset.period;
    loadRanks();
    loadBoard();
  });

  loadRanks();
  loadBoard();
})();
</script>
"""
        return _s_render("Leaderboards", f"<style>{sdb.FLAG_ANIM_CSS}</style>" + content, active_page="student_leaderboard")



    # ─── Public student profile (read-only) ────────────────────────────
    # Anyone logged in can view another student's public stats by clicking
    # them on the leaderboard. Only PUBLIC fields are exposed (see the
    # /api/academic/user/<id> endpoint). Email is never shown.
    @app.route("/student/profile/<int:user_id>")
    def student_public_profile_page(user_id):
        if not _logged_in():
            return redirect(url_for("login"))
        is_self = (user_id == _cid())
        title = "Your Profile" if is_self else "Student Profile"
        content = """
<style>""" + sdb.BANNER_ANIM_CSS + """
  #mr-prof { --pf-card:#10172A; --pf-border:rgba(148,163,184,.12); }
  #mr-prof .pf-loading, #mr-prof .pf-error { padding:60px 20px; text-align:center; color:var(--text-muted);}
  /* Twitter-style hero: full-width banner, avatar overlapping bottom-left,
     identity strip (name + rank·XP) sits below in a flat dark band. */
  #mr-prof .pf-banner {
    height:140px; border-radius:14px 14px 0 0; position:relative; overflow:hidden;
    border:1px solid var(--border); border-bottom: none;
  }
  #mr-prof .pf-banner-fallback {
    background: linear-gradient(135deg, #06b6d4 0%, #2563eb 100%);
  }
  #mr-prof .pf-identity {
    background: #0B1220; border: 1px solid var(--border); border-top: none;
    border-radius: 0 0 14px 14px; padding: 14px 22px 18px 22px;
    position: relative;
  }
  #mr-prof .pf-avatar {
    width:72px; height:72px; border-radius:50%; flex-shrink:0;
    background: linear-gradient(135deg, #3B4A7A, #5B4694);
    display:flex; align-items:center; justify-content:center;
    color:#fff; font-size:26px; font-weight:700;
    border: 3px solid #0B1220;
    position: absolute; left: 22px; top: -36px; z-index: 2;
  }
  #mr-prof .pf-id-body { padding-top: 42px; }
  #mr-prof .pf-name { font-size:22px; font-weight:800; margin:0 0 4px; letter-spacing:-.02em; color:#fff; }
  #mr-prof .pf-rankline {
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size:13px; color: var(--text-muted); letter-spacing:.02em;
  }
  #mr-prof .pf-rankline b { color:#fff; font-weight:700; }
  #mr-prof .pf-hero {
    background: linear-gradient(135deg, rgba(124,156,255,.12), rgba(192,132,252,.08));
    border: 1px solid var(--border); border-radius: 18px; padding: 22px 26px;
    display:flex; gap:20px; align-items:center; flex-wrap:wrap; position:relative;
    margin-top: 16px;
  }
  #mr-prof .pf-meta { color:var(--text-muted); font-size:14px; display:flex; gap:14px; flex-wrap:wrap; }
  #mr-prof .pf-rank-card {
    margin-left:auto; padding:14px 20px; border-radius:14px;
    background: rgba(255,255,255,.03); border:1px solid var(--border); text-align:center;
  }
  #mr-prof .pf-rank-name { font-size:18px; font-weight:700; }
  #mr-prof .pf-rank-xp { font-size:13px; color:var(--text-muted); margin-top:4px; }
  #mr-prof .pf-grid {
    display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap:14px; margin-top:20px;
  }
  #mr-prof .pf-stat {
    background: var(--card); border:1px solid var(--border); border-radius:14px;
    padding:18px 20px;
  }
  #mr-prof .pf-stat .label { font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.1em; }
  #mr-prof .pf-stat .value { font-size:22px; font-weight:700; margin-top:6px; }
  #mr-prof .pf-section {
    margin-top:24px; background:var(--card); border:1px solid var(--border);
    border-radius:18px; padding:24px;
  }
  #mr-prof .pf-section h3 { margin:0 0 16px; font-size:18px; }
  #mr-prof .pf-badges { display:flex; flex-wrap:wrap; gap:10px; }
  #mr-prof .pf-badge {
    padding:8px 14px; border-radius:999px;
    background: rgba(124,156,255,.1); border:1px solid rgba(124,156,255,.3);
    font-size:13px; display:inline-flex; align-items:center; gap:6px;
    cursor: help; position: relative;
  }
  #mr-prof .pf-badge .pf-tip {
    position:absolute; bottom:calc(100% + 6px); left:50%; transform:translateX(-50%);
    background:#0B1220; color:#fff; font-size:12px; line-height:1.35; padding:8px 12px;
    border-radius:8px; min-width:200px; max-width:280px; text-align:left;
    box-shadow:0 8px 24px rgba(15,23,42,.4); display:none; z-index:30; pointer-events:none;
    white-space:normal;
  }
  #mr-prof .pf-badge .pf-tip b { display:block; margin-bottom:3px; font-size:13px; }
  #mr-prof .pf-badge:hover .pf-tip { display:block; }
  #mr-prof .pf-empty { color:var(--text-muted); font-size:13px; }
  #mr-prof .pf-retired-tag {
    display:inline-block; padding:4px 12px; border-radius:999px;
    background: rgba(34,197,94,.12); color:#22c55e;
    font-size:12px; font-weight:600; margin-left:8px;
  }
  @media (max-width: 600px) {
    #mr-prof .pf-hero { padding:20px; }
    #mr-prof .pf-rank-card { margin-left:0; width:100%; }
    #mr-prof .pf-name { font-size:22px; }
  }
</style>
<div id="mr-prof"><div class="pf-loading">Loading profile…</div></div>
<script>
(function(){
  var USER_ID = """ + str(int(user_id)) + """;
  var IS_SELF = """ + ("true" if is_self else "false") + """;
  function escapeHtml(s){return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function initials(name){return (name||'?').split(/\\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase();}
  function fmtMin(m){ m = m||0; if (m < 60) return m + 'm'; var h = Math.floor(m/60), r = m%60; return r ? (h+'h '+r+'m') : (h+'h'); }
  fetch('/api/academic/user/' + USER_ID).then(r => r.json()).then(p => {
    var box = document.getElementById('mr-prof');
    if (!p || p.error) { box.innerHTML = '<div class="pf-error">Profile not found.</div>'; return; }
    var country = p.country ? ((p.country.flag_emoji || '') + ' ' + escapeHtml(p.country.name || '')) : '';
    var uniName = p.university ? escapeHtml(p.university.name || '') : '<span style="color:var(--text-muted);">No university set</span>';
    var majorName = p.major ? escapeHtml(p.major.name || '') : '<span style="color:var(--text-muted);">No major set</span>';
    var retiredTag = p.is_retired ? '<span class="pf-retired-tag">🏖️ Retired</span>' : '';
    var rankColor = (p.rank && p.rank.color) || '#6366F1';
    var rankName = (p.rank && p.rank.full_name) || 'Unranked';
    var leaderPos = p.leaderboard_position;
    var posLine = (leaderPos && leaderPos.rank)
      ? '#' + leaderPos.rank + ' / ' + (leaderPos.total||'?') + ' (' + (leaderPos.scope==='retirement' ? 'Retired' : 'Global') + ')'
      : 'Unranked';
    var bio = p.bio ? '<p style="margin:14px 0 0;color:var(--text-muted);font-size:14px;line-height:1.6;">' + escapeHtml(p.bio) + '</p>' : '';
    var html = '';
    var bnr = p.banner || {};
    var bannerStyle = bnr.css ? ('background:' + bnr.css + ';') : '';
    var bannerCls = bnr.css ? ('bnr-anim-host ' + escapeHtml(bnr.anim_class || '')) : 'pf-banner-fallback';
    // Twitter-style banner with avatar overlapping the identity strip below.
    html += '<div class="pf-banner ' + bannerCls + '" style="' + bannerStyle + '"></div>';
    html += '<div class="pf-identity">';
    html +=   '<div class="pf-avatar">' + initials(p.name) + '</div>';
    html +=   '<div class="pf-id-body">';
    html +=     '<h1 class="pf-name">' + escapeHtml(p.name) + retiredTag + '</h1>';
    var rankNum = (leaderPos && leaderPos.rank) ? ('#' + leaderPos.rank) : 'Unranked';
    html +=     '<div class="pf-rankline">Rank <b>' + rankNum + '</b> &middot; <b>' + (p.xp||0).toLocaleString() + '</b> XP</div>';
    html += '</div>';
    html += '</div>';

    // Secondary hero card: country / university / major / bio + league chip
    html += '<div class="pf-hero">';
    html +=   '<div style="flex:1;min-width:200px;">';
    html +=     '<div class="pf-meta">';
    if (country)  html += '<span>' + country + '</span>';
    html +=       '<span>🎓 ' + uniName + '</span>';
    html +=       '<span>📚 ' + majorName + '</span>';
    html +=     '</div>';
    html +=     bio;
    html +=   '</div>';
    html +=   '<div class="pf-rank-card" style="border-color:' + rankColor + '44;">';
    html +=     '<div class="pf-rank-name" style="color:' + rankColor + ';">' + escapeHtml(rankName) + '</div>';
    html +=     '<div class="pf-rank-xp">' + (p.xp||0).toLocaleString() + ' XP</div>';
    html +=   '</div>';
    html += '</div>';
    html += '<div class="pf-grid">';
    html +=   '<div class="pf-stat"><div class="label">XP total</div><div class="value">' + (p.xp||0).toLocaleString() + '</div></div>';
    html +=   '<div class="pf-stat"><div class="label">Total hours studied</div><div class="value">' + ((p.total_hours||0).toFixed(1)) + 'h</div></div>';
    html +=   '<div class="pf-stat"><div class="label">Focus sessions</div><div class="value">' + (p.sessions||0).toLocaleString() + '</div></div>';
    html +=   '<div class="pf-stat"><div class="label">Leaderboard</div><div class="value">' + posLine + '</div></div>';
    html +=   '<div class="pf-stat"><div class="label">Badges</div><div class="value">' + (p.badge_count||0) + '</div></div>';
    html +=   '<div class="pf-stat"><div class="label">Status</div><div class="value">' + (p.is_retired ? '🏖️ Retired' : '⚡ Active') + '</div></div>';
    html += '</div>';
    html += '<div class="pf-section"><h3>🏆 Badges</h3>';
    if (!p.badges || !p.badges.length) {
      html += '<div class="pf-empty">No badges earned yet.</div>';
    } else {
      html += '<div class="pf-badges">';
      p.badges.forEach(b => {
        var name = escapeHtml(b.name || b.key || 'Badge');
        var desc = escapeHtml(b.desc || 'Earned badge.');
        var earned = b.earned_at ? ('<div style="margin-top:4px;color:#94a3b8;font-size:11px;">Earned ' + escapeHtml(String(b.earned_at).slice(0,10)) + '</div>') : '';
        html += '<span class="pf-badge">' + (b.icon||'🎖️') + ' ' + name +
                '<span class="pf-tip"><b>' + name + '</b>' + desc + earned + '</span></span>';
      });
      html += '</div>';
    }
    html += '</div>';
    box.innerHTML = html;
  }).catch(e => {
    document.getElementById('mr-prof').innerHTML = '<div class="pf-error">Failed to load profile.</div>';
  });
})();
</script>
"""
        return _s_render(title, content, active_page="student_leaderboard")



    @app.route("/student/leaderboard/group/<int:group_id>")

    def student_lb_group_page(group_id):

        """View a personal leaderboard group."""

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()

        group = sdb.get_lb_group(group_id)

        if not group or not sdb.is_lb_member(cid, group_id):

            return redirect(url_for("student_leaderboard_page"))

        members = sdb.get_lb_group_leaderboard(group_id)

        rows_html = ""

        top_xp = max((r["total_xp"] for r in members), default=0)

        for i, r in enumerate(members, 1):

            is_me = (r["client_id"] == cid)

            bg = "background:rgba(99,102,241,0.08);" if is_me else ""

            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")

            name_display = _esc(r["name"] or "Student")

            xp_val = int(r.get("total_xp") or 0)

            bar_pct = int(100 * xp_val / top_xp) if top_xp > 0 else 0

            rows_html += f"""

            <tr style="{bg}">

              <td style="padding:12px 16px;font-size:18px;font-weight:700;text-align:center;width:60px">{medal}</td>

              <td style="padding:12px 16px"><div style="font-weight:600;color:var(--text)">{name_display}{"  ← you" if is_me else ""}</div></td>

              <td style="padding:12px 16px;min-width:160px"><div style="background:var(--bg);border-radius:8px;height:10px;overflow:hidden"><div style="width:{bar_pct}%;height:100%;background:linear-gradient(90deg,#8b5cf6,#22c55e)"></div></div></td>

              <td style="padding:12px 16px;text-align:right;font-weight:700;color:#22c55e;font-size:16px">+{xp_val} XP</td>

            </tr>"""

        if not rows_html:

            rows_html = '<tr><td colspan="4" style="padding:32px;text-align:center;color:var(--text-muted)">No members yet!</td></tr>'

        is_owner = group["owner_id"] == cid

        # Format group creation date for the "since" label

        created_str = ""

        try:

            _ca = group.get("created_at")

            if _ca:

                created_str = _ca.strftime("%b %d, %Y") if hasattr(_ca, "strftime") else str(_ca)[:10]

        except Exception:

            created_str = ""

        return _s_render(f"Group: {_esc(group['name'])}", f"""

        <div style="max-width:800px;margin:0 auto">

          <a href="/student/leaderboard" style="color:var(--text-muted);font-size:13px;text-decoration:none">&larr; Back to Leaderboard</a>

          <div style="display:flex;justify-content:space-between;align-items:center;margin:12px 0 20px;flex-wrap:wrap;gap:12px">

            <div>

              <h2 style="margin:0">{_esc(group['name'])}</h2>

              <p style="color:var(--text-muted);font-size:13px;margin:4px 0 0">

                Invite code: <code style="background:var(--bg);padding:2px 8px;border-radius:4px">{_esc(group['invite_code'])}</code>

                <button class="btn btn-ghost btn-sm" onclick="navigator.clipboard.writeText('{_esc(group['invite_code'])}').then(function(){{alert('Copied!')}})" style="font-size:11px;padding:2px 8px">Copy</button>

              </p>

            </div>

          </div>

          <div style="background:linear-gradient(135deg,rgba(139,92,246,.12),rgba(34,197,94,.08));border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;margin-bottom:16px;font-size:13px;color:var(--text-muted)">

            &#128161; <b style="color:var(--text)">Fair-play group.</b> Everyone starts at 0 XP the moment they join. This scoreboard only counts XP earned <i>inside</i> the group &mdash; separate from the global ranking. {('Created ' + created_str) if created_str else ''}

          </div>

          <div style="background:var(--card);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden">

            <table style="width:100%;border-collapse:collapse">

              <thead><tr style="border-bottom:2px solid var(--border)">

                <th style="padding:12px 16px;text-align:center;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Place</th>

                <th style="padding:12px 16px;text-align:left;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Student</th>

                <th style="padding:12px 16px;text-align:left;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">Progress</th>

                <th style="padding:12px 16px;text-align:right;font-size:12px;text-transform:uppercase;color:var(--text-muted);letter-spacing:1px">XP&nbsp;Gained</th>

              </tr></thead>

              <tbody>{rows_html}</tbody>

            </table>

          </div>

        </div>

        """, active_page="student_leaderboard")



    # ── Personal Leaderboard API ────────────────────────────



    @app.route("/api/student/leaderboard/groups", methods=["POST"])

    def student_create_lb_group():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        name = (data.get("name") or "").strip()

        if not name or len(name) > 60:

            return jsonify({"error": "Group name required (max 60 chars)"}), 400

        # Limit to 10 groups per user

        existing = sdb.get_my_lb_groups(_cid())

        if len(existing) >= 10:

            return jsonify({"error": "Maximum 10 groups reached"}), 400

        result = sdb.create_lb_group(_cid(), name)

        return jsonify({"ok": True, "group_id": result["id"], "invite_code": result["invite_code"]})



    @app.route("/api/student/leaderboard/join", methods=["POST"])

    def student_join_lb_group():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        code = (data.get("invite_code") or "").strip()

        if not code:

            return jsonify({"error": "Invite code required"}), 400

        group = sdb.join_lb_group(_cid(), code)

        if not group:

            return jsonify({"error": "Invalid invite code"}), 404

        return jsonify({"ok": True, "group_id": group["id"]})



    @app.route("/api/student/leaderboard/leave", methods=["POST"])

    def student_leave_lb_group():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True)

        group_id = data.get("group_id")

        if not group_id:

            return jsonify({"error": "group_id required"}), 400

        # Can't leave if owner

        group = sdb.get_lb_group(group_id)

        if group and group["owner_id"] == _cid():

            return jsonify({"error": "Owners can't leave. Delete the group instead."}), 400

        sdb.leave_lb_group(_cid(), group_id)

        return jsonify({"ok": True})



    @app.route("/api/student/leaderboard/groups/<int:group_id>", methods=["DELETE"])

    def student_delete_lb_group(group_id):

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        sdb.delete_lb_group(group_id, _cid())

        return jsonify({"ok": True})



    # ── Retirement (opt out of active rankings) ──────────────

    @app.route("/api/student/retire", methods=["POST"])
    def student_retire():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        from outreach.db import get_db, _exec
        try:
            with get_db() as db:
                _exec(
                    db,
                    "UPDATE clients SET retired = 1, retired_at = CURRENT_TIMESTAMP "
                    "WHERE id = %s",
                    (_cid(),),
                )
            return jsonify({"ok": True, "retired": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/student/unretire", methods=["POST"])
    def student_unretire():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        from outreach.db import get_db, _exec
        try:
            with get_db() as db:
                _exec(
                    db,
                    "UPDATE clients SET retired = 0, retired_at = NULL WHERE id = %s",
                    (_cid(),),
                )
            return jsonify({"ok": True, "retired": False})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500



    @app.route("/student/settings", methods=["GET", "POST"])

    def student_settings_page():

        if not _logged_in():

            return redirect(url_for("login"))

        cid = _cid()



        # Handle profile update

        from outreach.db import get_client, update_client


        client = get_client(cid)

        # Retirement flag — controls whether the user appears on active leaderboards
        _is_retired = bool((client or {}).get("retired") or 0)



        if request.method == "POST":

            name = request.form.get("name", "").strip()

            business = request.form.get("business", "").strip()

            physical_address = request.form.get("physical_address", "").strip()

            if name:

                update_client(cid, name, business, physical_address)

                session["client_name"] = name

            return redirect(url_for("student_settings_page"))



        prefs = sdb.get_email_prefs(cid) or {}

        de = prefs.get("daily_email", 1)

        hour = prefs.get("email_hour", 7)

        tz = prefs.get("timezone", "America/Mexico_City")

        university = prefs.get("university", "") or ""

        field_of_study = prefs.get("field_of_study", "") or ""

        canvas_tok = sdb.get_canvas_token(cid)

        canvas_status = "Conectado" if canvas_tok else "Sin conectar"

        canvas_color = "#10B981" if canvas_tok else "#EF4444"

        # ── Translations ──

        _lang = session.get("lang", "es")

        _ES = {

            "Settings": "Configuración",

            "Profile": "Perfil",

            "Name": "Nombre",

            "Email": "Correo electrónico",

            "Email cannot be changed.": "El correo no se puede cambiar.",

            "Save Changes": "Guardar cambios",

            "University & Studies": "Universidad y Estudios",

            "Set your university and field of study to appear on the leaderboard and connect with classmates.": "Define tu universidad y carrera para aparecer en la clasificación y conectarte con compañeros.",

            "University": "Universidad",

            "Field of Study": "Carrera",

            "View Leaderboard": "Ver clasificación",

            "Canvas LMS": "Canvas LMS",

            "Conectado": "Conectado",

            "Sin conectar": "No conectado",

            "Connect your Canvas LMS to sync courses, exams, and study materials.": "Conecta tu Canvas LMS para sincronizar cursos, exámenes y material de estudio.",

            "Manage Connection": "Administrar conexión",

            "Conectar Canvas": "Conectar Canvas",

            "Theme": "Tema",

            "Personalize how the app looks. Your choice is saved on this device.": "Personaliza cómo se ve la app. Tu elección se guarda en este dispositivo.",

            "Click any theme to switch instantly. Your choice is saved automatically.": "Haz clic en cualquier tema para cambiar al instante. Se guarda automáticamente.",

            "Daily Study Email": "Correo de estudio diario",

            "Get a morning email with your study plan, upcoming exams, and weak topics to review.": "Recibe un correo matutino con tu plan de estudio, exámenes próximos y temas débiles para repasar.",

            "Enable daily study email": "Habilitar correo diario de estudio",

            "Send at (hour)": "Enviar a (hora)",

            "Timezone": "Zona horaria",

            "Save Preferences": "Guardar preferencias",

            "Interactive Tutorial": "Tutorial interactivo",

            "Replay the guided walkthrough to rediscover all the features available to you.": "Vuelve a ver el recorrido guiado para redescubrir todas las funciones disponibles.",

            "Restart Tutorial": "Reiniciar tutorial",

            "Account Security": "Seguridad de la cuenta",

            "Your account is secure": "Tu cuenta está segura",

            "Password protected with bcrypt encryption. You can change your password below if needed.": "Protegida con cifrado bcrypt. Puedes cambiar tu contraseña abajo si lo necesitas.",

            "Change password": "Cambiar contraseña",

            "(optional)": "(opcional)",

            "Current Password": "Contraseña actual",

            "New Password": "Nueva contraseña",

            "Confirm Password": "Confirmar contraseña",

            "Minimum 6 characters.": "Mínimo 6 caracteres.",

            "Update Password": "Actualizar contraseña",

            "Danger Zone": "Zona de peligro",

            "Permanently delete your account and all associated data (courses, exams, notes, flashcards, quizzes, chat history, XP, badges). This action <strong>cannot be undone</strong>.": "Elimina permanentemente tu cuenta y todos los datos asociados (cursos, exámenes, notas, flashcards, quizzes, historial de chat, XP, insignias). Esta acción <strong>no se puede deshacer</strong>.",

            "Delete My Account": "Eliminar mi cuenta",

            "Type": "Escribe",

            "to confirm:": "para confirmar:",

            "Permanently Delete Account": "Eliminar cuenta permanentemente",

            "Saved!": "¡Guardado!",

            "Saving...": "Guardando...",

            "Failed": "Falló",

            "Connection error": "Error de conexión",

            "Error saving.": "Error al guardar.",

            "Retirement": "Egreso",

            "Retired": "Egresado",

            "You are currently retired from active rankings. Your name only appears on the Retirement leaderboard. You can come back at any time.": "Actualmente estás egresado de los rankings activos. Tu nombre solo aparece en el ranking de egresados. Puedes volver cuando quieras.",

            "Return to active rankings": "Volver a los rankings activos",

            "When you finish your studies, retire to leave the active rankings with honor. Your XP is preserved and you'll appear on the Retirement leaderboard alongside other graduates. You can return at any time.": "Cuando termines tus estudios, egresa para dejar los rankings activos con honor. Tu XP se preserva y aparecerás en el ranking de egresados junto a otros graduados. Puedes volver cuando quieras.",

            "Retire from active rankings": "Egresar de los rankings activos",

            "Are you sure? You will be removed from the global, country, university and major leaderboards.": "¿Estás seguro? Te quitaremos de los rankings global, país, universidad y carrera.",

            "Yes, retire me": "Sí, egresar",

            "Retiring...": "Egresando...",

            "Manage Connection": "Administrar conexión",

            "Academic Profile": "Perfil académico",

            "Used to rank you on the country, university, and major leaderboards. You can change these at any time.": "Se usa para clasificarte en los rankings de país, universidad y carrera. Puedes cambiarlo cuando quieras.",

            "Save Academic Profile": "Guardar perfil académico",

            "Country": "País",

            "University": "Universidad",

            "Major": "Carrera",

        }

        def _T(s):

            return _ES.get(s, s) if _lang == "es" else s

        return _s_render("Settings", f"""

        <div>

          <h2 style="margin-bottom:24px">⚙️ {_T("Settings")}</h2>



          <!-- Profile -->

          <div class="card">

            <div class="card-header"><h2>👤 {_T("Profile")}</h2></div>

            <form method="post">

              <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">

                <div>

                  <label>{_T("Name")}</label>

                  <input name="name" value="{_esc(client.get('name','') if client else '')}" required class="edit-input">

                </div>

                <div>

                  <label>{_T("Email")}</label>

                  <input value="{_esc(client.get('email','') if client else '')}" disabled class="edit-input" style="background:var(--border-light);color:var(--text-muted);cursor:not-allowed">

                  <p style="font-size:11px;color:var(--text-muted);margin-top:6px">{_T("Email cannot be changed.")}</p>

                </div>

              </div>

              <button class="btn btn-primary" type="submit" style="margin-top:14px">{_T("Save Changes")}</button>

            </form>

          </div>



          <!-- Academic Profile (country / university / major) -->
          <div class="card" style="margin-top:16px;" id="acad-profile-card">
            <div class="card-header"><h2>&#127891; {_T("Academic Profile")}</h2></div>
            <div style="padding:20px;">
              <p style="font-size:13px;color:var(--text-muted);margin:0 0 16px;">{_T("Used to rank you on the country, university, and major leaderboards. You can change these at any time.")}</p>

              <div id="acad-current" style="display:none;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 16px;margin-bottom:16px;font-size:13px;color:var(--text-muted);">
                <div><strong style="color:var(--text);">{_T("Country")}:</strong> <span id="acad-cur-country">&mdash;</span></div>
                <div><strong style="color:var(--text);">{_T("University")}:</strong> <span id="acad-cur-uni">&mdash;</span></div>
                <div><strong style="color:var(--text);">{_T("Major")}:</strong> <span id="acad-cur-major">&mdash;</span></div>
              </div>

              <div style="display:grid;gap:12px;">
                <div>
                  <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">{_T("Country")}</label>
                  <select id="acad-country" class="input" style="width:100%;"><option value="">{_T("Cargando...")}</option></select>
                </div>
                <div>
                  <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">{_T("University")}</label>
                  <input id="acad-uni-search" class="input" type="text" placeholder="{_T('Type to search your university...')}" autocomplete="off" style="width:100%;" disabled>
                  <div id="acad-uni-results" style="display:none;max-height:180px;overflow-y:auto;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);margin-top:6px;"></div>
                  <div id="acad-uni-selected" style="display:none;margin-top:8px;padding:10px 14px;background:rgba(99,102,241,.08);border:1px solid var(--primary);border-radius:var(--radius-sm);font-size:13px;align-items:center;justify-content:space-between;gap:10px;">
                    <span id="acad-uni-selected-name"></span>
                    <button type="button" onclick="acadClearUni()" class="btn btn-ghost btn-sm" style="padding:4px 10px;">&times;</button>
                  </div>
                </div>
                <div>
                  <label style="display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;">{_T("Major")}</label>
                  <input id="acad-major-search" class="input" type="text" placeholder="{_T('Type to search your major...')}" autocomplete="off" style="width:100%;" disabled>
                  <div id="acad-major-results" style="display:none;max-height:180px;overflow-y:auto;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);margin-top:6px;"></div>
                  <div id="acad-major-selected" style="display:none;margin-top:8px;padding:10px 14px;background:rgba(99,102,241,.08);border:1px solid var(--primary);border-radius:var(--radius-sm);font-size:13px;align-items:center;justify-content:space-between;gap:10px;">
                    <span id="acad-major-selected-name"></span>
                    <button type="button" onclick="acadClearMajor()" class="btn btn-ghost btn-sm" style="padding:4px 10px;">&times;</button>
                  </div>
                </div>
              </div>

              <div style="margin-top:16px;display:flex;gap:10px;align-items:center;">
                <button onclick="acadSave()" id="acad-save-btn" class="btn btn-primary btn-sm" disabled>&#128190; {_T("Save Academic Profile")}</button>
                <span id="acad-status" style="font-size:13px;color:var(--text-muted);"></span>
              </div>
            </div>
          </div>

          <script>
          (function(){{
            var acState = {{ country: "", universityId: null, universityName: "", majorId: null, majorName: "" }};
            var $ = function(id){{ return document.getElementById(id); }};
            var debounceTimer = null;

            function setStatus(msg, isErr){{
              var el = $('acad-status');
              el.textContent = msg || '';
              el.style.color = isErr ? 'var(--danger,#ef4444)' : 'var(--text-muted)';
            }}
            function refreshSaveBtn(){{
              $('acad-save-btn').disabled = !(acState.country && acState.universityId && acState.majorId);
            }}

            Promise.all([
              fetch('/api/academic/countries').then(function(r){{ return r.json(); }}).catch(function(){{ return {{countries:[]}}; }}),
              fetch('/api/academic/profile').then(function(r){{ return r.json(); }}).catch(function(){{ return {{}}; }})
            ]).then(function(results){{
              var countries = (results[0] && results[0].countries) || [];
              var prof = results[1] || {{}};

              var sel = $('acad-country');
              sel.innerHTML = '<option value="">- {_T("Select a country")} -</option>' +
                countries.map(function(c){{
                  var iso = c.iso_code || c.iso || '';
                  var flag = c.flag_emoji || c.flag || '';
                  return '<option value="' + iso + '">' + flag + ' ' + c.name + '</option>';
                }}).join('');
              sel.disabled = false;

              if (prof.country_iso) {{
                sel.value = prof.country_iso;
                acState.country = prof.country_iso;
                $('acad-uni-search').disabled = false;
                $('acad-major-search').disabled = false;
              }}
              if (prof.university && prof.university.id) {{
                acState.universityId = prof.university.id;
                acState.universityName = prof.university.name;
                $('acad-uni-selected-name').textContent = prof.university.name;
                $('acad-uni-selected').style.display = 'flex';
                $('acad-uni-search').style.display = 'none';
              }}
              if (prof.major && prof.major.id) {{
                acState.majorId = prof.major.id;
                acState.majorName = prof.major.name;
                $('acad-major-selected-name').textContent = prof.major.name;
                $('acad-major-selected').style.display = 'flex';
                $('acad-major-search').style.display = 'none';
              }}
              if (prof.country_iso || prof.university || prof.major) {{
                var countryName = '';
                for (var i=0; i<countries.length; i++) {{ var ci=countries[i]; if ((ci.iso_code||ci.iso) === prof.country_iso) {{ countryName = (ci.flag_emoji||ci.flag||'') + ' ' + ci.name; break; }} }}
                $('acad-cur-country').textContent = countryName || (prof.country_iso || '-');
                $('acad-cur-uni').textContent = (prof.university && prof.university.name) || '-';
                $('acad-cur-major').textContent = (prof.major && prof.major.name) || '-';
                $('acad-current').style.display = 'block';
              }}
              refreshSaveBtn();
            }});

            $('acad-country').addEventListener('change', function(e){{
              acState.country = e.target.value;
              $('acad-uni-search').disabled = !acState.country;
              $('acad-major-search').disabled = !acState.country;
              if (acState.universityName) {{ acadClearUni(); }}
              refreshSaveBtn();
            }});

            $('acad-uni-search').addEventListener('input', function(e){{
              var q = e.target.value.trim();
              clearTimeout(debounceTimer);
              if (q.length < 2 || !acState.country) {{ $('acad-uni-results').style.display = 'none'; return; }}
              debounceTimer = setTimeout(function(){{
                fetch('/api/academic/universities?country=' + encodeURIComponent(acState.country) + '&q=' + encodeURIComponent(q))
                  .then(function(r){{ return r.json(); }})
                  .then(function(j){{
                    var unis = (j && j.universities) || [];
                    var box = $('acad-uni-results');
                    if (!unis.length) {{
                      box.innerHTML = '<div style="padding:12px 14px;font-size:13px;color:var(--text-muted);">{_T("No universities found.")} <a href="#" id="acad-uni-create" style="color:var(--primary);">{_T("Add")} \\u201C' + escapeHtml(q) + '\\u201D</a></div>';
                      box.style.display = 'block';
                      var addLink = document.getElementById('acad-uni-create');
                      if (addLink) addLink.addEventListener('click', function(ev){{
                        ev.preventDefault();
                        fetch('/api/academic/universities', {{
                          method:'POST', headers:{{'Content-Type':'application/json'}},
                          body: JSON.stringify({{ name: q, country_iso: acState.country }})
                        }}).then(function(r){{ return r.json(); }}).then(function(j2){{
                          if (j2 && j2.university) acadPickUni(j2.university);
                        }});
                      }});
                      return;
                    }}
                    box.innerHTML = unis.map(function(u){{
                      return '<div class="acad-pick" data-id="' + u.id + '" data-name="' + escapeAttr(u.name) + '" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);font-size:13px;">' + escapeHtml(u.name) + '</div>';
                    }}).join('');
                    box.style.display = 'block';
                    Array.prototype.forEach.call(box.querySelectorAll('.acad-pick'), function(el){{
                      el.addEventListener('click', function(){{
                        acadPickUni({{ id: parseInt(el.dataset.id, 10), name: el.dataset.name }});
                      }});
                      el.addEventListener('mouseenter', function(){{ el.style.background = 'rgba(99,102,241,.08)'; }});
                      el.addEventListener('mouseleave', function(){{ el.style.background = ''; }});
                    }});
                  }});
              }}, 250);
            }});

            function runMajorSearch(q){{
                var url = '/api/academic/majors?q=' + encodeURIComponent(q || '');
                if (acState.universityId) url += '&university_id=' + acState.universityId;
                fetch(url).then(function(r){{ return r.json(); }}).then(function(j){{
                  var majors = (j && j.majors) || [];
                  var box = $('acad-major-results');
                  if (!majors.length && !q) {{
                    box.style.display = 'none';
                    return;
                  }}
                  if (!majors.length) {{
                    box.innerHTML = '<div style="padding:12px 14px;font-size:13px;color:var(--text-muted);">{_T("No majors found.")} <a href="#" id="acad-major-create" style="color:var(--primary);">{_T("Add")} \\u201C' + escapeHtml(q) + '\\u201D</a></div>';
                    box.style.display = 'block';
                    var addLink = document.getElementById('acad-major-create');
                    if (addLink) addLink.addEventListener('click', function(ev){{
                      ev.preventDefault();
                      if (!acState.universityId) {{ setStatus('{_T("Pick a university first before adding a new major.")}', true); return; }}
                      fetch('/api/academic/majors', {{
                        method:'POST', headers:{{'Content-Type':'application/json'}},
                        body: JSON.stringify({{ name: q, university_id: acState.universityId }})
                      }}).then(function(r){{ return r.json(); }}).then(function(j2){{
                        if (j2 && j2.major) acadPickMajor(j2.major);
                      }});
                    }});
                    return;
                  }}
                  box.innerHTML = majors.map(function(m){{
                    return '<div class="acad-pick" data-id="' + m.id + '" data-name="' + escapeAttr(m.name) + '" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);font-size:13px;">' + escapeHtml(m.name) + '</div>';
                  }}).join('');
                  box.style.display = 'block';
                  Array.prototype.forEach.call(box.querySelectorAll('.acad-pick'), function(el){{
                    el.addEventListener('click', function(){{
                      acadPickMajor({{ id: parseInt(el.dataset.id, 10), name: el.dataset.name }});
                    }});
                    el.addEventListener('mouseenter', function(){{ el.style.background = 'rgba(99,102,241,.08)'; }});
                    el.addEventListener('mouseleave', function(){{ el.style.background = ''; }});
                  }});
                }});
            }}  // end runMajorSearch

            // Trigger major search on input (debounced) and on focus when a
            // university is already selected, so users instantly see the full
            // catalog of that school without having to type.
            $('acad-major-search').addEventListener('input', function(e){{
              clearTimeout(window.__majorSearchT);
              window.__majorSearchT = setTimeout(function(){{
                runMajorSearch(e.target.value || '');
              }}, 250);
            }});
            $('acad-major-search').addEventListener('focus', function(){{
              if (acState.universityId) runMajorSearch('');
            }});

            function acadPickUni(u){{
              acState.universityId = u.id;
              acState.universityName = u.name;
              $('acad-uni-selected-name').textContent = u.name;
              $('acad-uni-selected').style.display = 'flex';
              $('acad-uni-results').style.display = 'none';
              $('acad-uni-search').style.display = 'none';
              $('acad-uni-search').value = '';
              refreshSaveBtn();
            }}
            function acadPickMajor(m){{
              acState.majorId = m.id;
              acState.majorName = m.name;
              $('acad-major-selected-name').textContent = m.name;
              $('acad-major-selected').style.display = 'flex';
              $('acad-major-results').style.display = 'none';
              $('acad-major-search').style.display = 'none';
              $('acad-major-search').value = '';
              refreshSaveBtn();
            }}
            window.acadClearUni = function(){{
              acState.universityId = null; acState.universityName = "";
              $('acad-uni-selected').style.display = 'none';
              $('acad-uni-search').style.display = 'block';
              $('acad-uni-search').value = '';
              refreshSaveBtn();
            }};
            window.acadClearMajor = function(){{
              acState.majorId = null; acState.majorName = "";
              $('acad-major-selected').style.display = 'none';
              $('acad-major-search').style.display = 'block';
              $('acad-major-search').value = '';
              refreshSaveBtn();
            }};
            window.acadSave = function(){{
              if (!(acState.country && acState.universityId && acState.majorId)) return;
              setStatus('{_T("Saving...")}');
              $('acad-save-btn').disabled = true;
              fetch('/api/academic/profile', {{
                method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{
                  country_iso: acState.country,
                  university_id: acState.universityId,
                  major_id: acState.majorId
                }})
              }}).then(function(r){{ return r.json().then(function(j){{ return {{ ok: r.ok, body: j }}; }}); }})
                .then(function(res){{
                  if (!res.ok || !res.body.ok) {{
                    setStatus(res.body.error || '{_T("Save failed.")}', true);
                    $('acad-save-btn').disabled = false;
                    return;
                  }}
                  setStatus('{_T("Saved!")} \\u2713');
                  $('acad-cur-country').textContent = $('acad-country').selectedOptions[0].textContent;
                  $('acad-cur-uni').textContent = acState.universityName;
                  $('acad-cur-major').textContent = acState.majorName;
                  $('acad-current').style.display = 'block';
                  $('acad-save-btn').disabled = false;
                }}).catch(function(){{
                  setStatus('{_T("Save failed.")}', true);
                  $('acad-save-btn').disabled = false;
                }});
            }};

            function escapeHtml(s){{ return (s||'').replace(/[&<>"']/g, function(c){{ return ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]; }}); }}
            function escapeAttr(s){{ return escapeHtml(s).replace(/"/g, '&quot;'); }}
          }})();
          </script>




          <!-- Canvas Connection -->

          <div class="card">

            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">

              <h2>🔗 {_T("Canvas LMS")}</h2>

              <span style="color:{canvas_color};font-weight:600;font-size:13px">● {_T(canvas_status)}</span>

            </div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:12px">

              {_T("Connect your Canvas LMS to sync courses, exams, and study materials.")}

            </p>

            <a href="/student/canvas-settings" class="btn btn-outline btn-sm">{_T("Manage Connection") if canvas_tok else _T("Conectar Canvas")}</a>

          </div>

          <!-- Theme selector removed: MachReach now uses one Claude-aligned design system. -->

          <div class="card" style="display:none" aria-hidden="true">

            <div class="card-header"><h2>🎨 {_T("Theme")}</h2></div>

            <p style="color:var(--text-muted);font-size:14px;margin-bottom:14px">

              {_T("Personalize how the app looks. Your choice is saved on this device.")}

            </p>

            <div id="theme-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px">

              <button type="button" class="theme-chip" data-theme="default" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#0f172a;color:#fff;text-align:left"><div style="font-weight:700">Default</div><div style="font-size:11px;opacity:.7">Indigo / Slate</div></button>

              <button type="button" class="theme-chip" data-theme="midnight" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#050816;color:#e2e8f0;text-align:left"><div style="font-weight:700">Midnight</div><div style="font-size:11px;opacity:.7">Deep black</div></button>

              <button type="button" class="theme-chip" data-theme="forest" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#0b2018;color:#d1fae5;text-align:left"><div style="font-weight:700">Forest</div><div style="font-size:11px;opacity:.7">Calm green</div></button>

              <button type="button" class="theme-chip" data-theme="ocean" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#082f49;color:#e0f2fe;text-align:left"><div style="font-weight:700">Ocean</div><div style="font-size:11px;opacity:.7">Deep blue</div></button>

              <button type="button" class="theme-chip" data-theme="rose" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#3f0a1a;color:#fecdd3;text-align:left"><div style="font-weight:700">Rose</div><div style="font-size:11px;opacity:.7">Warm crimson</div></button>

              <button type="button" class="theme-chip" data-theme="sunset" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:linear-gradient(135deg,#7c2d12,#ea580c);color:#fff;text-align:left"><div style="font-weight:700">Sunset</div><div style="font-size:11px;opacity:.7">Orange / amber</div></button>

              <button type="button" class="theme-chip" data-theme="mono" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#111;color:#fff;text-align:left"><div style="font-weight:700">Mono</div><div style="font-size:11px;opacity:.7">Pure black &amp; white</div></button>

              <button type="button" class="theme-chip" data-theme="light" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f8fafc;color:#111827;text-align:left"><div style="font-weight:700">Light</div><div style="font-size:11px;opacity:.7">Clean &amp; bright</div></button>

              <button type="button" class="theme-chip" data-theme="lavender" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f5f3ff;color:#3b0764;text-align:left"><div style="font-weight:700">Lavender</div><div style="font-size:11px;opacity:.7">Soft purple</div></button>

              <button type="button" class="theme-chip" data-theme="mint" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f0fdf4;color:#14532d;text-align:left"><div style="font-weight:700">Mint</div><div style="font-size:11px;opacity:.7">Fresh pastel green</div></button>

              <button type="button" class="theme-chip" data-theme="peach" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fff7ed;color:#7c2d12;text-align:left"><div style="font-weight:700">Peach</div><div style="font-size:11px;opacity:.7">Warm pastel orange</div></button>

              <button type="button" class="theme-chip" data-theme="sky" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#f0f9ff;color:#0c4a6e;text-align:left"><div style="font-weight:700">Sky</div><div style="font-size:11px;opacity:.7">Pastel blue</div></button>

              <button type="button" class="theme-chip" data-theme="butter" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fefce8;color:#713f12;text-align:left"><div style="font-weight:700">Butter</div><div style="font-size:11px;opacity:.7">Soft pastel yellow</div></button>

              <button type="button" class="theme-chip" data-theme="lilac" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fdf4ff;color:#581c87;text-align:left"><div style="font-weight:700">Lilac</div><div style="font-size:11px;opacity:.7">Pastel violet</div></button>

              <button type="button" class="theme-chip" data-theme="blush" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fff1f2;color:#881337;text-align:left"><div style="font-weight:700">Blush</div><div style="font-size:11px;opacity:.7">Soft pastel pink</div></button>

              <button type="button" class="theme-chip" data-theme="sand" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#faf5ee;color:#44342a;text-align:left"><div style="font-weight:700">Sand</div><div style="font-size:11px;opacity:.7">Warm beige</div></button>

              <button type="button" class="theme-chip" data-theme="cottoncandy" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#fdf2f8;color:#831843;text-align:left"><div style="font-weight:700">Cotton Candy</div><div style="font-size:11px;opacity:.7">Bubblegum pink</div></button>

              <button type="button" class="theme-chip" data-theme="seafoam" style="cursor:pointer;border:2px solid var(--border);border-radius:12px;padding:12px;background:#ecfeff;color:#164e63;text-align:left"><div style="font-weight:700">Seafoam</div><div style="font-size:11px;opacity:.7">Pastel cyan</div></button>

            </div>

            <script>

              (function() {{

                var current = localStorage.getItem('mr_theme') || 'default';

                function mark() {{

                  document.querySelectorAll('.theme-chip').forEach(function(b) {{

                    b.style.outline = (b.dataset.theme === current) ? '3px solid #6366f1' : 'none';

                    b.style.outlineOffset = (b.dataset.theme === current) ? '2px' : '0';

                  }});

                }}

                document.querySelectorAll('.theme-chip').forEach(function(b) {{

                  b.addEventListener('click', function() {{

                    current = b.dataset.theme;

                    localStorage.setItem('mr_theme', current);

                    if (window.applyMrTheme) window.applyMrTheme(current);

                    mark();

                    var s = document.getElementById('theme-status');

                    if (s) {{

                      s.textContent = '{_T("Saved!")} ';

                      setTimeout(function(){{ if(s) s.textContent=''; }}, 2200);

                    }}

                  }});

                }});

                mark();

              }})();

            </script>

            <span id="theme-status" style="color:var(--text-muted);font-size:13px;display:inline-block;margin-top:10px"></span>

            <p style="color:var(--text-muted);font-size:12px;margin-top:6px">&#128161; {_T("Click any theme to switch instantly. Your choice is saved automatically.")}</p>

          </div>




        </div>






        <!-- Account Security (mirrors business settings — optional change) -->

        <div class="card" style="margin-top:16px;">

          <div class="card-header"><h2>&#128272; {_T("Account Security")}</h2></div>

          <div style="padding:20px;">

            <div style="display:flex;align-items:center;gap:12px;padding:14px 18px;background:var(--green-light,rgba(16,185,129,.12));border-radius:var(--radius-sm);margin-bottom:16px;">

              <span style="font-size:22px;">&#9989;</span>

              <div>

                <div style="font-weight:600;font-size:14px;color:var(--green-dark,#059669);">{_T("Your account is secure")}</div>

                <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">{_T("Password protected with bcrypt encryption. You can change your password below if needed.")}</div>

              </div>

            </div>

            <details style="cursor:pointer;">

              <summary style="font-size:14px;font-weight:600;color:var(--text);padding:10px 0;list-style:none;display:flex;align-items:center;gap:8px;">

                <span style="transition:transform 0.2s;display:inline-block;" class="pw-arrow">&#9654;</span>

                {_T("Change password")} <span style="font-size:12px;font-weight:400;color:var(--text-muted);">{_T("(optional)")}</span>

              </summary>

              <div style="padding:16px 0 4px;">

                <form method="post" action="/settings/change-password">

                  <div class="form-group" style="margin-bottom:12px;">

                    <label style="font-size:12px;font-weight:600;color:var(--text);">{_T("Current Password")}</label>

                    <input name="current_password" type="password" required class="edit-input" autocomplete="current-password">

                  </div>

                  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">

                    <div class="form-group">

                      <label style="font-size:12px;font-weight:600;color:var(--text);">{_T("New Password")}</label>

                      <input name="new_password" type="password" required minlength="6" class="edit-input" autocomplete="new-password">

                    </div>

                    <div class="form-group">

                      <label style="font-size:12px;font-weight:600;color:var(--text);">{_T("Confirm Password")}</label>

                      <input name="confirm_password" type="password" required minlength="6" class="edit-input" autocomplete="new-password">

                    </div>

                  </div>

                  <p style="font-size:11px;color:var(--text-muted);margin:0 0 12px;">{_T("Minimum 6 characters.")}</p>

                  <button class="btn btn-outline btn-sm" type="submit">{_T("Update Password")}</button>

                </form>

              </div>

            </details>

          </div>

        </div>

        <style>

          details[open] .pw-arrow {{ transform: rotate(90deg); }}

        </style>



        <!-- Retirement -->
        <div class="card" id="retire-card" style="margin-top:16px;border-color:{('var(--green)' if _is_retired else 'var(--yellow)')};">
          <div class="card-header"><h2>{('🏖️ ' + _T('Retired')) if _is_retired else ('🎓 ' + _T('Retirement'))}</h2></div>
          <div style="padding:20px;">
            {(
              '''<p style="font-size:13px;color:var(--text-muted);margin:0 0 14px;line-height:1.55;">'''
              + _T("You are currently retired from active rankings. Your name only appears on the Retirement leaderboard. You can come back at any time.")
              + '''</p>
              <button class="btn btn-primary btn-sm" onclick="unretireMe()">↩️ ''' + _T("Return to active rankings") + '''</button>
              <span id="retire-status" style="margin-left:10px;font-size:13px;color:var(--text-muted);"></span>'''
            ) if _is_retired else (
              '''<p style="font-size:13px;color:var(--text-muted);margin:0 0 14px;line-height:1.55;">'''
              + _T("When you finish your studies, retire to leave the active rankings with honor. Your XP is preserved and you'll appear on the Retirement leaderboard alongside other graduates. You can return at any time.")
              + '''</p>
              <button class="btn btn-ghost btn-sm" onclick="document.getElementById('retire-confirm').style.display='block';this.style.display='none';" style="border:1px solid var(--yellow);color:var(--yellow);">🏖️ ''' + _T("Retire from active rankings") + '''</button>
              <div id="retire-confirm" style="display:none;margin-top:14px;padding:16px;border:1px solid var(--yellow);border-radius:var(--radius-sm);background:var(--yellow-light);">
                <p style="font-size:13px;color:var(--text);margin:0 0 10px;">''' + _T("Are you sure? You will be removed from the global, country, university and major leaderboards.") + '''</p>
                <button class="btn btn-primary btn-sm" onclick="retireMe()" style="background:var(--yellow);border-color:var(--yellow);">''' + _T("Yes, retire me") + '''</button>
                <span id="retire-status" style="margin-left:10px;font-size:13px;color:var(--text-muted);"></span>
              </div>'''
            )}
          </div>
        </div>
        <script>
          async function retireMe() {{
            var s = document.getElementById('retire-status');
            s.textContent = {repr(_T("Retiring..."))};
            try {{
              var r = await fetch('/api/student/retire', {{method:'POST'}});
              var j = await r.json();
              if (j.ok) {{ s.textContent = {repr(_T("Retired. Reloading..."))}; setTimeout(function(){{mrReload();}}, 600); }}
              else {{ s.textContent = (j.error || 'Failed.'); }}
            }} catch(e) {{ s.textContent = 'Error de red.'; }}
          }}
          async function unretireMe() {{
            var s = document.getElementById('retire-status');
            s.textContent = {repr(_T("Unretiring..."))};
            try {{
              var r = await fetch('/api/student/unretire', {{method:'POST'}});
              var j = await r.json();
              if (j.ok) {{ s.textContent = {repr(_T("Welcome back! Reloading..."))}; setTimeout(function(){{mrReload();}}, 600); }}
              else {{ s.textContent = (j.error || 'Failed.'); }}
            }} catch(e) {{ s.textContent = 'Error de red.'; }}
          }}
        </script>


        <!-- Delete Account -->

        <div class="card" style="margin-top:16px;border-color:var(--red);">

          <div class="card-header"><h2 style="color:var(--red);">&#9888;&#65039; {_T("Danger Zone")}</h2></div>

          <div style="padding:20px;">

            <p style="font-size:13px;color:var(--text-muted);margin:0 0 12px;">{_T("Permanently delete your account and all associated data (courses, exams, notes, flashcards, quizzes, chat history, XP, badges). This action <strong>cannot be undone</strong>.")}</p>

            <button class="btn btn-ghost btn-sm" style="color:var(--red);border:1px solid var(--red);" onclick="document.getElementById('delete-confirm-box').style.display='block';this.style.display='none';">{_T("Delete My Account")}</button>

            <div id="delete-confirm-box" style="display:none;margin-top:14px;padding:16px;border:1px solid var(--red);border-radius:var(--radius-sm);background:var(--red-light);">

              <form method="post" action="/settings/delete-account">

                <p style="font-size:13px;color:var(--text);margin:0 0 10px;font-weight:600;">{_T("Type")} <code style="background:var(--border);padding:2px 6px;border-radius:4px;">DELETE</code> {_T("to confirm:")}</p>

                <input name="confirm" placeholder="DELETE" required autocomplete="off" class="edit-input" style="border-color:var(--red);max-width:200px;margin-bottom:10px;">

                <br>

                <button class="btn btn-primary btn-sm" type="submit" style="background:var(--red);border-color:var(--red);">{_T("Permanently Delete Account")}</button>

              </form>

            </div>

          </div>

        </div>



        <script>

        async function savePrefs() {{
          // Daily-study email + Google Calendar were removed from settings;
          // this function only exists so any lingering onclick doesn't throw.
          var $pick = function(id){{ return document.getElementById(id); }};
          var body = {{}};
          if ($pick('pref-daily')) body.daily_email = $pick('pref-daily').checked;
          if ($pick('pref-hour')) body.email_hour = parseInt($pick('pref-hour').value);
          if ($pick('pref-tz')) body.timezone = $pick('pref-tz').value;
          if ($pick('pref-university')) body.university = $pick('pref-university').value.trim();
          if ($pick('pref-field')) body.field_of_study = $pick('pref-field').value.trim();
          var r = await fetch('/api/student/email-prefs', {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify(body)
          }});

          if (r.ok) {{

            alert('{_T("Saved!")}');

          }} else {{

            var msg = '{_T("Error saving.")}';

            try {{ var j = await r.json(); if (j && j.error) msg = j.error; }} catch(e) {{}}

            alert(msg);

          }}

        }}



        </script>

        """, active_page="student_settings")



    # ── Essay Assistant ──────────────────────────────────────

    @app.route("/student/essay")

    def student_essay_page():

        if not _logged_in():

            return redirect(url_for("login"))

        return _s_render("Ensayos", f"""

        <style>
        .essay-cd {{ max-width:1260px;margin:0 auto 80px;font-family:"Plus Jakarta Sans",system-ui,sans-serif;color:#1A1A1F; }}
        .essay-active {{ display:grid;grid-template-columns:340px 1fr;gap:20px;align-items:start; }}
        .essay-cd .card {{ background:#fff;border:1px solid #E2DCCC;border-radius:24px;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04); }}
        .essay-cd .essay-side {{ padding:22px;position:sticky;top:92px; }}
        .essay-cd .essay-doc {{ display:none; }}
        .essay-kicker {{ font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:#009B72;margin-bottom:8px; }}
        .essay-title {{ font-family:"Fraunces",Georgia,serif;font-size:clamp(42px,6vw,72px);line-height:.92;margin:0;font-weight:600;letter-spacing:-.05em; }}
        .essay-title em {{ color:#FF7A3D;font-style:italic; }}
        .essay-sub {{ color:#6E6A60;font-size:15px;margin:12px 0 24px;max-width:680px;line-height:1.6; }}
        .essay-cd label {{ display:block;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.12em;color:#77756F;margin-bottom:8px; }}
        .essay-cd input,.essay-cd textarea {{ width:100%;background:#FBF8F0!important;border:1px solid #D8D0BE!important;border-radius:14px!important;padding:12px 13px!important; }}
        #ea-drop {{ border:1.5px dashed #D8D0BE!important;border-radius:18px!important;padding:20px!important;background:#FBF8F0!important; }}
        #ea-drop:hover {{ border-color:#5B4694!important;background:#F5F0FF!important; }}
        #ea-btn {{ width:100%;border:0!important;border-radius:999px!important;background:#1A1A1F!important;color:#FFF8E1!important;padding:14px 18px!important;font-weight:900!important;box-shadow:0 4px 0 rgba(0,0,0,.16)!important; }}
        .essay-doc-head {{ display:flex;justify-content:space-between;align-items:center;padding:18px 22px;border-bottom:1px solid #E2DCCC;background:#FBF8F0; }}
        .essay-doc-head strong {{ font-family:"Fraunces",Georgia,serif;font-size:26px;font-weight:600;letter-spacing:-.03em; }}
        .essay-doc-head span {{ color:#77756F;font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase; }}
        #ea-essay {{ min-height:560px!important;border:0!important;border-radius:0!important;background:#fff!important;padding:34px 42px!important;color:#1A1A1F!important;font-family:"Fraunces",Georgia,serif!important;font-size:22px!important;line-height:1.75!important;resize:vertical;box-shadow:none!important; }}
        #ea-result {{ margin-top:0!important; }}
        #ea-result .card {{ border-radius:20px;border-color:#E2DCCC;background:#fff;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04);margin-top:14px; }}
        @media (max-width:980px) {{ .essay-active {{ grid-template-columns:1fr; }} .essay-cd .essay-side {{ position:relative;top:auto; }} #ea-essay {{ min-height:420px!important;padding:24px!important;font-size:19px!important; }} }}
        </style>
        <script>
        document.addEventListener('DOMContentLoaded', function(){{
          var outer = document.querySelector('h1');
          var oldWrap = outer && outer.parentElement && outer.parentElement.style.maxWidth ? outer.parentElement : null;
          if (!oldWrap || oldWrap.classList.contains('essay-cd')) return;
          oldWrap.className = 'essay-cd';
          oldWrap.removeAttribute('style');
          var firstCard = oldWrap.querySelector('.card');
          var result = document.getElementById('ea-result');
          if (outer) outer.outerHTML = '<div class="essay-kicker">Asistente de escritura</div><h1 class="essay-title">Ensayos <em>sin vueltas.</em></h1>';
          var sub = oldWrap.querySelector('p');
          if (sub) {{ sub.className = 'essay-sub'; sub.textContent = 'Sube o pega tu borrador y recibe feedback claro sobre tesis, estructura, gramática y fluidez.'; }}
          if (firstCard && result) {{
            firstCard.classList.add('essay-side');
            var essayGroup = document.getElementById('ea-essay') && document.getElementById('ea-essay').closest('.form-group');
            if (essayGroup) essayGroup.style.display = 'none';
            var doc = document.createElement('section');
            doc.className = 'card essay-doc';
            doc.innerHTML = '<div class="essay-doc-head"><strong>Borrador</strong><span>Editor</span></div>';
            if (essayGroup) doc.appendChild(essayGroup);
            var main = document.createElement('main');
            main.appendChild(doc);
            main.appendChild(result);
            var grid = document.createElement('div');
            grid.className = 'essay-active';
            firstCard.parentNode.insertBefore(grid, firstCard);
            grid.appendChild(firstCard);
            grid.appendChild(main);
          }}
          var prompt = document.getElementById('ea-prompt'); if (prompt) prompt.placeholder = '¿Qué tenía que responder el ensayo?';
          var essay = document.getElementById('ea-essay'); if (essay) essay.placeholder = 'El texto extraído del archivo aparecerá aquí internamente.';
          var btn = document.getElementById('ea-btn'); if (btn) btn.textContent = 'Analizar ensayo';
          document.querySelectorAll('.essay-cd label').forEach(function(l){{
            l.innerHTML = l.innerHTML.replace('Assignment prompt', 'Instrucción o pregunta').replace('Or drop your essay file', 'Archivo').replace('Your essay', 'Borrador');
          }});
        }});
        </script>

        <div style="max-width:900px;margin:0 auto">

          <h1 style="margin:0 0 6px">✏️ Essay Assistant</h1>

          <p style="color:var(--text-muted);margin:0 0 18px">Paste your draft. Get brutally honest feedback on thesis, structure, grammar, and flow.</p>

          <div class="card">

            <div class="form-group">

              <label>Assignment prompt <span style="color:var(--text-muted);font-size:12px">(optional)</span></label>

              <input id="ea-prompt" type="text" placeholder="What was the essay supposed to answer?" style="width:100%;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text)">

            </div>



            <!-- Drag & drop file -->

            <div class="form-group">

              <label>Or drop your essay file</label>

              <div id="ea-drop" style="border:2px dashed var(--border);border-radius:10px;padding:18px;text-align:center;cursor:pointer;background:var(--bg);transition:all .2s"

                ondragover="event.preventDefault();this.style.borderColor='var(--primary)'"

                ondragleave="this.style.borderColor='var(--border)'"

                ondrop="eaHandleDrop(event)" onclick="document.getElementById('ea-file').click()">

                <div style="font-size:28px">📄</div>

                <div style="font-weight:600;margin-top:4px;font-size:14px">Drop a PDF / DOCX / TXT</div>

                <div style="font-size:12px;color:var(--text-muted)">we'll extract the text into the editor below</div>

                <input type="file" id="ea-file" accept=".pdf,.docx,.doc,.txt" style="display:none" onchange="eaHandleFile(this.files[0])">

                <div id="ea-file-info" style="margin-top:6px;font-size:12px;color:var(--primary)"></div>

              </div>

            </div>



            <div class="form-group">

              <label>Your essay</label>

              <textarea id="ea-essay" placeholder="Paste your draft here..." style="width:100%;min-height:260px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);resize:vertical"></textarea>

            </div>

            <div style="display:flex;gap:8px;align-items:center">

              <button onclick="analyzeEssay()" class="btn btn-primary" id="ea-btn">Analyze</button>

              <span id="ea-status" style="color:var(--text-muted);font-size:13px"></span>

            </div>

          </div>

          <div id="ea-result" style="margin-top:18px"></div>

        </div>

        <script>

        async function eaHandleDrop(e) {{

          e.preventDefault();

          e.currentTarget.style.borderColor = 'var(--border)';

          if (e.dataTransfer.files.length) await eaHandleFile(e.dataTransfer.files[0]);

        }}

        async function eaHandleFile(file) {{

          if (!file) return;

          var ext = file.name.split('.').pop().toLowerCase();

          if (!['pdf','docx','doc','txt'].includes(ext)) {{ alert('PDF, DOCX, or TXT only'); return; }}

          if (file.size > 50*1024*1024) {{ alert('File too large (max 50MB)'); return; }}

          var info = document.getElementById('ea-file-info');

          info.textContent = '⏳ Extracting ' + file.name + '...';

          var fd = new FormData(); fd.append('file', file);

          try {{

            var r = await fetch('/api/student/extract-file', {{ method:'POST', body: fd }});

            var d = await r.json();

            if (!r.ok) {{ info.textContent = '❌ ' + (d.error || 'Failed'); return; }}

            document.getElementById('ea-essay').value = d.text;

            info.textContent = '✅ Loaded ' + d.filename + ' (' + d.char_count.toLocaleString() + ' chars)';

          }} catch(e) {{ info.textContent = '❌ Error de red'; }}

        }}

        async function analyzeEssay() {{

          var essay = document.getElementById('ea-essay').value.trim();

          if (essay.length < 80) {{ alert('Sube un archivo primero. No se analiza texto pegado manualmente.'); return; }}

          var btn = document.getElementById('ea-btn');

          var status = document.getElementById('ea-status');

          btn.disabled = true; btn.textContent = 'Analyzing...';

          status.textContent = 'This takes ~10 seconds.';

          var meta = document.querySelector('meta[name="csrf-token"]');

          var headers = {{'Content-Type':'application/json'}};

          if (meta) headers['X-CSRFToken'] = meta.getAttribute('content');

          try {{

            var r = await fetch('/api/student/essay/analyze', {{

              method:'POST', headers: headers,

              body: JSON.stringify({{essay: essay, prompt: document.getElementById('ea-prompt').value}})

            }});

            var d = await r.json();

            if (!r.ok) throw new Error(d.error || 'Analyze failed');

            renderEssay(d);

          }} catch(e) {{ status.innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }}

          finally {{ btn.disabled = false; btn.textContent = 'Analyze'; }}

        }}

        function renderEssay(d) {{

          var out = document.getElementById('ea-result');

          function bar(label, val) {{

            var color = val >= 85 ? '#22c55e' : (val >= 70 ? '#eab308' : '#ef4444');

            return '<div style="margin:6px 0"><div style="display:flex;justify-content:space-between;font-size:13px"><span>' + label + '</span><span style="font-weight:700">' + val + '</span></div>'

              + '<div style="background:var(--border);height:8px;border-radius:4px;overflow:hidden"><div style="width:' + val + '%;height:100%;background:' + color + '"></div></div></div>';

          }}

          var strengths = (d.strengths || []).map(function(s){{ return '<li>' + s + '</li>'; }}).join('');

          var weaknesses = (d.weaknesses || []).map(function(s){{ return '<li>' + s + '</li>'; }}).join('');

          var grammar = (d.grammar_issues || []).map(function(g){{

            return '<div style="padding:10px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px">'

              + '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">' + (g.reason || '') + '</div>'

              + '<div style="text-decoration:line-through;color:#ef4444">' + (g.original || '') + '</div>'

              + '<div style="color:#22c55e;margin-top:4px">→ ' + (g.suggestion || '') + '</div></div>';

          }}).join('') || '<p style="color:var(--text-muted);font-size:13px">No major grammar issues detected.</p>';

          out.innerHTML =

            '<div class="card"><h2 style="margin:0 0 10px">Overall: ' + (d.overall_score || 0) + '/100</h2>'

            + bar('Thesis', d.thesis_strength || 0)

            + bar('Structure', d.structure_score || 0)

            + bar('Grammar', d.grammar_score || 0)

            + bar('Clarity', d.clarity_score || 0)

            + '<div style="margin-top:14px;font-size:13px;color:var(--text-muted)">Words: ' + (d.word_count || 0) + ' · Level: ' + (d.reading_level || '—') + '</div></div>'

            + (d.thesis_feedback ? '<div class="card"><h3 style="margin:0 0 8px">Thesis Feedback</h3><p style="margin:0">' + d.thesis_feedback + '</p></div>' : '')

            + (strengths ? '<div class="card"><h3 style="margin:0 0 8px;color:#22c55e">Strengths</h3><ul style="margin:0;padding-left:20px">' + strengths + '</ul></div>' : '')

            + (weaknesses ? '<div class="card"><h3 style="margin:0 0 8px;color:#ef4444">Weaknesses</h3><ul style="margin:0;padding-left:20px">' + weaknesses + '</ul></div>' : '')

            + '<div class="card"><h3 style="margin:0 0 8px">Grammar & Style</h3>' + grammar + '</div>'

            + (d.improved_intro ? '<div class="card"><h3 style="margin:0 0 8px">Rewritten Intro</h3><p style="margin:0;white-space:pre-wrap">' + d.improved_intro + '</p></div>' : '');

        }}

        </script>

        """, active_page="student_essay")



    @app.route("/api/student/essay/analyze", methods=["POST"])

    def student_essay_analyze_api():

        if not _logged_in():

            return jsonify({"error": "Unauthorized"}), 401

        data = request.get_json(force=True) or {}

        essay = (data.get("essay") or "").strip()

        if len(essay) < 50:

            return jsonify({"error": "Essay too short"}), 400

        try:

            result = analyze_essay(essay, data.get("prompt", ""))

            return jsonify(result)

        except Exception as e:

            log.exception("essay analyze failed")

            return jsonify({"error": str(e)}), 500


    # ── Shop / Wallet / Profile ─────────────────────────────

    @app.route("/student/shop")
    def student_shop_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        wallet = sdb.get_wallet(cid)
        total_xp = sdb.get_total_xp(cid)
        # Build banner cards HTML
        is_plus = sdb._is_plus_user(cid)
        banner_cards = []
        for key, cfg in sdb.BANNERS.items():
            owned = key in wallet["unlocked_banners"]
            xp_ok = total_xp >= cfg["xp_required"]
            plus_only = bool(cfg.get("plus_only"))
            plus_locked = plus_only and not is_plus
            plus_pill = ' <span style="background:linear-gradient(135deg,#a855f7,#ec4899);color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:6px;vertical-align:middle;">PLUS</span>' if plus_only else ''
            if owned:
                tag = '<span style="color:#10b981;font-weight:700;">Comprado</span>'
                btn = ''
            elif plus_locked:
                tag = '<span style="color:#a855f7;">Requiere suscripción PLUS</span>'
                btn = '<button class="btn btn-sm btn-outline" disabled>Solo PLUS</button>'
            elif not xp_ok:
                tag = f'<span style="color:#94a3b8;">Alcanza {cfg["xp_required"]} XP para desbloquear</span>'
                btn = ''
            elif wallet["coins"] < cfg["price_coins"]:
                tag = f'<span style="color:#ef4444;">Necesitas {cfg["price_coins"]} monedas</span>'
                btn = f'<button class="btn btn-sm btn-outline" disabled>Comprar ({cfg["price_coins"]} \U0001FA99)</button>'
            else:
                tag = f'<span style="color:#94a3b8;">{cfg["xp_required"]} XP desbloqueado</span>'
                btn = f'<button class="btn btn-sm btn-primary" onclick="buyBanner(\'{key}\')">Comprar ({cfg["price_coins"]} \U0001FA99)</button>'
            banner_cards.append(
                f'<div style="background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;">'
                f'<div class="bnr-anim-host {(cfg.get("anim_class") or "") if cfg.get("animated") else ""}" style="height:90px;background:{cfg["css"]};"></div>'
                f'<div style="padding:14px;"><div style="font-weight:700;font-size:15px;">{cfg["name"]}{plus_pill}</div>'
                f'<div style="font-size:12px;margin-top:4px;">{tag}</div>'
                f'<div style="margin-top:10px;">{btn}</div></div></div>'
            )
        banners_html = "".join(banner_cards)

        # Build leaderboard flag cards HTML
        flag_state = sdb.get_flag_state(cid)
        flag_cards = []
        for key, cfg in sdb.FLAGS.items():
            if key == "none":
                continue
            owned = key in flag_state["unlocked_flags"]
            xp_ok = total_xp >= cfg["xp_required"]
            selected = (key == flag_state["selected_flag"])
            plus_only = bool(cfg.get("plus_only"))
            plus_locked = plus_only and not is_plus
            plus_pill = ' <span style="background:linear-gradient(135deg,#a855f7,#ec4899);color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:6px;vertical-align:middle;">PLUS</span>' if plus_only else ''
            if owned:
                if selected:
                    tag = '<span style="color:#7c3aed;font-weight:700;">Equipado</span>'
                    btn = '<button class="btn btn-sm btn-outline" disabled>En uso</button>'
                else:
                    tag = '<span style="color:#10b981;font-weight:700;">Comprado</span>'
                    btn = f'<button class="btn btn-sm btn-primary" onclick="equipFlag(\'{key}\')">Equipar</button>'
            elif plus_locked:
                tag = '<span style="color:#a855f7;">Requiere suscripción PLUS</span>'
                btn = '<button class="btn btn-sm btn-outline" disabled>Solo PLUS</button>'
            elif not xp_ok:
                tag = f'<span style="color:#94a3b8;">Alcanza {cfg["xp_required"]} XP para desbloquear</span>'
                btn = ''
            elif wallet["coins"] < cfg["price_coins"]:
                tag = f'<span style="color:#ef4444;">Necesitas {cfg["price_coins"]} monedas</span>'
                btn = f'<button class="btn btn-sm btn-outline" disabled>Comprar ({cfg["price_coins"]} \U0001FA99)</button>'
            else:
                tag = f'<span style="color:#94a3b8;">{cfg["xp_required"]} XP desbloqueado</span>'
                btn = f'<button class="btn btn-sm btn-primary" onclick="buyFlag(\'{key}\')">Comprar ({cfg["price_coins"]} \U0001FA99)</button>'
            # Preview the L\u2192R fade exactly like the leaderboard renders it.
            _flag_anim = (cfg.get("anim_class") or "") if cfg.get("animated") else ""
            preview = (
                '<div style="position:relative;height:48px;background:#0f172a;border-radius:8px;overflow:hidden;">'
                f'<div class="{_flag_anim}" style="position:absolute;inset:0;background:{cfg["css"]};'
                '-webkit-mask-image:linear-gradient(to right, rgba(0,0,0,.85) 0%, rgba(0,0,0,.45) 35%, rgba(0,0,0,.15) 65%, transparent 100%);'
                'mask-image:linear-gradient(to right, rgba(0,0,0,.85) 0%, rgba(0,0,0,.45) 35%, rgba(0,0,0,.15) 65%, transparent 100%);"></div>'
                '<div style="position:absolute;inset:0;display:flex;align-items:center;padding:0 12px;color:#fff;font-size:12px;font-weight:600;letter-spacing:.05em;">VISTA PREVIA</div>'
                '</div>'
            )
            flag_cards.append(
                '<div style="background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;padding:12px;">'
                f'{preview}'
                f'<div style="margin-top:10px;"><div style="font-weight:700;font-size:15px;">{cfg["name"]}{plus_pill}</div>'
                f'<div style="font-size:12px;margin-top:4px;">{tag}</div>'
                f'<div style="margin-top:10px;">{btn}</div></div></div>'
            )
        flags_html = "".join(flag_cards)
        coins = wallet["coins"]
        freezes = wallet["streak_freezes"]
        freeze_cap = sdb._streak_freeze_cap(cid)
        freeze_btn_disabled = "disabled" if (coins < sdb.STREAK_FREEZE_PRICE or freezes >= freeze_cap) else ""
        bundle_qty   = sdb.STREAK_FREEZE_BUNDLE_QTY
        bundle_price = sdb.STREAK_FREEZE_BUNDLE_PRICE
        bundle_save  = sdb.STREAK_FREEZE_PRICE * bundle_qty - bundle_price
        bundle_disabled = "disabled" if (coins < bundle_price or freezes + bundle_qty > freeze_cap) else ""

        # Boosts activos banner
        active_boosts = sdb.get_active_boosts(cid)
        if active_boosts:
            chips = []
            for b in active_boosts:
                exp = b.get("expires_at") or ""
                kind_label = "XP" if b.get("kind") == "xp" else "Coins"
                chips.append(
                    f'<span class="sh-active-chip" data-exp="{exp}">'
                    f'\u26a1 {b.get("multiplier",1):g}\u00d7 {kind_label} \u00b7 '
                    f'<span class="sh-cd">--:--:--</span></span>'
                )
            active_html = (
                '<div class="card" style="background:linear-gradient(135deg,#fef3c7,#fde68a);border:none;">'
                '<div style="font-weight:700;margin-bottom:6px;color:#78350f;">Boosts activos</div>'
                f'<div style="display:flex;gap:8px;flex-wrap:wrap;">{"".join(chips)}</div>'
                '</div>'
            )
        else:
            active_html = ""

        # ── Subscription tier cards ────────────────────────
        subscription_section = ""
        try:
            from student import subscription as _sub
            current_tier = _sub.get_tier(cid)
            plans = _sub.PLANS
            tier_order = ["free", "plus", "ultimate"]
            tier_colors = {
                "free":     ("#64748b", "#f1f5f9"),
                "plus":     ("#7c3aed", "#ede9fe"),
                "ultimate": ("#d97706", "#fef3c7"),
            }
            sub_cards = []
            for key in tier_order:
                cfg = plans.get(key)
                if not cfg:
                    continue
                border, _bg = tier_colors.get(key, ("#64748b", "#f1f5f9"))
                is_current = (key == current_tier)
                features_html = "".join(
                    f'<li style="margin:6px 0;font-size:13px;color:#334155;">{_esc(str(f))}</li>'
                    for f in cfg.get("features", [])
                )
                price = cfg.get("price_usd_month", 0)
                price_html = "Gratis" if not price else f"${price:.2f}<span style='font-size:13px;font-weight:500;color:#64748b;'>/mes</span>"
                label_name = cfg.get("name", key.title())
                if is_current:
                    btn = '<button class="btn btn-sm" disabled style="width:100%;background:#10b981;color:#fff;border:none;">Plan actual</button>'
                elif key == "free":
                    btn = f'<button class="btn btn-sm btn-outline" style="width:100%;" onclick="changeTier(\'{key}\')">Bajar a Gratis</button>'
                else:
                    btn = f'<button class="btn btn-sm btn-primary" style="width:100%;" onclick="changeTier(\'{key}\')">Mejorar a {_esc(label_name)}</button>'
                badge = '<div style="position:absolute;top:10px;right:10px;background:#10b981;color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px;">ACTIVO</div>' if is_current else ""
                sub_cards.append(
                    f'<div style="position:relative;background:var(--card);border:2px solid {border};border-radius:16px;padding:18px;display:flex;flex-direction:column;">'
                    f'{badge}'
                    f'<div style="font-size:13px;font-weight:700;color:{border};text-transform:uppercase;letter-spacing:.5px;">{_esc(label_name)}</div>'
                    f'<div style="font-size:28px;font-weight:800;margin:6px 0 4px;">{price_html}</div>'
                    f'<div style="color:var(--text-muted);font-size:12px;margin-bottom:10px;">{_esc(cfg.get("blurb",""))}</div>'
                    f'<ul style="list-style:none;padding:0;margin:0 0 14px;flex:1;">{features_html}</ul>'
                    f'{btn}'
                    '</div>'
                )
            subscriptions_html = "".join(sub_cards)
            junaeb_email = "support@machreach.com"
            junaeb_subject = "Junaeb%20%E2%80%94%20Solicitud%20de%20descuento%20PLUS"
            junaeb_body = (
                "Hola%20equipo%20Machreach%2C%0A%0A"
                "Adjunto%20fotos%20de%20mi%20Tarjeta%20Junaeb%20%28frente%20y%20reverso%29%20"
                "para%20solicitar%20el%20descuento%20PLUS.%0A%0A"
                "Mi%20correo%20de%20la%20cuenta%3A%20%5Bcompletar%5D%0A"
                "Mi%20universidad%3A%20%5Bcompletar%5D%0A%0AGracias!"
            )
            junaeb_mailto = f"mailto:{junaeb_email}?subject={junaeb_subject}&body={junaeb_body}"
            junaeb_card = (
                '<div style="margin-top:14px;background:linear-gradient(135deg,#ecfdf5,#d1fae5);'
                'border:1px solid #10b981;border-radius:14px;padding:16px 18px;color:#064e3b;">'
                '<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
                '<span style="font-size:22px;">\U0001f4b3</span>'
                '<div style="font-weight:800;font-size:15px;">¿Tienes Tarjeta Junaeb?</div>'
                '</div>'
                '<div style="font-size:13px;line-height:1.5;margin-bottom:10px;">'
                'Si tienes Junaeb activa, te damos un descuento especial en PLUS. '
                'Mándanos una foto del <b>frente</b> y <b>reverso</b> de tu tarjeta a '
                f'<a href="mailto:{junaeb_email}" style="color:#047857;font-weight:700;">{junaeb_email}</a> '
                'y te respondemos manualmente con un código de descuento (24–48h).'
                '</div>'
                f'<a href="{junaeb_mailto}" class="btn btn-sm btn-primary" '
                'style="background:#10b981;border:none;display:inline-block;">'
                '\U00002709️ Enviar correo a soporte</a>'
                '</div>'
            )
            subscription_section = (
                '<div class="card">'
                '<div class="card-header"><h2>\U0001F48E Suscripción</h2></div>'
                '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;">'
                + subscriptions_html +
                '</div>'
                + junaeb_card +
                '</div>'
            )
        except Exception as _sub_err:
            log.exception("Shop subscription section failed: %s", _sub_err)
            subscription_section = ""

        # Build identity bundle cards (25% off banner+flag bundles)
        bundle_cards = []
        cur_flag_state = sdb.get_flag_state(cid)
        for bkey, bcfg in sdb.BUNDLES.items():
            if bcfg.get("plus_only") and not is_plus:
                continue
            bnr = sdb.BANNERS.get(bcfg.get("banner") or "") or {}
            flg = sdb.FLAGS.get(bcfg.get("flag") or "") or {}
            full = int(bnr.get("price_coins") or 0) + int(flg.get("price_coins") or 0)
            price = sdb.bundle_price(bkey)
            already_b = (bcfg.get("banner") in (wallet["unlocked_banners"] or []))
            already_f = (bcfg.get("flag") in (cur_flag_state["unlocked_flags"] or []))
            both = already_b and already_f
            if both:
                cta = '<button class="btn btn-sm btn-outline" disabled>Comprado</button>'
            elif wallet["coins"] < price:
                cta = f'<button class="btn btn-sm btn-outline" disabled>Comprar ({price} \U0001FA99)</button>'
            else:
                cta = f'<button class="btn btn-sm btn-primary" onclick="buyBundle(\'{bkey}\')">Comprar ({price} \U0001FA99)</button>'
            anim_b = (bnr.get("anim_class") or "") if bnr.get("animated") else ""
            bundle_cards.append(
                f'<div style="background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;">'
                f'  <div class="bnr-anim-host {anim_b}" style="height:70px;background:{bnr.get("css","")};"></div>'
                f'  <div style="height:8px;background:{flg.get("css","")};opacity:.85;"></div>'
                f'  <div style="padding:14px;">'
                f'    <div style="font-weight:700;font-size:15px;">{bcfg["name"]} <span style="background:#22c55e;color:#03250f;font-size:10px;font-weight:800;padding:2px 6px;border-radius:6px;vertical-align:middle;">−25%</span></div>'
                f'    <div style="font-size:12px;color:var(--text-muted);margin:4px 0 8px;">{bcfg.get("desc","")}</div>'
                f'    <div style="font-size:12px;color:var(--text-muted);"><s>{full} \U0001FA99</s></div>'
                f'    <div style="margin-top:10px;">{cta}</div>'
                f'  </div>'
                f'</div>'
            )
        bundles_html = "".join(bundle_cards) or '<div style="color:var(--text-muted);font-size:13px;">No hay packs disponibles ahora.</div>'

        # ── Coin packs (real-money microtransactions) ─────────────────
        coin_pack_cards = []
        for pkey, pcfg in sdb.COIN_PACKS.items():
            total = int(pcfg["coins"]) + int(pcfg.get("bonus") or 0)
            bonus_html = (f'<div style="font-size:11px;color:#22c55e;font-weight:700;margin-top:4px;">+{pcfg["bonus"]} bonus \U0001FA99</div>'
                          if pcfg.get("bonus") else '')
            tag_html = (f'<div style="position:absolute;top:8px;right:8px;background:linear-gradient(90deg,#f59e0b,#ec4899);color:#fff;font-size:10px;font-weight:800;padding:3px 8px;border-radius:999px;letter-spacing:.04em;">{pcfg["tag"]}</div>'
                        if pcfg.get("tag") else '')
            coin_pack_cards.append(
                f'<div style="position:relative;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;text-align:center;">'
                f'  {tag_html}'
                f'  <div style="font-size:38px;line-height:1;">\U0001FA99</div>'
                f'  <div style="font-size:13px;color:var(--text-muted);margin-top:6px;">{pcfg["name"]}</div>'
                f'  <div style="font-size:24px;font-weight:800;margin-top:6px;">{total:,} monedas</div>'
                f'  {bonus_html}'
                f'  <div style="font-size:18px;font-weight:700;margin-top:10px;">${pcfg["price_usd"]:.2f}</div>'
                f'  <button class="btn btn-primary btn-sm" style="width:100%;margin-top:10px;" onclick="buyCoinPack(\'{pkey}\')">Comprar</button>'
                f'</div>'
            )
        coin_packs_html = "".join(coin_pack_cards)

        return _s_render("Shop", f"""
        <style>{sdb.BANNER_ANIM_CSS}
{sdb.FLAG_ANIM_CSS}</style>
        <div class="shop-cd">
        <h1 style="margin-bottom:6px;">\U0001f6d2 Tienda</h1>
        <p style="color:var(--text-muted);margin:0 0 24px;">Gasta monedas en congeladores de racha 🔥, banners de perfil y boosts temporales. Gana monedas completando sesiones de enfoque, quizzes, tarjetas y duelos.</p>
        <style>
          .shop-cd {{ --card:#FFFFFF; --card-bg:#FFFFFF; --bg:#F4F1EA; --text:#1A1A1F; --text-muted:#6E6A60; --border:#E2DCCC; font-family:"Plus Jakarta Sans",system-ui,sans-serif; color:#1A1A1F; }}
          .shop-cd h1 {{ font-family:"Fraunces",Georgia,serif;font-size:clamp(42px,6vw,72px);line-height:.92;letter-spacing:-.05em;font-weight:600;color:#1A1A1F; }}
          .shop-cd .card, .shop-cd .stat-card {{ background:#FFFFFF!important;border:1px solid #E2DCCC!important;border-radius:22px!important;box-shadow:0 1px 0 rgba(20,18,30,.04),0 2px 10px rgba(20,18,30,.04)!important;color:#1A1A1F!important; }}
          .shop-cd .card-header {{ border-bottom:1px solid #E2DCCC!important; }}
          .shop-cd .card-header h2 {{ font-family:"Plus Jakarta Sans",system-ui,sans-serif!important;font-size:16px!important;letter-spacing:0!important;font-weight:900!important;color:#1A1A1F!important; }}
          .shop-cd .btn-primary {{ background:#1A1A1F!important;color:#FFF8E1!important;border-color:#1A1A1F!important;box-shadow:0 4px 0 rgba(0,0,0,.16)!important;border-radius:999px!important; }}
          .shop-cd .btn-outline, .shop-cd .btn-ghost {{ background:#FBF8F0!important;color:#1A1A1F!important;border:1px solid #D8D0BE!important;border-radius:999px!important; }}
          .shop-cd [style*="background:var(--card)"] {{ background:#FFFFFF!important; }}
          .shop-cd [style*="color:#334155"] {{ color:#5C5C66!important; }}
          .shop-cd [style*="color:#94a3b8"], .shop-cd [style*="color:#64748b"] {{ color:#77756F!important; }}
          .sh-active-chip {{ display:inline-flex; align-items:center; gap:6px; padding:6px 10px; background:rgba(255,255,255,.7); border-radius:999px; font-size:12px; font-weight:600; color:#78350f; }}
          .sh-cd {{ font-variant-numeric: tabular-nums; }}
        </style>
        {active_html}
        {subscription_section}
        <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px;">
          <div class="stat-card stat-yellow" style="min-width:170px;"><div class="num" id="sh-coins">{coins} \U0001FA99</div><div class="label">Monedas</div></div>
          <div class="stat-card stat-blue" style="min-width:170px;"><div class="num" id="sh-freezes">{freezes} \u2744\ufe0f</div><div class="label">Congeladores de racha 🔥</div></div>
          <div class="stat-card stat-purple" style="min-width:170px;"><div class="num">{total_xp}</div><div class="label">XP total</div></div>
        </div>

        <div class="card">
          <div class="card-header"><h2>\U0001FA99 Comprar monedas</h2></div>
          <p style="color:var(--text-muted);font-size:13px;margin-bottom:14px;">Recarga tu billetera con paquetes de monedas. Bonus en los paquetes más grandes.</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;">
            {coin_packs_html}
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>\u2744\ufe0f Congeladores de racha 🔥</h2></div>
          <p style="color:var(--text-muted);font-size:13px;">Se usan automáticamente si te saltas un día. Plus incluye Streak Insurance+: 1 congelador extra al mes y capacidad para guardar hasta 5.</p>
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:10px;">
            <div style="font-size:18px;font-weight:700;">{sdb.STREAK_FREEZE_PRICE} \U0001FA99 cada uno</div>
            <button class="btn btn-primary btn-sm" id="buy-freeze-btn" onclick="buyFreeze()" {freeze_btn_disabled}>Comprar 1</button>
            <div style="width:1px;height:24px;background:var(--border);"></div>
            <div style="font-size:14px;"><b>Pack de 3</b>: {bundle_price} \U0001FA99 <span style="color:#10b981;font-weight:700;">(ahorra {bundle_save})</span></div>
            <button class="btn btn-primary btn-sm" onclick="buyFreezeBundle()" {bundle_disabled}>Comprar pack de 3</button>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>🎨 Banners de perfil</h2></div>
          <p style="color:var(--text-muted);font-size:13px;margin-bottom:14px;">Desbloquéalos al alcanzar el XP requerido y cómpralos con monedas.</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;">
            {banners_html}
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>\U0001F3F4 Banderas del ranking</h2></div>
          <p style="color:var(--text-muted);font-size:13px;margin-bottom:14px;">Lúcete en el ranking. Tu bandera fluye detrás de tu fila, desvaneciéndose de izquierda a derecha \u2014 visible para todos los estudiantes.</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;">
            {flags_html}
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>🎁 Packs de identidad &nbsp; <span style="font-size:13px;font-weight:600;color:#22c55e;">25% de descuento</span></h2></div>
          <p style="color:var(--text-muted);font-size:13px;margin-bottom:14px;">Packs que desbloquean banner + bandera juntos por menos que comprándolos por separado.</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;">
            {bundles_html}
          </div>
        </div>

        </div>
        <script>
        async function buyFreeze() {{
          const r = await fetch('/api/student/wallet/buy-freeze', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo comprar.'); return; }}
          mrReload();
        }}
        async function buyCoinPack(packKey) {{
          if (!confirm('¿Comprar este paquete de monedas? Serás redirigido al checkout.')) return;
          const r = await fetch('/api/student/wallet/buy-coin-pack', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{pack_key: packKey}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo iniciar el checkout.'); return; }}
          if (r.checkout_url) {{ window.location = r.checkout_url; return; }}
          mrReload();
        }}
        async function buyFreezeBundle() {{
          const r = await fetch('/api/student/wallet/buy-freeze-bundle', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo comprar.'); return; }}
          mrReload();
        }}
        async function buyBoost(key) {{
          const r = await fetch('/api/student/wallet/buy-boost', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{boost_key:key}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo comprar.'); return; }}
          mrReload();
        }}
        async function buyBanner(key) {{
          const r = await fetch('/api/student/wallet/buy-banner', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{banner_key: key}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo comprar.'); return; }}
          mrReload();
        }}
        async function buyFlag(key) {{
          const r = await fetch('/api/student/wallet/buy-flag', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{flag_key: key}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo comprar.'); return; }}
          mrReload();
        }}
        async function buyBundle(key) {{
          if (!confirm('¿Comprar este pack? Las monedas se descontarán altiro.')) return;
          const r = await fetch('/api/student/wallet/buy-bundle', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{bundle_key: key}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo comprar.'); return; }}
          alert('Desbloqueado: ' + r.banner_name + ' + ' + r.flag_name);
          mrReload();
        }}
        async function equipFlag(key) {{
          const r = await fetch('/api/student/wallet/set-flag', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{flag_key: key}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo equipar.'); return; }}
          mrReload();
        }}
        async function changeTier(tier) {{
          const labels = {{free:'Gratis', plus:'Plus', ultimate:'Ultimate'}};
          if (!confirm('¿Cambiar al plan ' + (labels[tier]||tier) + '?')) return;
          const r = await fetch('/api/student/subscription/change', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{tier:tier}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'No se pudo cambiar el plan.'); return; }}
          if (r.checkout_url) {{ window.location = r.checkout_url; return; }}
          mrReload();
        }}
        // Live countdown for active boost chips
        (function() {{
          const chips = document.querySelectorAll('.sh-active-chip');
          if (!chips.length) return;
          function pad(n) {{ return String(n).padStart(2, '0'); }}
          function tick() {{
            const now = Date.now();
            chips.forEach(chip => {{
              const exp = new Date(chip.dataset.exp).getTime();
              const ms = exp - now;
              if (ms <= 0) {{ chip.querySelector('.sh-cd').textContent = 'expirado'; return; }}
              const s = Math.floor(ms / 1000);
              const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
              chip.querySelector('.sh-cd').textContent = pad(h) + ':' + pad(m) + ':' + pad(sec);
            }});
          }}
          tick(); setInterval(tick, 1000);
        }})();
        </script>
        """, active_page="student_shop")


    @app.route("/student/profile")
    def student_profile_page():
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        from outreach.db import get_client
        c = get_client(cid) or {}
        wallet = sdb.get_wallet(cid)
        total_xp = sdb.get_total_xp(cid)
        focus_stats = sdb.get_focus_stats(cid)
        try:
            level_name, floor, ceil = sdb.get_level(total_xp)
        except Exception:
            level_name, floor, ceil = "Beginner", 0, 100
        # Pull the leaderboard rank for the identity strip ("Rank #X · Y XP").
        try:
            from student import academic as _ac
            _retired = bool((c or {}).get("retired") or 0)
            _my_rank_obj = _ac.my_rank("retirement" if _retired else "global", cid) or {}
            _my_rank = _my_rank_obj.get("rank")
        except Exception:
            _my_rank = None
        rank_display = ("#" + str(_my_rank)) if _my_rank else "Unranked"
        banner_css = sdb.BANNERS.get(wallet["selected_banner"], sdb.BANNERS["default"])["css"]
        equipped_banner_cfg = sdb.BANNERS.get(wallet["selected_banner"], sdb.BANNERS["default"]) or {}
        equipped_banner_anim = equipped_banner_cfg.get("anim_class") if equipped_banner_cfg.get("animated") else ""

        # ── Banners (grid of unlocked) ──────────────────────────────
        banner_cards = []
        for key in wallet["unlocked_banners"]:
            cfg = sdb.BANNERS.get(key)
            if not cfg:
                continue
            is_eq = key == wallet["selected_banner"]
            badge = '<div style="position:absolute;top:6px;right:6px;background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;">EQUIPADO</div>' if is_eq else ''
            border = "border:2px solid #10b981;" if is_eq else "border:1px solid var(--border);"
            anim_cls = (cfg.get("anim_class") or "") if cfg.get("animated") else ""
            anim_pill = '<div style="position:absolute;top:6px;left:6px;background:#7c3aed;color:#fff;font-size:9px;font-weight:700;padding:2px 6px;border-radius:999px;">ANIM</div>' if anim_cls else ''
            banner_cards.append(
                f'<div class="profile-cosm-card" data-banner="{key}" data-css="{cfg["css"]}" data-anim="{anim_cls}" '
                f'onclick="equipBanner(\'{key}\')" '
                f'style="position:relative;cursor:pointer;border-radius:12px;overflow:hidden;{border}background:var(--card);">'
                f'  <div class="bnr-anim-host {anim_cls}" style="height:60px;background:{cfg["css"]};"></div>'
                f'  <div style="padding:6px 10px;font-size:12px;color:var(--text);">{cfg["name"]}</div>'
                f'  {anim_pill}{badge}'
                f'</div>'
            )
        banners_grid = "".join(banner_cards) or '<div style="color:var(--text-muted);font-size:13px;">No banners unlocked yet — visit the Shop.</div>'

        # ── Flags (grid of unlocked) ────────────────────────────────
        flag_state = sdb.get_flag_state(cid)
        flag_cards = []
        for key in flag_state["unlocked_flags"]:
            cfg = sdb.FLAGS.get(key)
            if not cfg:
                continue
            is_eq = key == flag_state["selected_flag"]
            badge = '<div style="position:absolute;top:6px;right:6px;background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;">EQUIPADO</div>' if is_eq else ''
            border = "border:2px solid #10b981;" if is_eq else "border:1px solid var(--border);"
            preview = ('<div style="height:30px;background:transparent;"></div>' if key == "none"
                       else f'<div class="{(cfg.get("anim_class") or "") if cfg.get("animated") else ""}" style="height:30px;background:{cfg["css"]};-webkit-mask-image:linear-gradient(to right,#000 0%,#000 60%,transparent 100%);mask-image:linear-gradient(to right,#000 0%,#000 60%,transparent 100%);"></div>')
            flag_cards.append(
                f'<div class="profile-cosm-card" data-flag="{key}" onclick="equipFlag(\'{key}\')" '
                f'style="position:relative;cursor:pointer;border-radius:12px;overflow:hidden;{border}background:var(--card);">'
                f'  {preview}'
                f'  <div style="padding:6px 10px;font-size:12px;color:var(--text);">{cfg["name"]}</div>'
                f'  {badge}'
                f'</div>'
            )
        flags_grid = "".join(flag_cards)

        # ── Earned badges (two slots: left + right of name) ──
        earned = sdb.get_badges(cid)
        eq_badges = sdb.get_equipped_badges(cid)
        eq_left = eq_badges.get("left") or ""
        eq_right = eq_badges.get("right") or ""
        badge_cards = []
        # "None" tile clears the currently selected slot
        none_eq = (not eq_left and not eq_right)
        none_border = "border:2px solid #10b981;" if none_eq else "border:1px solid var(--border);"
        none_badge = '<div style="position:absolute;top:6px;right:6px;background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;">EQUIPADO</div>' if none_eq else ''
        badge_cards.append(
            f'<div class="profile-cosm-card" data-badge="" onclick="equipBadge(\'\')" '
            f'style="position:relative;cursor:pointer;border-radius:12px;overflow:hidden;{none_border}background:var(--card);padding:14px;text-align:center;">'
            f'  <div style="font-size:32px;opacity:.4;">🚫</div>'
            f'  <div style="font-size:12px;color:var(--text-muted);">None</div>'
            f'  {none_badge}'
            f'</div>'
        )
        for b in earned:
            key = b["badge_key"]
            emoji = b.get("emoji", "🏅")
            name = (b.get("name") or key).replace("'", "\\'").replace('"', "&quot;")
            is_l = (key == eq_left)
            is_r = (key == eq_right)
            slot_pills = []
            if is_l: slot_pills.append('<span style="background:#3b82f6;color:#fff;font-size:9px;font-weight:700;padding:2px 6px;border-radius:999px;">L</span>')
            if is_r: slot_pills.append('<span style="background:#7c3aed;color:#fff;font-size:9px;font-weight:700;padding:2px 6px;border-radius:999px;">R</span>')
            border = "border:2px solid #10b981;" if (is_l or is_r) else "border:1px solid var(--border);"
            eq_pill = (f'<div style="position:absolute;top:6px;right:6px;display:flex;gap:3px;">{"".join(slot_pills)}</div>'
                       if (is_l or is_r) else '')
            badge_cards.append(
                f'<div class="profile-cosm-card" data-badge="{key}" onclick="equipBadge(\'{key}\')" title="{b.get("desc","")}" '
                f'style="position:relative;cursor:pointer;border-radius:12px;overflow:hidden;{border}background:var(--card);padding:14px;text-align:center;">'
                f'  <div style="font-size:32px;">{emoji}</div>'
                f'  <div style="font-size:12px;color:var(--text);font-weight:600;">{name}</div>'
                f'  {eq_pill}'
                f'</div>'
            )
        badges_grid = "".join(badge_cards)

        # ── Generic cosmetic grids (focus theme, streak flame, quiz theme, timer ring) ──
        cos_state = sdb.get_cosmetic_state(cid)

        def _cos_grid(kind, catalog, sel, render_preview):
            cards = []
            for key, cfg in catalog.items():
                is_eq = (key == sel)
                border = "border:2px solid #10b981;" if is_eq else "border:1px solid var(--border);"
                pill = '<div style="position:absolute;top:6px;right:6px;background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;">EQUIPADO</div>' if is_eq else ''
                cards.append(
                    f'<div class="profile-cosm-card" data-key="{key}" onclick="equipCosmetic(\'{kind}\',\'{key}\')" '
                    f'style="position:relative;cursor:pointer;border-radius:12px;overflow:hidden;{border}background:var(--card);">'
                    f'  {render_preview(key, cfg)}'
                    f'  <div style="padding:6px 10px;font-size:12px;color:var(--text);">{cfg.get("name", key)}</div>'
                    f'  {pill}'
                    f'</div>'
                )
            return "".join(cards)

        focus_grid = _cos_grid("focus_theme", sdb.FOCUS_THEMES, cos_state["focus_theme"],
            lambda k, cfg: f'<div style="height:60px;background:{cfg["css"]};"></div>')
        ring_grid = _cos_grid("timer_ring", sdb.TIMER_RINGS, cos_state["timer_ring"],
            lambda k, cfg: f'<div style="height:60px;display:flex;align-items:center;justify-content:center;"><div style="width:42px;height:42px;border-radius:50%;background:{cfg["css"]};display:flex;align-items:center;justify-content:center;"><div style="width:30px;height:30px;border-radius:50%;background:var(--card);"></div></div></div>')

        # ── Identity bundles (25% off) ──
        bundle_cards = []
        for bkey, bcfg in sdb.BUNDLES.items():
            if bcfg.get("plus_only") and not getattr(sdb, "_is_plus_user", lambda _x: False)(cid):
                continue
            bnr = sdb.BANNERS.get(bcfg.get("banner") or "") or {}
            flg = sdb.FLAGS.get(bcfg.get("flag") or "") or {}
            full = int(bnr.get("price_coins") or 0) + int(flg.get("price_coins") or 0)
            price = sdb.bundle_price(bkey)
            already_b = (bcfg.get("banner") in (wallet["unlocked_banners"] or []))
            already_f = (bcfg.get("flag") in (sdb.get_flag_state(cid)["unlocked_flags"] or []))
            both = already_b and already_f
            cta = ('<button disabled style="background:#374151;color:#9ca3af;border:0;border-radius:8px;padding:6px 12px;font-size:12px;cursor:not-allowed;">Comprado</button>'
                   if both else
                   f'<button onclick="buyBundle(\'{bkey}\')" style="background:linear-gradient(90deg,#7c3aed,#3b82f6);color:#fff;border:0;border-radius:8px;padding:6px 12px;font-size:12px;font-weight:700;cursor:pointer;">Buy {price} 🪙</button>')
            bundle_cards.append(
                f'<div style="border:1px solid var(--border);border-radius:14px;overflow:hidden;background:var(--card);">'
                f'  <div style="height:48px;background:{bnr.get("css","")};"></div>'
                f'  <div style="height:6px;background:{flg.get("css","")};opacity:.85;"></div>'
                f'  <div style="padding:10px 12px;">'
                f'    <div style="font-weight:700;font-size:14px;">{bcfg["name"]}</div>'
                f'    <div style="font-size:11px;color:var(--text-muted);margin:4px 0 8px;">{bcfg.get("desc","")}</div>'
                f'    <div style="font-size:11px;color:var(--text-muted);"><s>{full} 🪙</s> &nbsp; <b style="color:#22c55e;">−25%</b></div>'
                f'    <div style="display:flex;justify-content:flex-end;margin-top:6px;">{cta}</div>'
                f'  </div>'
                f'</div>'
            )
        bundles_grid = "".join(bundle_cards) or '<div style="color:var(--text-muted);font-size:13px;">No bundles available right now.</div>'

        name = (c.get("name") or "Student").replace("<", "&lt;")
        email = (c.get("email") or "").replace("<", "&lt;")
        progress_pct = 0
        if ceil > floor:
            progress_pct = max(0, min(100, int((total_xp - floor) * 100 / (ceil - floor))))
        return _s_render("My Profile", f"""
        <style>{sdb.BANNER_ANIM_CSS}
{sdb.FLAG_ANIM_CSS}
          .student-profile-wrap {{ max-width:900px;margin:-16px auto 0; }}
          @media (max-width: 768px) {{ .student-profile-wrap {{ margin-top:-8px; }} }}
        </style>
        <div class="student-profile-wrap">
          <div style="margin-bottom:22px;">
            <div id="profile-banner" class="bnr-anim-host {equipped_banner_anim}" style="height:140px;background:{banner_css};border:1px solid var(--border);border-bottom:none;border-radius:14px 14px 0 0;"></div>
            <div style="background:#0B1220;border:1px solid var(--border);border-top:none;border-radius:0 0 14px 14px;padding:14px 22px 18px;position:relative;">
              <div style="width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,#3B4A7A,#5B4694);display:flex;align-items:center;justify-content:center;color:#fff;font-size:26px;font-weight:700;border:3px solid #0B1220;position:absolute;left:22px;top:-36px;z-index:2;">{(name[:2] or '?').upper()}</div>
              <div style="padding-top:42px;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:14px;">
                <div>
                  <div style="font-size:22px;font-weight:800;color:#fff;letter-spacing:-.02em;">{name}</div>
                  <div style="color:var(--text-muted);font-size:12px;">{email}</div>
                  <div style="margin-top:4px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:13px;color:var(--text-muted);letter-spacing:.02em;">Rank <b style="color:#fff;font-weight:700;">{rank_display}</b> &middot; <b style="color:#fff;font-weight:700;">{total_xp:,}</b> XP</div>
                </div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;">
                  <div class="stat-card stat-yellow" style="min-width:110px;padding:10px 14px;"><div class="num" style="font-size:20px;">{wallet['coins']} \U0001FA99</div><div class="label">Coins</div></div>
                  <div class="stat-card stat-blue" style="min-width:110px;padding:10px 14px;"><div class="num" style="font-size:20px;">{wallet['streak_freezes']} ❄️</div><div class="label">Freezes</div></div>
                  <div class="stat-card stat-red" style="min-width:110px;padding:10px 14px;"><div class="num" style="font-size:20px;">{focus_stats.get('streak_days',0)} 🔥</div><div class="label">Racha 🔥</div></div>
                </div>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-header"><h2>🎨 Profile banner</h2></div>
            <p style="color:var(--text-muted);font-size:13px;margin-bottom:12px;">Click any banner you've unlocked to equip it. <a href="/student/shop" style="color:var(--primary);">Visit the Shop</a> to unlock more.</p>
            <div class="profile-cosm-grid" data-cosm="banners">{banners_grid}</div>
          </div>

          <div class="card">
            <div class="card-header"><h2>🚩 Leaderboard flag</h2></div>
            <p style="color:var(--text-muted);font-size:13px;margin-bottom:12px;">Click any flag you've unlocked to equip it. Your flag fades in behind your row on the leaderboard.</p>
            <div class="profile-cosm-grid" data-cosm="flags">{flags_grid}</div>
          </div>

          <div class="card">
            <div class="card-header"><h2>🏅 Leaderboard badges</h2></div>
            <p style="color:var(--text-muted);font-size:13px;margin-bottom:8px;">You can equip <b>two</b> badges — one on each side of your name. Pick which slot to fill, then click a badge. The same badge can fill both sides.</p>
            <div style="display:flex;gap:8px;margin-bottom:12px;">
              <button id="badge-side-left"  onclick="setBadgeSide('left')"  class="badge-side-btn" style="border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:999px;padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer;">◀ Left slot</button>
              <button id="badge-side-right" onclick="setBadgeSide('right')" class="badge-side-btn active" style="border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:999px;padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer;">Right slot ▶</button>
            </div>
            <div class="profile-cosm-grid" data-cosm="badges">{badges_grid}</div>
          </div>

          <div class="card">
            <div class="card-header"><h2>🎁 Identity bundles &nbsp; <span style="font-size:12px;font-weight:600;color:#22c55e;">25% de descuento</span></h2></div>
            <p style="color:var(--text-muted);font-size:13px;margin-bottom:12px;">Bundles unlock a matching banner + flag at a discount vs buying separately.</p>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;">{bundles_grid}</div>
          </div>
        </div>

        <style>
          .profile-cosm-grid {{ display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px; }}
          .profile-cosm-card {{ transition:transform .12s ease, box-shadow .12s ease; }}
          .profile-cosm-card:hover {{ transform:translateY(-2px); box-shadow:0 4px 12px rgba(0,0,0,.18); }}
        </style>
        <script>
        const BANNER_CSS  = {{ {", ".join(f'"{k}":{json.dumps(v["css"])}' for k,v in sdb.BANNERS.items())} }};
        const BANNER_ANIM = {{ {", ".join(f'"{k}":{json.dumps((v.get("anim_class") or "") if v.get("animated") else "")}' for k,v in sdb.BANNERS.items())} }};
        const FLAG_CSS    = {{ {", ".join(f'"{k}":{json.dumps(v["css"])}' for k,v in sdb.FLAGS.items())} }};
        const FLAG_ANIM   = {{ {", ".join(f'"{k}":{json.dumps((v.get("anim_class") or "") if v.get("animated") else "")}' for k,v in sdb.FLAGS.items())} }};
        const EQUIP_PILL  = '<div class="profile-eq-pill" style="position:absolute;top:6px;right:6px;background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;">EQUIPADO</div>';

        function _markEquippedSingle(containerSelector, attrName, key) {{
          const container = document.querySelector(containerSelector);
          if (!container) return;
          container.querySelectorAll('.profile-cosm-card').forEach(card => {{
            const k = card.getAttribute(attrName);
            const isEq = (k === key);
            card.style.border = isEq ? '2px solid #10b981' : '1px solid var(--border)';
            const old = card.querySelector('.profile-eq-pill');
            if (old) old.remove();
            if (isEq) card.insertAdjacentHTML('beforeend', EQUIP_PILL);
          }});
        }}

        async function equipBanner(k) {{
          const el = document.getElementById('profile-banner');
          if (BANNER_CSS[k]) el.style.background = BANNER_CSS[k];
          el.className = 'bnr-anim-host ' + (BANNER_ANIM[k] || '');
          _markEquippedSingle('[data-cosm="banners"]', 'data-banner', k);
          const r = await fetch('/api/student/wallet/set-banner', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{banner_key:k}})}}).then(r=>r.json());
          if (!r.ok) alert(r.error || 'Could not apply.');
        }}
        async function equipFlag(k) {{
          _markEquippedSingle('[data-cosm="flags"]', 'data-flag', k);
          const r = await fetch('/api/student/wallet/set-flag', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{flag_key:k}})}}).then(r=>r.json());
          if (!r.ok) alert(r.error || 'Could not apply.');
        }}
        let BADGE_SIDE = 'right';
        let BADGE_LEFT = {json.dumps(eq_left)};
        let BADGE_RIGHT = {json.dumps(eq_right)};
        function setBadgeSide(side) {{
          BADGE_SIDE = side;
          document.getElementById('badge-side-left').style.background  = (side==='left')  ? 'linear-gradient(90deg,#3b82f6,#7c3aed)' : 'var(--card)';
          document.getElementById('badge-side-left').style.color       = (side==='left')  ? '#fff' : 'var(--text)';
          document.getElementById('badge-side-right').style.background = (side==='right') ? 'linear-gradient(90deg,#3b82f6,#7c3aed)' : 'var(--card)';
          document.getElementById('badge-side-right').style.color      = (side==='right') ? '#fff' : 'var(--text)';
        }}
        setBadgeSide('right');
        function _refreshBadgeCards() {{
          const container = document.querySelector('[data-cosm="badges"]');
          if (!container) return;
          container.querySelectorAll('.profile-cosm-card').forEach(card => {{
            const k = card.getAttribute('data-badge') || '';
            const isL = (k && k === BADGE_LEFT);
            const isR = (k && k === BADGE_RIGHT);
            const noneTile = (k === '');
            const eq = noneTile ? (!BADGE_LEFT && !BADGE_RIGHT) : (isL || isR);
            card.style.border = eq ? '2px solid #10b981' : '1px solid var(--border)';
            const old = card.querySelector('.profile-eq-pill');
            if (old) old.remove();
            if (noneTile && eq) {{
              card.insertAdjacentHTML('beforeend', EQUIP_PILL);
            }} else if (eq) {{
              const pills = [];
              if (isL) pills.push('<span style="background:#3b82f6;color:#fff;font-size:9px;font-weight:700;padding:2px 6px;border-radius:999px;">L</span>');
              if (isR) pills.push('<span style="background:#7c3aed;color:#fff;font-size:9px;font-weight:700;padding:2px 6px;border-radius:999px;">R</span>');
              card.insertAdjacentHTML('beforeend', '<div class="profile-eq-pill" style="position:absolute;top:6px;right:6px;display:flex;gap:3px;">' + pills.join('') + '</div>');
            }}
          }});
        }}
        async function equipBadge(k) {{
          if (BADGE_SIDE === 'left') BADGE_LEFT = k; else BADGE_RIGHT = k;
          if (k === '') {{ BADGE_LEFT = ''; BADGE_RIGHT = ''; }}
          _refreshBadgeCards();
          const r = await fetch('/api/student/wallet/set-badge', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{badge_key:k, side: BADGE_SIDE}})}}).then(r=>r.json());
          if (!r.ok) alert(r.error || 'Could not equip.');
        }}
        async function equipCosmetic(kind, key) {{
          _markEquippedSingle('[data-cosm="' + kind + '"]', 'data-key', key);
          const r = await fetch('/api/student/wallet/set-cosmetic', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{kind, key}})}}).then(r=>r.json());
          if (!r.ok) alert(r.error || 'Could not apply.');
        }}
        async function buyBundle(k) {{
          if (!confirm('Buy this bundle? Coins will be deducted immediately.')) return;
          const r = await fetch('/api/student/wallet/buy-bundle', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{bundle_key:k}})}}).then(r=>r.json());
          if (!r.ok) {{ alert(r.error || 'Could not purchase.'); return; }}
          alert('Unlocked: ' + r.banner_name + ' + ' + r.flag_name);
          mrReload();
        }}
        </script>
        """, active_page="student_profile")


    @app.route("/api/student/wallet/buy-freeze", methods=["POST"])
    def student_wallet_buy_freeze_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.buy_streak_freeze(_cid(), 1))


    @app.route("/api/student/wallet/buy-freeze-bundle", methods=["POST"])
    def student_wallet_buy_freeze_bundle_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.buy_streak_freeze(_cid(), sdb.STREAK_FREEZE_BUNDLE_QTY, bundle=True))


    @app.route("/api/student/wallet/buy-boost", methods=["POST"])
    def student_wallet_buy_boost_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.buy_boost(_cid(), str(data.get("boost_key") or "")))


    @app.route("/api/student/wallet/buy-banner", methods=["POST"])
    def student_wallet_buy_banner_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.buy_banner(_cid(), str(data.get("banner_key") or "")))


    @app.route("/api/student/wallet/buy-coin-pack", methods=["POST"])
    def student_wallet_buy_coin_pack_api():
        """Microtransaction: kick off a Lemon Squeezy hosted-checkout for a
        coin pack. The actual credit happens server-side in the LS webhook
        (`order_created`), not here. Returns a `checkout_url` for the client
        to redirect to.
        """
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        pack_key = str(data.get("pack_key") or "")
        if pack_key not in sdb.COIN_PACKS:
            return jsonify(ok=False, error="Unknown coin pack"), 400
        from outreach import lemonsqueezy as ls
        from outreach import config as _cfg
        variant_map = {
            "small":  _cfg.LS_VARIANT_COIN_SMALL,
            "medium": _cfg.LS_VARIANT_COIN_MEDIUM,
            "large":  _cfg.LS_VARIANT_COIN_LARGE,
            "mega":   _cfg.LS_VARIANT_COIN_MEGA,
            "ultra":  _cfg.LS_VARIANT_COIN_ULTRA,
        }
        variant = variant_map.get(pack_key, "")
        if not variant:
            return jsonify(ok=False, error="Coin pack not configured for checkout."), 503
        cid = _cid()
        try:
            url = ls.create_checkout(
                variant,
                custom_data={"purpose": "coin_pack", "pack_key": pack_key, "client_id": str(cid)},
                email=session.get("email") or None,
                redirect_url=request.url_root.rstrip("/") + "/student/shop?bought=1",
            )
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500
        return jsonify(ok=True, checkout_url=url)


    @app.route("/api/student/wallet/set-banner", methods=["POST"])
    def student_wallet_set_banner_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.set_selected_banner(_cid(), str(data.get("banner_key") or "")))


    @app.route("/api/student/wallet/buy-flag", methods=["POST"])
    def student_wallet_buy_flag_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.buy_flag(_cid(), str(data.get("flag_key") or "")))


    @app.route("/api/student/wallet/set-flag", methods=["POST"])
    def student_wallet_set_flag_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.set_selected_flag(_cid(), str(data.get("flag_key") or "")))


    @app.route("/api/student/wallet/set-badge", methods=["POST"])
    def student_wallet_set_badge_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        side = str(data.get("side") or "right").lower()
        return jsonify(sdb.set_equipped_badge(_cid(), str(data.get("badge_key") or ""), side=side))


    @app.route("/api/student/wallet/set-cosmetic", methods=["POST"])
    def student_wallet_set_cosmetic_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.set_cosmetic(
            _cid(),
            str(data.get("kind") or ""),
            str(data.get("key") or ""),
        ))


    @app.route("/api/student/wallet/buy-bundle", methods=["POST"])
    def student_wallet_buy_bundle_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        return jsonify(sdb.buy_bundle(_cid(), str(data.get("bundle_key") or "")))


    @app.route("/api/student/wallet/use-freeze", methods=["POST"])
    def student_wallet_use_freeze_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.use_streak_freeze(_cid()))


    @app.route("/api/student/subscription/change", methods=["POST"])
    def student_subscription_change_api():
        """Switch student tier.

        - tier=free  -> cancel any existing LS subscription + flip back to free.
        - tier=plus / ultimate -> return a Lemon Squeezy hosted-checkout URL.
          The actual tier change happens server-side in the LS webhook.
        """
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        tier = str(data.get("tier") or "").strip().lower()
        try:
            from student import subscription as _sub
            if tier not in _sub.PLANS:
                return jsonify(ok=False, error="Unknown plan"), 400
            cid = _cid()
            if tier == "free":
                # Cancel the active LS subscription if we have its id stashed.
                try:
                    from outreach.db import get_db, _fetchone, _exec
                    from outreach import lemonsqueezy as ls
                    import json as _json
                    with get_db() as db:
                        row = _fetchone(db, "SELECT mail_preferences FROM clients WHERE id = %s", (cid,))
                    prefs = {}
                    try:
                        prefs = _json.loads((row or {}).get("mail_preferences") or "{}")
                    except Exception:
                        prefs = {}
                    sub_id = ((prefs.get("subscription") or {}).get("ls_sub_id")) or ""
                    if sub_id:
                        ls.cancel_subscription(sub_id)
                except Exception:
                    pass
                _sub.set_tier(cid, "free")
                return jsonify(ok=True, tier="free")
            # Upgrade -> hosted checkout
            from outreach import lemonsqueezy as ls
            from outreach import config as _cfg
            variant = _cfg.LS_VARIANT_STUDENT_PLUS if tier == "plus" else _cfg.LS_VARIANT_STUDENT_ULTIMATE
            if not variant:
                return jsonify(ok=False, error="Plan not configured for checkout."), 503
            url = ls.create_checkout(
                variant,
                custom_data={"purpose": "student_sub", "tier": tier, "client_id": str(cid)},
                email=session.get("email") or None,
                redirect_url=request.url_root.rstrip("/") + "/student/shop?upgraded=1",
            )
            return jsonify(ok=True, checkout_url=url)
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500


    # ================================================================
    #  Quiz Duels v2 — file-upload + AI-generated, synchronous play
    # ================================================================

    def _qd_can_view(d, cid):
        return d and cid in (d.get("challenger_id"), d.get("opponent_id"))

    @app.route("/api/student/duels/quiz/create", methods=["POST"])
    def student_duels_quiz_create_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        cid = _cid()
        try:
            opp = int(request.form.get("opponent_id") or 0)
        except Exception:
            return jsonify(ok=False, error="Invalid opponent_id"), 400
        topic = (request.form.get("topic") or "").strip()[:200]
        if opp == cid or opp <= 0:
            return jsonify(ok=False, error="Pick a friend to challenge."), 400
        # Must be friends
        f = sdb.list_friends(cid)
        if not any(x["id"] == opp for x in f["friends"]):
            return jsonify(ok=False, error="You must be friends to start a duel."), 400
        # Quiz duels are real-time — opponent must be online RIGHT NOW.
        if not sdb.is_user_online(opp):
            return jsonify(ok=False, error="That friend is offline. Quiz duels need both players online — try a Study Marathon instead, or wait until they come back."), 400
        # Avoid duplicate pending invite to same opponent
        existing = sdb.list_active_quiz_duels_for(cid)
        if any(
            x for x in existing
            if x["status"] in ("pending", "ready", "playing")
            and ((x["challenger_id"] == cid and x["opponent_id"] == opp)
                 or (x["opponent_id"] == cid and x["challenger_id"] == opp))
        ):
            return jsonify(ok=False, error="You already have an open quiz duel with this user."), 400

        # Free-tier quota: up to 3 quiz duels per day. Sending one also burns
        # the day's AI-quiz slot (handled inside subscription.can_send_quiz_duel_today).
        try:
            from student import subscription as _sub
            allowed, reason = _sub.can_send_quiz_duel_today(cid)
            if not allowed:
                return jsonify(ok=False, error=reason or "Daily quiz-duel limit reached."), 402
        except Exception:
            pass

        # File upload (PDF / DOCX / TXT). Hard-cap at 8 MB.
        f_in = request.files.get("file")
        if not f_in or not f_in.filename:
            return jsonify(ok=False, error="Upload a study file (PDF, DOCX, or TXT)."), 400
        raw = f_in.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return jsonify(ok=False, error="File too large (max 8 MB)."), 400
        fname = (f_in.filename or "")[:200]
        lname = fname.lower()
        try:
            if lname.endswith(".pdf"):
                text = extract_text_from_pdf(raw)
            elif lname.endswith(".docx"):
                text = extract_text_from_docx(raw)
            elif lname.endswith(".txt") or lname.endswith(".md"):
                text = raw.decode("utf-8", errors="ignore")
            else:
                return jsonify(ok=False, error="Unsupported file type. Use PDF, DOCX, or TXT."), 400
        except Exception as e:
            log.exception("Quiz-duel file extraction failed: %s", e)
            return jsonify(ok=False, error="Could not read that file."), 400
        text = (text or "").strip()
        if len(text) < 200:
            return jsonify(ok=False, error="The file has too little readable text to build a quiz."), 400

        try:
            qs = generate_quiz(
                course_name=topic or "Duel",
                topics=[topic] if topic else None,
                source_text=text,
                difficulty="medium",
                count=sdb.QUIZ_DUEL_QUESTION_COUNT,
            ) or []
        except Exception as e:
            log.exception("Quiz-duel AI generation failed: %s", e)
            return jsonify(ok=False, error="Quiz generation failed. Try again."), 500
        # Trim to exact count and ensure each Q has the expected shape
        clean = []
        for q in qs[: sdb.QUIZ_DUEL_QUESTION_COUNT]:
            if not isinstance(q, dict):
                continue
            if (q.get("correct") or "").strip().lower() not in ("a", "b", "c", "d"):
                continue
            if not all(q.get("option_" + k) for k in ("a", "b", "c", "d")):
                continue
            if not q.get("question"):
                continue
            clean.append({
                "question":    str(q["question"])[:600],
                "option_a":    str(q["option_a"])[:300],
                "option_b":    str(q["option_b"])[:300],
                "option_c":    str(q["option_c"])[:300],
                "option_d":    str(q["option_d"])[:300],
                "correct":     q["correct"].strip().lower(),
                "explanation": str(q.get("explanation") or "")[:500],
                "topic":       str(q.get("topic") or "")[:80],
            })
        if len(clean) < 4:
            return jsonify(ok=False, error="Could not generate enough quality questions from this file."), 500

        did = sdb.create_quiz_duel(cid, opp, clean, topic=topic, file_name=fname)
        try:
            from student import subscription as _sub
            _sub.record_generation(cid, "quiz_duel_sent")
        except Exception:
            pass
        return jsonify(ok=True, duel_id=did, count=len(clean))


    @app.route("/api/student/duels/quiz/<int:duel_id>/accept", methods=["POST"])
    def student_duels_quiz_accept_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.accept_quiz_duel(duel_id, _cid()))


    @app.route("/api/student/duels/quiz/<int:duel_id>/decline", methods=["POST"])
    def student_duels_quiz_decline_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        return jsonify(sdb.decline_quiz_duel(duel_id, _cid()))


    @app.route("/api/student/duels/quiz/<int:duel_id>/submit", methods=["POST"])
    def student_duels_quiz_submit_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        try:
            qi = int(data.get("question_idx"))
            tm = int(data.get("time_ms") or 0)
        except Exception:
            return jsonify(ok=False, error="Bad payload."), 400
        ans = (data.get("answer") or "").strip().lower()[:1]
        return jsonify(sdb.submit_duel_answer(duel_id, _cid(), qi, ans, tm))


    @app.route("/api/student/duels/quiz/<int:duel_id>/forfeit", methods=["POST"])
    def student_duels_quiz_forfeit_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "")[:80]
        return jsonify(sdb.forfeit_quiz_duel(duel_id, _cid(), reason=reason))


    @app.route("/api/student/duels/quiz/<int:duel_id>/state")
    def student_duels_quiz_state_api(duel_id):
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        cid = _cid()
        d = sdb.get_quiz_duel(duel_id, viewer_id=cid)
        if not d:
            return jsonify(ok=False, error="Not found."), 404
        if not _qd_can_view(d, cid):
            return jsonify(ok=False, error="Not your duel."), 403
        # Auto-expire ready/pending matches whose play TTL ran out without both
        # players finishing — counts as a tie.
        if d["status"] in ("ready", "playing"):
            try:
                exp = d.get("expires_at")
                exp_dt = exp if isinstance(exp, datetime) else datetime.fromisoformat(str(exp).replace(" ", "T")[:19])
                if datetime.now() > exp_dt and not (d.get("challenger_done") and d.get("opponent_done")):
                    # Mark whichever side is incomplete as not done -> just settle
                    # by current scores using settle_quiz_duel_if_done after
                    # forcing both done flags.
                    sdb._exec  # noqa: F401 (ensures attr exists)
                    from student import db as _sdb
                    with _sdb.get_db() as _db:
                        _sdb._exec(
                            _db,
                            "UPDATE student_quiz_duels SET challenger_done = %s, opponent_done = %s WHERE id = %s",
                            (True, True, duel_id) if _sdb._USE_PG else (1, 1, duel_id),
                        )
                    sdb.settle_quiz_duel_if_done(duel_id)
                    d = sdb.get_quiz_duel(duel_id, viewer_id=cid)
            except Exception:
                pass
        # Trim sensitive fields
        out = {
            "id": d["id"],
            "status": d["status"],
            "topic": d.get("topic", ""),
            "file_name": d.get("file_name", ""),
            "challenger_id": d["challenger_id"],
            "opponent_id": d["opponent_id"],
            "challenger_name": d.get("challenger_name", ""),
            "opponent_name": d.get("opponent_name", ""),
            "challenger_score": d.get("challenger_score", 0),
            "opponent_score": d.get("opponent_score", 0),
            "challenger_time_ms": d.get("challenger_time_ms", 0),
            "opponent_time_ms": d.get("opponent_time_ms", 0),
            "challenger_done": bool(d.get("challenger_done")),
            "opponent_done": bool(d.get("opponent_done")),
            "winner_id": d.get("winner_id"),
            "forfeit_by": d.get("forfeit_by"),
            "expires_at": str(d.get("expires_at") or ""),
            "questions": d.get("questions", []),  # already redacted if in-progress
            "me": cid,
        }
        return jsonify(out)


    @app.route("/api/student/duels/quiz/pending")
    def student_duels_quiz_pending_api():
        if not _logged_in():
            return jsonify(ok=False, error="Login required"), 401
        cid = _cid()
        pend = sdb.list_pending_quiz_duels_for(cid)
        active = sdb.list_active_quiz_duels_for(cid)
        # Active that the user can resume (status ready/playing where they're
        # still in the match)
        playable = [
            x for x in active
            if x["status"] in ("ready", "playing")
            and (cid == x["challenger_id"] or cid == x["opponent_id"])
        ]
        return jsonify(
            pending=[{
                "id": x["id"],
                "challenger_id": x["challenger_id"],
                "challenger_name": x.get("challenger_name", ""),
                "topic": x.get("topic", ""),
                "file_name": x.get("file_name", ""),
                "expires_at": str(x.get("expires_at") or ""),
            } for x in pend],
            playable=[{
                "id": x["id"],
                "status": x["status"],
                "challenger_id": x["challenger_id"],
                "opponent_id": x["opponent_id"],
                "challenger_name": x.get("challenger_name", ""),
                "opponent_name": x.get("opponent_name", ""),
                "topic": x.get("topic", ""),
            } for x in playable],
        )


    @app.route("/student/duels/quiz/<int:duel_id>/play")
    def student_duels_quiz_play_page(duel_id):
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        d = sdb.get_quiz_duel(duel_id, viewer_id=cid)
        if not d or cid not in (d["challenger_id"], d["opponent_id"]):
            return _s_render("Quiz Duel", "<div class='card' style='padding:40px;text-align:center'>Duel not found.</div>", active_page="student_friends")
        if d["status"] in ("settled", "tied", "forfeit", "declined", "expired"):
            return redirect(url_for("student_duels_quiz_result_page", duel_id=duel_id))
        # Auto-accept on first visit by the opponent (so play page can immediately render)
        if d["status"] == "pending" and cid == d["opponent_id"]:
            sdb.accept_quiz_duel(duel_id, cid)

        return _s_render("Quiz Duel", f"""
        <style>
          .qd {{ max-width: 760px; margin: 0 auto; }}
          .qd-h {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
          .qd-bar {{ display:flex; gap:6px; margin-bottom:18px; }}
          .qd-step {{ flex:1; height:6px; border-radius:3px; background:var(--border); }}
          .qd-step.done {{ background: linear-gradient(90deg,#22c55e,#16a34a); }}
          .qd-step.cur {{ background: linear-gradient(90deg,#6366f1,#8b5cf6); }}
          .qd-q {{ background: var(--card); border:1px solid var(--border); border-radius:14px; padding:24px; }}
          .qd-q h2 {{ margin:0 0 18px; font-size:18px; }}
          .qd-opt {{ display:block; width:100%; text-align:left; padding:14px 16px; margin:8px 0; background:var(--bg); border:1px solid var(--border); border-radius:10px; cursor:pointer; font-size:14px; color:var(--text); transition:all .12s; }}
          .qd-opt:hover {{ border-color:var(--primary); background: rgba(99,102,241,.06); }}
          .qd-opt.picked {{ border-color:var(--primary); background: rgba(99,102,241,.12); }}
          .qd-opt.correct {{ border-color:#22c55e; background:rgba(34,197,94,.16); color:#dcfce7; }}
          .qd-opt.wrong {{ border-color:#ef4444; background:rgba(239,68,68,.16); color:#fee2e2; }}
          .qd-opt[disabled] {{ cursor:default; opacity:.95; }}
          .qd-feedback {{ margin-top:12px; padding:10px 12px; border-radius:10px; font-size:13px; line-height:1.4; }}
          .qd-feedback.ok {{ background:rgba(34,197,94,.12); border:1px solid rgba(34,197,94,.45); color:#86efac; }}
          .qd-feedback.bad {{ background:rgba(239,68,68,.12); border:1px solid rgba(239,68,68,.45); color:#fca5a5; }}
          .qd-meta {{ display:flex; justify-content:space-between; font-size:12px; color:var(--text-muted); margin-bottom:12px; }}
          .qd-overlay {{ position:fixed; inset:0; background:rgba(0,0,0,.7); display:flex; align-items:center; justify-content:center; z-index:10000; }}
          .qd-overlay .box {{ background:var(--card); padding:32px; border-radius:14px; max-width:420px; text-align:center; }}
          .qd-warn {{ background:rgba(239,68,68,.1); border:1px solid #ef4444; color:#ef4444; padding:8px 12px; border-radius:8px; font-size:12px; margin-bottom:14px; }}
        </style>

        <div class="qd">
          <div class="qd-h">
            <div>
              <h1 style="margin:0;font-size:22px">⚔️ Quiz Duel</h1>
              <div style="font-size:12px;color:var(--text-muted)">vs <b id="qd-opp-name">…</b> · {d.get('topic','') or 'No topic'}</div>
            </div>
            <div style="text-align:right;font-size:12px;color:var(--text-muted)">
              <div>Time: <b id="qd-time">0.0s</b></div>
              <div>Score: <b id="qd-score">0</b> · Theirs: <b id="qd-their-score">0</b></div>
            </div>
          </div>

          <div class="qd-warn">⚠️ Anti-cheat: leaving this tab will instantly forfeit the duel.</div>

          <div class="qd-bar" id="qd-bar"></div>

          <div id="qd-stage">
            <div class="qd-q" style="text-align:center;color:var(--text-muted)">Loading questions…</div>
          </div>
        </div>

        <script>
        (function() {{
          const DUEL_ID = {duel_id};
          let questions = [];
          let idx = 0;
          let qStart = 0;
          let myScore = 0;
          let myDone = false;
          let busy = false;
          let forfeited = false;

          function fmtTime(ms) {{ return (ms/1000).toFixed(1) + 's'; }}

          async function poll() {{
            try {{
              const r = await fetch('/api/student/duels/quiz/' + DUEL_ID + '/state').then(r=>r.json());
              if (!r || r.error) return;
              const meIsChal = r.me === r.challenger_id;
              document.getElementById('qd-opp-name').textContent =
                meIsChal ? r.opponent_name : r.challenger_name;
              const polledMyScore = meIsChal ? r.challenger_score : r.opponent_score;
              const theirScore = meIsChal ? r.opponent_score : r.challenger_score;
              myScore = Math.max(myScore, Number(polledMyScore || 0));
              document.getElementById('qd-score').textContent = myScore;
              document.getElementById('qd-their-score').textContent = theirScore;
              if (questions.length === 0 && r.questions && r.questions.length) {{
                questions = r.questions;
                renderBar();
                renderQ();
              }}
              if (r.status === 'settled' || r.status === 'tied' || r.status === 'forfeit') {{
                mrGo('/student/duels/quiz/' + DUEL_ID + '/result');
              }}
            }} catch(e) {{}}
          }}

          function renderBar() {{
            const bar = document.getElementById('qd-bar');
            bar.innerHTML = questions.map((_, i) =>
              `<div class="qd-step ${{i < idx ? 'done' : (i === idx ? 'cur' : '')}}"></div>`
            ).join('');
          }}

          function renderQ() {{
            const stage = document.getElementById('qd-stage');
            if (idx >= questions.length) {{
              myDone = true;
              stage.innerHTML = '<div class="qd-q" style="text-align:center"><h2>✅ You finished!</h2><p style="color:var(--text-muted)">Waiting for your opponent to finish…</p></div>';
              return;
            }}
            const q = questions[idx];
            qStart = Date.now();
            stage.innerHTML = `
              <div class="qd-meta">
                <span>Question ${{idx+1}} / ${{questions.length}}</span>
                <span>${{q.topic || ''}}</span>
              </div>
              <div class="qd-q">
                <h2>${{escapeHtml(q.question)}}</h2>
                <button class="qd-opt" data-k="a">A) ${{escapeHtml(q.option_a)}}</button>
                <button class="qd-opt" data-k="b">B) ${{escapeHtml(q.option_b)}}</button>
                <button class="qd-opt" data-k="c">C) ${{escapeHtml(q.option_c)}}</button>
                <button class="qd-opt" data-k="d">D) ${{escapeHtml(q.option_d)}}</button>
                <div id="qd-feedback"></div>
              </div>`;
            stage.querySelectorAll('.qd-opt').forEach(b => {{
              b.addEventListener('click', () => answer(b.dataset.k));
            }});
          }}

          async function answer(k) {{
            if (busy) return;
            busy = true;
            const elapsed = Date.now() - qStart;
            const buttons = Array.from(document.querySelectorAll('.qd-opt'));
            buttons.forEach(b => b.disabled = true);
            const picked = buttons.find(b => b.dataset.k === k);
            if (picked) picked.classList.add('picked');
            const feedback = document.getElementById('qd-feedback');
            let shouldAdvance = false;
            try {{
              const r = await fetch('/api/student/duels/quiz/' + DUEL_ID + '/submit', {{
                method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{question_idx: idx, answer: k, time_ms: elapsed}}),
              }}).then(r=>r.json());
              if (r && r.ok) {{
                shouldAdvance = true;
                const correct = String(r.correct || '').toLowerCase();
                const correctBtn = buttons.find(b => b.dataset.k === correct);
                if (correctBtn) correctBtn.classList.add('correct');
                if (r.is_correct) {{
                  myScore++;
                  document.getElementById('qd-score').textContent = myScore;
                  if (feedback) feedback.innerHTML = '<div class="qd-feedback ok"><b>Correcto.</b>' + (r.explanation ? ' ' + escapeHtml(r.explanation) : '') + '</div>';
                }} else {{
                  if (picked) picked.classList.add('wrong');
                  const label = correct ? correct.toUpperCase() : '?';
                  if (feedback) feedback.innerHTML = '<div class="qd-feedback bad"><b>Incorrecto.</b> Respuesta correcta: ' + label + (r.correct_text ? ') ' + escapeHtml(r.correct_text) : '') + (r.explanation ? '<br>' + escapeHtml(r.explanation) : '') + '</div>';
                }}
                await sleep(r.is_correct ? 650 : 1100);
              }} else {{
                if (feedback) feedback.innerHTML = '<div class="qd-feedback bad">' + escapeHtml((r && r.error) || 'No se pudo guardar la respuesta.') + '</div>';
                await sleep(900);
              }}
            }} catch(e) {{
              if (feedback) feedback.innerHTML = '<div class="qd-feedback bad">No se pudo guardar la respuesta. Inténtalo de nuevo.</div>';
              buttons.forEach(b => b.disabled = false);
              if (picked) picked.classList.remove('picked');
              busy = false;
              return;
            }}
            if (!shouldAdvance) {{
              buttons.forEach(b => b.disabled = false);
              if (picked) picked.classList.remove('picked');
              busy = false;
              return;
            }}
            idx++;
            renderBar();
            renderQ();
            busy = false;
          }}

          function sleep(ms) {{ return new Promise(resolve => setTimeout(resolve, ms)); }}

          function escapeHtml(s) {{
            return String(s||'').replace(/[&<>'\\"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','\\'':'&#39;','\\"':'&quot;'}}[c]));
          }}

          // Anti-cheat: leaving the tab = instant loss
          document.addEventListener('visibilitychange', () => {{
            if (document.hidden && !myDone && !forfeited) {{
              forfeited = true;
              navigator.sendBeacon(
                '/api/student/duels/quiz/' + DUEL_ID + '/forfeit',
                new Blob([JSON.stringify({{reason:'tab_hidden'}})], {{type:'application/json'}})
              );
              // Also try a normal POST as a backup
              try {{
                fetch('/api/student/duels/quiz/' + DUEL_ID + '/forfeit', {{
                  method:'POST', keepalive:true,
                  headers:{{'Content-Type':'application/json'}},
                  body: JSON.stringify({{reason:'tab_hidden'}}),
                }});
              }} catch(e) {{}}
            }}
          }});

          // Live elapsed-time counter
          const startedAt = Date.now();
          setInterval(() => {{
            document.getElementById('qd-time').textContent = ((Date.now()-startedAt)/1000).toFixed(1) + 's';
          }}, 100);

          // Poll opponent state every 1s
          poll();
          setInterval(poll, 1000);
        }})();
        </script>
        """, active_page="student_duels")


    @app.route("/student/duels/quiz/<int:duel_id>/result")
    def student_duels_quiz_result_page(duel_id):
        if not _logged_in():
            return redirect(url_for("login"))
        cid = _cid()
        d = sdb.get_quiz_duel(duel_id, viewer_id=cid)
        if not d or cid not in (d["challenger_id"], d["opponent_id"]):
            return _s_render("Quiz Duel", "<div class='card' style='padding:40px;text-align:center'>Duel not found.</div>", active_page="student_friends")
        meIsChal = (cid == d["challenger_id"])
        my_score = d["challenger_score"] if meIsChal else d["opponent_score"]
        their_score = d["opponent_score"] if meIsChal else d["challenger_score"]
        their_name = d["opponent_name"] if meIsChal else d["challenger_name"]
        won = (d.get("winner_id") == cid)
        lost = (d.get("winner_id") and d.get("winner_id") != cid)
        tied = not d.get("winner_id") and d["status"] in ("tied", "settled")
        forfeit_msg = ""
        if d["status"] == "forfeit":
            if d.get("forfeit_by") == cid:
                forfeit_msg = "<div class='qd-tag' style='background:rgba(239,68,68,.15);color:#ef4444'>You forfeited (left the tab).</div>"
            else:
                forfeit_msg = "<div class='qd-tag' style='background:rgba(34,197,94,.15);color:#22c55e'>Opponent forfeited.</div>"
        if won:
            head = "<div style='font-size:48px'>🏆</div><h1 style='margin:8px 0'>You won!</h1>"
            payout = f"<div style='color:#22c55e;margin-top:6px'>+{sdb.QUIZ_DUEL_WIN_XP} XP · +{sdb.QUIZ_DUEL_WIN_COINS} coins</div>"
        elif lost:
            head = "<div style='font-size:48px'>💔</div><h1 style='margin:8px 0'>You lost</h1>"
            payout = "<div style='color:var(--text-muted);margin-top:6px'>No reward this time.</div>"
        elif tied:
            head = "<div style='font-size:48px'>🤝</div><h1 style='margin:8px 0'>It's a tie</h1>"
            payout = f"<div style='color:#f59e0b;margin-top:6px'>+{sdb.QUIZ_DUEL_TIE_XP} XP · +{sdb.QUIZ_DUEL_TIE_COINS} coins each</div>"
        else:
            head = "<h1>Duel ended</h1>"
            payout = ""
        # Per-question review (questions now include correct + explanation)
        review_rows = []
        for i, q in enumerate(d.get("questions", [])):
            review_rows.append(
                f"<div style='padding:10px 0;border-bottom:1px solid var(--border)'>"
                f"<div style='font-size:13px;font-weight:600'>Q{i+1}. {(q.get('question') or '')[:200]}</div>"
                f"<div style='font-size:12px;color:#22c55e;margin-top:4px'>Correct: {q.get('correct','').upper()}) {q.get('option_'+(q.get('correct') or 'a'),'')}</div>"
                f"</div>"
            )
        review_html = "".join(review_rows) or "<div style='color:var(--text-muted);font-size:13px'>No questions to review.</div>"

        return _s_render("Quiz Duel — Result", f"""
        <style>
          .qd-r {{ max-width: 720px; margin:0 auto; text-align:center; }}
          .qd-r .scorebox {{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:24px; margin:18px 0; }}
          .qd-r .vs {{ display:flex; justify-content:space-around; align-items:center; margin:14px 0; }}
          .qd-r .side .num {{ font-size:48px; font-weight:800; }}
          .qd-r .side .name {{ font-size:13px; color:var(--text-muted); margin-top:4px; }}
          .qd-tag {{ display:inline-block; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:600; margin:8px 0; }}
        </style>
        <div class="qd-r">
          {head}
          {forfeit_msg}
          {payout}
          <div class="scorebox">
            <div class="vs">
              <div class="side"><div class="num">{my_score}</div><div class="name">You</div></div>
              <div style="font-size:24px;color:var(--text-muted)">vs</div>
              <div class="side"><div class="num">{their_score}</div><div class="name">{their_name}</div></div>
            </div>
            <div style='font-size:12px;color:var(--text-muted)'>Topic: {d.get('topic','') or '—'} · File: {d.get('file_name','') or '—'}</div>
          </div>
          <div style="text-align:left;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;margin-top:18px">
            <h3 style="margin:0 0 8px;font-size:15px">Answer key</h3>
            {review_html}
          </div>
          <div style="margin-top:18px">
            <a class="btn btn-primary" href="/student/friends">Back to friends</a>
          </div>
        </div>
        """, active_page="student_friends")


    log.info("Student routes registered.")
