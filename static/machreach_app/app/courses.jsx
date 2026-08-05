/* Cursos page */
const SEMS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];
const CRS = [
  {
    code: "MAT1620", name: "Cálculo II", cc: "#FF6A2B", origin: "Canvas / extensión", sessions: 38, studied: "42h 10m",
    next: "Prueba 2 · 04/08 (3d)", urgent: true,
    exams: [
      { nm: "Prueba 1", wt: "25%", dt: "12/06", done: true, files: [{ n: "Guía límites y continuidad.pdf", m: "820 KB · 9 págs" }] },
      { nm: "Prueba 2", wt: "30%", dt: "04/08", files: [{ n: "Guía series de Taylor.pdf", m: "1,2 MB · 14 págs" }, { n: "Prueba 2 años anteriores.pdf", m: "780 KB · 8 págs" }] },
      { nm: "Examen", wt: "45%", dt: "12/09", files: [] },
    ],
    bench: { hrs: "41h", grade: "5,4", mine: "42h", delta: "+3%", n: 23 },
  },
  {
    code: "FIS1523", name: "Termodinámica", cc: "#6FB03A", origin: "Canvas / extensión", sessions: 21, studied: "26h 45m",
    next: "Control 3 · 10/08 (9d)",
    exams: [
      { nm: "Control 1", wt: "15%", dt: "20/06", done: true, files: [] },
      { nm: "Control 2", wt: "15%", dt: "11/07", done: true, files: [] },
      { nm: "Control 3", wt: "15%", dt: "10/08", files: [{ n: "Apuntes ciclo de Carnot.pdf", m: "640 KB · 6 págs" }] },
      { nm: "Examen", wt: "40%", dt: "05/09", files: [] },
    ],
    bench: { hrs: "35h", grade: "4,9", mine: "27h", delta: "−23%", n: 17 },
  },
  {
    code: "QIM100E", name: "Química Orgánica", cc: "#6E4CD8", origin: "Canvas / extensión", sessions: 19, studied: "31h 20m",
    next: "Informe de laboratorio · 13/08 (12d)",
    exams: [
      { nm: "Laboratorio 1", wt: "10%", dt: "28/06", done: true, files: [] },
      { nm: "Informe de laboratorio", wt: "20%", dt: "13/08", files: [{ n: "Pauta informe laboratorio.docx", m: "220 KB · 4 págs" }] },
      { nm: "Examen", wt: "40%", dt: "02/09", files: [{ n: "Grupos funcionales.pdf", m: "1,8 MB · 22 págs" }] },
    ],
    bench: { hrs: "44h", grade: "5,1", mine: "31h", delta: "−29%", n: 31 },
  },
  {
    code: "MAT1203", name: "Álgebra Lineal", cc: "#2FA8C6", origin: "Agregado a mano", sessions: 16, studied: "27h 50m",
    next: "Examen · 26/08 (25d)",
    exams: [{ nm: "Prueba 1", wt: "30%", dt: "01/07", done: true, files: [] }, { nm: "Examen", wt: "40%", dt: "26/08", files: [] }],
    bench: { hrs: "30h", grade: "5,6", mine: "28h", delta: "−7%", n: 12 },
  },
  {
    code: "IIC1103", name: "Introducción a la Programación", cc: "#D2528B", origin: "Canvas / extensión", sessions: 12, studied: "14h 05m",
    next: "Sin evaluaciones próximas", closed: true,
    exams: [{ nm: "Prueba 1", wt: "30%", dt: "05/05", done: true, files: [] }, { nm: "Examen", wt: "40%", dt: "28/06", done: true, files: [] }],
    bench: { hrs: "28h", grade: "5,8", mine: "14h", delta: "−50%", n: 46 },
  },
];

const IconEditPen = (p) => <Icon {...p}><path d="M4 20h4l10-10a2.8 2.8 0 10-4-4L4 16v4z" /><path d="M13.5 6.5l4 4" /></Icon>;

/* The date input hands back yyyy-mm-dd; the exam list shows dd/mm. */
const shortDate = (value) => {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  return parts ? `${parts[3]}/${parts[2]}` : (value || "—");
};

function CourseCard({ c, plus, d = 0, onDelete }) {
  const [open, setOpen] = React.useState(false);
  const [exams, setExams] = React.useState(c.exams);
  const [newEx, setNewEx] = React.useState(null);
  const [editEx, setEditEx] = React.useState(null);
  const [upload, setUpload] = React.useState(null);
  const [openEx, setOpenEx] = React.useState(null);
  const [grade, setGrade] = React.useState("");
  const [passing, setPassing] = React.useState("3.95");
  const [resultStatus, setResultStatus] = React.useState("");
  const [locked, setLocked] = React.useState(!!c.locked);
  const fileIn = React.useRef(null);
  const [ref, seen] = useReveal({ threshold: 0.12 });
  const addExam = async () => {
    if (!newEx.nm.trim()) return setNewEx(null);
    let id = null;
    if (c.id) {
      const response = await fetch("/api/student/courses/" + c.id + "/exams", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ name: newEx.nm, weight_pct: Number(newEx.wt || 0), exam_date: newEx.dt || null }) });
      const body = await response.json();
      if (!response.ok) return alert(body.error || "No se pudo guardar la evaluación.");
      id = body.id;
    }
    setExams((x) => [...x, { id, nm: newEx.nm, wt: (newEx.wt || "0") + "%", dt: newEx.dt || "—", files: [] }]);
    setNewEx(null);
  };
  const startEdit = (exam, index) => setEditEx({
    i: index,
    nm: exam.nm,
    wt: String(parseInt(exam.wt, 10) || 0),
    dt: /^\d{4}-\d{2}-\d{2}$/.test(exam.dt || "") ? exam.dt : "",
  });
  const saveEdit = async () => {
    const target = exams[editEx.i];
    if (!editEx.nm.trim()) return setEditEx(null);
    if (target?.id && c.id) {
      const response = await fetch("/api/student/exams/" + target.id, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" },
        body: JSON.stringify({ course_id: c.id, name: editEx.nm, weight_pct: Number(editEx.wt || 0), exam_date: editEx.dt || null }),
      });
      if (!response.ok) {
        let body = {};
        try { body = await response.json(); } catch (e) { body = {}; }
        return alert(body.error || "No se pudo guardar la evaluación.");
      }
    }
    setExams((x) => x.map((ex, j) => (j === editEx.i
      ? { ...ex, nm: editEx.nm, wt: (editEx.wt || "0") + "%", dt: editEx.dt || "—" }
      : ex)));
    setEditEx(null);
  };
  const removeExam = async (exam, index) => {
    if (exam.id) {
      const response = await fetch("/api/student/exams/" + exam.id, { method: "DELETE", headers: { "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" } });
      if (!response.ok) return alert("No se pudo eliminar la evaluación.");
    }
    setExams((x) => x.filter((_, j) => j !== index));
  };
  // XHR rather than fetch: only XHR reports upload progress, and a syllabus
  // PDF on a phone connection is a long silent wait otherwise.
  const sendFile = (file, examId) => new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/student/courses/${c.id}/sources`);
    request.setRequestHeader("X-CSRFToken", (window.__MACHREACH_APP__ || {}).csrf || "");
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      setUpload({ name: file.name, pct: Math.round((event.loaded / event.total) * 100) });
    };
    // Upload done, server still reading the file: keep the bar full and say so.
    request.upload.onload = () => setUpload({ name: file.name, pct: 100 });
    request.onload = () => {
      let body = {};
      try { body = JSON.parse(request.responseText); } catch (e) { body = {}; }
      if (request.status >= 400) reject(new Error(body.error || "No se pudo subir el archivo."));
      else resolve(body);
    };
    request.onerror = () => reject(new Error("No se pudo subir el archivo."));
    const form = new FormData();
    form.append("exam_id", String(examId));
    form.append("file", file);
    request.send(form);
  });
  const pickFiles = async (e) => {
    const selected = [...(e.target.files || [])];
    const exam = exams[openEx];
    const index = openEx;
    e.target.value = "";
    if (!selected.length || !exam?.id || !c.id) return;
    for (const file of selected) {
      setUpload({ name: file.name, pct: 0 });
      try {
        const body = await sendFile(file, exam.id);
        setExams((x) => x.map((ex, j) => j === index ? { ...ex, files: [...ex.files, { id: body.id, n: body.name, m: `${body.char_count || 0} caracteres` }] } : ex));
      } catch (error) {
        alert(error.message);
      }
    }
    setUpload(null);
  };
  const dropFile = async (ei, fi) => {
    const file = exams[ei]?.files?.[fi];
    if (file?.id) {
      const response = await fetch(`/api/student/files/${file.id}`, { method: "DELETE", headers: { "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" } });
      if (!response.ok) return alert("No se pudo eliminar el archivo.");
    }
    setExams((x) => x.map((ex, j) => (j === ei ? { ...ex, files: ex.files.filter((_, k) => k !== fi) } : ex)));
  };
  const saveResult = async () => {
    if (!c.id) return;
    if (!confirm(`¿Guardar ${grade || "—"} como nota final de ${c.name}?

El ramo queda cerrado: no podrás agregar ni editar evaluaciones ni subir material. Para reabrirlo tendrías que borrar el ramo y crearlo de nuevo.`)) return;
    setResultStatus("Guardando…");
    const response = await fetch(`/api/student/courses/${c.id}/outcome`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ final_grade: grade, passing_grade: passing }) });
    const body = await response.json();
    if (response.ok) {
      setLocked(true);
      setResultStatus("Ramo cerrado con nota " + (grade || "—"));
    } else {
      setResultStatus(body.error || "No se pudo guardar");
    }
  };
  return (
    <article className={"ccard pop" + (seen ? " in" : "")} ref={ref} style={{ "--cc": c.cc, "--d": d + "ms" }}>
      <div className="ccard-head">
        <span className="ccard-code">{c.code}</span>
        <span className="ccard-origin">{c.origin}</span>
        <button className="ccard-del" aria-label="Eliminar curso" onClick={() => onDelete(c)}><IconClose size={14} /></button>
      </div>
      <h2 className="ccard-name">{c.name}</h2>
      <div className="ccard-prof">{c.sessions} sesiones registradas</div>
      <div className="ccard-stats">
        <div className="ccs"><div className="ccs-n num">{c.studied}</div><div className="ccs-l">Estudiado</div></div>
        <div className="ccs"><div className="ccs-n num">{exams.length}</div><div className="ccs-l">Evaluaciones</div></div>
        <div className="ccs"><div className="ccs-n num">{exams.filter((e) => e.done).length}</div><div className="ccs-l">Rendidas</div></div>
      </div>
      {c.closed && <div className="ccard-note">Resultado pendiente: registra tu nota final para poder avanzar de semestre.</div>}
      <div className="ccard-foot">
        <span className={"ccard-next" + (c.urgent ? " urgent" : "")}>↗ {c.next}</span>
        <button className={"ccard-go" + (open ? " open" : "")} onClick={() => setOpen(!open)}>
          {open ? "Ocultar" : "Ver detalles"} <IconChevron size={14} />
        </button>
      </div>
      {open && (
        <div className="cdet">
          <div>
            <div className="cdet-h">
              <h4>Evaluaciones y su material</h4>
              {!locked && <button className="mini" onClick={() => setNewEx({ nm: "", wt: "", dt: "" })}>+ Agregar evaluación</button>}
            </div>
            {exams.map((e, i) => (
              <div key={e.nm + i}>
                {editEx && editEx.i === i ? (
                  <div className="new-ex">
                    <input autoFocus placeholder="Nombre de la evaluación" value={editEx.nm} onChange={(ev) => setEditEx({ ...editEx, nm: ev.target.value })} onKeyDown={(ev) => ev.key === "Enter" && saveEdit()} />
                    <input placeholder="%" value={editEx.wt} onChange={(ev) => setEditEx({ ...editEx, wt: ev.target.value })} />
                    <input type="date" aria-label="Fecha de la evaluación" value={editEx.dt} onChange={(ev) => setEditEx({ ...editEx, dt: ev.target.value })} />
                    <button className="btn btn-primary btn-sm" onClick={saveEdit}>Guardar</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setEditEx(null)}>Cancelar</button>
                  </div>
                ) : (
                <div className={"ex-row" + (openEx === i ? " open" : "")}>
                  <button className="nm" onClick={() => setOpenEx(openEx === i ? null : i)}>
                    <IconChevron size={13} className={openEx === i ? "rot" : ""} /> {e.nm}
                  </button>
                  <span className="mat-count" title="Material de esta evaluación">📎 {e.files.length}</span>
                  <span className="wt">{e.wt}</span>
                  <span className="dt">{e.done ? "Rendida" : shortDate(e.dt)}</span>
                  {!locked && <button className="fx" onClick={() => startEdit(e, i)} aria-label={"Editar " + e.nm}><IconEditPen size={13} /></button>}
                  {!locked && <button className="fx" onClick={() => removeExam(e, i)} aria-label="Quitar evaluación"><IconClose size={13} /></button>}
                </div>
                )}
                {openEx === i && (
                  <div className="ex-mat">
                    <div className="ex-mat-h">Material de {e.nm} — MachReach lo usa para armar quizzes y flashcards de esta evaluación</div>
                    {e.files.map((fl, k) => (
                      <div className="file-row" key={fl.n + k}>
                        <IconBook size={15} color="var(--ink-3)" />
                        <span className="fn">{fl.n}</span>
                        <span className="fm">{fl.m}</span>
                        <button className="fx" onClick={() => dropFile(i, k)} aria-label="Eliminar archivo"><IconClose size={13} /></button>
                      </div>
                    ))}
                    {!e.files.length && !upload && <div className="ex-mat-empty">Sin material todavía para esta evaluación.</div>}
                    {upload && (
                      <div className="up-prog" role="status" aria-live="polite">
                        <div className="up-bar"><i style={{ width: upload.pct + "%" }} /></div>
                        <span className="mono">{upload.name} · {upload.pct < 100 ? upload.pct + "%" : "Procesando…"}</span>
                      </div>
                    )}
                    {!locked && <button className="drop" disabled={!!upload} onClick={() => fileIn.current.click()}><IconSparkle size={15} /> {upload ? "Subiendo…" : `Subir material a ${e.nm}`}</button>}
                  </div>
                )}
              </div>
            ))}
            {newEx && (
              <div className="new-ex">
                <input autoFocus placeholder="Nombre de la evaluación" value={newEx.nm} onChange={(e) => setNewEx({ ...newEx, nm: e.target.value })} onKeyDown={(e) => e.key === "Enter" && addExam()} />
                <input placeholder="%" value={newEx.wt} onChange={(e) => setNewEx({ ...newEx, wt: e.target.value })} />
                <input type="date" aria-label="Fecha de la evaluación" value={newEx.dt} onChange={(e) => setNewEx({ ...newEx, dt: e.target.value })} />
                <button className="btn btn-primary btn-sm" onClick={addExam}>Guardar</button>
              </div>
            )}
            <input type="file" accept=".pdf,.docx,.txt" multiple hidden ref={fileIn} onChange={pickFiles} />
          </div>
          <div>
            <h4>Resultado del ramo</h4>
            {locked ? (
              <div className="course-closed">
                <b>Ramo cerrado con nota {c.final_grade || grade || "—"}</b>
                <p>Ya registraste la nota final, así que este ramo quedó congelado. Sale de tus cursos una semana después y su nota queda guardada en Notas. Si necesitas cambiarla, borra el ramo y créalo de nuevo — eso también quita tu nota del benchmark y borra tu review.</p>
              </div>
            ) : (
            <div className="grade-row">
              <label>Nota final<input type="number" step="0.01" min="1" max="7" placeholder="5,40" value={grade} onChange={(e) => setGrade(e.target.value)} /></label>
              <label>Nota para aprobar<input type="number" step="0.01" min="1" max="7" value={passing} onChange={(e) => setPassing(e.target.value)} /></label>
              <button className="btn btn-ghost btn-sm" onClick={saveResult}>Guardar resultado</button>
              {resultStatus && <span className="mono">{resultStatus}</span>}
            </div>)}
          </div>
        </div>
      )}
    </article>
  );
}

/* The chips used to BE the semester setter: clicking a numeral silently
   rewrote which semester you were in. They are a viewer now, and the semester
   you are actually in is stated, with its own two verbs — corregir (I picked
   wrong) and terminar (the semester is over). Those are different things and
   only one of them should ask for your grades. */
function CoursesHead({ onAdd, onCorrect, onClose, data = {}, viewing, onView }) {
  const [ref, seen] = useReveal({ threshold: 0.2 });
  const current = data.live ? (data.semester || "") : "VI";
  const available = data.live
    ? Array.from(new Set([...(data.semesters || []), current].filter(Boolean)))
    : SEMS.slice(0, 6);
  const closed = new Set(data.closed_semesters || []);
  const shown = viewing || current;
  const ordered = available.slice().sort((a, b) => SEMS.indexOf(a) - SEMS.indexOf(b));

  return (
    <section className={"cr-head rv" + (seen ? " in" : "")} ref={ref}>
      <div>
        <div className="pl-kicker">{data.live ? data.term_label : "2025 · segundo semestre"}</div>
        <h1>Mis cursos</h1>
        <div className="sem">
          <span className="sem-now">
            Semestre actual <b>{current || "—"}</b>
            <button type="button" className="sem-fix" onClick={onCorrect}>Corregir</button>
          </span>
          {ordered.length > 1 && ordered.map((label) => (
            <button key={label} className={shown === label ? "on" : ""} onClick={() => onView(label)}
              title={closed.has(label) ? "Semestre cerrado" : "Semestre en curso"}>
              {label}{closed.has(label) ? " ✓" : ""}
            </button>
          ))}
        </div>
      </div>
      <div className="cr-actions">
        <a href="https://chromewebstore.google.com/detail/djfnmpaihpkibcngaaekhnbalbaibgnk" target="_blank" rel="noopener" className="btn btn-primary"><IconCanvas size={16} /> Sincronizar Canvas</a>
        <button className="btn btn-ghost" onClick={onAdd}>Agregar a mano</button>
        <button className="btn btn-ghost" onClick={onClose}>Terminé el semestre</button>
      </div>
    </section>
  );
}

/* --- Corregir el semestre (no es lo mismo que terminarlo) --- */

function CorrectSemesterModal({ data = {}, csrf = "", onClose, onDone }) {
  const [label, setLabel] = React.useState(data.semester || "I");
  const [move, setMove] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const count = (data.items || []).length;

  const save = async () => {
    setBusy(true); setErr("");
    try {
      const response = await fetch("/api/student/semester/correct", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ label, move_courses: move }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "No se pudo corregir el semestre.");
      onDone(body);
    } catch (error) { setErr(error.message); setBusy(false); }
  };

  return (
    <Modal title="Corregir tu semestre"
      sub="Úsalo si elegiste mal el semestre al empezar. No te pide notas: no estás terminando nada."
      onClose={onClose}
      foot={<>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancelar</button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>{busy ? "Guardando…" : "Guardar"}</button>
      </>}>
      <label className="fld-label">En qué semestre vas realmente</label>
      <div className="sem-grid">
        {SEMS.map((s) => (
          <button key={s} type="button" className={"sem-pick" + (label === s ? " on" : "")} onClick={() => setLabel(s)}>{s}</button>
        ))}
      </div>
      {count > 0 && (
        <label className="sem-move">
          <input type="checkbox" checked={move} onChange={(e) => setMove(e.target.checked)} />
          <span>Mover mis {count} ramo{count === 1 ? "" : "s"} actuales a {label}</span>
        </label>
      )}
      {err && <p className="sem-err">{err}</p>}
    </Modal>
  );
}

/* --- Terminé el semestre --- */

const CLOSE_STATUS = [
  { key: "graded", label: "Tengo la nota" },
  { key: "pending", label: "Aún no la tengo" },
  { key: "withdrawn", label: "Me retiré / convalidé" },
];

function CloseSemesterWizard({ data = {}, csrf = "", onClose, onDone }) {
  const label = data.semester || "";
  const [step, setStep] = React.useState(0);
  const [courses, setCourses] = React.useState([]);
  const [answers, setAnswers] = React.useState({});
  const [dropped, setDropped] = React.useState({});
  const [result, setResult] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    if (!data.live) {
      const demo = (data.items || []).map((c, i) => ({ id: c.id || -i - 1, name: c.name, code: c.code }));
      setCourses(demo);
      return;
    }
    fetch(`/api/student/semester/summary?label=${encodeURIComponent(label)}`)
      .then((r) => r.json())
      .then((body) => {
        setCourses(body.courses || []);
        const seed = {};
        (body.courses || []).forEach((c) => {
          seed[c.id] = { status: c.status || "graded", grade: c.final_grade == null ? "" : String(c.final_grade).replace(".", ",") };
        });
        setAnswers(seed);
      })
      .catch(() => setErr("No pudimos cargar tus ramos."));
  }, [label, data.live]);

  const kept = courses.filter((c) => !dropped[c.id]);
  const setAnswer = (id, patch) => setAnswers((a) => ({ ...a, [id]: { ...(a[id] || {}), ...patch } }));

  const parseGrade = (text) => {
    const value = parseFloat(String(text || "").replace(",", "."));
    return Number.isFinite(value) ? value : null;
  };

  const missing = kept.filter((c) => {
    const answer = answers[c.id] || {};
    if (answer.status !== "graded") return !answer.status;
    const grade = parseGrade(answer.grade);
    return grade === null || grade < 1 || grade > 7;
  });

  const submit = async () => {
    setBusy(true); setErr("");
    try {
      const payload = kept.map((c) => {
        const answer = answers[c.id] || {};
        return {
          course_id: c.id,
          status: answer.status || "graded",
          final_grade: answer.status === "graded" ? parseGrade(answer.grade) : null,
        };
      });
      const response = await fetch("/api/student/semester/close", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ label, answers: payload, next_label: SEMS[Math.min(SEMS.length - 1, SEMS.indexOf(label) + 1)] }),
      });
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body.error || "No se pudo cerrar el semestre.");
      setResult(body);
      setStep(2);
    } catch (error) { setErr(error.message); }
    setBusy(false);
  };

  if (step === 2 && result) {
    return (
      <Modal title={`Semestre ${label} cerrado`} sub="Tus notas quedaron guardadas en Notas."
        onClose={() => onDone(result)}
        foot={<button className="btn btn-primary btn-sm" onClick={() => onDone(result)}>
          {result.next_semester ? `Empezar semestre ${result.next_semester}` : "Listo"}
        </button>}>
        <div className="sem-sum">
          <div><b>{result.average == null ? "—" : String(result.average).replace(".", ",")}</b><span>Promedio</span></div>
          <div><b>{result.passed}</b><span>Aprobados</span></div>
          <div><b>{result.failed}</b><span>Reprobados</span></div>
          <div><b>{Math.round((result.focus_minutes || 0) / 60)}h</b><span>Estudiadas</span></div>
        </div>
        <div className="sem-reward">
          <b>+{result.coins_awarded} 🪙</b>
          <span>por cerrar el semestre{result.pending ? ` · ${result.pending} nota(s) por confirmar` : ""}</span>
        </div>
        {!!(result.badges || []).length && <p className="sem-note">Nueva insignia desbloqueada 🎓</p>}
        {result.best && <p className="sem-note">Tu mejor ramo fue <b>{result.best}</b>.</p>}
      </Modal>
    );
  }

  return (
    <Modal title={`Terminar el semestre ${label}`}
      sub={step === 0 ? "Primero confirma qué ramos cursaste realmente." : "Ahora, cómo terminó cada uno."}
      onClose={onClose}
      foot={step === 0
        ? <>
            <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancelar</button>
            <button className="btn btn-primary btn-sm" onClick={() => setStep(1)} disabled={!kept.length}>Continuar</button>
          </>
        : <>
            <button className="btn btn-ghost btn-sm" onClick={() => setStep(0)}>Atrás</button>
            <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !!missing.length}>
              {busy ? "Cerrando…" : "Cerrar semestre"}
            </button>
          </>}>
      {step === 0 && (
        <div className="sem-list">
          {!courses.length && <p className="sem-note">No hay ramos en este semestre.</p>}
          {courses.map((c) => (
            <label key={c.id} className="sem-row">
              <input type="checkbox" checked={!dropped[c.id]}
                onChange={(e) => setDropped((d) => ({ ...d, [c.id]: !e.target.checked }))} />
              <span className="tx"><b>{c.name}</b><span>{c.code}</span></span>
            </label>
          ))}
          <p className="sem-note">Desmarca un ramo que hayas agregado por error: se queda en tu lista, pero no entra al cierre.</p>
        </div>
      )}
      {step === 1 && (
        <div className="sem-list">
          {kept.map((c) => {
            const answer = answers[c.id] || {};
            return (
              <div key={c.id} className="sem-answer">
                <div className="tx"><b>{c.name}</b><span>{c.code}</span></div>
                <div className="opts">
                  {CLOSE_STATUS.map((option) => (
                    <button key={option.key} type="button"
                      className={"opt" + (answer.status === option.key ? " on" : "")}
                      onClick={() => setAnswer(c.id, { status: option.key })}>{option.label}</button>
                  ))}
                </div>
                {answer.status === "graded" && (
                  <input className="sem-grade" inputMode="decimal" placeholder="Nota final (1,0 – 7,0)"
                    value={answer.grade || ""} onChange={(e) => setAnswer(c.id, { grade: e.target.value })} />
                )}
              </div>
            );
          })}
          <p className="sem-note">
            Solo las notas reales entran a tu promedio y a los benchmarks. Las pendientes
            quedan anotadas para que las confirmes después, y no ensucian nada mientras tanto.
          </p>
          {!!missing.length && <p className="sem-err">Falta responder por {missing.length} ramo(s).</p>}
        </div>
      )}
      {err && <p className="sem-err">{err}</p>}
    </Modal>
  );
}

function CoursesStats({ n = 5, ex = 16, data = {} }) {
  const live = !!data.live;
  const s = [
    ["Cursos activos", n, "", "este semestre", "a", "📘"],
    ["Evaluaciones", ex, "", live ? `${Number(data.completed_exams || 0)} ya rendidas` : "8 ya rendidas", "b", "📝"],
    ["Horas totales", live ? Number(data.total_hours || 0) : 142, "h", live ? `en ${n} ramos` : "en los 5 ramos", "c", "⏱"],
    ["Sesiones", live ? Number(data.total_sessions || 0) : 106, "", "guardadas desde Enfoque", "d", "⚡"],
  ];
  return (
    <div className="cr-stats">
      {s.map(([l, v, suf, sub, cls, deco], i) => <StatCard key={l} cls={cls} label={l} value={v} suffix={suf} sub={sub} deco={deco} d={i * 70} tilt={i % 2 ? "1.2deg" : "-1.4deg"} />)}
    </div>
  );
}

function ExtCard() {
  return (
    <div className="ext-card">
      <span className="ico-badge" style={{ background: "var(--brand)", color: "#fff" }}><IconCanvas size={17} /></span>
      <div>
        <h3>¿Falta un ramo?</h3>
        <p>La extensión de MachReach lee tus cursos y evaluaciones de Canvas cada vez que entras. También puedes agregarlos a mano.</p>
      </div>
      <a href="https://chromewebstore.google.com/detail/djfnmpaihpkibcngaaekhnbalbaibgnk" target="_blank" rel="noopener" className="btn btn-primary btn-sm">Instalar extensión</a>
    </div>
  );
}

function CoursesGrid({ plus, list, onDelete, onAdd }) {
  if (!list.length) {
    // Without this the page just stopped below the stat cards.
    return (
      <div className="cgrid-empty" id="manual-course-panel">
        <div className="ico"><IconBook size={26} /></div>
        <h3>Está vacío…</h3>
        <p>¡Comienza agregando tu primer ramo!</p>
        {onAdd && <button className="btn btn-primary" onClick={onAdd}>Agregar a mano<IconArrow size={17} /></button>}
      </div>
    );
  }
  return <div className="cgrid" id="manual-course-panel">{list.map((c, i) => <CourseCard key={c.code} c={c} plus={plus} d={(i % 2) * 90} onDelete={onDelete} />)}</div>;
}

/* --- Agregar a mano --- */

/* Suggestions shown on the design preview, where there is no API to ask. */
const CATALOG_DEMO = [
  { id: -1, code: "MAT1610", name: "Cálculo I", uses: 184 },
  { id: -2, code: "MAT1620", name: "Cálculo II", uses: 152 },
  { id: -3, code: "MAT1640", name: "Ecuaciones Diferenciales", uses: 97 },
  { id: -4, code: "MAT1203", name: "Álgebra Lineal", uses: 143 },
  { id: -5, code: "FIS1523", name: "Termodinámica", uses: 61 },
  { id: -6, code: "IIC1103", name: "Introducción a la Programación", uses: 128 },
];

/* Course names other students at YOUR university already added. The endpoint is
   scoped to the student's university server-side; we never send a university id. */
function useCourseSuggestions(query, live, active) {
  const [items, setItems] = React.useState([]);
  React.useEffect(() => {
    if (!active) { setItems([]); return undefined; }
    const q = (query || "").trim();
    if (!live) {
      const qn = q.toLowerCase();
      setItems(CATALOG_DEMO.filter((c) => !qn || (c.name + " " + c.code).toLowerCase().includes(qn)).slice(0, 8));
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      fetch("/api/student/courses/catalog?q=" + encodeURIComponent(q))
        .then((r) => (r.ok ? r.json() : { courses: [] }))
        .then((body) => { if (!cancelled) setItems(Array.isArray(body.courses) ? body.courses : []); })
        .catch(() => { if (!cancelled) setItems([]); });
    }, 180);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query, live, active]);
  return items;
}

function AddCourseModal({ onClose, onAdd, live = false }) {
  const [v, setV] = React.useState({ name: "", code: "", color: "#FF6A2B", catalogId: null });
  const [open, setOpen] = React.useState(false);
  const [hi, setHi] = React.useState(-1);
  const PAL = ["#FF6A2B", "#6FB03A", "#6E4CD8", "#2FA8C6", "#D2528B", "#F4B740"];
  const sug = useCourseSuggestions(v.name, live, open);
  const save = () => { if (v.name.trim()) { onAdd(v); onClose(); } };
  const pick = (c) => {
    setV((s) => ({ ...s, name: c.name, code: c.code || s.code, catalogId: c.id > 0 ? c.id : null }));
    setOpen(false);
    setHi(-1);
  };
  const onNameKey = (e) => {
    if (open && sug.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); return setHi((i) => (i + 1) % sug.length); }
      if (e.key === "ArrowUp") { e.preventDefault(); return setHi((i) => (i <= 0 ? sug.length : i) - 1); }
      // Escape closes the list only — Modal's window listener would close the dialog.
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); return setHi(-1); }
      if (e.key === "Enter" && hi >= 0) { e.preventDefault(); return pick(sug[hi]); }
    }
    if (e.key === "Enter") save();
  };
  return (
    <Modal title="Agregar curso a mano" sub="Úsalo para ramos que no llegan desde Canvas. Puedes agregarle evaluaciones y material después."
      onClose={onClose}
      foot={<><button className="btn btn-ghost btn-sm" onClick={onClose}>Cancelar</button><button className="btn btn-primary btn-sm" onClick={save} disabled={!v.name.trim()}>Agregar curso</button></>}>
      <div className="mdl-f">
        <div className="mc-ac">
          <label>Nombre del ramo
            <input id="mc-name" autoFocus autoComplete="off" role="combobox" aria-autocomplete="list"
              aria-expanded={open && sug.length > 0} aria-controls="mc-ac-list"
              placeholder="Ej: Ecuaciones Diferenciales" value={v.name}
              onChange={(e) => { setV({ ...v, name: e.target.value, catalogId: null }); setOpen(true); setHi(-1); }}
              onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onKeyDown={onNameKey} />
          </label>
          {open && sug.length > 0 && (
            <ul className="mc-ac-list" id="mc-ac-list" role="listbox">
              {sug.map((c, i) => (
                <li key={c.id} role="option" aria-selected={i === hi} className={i === hi ? "on" : ""}
                  onMouseEnter={() => setHi(i)} onMouseDown={(e) => { e.preventDefault(); pick(c); }}>
                  <span className="mc-ac-n">{c.name}</span>
                  {c.code ? <span className="mono mc-ac-c">{c.code}</span> : null}
                  {c.uses > 1 ? <span className="mc-ac-u">{c.uses}</span> : null}
                </li>
              ))}
            </ul>
          )}
          {v.catalogId ? <p className="mc-ac-hint">Ya existe en tu universidad — te unirás a ese curso.</p> : null}
        </div>
        <div className="mdl-2">
          <label>Código
            <input id="mc-code" placeholder="MAT1640" value={v.code} onChange={(e) => setV({ ...v, code: e.target.value, catalogId: null })} />
          </label>
          <label>Semestre
            <select defaultValue="VI">{SEMS.map((s) => <option key={s}>{s}</option>)}</select>
          </label>
        </div>
        <label>Color
          <div style={{ display: "flex", gap: 8 }}>
            {PAL.map((p) => (
              <button key={p} onClick={() => setV({ ...v, color: p })} aria-label={"Color " + p}
                style={{ width: 34, height: 34, borderRadius: 10, background: p, border: "2px solid var(--ink)", boxShadow: v.color === p ? "0 0 0 3px var(--amber)" : "0 2px 0 var(--ink)" }} />
            ))}
          </div>
        </label>
      </div>
    </Modal>
  );
}

function DeleteCourseModal({ course, onClose, onConfirm }) {
  return (
    <Modal title={"¿Eliminar " + course.name + "?"} onClose={onClose}
      foot={<><button className="btn btn-ghost btn-sm" onClick={onClose}>Cancelar</button><button className="btn btn-sm" style={{ background: "var(--bad)", color: "#fff" }} onClick={() => { onConfirm(course); onClose(); }}>Eliminar curso</button></>}>
      <div className="mdl-warn">
        <IconShield size={20} />
        <div>Se borran sus {course.exams.length} evaluaciones, el material subido y el tiempo de enfoque asociado. Esto no se puede deshacer.</div>
      </div>
    </Modal>
  );
}

/* Offered on evidence — every evaluation is behind us, or the semester has
   been silent for two weeks. Never closes anything on its own: advancing
   without permission silently corrupts a history that took months to build. */
function SemesterCloseBanner({ hint, csrf = "", onClose }) {
  const [hidden, setHidden] = React.useState(false);
  if (hidden) return null;
  const dismiss = async () => {
    setHidden(true);
    try {
      await fetch("/api/student/semester/prompt", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ label: hint.semester }),
      });
    } catch (e) { /* dismissing is cosmetic; never block the page on it */ }
  };
  return (
    <section className="sem-banner" role="status">
      <span className="ico-badge" style={{ background: "#FFF2C9" }}><IconTrophy size={17} /></span>
      <div className="tx">
        <b>¿Terminaste el semestre {hint.semester}?</b>
        <span>{hint.reason === "inactive"
          ? "Llevas dos semanas sin actividad en estos ramos."
          : "Ya pasaron todas las evaluaciones que tenías agendadas."} Cierra el semestre y guarda tus notas.</span>
      </div>
      <button className="btn btn-primary btn-sm" onClick={onClose}>Terminé el semestre</button>
      <button className="sem-x" onClick={dismiss} aria-label="Ahora no"><IconClose size={14} /></button>
    </section>
  );
}

/* A closed semester is history: readable, not editable. */
function PastSemester({ label, summary, onBack }) {
  const courses = summary?.courses || [];
  const grade = (course) => {
    if (course.status === "pending") return "Nota pendiente";
    if (course.status === "withdrawn") return "Retirado";
    return course.final_grade == null ? "—" : String(course.final_grade).replace(".", ",");
  };
  return (
    <section className="pnl sem-past">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#E8DEFF" }}><IconBook size={17} /></span>
        <h3>Semestre {label}</h3>
        <button className="btn btn-ghost btn-sm" onClick={onBack} style={{ marginLeft: "auto" }}>Volver al actual</button>
      </div>
      {summary?.average != null && (
        <div className="sem-sum">
          <div><b>{String(summary.average).replace(".", ",")}</b><span>Promedio</span></div>
          <div><b>{summary.passed}</b><span>Aprobados</span></div>
          <div><b>{summary.failed}</b><span>Reprobados</span></div>
          <div><b>{Math.round((summary.focus_minutes || 0) / 60)}h</b><span>Estudiadas</span></div>
        </div>
      )}
      <div className="sem-list">
        {!courses.length && <p className="sem-note">Este semestre no tiene ramos guardados.</p>}
        {courses.map((course) => (
          <div className="sem-row" key={course.id}>
            <span className="tx"><b>{course.name}</b><span>{course.code}</span></span>
            <span className={"sem-grade-tag" + (course.status === "pending" ? " warn" : "")}>{grade(course)}</span>
          </div>
        ))}
      </div>
      <p className="sem-note">Los semestres cerrados son solo lectura. Sus notas viven en Notas.</p>
    </section>
  );
}

Object.assign(window, { CoursesHead, CoursesStats, CoursesGrid, ExtCard, AddCourseModal, DeleteCourseModal, CorrectSemesterModal, CloseSemesterWizard, SemesterCloseBanner, PastSemester, CRS, SEMS });
