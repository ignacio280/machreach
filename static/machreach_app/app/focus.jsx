/* Focus page sections */
const SCENES = [
  { id: "silence", ic: "🤫", label: "Silencio" },
  { id: "rain", ic: "🌧", label: "Lluvia" },
  { id: "ocean", ic: "🌊", label: "Océano" },
  { id: "forest", ic: "🌲", label: "Bosque" },
  { id: "fire", ic: "🔥", label: "Fuego" },
  { id: "night", ic: "🌙", label: "Noche" },
];
const FX_COURSES = ["Cálculo II", "Termodinámica", "Química Orgánica", "Álgebra Lineal"];
const FX_EXAMS = { "Cálculo II": ["Prueba 2 — 4 ago", "Examen — 12 sep"], "Termodinámica": ["Control 3 — 10 ago"], "Química Orgánica": ["Informe de laboratorio — 13 ago"], "Álgebra Lineal": ["Examen — 26 ago"] };

function pad(n) { return String(n).padStart(2, "0"); }

function FocusHead({ scene, data = {} }) {
  const stats = data.stats || {};
  const live = !!data.live;
  return (
    <section className="fx-head">
      <div>
        <span className="pill"><i className="dot" />{live ? (data.status_label || "Listo para comenzar") : "Sesión en curso · Cálculo II"}</span>
        <h1 style={{ marginTop: 14 }}>Enfoque</h1>
        <p>Mostrando lo que has estudiado <b style={{ color: "var(--ink)" }}>hoy</b> · {live ? (data.today_label || "hoy") : "martes 4 de agosto"}</p>
      </div>
      <div className="fx-head-stats">
        <div className="fx-hs"><div className="n num">{live ? `${Number(stats.total_minutes || 0)}m` : "58m"}</div><div className="l">Hoy</div></div>
        <div className="fx-hs"><div className="n num">{live ? Number(stats.sessions || 0) : 2}</div><div className="l">Sesiones</div></div>
        <div className="fx-hs"><div className="n num">{live ? Number(stats.streak_days || 0) : 17}</div><div className="l">Racha</div></div>
        <div className="fx-hs"><div className="n num">{live ? Number(stats.xp_today || 0) : 215}</div><div className="l">XP hoy</div></div>
      </div>
    </section>
  );
}

function FocusNotes({ data = {} }) {
  if (data.live) {
    const exam = data.next_exam;
    return (
      <div className="fx-strip">
        <div className="fx-note rival">
          <span className="ico-badge" style={{ background: "var(--surface)" }}><IconFire size={17} color="var(--brand)" /></span>
          <div><div className="eye">Tu ritmo de hoy</div><div className="t">{Number(data.stats?.total_minutes || 0)} minutos de enfoque</div><div className="s">Cada bloque terminado se guarda en tu cuenta</div></div>
        </div>
        <div className="fx-note exam">
          <span className="ico-badge" style={{ background: "var(--surface)" }}><IconStar size={17} color="var(--plum)" /></span>
          <div><div className="eye">Próxima prueba</div><div className="t">{exam ? `${exam.name} — ${exam.course}` : "Sin evaluaciones próximas"}</div><div className="s">{exam ? `${exam.date} · ${exam.weight}% de la nota` : "Agrégala en Cursos para enfocar tu sesión"}</div></div>
          {exam && <div className="big num">{exam.days}<small>días</small></div>}
        </div>
      </div>
    );
  }
  return (
    <div className="fx-strip">
      <div className="fx-note rival">
        <span className="ico-badge" style={{ background: "var(--surface)" }}><IconFire size={17} color="var(--brand)" /></span>
        <div>
          <div className="eye">Rival del día</div>
          <div className="t">Camila te lleva 34 minutos</div>
          <div className="s">Un bloque de 35 min y la pasas</div>
        </div>
        <div className="big num">92m<small>de ella</small></div>
      </div>
      <div className="fx-note exam">
        <span className="ico-badge" style={{ background: "var(--surface)" }}><IconStar size={17} color="var(--plum)" /></span>
        <div>
          <div className="eye">Próxima prueba</div>
          <div className="t">Prueba 2 — Cálculo II</div>
          <div className="s">Series de Taylor · Integrales impropias</div>
        </div>
        <div className="big num">3<small>días</small></div>
      </div>
    </div>
  );
}

function Timer({ scene, onRun, onCourse, data = {} }) {
  const MODES = { pomodoro: { work: 25 * 60, brk: 5 * 60, rounds: 4 }, custom: { work: 50 * 60, brk: 10 * 60, rounds: 2 } };
  const [mode, setMode] = React.useState("pomodoro");
  const [phase, setPhase] = React.useState("work");
  const [round, setRound] = React.useState(1);
  const [running, setRunning] = React.useState(false);
  React.useEffect(() => { onRun && onRun(running); }, [running]);
  const cfg = MODES[mode];
  const total = phase === "work" ? cfg.work : cfg.brk;
  const [left, setLeft] = React.useState(cfg.work);
  const courses = data.courses?.length ? data.courses : FX_COURSES.map((name, i) => ({ id: i + 1, name, exams: (FX_EXAMS[name] || []).map((name, j) => ({ id: j + 1, name })) }));
  const [courseId, setCourseId] = React.useState(courses[0]?.id || "");
  const course = courses.find((c) => String(c.id) === String(courseId)) || courses[0];
  const [examId, setExamId] = React.useState("");
  const phaseId = React.useRef("");
  React.useEffect(() => { onCourse && onCourse(course?.id || ""); }, [course?.id]);

  const beginVerifiedPhase = async () => {
    if (!data.live || phase !== "work") return true;
    const id = "focus-" + Date.now() + "-" + Math.random().toString(36).slice(2, 9);
    const response = await fetch("/api/student/focus/phase/start", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ phase_id: id, mode, expected_minutes: Math.round(cfg.work / 60), course_id: course?.id, exam_id: examId || null }) });
    const body = await response.json();
    if (!response.ok) { alert(body.error || "No se pudo iniciar la sesión."); return false; }
    phaseId.current = id;
    return true;
  };
  const saveVerifiedPhase = async () => {
    if (!data.live || !phaseId.current || !course) return;
    try {
      const response = await fetch("/api/student/focus/save", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ phase_id: phaseId.current, mode, minutes: Math.round(cfg.work / 60), pages: 0, course_id: course.id, course_name: course.name, exam_id: examId || null }) });
      if (!response.ok) throw new Error("No se pudo guardar el bloque de enfoque.");
    } catch (error) {
      setRunning(false);
      alert(error.message || "No se pudo guardar el bloque de enfoque.");
    } finally {
      phaseId.current = "";
    }
  };

  React.useEffect(() => { setRunning(false); setPhase("work"); setRound(1); setLeft(MODES[mode].work); }, [mode]);

  React.useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      setLeft((v) => {
        if (v > 1) return v - 1;
        if (phase === "work") { void saveVerifiedPhase(); setPhase("break"); return cfg.brk; }
        setPhase("work"); setRound((r) => (r >= cfg.rounds ? 1 : r + 1)); return cfg.work;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [running, phase, mode]);

  React.useEffect(() => {
    if (running && phase === "work" && !phaseId.current) void beginVerifiedPhase();
  }, [running, phase, round]);

  const pct = 1 - left / total;
  const R = 132, C = 2 * Math.PI * R;
  const [kick, setKick] = React.useState(0);
  const reset = () => { setRunning(false); setPhase("work"); setRound(1); setLeft(cfg.work); };
  const toggle = async () => {
    if (!running && left === cfg.work && phase === "work" && !(await beginVerifiedPhase())) return;
    setRunning((r) => { if (!r) setKick((k) => k + 1); return !r; });
  };
  const ang = (-90 + pct * 360) * Math.PI / 180;
  const hx = 150 + R * Math.cos(ang), hy = 150 + R * Math.sin(ang);

  return (
    <section className={"timer" + (running ? " run" : "")}>
      <div className="fx-fields">
        <div className="fld">
          <label>Curso</label>
          <select value={courseId} onChange={(e) => { setCourseId(e.target.value); setExamId(""); }}>{courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        </div>
        <div className="fld">
          <label>Preparando para</label>
          <select value={examId} onChange={(e) => setExamId(e.target.value)}><option value="">Sin evaluación</option>{(course?.exams || []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
        </div>
      </div>
      <div className="modes">
        <button className={mode === "pomodoro" ? "on" : ""} onClick={() => setMode("pomodoro")}>Pomodoro 25/5</button>
        <button className={mode === "custom" ? "on" : ""} onClick={() => setMode("custom")}>Bloque 50/10</button>
      </div>
      <div className="ring-wrap">
        {kick > 0 && <i className="shock" key={kick} />}
        <svg viewBox="0 0 300 300">
          <circle cx="150" cy="150" r={R} fill="none" stroke="var(--paper-2)" strokeWidth="16" />
          <circle cx="150" cy="150" r={R} fill="none" stroke="var(--ink)" strokeWidth="20" opacity=".12" />
          <circle cx="150" cy="150" r={R} fill="none" stroke={phase === "work" ? "var(--scene)" : "var(--good)"} strokeWidth="16" strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={C * (1 - pct)} transform="rotate(-90 150 150)" style={{ transition: "stroke-dashoffset .8s linear, stroke .4s" }} />
          {running && <circle className="head" cx={hx} cy={hy} r="9" fill={phase === "work" ? "var(--scene)" : "var(--good)"} stroke="var(--ink)" strokeWidth="2.5" />}
        </svg>
        <div className="ring-time" key={kick}>
          <div className="tt">{pad(Math.floor(left / 60))}:{pad(left % 60)}</div>
          <div className="tl">{phase === "work" ? "Bloque de enfoque" : "Descanso"}</div>
        </div>
      </div>
      <div className="orbit">
        {Array.from({ length: cfg.rounds }).map((_, i) => (
          <i key={i} className={i + 1 < round ? "done" : i + 1 === round ? "now" : ""} />
        ))}
      </div>
      <div className="fx-controls">
        <button className={"fx-start btn btn-lg " + (running ? "btn-ghost" : "btn-primary")} onClick={toggle}>
          {running ? <><IconClose size={17} /> Pausar</> : <><IconTimer size={19} /> {left === cfg.work && phase === "work" ? "Empezar" : "Reanudar"}</>}
        </button>
        <button className="btn btn-ghost btn-lg" onClick={reset}>Reiniciar</button>
      </div>
      <div className="fx-status">Ronda {round} de {cfg.rounds} · {course?.name || "Sin curso"} · el tiempo se guarda al terminar el bloque</div>
    </section>
  );
}

function Ambience({ scene, setScene }) {
  const [vol, setVol] = React.useState(45);
  useAmbience(scene);
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconSparkle size={17} /></span>
        <h3>Ambiente</h3>
      </div>
      <div className="scenes">
        {SCENES.map((s) => (
          <button key={s.id} className={"scene" + (scene === s.id ? " on" : "")} onClick={() => setScene(s.id)}><span>{s.ic}</span>{s.label}</button>
        ))}
      </div>
      <div className="vol">
        <span className="mono">Volumen</span>
        <input type="range" min="0" max="100" value={vol} onChange={(e) => setVol(+e.target.value)} disabled={scene === "silence"} />
        <span className="mono num">{scene === "silence" ? "—" : vol + "%"}</span>
      </div>
    </section>
  );
}

function Guard() {
  return (
    <section className="pnl guard-pnl">
      <span className="ico-badge" style={{ background: "#E1F3D6" }}><IconShield size={17} /></span>
      <h3>Focus Guard</h3>
      <span className="tagchip"><i className="live-dot" />Activo</span>
    </section>
  );
}

function Benchmark({ plus, data = {}, courseId }) {
  const [bench, setBench] = React.useState(data.benchmark);
  React.useEffect(() => {
    if (!data.live || !courseId) return;
    let active = true;
    fetch(`/api/student/courses/${courseId}/benchmark`).then((response) => response.json()).then((body) => {
      if (!active) return;
      const myMinutes = Number(body.my_outcome?.total_focus_minutes || 0);
      setBench({ ...body, my_hours: Math.round((myMinutes / 60) * 10) / 10 });
    }).catch(() => { if (active) setBench({ has_data: false, min_required: 5 }); });
    return () => { active = false; };
  }, [data.live, courseId]);
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#EFE7FF" }}><IconChart size={17} /></span>
        <h3>Benchmark del ramo</h3>
        {!plus && <PlusBadge small />}
      </div>
      <PlusGate locked={!plus} minHeight={190}
        title="Compárate con quienes ya aprobaron"
        copy="Plus muestra las horas promedio y la nota final promedio de estudiantes anónimos del mismo ramo, universidad y cohorte.">
        {data.live && !bench?.has_data ? <p style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 700, lineHeight: 1.5 }}>Aún no hay una muestra anónima suficiente. Solo mostramos datos del mismo ramo canónico, universidad y cohorte al reunir al menos {bench?.min_required || 5} resultados.</p> : <React.Fragment>
          <div className="bench">
            <div><div className="n num">{data.live ? `${bench.avg_hours}h` : "41h"}</div><div className="l">Horas promedio</div></div>
            <div><div className="n num">{data.live ? String(bench.avg_final_grade).replace(".", ",") : "5,4"}</div><div className="l">Nota final promedio</div></div>
            <div><div className="n num">{data.live ? `${bench.my_hours || 0}h` : "128h"}</div><div className="l">Tus horas</div></div>
            <div><div className="n num">{data.live ? `${bench.total_reports}` : "+3%"}</div><div className="l">{data.live ? "Resultados anónimos" : "Sobre el promedio"}</div></div>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 12, fontWeight: 700, lineHeight: 1.4 }}>{data.live ? bench.methodology : "Basado en 23 estudiantes anónimos que rindieron Cálculo II en tu universidad."}</p>
        </React.Fragment>}
      </PlusGate>
    </section>
  );
}

Object.assign(window, { FocusHead, FocusNotes, Timer, Ambience, Guard, Benchmark, SCENES });
