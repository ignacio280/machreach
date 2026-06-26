"""MachReach JR — role-scoped, per-entity JSON API.

Replaces the prototype's blob endpoints (`GET/PUT /api/state`, which handed
every caller the whole school including everyone's password). Here a request is
authenticated by the university session (`session["client_id"]`), resolved to a
single `jr_members` row, and every read/write is scoped through that membership:

    student  -> only self (own stats / plan / exams)
    teacher  -> only their own classes (+ those classes' exams/students)
    parent   -> only their linked children
    admin    -> only their own school (never another school)

CSRF: the blueprint is *not* exempted, so the app-wide flask_wtf CSRFProtect
guards every mutating request (it reads the `X-CSRFToken` header). `GET /jr/api/me`
returns a fresh token the SPA echoes back on writes (wired in Workstream B).

Mounted at /jr/api by app.py via register_blueprint(jr_bp).
"""
from __future__ import annotations

import json
from functools import wraps
from html import escape as _esc

from flask import Blueprint, g, jsonify, request, session
from flask_wtf.csrf import generate_csrf

from outreach.db import get_client, get_client_by_email
from outreach.config import BASE_URL
from outreach.mailer import send_system_email
from outreach import jr_db

jr_bp = Blueprint("jr", __name__, url_prefix="/jr/api")


# ---------------------------------------------------------------------------
# Auth / scoping — runs before every JR endpoint
# ---------------------------------------------------------------------------

# Endpoints reachable by a logged-in client who has no JR membership yet
# (so a freshly-invited admin can create their school).
_NO_MEMBERSHIP_OK = {"jr.me", "jr.create_school"}


@jr_bp.before_request
def _resolve_member():
    client_id = session.get("client_id")
    if not client_id:
        return jsonify(error="unauthorized"), 401

    member = jr_db.get_member_for_client(client_id)
    g.jr_client_id = client_id
    g.jr_member = member
    g.jr_school_id = member["school_id"] if member else None
    g.jr_role = member["role"] if member else None

    if member is None and request.endpoint not in _NO_MEMBERSHIP_OK:
        return jsonify(error="no JR membership"), 403


def require_role(*roles):
    """Gate an endpoint to one or more JR roles."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.jr_role not in roles:
                return jsonify(error="forbidden"), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def _err(msg, code=400):
    return jsonify(error=msg), code


def _body():
    return request.get_json(force=True, silent=True) or {}


# ---------------------------------------------------------------------------
# Shared serializers
# ---------------------------------------------------------------------------

def _member_public(m: dict) -> dict:
    """A member as the client should see it — never a password, never another
    school's internals."""
    return {
        "id": m["id"],
        "name": m.get("name", ""),
        "email": m.get("email", ""),
        "role": m["role"],
        "grade": m.get("grade", ""),
        "section": m.get("section", ""),
        # "invited" until the account accepts its invite (email_verified=1);
        # only then can it log in through the shared university /login.
        "status": "active" if m.get("email_verified") else "invited",
    }


def _class_public(c: dict, exam_count: int | None = None) -> dict:
    out = {
        "id": c["id"],
        "subject": c["subject"],
        "grade": c["grade"],
        "section": c["section"],
        "name": c.get("name", ""),
        "teacher_member_id": c.get("teacher_member_id"),
    }
    if exam_count is not None:
        out["exam_count"] = exam_count
    return out


def _student_dashboard(member: dict) -> dict:
    """Assemble a student's own view. Reused for the parent's child view."""
    mid = member["id"]
    stats = jr_db.get_stats(mid)
    try:
        study_hours = json.loads(stats.get("study_hours") or "{}")
    except Exception:
        study_hours = {}
    classes = jr_db.list_classes_for_student(
        member["school_id"], member.get("grade", ""), member.get("section", ""))
    done_ids = set(jr_db.list_done_exam_ids(mid))
    classes_out = []
    for c in classes:
        exams = jr_db.list_exams(c["id"])
        classes_out.append({
            **_class_public(c),
            "exams": [{
                "id": e["id"], "name": e["name"], "date": e.get("exam_date", ""),
                "pdf_name": e.get("pdf_name", ""), "has_pdf": bool(e.get("has_pdf")),
                "done": e["id"] in done_ids,
            } for e in exams],
        })
    return {
        "member": _member_public(member),
        "stats": {
            "xp": stats.get("xp", 0),
            "focus_min": stats.get("focus_min", 0),
            "streak": stats.get("streak", 0),
            "study_hours": study_hours,
        },
        "classes": classes_out,
        "plan": [{
            "id": b["id"], "date": b.get("block_date", ""), "subject": b.get("subject", ""),
            "topic": b.get("topic", ""), "minutes": b.get("minutes", 0),
            "exam_id": b.get("exam_id"), "done": bool(b.get("done")),
        } for b in jr_db.get_plan(mid)],
    }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@jr_bp.get("/me")
def me():
    """Bootstrap call: who am I, what school/role, and a CSRF token for writes."""
    out = {"csrf_token": generate_csrf()}
    if g.jr_member:
        out["member"] = _member_public(g.jr_member)
        out["school"] = jr_db.get_school(g.jr_school_id)
    else:
        client = get_client(g.jr_client_id) or {}
        out["member"] = None
        out["identity"] = {"name": client.get("name", ""), "email": client.get("email", "")}
    return jsonify(out)


# ---------------------------------------------------------------------------
# School creation (the v2 replacement for blob bootstrap)
# ---------------------------------------------------------------------------

@jr_bp.post("/schools")
def create_school():
    """A logged-in client with no membership creates a school and becomes its
    admin. Provisioning more schools per client is intentionally blocked (one
    client = one school via the jr_members UNIQUE constraint)."""
    if g.jr_member is not None:
        return _err("already a member of a school", 409)
    body = _body()
    name = (body.get("name") or "").strip()
    if not name:
        return _err("name required")
    subjects = body.get("subjects") or []
    school_id = jr_db.create_school(name)
    jr_db.add_member(g.jr_client_id, school_id, "admin")
    if subjects:
        jr_db.set_subjects(school_id, subjects)
    return jsonify(school=jr_db.get_school(school_id)), 201


# ---------------------------------------------------------------------------
# Admin — school
# ---------------------------------------------------------------------------

@jr_bp.get("/school")
@require_role("admin")
def get_school():
    school = jr_db.get_school(g.jr_school_id)
    return jsonify(
        school=school,
        seats_used=jr_db.school_seat_count(g.jr_school_id),
        counts={
            "students": len(jr_db.list_members(g.jr_school_id, "student")),
            "teachers": len(jr_db.list_members(g.jr_school_id, "teacher")),
            "parents": len(jr_db.list_members(g.jr_school_id, "parent")),
            "classes": len(jr_db.list_classes(g.jr_school_id)),
        },
    )


@jr_bp.put("/school")
@require_role("admin")
def update_school():
    body = _body()
    jr_db.update_school(g.jr_school_id, **{
        k: body[k] for k in ("name", "plan", "seat_cap", "contract_start",
                             "contract_end", "status") if k in body
    })
    return jsonify(school=jr_db.get_school(g.jr_school_id))


# ---------------------------------------------------------------------------
# Admin — members
# ---------------------------------------------------------------------------

@jr_bp.get("/members")
@require_role("admin")
def list_members():
    role = request.args.get("role")
    rows = jr_db.list_members(g.jr_school_id, role if role in jr_db.JR_ROLES else None)
    return jsonify(members=[_member_public(m) for m in rows])


def _send_invite(email: str, name: str, school: dict, token: str) -> bool:
    """Email a one-time JR invite link. Returns whether the mail was sent."""
    link = f"{BASE_URL}/jr/invite/{token}"
    school_name = (school or {}).get("name", "tu colegio")
    body = (
        f"Hola {name or ''},\n\n"
        f"Te invitaron a MachReach JR para {school_name}. Crea tu contraseña "
        f"para activar tu cuenta:\n\n"
        f"{link}\n\n"
        f"El enlace vence en {jr_db.INVITE_TTL_HOURS // 24} días.\n\n"
        f"— MachReach JR"
    )
    return send_system_email(email, f"MachReach JR — Activa tu cuenta ({school_name})", body)


@jr_bp.post("/members")
@require_role("admin")
def add_member():
    """Add a member to this school.

    Two paths:
      • the email/client already has a university account -> attach it directly.
      • brand-new email -> provision an UNVERIFIED account and email an invite
        link (the invitee sets their own password, which clears the login gate).
    """
    body = _body()
    role = body.get("role")
    if role not in jr_db.JR_ROLES:
        return _err("valid role required")
    grade = body.get("grade", "")
    section = body.get("section", "")

    # Resolve any existing client by id or email.
    client_id = body.get("client_id")
    email = (body.get("email") or "").strip().lower()
    existing_client = get_client(client_id) if client_id else (
        get_client_by_email(email) if email else None)
    if existing_client and not email:
        email = existing_client.get("email", "")

    # Seat guard (parents are free; seat_cap 0 = unlimited, the pilot default).
    if role != "parent":
        school = jr_db.get_school(g.jr_school_id)
        cap = (school or {}).get("seat_cap") or 0
        if cap and jr_db.school_seat_count(g.jr_school_id) >= cap:
            return _err("seat cap reached for this school", 409)

    if existing_client:
        if jr_db.get_member_for_client(existing_client["id"]):
            return _err("that account already belongs to a school", 409)
        member_id = jr_db.add_member(existing_client["id"], g.jr_school_id, role,
                                     grade=grade, section=section)
        m = jr_db.get_member_in_school(member_id, g.jr_school_id)
        return jsonify(member=_member_public(m), invited=False), 201

    # Brand-new account → provision + invite.
    if not email:
        return _err("email required to invite a new member")
    member_id, token = jr_db.provision_member(
        g.jr_school_id, email, body.get("name", ""), role, grade=grade, section=section)
    school = jr_db.get_school(g.jr_school_id)
    sent = _send_invite(email, body.get("name", ""), school, token)
    m = jr_db.get_member_in_school(member_id, g.jr_school_id)
    return jsonify(member=_member_public(m), invited=True,
                   email_sent=sent, invite_url=f"{BASE_URL}/jr/invite/{token}"), 201


@jr_bp.post("/members/<int:member_id>/resend-invite")
@require_role("admin")
def resend_invite(member_id):
    """Re-mint + re-send an invite for a member who hasn't accepted yet."""
    m = jr_db.get_member_in_school(member_id, g.jr_school_id)
    if not m:
        return _err("member not found", 404)
    if m.get("email_verified"):
        return _err("member already active", 409)
    token = jr_db.create_invite(m["client_id"], g.jr_school_id, m["role"], m["email"])
    sent = _send_invite(m["email"], m.get("name", ""), jr_db.get_school(g.jr_school_id), token)
    return jsonify(ok=True, email_sent=sent, invite_url=f"{BASE_URL}/jr/invite/{token}")


@jr_bp.delete("/members/<int:member_id>")
@require_role("admin")
def remove_member(member_id):
    if not jr_db.remove_member(member_id, g.jr_school_id):
        return _err("member not found", 404)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Admin — subjects
# ---------------------------------------------------------------------------

@jr_bp.get("/subjects")
def list_subjects():
    # Any member of the school may read the subject catalog.
    return jsonify(subjects=jr_db.list_subjects(g.jr_school_id))


@jr_bp.put("/subjects")
@require_role("admin")
def set_subjects():
    body = _body()
    names = body.get("subjects")
    if not isinstance(names, list):
        return _err("subjects array required")
    jr_db.set_subjects(g.jr_school_id, names)
    return jsonify(subjects=jr_db.list_subjects(g.jr_school_id))


# ---------------------------------------------------------------------------
# Admin — classes
# ---------------------------------------------------------------------------

@jr_bp.get("/classes")
@require_role("admin")
def list_classes():
    rows = jr_db.list_classes(g.jr_school_id)
    return jsonify(classes=[
        _class_public(c, exam_count=len(jr_db.list_exams(c["id"]))) for c in rows
    ])


@jr_bp.post("/classes")
@require_role("admin")
def add_class():
    body = _body()
    subject = (body.get("subject") or "").strip()
    grade = (body.get("grade") or "").strip()
    if not subject or not grade:
        return _err("subject and grade required")
    section = (body.get("section") or "").strip()
    name = (body.get("name") or "").strip()
    teacher_member_id = body.get("teacher_member_id")
    if teacher_member_id is not None:
        # A teacher can only be assigned if they belong to this school.
        if not jr_db.get_member_in_school(teacher_member_id, g.jr_school_id):
            return _err("teacher not in this school", 400)
    try:
        class_id = jr_db.add_class(g.jr_school_id, subject, grade, section, name, teacher_member_id)
    except Exception:
        return _err("class already exists for that subject/grade/section", 409)
    return jsonify(**{"class": jr_db.get_class(class_id, g.jr_school_id)}), 201


@jr_bp.put("/classes/<int:class_id>/teacher")
@require_role("admin")
def assign_teacher(class_id):
    body = _body()
    teacher_member_id = body.get("teacher_member_id")
    if teacher_member_id is not None:
        t = jr_db.get_member_in_school(teacher_member_id, g.jr_school_id)
        if not t or t["role"] != "teacher":
            return _err("not a teacher in this school", 400)
    if not jr_db.assign_teacher(class_id, g.jr_school_id, teacher_member_id):
        return _err("class not found", 404)
    return jsonify(ok=True)


@jr_bp.delete("/classes/<int:class_id>")
@require_role("admin")
def remove_class(class_id):
    if not jr_db.remove_class(class_id, g.jr_school_id):
        return _err("class not found", 404)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Admin — parent/child links
# ---------------------------------------------------------------------------

@jr_bp.post("/links")
@require_role("admin")
def add_link():
    body = _body()
    parent_id = body.get("parent_member_id")
    student_id = body.get("student_member_id")
    p = jr_db.get_member_in_school(parent_id, g.jr_school_id) if parent_id else None
    s = jr_db.get_member_in_school(student_id, g.jr_school_id) if student_id else None
    if not p or p["role"] != "parent":
        return _err("parent_member_id must be a parent in this school")
    if not s or s["role"] != "student":
        return _err("student_member_id must be a student in this school")
    jr_db.link_parent(parent_id, student_id)
    return jsonify(ok=True)


@jr_bp.delete("/links")
@require_role("admin")
def remove_link():
    body = _body()
    parent_id = body.get("parent_member_id")
    student_id = body.get("student_member_id")
    # Both must be in this admin's school to touch the link.
    if not (parent_id and student_id
            and jr_db.get_member_in_school(parent_id, g.jr_school_id)
            and jr_db.get_member_in_school(student_id, g.jr_school_id)):
        return _err("link not in this school", 404)
    jr_db.unlink_parent(parent_id, student_id)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Teacher — classes & exams
# ---------------------------------------------------------------------------

def _class_for_teacher_or_admin(class_id):
    """Return the class if the caller may manage it, else None.

    Admin: any class in their school. Teacher: only classes they own.
    """
    c = jr_db.get_class(class_id, g.jr_school_id)
    if not c:
        return None
    if g.jr_role == "admin":
        return c
    if g.jr_role == "teacher" and c.get("teacher_member_id") == g.jr_member["id"]:
        return c
    return None


@jr_bp.get("/teacher/classes")
@require_role("teacher")
def teacher_classes():
    rows = jr_db.list_classes_for_teacher(g.jr_member["id"])
    # Students of a class = school students in that grade+section.
    students = jr_db.list_members(g.jr_school_id, "student")
    out = []
    for c in rows:
        student_count = sum(
            1 for s in students
            if s.get("grade", "") == c["grade"] and s.get("section", "") == c["section"]
        )
        out.append({
            **_class_public(c, exam_count=len(jr_db.list_exams(c["id"]))),
            "student_count": student_count,
        })
    return jsonify(classes=out)


@jr_bp.get("/classes/<int:class_id>/exams")
@require_role("admin", "teacher")
def class_exams(class_id):
    c = _class_for_teacher_or_admin(class_id)
    if not c:
        return _err("class not found", 404)
    exams = jr_db.list_exams(class_id)
    return jsonify(exams=[{
        "id": e["id"], "name": e["name"], "date": e.get("exam_date", ""),
        "pdf_name": e.get("pdf_name", ""), "has_pdf": bool(e.get("has_pdf")),
    } for e in exams])


@jr_bp.post("/classes/<int:class_id>/exams")
@require_role("admin", "teacher")
def add_exam(class_id):
    c = _class_for_teacher_or_admin(class_id)
    if not c:
        return _err("class not found", 404)
    body = _body()
    name = (body.get("name") or "").strip()
    if not name:
        return _err("exam name required")
    exam_id = jr_db.add_exam(
        class_id, name, body.get("date", ""),
        body.get("pdf_name", ""), body.get("pdf_data", ""))
    return jsonify(exam_id=exam_id), 201


@jr_bp.delete("/exams/<int:exam_id>")
@require_role("admin", "teacher")
def remove_exam(exam_id):
    exam = jr_db.get_exam(exam_id)
    if not exam or not jr_db.exam_belongs_to_school(exam_id, g.jr_school_id):
        return _err("exam not found", 404)
    if g.jr_role == "teacher":
        c = _class_for_teacher_or_admin(exam["class_id"])
        if not c:
            return _err("forbidden", 403)
    jr_db.remove_exam(exam_id)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Student — self
# ---------------------------------------------------------------------------

@jr_bp.get("/student/dashboard")
@require_role("student")
def student_dashboard():
    return jsonify(_student_dashboard(g.jr_member))


@jr_bp.put("/student/plan")
@require_role("student")
def replace_plan():
    body = _body()
    blocks = body.get("blocks")
    if not isinstance(blocks, list):
        return _err("blocks array required")
    # Only accept exam_ids that belong to this student's school.
    clean = []
    for b in blocks:
        exam_id = b.get("exam_id")
        if exam_id is not None and not jr_db.exam_belongs_to_school(exam_id, g.jr_school_id):
            exam_id = None
        clean.append({
            "block_date": b.get("date", ""), "subject": b.get("subject", ""),
            "topic": b.get("topic", ""), "minutes": b.get("minutes", 0),
            "exam_id": exam_id, "done": bool(b.get("done")),
        })
    jr_db.replace_plan(g.jr_member["id"], clean)
    return jsonify(ok=True, plan=[{
        "id": b["id"], "date": b.get("block_date", ""), "subject": b.get("subject", ""),
        "topic": b.get("topic", ""), "minutes": b.get("minutes", 0),
        "exam_id": b.get("exam_id"), "done": bool(b.get("done")),
    } for b in jr_db.get_plan(g.jr_member["id"])])


@jr_bp.post("/student/plan/<int:block_id>/done")
@require_role("student")
def toggle_block(block_id):
    done = bool(_body().get("done"))
    if not jr_db.set_block_done(block_id, g.jr_member["id"], done):
        return _err("block not found", 404)
    # Mirror the prototype's reward: +15 XP per completed block.
    if done:
        jr_db.add_xp(g.jr_member["id"], 15)
    return jsonify(ok=True, xp=jr_db.get_stats(g.jr_member["id"]).get("xp", 0))


@jr_bp.post("/student/exams/<int:exam_id>/done")
@require_role("student")
def toggle_exam_done(exam_id):
    if not jr_db.exam_belongs_to_school(exam_id, g.jr_school_id):
        return _err("exam not found", 404)
    done = bool(_body().get("done"))
    jr_db.set_exam_done(g.jr_member["id"], exam_id, done)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Parent — linked children only
# ---------------------------------------------------------------------------

@jr_bp.get("/parent/children")
@require_role("parent")
def parent_children():
    kids = jr_db.children_of(g.jr_member["id"])
    return jsonify(children=[_member_public(k) for k in kids])


@jr_bp.get("/parent/children/<int:member_id>/dashboard")
@require_role("parent")
def parent_child_dashboard(member_id):
    if not jr_db.is_parent_of(g.jr_member["id"], member_id):
        return _err("not your child", 403)
    child = jr_db.get_member_in_school(member_id, g.jr_school_id)
    if not child or child["role"] != "student":
        return _err("child not found", 404)
    return jsonify(_student_dashboard(child))


# ===========================================================================
# Public invite-acceptance pages (NO session required) — Workstream C
# ===========================================================================
# Separate blueprint: the /jr/api before_request demands a session, but an
# invitee has no account-session yet. These minimal HTML pages let them set a
# password, which is the only path that flips email_verified=1 and unlocks the
# shared university /login. CSRF on the POST is enforced by the app-wide
# flask_wtf CSRFProtect (the hidden csrf_token field below).

jr_invite_bp = Blueprint("jr_invite", __name__, url_prefix="/jr")


def _page(title: str, inner: str, code: int = 200):
    doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
  :root {{ --brand:#4f46e5; --ink:#1f2330; --muted:#6b7280; --line:#e5e7eb; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif; background:#f6f7fb;
         color:var(--ink); margin:0; display:flex; min-height:100vh;
         align-items:center; justify-content:center; padding:20px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:16px;
          padding:28px; width:100%; max-width:420px;
          box-shadow:0 10px 30px rgba(20,20,50,.06); }}
  h1 {{ font-size:21px; margin:0 0 4px; }}
  p.sub {{ color:var(--muted); margin:0 0 18px; font-size:14px; }}
  label {{ display:block; font-size:13px; font-weight:600; margin:12px 0 5px; }}
  input[type=password] {{ width:100%; padding:11px 12px; border:1px solid var(--line);
          border-radius:10px; font-size:15px; }}
  button {{ width:100%; margin-top:18px; padding:12px; border:0; border-radius:10px;
          background:var(--brand); color:#fff; font-size:15px; font-weight:600;
          cursor:pointer; }}
  .err {{ background:#fef2f2; color:#b91c1c; border:1px solid #fecaca;
          border-radius:10px; padding:10px 12px; font-size:14px; margin-bottom:6px; }}
  .ok {{ font-size:38px; }}
</style></head><body><div class="card">{inner}</div></body></html>"""
    return doc, code


def _invite_form(token: str, school_name: str, name: str, error: str = ""):
    err_html = f'<div class="err">{_esc(error)}</div>' if error else ""
    return _page("Activar cuenta — MachReach JR", f"""
      <h1>Crea tu contraseña</h1>
      <p class="sub">Hola {_esc(name) or "👋"}, activa tu cuenta de
        <b>{_esc(school_name)}</b>.</p>
      {err_html}
      <form method="post" action="/jr/invite/{_esc(token)}" autocomplete="off">
        <input type="hidden" name="csrf_token" value="{generate_csrf()}">
        <label>Contraseña (mínimo 6)</label>
        <input type="password" name="password" minlength="6" required autocomplete="new-password">
        <label>Repite la contraseña</label>
        <input type="password" name="password2" minlength="6" required autocomplete="new-password">
        <button type="submit">Activar cuenta</button>
      </form>
    """)


def _invalid_page():
    return _page("Enlace inválido — MachReach JR", """
      <h1>Enlace inválido o vencido</h1>
      <p class="sub">Esta invitación ya fue usada o expiró. Pídele a tu colegio
        que te reenvíe el enlace.</p>
    """, 400)


@jr_invite_bp.get("/invite/<token>")
def invite_form(token):
    inv = jr_db.get_valid_invite(token)
    if not inv:
        return _invalid_page()
    school = jr_db.get_school(inv["school_id"]) or {}
    client = get_client(inv["client_id"]) or {}
    return _invite_form(token, school.get("name", ""), client.get("name", ""))


@jr_invite_bp.post("/invite/<token>")
def invite_submit(token):
    inv = jr_db.get_valid_invite(token)
    if not inv:
        return _invalid_page()
    school = jr_db.get_school(inv["school_id"]) or {}
    client = get_client(inv["client_id"]) or {}
    pw = request.form.get("password", "")
    pw2 = request.form.get("password2", "")
    if len(pw) < 6:
        return _invite_form(token, school.get("name", ""), client.get("name", ""),
                            "La contraseña debe tener al menos 6 caracteres.")
    if pw != pw2:
        return _invite_form(token, school.get("name", ""), client.get("name", ""),
                            "Las contraseñas no coinciden.")

    res = jr_db.accept_invite(token, pw)
    if not res:
        return _invalid_page()

    # Log the freshly-activated member in (same session shape as /login).
    full = get_client(res["client_id"]) or {}
    session.clear()
    session["client_id"] = res["client_id"]
    session["client_name"] = full.get("name", "")
    session["account_type"] = full.get("account_type") or "student"
    return _page("Cuenta activada — MachReach JR", f"""
      <div class="ok">✅</div>
      <h1>¡Cuenta activada!</h1>
      <p class="sub">Listo, {_esc(full.get("name", ""))}. Tu cuenta de
        <b>{_esc(school.get("name", ""))}</b> ya está activa y tu sesión iniciada.</p>
    """)
