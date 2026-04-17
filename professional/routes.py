"""
MachReach Pro routes — productivity suite for business accounts:
Tasks, Time Tracker, Invoices, Expenses, Goals/OKRs, AI Writing Assistant.

Call register_professional_routes(app, csrf, limiter) from app.py.
"""
from __future__ import annotations

import html as html_module
import json
import logging
from datetime import datetime

from flask import jsonify, redirect, request, session, url_for, render_template_string
from markupsafe import Markup

log = logging.getLogger(__name__)


def register_professional_routes(app, csrf, limiter):
    from professional import db as pdb
    from professional import ai as pai

    def _esc(s) -> str:
        return html_module.escape(str(s)) if s else ""

    def _logged_in() -> bool:
        return "client_id" in session

    def _cid() -> int:
        return session["client_id"]

    def _p_render(title, content_html, active_page="pro"):
        from app import LAYOUT
        from outreach.db import get_client
        from outreach.i18n import t_dict
        flashed = list(session.pop("_flashes", []) if "_flashes" in session else [])
        nav = t_dict("nav")
        is_admin = False
        if _logged_in():
            c = get_client(session["client_id"])
            is_admin = bool(c and c.get("is_admin"))
        return render_template_string(
            LAYOUT,
            title=f"Pro — {title}",
            content=Markup(content_html),
            logged_in=_logged_in(),
            messages=flashed,
            active_page=active_page,
            client_name=session.get("client_name", ""),
            wide=False,
            nav=nav,
            lang=session.get("lang", "en"),
            is_admin=is_admin,
            account_type="business",
        )

    # ─────────────────────────────────────────────────────────
    # HUB PAGE
    # ─────────────────────────────────────────────────────────
    @app.route("/pro")
    def pro_hub():
        if not _logged_in():
            return redirect(url_for("login"))
        return _p_render("Pro Toolkit", """
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;flex-wrap:wrap;gap:12px;">
          <div>
            <h1 style="margin:0;font-size:28px;">&#128188; <span class="gradient-text">Pro Toolkit</span></h1>
            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Everything you need to run the business side of work &mdash; alongside your email outreach.</p>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;">
          <a href="/pro/tasks" class="fx-tile"><span class="fx-ico">&#9989;</span><b>Tasks</b><span>Prioritized to-dos, projects, due dates.</span></a>
          <a href="/pro/time" class="fx-tile"><span class="fx-ico">&#9201;&#65039;</span><b>Time Tracker</b><span>Billable hours by project + weekly summary.</span></a>
          <a href="/pro/invoices" class="fx-tile"><span class="fx-ico">&#128196;</span><b>Invoices</b><span>Create, email and print professional invoices.</span></a>
          <a href="/pro/expenses" class="fx-tile"><span class="fx-ico">&#128176;</span><b>Expenses</b><span>Track spending by category with monthly totals.</span></a>
          <a href="/pro/goals" class="fx-tile"><span class="fx-ico">&#127919;</span><b>Goals & OKRs</b><span>Quarterly objectives with measurable key results.</span></a>
          <a href="/pro/assistant" class="fx-tile"><span class="fx-ico">&#129504;</span><b>AI Assistant</b><span>Polish text, draft posts, agendas, proposals.</span></a>
          <a href="/pro/meeting-agenda" class="fx-tile"><span class="fx-ico">&#128197;</span><b>Meeting Agenda</b><span>Generate a tight agenda for any meeting.</span></a>
          <a href="/pro/cold-call" class="fx-tile"><span class="fx-ico">&#128222;</span><b>Cold Call Script</b><span>Ready-to-use B2B script with objection handling.</span></a>
          <a href="/pro/linkedin-post" class="fx-tile"><span class="fx-ico">&#128100;</span><b>LinkedIn Post</b><span>High-performing posts in your voice.</span></a>
          <a href="/pro/proposal" class="fx-tile"><span class="fx-ico">&#128221;</span><b>Proposal Outline</b><span>Full proposal skeleton in seconds.</span></a>
        </div>
        <style>
          .fx-tile { display:flex;flex-direction:column;gap:4px;padding:16px;border:1px solid var(--border);border-radius:14px;
            background:var(--card);text-decoration:none;color:var(--text);transition:all .18s ease; }
          .fx-tile:hover { border-color:var(--primary);transform:translateY(-2px);box-shadow:0 8px 20px rgba(99,102,241,.15); }
          .fx-tile .fx-ico { font-size:22px; }
          .fx-tile b { font-size:14px;font-weight:700; }
          .fx-tile span:last-child { font-size:12px;color:var(--text-muted);line-height:1.45; }
        </style>
        """, active_page="pro")

    # ─────────────────────────────────────────────────────────
    # TASKS
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/tasks")
    def pro_tasks_page():
        if not _logged_in():
            return redirect(url_for("login"))
        tasks = pdb.list_tasks(_cid())
        def task_card(t):
            prio_colors = {"high": "#EF4444", "medium": "#F59E0B", "low": "#10B981"}
            pc = prio_colors.get(t.get("priority", "medium"), "#94A3B8")
            done = t.get("status") == "done"
            strike = "text-decoration:line-through;opacity:0.55;" if done else ""
            checked = "checked" if done else ""
            due = t.get("due_date") or ""
            proj = t.get("project_tag") or ""
            proj_html = f"<span style='background:var(--bg);padding:2px 8px;border-radius:10px;font-size:11px;margin-left:6px;color:var(--text-muted)'>#{_esc(proj)}</span>" if proj else ""
            return f"""
            <div class="card" id="task-{t['id']}" style="padding:14px 18px;border-left:4px solid {pc};margin-bottom:10px;{strike}">
              <div style="display:flex;align-items:flex-start;gap:10px;">
                <input type="checkbox" {checked} onchange="toggleDone({t['id']},this.checked)" style="margin-top:4px;width:18px;height:18px;cursor:pointer;accent-color:var(--primary);">
                <div style="flex:1;min-width:0;">
                  <div style="font-weight:600;color:var(--text);">{_esc(t['title'])} {proj_html}</div>
                  {"<div style='color:var(--text-muted);font-size:13px;margin-top:4px;'>" + _esc(t.get('description','')) + "</div>" if t.get('description') else ""}
                  <div style="display:flex;gap:10px;align-items:center;margin-top:6px;font-size:12px;color:var(--text-muted);">
                    <span style="color:{pc};font-weight:600;">{t.get('priority','medium').upper()}</span>
                    {"&middot; Due " + _esc(due) if due else ""}
                  </div>
                </div>
                <button onclick="delTask({t['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:12px;">&#128465;</button>
              </div>
            </div>"""
        tasks_html = "".join(task_card(t) for t in tasks) or """
          <div class="empty"><div class="empty-icon">&#128221;</div><h3>No tasks yet</h3><p>Add your first task above.</p></div>"""

        return _p_render("Tasks", f"""
        <h1 style="margin-bottom:20px;">&#9989; Tasks</h1>
        <div class="card" style="margin-bottom:16px;">
          <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:8px;align-items:end;">
            <div><label style="font-size:12px;">Title</label><input id="nt-title" class="edit-input" placeholder="What needs doing?"></div>
            <div><label style="font-size:12px;">Priority</label>
              <select id="nt-prio" class="edit-input">
                <option value="high">High</option><option value="medium" selected>Medium</option><option value="low">Low</option>
              </select>
            </div>
            <div><label style="font-size:12px;">Due</label><input id="nt-due" type="date" class="edit-input"></div>
            <div><label style="font-size:12px;">Project</label><input id="nt-proj" class="edit-input" placeholder="tag"></div>
            <button onclick="addTask()" class="btn btn-primary btn-sm">+ Add</button>
          </div>
        </div>
        <div id="task-list">{tasks_html}</div>
        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function addTask(){{
          var title=document.getElementById('nt-title').value.trim();
          if(!title){{alert('Title required');return;}}
          var r=await fetch('/api/pro/tasks',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{title:title,priority:document.getElementById('nt-prio').value,due_date:document.getElementById('nt-due').value,project_tag:document.getElementById('nt-proj').value}})}});
          if(r.ok) location.reload();
          else alert('Failed');
        }}
        async function toggleDone(id,done){{
          await fetch('/api/pro/tasks/'+id,{{method:'PUT',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{status:done?'done':'todo'}})}});
          var row=document.getElementById('task-'+id);
          if(done){{row.style.textDecoration='line-through';row.style.opacity=0.55;}}else{{row.style.textDecoration='';row.style.opacity=1;}}
        }}
        async function delTask(id){{
          if(!confirm('Delete this task?')) return;
          await fetch('/api/pro/tasks/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        </script>
        """, active_page="pro_tasks")

    @app.route("/api/pro/tasks", methods=["POST"])
    def pro_task_create():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        title = (d.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        tid = pdb.create_task(
            _cid(), title,
            description=d.get("description", ""),
            priority=d.get("priority", "medium"),
            due_date=d.get("due_date", ""),
            project_tag=d.get("project_tag", ""),
        )
        return jsonify({"ok": True, "id": tid})

    @app.route("/api/pro/tasks/<int:task_id>", methods=["PUT"])
    def pro_task_update(task_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        fields = {k: v for k, v in d.items() if k in {"title", "description", "priority", "status", "due_date", "project_tag"}}
        if d.get("status") == "done":
            fields["completed_at"] = datetime.now().isoformat(timespec="seconds")
        pdb.update_task(task_id, _cid(), **fields)
        return jsonify({"ok": True})

    @app.route("/api/pro/tasks/<int:task_id>", methods=["DELETE"])
    def pro_task_delete(task_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_task(task_id, _cid())
        return jsonify({"ok": True})

    # ─────────────────────────────────────────────────────────
    # TIME TRACKER
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/time")
    def pro_time_page():
        if not _logged_in():
            return redirect(url_for("login"))
        running = pdb.get_running_timer(_cid())
        entries = pdb.list_time_entries(_cid(), days=30)
        summary = pdb.time_summary(_cid(), days=7)

        def fmt_dur(sec):
            sec = int(sec or 0)
            h, rem = divmod(sec, 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"

        rows_html = ""
        for e in entries:
            dur = fmt_dur(e.get("duration_seconds", 0))
            started = str(e.get("started_at", ""))[:16]
            ended = str(e.get("ended_at", ""))[:16] if e.get("ended_at") else "<em>running</em>"
            rate = float(e.get("hourly_rate") or 0)
            amount = rate * (int(e.get("duration_seconds") or 0) / 3600.0) if rate else 0
            rows_html += f"""<tr>
              <td>{_esc(e.get('project',''))}</td>
              <td style="font-size:13px;">{_esc(e.get('description',''))}</td>
              <td>{started}</td>
              <td>{ended}</td>
              <td style="font-family:monospace;">{dur}</td>
              <td>{'Yes' if e.get('billable') else 'No'}</td>
              <td>{('$' + f'{amount:,.2f}') if amount else '-'}</td>
              <td><button onclick="delEntry({e['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:11px;">&#10005;</button></td>
            </tr>"""
        if not rows_html:
            rows_html = "<tr><td colspan='8' style='text-align:center;padding:24px;color:var(--text-muted);'>No time entries yet.</td></tr>"

        by_proj_html = ""
        for p in (summary.get("by_project") or []):
            by_proj_html += f"<div style='display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;'><span>{_esc(p['project'])}</span><span style='font-family:monospace;'>{fmt_dur(p['seconds'])}</span></div>"

        running_html = ""
        if running:
            running_html = f"""
            <div class="card" style="background:linear-gradient(135deg,rgba(139,92,246,.12),rgba(99,102,241,.10));border-left:4px solid var(--primary);margin-bottom:16px;">
              <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
                <div>
                  <div style="font-size:12px;color:var(--text-muted);font-weight:600;">&#9199; RUNNING</div>
                  <div style="font-weight:700;font-size:16px;">{_esc(running.get('project','(no project)'))}</div>
                  <div style="color:var(--text-muted);font-size:13px;">{_esc(running.get('description',''))}</div>
                </div>
                <div style="text-align:right;">
                  <div id="live-timer" data-started="{running.get('started_at','')}" style="font-family:monospace;font-size:26px;font-weight:700;color:var(--primary);">00:00:00</div>
                  <button onclick="stopTimer({running['id']})" class="btn btn-outline btn-sm" style="margin-top:4px;">&#9724; Stop</button>
                </div>
              </div>
            </div>"""

        return _p_render("Time Tracker", f"""
        <h1 style="margin-bottom:20px;">&#9201;&#65039; Time Tracker</h1>
        {running_html}
        <div class="card" style="margin-bottom:16px;">
          <h3 style="margin:0 0 10px;font-size:15px;">Start a new timer</h3>
          <div style="display:grid;grid-template-columns:2fr 3fr 1fr 1fr auto;gap:8px;align-items:end;">
            <div><label style="font-size:12px;">Project</label><input id="tm-project" class="edit-input" placeholder="Acme Corp"></div>
            <div><label style="font-size:12px;">What are you working on?</label><input id="tm-desc" class="edit-input" placeholder="Design review"></div>
            <div><label style="font-size:12px;">Hourly rate ($)</label><input id="tm-rate" type="number" step="0.01" value="0" class="edit-input"></div>
            <div><label style="font-size:12px;">Billable</label>
              <select id="tm-bill" class="edit-input"><option value="1" selected>Yes</option><option value="0">No</option></select>
            </div>
            <button onclick="startTimer()" class="btn btn-primary btn-sm">&#9654; Start</button>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:3fr 1fr;gap:16px;">
          <div class="card" style="overflow:auto;">
            <h3 style="margin:0 0 10px;font-size:15px;">Last 30 days</h3>
            <table><thead><tr><th>Project</th><th>Description</th><th>Started</th><th>Ended</th><th>Duration</th><th>Billable</th><th>Amount</th><th></th></tr></thead><tbody>{rows_html}</tbody></table>
          </div>
          <div class="card">
            <h3 style="margin:0 0 10px;font-size:15px;">Last 7 days</h3>
            <div style="font-size:12px;color:var(--text-muted);">Total</div>
            <div style="font-family:monospace;font-size:22px;font-weight:700;">{fmt_dur(summary['total_seconds'])}</div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:10px;">Billable</div>
            <div style="font-family:monospace;font-size:18px;font-weight:700;color:var(--green);">{fmt_dur(summary['billable_seconds'])}</div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:14px;">By project</div>
            {by_proj_html or "<div style='color:var(--text-muted);font-size:12px;'>No data yet</div>"}
          </div>
        </div>

        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function startTimer(){{
          var proj=document.getElementById('tm-project').value.trim();
          var r=await fetch('/api/pro/time/start',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{project:proj,description:document.getElementById('tm-desc').value,hourly_rate:parseFloat(document.getElementById('tm-rate').value)||0,billable:document.getElementById('tm-bill').value==='1'}})}});
          if(r.ok) location.reload(); else alert('Failed');
        }}
        async function stopTimer(id){{
          var r=await fetch('/api/pro/time/'+id+'/stop',{{method:'POST',headers:csrfHeader()}});
          if(r.ok) location.reload();
        }}
        async function delEntry(id){{
          if(!confirm('Delete this entry?')) return;
          await fetch('/api/pro/time/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        // Live running timer
        var live=document.getElementById('live-timer');
        if(live){{
          var startedStr=live.dataset.started;
          var started=new Date(startedStr.replace(' ','T'));
          function tick(){{
            var s=Math.floor((Date.now()-started.getTime())/1000);
            if(s<0) s=0;
            var h=String(Math.floor(s/3600)).padStart(2,'0');
            var m=String(Math.floor((s%3600)/60)).padStart(2,'0');
            var ss=String(s%60).padStart(2,'0');
            live.textContent=h+':'+m+':'+ss;
          }}
          tick(); setInterval(tick,1000);
        }}
        </script>
        """, active_page="pro_time")

    @app.route("/api/pro/time/start", methods=["POST"])
    def pro_time_start():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        eid = pdb.start_timer(
            _cid(),
            project=d.get("project", ""),
            description=d.get("description", ""),
            billable=bool(d.get("billable", True)),
            hourly_rate=float(d.get("hourly_rate") or 0),
        )
        return jsonify({"ok": True, "id": eid})

    @app.route("/api/pro/time/<int:entry_id>/stop", methods=["POST"])
    def pro_time_stop(entry_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.stop_timer(_cid(), entry_id)
        return jsonify({"ok": True})

    @app.route("/api/pro/time/<int:entry_id>", methods=["DELETE"])
    def pro_time_delete(entry_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_time_entry(entry_id, _cid())
        return jsonify({"ok": True})

    # ─────────────────────────────────────────────────────────
    # INVOICES
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/invoices")
    def pro_invoices_page():
        if not _logged_in():
            return redirect(url_for("login"))
        invoices = pdb.list_invoices(_cid())
        status_colors = {"paid": "#10B981", "sent": "#6366F1", "overdue": "#EF4444", "draft": "#94A3B8"}
        rows_html = ""
        for inv in invoices:
            st = inv.get("status", "draft")
            color = status_colors.get(st, "#94A3B8")
            subtotal = float(inv.get("subtotal") or 0)
            tax = subtotal * float(inv.get("tax_rate") or 0) / 100
            total = subtotal + tax
            rows_html += f"""<tr>
              <td style="font-weight:600;"><a href="/pro/invoices/{inv['id']}" style="color:var(--primary);text-decoration:none;">{_esc(inv.get('invoice_number',''))}</a></td>
              <td>{_esc(inv.get('bill_to_name',''))}</td>
              <td>{_esc(inv.get('issue_date','') or '-')}</td>
              <td>{_esc(inv.get('due_date','') or '-')}</td>
              <td style="font-family:monospace;">{inv.get('currency','USD')} {total:,.2f}</td>
              <td><span style="background:{color}22;color:{color};padding:3px 10px;border-radius:10px;font-size:11px;font-weight:700;">{st.upper()}</span></td>
              <td>
                <a href="/pro/invoices/{inv['id']}" class="btn btn-ghost btn-sm" style="font-size:11px;">View</a>
                <button onclick="delInv({inv['id']})" class="btn btn-ghost btn-sm" style="font-size:11px;color:var(--red);">&#10005;</button>
              </td>
            </tr>"""
        if not rows_html:
            rows_html = "<tr><td colspan='7' style='text-align:center;padding:24px;color:var(--text-muted);'>No invoices yet.</td></tr>"

        return _p_render("Invoices", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:8px;">
          <h1>&#128196; Invoices</h1>
          <a href="/pro/invoices/new" class="btn btn-primary btn-sm">+ New Invoice</a>
        </div>
        <div class="card" style="overflow:auto;">
          <table><thead><tr><th>Number</th><th>Bill To</th><th>Issued</th><th>Due</th><th>Total</th><th>Status</th><th></th></tr></thead><tbody>{rows_html}</tbody></table>
        </div>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function delInv(id){{
          if(!confirm('Delete this invoice?')) return;
          await fetch('/api/pro/invoices/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        </script>
        """, active_page="pro_invoices")

    @app.route("/pro/invoices/new", methods=["GET"])
    def pro_invoice_new_page():
        if not _logged_in():
            return redirect(url_for("login"))
        return _invoice_editor(inv_id=None)

    @app.route("/pro/invoices/<int:inv_id>", methods=["GET"])
    def pro_invoice_detail_page(inv_id):
        if not _logged_in():
            return redirect(url_for("login"))
        return _invoice_editor(inv_id=inv_id)

    def _invoice_editor(inv_id):
        inv = pdb.get_invoice(inv_id, _cid()) if inv_id else None
        if inv_id and not inv:
            return redirect(url_for("pro_invoices_page"))
        default_num = pdb.next_invoice_number(_cid()) if not inv else inv["invoice_number"]
        today = datetime.now().strftime("%Y-%m-%d")
        items_json = json.dumps([dict(i) for i in (inv["items"] if inv else [])])
        return _p_render("Invoice", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px;">
          <a href="/pro/invoices" style="color:var(--text-muted);text-decoration:none;font-size:13px;">&larr; Back</a>
          <div style="display:flex;gap:8px;">
            <button onclick="window.print()" class="btn btn-outline btn-sm">&#128424; Print / PDF</button>
            <button onclick="saveInvoice()" class="btn btn-primary btn-sm" id="save-btn">&#128190; Save</button>
          </div>
        </div>
        <div class="card" id="invoice-root">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
            <div>
              <div class="form-group"><label>Invoice Number</label><input id="inv-number" class="edit-input" value="{_esc((inv or {}).get('invoice_number', default_num))}"></div>
              <div class="form-group"><label>Issue Date</label><input id="inv-issue" type="date" class="edit-input" value="{_esc((inv or {}).get('issue_date', today) or today)}"></div>
              <div class="form-group"><label>Due Date</label><input id="inv-due" type="date" class="edit-input" value="{_esc((inv or {}).get('due_date','') or '')}"></div>
              <div class="form-group"><label>Currency</label>
                <select id="inv-currency" class="edit-input">
                  {"".join([f'<option value="{c}" {"selected" if (inv or {}).get("currency", "USD") == c else ""}>{c}</option>' for c in ["USD","EUR","GBP","MXN","CAD","AUD","JPY","BRL","COP","CLP","ARS"]])}
                </select>
              </div>
              <div class="form-group"><label>Status</label>
                <select id="inv-status" class="edit-input">
                  {"".join([f'<option value="{s}" {"selected" if (inv or {}).get("status", "draft") == s else ""}>{s.title()}</option>' for s in ["draft","sent","paid","overdue"]])}
                </select>
              </div>
            </div>
            <div>
              <div class="form-group"><label>Bill To Name</label><input id="inv-name" class="edit-input" value="{_esc((inv or {}).get('bill_to_name',''))}"></div>
              <div class="form-group"><label>Bill To Email</label><input id="inv-email" type="email" class="edit-input" value="{_esc((inv or {}).get('bill_to_email',''))}"></div>
              <div class="form-group"><label>Bill To Address</label><textarea id="inv-addr" class="edit-input" rows="3">{_esc((inv or {}).get('bill_to_addr',''))}</textarea></div>
              <div class="form-group"><label>Tax Rate (%)</label><input id="inv-tax" type="number" step="0.01" class="edit-input" value="{(inv or {}).get('tax_rate', 0)}" onchange="recalc()"></div>
            </div>
          </div>

          <h3 style="margin:20px 0 10px;">Line items</h3>
          <table id="items-table">
            <thead><tr><th style="width:50%;">Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th><th></th></tr></thead>
            <tbody id="items-body"></tbody>
          </table>
          <button onclick="addItem()" class="btn btn-outline btn-sm" style="margin-top:8px;">+ Add line</button>

          <div style="margin-top:20px;text-align:right;border-top:2px solid var(--border);padding-top:14px;">
            <div style="display:inline-block;text-align:left;min-width:280px;">
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span style="color:var(--text-muted);">Subtotal</span><span id="inv-subtotal" style="font-family:monospace;">0.00</span></div>
              <div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span style="color:var(--text-muted);">Tax</span><span id="inv-taxamt" style="font-family:monospace;">0.00</span></div>
              <div style="display:flex;justify-content:space-between;font-weight:700;font-size:18px;border-top:1px solid var(--border);padding-top:6px;"><span>Total</span><span id="inv-total" style="font-family:monospace;">0.00</span></div>
            </div>
          </div>

          <div class="form-group" style="margin-top:18px;"><label>Notes</label><textarea id="inv-notes" class="edit-input" rows="3" placeholder="Payment terms, thank you note, wire info...">{_esc((inv or {}).get('notes',''))}</textarea></div>
        </div>
        <style>
          @media print {{ .nav, .hamburger, button, a.btn, a[href="/pro/invoices"] {{ display:none!important; }} .card{{box-shadow:none;border:none;}} }}
          .edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}
          .edit-input:focus{{border-color:var(--primary);outline:none;}}
        </style>
        <script>
        var INV_ID = {inv_id if inv_id else 'null'};
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        function itemRow(it){{
          var tr=document.createElement('tr');
          tr.innerHTML='<td><input class="edit-input itm-desc" value="'+escAttr(it.description||'')+'"></td>'
            +'<td><input type="number" class="edit-input itm-qty" value="'+(it.quantity||1)+'" step="0.01" style="width:80px;" onchange="recalc()"></td>'
            +'<td><input type="number" class="edit-input itm-price" value="'+(it.unit_price||0)+'" step="0.01" style="width:100px;" onchange="recalc()"></td>'
            +'<td style="font-family:monospace;" class="itm-amt">0.00</td>'
            +'<td><button onclick="this.closest(\\'tr\\').remove();recalc()" class="btn btn-ghost btn-sm" style="color:var(--red);">&#10005;</button></td>';
          return tr;
        }}
        function escAttr(s){{return String(s).replace(/"/g,'&quot;');}}
        function addItem(){{
          document.getElementById('items-body').appendChild(itemRow({{}}));
          recalc();
        }}
        function recalc(){{
          var sub=0;
          document.querySelectorAll('#items-body tr').forEach(function(tr){{
            var q=parseFloat(tr.querySelector('.itm-qty').value)||0;
            var p=parseFloat(tr.querySelector('.itm-price').value)||0;
            var a=q*p;
            tr.querySelector('.itm-amt').textContent=a.toFixed(2);
            sub+=a;
          }});
          var tr_=parseFloat(document.getElementById('inv-tax').value)||0;
          var tax=sub*tr_/100;
          document.getElementById('inv-subtotal').textContent=sub.toFixed(2);
          document.getElementById('inv-taxamt').textContent=tax.toFixed(2);
          document.getElementById('inv-total').textContent=(sub+tax).toFixed(2);
        }}
        async function saveInvoice(){{
          var items=[];
          document.querySelectorAll('#items-body tr').forEach(function(tr){{
            items.push({{description:tr.querySelector('.itm-desc').value,quantity:parseFloat(tr.querySelector('.itm-qty').value)||0,unit_price:parseFloat(tr.querySelector('.itm-price').value)||0}});
          }});
          var data={{
            invoice_number:document.getElementById('inv-number').value,
            bill_to_name:document.getElementById('inv-name').value,
            bill_to_email:document.getElementById('inv-email').value,
            bill_to_addr:document.getElementById('inv-addr').value,
            issue_date:document.getElementById('inv-issue').value,
            due_date:document.getElementById('inv-due').value,
            notes:document.getElementById('inv-notes').value,
            status:document.getElementById('inv-status').value,
            tax_rate:parseFloat(document.getElementById('inv-tax').value)||0,
            currency:document.getElementById('inv-currency').value,
            items:items
          }};
          var btn=document.getElementById('save-btn');
          btn.disabled=true; btn.textContent='Saving...';
          var url=INV_ID?('/api/pro/invoices/'+INV_ID):'/api/pro/invoices';
          var method=INV_ID?'PUT':'POST';
          var r=await fetch(url,{{method:method,headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify(data)}});
          var d=await r.json().catch(function(){{return{{}};}});
          if(r.ok){{
            if(!INV_ID && d.id) window.location='/pro/invoices/'+d.id;
            else{{ btn.textContent='Saved \\u2713'; setTimeout(function(){{btn.textContent='\\ud83d\\udcbe Save';btn.disabled=false;}},1500); }}
          }} else {{ alert(d.error||'Save failed'); btn.disabled=false; btn.textContent='\\ud83d\\udcbe Save'; }}
        }}
        // Load existing items
        var existing = {items_json};
        if(existing.length){{
          existing.forEach(function(it){{ document.getElementById('items-body').appendChild(itemRow(it)); }});
        }} else {{
          addItem();
        }}
        recalc();
        </script>
        """, active_page="pro_invoices")

    @app.route("/api/pro/invoices", methods=["POST"])
    def pro_invoice_create():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        iid = pdb.create_invoice(_cid(), d)
        return jsonify({"ok": True, "id": iid})

    @app.route("/api/pro/invoices/<int:inv_id>", methods=["PUT"])
    def pro_invoice_update(inv_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.update_invoice(inv_id, _cid(), request.get_json(force=True) or {})
        return jsonify({"ok": True})

    @app.route("/api/pro/invoices/<int:inv_id>", methods=["DELETE"])
    def pro_invoice_delete(inv_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_invoice(inv_id, _cid())
        return jsonify({"ok": True})

    # ─────────────────────────────────────────────────────────
    # EXPENSES
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/expenses")
    def pro_expenses_page():
        if not _logged_in():
            return redirect(url_for("login"))
        expenses = pdb.list_expenses(_cid(), days=90)
        summary = pdb.expense_summary(_cid(), days=30)
        rows = ""
        for x in expenses:
            rows += f"""<tr>
              <td>{_esc(x.get('expense_date','') or '-')}</td>
              <td>{_esc(x.get('category',''))}</td>
              <td>{_esc(x.get('vendor',''))}</td>
              <td>{_esc(x.get('description',''))}</td>
              <td style="font-family:monospace;text-align:right;">{x.get('currency','USD')} {float(x.get('amount') or 0):,.2f}</td>
              <td><button onclick="delExp({x['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:11px;">&#10005;</button></td>
            </tr>"""
        if not rows:
            rows = "<tr><td colspan='6' style='text-align:center;padding:24px;color:var(--text-muted);'>No expenses yet.</td></tr>"

        cat_html = ""
        for cat in summary.get("by_category", []):
            cat_html += f"<div style='display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;'><span>{_esc(cat['category'].title())}</span><span style='font-family:monospace;'>${float(cat['total']):,.2f}</span></div>"

        cat_opts = "".join(f'<option value="{c}">{c.title()}</option>' for c in pdb.EXPENSE_CATEGORIES)
        today = datetime.now().strftime("%Y-%m-%d")

        return _p_render("Expenses", f"""
        <h1 style="margin-bottom:20px;">&#128176; Expenses</h1>
        <div class="card" style="margin-bottom:16px;">
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr 2fr 1fr 1fr auto;gap:8px;align-items:end;">
            <div><label style="font-size:12px;">Date</label><input id="ex-date" type="date" class="edit-input" value="{today}"></div>
            <div><label style="font-size:12px;">Category</label><select id="ex-cat" class="edit-input">{cat_opts}</select></div>
            <div><label style="font-size:12px;">Vendor</label><input id="ex-vendor" class="edit-input" placeholder="Notion"></div>
            <div><label style="font-size:12px;">Description</label><input id="ex-desc" class="edit-input" placeholder="Team plan subscription"></div>
            <div><label style="font-size:12px;">Amount</label><input id="ex-amount" type="number" step="0.01" class="edit-input" placeholder="0.00"></div>
            <div><label style="font-size:12px;">Currency</label><select id="ex-currency" class="edit-input"><option>USD</option><option>EUR</option><option>GBP</option><option>MXN</option><option>CAD</option></select></div>
            <button onclick="addExpense()" class="btn btn-primary btn-sm">+ Add</button>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:3fr 1fr;gap:16px;">
          <div class="card" style="overflow:auto;">
            <h3 style="margin:0 0 10px;font-size:15px;">Last 90 days</h3>
            <table><thead><tr><th>Date</th><th>Category</th><th>Vendor</th><th>Description</th><th style="text-align:right;">Amount</th><th></th></tr></thead><tbody>{rows}</tbody></table>
          </div>
          <div class="card">
            <h3 style="margin:0 0 10px;font-size:15px;">Last 30 days</h3>
            <div style="font-size:12px;color:var(--text-muted);">Total</div>
            <div style="font-family:monospace;font-size:22px;font-weight:700;">${summary['total']:,.2f}</div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:14px;">By category</div>
            {cat_html or "<div style='color:var(--text-muted);font-size:12px;'>No data yet</div>"}
          </div>
        </div>
        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function addExpense(){{
          var amt=parseFloat(document.getElementById('ex-amount').value);
          if(!amt||amt<=0){{alert('Enter an amount');return;}}
          var r=await fetch('/api/pro/expenses',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{amount:amt,category:document.getElementById('ex-cat').value,vendor:document.getElementById('ex-vendor').value,description:document.getElementById('ex-desc').value,expense_date:document.getElementById('ex-date').value,currency:document.getElementById('ex-currency').value}})}});
          if(r.ok) location.reload(); else alert('Failed');
        }}
        async function delExp(id){{
          if(!confirm('Delete?')) return;
          await fetch('/api/pro/expenses/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        </script>
        """, active_page="pro_expenses")

    @app.route("/api/pro/expenses", methods=["POST"])
    def pro_expense_create():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        try:
            amt = float(d.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid amount"}), 400
        if amt <= 0:
            return jsonify({"error": "amount must be > 0"}), 400
        eid = pdb.create_expense(
            _cid(), amt,
            category=d.get("category", "other"),
            description=d.get("description", ""),
            expense_date=d.get("expense_date", ""),
            vendor=d.get("vendor", ""),
            currency=d.get("currency", "USD"),
        )
        return jsonify({"ok": True, "id": eid})

    @app.route("/api/pro/expenses/<int:exp_id>", methods=["DELETE"])
    def pro_expense_delete(exp_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_expense(exp_id, _cid())
        return jsonify({"ok": True})

    # ─────────────────────────────────────────────────────────
    # GOALS / OKRs
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/goals")
    def pro_goals_page():
        if not _logged_in():
            return redirect(url_for("login"))
        goals = pdb.list_goals(_cid())
        goals_html = ""
        for g in goals:
            krs_html = ""
            for kr in g.get("key_results", []):
                pct = min(100, (float(kr["current"] or 0) / float(kr["target"] or 1)) * 100)
                krs_html += f"""
                <div style="padding:10px 0;border-top:1px solid var(--border);">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span style="font-weight:500;">{_esc(kr['title'])}</span>
                    <div style="display:flex;gap:6px;align-items:center;">
                      <input type="number" value="{kr['current']}" step="0.01" style="width:90px;" class="edit-input" onchange="setKR({kr['id']},this.value)">
                      <span style="color:var(--text-muted);font-size:12px;">/ {kr['target']} {_esc(kr.get('unit','%'))}</span>
                    </div>
                  </div>
                  <div style="background:var(--bg);height:8px;border-radius:8px;margin-top:6px;overflow:hidden;">
                    <div style="background:linear-gradient(90deg,var(--primary),#8B5CF6);height:100%;width:{pct:.1f}%;border-radius:8px;"></div>
                  </div>
                </div>"""
            status_chip = {"active": "#6366F1", "done": "#10B981", "on_hold": "#94A3B8"}.get(g.get("status", "active"), "#6366F1")
            goals_html += f"""
            <div class="card" style="margin-bottom:14px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap;">
                <div style="flex:1;min-width:0;">
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <h3 style="margin:0;font-size:16px;">{_esc(g['title'])}</h3>
                    <span style="background:{status_chip}22;color:{status_chip};padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;">Q{g['quarter']} {g['year']}</span>
                    <span style="color:var(--text-muted);font-size:13px;">&middot; {g['progress_pct']}% complete</span>
                  </div>
                  {"<p style='color:var(--text-muted);font-size:13px;margin:6px 0 0;'>" + _esc(g.get('description','')) + "</p>" if g.get('description') else ""}
                </div>
                <button onclick="delGoal({g['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:11px;">&#10005;</button>
              </div>
              {krs_html or "<div style='color:var(--text-muted);font-size:12px;margin-top:10px;'>No key results yet</div>"}
            </div>"""
        if not goals_html:
            goals_html = "<div class='empty'><div class='empty-icon'>&#127919;</div><h3>Set your first quarterly goal</h3></div>"

        year = datetime.now().year
        q = (datetime.now().month - 1) // 3 + 1

        return _p_render("Goals & OKRs", f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:8px;">
          <h1>&#127919; Goals & OKRs</h1>
          <button onclick="document.getElementById('gform').style.display='block'" class="btn btn-primary btn-sm">+ New Goal</button>
        </div>

        <div id="gform" class="card" style="display:none;margin-bottom:16px;">
          <h3 style="margin:0 0 10px;">New quarterly goal</h3>
          <div class="form-group"><label>Title</label><input id="g-title" class="edit-input" placeholder="Reach $50k MRR"></div>
          <div class="form-group"><label>Description</label><textarea id="g-desc" class="edit-input" rows="2"></textarea></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <div class="form-group"><label>Quarter</label>
              <select id="g-q" class="edit-input">
                {"".join([f'<option value="{i}" {"selected" if i == q else ""}>Q{i}</option>' for i in [1, 2, 3, 4]])}
              </select>
            </div>
            <div class="form-group"><label>Year</label><input id="g-year" type="number" class="edit-input" value="{year}"></div>
          </div>
          <h4 style="margin:10px 0 6px;">Key results (measurable)</h4>
          <div id="krs-box"></div>
          <button onclick="addKR()" class="btn btn-outline btn-sm" style="margin-top:6px;">+ Add key result</button>
          <div style="margin-top:12px;display:flex;gap:8px;">
            <button onclick="document.getElementById('gform').style.display='none'" class="btn btn-ghost btn-sm">Cancel</button>
            <button onclick="saveGoal()" class="btn btn-primary btn-sm">Save goal</button>
          </div>
        </div>

        {goals_html}
        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        function addKR(){{
          var d=document.createElement('div');
          d.className='kr-row';
          d.style='display:grid;grid-template-columns:3fr 1fr 1fr 1fr auto;gap:6px;margin-bottom:6px;align-items:center;';
          d.innerHTML='<input class="edit-input kr-t" placeholder="Key result title">'
            +'<input class="edit-input kr-target" type="number" placeholder="Target" value="100">'
            +'<input class="edit-input kr-current" type="number" placeholder="Current" value="0">'
            +'<input class="edit-input kr-unit" placeholder="unit" value="%">'
            +'<button onclick="this.parentElement.remove()" class="btn btn-ghost btn-sm" style="color:var(--red);">&#10005;</button>';
          document.getElementById('krs-box').appendChild(d);
        }}
        addKR();
        async function saveGoal(){{
          var krs=[];
          document.querySelectorAll('.kr-row').forEach(function(r){{
            var t=r.querySelector('.kr-t').value.trim();
            if(!t) return;
            krs.push({{title:t,target:parseFloat(r.querySelector('.kr-target').value)||100,current:parseFloat(r.querySelector('.kr-current').value)||0,unit:r.querySelector('.kr-unit').value||'%'}});
          }});
          var r=await fetch('/api/pro/goals',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{title:document.getElementById('g-title').value,description:document.getElementById('g-desc').value,quarter:parseInt(document.getElementById('g-q').value),year:parseInt(document.getElementById('g-year').value),key_results:krs}})}});
          if(r.ok) location.reload(); else alert('Failed');
        }}
        async function setKR(id,val){{
          await fetch('/api/pro/goals/kr/'+id,{{method:'PUT',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{current:parseFloat(val)||0}})}});
        }}
        async function delGoal(id){{
          if(!confirm('Delete this goal and all its key results?')) return;
          await fetch('/api/pro/goals/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        </script>
        """, active_page="pro_goals")

    @app.route("/api/pro/goals", methods=["POST"])
    def pro_goal_create():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        title = (d.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        gid = pdb.create_goal(
            _cid(), title,
            description=d.get("description", ""),
            quarter=int(d.get("quarter", 1)),
            year=int(d.get("year", datetime.now().year)),
            key_results=d.get("key_results", []),
        )
        return jsonify({"ok": True, "id": gid})

    @app.route("/api/pro/goals/kr/<int:kr_id>", methods=["PUT"])
    def pro_goal_update_kr(kr_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        pdb.update_key_result(kr_id, _cid(), float(d.get("current") or 0))
        return jsonify({"ok": True})

    @app.route("/api/pro/goals/<int:goal_id>", methods=["DELETE"])
    def pro_goal_delete(goal_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_goal(goal_id, _cid())
        return jsonify({"ok": True})

    # ─────────────────────────────────────────────────────────
    # AI ASSISTANT (simple text tools)
    # ─────────────────────────────────────────────────────────
    def _ai_tool_page(title, icon, description, form_html, endpoint, active="pro_assistant"):
        return _p_render(title, f"""
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
          <a href="/pro" style="color:var(--text-muted);text-decoration:none;font-size:13px;">&larr; Pro Toolkit</a>
        </div>
        <h1 style="margin-bottom:6px;">{icon} {title}</h1>
        <p style="color:var(--text-muted);margin-bottom:16px;">{description}</p>
        <div class="card">
          {form_html}
          <button onclick="runAI()" class="btn btn-primary btn-sm" id="run-btn" style="margin-top:12px;">&#10024; Generate</button>
        </div>
        <div id="output-card" class="card" style="display:none;margin-top:16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
            <h3 style="margin:0;">Result</h3>
            <button onclick="copyOutput()" class="btn btn-outline btn-sm">&#128203; Copy</button>
          </div>
          <pre id="ai-output" style="white-space:pre-wrap;word-wrap:break-word;font-family:inherit;font-size:14px;line-height:1.6;margin:0;color:var(--text);"></pre>
        </div>
        <style>.edit-input,textarea.edit-input{{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;font-family:inherit;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function runAI(){{
          var btn=document.getElementById('run-btn');
          btn.disabled=true; btn.innerHTML='&#9203; Thinking...';
          var payload={{}};
          document.querySelectorAll('[data-field]').forEach(function(el){{ payload[el.dataset.field]=el.value; }});
          try{{
            var r=await fetch('{endpoint}',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify(payload)}});
            var d=await r.json();
            if(r.ok && d.result){{
              document.getElementById('ai-output').textContent=d.result;
              document.getElementById('output-card').style.display='block';
              document.getElementById('output-card').scrollIntoView({{behavior:'smooth'}});
            }} else {{ alert(d.error||'Failed'); }}
          }}catch(e){{ alert('Network error'); }}
          btn.disabled=false; btn.innerHTML='&#10024; Generate';
        }}
        function copyOutput(){{
          var t=document.getElementById('ai-output').textContent;
          navigator.clipboard.writeText(t); alert('Copied!');
        }}
        </script>
        """, active_page=active)

    @app.route("/pro/assistant")
    def pro_assistant_page():
        if not _logged_in():
            return redirect(url_for("login"))
        form = """
          <div class="form-group"><label>Text to polish</label>
            <textarea data-field="text" rows="8" class="edit-input" placeholder="Paste the email, post, or paragraph you want polished..."></textarea>
          </div>
          <div class="form-group"><label>Tone</label>
            <select data-field="tone" class="edit-input">
              <option value="professional" selected>Professional</option>
              <option value="friendly">Friendly</option>
              <option value="concise">Concise</option>
              <option value="persuasive">Persuasive</option>
              <option value="formal">Formal</option>
            </select>
          </div>"""
        return _ai_tool_page("Text Polish", "&#9997;", "Rewrite any text with better grammar, clarity, and tone.",
                             form, "/api/pro/ai/polish", active="pro_assistant")

    @app.route("/pro/meeting-agenda")
    def pro_agenda_page():
        if not _logged_in():
            return redirect(url_for("login"))
        form = """
          <div class="form-group"><label>Meeting topic</label><input data-field="topic" class="edit-input" placeholder="Q2 marketing kickoff"></div>
          <div class="form-group"><label>Duration (minutes)</label><input data-field="duration_min" type="number" class="edit-input" value="30"></div>
          <div class="form-group"><label>Context / goals</label><textarea data-field="context" rows="3" class="edit-input" placeholder="What do you want to come out of the meeting?"></textarea></div>"""
        return _ai_tool_page("Meeting Agenda", "&#128197;", "Get a tight, timed agenda with participants, pre-reads, and decisions to make.",
                             form, "/api/pro/ai/agenda")

    @app.route("/pro/cold-call")
    def pro_coldcall_page():
        if not _logged_in():
            return redirect(url_for("login"))
        form = """
          <div class="form-group"><label>What are you offering?</label><textarea data-field="offer" rows="3" class="edit-input" placeholder="B2B SaaS that automates cold email campaigns..."></textarea></div>
          <div class="form-group"><label>Target persona</label><input data-field="target" class="edit-input" placeholder="Head of Sales at 50-200 employee SaaS companies"></div>"""
        return _ai_tool_page("Cold Call Script", "&#128222;", "Get an opener, value prop, discovery question, CTA, plus the 3 most likely objections.",
                             form, "/api/pro/ai/coldcall")

    @app.route("/pro/linkedin-post")
    def pro_linkedin_page():
        if not _logged_in():
            return redirect(url_for("login"))
        form = """
          <div class="form-group"><label>Topic</label><input data-field="topic" class="edit-input" placeholder="The #1 mistake founders make when hiring..."></div>
          <div class="form-group"><label>Key points (optional)</label><textarea data-field="key_points" rows="3" class="edit-input"></textarea></div>
          <div class="form-group"><label>Audience</label><input data-field="audience" class="edit-input" value="founders and operators"></div>"""
        return _ai_tool_page("LinkedIn Post", "&#128100;", "High-performing LinkedIn post with a hook, structure, and 3 relevant hashtags.",
                             form, "/api/pro/ai/linkedin")

    @app.route("/pro/proposal")
    def pro_proposal_page():
        if not _logged_in():
            return redirect(url_for("login"))
        form = """
          <div class="form-group"><label>Project</label><textarea data-field="project" rows="2" class="edit-input" placeholder="Redesign the checkout flow for an e-commerce client"></textarea></div>
          <div class="form-group"><label>Deliverables</label><textarea data-field="deliverables" rows="3" class="edit-input"></textarea></div>
          <div class="form-group"><label>Budget (optional)</label><input data-field="budget" class="edit-input" placeholder="$12,000 - $18,000"></div>"""
        return _ai_tool_page("Proposal Outline", "&#128221;", "Full proposal skeleton: summary, scope, deliverables, timeline, investment, terms.",
                             form, "/api/pro/ai/proposal")

    # AI JSON endpoints (rate limited)
    @app.route("/api/pro/ai/polish", methods=["POST"])
    @limiter.limit("20 per minute")
    def pro_ai_polish():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        text = (d.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Paste some text first"}), 400
        return jsonify({"result": pai.polish_text(text, tone=d.get("tone", "professional"))})

    @app.route("/api/pro/ai/agenda", methods=["POST"])
    @limiter.limit("15 per minute")
    def pro_ai_agenda():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        topic = (d.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "Topic required"}), 400
        try:
            dur = int(d.get("duration_min") or 30)
        except (TypeError, ValueError):
            dur = 30
        return jsonify({"result": pai.meeting_agenda(topic, duration_min=dur, context=d.get("context", ""))})

    @app.route("/api/pro/ai/coldcall", methods=["POST"])
    @limiter.limit("10 per minute")
    def pro_ai_coldcall():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        offer = (d.get("offer") or "").strip()
        if not offer:
            return jsonify({"error": "What are you offering?"}), 400
        return jsonify({"result": pai.cold_call_script(offer, target=d.get("target", "decision-maker"))})

    @app.route("/api/pro/ai/linkedin", methods=["POST"])
    @limiter.limit("15 per minute")
    def pro_ai_linkedin():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        topic = (d.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "Topic required"}), 400
        return jsonify({"result": pai.linkedin_post(topic, key_points=d.get("key_points", ""), audience=d.get("audience", "professionals"))})

    @app.route("/api/pro/ai/proposal", methods=["POST"])
    @limiter.limit("10 per minute")
    def pro_ai_proposal():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        project = (d.get("project") or "").strip()
        if not project:
            return jsonify({"error": "Project description required"}), 400
        return jsonify({"result": pai.proposal_outline(project, deliverables=d.get("deliverables", ""), budget=d.get("budget", ""))})

    log.info("Professional routes registered.")
