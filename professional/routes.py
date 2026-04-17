"""
MachReach Pro routes — productivity suite for business accounts:
Tasks, Finance (banking + AI budget), Goals/OKRs, Meeting Agenda,
AI Assistant (polish, LinkedIn), Relationship Intelligence.

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


# Comprehensive currency list (ISO 4217 codes) — grouped by region for UX
CURRENCY_OPTIONS = [
    ("Common", ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]),
    ("Latin America", ["MXN", "BRL", "ARS", "CLP", "COP", "PEN", "UYU", "VES", "BOB", "PYG", "CRC", "DOP", "GTQ", "HNL", "NIO", "PAB"]),
    ("Europe", ["NOK", "SEK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN", "ISK", "HRK", "RSD", "TRY", "UAH", "RUB"]),
    ("Asia-Pacific", ["CNY", "HKD", "TWD", "KRW", "SGD", "THB", "IDR", "MYR", "PHP", "VND", "INR", "PKR", "BDT", "LKR", "NPR"]),
    ("Middle East & Africa", ["AED", "SAR", "QAR", "KWD", "BHD", "OMR", "JOD", "ILS", "EGP", "ZAR", "NGN", "KES", "MAD", "TND", "GHS"]),
    ("Crypto", ["BTC", "ETH", "USDT", "USDC"]),
]


def _currency_options(default: str = "USD") -> str:
    out = []
    for group_label, codes in CURRENCY_OPTIONS:
        out.append(f'<optgroup label="{group_label}">')
        for c in codes:
            sel = ' selected' if c == default else ''
            out.append(f'<option value="{c}"{sel}>{c}</option>')
        out.append('</optgroup>')
    return "".join(out)


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

    def _today_tasks_banner() -> str:
        """Returns HTML banner showing today's + overdue tasks (empty if none)."""
        if not _logged_in():
            return ""
        try:
            due = pdb.tasks_due_today(_cid())
            overdue = pdb.tasks_overdue(_cid())
        except Exception:
            return ""
        if not due and not overdue:
            return ""
        parts = []
        for t in due:
            parts.append(f"<li><b>Today:</b> {_esc(t['title'])}</li>")
        for t in overdue[:3]:
            parts.append(f"<li style='color:#EF4444;'><b>Overdue ({_esc(t.get('due_date',''))}):</b> {_esc(t['title'])}</li>")
        return f"""
        <div class="card" style="background:linear-gradient(135deg,rgba(239,68,68,.10),rgba(245,158,11,.10));
             border-left:4px solid #F59E0B;margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap;">
            <div>
              <div style="font-weight:700;font-size:14px;margin-bottom:6px;">&#9888;&#65039; You have {len(due)} task(s) due today{' + ' + str(len(overdue)) + ' overdue' if overdue else ''}</div>
              <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.7;">{''.join(parts)}</ul>
            </div>
            <a href="/pro/tasks" class="btn btn-primary btn-sm">Open Tasks</a>
          </div>
        </div>"""

    # ─────────────────────────────────────────────────────────
    # HUB
    # ─────────────────────────────────────────────────────────
    @app.route("/pro")
    def pro_hub():
        if not _logged_in():
            return redirect(url_for("login"))
        banner = _today_tasks_banner()
        return _p_render("Pro Toolkit", f"""
        {banner}
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;flex-wrap:wrap;gap:12px;">
          <div>
            <h1 style="margin:0;font-size:28px;">&#128188; <span class="gradient-text">Pro Toolkit</span></h1>
            <p style="color:var(--text-muted);margin:4px 0 0;font-size:14px;">Your second brain for work &mdash; tasks, money, meetings, and relationships.</p>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;">
          <a href="/pro/tasks" class="fx-tile"><span class="fx-ico">&#9989;</span><b>Tasks</b><span>Urgent tasks auto-sent to your inbox with reminders.</span></a>
          <a href="/pro/finance" class="fx-tile"><span class="fx-ico">&#128176;</span><b>Finance</b><span>Connect bank accounts, track spending, AI budget.</span></a>
          <a href="/pro/goals" class="fx-tile"><span class="fx-ico">&#127919;</span><b>Goals & OKRs</b><span>Quarterly objectives with measurable key results.</span></a>
          <a href="/pro/relationships" class="fx-tile"><span class="fx-ico">&#129504;</span><b>Relationship Intelligence</b><span>Your AI memory for every contact and conversation.</span></a>
          <a href="/pro/meeting-agenda" class="fx-tile"><span class="fx-ico">&#128197;</span><b>Meeting Agenda</b><span>AI scans your inbox for meetings + preps you.</span></a>
          <a href="/pro/invoices" class="fx-tile"><span class="fx-ico">&#128196;</span><b>Invoices</b><span>Create, send, and track professional invoices.</span></a>
          <a href="/pro/assistant" class="fx-tile"><span class="fx-ico">&#9997;</span><b>Text Polish</b><span>Rewrite any text with the right tone.</span></a>
          <a href="/pro/linkedin-post" class="fx-tile"><span class="fx-ico">&#128100;</span><b>LinkedIn Post</b><span>High-performing posts in your voice.</span></a>
        </div>
        <style>
          .fx-tile {{ display:flex;flex-direction:column;gap:4px;padding:16px;border:1px solid var(--border);border-radius:14px;
            background:var(--card);text-decoration:none;color:var(--text);transition:all .18s ease; }}
          .fx-tile:hover {{ border-color:var(--primary);transform:translateY(-2px);box-shadow:0 8px 20px rgba(99,102,241,.15); }}
          .fx-tile .fx-ico {{ font-size:22px; }}
          .fx-tile b {{ font-size:14px;font-weight:700; }}
          .fx-tile span:last-child {{ font-size:12px;color:var(--text-muted);line-height:1.45; }}
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
        banner = _today_tasks_banner()
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
        {banner}
        <h1 style="margin-bottom:8px;">&#9989; Tasks</h1>
        <p style="color:var(--text-muted);font-size:13px;margin-bottom:20px;">Every task you create is emailed to you and pinned as urgent in your Mail Hub so it shows up alongside your most important inbox items.</p>
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
          <div style="margin-top:8px;font-size:11px;color:var(--text-muted);">
            <label style="cursor:pointer;"><input type="checkbox" id="nt-email" checked> Email me this task & add it to Mail Hub as urgent</label>
          </div>
        </div>
        <div id="task-list">{tasks_html}</div>
        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function addTask(){{
          var title=document.getElementById('nt-title').value.trim();
          if(!title){{alert('Title required');return;}}
          var r=await fetch('/api/pro/tasks',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{title:title,priority:document.getElementById('nt-prio').value,due_date:document.getElementById('nt-due').value,project_tag:document.getElementById('nt-proj').value,email_me:document.getElementById('nt-email').checked}})}});
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
        # Side effects: email the user + insert into mail_inbox as urgent
        if d.get("email_me", True):
            try:
                _send_task_email_and_inbox(_cid(), title, d)
            except Exception as e:
                log.warning("Task email/inbox insert failed: %s", e)
        return jsonify({"ok": True, "id": tid})

    def _send_task_email_and_inbox(client_id: int, title: str, d: dict):
        """Send task as email to user and insert as urgent in mail_inbox."""
        from outreach.db import get_client, get_db, _exec, _fetchone
        c = get_client(client_id)
        if not c:
            return
        email = c.get("email") or ""
        if not email:
            return
        due = d.get("due_date") or "No due date"
        prio = d.get("priority", "medium").upper()
        body = (f"You just created a task in MachReach Pro:\n\n"
                f"Title: {title}\n"
                f"Priority: {prio}\n"
                f"Due: {due}\n"
                f"Project: {d.get('project_tag') or '-'}\n"
                f"Notes: {d.get('description') or '-'}\n\n"
                f"This task is pinned as URGENT in your Mail Hub. Open MachReach to mark it complete.\n\n"
                f"— MachReach Pro")
        subject = f"[Task] {title}"
        try:
            from app import _send_system_email
            _send_system_email(email, subject, body)
        except Exception as e:
            log.warning("Task email send failed: %s", e)

        # Insert into mail_inbox as urgent so it appears in Mail Hub
        try:
            import secrets
            msg_id = f"pro-task-{secrets.token_hex(8)}@machreach"
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            preview = (f"Task created in MachReach Pro. Priority {prio}. "
                       f"Due {due}. {d.get('description') or ''}")[:300]
            with get_db() as db:
                existing = _fetchone(db, "SELECT id FROM mail_inbox WHERE client_id = %s AND message_id = %s",
                                     (client_id, msg_id))
                if not existing:
                    _exec(db,
                        "INSERT INTO mail_inbox (client_id, message_id, from_name, from_email, to_email, "
                        "subject, body_preview, received_at, priority, category, ai_summary) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (client_id, msg_id, "MachReach Pro", "tasks@machreach.com", email,
                         subject, preview, now, "urgent", "task",
                         f"Task due {due} - priority {prio}"))
        except Exception as e:
            log.warning("Mail hub task insert failed: %s", e)

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
    # FINANCE (banking + transactions + AI budget)
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/finance")
    def pro_finance_page():
        if not _logged_in():
            return redirect(url_for("login"))
        # Auto-credit recurring monthly income on every visit (idempotent)
        try:
            pdb.apply_recurring_income(_cid())
        except Exception:
            log.exception("apply_recurring_income failed")
        banks = pdb.list_bank_connections(_cid())
        txs = pdb.list_transactions(_cid(), days=60)
        summary = pdb.spending_summary(_cid(), days=30)
        budget = pdb.get_budget(_cid())
        banner = _today_tasks_banner()

        total_bal = sum(float(b.get("balance") or 0) for b in banks)

        # Bank connections
        banks_html = ""
        for b in banks:
            mi = float(b.get("monthly_income") or 0)
            iday = int(b.get("income_day") or 1)
            last_inc = b.get("last_income_date") or ""
            last_inc_html = f'<div style="font-size:11px;color:var(--text-muted);">Last credited: {_esc(last_inc)}</div>' if last_inc else ""
            banks_html += f"""
            <div class="card" style="padding:14px 16px;background:linear-gradient(135deg,rgba(99,102,241,.08),rgba(139,92,246,.08));border-left:3px solid var(--primary);margin-bottom:8px;">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
                <div>
                  <div style="font-weight:700;">{_esc(b.get('institution_name',''))} <span style="color:var(--text-muted);font-weight:400;font-size:12px;">&middot; {_esc(b.get('account_type',''))}{' &middot; ****' + _esc(b.get('last_4','')) if b.get('last_4') else ''}</span></div>
                  {"<div style='font-size:12px;color:var(--text-muted);'>" + _esc(b.get('account_name','')) + "</div>" if b.get('account_name') else ""}
                  {last_inc_html}
                </div>
                <div style="text-align:right;">
                  <div style="font-family:monospace;font-size:18px;font-weight:700;">{_esc(b.get('currency','USD'))} <input type="number" step="0.01" id="bal-{b['id']}" value="{float(b.get('balance') or 0):.2f}" style="width:120px;background:transparent;border:none;color:var(--text);font-family:monospace;font-size:18px;font-weight:700;text-align:right;" onchange="saveBalance({b['id']})"></div>
                  <button onclick="delBank({b['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:11px;">Remove</button>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:6px;margin-top:10px;padding-top:10px;border-top:1px dashed var(--border);align-items:end;">
                <div>
                  <label style="font-size:11px;color:var(--text-muted);">Monthly income for this card</label>
                  <input type="number" step="0.01" id="inc-{b['id']}" value="{mi:.2f}" placeholder="0.00" class="edit-input" style="font-family:monospace;">
                </div>
                <div>
                  <label style="font-size:11px;color:var(--text-muted);">Day of month to credit</label>
                  <input type="number" min="1" max="28" id="incday-{b['id']}" value="{iday}" class="edit-input">
                </div>
                <button onclick="saveIncome({b['id']})" class="btn btn-primary btn-sm">Save</button>
              </div>
              <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">Set this once and your card balance auto-updates each month. Then just import statements to log spending.</div>
            </div>"""
        if not banks_html:
            banks_html = """<div style="padding:24px;text-align:center;color:var(--text-muted);border:2px dashed var(--border);border-radius:10px;">
              <div style="font-size:32px;margin-bottom:6px;">&#127974;</div>
              <div style="font-weight:700;color:var(--text);">Connect your first account</div>
              <div style="font-size:12px;margin-top:4px;">Add bank accounts & cards to see total balance + spending in one place.</div>
            </div>"""

        # Transactions table
        rows = ""
        for t in txs[:50]:
            cat = (t.get("category") or "other").replace("_", " ").title()
            amt = float(t.get("amount") or 0)
            color = "#10B981" if t.get("category") == "income" else "var(--text)"
            sign = "+" if t.get("category") == "income" else "-"
            rows += f"""<tr>
              <td>{_esc(str(t.get('tx_date','') or '')[:10])}</td>
              <td style="font-weight:500;">{_esc(t.get('merchant') or '-')}</td>
              <td><span style="background:var(--bg);padding:2px 8px;border-radius:10px;font-size:11px;">{_esc(cat)}</span></td>
              <td style="font-size:12px;color:var(--text-muted);">{_esc(t.get('institution_name','') or 'Manual')}{' &middot; ****' + _esc(t.get('last_4','')) if t.get('last_4') else ''}</td>
              <td style="font-family:monospace;text-align:right;color:{color};font-weight:600;">{sign} {_esc(t.get('currency','USD'))} {amt:,.2f}</td>
              <td><button onclick="delTx({t['id']})" class="btn btn-ghost btn-sm" style="color:var(--red);font-size:11px;">&#10005;</button></td>
            </tr>"""
        if not rows:
            rows = "<tr><td colspan='6' style='text-align:center;padding:24px;color:var(--text-muted);'>No transactions yet. Import a bank statement (PDF/CSV/OFX) or add one manually.</td></tr>"

        # Spending by category
        cat_html = ""
        total_spent = summary["total_spent"] or 1
        for cat in summary.get("by_category", []):
            pct = (float(cat["total"]) / total_spent) * 100
            cat_html += f"""
            <div style="margin-bottom:8px;">
              <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px;">
                <span>{_esc(cat['category'].replace('_',' ').title())}</span>
                <span style="font-family:monospace;">${float(cat['total']):,.2f} <span style="color:var(--text-muted);">({pct:.0f}%)</span></span>
              </div>
              <div style="background:var(--bg);height:6px;border-radius:4px;overflow:hidden;">
                <div style="background:linear-gradient(90deg,var(--primary),#8B5CF6);height:100%;width:{pct:.1f}%;"></div>
              </div>
            </div>"""

        merchants_html = ""
        for m in summary.get("top_merchants", []):
            merchants_html += f"<div style='display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);font-size:12px;'><span>{_esc(m['merchant'])} <span style='color:var(--text-muted);'>({m['count']}x)</span></span><span style='font-family:monospace;'>${float(m['total']):,.2f}</span></div>"

        # Budget card
        if budget:
            budget_html = f"""
              <div class="form-group"><label>Monthly income</label><input id="bd-income" type="number" class="edit-input" value="{float(budget.get('income') or 0):.2f}" step="0.01"></div>
              <div class="form-group"><label>Savings goal / month</label><input id="bd-savings" type="number" class="edit-input" value="{float(budget.get('savings_goal') or 0):.2f}" step="0.01"></div>
              <div class="form-group"><label>Currency</label>
                <select id="bd-currency" class="edit-input">{_currency_options(default=budget.get('currency','USD'))}</select>
              </div>
              <div class="form-group"><label>Preferences (tell the AI about your lifestyle)</label>
                <textarea id="bd-prefs" rows="4" class="edit-input" placeholder="I'm a freelancer, variable income. Want to travel once a quarter. Prefer to save 25%...">{_esc(budget.get('preferences',''))}</textarea>
              </div>"""
            ai_plan = budget.get("ai_plan") or ""
            plan_html = (f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:13px;line-height:1.6;margin:0;'>{_esc(ai_plan)}</pre>"
                         if ai_plan else "<div style='color:var(--text-muted);font-size:13px;'>Click &ldquo;Generate AI Plan&rdquo; to build your personalized budget from your spending.</div>")
        else:
            budget_html = """
              <div class="form-group"><label>Monthly income</label><input id="bd-income" type="number" class="edit-input" value="0" step="0.01"></div>
              <div class="form-group"><label>Savings goal / month</label><input id="bd-savings" type="number" class="edit-input" value="0" step="0.01"></div>
              <div class="form-group"><label>Currency</label>
                <select id="bd-currency" class="edit-input">{_currency_options(default='USD')}</select>
              </div>
              <div class="form-group"><label>Preferences (tell the AI about your lifestyle)</label>
                <textarea id="bd-prefs" rows="4" class="edit-input" placeholder="I'm a freelancer, variable income. Want to travel once a quarter..."></textarea>
              </div>"""
            plan_html = "<div style='color:var(--text-muted);font-size:13px;'>Set your income and preferences, then click &ldquo;Generate AI Plan&rdquo;.</div>"

        cat_opts = "".join(f'<option value="{c}">{c.replace("_"," ").title()}</option>' for c in pdb.TRANSACTION_CATEGORIES)
        today = datetime.now().strftime("%Y-%m-%d")

        return _p_render("Finance", f"""
        {banner}
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:10px;">
          <h1 style="margin:0;">&#128176; Finance</h1>
          <div style="text-align:right;">
            <div style="font-size:12px;color:var(--text-muted);">Total balance</div>
            <div style="font-family:monospace;font-size:22px;font-weight:700;color:var(--primary);">${total_bal:,.2f}</div>
          </div>
        </div>
        <p style="color:var(--text-muted);font-size:13px;margin-bottom:20px;">Connect your accounts to see every transaction, spot overspending, and let AI build a budget for you.</p>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
          <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
              <h3 style="margin:0;font-size:15px;">&#127974; Accounts & Cards</h3>
              <button onclick="document.getElementById('bank-form').style.display='block'" class="btn btn-primary btn-sm">+ Add</button>
            </div>
            <div id="bank-form" style="display:none;margin-bottom:12px;padding:12px;border:1px dashed var(--border);border-radius:8px;">
              <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">Add a card or account manually. Use the bank-statement importer below to bring in transactions — your data stays on our servers, no third-party login.</div>
              <div style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:6px;margin-bottom:6px;">
                <input id="bk-name" placeholder="Institution (Chase, Wells Fargo...)" class="edit-input">
                <select id="bk-type" class="edit-input"><option value="checking">Checking</option><option value="savings">Savings</option><option value="credit_card">Credit Card</option><option value="investment">Investment</option></select>
                <input id="bk-last4" placeholder="Last 4" maxlength="4" class="edit-input">
              </div>
              <div style="display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:6px;">
                <input id="bk-account" placeholder="Nickname (e.g. Main Checking)" class="edit-input">
                <input id="bk-balance" type="number" step="0.01" placeholder="Balance" class="edit-input" value="0">
                <select id="bk-currency" class="edit-input">{_currency_options(default='USD')}</select>
                <button onclick="addBank()" class="btn btn-primary btn-sm">Save</button>
              </div>
              <div style="margin-top:8px;font-size:11px;">
                <label style="cursor:pointer;"><input type="checkbox" id="bk-seed" checked> Populate with 30 days of realistic demo transactions</label>
              </div>
            </div>
            {banks_html}
          </div>

          <div class="card">
            <h3 style="margin:0 0 10px;font-size:15px;">&#128202; Last 30 Days</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px;">
              <div><div style="font-size:11px;color:var(--text-muted);">Spent</div><div style="font-family:monospace;font-size:18px;font-weight:700;color:#EF4444;">${summary['total_spent']:,.2f}</div></div>
              <div><div style="font-size:11px;color:var(--text-muted);">Income</div><div style="font-family:monospace;font-size:18px;font-weight:700;color:#10B981;">${summary['total_income']:,.2f}</div></div>
              <div><div style="font-size:11px;color:var(--text-muted);">Net</div><div style="font-family:monospace;font-size:18px;font-weight:700;color:{'#10B981' if summary['net']>=0 else '#EF4444'};">${summary['net']:,.2f}</div></div>
            </div>
            <div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:6px;">By category</div>
            {cat_html or "<div style='color:var(--text-muted);font-size:12px;'>No spending data yet.</div>"}
            {f'<div style="font-size:12px;font-weight:600;color:var(--text-muted);margin:14px 0 6px;">Top merchants</div>{merchants_html}' if merchants_html else ''}
          </div>
        </div>

        <div class="card" style="margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
            <h3 style="margin:0;font-size:15px;">&#128181; Transactions</h3>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <button onclick="document.getElementById('import-form').style.display=document.getElementById('import-form').style.display==='none'?'block':'none'" class="btn btn-primary btn-sm">&#128228; Import statement</button>
              <button onclick="document.getElementById('tx-form').style.display=document.getElementById('tx-form').style.display==='none'?'flex':'none'" class="btn btn-outline btn-sm">+ Add manually</button>
            </div>
          </div>
          <div id="import-form" style="display:none;margin-bottom:12px;padding:12px;border:1px dashed var(--primary);border-radius:8px;background:linear-gradient(135deg,rgba(99,102,241,.05),rgba(139,92,246,.05));">
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">
              Upload a statement export from ANY bank. We support <b>PDF</b>, <b>CSV</b> (every bank) and <b>OFX/QFX</b> (most US/UK/EU/LatAm banks).
              Download your statement from your online banking, then drop the file here &mdash; no bank login required.
            </div>
            <div style="display:grid;grid-template-columns:2fr 1fr auto;gap:8px;align-items:end;">
              <div><label style="font-size:11px;">File (.pdf, .csv, .ofx, .qfx)</label><input type="file" id="imp-file" accept=".pdf,.csv,.ofx,.qfx,.txt" class="edit-input"></div>
              <div><label style="font-size:11px;">Link to account (optional)</label>
                <select id="imp-bank" class="edit-input">
                  <option value="">(no account)</option>
                  {"".join(f'<option value="{b["id"]}">{_esc(b.get("institution_name",""))}{" · ****" + _esc(b.get("last_4","")) if b.get("last_4") else ""}</option>' for b in banks)}
                </select>
              </div>
              <button onclick="importStatement()" class="btn btn-primary btn-sm" id="imp-btn">Import</button>
            </div>
            <div id="imp-status" style="margin-top:8px;font-size:12px;"></div>
          </div>
          <div id="tx-form" style="display:none;gap:6px;margin-bottom:12px;padding:10px;border:1px dashed var(--border);border-radius:8px;flex-wrap:wrap;align-items:end;">
            <div style="flex:0 0 130px;"><label style="font-size:11px;">Date</label><input id="tx-date" type="date" class="edit-input" value="{today}"></div>
            <div style="flex:1 1 160px;"><label style="font-size:11px;">Merchant</label><input id="tx-merchant" class="edit-input" placeholder="Starbucks"></div>
            <div style="flex:0 0 150px;"><label style="font-size:11px;">Category</label><select id="tx-cat" class="edit-input">{cat_opts}</select></div>
            <div style="flex:0 0 110px;"><label style="font-size:11px;">Amount</label><input id="tx-amount" type="number" step="0.01" class="edit-input"></div>
            <div style="flex:0 0 100px;"><label style="font-size:11px;">Currency</label><select id="tx-currency" class="edit-input"><option value="USD">USD</option><option value="EUR">EUR</option><option value="GBP">GBP</option><option value="MXN">MXN</option><option value="CLP">CLP</option><option value="COP">COP</option><option value="ARS">ARS</option><option value="BRL">BRL</option><option value="PEN">PEN</option><option value="UYU">UYU</option><option value="CAD">CAD</option><option value="AUD">AUD</option><option value="JPY">JPY</option><option value="CHF">CHF</option><option value="INR">INR</option><option value="CNY">CNY</option></select></div>
            <button onclick="addTx()" class="btn btn-primary btn-sm">Save</button>
            <button onclick="document.getElementById('tx-form').style.display='none'" class="btn btn-ghost btn-sm">Cancel</button>
          </div>
          <div style="overflow:auto;">
            <table><thead><tr><th>Date</th><th>Merchant</th><th>Category</th><th>Account</th><th style="text-align:right;">Amount</th><th></th></tr></thead><tbody>{rows}</tbody></table>
          </div>
        </div>

        <div class="card">
          <h3 style="margin:0 0 10px;font-size:15px;">&#129504; AI Budget Plan</h3>
          <p style="color:var(--text-muted);font-size:13px;margin-bottom:14px;">We combine your real spending with your goals to build a personalized plan &mdash; with specific cuts to make and a savings schedule.</p>
          <div style="display:grid;grid-template-columns:1fr 1.5fr;gap:20px;">
            <div>{budget_html}
              <button onclick="genBudget()" class="btn btn-primary btn-sm" id="gen-budget-btn" style="margin-top:8px;">&#10024; Generate AI Plan</button>
            </div>
            <div id="plan-box" style="padding:14px;background:var(--bg);border-radius:10px;max-height:480px;overflow:auto;">
              {plan_html}
            </div>
          </div>
        </div>

        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        async function addBank(){{
          var r=await fetch('/api/pro/banks',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{
            institution_name:document.getElementById('bk-name').value,
            account_name:document.getElementById('bk-account').value,
            account_type:document.getElementById('bk-type').value,
            last_4:document.getElementById('bk-last4').value,
            balance:parseFloat(document.getElementById('bk-balance').value)||0,
            currency:document.getElementById('bk-currency').value,
            seed_demo:document.getElementById('bk-seed').checked
          }})}});
          if(r.ok) location.reload(); else alert('Failed');
        }}
        async function delBank(id){{
          if(!confirm('Remove this account? Transactions linked to it will stay but lose the account link.')) return;
          await fetch('/api/pro/banks/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        async function saveBalance(id){{
          var v=parseFloat(document.getElementById('bal-'+id).value||'0');
          var r=await fetch('/api/pro/banks/'+id+'/balance',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{balance:v}})}});
          if(!r.ok) alert('Failed to save balance');
        }}
        async function saveIncome(id){{
          var amt=parseFloat(document.getElementById('inc-'+id).value||'0');
          var day=parseInt(document.getElementById('incday-'+id).value||'1');
          if(day<1) day=1; if(day>28) day=28;
          var r=await fetch('/api/pro/banks/'+id+'/income',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{monthly_income:amt,income_day:day}})}});
          if(r.ok){{ if(amt>0) alert('Saved! This card will auto-credit '+amt.toFixed(2)+' each month on day '+day+'.'); else alert('Auto-income disabled for this card.'); location.reload(); }}
          else alert('Failed to save');
        }}
        async function addTx(){{
          var amt=parseFloat(document.getElementById('tx-amount').value);
          if(!amt||amt<=0){{alert('Enter an amount');return;}}
          var ccyEl=document.getElementById('tx-currency');
          var ccy=ccyEl?ccyEl.value:'USD';
          var r=await fetch('/api/pro/transactions',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{amount:amt,merchant:document.getElementById('tx-merchant').value,category:document.getElementById('tx-cat').value,tx_date:document.getElementById('tx-date').value,currency:ccy}})}});
          if(r.ok) location.reload(); else {{ var d={{}}; try{{d=await r.json();}}catch(e){{}} alert(d.error||'Failed'); }}
        }}
        async function importStatement(){{
          var f=document.getElementById('imp-file').files[0];
          if(!f){{alert('Choose a file');return;}}
          var btn=document.getElementById('imp-btn');
          var status=document.getElementById('imp-status');
          btn.disabled=true; btn.textContent='Importing...';
          status.innerHTML='<span style="color:var(--text-muted);">Parsing '+f.name+'...</span>';
          var fd=new FormData();
          fd.append('file',f);
          var bankId=document.getElementById('imp-bank').value;
          if(bankId) fd.append('bank_connection_id',bankId);
          try{{
            var r=await fetch('/api/pro/transactions/import',{{method:'POST',headers:csrfHeader(),body:fd}});
            var d=await r.json();
            if(r.ok){{
              status.innerHTML='<span style="color:#10B981;">&#10003; Imported '+d.imported+' transactions. Skipped '+d.skipped+' duplicates.</span>';
              setTimeout(function(){{location.reload();}},1400);
            }} else {{
              status.innerHTML='<span style="color:#EF4444;">'+(d.error||'Import failed')+'</span>';
              btn.disabled=false; btn.textContent='Import';
            }}
          }} catch(e){{
            status.innerHTML='<span style="color:#EF4444;">Network error</span>';
            btn.disabled=false; btn.textContent='Import';
          }}
        }}
        async function delTx(id){{
          if(!confirm('Delete transaction?')) return;
          await fetch('/api/pro/transactions/'+id,{{method:'DELETE',headers:csrfHeader()}});
          location.reload();
        }}
        async function genBudget(){{
          var btn=document.getElementById('gen-budget-btn');
          btn.disabled=true; btn.innerHTML='&#9203; Analyzing...';
          var r=await fetch('/api/pro/budget/generate',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{
            income:parseFloat(document.getElementById('bd-income').value)||0,
            savings_goal:parseFloat(document.getElementById('bd-savings').value)||0,
            currency:document.getElementById('bd-currency').value,
            preferences:document.getElementById('bd-prefs').value
          }})}});
          var d=await r.json();
          if(r.ok){{ document.getElementById('plan-box').innerHTML='<pre style=\\'white-space:pre-wrap;font-family:inherit;font-size:13px;line-height:1.6;margin:0;\\'></pre>'; document.querySelector('#plan-box pre').textContent=d.plan; }}
          else alert(d.error||'Failed');
          btn.disabled=false; btn.innerHTML='&#10024; Generate AI Plan';
        }}
        </script>
        """, active_page="pro_finance")

    @app.route("/api/pro/banks", methods=["POST"])
    def pro_bank_create():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        name = (d.get("institution_name") or "").strip()
        if not name:
            return jsonify({"error": "institution name required"}), 400
        bid = pdb.create_bank_connection(
            _cid(), institution_name=name,
            account_name=d.get("account_name", ""),
            account_type=d.get("account_type", "checking"),
            last_4=(d.get("last_4") or "")[:4],
            balance=float(d.get("balance") or 0),
            currency=d.get("currency", "USD"),
        )
        if d.get("seed_demo"):
            try:
                pdb.seed_demo_transactions(_cid(), bid)
            except Exception as e:
                log.warning("seed_demo failed: %s", e)
        return jsonify({"ok": True, "id": bid})

    @app.route("/api/pro/banks/<int:bank_id>", methods=["DELETE"])
    def pro_bank_delete(bank_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_bank_connection(bank_id, _cid())
        return jsonify({"ok": True})

    @app.route("/api/pro/banks/<int:bank_id>/balance", methods=["POST"])
    def pro_bank_set_balance(bank_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        try:
            pdb.update_bank_balance(bank_id, _cid(), float(d.get("balance") or 0))
        except Exception as e:
            log.warning("update_bank_balance failed: %s", e)
            return jsonify({"error": "update failed"}), 500
        return jsonify({"ok": True})

    @app.route("/api/pro/banks/<int:bank_id>/income", methods=["POST"])
    def pro_bank_set_income(bank_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        try:
            pdb.update_bank_income_settings(
                bank_id, _cid(),
                float(d.get("monthly_income") or 0),
                int(d.get("income_day") or 1),
            )
        except Exception as e:
            log.warning("update_bank_income_settings failed: %s", e)
            return jsonify({"error": "update failed"}), 500
        return jsonify({"ok": True})

    @app.route("/api/pro/transactions", methods=["POST"])
    def pro_tx_create():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        try:
            amt = float(d.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid amount"}), 400
        if amt <= 0:
            return jsonify({"error": "amount > 0 required"}), 400
        cat = d.get("category") or "other"
        if not cat:
            try:
                cat = pai.categorize_transaction(d.get("merchant", ""), d.get("description", ""))
            except Exception:
                cat = "other"
        tid = pdb.create_transaction(
            _cid(), amt,
            merchant=d.get("merchant", ""),
            category=cat,
            tx_date=d.get("tx_date", ""),
            description=d.get("description", ""),
            currency=d.get("currency", "USD"),
            bank_connection_id=d.get("bank_connection_id"),
            is_manual=True,
        )
        return jsonify({"ok": True, "id": tid})

    @app.route("/api/pro/transactions/<int:tx_id>", methods=["DELETE"])
    def pro_tx_delete(tx_id):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        pdb.delete_transaction(tx_id, _cid())
        return jsonify({"ok": True})

    @app.route("/api/pro/transactions/import", methods=["POST"])
    @limiter.limit("10 per minute")
    def pro_tx_import():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        from professional import statement_import as si
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "No file uploaded"}), 400
        raw = f.read()
        if len(raw) > 10 * 1024 * 1024:
            return jsonify({"error": "File too large (max 10 MB)"}), 400
        try:
            txs = si.parse_statement(f.filename, raw)
        except Exception as e:
            log.exception("Statement parse failed")
            return jsonify({"error": f"Parse error: {e}"}), 400
        if not txs:
            return jsonify({"error": "No transactions found in file. Make sure it's a CSV or OFX/QFX export."}), 400

        bank_id = request.form.get("bank_connection_id")
        try:
            bank_id = int(bank_id) if bank_id else None
        except (TypeError, ValueError):
            bank_id = None

        # Default currency from linked bank or user's budget, else first tx's, else USD
        default_ccy = "USD"
        if bank_id:
            for b in pdb.list_bank_connections(_cid()):
                if b["id"] == bank_id:
                    default_ccy = b.get("currency") or "USD"
                    break

        # Fetch existing transactions (same date + amount + merchant) to dedupe
        from outreach.db import get_db, _fetchall
        with get_db() as db:
            existing = _fetchall(db,
                "SELECT tx_date, amount, merchant FROM pro_transactions WHERE client_id = %s",
                (_cid(),))
        seen = set()
        for e in existing:
            key = (str(e.get("tx_date") or "")[:10],
                   round(float(e.get("amount") or 0), 2),
                   (e.get("merchant") or "").strip().lower()[:80])
            seen.add(key)

        imported = 0
        skipped = 0
        for tx in txs:
            key = (tx["tx_date"], round(float(tx["amount"]), 2),
                   (tx["merchant"] or "").strip().lower()[:80])
            if key in seen:
                skipped += 1
                continue
            try:
                pdb.create_transaction(
                    _cid(), tx["amount"],
                    merchant=tx["merchant"],
                    category=tx["category"],
                    tx_date=tx["tx_date"],
                    description=tx.get("description", ""),
                    currency=(tx.get("currency") or default_ccy or "USD"),
                    bank_connection_id=bank_id,
                    is_manual=False,
                )
                seen.add(key)
                imported += 1
            except Exception as e:
                log.warning("import insert failed: %s", e)
        return jsonify({"ok": True, "imported": imported, "skipped": skipped, "total": len(txs)})

    @app.route("/api/pro/budget/generate", methods=["POST"])
    @limiter.limit("6 per minute")
    def pro_budget_generate():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        try:
            income = float(d.get("income") or 0)
            savings = float(d.get("savings_goal") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid income/savings"}), 400
        currency = d.get("currency", "USD")
        prefs = (d.get("preferences") or "").strip()
        summary = pdb.spending_summary(_cid(), days=30)
        plan = pai.generate_budget_plan(income, savings, currency, prefs, summary)
        pdb.upsert_budget(_cid(), income, savings, preferences=prefs, ai_plan=plan, currency=currency)
        return jsonify({"ok": True, "plan": plan})

    # ─────────────────────────────────────────────────────────
    # INVOICES (unchanged from v1, condensed reuse)
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
        {_today_tasks_banner()}
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
                <select id="inv-currency" class="edit-input">{_currency_options(default=(inv or {}).get('currency', 'USD'))}</select>
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
    # GOALS / OKRs (unchanged)
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
        {_today_tasks_banner()}
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
    # MEETING AGENDA — auto-detect from inbox
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/meeting-agenda")
    def pro_agenda_page():
        if not _logged_in():
            return redirect(url_for("login"))
        return _p_render("Meeting Agenda", f"""
        {_today_tasks_banner()}
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
          <a href="/pro" style="color:var(--text-muted);text-decoration:none;font-size:13px;">&larr; Pro Toolkit</a>
        </div>
        <h1 style="margin-bottom:6px;">&#128197; Meeting Agenda</h1>
        <p style="color:var(--text-muted);margin-bottom:16px;">We scan your recent emails for any scheduled meeting or call and prep you with a timed agenda.</p>

        <div class="card">
          <div style="display:grid;grid-template-columns:1fr 1fr auto;gap:10px;align-items:end;margin-bottom:12px;">
            <div class="form-group" style="margin:0;"><label>Topic (optional, for manual agenda)</label><input id="mt-topic" class="edit-input" placeholder="e.g. Q2 marketing kickoff"></div>
            <div class="form-group" style="margin:0;"><label>Duration (min)</label><input id="mt-dur" type="number" class="edit-input" value="30"></div>
            <button onclick="genManual()" class="btn btn-outline btn-sm" id="manual-btn">Generate Manual</button>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:12px;">
            <button onclick="scanInbox()" class="btn btn-primary btn-sm" id="scan-btn">&#128225; Scan Inbox for Upcoming Meetings</button>
            <span style="color:var(--text-muted);font-size:12px;margin-left:10px;">Looks at your last 30 emails and extracts any meeting.</span>
          </div>
        </div>

        <div id="output-card" class="card" style="display:none;margin-top:16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
            <h3 id="output-title" style="margin:0;">Result</h3>
            <button onclick="copyOutput()" class="btn btn-outline btn-sm">&#128203; Copy</button>
          </div>
          <pre id="ai-output" style="white-space:pre-wrap;word-wrap:break-word;font-family:inherit;font-size:14px;line-height:1.6;margin:0;color:var(--text);"></pre>
        </div>
        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        function showResult(title, text){{
          document.getElementById('output-title').textContent=title;
          document.getElementById('ai-output').textContent=text;
          document.getElementById('output-card').style.display='block';
          document.getElementById('output-card').scrollIntoView({{behavior:'smooth'}});
        }}
        async function scanInbox(){{
          var btn=document.getElementById('scan-btn');
          btn.disabled=true; btn.innerHTML='&#9203; Scanning inbox...';
          var r=await fetch('/api/pro/meetings/scan',{{method:'POST',headers:csrfHeader()}});
          var d=await r.json();
          if(r.ok) showResult('Meetings found in your inbox', d.result);
          else alert(d.error||'Failed');
          btn.disabled=false; btn.innerHTML='&#128225; Scan Inbox for Upcoming Meetings';
        }}
        async function genManual(){{
          var topic=document.getElementById('mt-topic').value.trim();
          if(!topic){{alert('Enter a topic');return;}}
          var btn=document.getElementById('manual-btn');
          btn.disabled=true;
          var r=await fetch('/api/pro/ai/agenda',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{topic:topic,duration_min:parseInt(document.getElementById('mt-dur').value)||30}})}});
          var d=await r.json();
          if(r.ok) showResult('Agenda', d.result);
          else alert(d.error||'Failed');
          btn.disabled=false;
        }}
        function copyOutput(){{
          navigator.clipboard.writeText(document.getElementById('ai-output').textContent);
          alert('Copied!');
        }}
        </script>
        """, active_page="pro_meetings")

    @app.route("/api/pro/meetings/scan", methods=["POST"])
    @limiter.limit("6 per minute")
    def pro_meetings_scan():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        from outreach.db import get_mail_inbox
        try:
            emails = get_mail_inbox(_cid(), filter_by="all", limit=30)
        except TypeError:
            emails = get_mail_inbox(_cid())[:30]
        result = pai.extract_meetings_from_emails(emails or [])
        return jsonify({"result": result})

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

    # ─────────────────────────────────────────────────────────
    # RELATIONSHIP INTELLIGENCE
    # ─────────────────────────────────────────────────────────
    @app.route("/pro/relationships")
    def pro_relationships_page():
        if not _logged_in():
            return redirect(url_for("login"))
        contacts = pdb.list_relationship_contacts(_cid(), limit=100)
        rows = ""
        for c in contacts:
            last = str(c.get("last_at","") or "")[:10] or "—"
            summary = (c.get("ai_summary") or "").strip()
            has_summary = bool(summary)
            icon = "&#9989;" if has_summary else "&#128373;"
            rows += f"""<tr onclick="openContact('{_esc(c['email'])}')" style="cursor:pointer;">
              <td>{icon}</td>
              <td style="font-weight:600;">{_esc(c.get('name','') or c['email'])}</td>
              <td style="font-size:12px;color:var(--text-muted);">{_esc(c['email'])}</td>
              <td style="font-size:12px;">{_esc(c.get('role',''))}{' @ ' + _esc(c.get('company','')) if c.get('company') else ''}</td>
              <td style="text-align:center;">{c.get('msg_count',0)}</td>
              <td style="font-size:12px;color:var(--text-muted);">{last}</td>
            </tr>"""
        if not rows:
            rows = "<tr><td colspan='6' style='text-align:center;padding:24px;color:var(--text-muted);'>No contacts in your inbox yet. Connect your email in Mail Hub.</td></tr>"

        return _p_render("Relationship Intelligence", f"""
        {_today_tasks_banner()}
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
          <a href="/pro" style="color:var(--text-muted);text-decoration:none;font-size:13px;">&larr; Pro Toolkit</a>
        </div>
        <h1 style="margin-bottom:6px;">&#129504; Relationship Intelligence</h1>
        <p style="color:var(--text-muted);margin-bottom:16px;">Your AI memory for every professional relationship. Open any contact to see a living summary &mdash; then ask AI who to reach out to for any goal.</p>

        <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
          <div class="card" style="overflow:auto;">
            <h3 style="margin:0 0 10px;font-size:15px;">People you've talked to</h3>
            <table><thead><tr><th></th><th>Name</th><th>Email</th><th>Role / Company</th><th style="text-align:center;">Msgs</th><th>Last</th></tr></thead><tbody>{rows}</tbody></table>
          </div>
          <div>
            <div class="card" style="margin-bottom:10px;">
              <h3 style="margin:0 0 10px;font-size:15px;">&#128226; Ask your network</h3>
              <p style="color:var(--text-muted);font-size:12px;margin-bottom:10px;">E.g. "Who should I talk to if I want to raise money?"</p>
              <textarea id="goal-prompt" class="edit-input" rows="3" placeholder="Who should I talk to if I need help with..."></textarea>
              <button onclick="askNetwork()" class="btn btn-primary btn-sm" id="ask-btn" style="margin-top:8px;width:100%;">&#128269; Ask AI</button>
              <pre id="network-out" style="display:none;margin-top:10px;padding:10px;background:var(--bg);border-radius:8px;white-space:pre-wrap;font-family:inherit;font-size:12px;line-height:1.5;"></pre>
            </div>
            <div class="card">
              <h3 style="margin:0 0 10px;font-size:15px;">&#128197; Reconnect this week</h3>
              <button onclick="reconnectSuggest()" class="btn btn-outline btn-sm" id="rec-btn" style="width:100%;">Suggest people to reach out to</button>
              <pre id="rec-out" style="display:none;margin-top:10px;padding:10px;background:var(--bg);border-radius:8px;white-space:pre-wrap;font-family:inherit;font-size:12px;line-height:1.5;"></pre>
            </div>
          </div>
        </div>

        <!-- Contact modal -->
        <div id="c-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;align-items:center;justify-content:center;padding:20px;">
          <div class="card" style="max-width:720px;width:100%;max-height:88vh;overflow:auto;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
              <h3 id="c-title" style="margin:0;"></h3>
              <button onclick="closeContact()" class="btn btn-ghost btn-sm">&#10005;</button>
            </div>
            <div id="c-meta" style="font-size:12px;color:var(--text-muted);margin-bottom:10px;"></div>
            <div style="display:flex;gap:8px;margin-bottom:14px;">
              <button onclick="rebuildSummary()" class="btn btn-primary btn-sm" id="rebuild-btn">&#10024; Build AI Summary</button>
            </div>
            <pre id="c-summary" style="white-space:pre-wrap;font-family:inherit;font-size:13px;line-height:1.6;background:var(--bg);padding:12px;border-radius:8px;min-height:60px;margin-bottom:14px;">(no summary yet — click &quot;Build AI Summary&quot;)</pre>

            <h4 style="margin:12px 0 6px;font-size:13px;">Your private notes</h4>
            <textarea id="c-notes" class="edit-input" rows="3"></textarea>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:6px;">
              <input id="c-name" class="edit-input" placeholder="Name">
              <input id="c-role" class="edit-input" placeholder="Role">
              <input id="c-company" class="edit-input" placeholder="Company">
            </div>
            <button onclick="saveContact()" class="btn btn-primary btn-sm" style="margin-top:8px;">&#128190; Save</button>
          </div>
        </div>

        <style>.edit-input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-size:13px;font-family:inherit;}}.edit-input:focus{{border-color:var(--primary);outline:none;}}</style>
        <script>
        function csrfHeader(){{var m=document.querySelector('meta[name="csrf-token"]');return m?{{'X-CSRFToken':m.content}}:{{}};}}
        var currentEmail=null;
        async function openContact(email){{
          currentEmail=email;
          var r=await fetch('/api/pro/relationships/'+encodeURIComponent(email));
          var d=await r.json();
          document.getElementById('c-title').textContent=d.name||email;
          document.getElementById('c-meta').textContent=email+' &middot; '+(d.msg_count||0)+' emails exchanged';
          document.getElementById('c-meta').innerHTML=document.getElementById('c-meta').textContent;
          document.getElementById('c-summary').textContent=d.ai_summary||'(no summary yet — click \\"Build AI Summary\\")';
          document.getElementById('c-notes').value=d.notes||'';
          document.getElementById('c-name').value=d.name||'';
          document.getElementById('c-role').value=d.role||'';
          document.getElementById('c-company').value=d.company||'';
          document.getElementById('c-modal').style.display='flex';
        }}
        function closeContact(){{ document.getElementById('c-modal').style.display='none'; }}
        async function rebuildSummary(){{
          if(!currentEmail) return;
          var btn=document.getElementById('rebuild-btn');
          btn.disabled=true; btn.innerHTML='&#9203; Analyzing emails...';
          var r=await fetch('/api/pro/relationships/'+encodeURIComponent(currentEmail)+'/summary',{{method:'POST',headers:csrfHeader()}});
          var d=await r.json();
          if(r.ok) document.getElementById('c-summary').textContent=d.summary;
          else alert(d.error||'Failed');
          btn.disabled=false; btn.innerHTML='&#10024; Rebuild AI Summary';
        }}
        async function saveContact(){{
          if(!currentEmail) return;
          await fetch('/api/pro/relationships/'+encodeURIComponent(currentEmail),{{method:'PUT',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{
            notes:document.getElementById('c-notes').value,
            contact_name:document.getElementById('c-name').value,
            contact_role:document.getElementById('c-role').value,
            company:document.getElementById('c-company').value
          }})}});
          alert('Saved');
          location.reload();
        }}
        async function askNetwork(){{
          var goal=document.getElementById('goal-prompt').value.trim();
          if(!goal){{alert('Enter a goal');return;}}
          var btn=document.getElementById('ask-btn');
          btn.disabled=true; btn.innerHTML='&#9203; Thinking...';
          var r=await fetch('/api/pro/relationships/suggest',{{method:'POST',headers:Object.assign({{'Content-Type':'application/json'}},csrfHeader()),body:JSON.stringify({{goal:goal}})}});
          var d=await r.json();
          if(r.ok){{
            document.getElementById('network-out').textContent=d.result;
            document.getElementById('network-out').style.display='block';
          }} else alert(d.error||'Failed');
          btn.disabled=false; btn.innerHTML='&#128269; Ask AI';
        }}
        async function reconnectSuggest(){{
          var btn=document.getElementById('rec-btn');
          btn.disabled=true; btn.innerHTML='&#9203; Thinking...';
          var r=await fetch('/api/pro/relationships/reconnect',{{method:'POST',headers:csrfHeader()}});
          var d=await r.json();
          if(r.ok){{
            document.getElementById('rec-out').textContent=d.result;
            document.getElementById('rec-out').style.display='block';
          }} else alert(d.error||'Failed');
          btn.disabled=false; btn.innerHTML='Suggest people to reach out to';
        }}
        </script>
        """, active_page="pro_relationships")

    @app.route("/api/pro/relationships/<path:email>", methods=["GET"])
    def pro_rel_get(email):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        from outreach.db import get_contact_email_history, get_db, _fetchval
        note = pdb.get_relationship_note(_cid(), email) or {}
        with get_db() as db:
            msg_count = _fetchval(db,
                "SELECT COUNT(*) FROM mail_inbox WHERE client_id = %s AND from_email = %s",
                (_cid(), email)) or 0
        return jsonify({
            "email": email,
            "name": note.get("contact_name", ""),
            "role": note.get("contact_role", ""),
            "company": note.get("company", ""),
            "notes": note.get("notes", ""),
            "ai_summary": note.get("ai_summary", ""),
            "msg_count": msg_count,
        })

    @app.route("/api/pro/relationships/<path:email>", methods=["PUT"])
    def pro_rel_update(email):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        pdb.upsert_relationship_note(
            _cid(), email,
            contact_name=d.get("contact_name", ""),
            contact_role=d.get("contact_role", ""),
            company=d.get("company", ""),
            notes=d.get("notes", ""),
        )
        return jsonify({"ok": True})

    @app.route("/api/pro/relationships/<path:email>/summary", methods=["POST"])
    @limiter.limit("10 per minute")
    def pro_rel_summary(email):
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        from outreach.db import get_contact_email_history
        emails = get_contact_email_history(_cid(), email, limit=20) or []
        note = pdb.get_relationship_note(_cid(), email) or {}
        name = note.get("contact_name") or (emails[0].get("from_name") if emails and emails[0].get("from_name") else "")
        summary = pai.relationship_summary(name, email, emails)
        pdb.upsert_relationship_note(_cid(), email, ai_summary=summary,
                                     last_summary_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return jsonify({"summary": summary})

    @app.route("/api/pro/relationships/suggest", methods=["POST"])
    @limiter.limit("6 per minute")
    def pro_rel_suggest():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        d = request.get_json(force=True) or {}
        goal = (d.get("goal") or "").strip()
        if not goal:
            return jsonify({"error": "goal required"}), 400
        contacts = pdb.list_relationship_contacts(_cid(), limit=80)
        return jsonify({"result": pai.suggest_contacts_for_goal(goal, contacts)})

    @app.route("/api/pro/relationships/reconnect", methods=["POST"])
    @limiter.limit("6 per minute")
    def pro_rel_reconnect():
        if not _logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        contacts = pdb.list_relationship_contacts(_cid(), limit=100)
        return jsonify({"result": pai.weekly_reconnect_suggestions(contacts)})

    # ─────────────────────────────────────────────────────────
    # AI TEXT POLISH + LINKEDIN
    # ─────────────────────────────────────────────────────────
    def _ai_tool_page(title, icon, description, form_html, endpoint, active="pro_assistant"):
        return _p_render(title, f"""
        {_today_tasks_banner()}
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

    @app.route("/pro/linkedin-post")
    def pro_linkedin_page():
        if not _logged_in():
            return redirect(url_for("login"))
        form = """
          <div class="form-group"><label>Topic</label><input data-field="topic" class="edit-input" placeholder="The #1 mistake founders make when hiring..."></div>
          <div class="form-group"><label>Key points (optional)</label><textarea data-field="key_points" rows="3" class="edit-input"></textarea></div>
          <div class="form-group"><label>Audience</label><input data-field="audience" class="edit-input" value="founders and operators"></div>"""
        return _ai_tool_page("LinkedIn Post", "&#128100;", "High-performing LinkedIn post with a hook, structure, and 3 relevant hashtags.",
                             form, "/api/pro/ai/linkedin", active="pro_linkedin")

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

    log.info("Professional routes registered.")
