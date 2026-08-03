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

function CourseCard({ c, plus, d = 0, onDelete }) {
  const [open, setOpen] = React.useState(false);
  const [exams, setExams] = React.useState(c.exams);
  const [newEx, setNewEx] = React.useState(null);
  const [openEx, setOpenEx] = React.useState(null);
  const [grade, setGrade] = React.useState("");
  const [passing, setPassing] = React.useState("3.95");
  const [resultStatus, setResultStatus] = React.useState("");
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
  const removeExam = async (exam, index) => {
    if (exam.id) {
      const response = await fetch("/api/student/exams/" + exam.id, { method: "DELETE", headers: { "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" } });
      if (!response.ok) return alert("No se pudo eliminar la evaluación.");
    }
    setExams((x) => x.filter((_, j) => j !== index));
  };
  const pickFiles = async (e) => {
    const selected = [...(e.target.files || [])];
    const exam = exams[openEx];
    e.target.value = "";
    if (!selected.length || !exam?.id || !c.id) return;
    for (const file of selected) {
      const form = new FormData(); form.append("exam_id", String(exam.id)); form.append("file", file);
      const response = await fetch(`/api/student/courses/${c.id}/sources`, { method: "POST", headers: { "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: form });
      const body = await response.json();
      if (!response.ok) { alert(body.error || "No se pudo subir el archivo."); continue; }
      setExams((x) => x.map((ex, j) => j === openEx ? { ...ex, files: [...ex.files, { id: body.id, n: body.name, m: `${body.char_count || 0} caracteres` }] } : ex));
    }
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
    setResultStatus("Guardando…");
    const response = await fetch(`/api/student/courses/${c.id}/outcome`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ final_grade: grade, passing_grade: passing }) });
    const body = await response.json();
    setResultStatus(response.ok ? "Resultado guardado" : (body.error || "No se pudo guardar"));
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
              <button className="mini" onClick={() => setNewEx({ nm: "", wt: "", dt: "" })}>+ Agregar evaluación</button>
            </div>
            {exams.map((e, i) => (
              <div key={e.nm + i}>
                <div className={"ex-row" + (openEx === i ? " open" : "")}>
                  <button className="nm" onClick={() => setOpenEx(openEx === i ? null : i)}>
                    <IconChevron size={13} className={openEx === i ? "rot" : ""} /> {e.nm}
                  </button>
                  <span className="mat-count" title="Material de esta evaluación">📎 {e.files.length}</span>
                  <span className="wt">{e.wt}</span>
                  <span className="dt">{e.done ? "Rendida" : e.dt}</span>
                  <button className="fx" onClick={() => removeExam(e, i)} aria-label="Quitar evaluación"><IconClose size={13} /></button>
                </div>
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
                    {!e.files.length && <div className="ex-mat-empty">Sin material todavía para esta evaluación.</div>}
                    <button className="drop" onClick={() => fileIn.current.click()}><IconSparkle size={15} /> Subir material a {e.nm}</button>
                  </div>
                )}
              </div>
            ))}
            {newEx && (
              <div className="new-ex">
                <input autoFocus placeholder="Nombre de la evaluación" value={newEx.nm} onChange={(e) => setNewEx({ ...newEx, nm: e.target.value })} onKeyDown={(e) => e.key === "Enter" && addExam()} />
                <input placeholder="%" value={newEx.wt} onChange={(e) => setNewEx({ ...newEx, wt: e.target.value })} />
                <input placeholder="dd/mm" value={newEx.dt} onChange={(e) => setNewEx({ ...newEx, dt: e.target.value })} />
                <button className="btn btn-primary btn-sm" onClick={addExam}>Guardar</button>
              </div>
            )}
            <input type="file" accept=".pdf,.docx,.txt" multiple hidden ref={fileIn} onChange={pickFiles} />
          </div>
          <div>
            <h4>Resultado del ramo</h4>
            <div className="grade-row">
              <label>Nota final<input type="number" step="0.01" min="1" max="7" placeholder="5,40" value={grade} onChange={(e) => setGrade(e.target.value)} /></label>
              <label>Nota para aprobar<input type="number" step="0.01" min="1" max="7" value={passing} onChange={(e) => setPassing(e.target.value)} /></label>
              <button className="btn btn-ghost btn-sm" onClick={saveResult}>Guardar resultado</button>
              {resultStatus && <span className="mono">{resultStatus}</span>}
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

function CoursesHead({ onAdd, data = {}, csrf = "" }) {
  const [sem, setSem] = React.useState(data.semester || "VI");
  const [ref, seen] = useReveal({ threshold: 0.2 });
  const chooseSemester = async (value) => {
    if (data.live) {
      const response = await fetch("/api/student/semester/current", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ label: value }) });
      const body = await response.json();
      if (!response.ok) return alert(body.error || "No se pudo cambiar el semestre.");
    }
    setSem(value);
  };
  return (
    <section className={"cr-head rv" + (seen ? " in" : "")} ref={ref}>
      <div>
        <div className="pl-kicker">{data.live ? data.term_label : "2025 · segundo semestre"}</div>
        <h1>Mis cursos</h1>
        <p>Tus ramos llegan solos desde Canvas con la extensión de MachReach. Agrega evaluaciones y notas para que el plan y el ranking sepan qué priorizar.</p>
        <div className="sem">
          <span className="mono">Semestre</span>
          {SEMS.map((s) => <button key={s} className={sem === s ? "on" : ""} onClick={() => chooseSemester(s)}>{s}</button>)}
        </div>
      </div>
      <div className="cr-actions">
        <a href="https://chromewebstore.google.com/detail/djfnmpaihpkibcngaaekhnbalbaibgnk" target="_blank" rel="noopener" className="btn btn-primary"><IconCanvas size={16} /> Sincronizar Canvas</a>
        <button className="btn btn-ghost" onClick={onAdd}>Agregar a mano</button>
      </div>
    </section>
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

function CoursesGrid({ plus, list, onDelete }) {
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

Object.assign(window, { CoursesHead, CoursesStats, CoursesGrid, ExtCard, AddCourseModal, DeleteCourseModal, CRS, SEMS });
