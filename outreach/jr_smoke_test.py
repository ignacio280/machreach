"""End-to-end smoke test for the JR v2 persistence + scoped API (Workstream A).

Runs against a throwaway SQLite file (never the real local/prod DB): we force
DATABASE_URL="" and a temp DATABASE_PATH *before* importing any outreach module,
and python-dotenv won't override already-set env vars.

Two layers are exercised:
  1. jr_db   — schema + the scoped data-access helpers.
  2. jr_api  — mounted on a minimal Flask app (only jr_bp) with a stubbed
               session, asserting the tenancy invariants hold over HTTP:
               no session -> 401, wrong role -> 403, and no path leaks across
               schools or to another parent's child.

Run:  .venv\\Scripts\\python.exe outreach\\jr_smoke_test.py
"""
import os
import sys
import tempfile

# ── Isolate to a temp SQLite DB BEFORE importing config/db ──
# (python-dotenv's load_dotenv won't override env vars we set here first.)
_TMP = tempfile.mkdtemp(prefix="jr_smoke_")
os.environ["DATABASE_URL"] = ""                       # force SQLite branch
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "jr_smoke.sqlite3")
# Neutralize transactional email: an empty SMTP password makes send_system_email
# short-circuit to False without any network — the invite flow is exercised via
# the returned invite_url, not the email itself.
os.environ["SMTP_PASSWORD"] = ""
os.environ["SYSTEM_SMTP_PASSWORD"] = ""

from outreach.db import init_db, create_client, get_client_by_email   # noqa: E402
from outreach import jr_db                               # noqa: E402

_FAILS = []


def check(cond, label):
    status = "ok  " if cond else "FAIL"
    print(f"  [{status}] {label}")
    if not cond:
        _FAILS.append(label)


def section(title):
    print(f"\n== {title} ==")


def _client(name, email):
    return create_client(name, email, "x-hash", account_type="student")


# ---------------------------------------------------------------------------
# Layer 1 — jr_db
# ---------------------------------------------------------------------------

def test_db_layer():
    section("jr_db data layer")
    init_db()        # creates clients + the rest of the university schema
    jr_db.jr_init_db()

    # Two independent schools, to prove cross-school isolation later.
    s1 = jr_db.create_school("Colegio San Diego")
    s2 = jr_db.create_school("Colegio Los Andes")
    check(s1 != s2, "two schools get distinct ids")

    # School 1 membership.
    c_admin = _client("Admin Uno", "admin1@s1.cl")
    c_teach = _client("Prof Rojas", "rojas@s1.cl")
    c_stud = _client("Sofia Diaz", "sofia@s1.cl")
    c_par = _client("Apoderado Sofia", "apo@s1.cl")

    m_admin = jr_db.add_member(c_admin, s1, "admin")
    m_teach = jr_db.add_member(c_teach, s1, "teacher")
    m_stud = jr_db.add_member(c_stud, s1, "student", grade="8° básico", section="A")
    m_par = jr_db.add_member(c_par, s1, "parent")

    check(jr_db.get_member_for_client(c_stud)["id"] == m_stud,
          "client resolves to its member")
    check(jr_db.get_member_in_school(m_stud, s2) is None,
          "member not visible from another school")

    # Seats: 3 (admin+teacher+student), parents don't consume a seat.
    check(jr_db.school_seat_count(s1) == 3, "seat count excludes parents")

    # Subjects.
    jr_db.set_subjects(s1, ["Matemática", "Lenguaje", "Historia"])
    check(jr_db.list_subjects(s1) == ["Historia", "Lenguaje", "Matemática"],
          "subjects stored + sorted")

    # Class + teacher assignment.
    cls = jr_db.add_class(s1, "Matemática", "8° básico", "A", teacher_member_id=m_teach)
    check(len(jr_db.list_classes_for_teacher(m_teach)) == 1, "teacher sees their class")
    check(len(jr_db.list_classes_for_student(s1, "8° básico", "A")) == 1,
          "student sees their grade/section class")
    check(len(jr_db.list_classes_for_student(s1, "8° básico", "B")) == 0,
          "student in other section sees nothing")

    # Exam.
    ex = jr_db.add_exam(cls, "Prueba fracciones", "2026-07-10")
    check(jr_db.exam_belongs_to_school(ex, s1) is True, "exam scoped to its school")
    check(jr_db.exam_belongs_to_school(ex, s2) is False, "exam not in other school")

    # Parent link.
    jr_db.link_parent(m_par, m_stud)
    check(jr_db.is_parent_of(m_par, m_stud) is True, "parent linked to child")
    check([k["id"] for k in jr_db.children_of(m_par)] == [m_stud], "children_of returns child")

    # Stats + xp.
    start_xp = jr_db.get_stats(m_stud)["xp"]
    jr_db.add_xp(m_stud, 15)
    check(jr_db.get_stats(m_stud)["xp"] == start_xp + 15, "add_xp increments")

    # Exam-done toggle.
    jr_db.set_exam_done(m_stud, ex, True)
    check(jr_db.list_done_exam_ids(m_stud) == [ex], "exam marked done")
    jr_db.set_exam_done(m_stud, ex, False)
    check(jr_db.list_done_exam_ids(m_stud) == [], "exam un-done")

    # Plan replace + block toggle.
    jr_db.replace_plan(m_stud, [
        {"block_date": "2026-07-08", "subject": "Matemática", "topic": "Estudiar",
         "minutes": 60, "exam_id": ex, "done": False},
    ])
    plan = jr_db.get_plan(m_stud)
    check(len(plan) == 1, "plan stored")
    check(jr_db.set_block_done(plan[0]["id"], m_stud, True), "block toggled by owner")
    check(jr_db.set_block_done(plan[0]["id"], m_teach, True) is False,
          "block NOT toggleable by a non-owner member")

    # Hard-delete cascade.
    jr_db.remove_member(m_stud, s1)
    check(jr_db.get_member(m_stud) is None, "member removed")
    check(jr_db.get_plan(m_stud) == [], "plan cascade-deleted with member")

    return {
        "s1": s1, "s2": s2,
        "admin1": c_admin, "teach1": c_teach, "par1": c_par,
    }


# ---------------------------------------------------------------------------
# Layer 2 — jr_api over HTTP (minimal app, stubbed session)
# ---------------------------------------------------------------------------

def _make_app():
    from flask import Flask
    from outreach.jr_api import jr_bp, jr_invite_bp
    app = Flask(__name__)
    app.secret_key = "smoke-secret"
    app.config["WTF_CSRF_ENABLED"] = False   # CSRF is the app-wide layer; not under test here
    app.register_blueprint(jr_bp)
    app.register_blueprint(jr_invite_bp)
    return app


def _as(client, client_id):
    """Stub a logged-in session for a given university client id."""
    with client.session_transaction() as sess:
        if client_id is None:
            sess.clear()
        else:
            sess["client_id"] = client_id


def test_api_scoping():
    section("jr_api scoping over HTTP")
    app = _make_app()
    c = app.test_client()

    # Fresh clients (the db-layer student was deleted; make a clean cast).
    a1 = create_client("Dir Uno", "dir1@api.cl", "h", account_type="student")
    t1 = create_client("Prof Uno", "prof1@api.cl", "h", account_type="student")
    s1c = create_client("Alum Uno", "alum1@api.cl", "h", account_type="student")
    p1 = create_client("Apo Uno", "apo1@api.cl", "h", account_type="student")
    a2 = create_client("Dir Dos", "dir2@api.cl", "h", account_type="student")
    s2c = create_client("Alum Dos", "alum2@api.cl", "h", account_type="student")

    # No session → 401.
    _as(c, None)
    check(c.get("/jr/api/me").status_code == 401, "no session -> 401")

    # a1 has no membership yet → /me ok (200) but /school forbidden until a school exists.
    _as(c, a1)
    r = c.get("/jr/api/me")
    check(r.status_code == 200 and r.get_json()["member"] is None,
          "logged-in non-member can call /me")

    # a1 creates School A → becomes admin.
    r = c.post("/jr/api/schools", json={"name": "Escuela A", "subjects": ["Mate", "Lenguaje"]})
    check(r.status_code == 201, "non-member creates school -> 201")
    school_a = r.get_json()["school"]["id"]

    # Can't create a second school as an existing member.
    check(c.post("/jr/api/schools", json={"name": "X"}).status_code == 409,
          "existing member cannot create another school")

    # Admin adds teacher, student, parent (existing clients by email).
    def add_member(email, role, **extra):
        return c.post("/jr/api/members", json={"email": email, "role": role, **extra})

    rt = add_member("prof1@api.cl", "teacher")
    rs = add_member("alum1@api.cl", "student", grade="8° básico", section="A")
    rp = add_member("apo1@api.cl", "parent")
    check(rt.status_code == 201 and rs.status_code == 201 and rp.status_code == 201,
          "admin provisions teacher/student/parent")
    m_teach = rt.get_json()["member"]["id"]
    m_stud = rs.get_json()["member"]["id"]
    m_par = rp.get_json()["member"]["id"]

    # Class + assign teacher + exam (as admin then teacher).
    rc = c.post("/jr/api/classes", json={"subject": "Mate", "grade": "8° básico", "section": "A"})
    check(rc.status_code == 201, "admin creates class")
    class_a = rc.get_json()["class"]["id"]
    check(c.put(f"/jr/api/classes/{class_a}/teacher",
                json={"teacher_member_id": m_teach}).status_code == 200,
          "admin assigns teacher")
    check(c.post("/jr/api/links",
                 json={"parent_member_id": m_par, "student_member_id": m_stud}
                 ).status_code == 200, "admin links parent->student")

    # Teacher adds an exam to their class.
    _as(c, t1)
    re = c.post(f"/jr/api/classes/{class_a}/exams",
                json={"name": "Prueba 1", "date": "2026-07-10"})
    check(re.status_code == 201, "teacher adds exam to own class")
    exam_a = re.get_json()["exam_id"]
    # Teacher cannot call an admin-only endpoint.
    check(c.get("/jr/api/members").status_code == 403, "teacher blocked from /members (admin only)")

    # Student sees own dashboard (class + exam), can plan + toggle.
    _as(c, s1c)
    rd = c.get("/jr/api/student/dashboard")
    check(rd.status_code == 200, "student loads own dashboard")
    dash = rd.get_json()
    check(len(dash["classes"]) == 1 and len(dash["classes"][0]["exams"]) == 1,
          "dashboard shows the class + exam")
    check(c.get("/jr/api/school").status_code == 403, "student blocked from admin /school")
    rp2 = c.put("/jr/api/student/plan", json={"blocks": [
        {"date": "2026-07-08", "subject": "Mate", "topic": "Repasar", "minutes": 45,
         "exam_id": exam_a}]})
    check(rp2.status_code == 200 and len(rp2.get_json()["plan"]) == 1, "student saves plan")
    block_id = rp2.get_json()["plan"][0]["id"]
    rdone = c.post(f"/jr/api/student/plan/{block_id}/done", json={"done": True})
    check(rdone.status_code == 200 and rdone.get_json()["xp"] >= 15,
          "completing a block awards xp")

    # Parent sees only the linked child.
    _as(c, p1)
    rch = c.get("/jr/api/parent/children")
    check(rch.status_code == 200 and len(rch.get_json()["children"]) == 1,
          "parent sees their one child")
    check(c.get(f"/jr/api/parent/children/{m_stud}/dashboard").status_code == 200,
          "parent opens their child's dashboard")

    # ── Cross-school isolation ──
    _as(c, a2)
    school_b = c.post("/jr/api/schools", json={"name": "Escuela B"}).get_json()["school"]["id"]
    check(school_a != school_b, "second admin gets a separate school")
    rs2 = c.post("/jr/api/members", json={"email": "alum2@api.cl", "role": "student",
                                          "grade": "8° básico", "section": "A"})
    m_stud2 = rs2.get_json()["member"]["id"]

    # Admin B cannot delete a member of School A.
    check(c.delete(f"/jr/api/members/{m_stud}").status_code == 404,
          "admin B cannot delete School A's member")
    # Admin B cannot read School A's class exams.
    check(c.get(f"/jr/api/classes/{class_a}/exams").status_code == 404,
          "admin B cannot read School A's class")

    # Parent A cannot open School B's student.
    _as(c, p1)
    check(c.get(f"/jr/api/parent/children/{m_stud2}/dashboard").status_code == 403,
          "parent A cannot open a non-child (other school)")


def test_invite_flow():
    section("jr_api invite-based provisioning (Workstream C)")
    app = _make_app()
    admin = app.test_client()      # the school admin
    invitee = app.test_client()    # a brand-new person clicking the email link

    # Admin sets up a school.
    a = create_client("Dir Invite", "dir-inv@api.cl", "h", account_type="student")
    _as(admin, a)
    admin.post("/jr/api/schools", json={"name": "Escuela Invite"})

    # Invite a brand-new email (no prior account).
    new_email = "nuevo.alumno@api.cl"
    check(get_client_by_email(new_email) is None, "invitee has no account yet")
    r = admin.post("/jr/api/members", json={
        "email": new_email, "name": "Nuevo Alumno", "role": "student",
        "grade": "8° básico", "section": "A"})
    body = r.get_json()
    check(r.status_code == 201 and body["invited"] is True, "new email -> provisioned + invited")
    check(body["member"]["status"] == "invited", "member shows as invited (not active)")
    member_id = body["member"]["id"]
    token = body["invite_url"].rsplit("/jr/invite/", 1)[1]

    # The account now exists but is gated (email_verified=0 => /login refuses it).
    acct = get_client_by_email(new_email)
    check(acct is not None and not acct.get("email_verified"),
          "provisioned account exists but is unverified")

    # The invite page renders for a fresh visitor (no session).
    g = invitee.get(f"/jr/invite/{token}")
    check(g.status_code == 200 and "contraseña" in g.get_data(as_text=True).lower(),
          "invite link shows the set-password form")

    # Validation: mismatch + too-short are rejected (form re-rendered with error).
    bad = invitee.post(f"/jr/invite/{token}", data={"password": "abcdef", "password2": "zzzzzz"})
    check("no coinciden" in bad.get_data(as_text=True), "mismatched passwords rejected")
    short = invitee.post(f"/jr/invite/{token}", data={"password": "abc", "password2": "abc"})
    check("al menos 6" in short.get_data(as_text=True), "short password rejected")

    # Accept for real → account activated + session started.
    ok = invitee.post(f"/jr/invite/{token}", data={"password": "miClave123", "password2": "miClave123"})
    check(ok.status_code == 200 and "activada" in ok.get_data(as_text=True),
          "valid password activates the account")
    acct2 = get_client_by_email(new_email)
    check(bool(acct2.get("email_verified")), "email_verified cleared by accepting invite")
    import bcrypt
    check(bcrypt.checkpw(b"miClave123", acct2["password"].encode()),
          "the chosen password verifies against the stored bcrypt hash")

    # The invitee is now logged in and scoped as their student self.
    dash = invitee.get("/jr/api/student/dashboard")
    check(dash.status_code == 200, "activated member can load their own dashboard")

    # Token is single-use.
    check(invitee.get(f"/jr/invite/{token}").status_code == 400, "used token is invalid")

    # Resending an invite to an already-active member is refused.
    check(admin.post(f"/jr/api/members/{member_id}/resend-invite").status_code == 409,
          "cannot resend invite to an active member")

    # Resend works for a still-pending member, and rotates the token.
    r2 = admin.post("/jr/api/members", json={
        "email": "pendiente@api.cl", "name": "Pendiente", "role": "teacher"})
    tok_old = r2.get_json()["invite_url"].rsplit("/jr/invite/", 1)[1]
    pend_id = r2.get_json()["member"]["id"]
    rs = admin.post(f"/jr/api/members/{pend_id}/resend-invite")
    tok_new = rs.get_json()["invite_url"].rsplit("/jr/invite/", 1)[1]
    check(rs.status_code == 200 and tok_new != tok_old, "resend issues a fresh token")
    check(invitee.get(f"/jr/invite/{tok_old}").status_code == 400, "old token invalidated by resend")
    check(invitee.get(f"/jr/invite/{tok_new}").status_code == 200, "new token valid")

    # Attaching an EXISTING verified account skips the invite entirely.
    ex = create_client("Ya Existe", "existe@api.cl", jr_db.hash_password("x"),
                       account_type="student")
    with jr_db.get_db() as db:
        jr_db._exec(db, "UPDATE clients SET email_verified = 1 WHERE id = %s", (ex,))
    ra = admin.post("/jr/api/members", json={"email": "existe@api.cl", "role": "parent"})
    check(ra.status_code == 201 and ra.get_json()["invited"] is False,
          "existing verified account attached without an invite")
    check(ra.get_json()["member"]["status"] == "active", "attached existing account is active")

    # Seat cap is enforced for seat-consuming roles (parents stay free).
    admin.put("/jr/api/school", json={"seat_cap": jr_db.school_seat_count(
        jr_db.get_member_for_client(a)["school_id"])})
    capped = admin.post("/jr/api/members", json={"email": "extra@api.cl", "name": "Extra",
                                                 "role": "student", "grade": "7° básico"})
    check(capped.status_code == 409, "seat cap blocks another seat-consuming member")
    free = admin.post("/jr/api/members", json={"email": "apo-extra@api.cl", "name": "ApoExtra",
                                               "role": "parent"})
    check(free.status_code == 201, "parents are exempt from the seat cap")


def main():
    info = test_db_layer()
    assert info  # keep linter calm
    test_api_scoping()
    test_invite_flow()

    print("\n" + ("=" * 40))
    if _FAILS:
        print(f"FAILED: {len(_FAILS)} check(s)")
        for f in _FAILS:
            print(f"   - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
