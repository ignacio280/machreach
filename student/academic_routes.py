"""
Academic routes — onboarding modal, leaderboards, and profile APIs.

Mounted by calling `register_academic_routes(app, csrf, limiter)` from
app.py, right after register_student_routes().

Endpoints
─────────
  GET   /api/academic/countries
  GET   /api/academic/universities?country=CL&q=<query>
  POST  /api/academic/universities          body: {name, country_iso}
  GET   /api/academic/majors?q=<query>&university_id=<id>
  POST  /api/academic/majors                body: {name, university_id}
  GET   /api/academic/profile
  POST  /api/academic/profile               body: {country_iso, university_id, major_id, canvas_url, canvas_token}
  POST  /api/academic/banner/seen
  GET   /api/academic/leaderboard?scope=global|country|university|major
  GET   /api/academic/ranks                 // compact summary for dashboard
  GET   /student/leaderboards               // full leaderboards page
"""

from __future__ import annotations

import logging
from flask import jsonify, request, session, render_template_string
from markupsafe import Markup

from student import academic as ac
from student import db as sdb


log = logging.getLogger("student.academic_routes")


def register_academic_routes(app, csrf, limiter):

    def _logged_in() -> bool:
        return "client_id" in session

    def _cid() -> int:
        return session["client_id"]

    # ── countries ───────────────────────────────────────────

    @app.route("/api/academic/countries", methods=["GET"])
    def academic_countries():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"countries": ac.list_countries()})

    # ── universities ────────────────────────────────────────

    @app.route("/api/academic/universities", methods=["GET"])
    def academic_universities():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        country = (request.args.get("country") or "").upper()[:2]
        q = (request.args.get("q") or "").strip()
        if not country:
            return jsonify({"error": "country required"}), 400
        rows = ac.search_universities(country, q, limit=25)
        return jsonify({"universities": rows})

    @app.route("/api/academic/universities", methods=["POST"])
    @limiter.limit("12 per hour")
    def academic_create_university():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        country_iso = (data.get("country_iso") or "").upper()[:2]
        if not name or len(name) < 3:
            return jsonify({"error": "name too short"}), 400
        if not country_iso:
            return jsonify({"error": "country required"}), 400
        univ_id = ac.create_university(name=name, country_iso=country_iso, created_by=_cid())
        return jsonify({"ok": True, "university": ac.get_university(univ_id)})

    # ── majors ──────────────────────────────────────────────

    @app.route("/api/academic/majors", methods=["GET"])
    def academic_majors():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        q = (request.args.get("q") or "").strip()
        univ_id_raw = request.args.get("university_id")
        univ_id = int(univ_id_raw) if (univ_id_raw or "").isdigit() else None
        rows = ac.search_majors(q, university_id=univ_id, limit=20) if q else []
        return jsonify({"majors": rows})

    @app.route("/api/academic/majors", methods=["POST"])
    @limiter.limit("20 per hour")
    def academic_create_major():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        univ_raw = data.get("university_id")
        univ_id = int(univ_raw) if isinstance(univ_raw, int) or (str(univ_raw).isdigit()) else None
        if not name or len(name) < 2:
            return jsonify({"error": "name too short"}), 400
        major_id = ac.create_major(name=name, university_id=univ_id, created_by=_cid())
        return jsonify({"ok": True, "major": ac.get_major(major_id)})

    # ── profile ─────────────────────────────────────────────

    @app.route("/api/academic/profile", methods=["GET"])
    def academic_profile_get():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        cid = _cid()
        prof = ac.get_academic_profile(cid)
        univ = ac.get_university(int(prof["university_id"])) if prof.get("university_id") else None
        major = ac.get_major(int(prof["major_id"])) if prof.get("major_id") else None
        return jsonify({
            "needs_setup": ac.needs_setup(cid),
            "country_iso": prof.get("country_iso") or "",
            "university": univ,
            "major": major,
            "xp_preserve_banner_seen": bool(prof.get("xp_preserve_banner_seen")),
        })

    @app.route("/api/academic/profile", methods=["POST"])
    @limiter.limit("10 per minute")
    def academic_profile_save():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True) or {}
        cid = _cid()
        country_iso = (data.get("country_iso") or "").upper()[:2]
        try:
            university_id = int(data.get("university_id"))
            major_id = int(data.get("major_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid ids"}), 400
        if not country_iso or not university_id or not major_id:
            return jsonify({"error": "missing fields"}), 400

        ac.save_academic_profile(
            client_id=cid,
            country_iso=country_iso,
            university_id=university_id,
            major_id=major_id,
        )

        # Optionally save Canvas credentials if provided (step 4 of the modal)
        canvas_url = (data.get("canvas_url") or "").strip()
        canvas_token = (data.get("canvas_token") or "").strip()
        canvas_saved = False
        if canvas_url and canvas_token:
            try:
                sdb.save_canvas_token(cid, canvas_url, canvas_token)
                canvas_saved = True
            except Exception as e:
                log.warning("canvas save failed: %s", e)

        return jsonify({"ok": True, "canvas_saved": canvas_saved})

    @app.route("/api/academic/banner/seen", methods=["POST"])
    def academic_banner_seen():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        ac.mark_welcome_banner_seen(_cid())
        return jsonify({"ok": True})

    # ── leaderboards ────────────────────────────────────────

    @app.route("/api/academic/leaderboard", methods=["GET"])
    def academic_leaderboard():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        scope = (request.args.get("scope") or "global").lower()
        if scope not in {"global", "country", "university", "major"}:
            return jsonify({"error": "bad scope"}), 400
        rows = ac.leaderboard(scope, _cid(), limit=100)
        return jsonify({"scope": scope, "rows": rows})

    @app.route("/api/academic/ranks", methods=["GET"])
    def academic_ranks():
        if not _logged_in():
            return jsonify({"error": "unauthorized"}), 401
        cid = _cid()
        summary = ac.ranks_summary(cid)
        # include league for me
        xp = summary.get("global", {}).get("xp", 0) if summary.get("global") else 0
        return jsonify({"ranks": summary, "league": ac.league_for_xp(int(xp))})

    @app.route("/student/leaderboards", methods=["GET"])
    def student_leaderboards_page():
        if not _logged_in():
            return _redirect_to_login()
        # The page itself is a thin HTML shell; it calls /api/academic/leaderboard
        # for each scope via fetch() and renders client-side.
        return render_template_string(_LEADERBOARDS_TEMPLATE, csrf=Markup(""))


def _redirect_to_login():
    from flask import redirect, url_for
    try:
        return redirect(url_for("login"))
    except Exception:
        return redirect("/login")


# ═══════════════════════════════════════════════════════════════════════
#  Leaderboards page — premium-looking dark UI, zero external deps.
# ═══════════════════════════════════════════════════════════════════════
_LEADERBOARDS_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Leaderboards · MachReach</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0A0E1A;
    --panel: #10172A;
    --panel-2: #141C36;
    --border: rgba(148, 163, 184, .12);
    --text: #E5EAF5;
    --muted: #8B93A7;
    --accent: #7C9CFF;
    --accent-2: #C084FC;
    --gold: #F5C451;
    --silver: #C7CED8;
    --bronze: #D68F5A;
  }
  * { box-sizing: border-box; }
  body {
    margin:0; background: radial-gradient(ellipse at top, #182143 0%, #0A0E1A 55%);
    color: var(--text); font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, Inter, sans-serif; min-height:100vh;
  }
  header.topbar {
    display:flex; align-items:center; justify-content:space-between;
    padding: 18px 28px; border-bottom: 1px solid var(--border);
    backdrop-filter: blur(20px);
  }
  header.topbar h1 { margin:0; font-size: 20px; letter-spacing: -.02em; }
  header.topbar a { color: var(--muted); text-decoration:none; font-size:14px; margin-left:16px;}
  header.topbar a:hover { color: var(--text);}
  .wrap { max-width: 1080px; margin: 28px auto 80px; padding: 0 24px;}
  .hero {
    background: linear-gradient(135deg, rgba(124,156,255,.12), rgba(192,132,252,.08));
    border: 1px solid var(--border);
    border-radius: 20px; padding: 28px 32px; margin-bottom: 24px;
    position:relative; overflow:hidden;
  }
  .hero::after {
    content:""; position:absolute; inset:auto -50px -80px auto; width:300px; height:300px;
    background: radial-gradient(circle, rgba(124,156,255,.35), transparent 70%);
    filter: blur(10px); pointer-events:none;
  }
  .hero h2 { margin:0 0 8px; font-size: 28px; letter-spacing:-.03em;}
  .hero p { margin:0; color: var(--muted);}
  .rank-strip {
    display:grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 20px;
  }
  .rank-card {
    background: rgba(255,255,255,.02); border:1px solid var(--border);
    border-radius: 14px; padding: 14px 16px;
  }
  .rank-card .label { color: var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.1em;}
  .rank-card .rank-big { font-size: 28px; font-weight:700; letter-spacing:-.02em; margin-top:4px;}
  .rank-card .of { color: var(--muted); font-size: 14px;}
  .tabs {
    display:flex; gap:6px; background: var(--panel);
    border:1px solid var(--border); border-radius: 14px; padding:6px; margin-bottom:16px;
  }
  .tab {
    flex:1; padding: 10px 14px; border-radius: 10px; text-align:center; cursor:pointer;
    color: var(--muted); font-weight:500; transition: all .18s;
    user-select:none;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    background: linear-gradient(135deg, rgba(124,156,255,.25), rgba(192,132,252,.2));
    color: var(--text);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.08);
  }
  .board {
    background: var(--panel); border:1px solid var(--border); border-radius:18px;
    overflow:hidden;
  }
  .row {
    display:grid; grid-template-columns: 56px 1fr 110px 110px;
    align-items:center; padding: 14px 20px; border-top:1px solid var(--border);
    transition: background .15s;
  }
  .row:first-child { border-top:none;}
  .row:hover { background: rgba(255,255,255,.02);}
  .row.me {
    background: linear-gradient(90deg, rgba(124,156,255,.15), transparent);
    border-left: 3px solid var(--accent);
  }
  .medal { font-size: 22px; text-align:center;}
  .pos { font-weight:700; text-align:center; color: var(--muted);}
  .who { display:flex; align-items:center; gap:10px;}
  .avatar {
    width:38px; height:38px; border-radius:50%;
    background: linear-gradient(135deg, #3B4A7A, #5B4694); display:flex;
    align-items:center; justify-content:center; font-weight:600; color:#fff;
  }
  .xp { font-variant-numeric: tabular-nums; color: var(--text); font-weight:600;}
  .league-pill {
    display:inline-block; padding: 3px 9px; font-size:11px; border-radius:999px;
    font-weight:600; letter-spacing:.02em;
  }
  .empty, .loading { padding: 36px 20px; text-align:center; color: var(--muted);}
  .skeleton { display:flex; flex-direction:column; gap: 10px; padding: 14px 20px;}
  .sk-row { height: 48px; background: linear-gradient(90deg, #141C36, #1A2340, #141C36);
    background-size: 200% 100%; border-radius: 10px; animation: shimmer 1.6s infinite linear;}
  @keyframes shimmer { from { background-position: 0 0;} to { background-position: -200% 0;}}
  @media (max-width: 720px) {
    .rank-strip { grid-template-columns: repeat(2, 1fr); }
    .row { grid-template-columns: 40px 1fr 80px; }
    .row .league-pill-col { display:none;}
  }
</style>
</head>
<body>
<header class="topbar">
  <h1>Leaderboards</h1>
  <nav>
    <a href="/student/dashboard">Dashboard</a>
    <a href="/student/study">Study</a>
    <a href="/logout">Logout</a>
  </nav>
</header>
<div class="wrap">
  <section class="hero">
    <h2>Climb the ranks.</h2>
    <p>Your XP is live-counted against every other student — globally, and in your country, university, and major.</p>
    <div class="rank-strip" id="rankStrip">
      <div class="rank-card"><div class="label">Global</div><div class="rank-big" id="r_global">—</div></div>
      <div class="rank-card"><div class="label">Country</div><div class="rank-big" id="r_country">—</div></div>
      <div class="rank-card"><div class="label">University</div><div class="rank-big" id="r_university">—</div></div>
      <div class="rank-card"><div class="label">Major</div><div class="rank-big" id="r_major">—</div></div>
    </div>
  </section>

  <div class="tabs" id="tabs">
    <div class="tab active" data-scope="global">🌍 Global</div>
    <div class="tab" data-scope="country">🏳️ Country</div>
    <div class="tab" data-scope="university">🎓 University</div>
    <div class="tab" data-scope="major">📚 Major</div>
  </div>

  <div class="board" id="board">
    <div class="skeleton">
      <div class="sk-row"></div><div class="sk-row"></div><div class="sk-row"></div>
      <div class="sk-row"></div><div class="sk-row"></div>
    </div>
  </div>
</div>

<script>
const medal = (rank) => rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : '';
const initials = (name) => (name||'?').split(/\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase();

async function loadRanks() {
  try {
    const r = await fetch('/api/academic/ranks');
    if (!r.ok) return;
    const j = await r.json();
    const fmt = (obj) => obj ? `#${obj.rank} <span class="of">/ ${obj.total}</span>` : '—';
    document.getElementById('r_global').innerHTML = fmt(j.ranks.global);
    document.getElementById('r_country').innerHTML = fmt(j.ranks.country);
    document.getElementById('r_university').innerHTML = fmt(j.ranks.university);
    document.getElementById('r_major').innerHTML = fmt(j.ranks.major);
  } catch(e) { console.error(e); }
}

async function loadScope(scope) {
  const board = document.getElementById('board');
  board.innerHTML = '<div class="skeleton"><div class="sk-row"></div><div class="sk-row"></div><div class="sk-row"></div><div class="sk-row"></div></div>';
  try {
    const r = await fetch('/api/academic/leaderboard?scope=' + encodeURIComponent(scope));
    const j = await r.json();
    const rows = j.rows || [];
    if (!rows.length) {
      board.innerHTML = `<div class="empty">No one to compare against yet in this scope. Invite some friends!</div>`;
      return;
    }
    board.innerHTML = rows.map(r => `
      <div class="row ${r.is_you?'me':''}">
        <div class="${r.rank<=3?'medal':'pos'}">${r.rank<=3 ? medal(r.rank) : '#'+r.rank}</div>
        <div class="who">
          <div class="avatar">${initials(r.name)}</div>
          <div><div>${escapeHtml(r.name)}${r.is_you?' <span style="color:var(--accent);font-size:12px;">(you)</span>':''}</div>
               <div class="league-pill-col"><span class="league-pill" style="background:${r.league_color}22;color:${r.league_color};">${r.league_name}</span></div></div>
        </div>
        <div class="xp">${r.xp.toLocaleString()} XP</div>
        <div class="league-pill-col"><span class="league-pill" style="background:${r.league_color}22;color:${r.league_color};">${r.league_name}</span></div>
      </div>
    `).join('');
  } catch(e) {
    board.innerHTML = `<div class="empty">Failed to load. ${e}</div>`;
  }
}

function escapeHtml(s){return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

document.getElementById('tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  loadScope(tab.dataset.scope);
});

loadRanks();
loadScope('global');
</script>
</body>
</html>
"""
