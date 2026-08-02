/* Dashboard — left column */
const QUESTS = [
  { ic: "⏱", bg: "#FFE0CB", t: "Estudia 90 minutos hoy", p: 65, tgt: 90, cur: 58, xp: 40 },
  { ic: "🎯", bg: "#E4D9FF", t: "Completa 3 bloques del plan", p: 100, tgt: 3, cur: 3, xp: 25, done: true },
  { ic: "🧠", bg: "#D7EEF7", t: "Responde un quiz de repaso", p: 0, tgt: 1, cur: 0, xp: 30 },
];

const SESSIONS = [
  { time: "08:30", course: "Cálculo II", topic: "Series de Taylor — ejercicios pares", meta: "1.5 h · Práctica", prio: "hi", label: "Alta" },
  { time: "11:00", course: "Termodinámica", topic: "Ciclo de Carnot y entropía", meta: "1 h · Lectura guiada", prio: "mid", label: "Media" },
  { time: "16:00", course: "Química Orgánica", topic: "Flashcards: grupos funcionales", meta: "45 min · Repaso", prio: "mid", label: "Media" },
  { time: "19:30", course: "Álgebra Lineal", topic: "Diagonalización — quiz corto", meta: "30 min · Quiz", prio: "lo", label: "Baja" },
];

const COURSES = [
  { code: "MAT1620", name: "Cálculo II", pct: 62, next: "Prueba 2 en 3 días", urgent: true, bg: "#FFEBD8" },
  { code: "FIS1523", name: "Termodinámica", pct: 45, next: "Control en 9 días", bg: "#E6F3DC" },
  { code: "QIM100E", name: "Química Orgánica", pct: 78, next: "Informe en 12 días", bg: "#E8DEFF" },
  { code: "MAT1203", name: "Álgebra Lineal", pct: 30, next: "Sin evaluaciones", bg: "#DCEFF6" },
];

const EXAMS = [
  { d: "04", m: "ago", tone: "red", t: "Prueba 2 — Cálculo II", meta: "30% de la nota · 4 temas", cd: "3", sub: "días", red: true },
  { d: "10", m: "ago", tone: "yel", t: "Control 3 — Termodinámica", meta: "15% de la nota · 2 temas", cd: "9", sub: "días" },
  { d: "13", m: "ago", tone: "yel", t: "Informe de laboratorio — Química", meta: "20% de la nota · Entrega", cd: "12", sub: "días" },
  { d: "26", m: "ago", tone: "grn", t: "Examen — Álgebra Lineal", meta: "40% de la nota · 6 temas", cd: "25", sub: "días" },
];

function Mission({ data = {} }) {
  const quests = data.quests || QUESTS;
  const [ref, seen] = useReveal({ threshold: 0.15 });
  return (
    <section className={"mission rv" + (seen ? " in" : "")} ref={ref}>
      <div className="mission-top">
        <div>
          <span className="pill"><i className="dot" />{data.eyebrow || "Martes · semana 6 del semestre"}</span>
          <h1 style={{ marginTop: 16 }} dangerouslySetInnerHTML={{ __html: data.headline || "Buenos días, Sofía. Hoy toca <em>Cálculo II</em>." }} />
          <p className="mission-sub">{data.copy || "Tu prueba está en 3 días y llevas 62% del temario. Un bloque de enfoque ahora te deja al día."}</p>
        </div>
        <div className="mission-cta">
          <a href={data.focus_href || "/student/focus"} className="btn btn-primary btn-lg"><IconTimer size={19} /> {data.focus_label || "Empezar enfoque"}</a>
          <a href={data.plan_href || "/student/planner"} className="btn btn-ghost btn-sm">{data.plan_label || "Ver plan completo"}</a>
        </div>
      </div>
      <div className="quests">
        {quests.map((q, i) => (
          <div className={"quest" + (q.done ? " done" : "")} key={i}>
            <div className="quest-h">
              <span className="quest-ic" style={{ background: q.bg }}>{q.ic}</span>
              {q.done ? <span className="quest-rew" style={{ color: "var(--good)" }}>✓ Listo</span> : <span className="quest-rew">+{q.xp} XP</span>}
            </div>
            <div className="quest-t">{q.t}</div>
            <div className="bar"><i style={{ width: (seen ? q.p : 0) + "%" }} /></div>
            <div className="quest-n">{q.cur}/{q.tgt}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function StatCard({ cls, label, value, suffix, sub, deco, d, tilt }) {
  const [ref, seen] = useReveal({ threshold: 0.4 });
  const n = useCountUp(value, seen, 1300);
  return (
    <div className={"stat " + cls + " pop" + (seen ? " in" : "")} ref={ref} style={{ "--d": d + "ms", "--tilt": tilt }}>
      <div className="l">{label}</div>
      <div className="v num">{Math.round(n).toLocaleString("es-CL")}{suffix}</div>
      <div className="s">{sub}</div>
      <div className="deco">{deco}</div>
    </div>
  );
}

function Stats({ items }) {
  if (items && items.length) {
    return <div className="stats">{items.map((s, i) => <StatCard key={i} cls={s.cls || ["a","b","c","d"][i % 4]} label={s.label} value={s.value} suffix={s.suffix || ""} sub={s.sub || ""} deco={s.deco || ""} d={i * 70} tilt={i % 2 ? "1.2deg" : "-1.2deg"} />)}</div>;
  }
  return (
    <div className="stats">
      <StatCard cls="a" label="Total estudiado" value={128} suffix="h" sub="94 sesiones" deco="📚" d={0} tilt="-1.5deg" />
      <StatCard cls="b" label="Mejor día" value={6} suffix="h" sub="Miércoles, últimos 35 d" deco="⚡" d={70} tilt="1.2deg" />
      <StatCard cls="c" label="Tu racha" value={17} suffix="" sub="días seguidos 🔥" deco="🔥" d={140} tilt="-1.2deg" />
      <StatCard cls="d" label="XP de hoy" value={215} suffix="" sub="↑ 85 XP para nivel 12" deco="⚡" d={210} tilt="1.5deg" />
    </div>
  );
}

function TodayPlan({ plus = false, data = {} }) {
  const sessions = data.sessions || SESSIONS;
  const initialDone = {};
  sessions.forEach((s, i) => { if (s.done) initialDone[i] = true; });
  const [done, setDone] = React.useState(initialDone);
  const [saving, setSaving] = React.useState({});
  const [saveError, setSaveError] = React.useState("");
  const [ref, seen] = useReveal({ threshold: 0.12 });
  const n = sessions.length, c = Object.values(done).filter(Boolean).length;
  const toggle = async (i) => {
    if (saving[i]) return;
    const next = !done[i];
    setDone((d) => ({ ...d, [i]: next }));
    setSaveError("");
    if (data.toggle_url) {
      setSaving((s) => ({ ...s, [i]: true }));
      try {
        const response = await fetch(data.toggle_url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": data.csrf || "" }, body: JSON.stringify({ date: data.date, session_index: i, completed: next }) });
        if (!response.ok) throw new Error("plan update failed");
      } catch (_) {
        setDone((d) => ({ ...d, [i]: !next }));
        setSaveError(data.save_error || "No pudimos guardar el cambio. Inténtalo de nuevo.");
      } finally {
        setSaving((s) => ({ ...s, [i]: false }));
      }
    }
  };
  return (
    <section className={"pnl rv" + (seen ? " in" : "")} ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFE0CB" }}><IconCal size={17} /></span>
        <h3>{data.title || "Plan de hoy"}</h3>
        <span className="mono" style={{ marginLeft: 4 }}>{plus ? c + "/" + n + " " + (data.ready_label || "listos") : (data.plus_label || "Solo Plus")}</span>
        <PlusLink locked={!plus} href={data.week_href || "/student/planner"}>{data.week_label || "Ver semana"}</PlusLink>
      </div>
      <PlusGate locked={!plus} minHeight={330}
        title={data.lock_title || "El plan semanal es una herramienta Plus"}
        copy={data.lock_copy || "MachReach ordena tus bloques usando ponderaciones, fechas de prueba, material adjunto y tus sesiones reales. Sin Plus puedes estudiar libre desde Enfoque."}>
        {sessions.length ? sessions.map((s, i) => (
          <div className={"sess" + (done[i] ? " done" : "")} key={i}>
            <button className="sess-check" onClick={() => toggle(i)} disabled={!!saving[i]} aria-label={data.check_label || "Marcar como hecho"}><IconCheck size={14} /></button>
            <div className="sess-time">{s.time}</div>
            <div className="sess-b">
              <div className="sess-c">{s.course}</div>
              <div className="sess-t">{s.topic}</div>
              <div className="sess-m">{s.meta}</div>
            </div>
            <span className={"prio " + s.prio}>{s.label}</span>
          </div>
        )) : <div className="dash-empty">{data.empty || "Aún no hay sesiones para hoy."}</div>}
        {saveError && <div className="dash-save-error" role="alert">{saveError}</div>}
      </PlusGate>
    </section>
  );
}

function MyCourses({ data = {} }) {
  const courses = data.items || COURSES;
  const [ref, seen] = useReveal({ threshold: 0.15 });
  return (
    <section className={"pnl rv" + (seen ? " in" : "")} ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#E8DEFF" }}><IconBook size={17} /></span>
        <h3>{data.title || "Mis cursos"}</h3>
        <span className="mono">{data.semester || "2025 · 2º semestre"}</span>
        <a href={data.href || "/student/courses"} className="lnk">{data.all_label || "Ver todos"} <IconArrow size={14} /></a>
      </div>
      <div className="courses">
        {courses.length ? courses.map((c, i) => (
          <a href={c.href || data.href || "/student/courses"} className="course pop" key={i} style={{ background: c.bg, "--d": i * 60 + "ms" }}>
            <span className="ring"><Ring pct={seen ? c.pct : 0} size={34} sw={5} label={c.pct + ""} /></span>
            <div className="code">{c.code}</div>
            <div className="nm">{c.name}</div>
            <div className={"nx" + (c.urgent ? " urgent" : "")}>{c.next}</div>
          </a>
        )) : <div className="dash-empty">{data.empty || "No tienes cursos todavía."}</div>}
      </div>
    </section>
  );
}

function Exams({ data = {} }) {
  const exams = data.items || EXAMS;
  const [ref, seen] = useReveal({ threshold: 0.12 });
  return (
    <section className={"pnl rv" + (seen ? " in" : "")} ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFDFE1" }}><IconStar size={17} /></span>
        <h3>{data.title || "Próximas evaluaciones"}</h3>
        <a href={data.href || "/student/courses"} className="lnk">{data.add_label || "Agregar"} <IconArrow size={14} /></a>
      </div>
      <div>
        {exams.length ? exams.map((e, i) => (
          <div className="exam" key={i}>
            <div className={"exam-day " + e.tone}><div className="d">{e.d}</div><div className="m">{e.m}</div></div>
            <div className="exam-b">
              <div className="exam-t">{e.t}</div>
              <div className="exam-m">{e.meta}</div>
            </div>
            <div className={"cd" + (e.red ? " red" : "")}>{e.cd}<small>{e.sub}</small></div>
          </div>
        )) : <div className="dash-empty">{data.empty || "Sin pruebas próximas."}</div>}
      </div>
    </section>
  );
}

Object.assign(window, { Mission, Stats, TodayPlan, MyCourses, Exams });
