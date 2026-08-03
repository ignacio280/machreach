/* Plan page sections */
const DAYS = [
  { n: "Lun", dt: "4 ago", today: true },
  { n: "Mar", dt: "5 ago" },
  { n: "Mié", dt: "6 ago" },
  { n: "Jue", dt: "7 ago" },
  { n: "Vie", dt: "8 ago" },
  { n: "Sáb", dt: "9 ago" },
  { n: "Dom", dt: "10 ago", off: true },
];
const CLR = { "Cálculo II": "#FF6A2B", "Termodinámica": "#6FB03A", "Química Orgánica": "#6E4CD8", "Álgebra Lineal": "#2FA8C6" };
const BLOCKS = [
  [{ ph: "Practicar", cs: "Cálculo II", tp: "Series de Taylor — ejercicios pares", mn: "90 min", mat: true }, { ph: "Aprender", cs: "Termodinámica", tp: "Ciclo de Carnot", mn: "60 min" }],
  [{ ph: "Repasar", cs: "Cálculo II", tp: "Integrales impropias", mn: "60 min", mat: true }, { ph: "Practicar", cs: "Química Orgánica", tp: "Grupos funcionales", mn: "45 min" }],
  [{ ph: "Practicar", cs: "Cálculo II", tp: "Guía de la prueba 2", mn: "90 min", mat: true }],
  [{ ph: "Repasar", cs: "Cálculo II", tp: "Simulacro cronometrado", mn: "90 min" }, { ph: "Aprender", cs: "Álgebra Lineal", tp: "Diagonalización", mn: "45 min" }],
  [{ ph: "Aprender", cs: "Termodinámica", tp: "Entropía y segunda ley", mn: "60 min" }],
  [{ ph: "Practicar", cs: "Química Orgánica", tp: "Informe de laboratorio", mn: "120 min", mat: true }, { ph: "Repasar", cs: "Álgebra Lineal", tp: "Flashcards del capítulo 4", mn: "40 min" }],
  [],
];
const WHY = [
  ["1", "Prueba 2 de Cálculo II en 3 días", "Pesa 30% de la nota y llevas 62% del temario — se lleva la mitad de la semana."],
  ["2", "Química va después, no antes", "El informe entrega en 12 días y ya tienes el material subido; basta con el bloque del sábado."],
  ["3", "Dificultad real, no la declarada", "Las reviews anónimas ponen Cálculo II en 4,3/5 — más alta que la que marcaste tú."],
  ["4", "Bloques con material adjunto", "Los bloques con 📎 leen tus PDFs para armar ejercicios y flashcards del tema exacto."],
  ["5", "Domingo libre por decisión tuya", "Lo marcaste como día libre, así que sus horas se repartieron en jueves y sábado."],
];
const RISK = [
  { n: "Prueba 2 — Cálculo II", pct: 78, tone: "#E8505B", l: "Alto" },
  { n: "Control 3 — Termodinámica", pct: 44, tone: "#F4B740", l: "Medio" },
  { n: "Informe — Química Orgánica", pct: 26, tone: "#1FB584", l: "Bajo" },
  { n: "Examen — Álgebra Lineal", pct: 18, tone: "#1FB584", l: "Bajo" },
];

function PlanHero({ data = null }) {
  const [ref, seen] = useReveal({ threshold: 0.15 });
  const next = data?.days?.flatMap((d) => d.blocks || [])[0];
  return (
    <section className={"pl-hero rv" + (seen ? " in" : "")} ref={ref}>
      <div>
        <div className="pl-kicker">Planificación de estudio</div>
        <h1>Tu semana, ordenada.</h1>
        <p>Define cuánto puedes estudiar cada día. MachReach reparte tus bloques según pruebas, fechas, ponderaciones y dificultad del ramo.</p>
      </div>
      <div className="pl-next">
        <div className="l">Próxima acción</div>
        <div className="t">{next ? `${next.minutes || 0} min de ${next.course || "estudio"} — ${next.title || next.phase || "Siguiente bloque"}` : "Aún no tienes bloques guardados"}</div>
        <a href="/student/focus" className="btn btn-primary btn-sm"><IconTimer size={15} /> Empezar en Enfoque</a>
      </div>
    </section>
  );
}

function Availability({ data = null, csrf = "", onPlan }) {
  const initial = data?.availability || [];
  const [hrs, setHrs] = React.useState(() => DAYS.map((_, i) => Number(initial[i]?.available_hours ?? [2.5,2,2.5,3,1.5,3,0][i])));
  const [free, setFree] = React.useState(() => DAYS.map((_, i) => Boolean(initial[i]?.is_free_day ?? (i === 6))));
  const [status, setStatus] = React.useState("");
  const bump = (i, d) => setHrs((h) => h.map((v, j) => (j === i ? Math.max(0, Math.min(12, +(v + d).toFixed(1))) : v)));
  const total = hrs.reduce((a, b, i) => a + (free[i] ? 0 : b), 0);
  const save = async () => {
    if (!data?.live) return;
    setStatus("Guardando…");
    try {
      const response = await fetch("/api/student/planner/availability", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ settings: DAYS.map((_, i) => ({ day_of_week: i, available_hours: hrs[i], is_free_day: free[i] })) }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "No se pudo guardar");
      setStatus("Generando plan…");
      const planResponse = await fetch("/api/student/planner/regenerate", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: "{}" });
      const planBody = await planResponse.json();
      if (!planResponse.ok) throw new Error(planBody.error || "No se pudo generar el plan");
      onPlan && onPlan(planBody.plan);
      setStatus("Plan actualizado");
    } catch (error) { setStatus(error.message || "No se pudo guardar"); }
  };
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFE0CB" }}><IconCal size={17} /></span>
        <h3>Horas de esta semana</h3>
        <span className="mono">{total.toFixed(1)} h disponibles</span>
        {status && <span className="mono">{status}</span>}
        <button className="btn btn-ink btn-sm" onClick={save}><IconSparkle size={14} /> Regenerar plan</button>
      </div>
      <div className="pl-avail">
        {DAYS.map((d, i) => (
          <div className={"av" + (free[i] ? " free" : "")} key={i}>
            <div className="d">{d.n}</div>
            <div className="h num">{free[i] ? "—" : hrs[i] + "h"}</div>
            <div className="av-step">
              <button onClick={() => bump(i, -0.5)} disabled={free[i]} aria-label="Menos horas">−</button>
              <button onClick={() => bump(i, 0.5)} disabled={free[i]} aria-label="Más horas">+</button>
            </div>
            <button className="av-free" onClick={() => setFree((f) => f.map((v, j) => (j === i ? !v : v)))}>{free[i] ? "Activar" : "Día libre"}</button>
          </div>
        ))}
      </div>
    </section>
  );
}

function WeekGrid({ data = null, csrf = "" }) {
  const liveDays = data?.days || [];
  const dayDefs = liveDays.length ? liveDays.map((d, i) => ({ n: DAYS[i]?.n || "Día", dt: String(d.date || "").slice(5), off: !(d.blocks || []).length })) : DAYS;
  const blocks = liveDays.length ? liveDays.map((d) => (d.blocks || []).map((b) => ({ ph: b.phase || "Estudiar", cs: b.course || "Curso", tp: b.title || b.copy || "Bloque de estudio", mn: `${Number(b.minutes || 0)} min`, mat: !!b.material_based, why: b.why || "", date: d.date, examId: Number(b.exam_id || 0), unitIndex: b.material_based ? Number(b.unit_index ?? -1) : -1, minutes: Number(b.minutes || 0), doneTitle: [b.title || "", b.phase || "", b.index == null ? 0 : b.index, b.minutes || 0, b.material_based ? `unit:${b.unit_index == null ? -1 : b.unit_index}` : "general"].join("::") }))) : (data?.live ? DAYS.map(() => []) : BLOCKS);
  const doneKey = (b) => `${b.date || ""}|${b.examId || 0}|${b.unitIndex ?? -1}|${b.doneTitle || b.tp || ""}`;
  const [done, setDone] = React.useState(() => {
    if (!data?.live) return { "0-1": true };
    const out = {};
    (data.done || []).forEach((d) => { out[`${d.block_date || ""}|${Number(d.exam_id || 0)}|${Number(d.unit_index ?? -1)}|${d.title || ""}`] = true; });
    return out;
  });
  React.useEffect(() => {
    if (!data?.live) return;
    const out = {};
    (data.done || []).forEach((d) => { out[`${d.block_date || ""}|${Number(d.exam_id || 0)}|${Number(d.unit_index ?? -1)}|${d.title || ""}`] = true; });
    setDone(out);
  }, [data?.week_start]);
  const [saving, setSaving] = React.useState("");
  const toggleBlock = async (b, fallbackKey) => {
    const k = data?.live ? doneKey(b) : fallbackKey;
    const next = !done[k];
    if (!data?.live) { setDone((s) => ({ ...s, [k]: next })); return; }
    setSaving(k);
    try {
      const response = await fetch("/api/student/planner/block-done", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ date: b.date, exam_id: b.examId, unit_index: b.unitIndex, title: b.doneTitle || b.tp, phase: b.ph, minutes: b.minutes, done: next }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "No se pudo guardar");
      setDone((s) => ({ ...s, [k]: next }));
    } catch (error) { alert(error.message || "No se pudo guardar el bloque"); }
    finally { setSaving(""); }
  };
  const [ref, seen] = useReveal({ threshold: 0.08 });
  const total = blocks.flat().length, c = Object.values(done).filter(Boolean).length;
  return (
    <section className={"pnl rv" + (seen ? " in" : "")} ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#E8DEFF" }}><IconGrid size={17} /></span>
        <h3>{data?.week_start ? `Semana desde ${data.week_start}` : "Plan semanal"}</h3>
        <span className="mono">{c}/{total} bloques listos</span>
        {data?.live ? <span className="lnk" aria-disabled="true">Semana siguiente <IconArrow size={14} /></span> : <a href="#" className="lnk">Semana siguiente <IconArrow size={14} /></a>}
      </div>
      <div className="week">
        {dayDefs.map((d, i) => (
          <div className={"day" + (d.today ? " today" : "") + (d.off ? " off" : "")} key={i}>
            <div className="day-h">
              <span className="n">{d.n}</span>
              <span className="dt">{d.dt}</span>
              <span className="hrs">{d.off ? "libre" : (blocks[i].reduce((a, b) => a + parseInt(b.mn), 0) / 60).toFixed(1) + "h"}</span>
            </div>
            {blocks[i].length === 0 ? (
              <div className="day-empty">{data?.live ? "Sin bloques guardados" : "Día libre — descansa"}</div>
            ) : blocks[i].map((b, j) => {
              const k = data?.live ? doneKey(b) : i + "-" + j;
              return (
                <button className={"blk" + (done[k] ? " done" : "") + (b.mat ? " mat" : "")} key={j}
                  disabled={saving === k} style={{ borderLeftColor: CLR[b.cs] }} onClick={() => toggleBlock(b, k)}
                  title={b.why || undefined}>
                  <span className="tick2"><IconCheck size={11} /></span>
                  <div className="ph">{b.ph}</div>
                  <div className="cs">{b.cs}</div>
                  <div className="tp">{b.tp}</div>
                  {b.why && <div className="wy">{b.why}</div>}
                  <div className="mn">{b.mn}</div>
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

const DIFF_LABEL = ["", "muy fácil", "fácil", "media", "difícil", "muy difícil"];

function WhyPanel({ data = null }) {
  // With an AI plan each evaluation carries the difficulty the model read out
  // of the uploaded material, so the reason can say more than date + weight.
  const rows = data?.live ? (data.exams || []).slice(0, 5).map((exam, i) => [
    String(i + 1),
    `${exam.name} — ${exam.course}`,
    exam.difficulty
      ? `${exam.weight || 0}% de la nota · ${exam.date || "sin fecha"} · dificultad ${DIFF_LABEL[exam.difficulty] || exam.difficulty}. ${exam.difficulty_reason || ""}`.trim()
      : `${exam.weight || 0}% de la nota · fecha ${exam.date || "sin fecha"}.`,
  ]) : WHY;
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconBrain size={17} /></span>
        <h3>Por qué este orden</h3>
      </div>
      <div className="why">
        {(rows.length ? rows : [["1", "Sin evaluaciones próximas", "Agrega fechas y ponderaciones en Cursos para recibir prioridades reales."]]).map(([n, t, s]) => (
          <div className="why-row" key={n}>
            <span className="why-n">{n}</span>
            <div><div className="why-t">{t}</div><div className="why-s">{s}</div></div>
          </div>
        ))}
      </div>
    </section>
  );
}

function RiskPanel({ data = null }) {
  const [ref, seen] = useReveal({ threshold: 0.25 });
  const rows = data?.live ? (data.exams || []).slice(0, 5).map((exam) => {
    const days = exam.date ? Math.max(0, Math.ceil((new Date(exam.date + "T12:00:00") - new Date()) / 86400000)) : 30;
    const pct = Math.max(8, Math.min(95, Math.round((Number(exam.weight || 0) * 1.2) + Math.max(0, 35 - days * 3))));
    return { n: `${exam.name} — ${exam.course}`, pct, tone: pct >= 70 ? "#E8505B" : pct >= 40 ? "#F4B740" : "#1FB584", l: pct >= 70 ? "Alto" : pct >= 40 ? "Medio" : "Bajo" };
  }) : RISK;
  return (
    <section className="pnl" ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFDFE1" }}><IconChart size={17} /></span>
        <h3>Riesgo por evaluación</h3>
      </div>
      {(rows.length ? rows : [{ n: "Sin evaluaciones próximas", pct: 0, tone: "#1FB584", l: "Sin datos" }]).map((r) => (
        <div className="risk" key={r.n}>
          <div className="risk-h"><b>{r.n}</b><span className="mono">{r.l}</span></div>
          <div className="risk-bar"><i style={{ width: (seen ? r.pct : 0) + "%", background: r.tone }} /></div>
        </div>
      ))}
      <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 14, fontWeight: 700, lineHeight: 1.4 }}>
        Calculado con ponderación, días restantes, tus minutos de enfoque y los puntajes de quiz por prueba.
      </p>
    </section>
  );
}

function PlanLocked() {
  return (
    <section className="pl-locked">
      <div className="pl-lock-card">
        <span className="pl-lock-ic"><IconLock size={28} /></span>
        <div className="pl-kicker">Plus</div>
        <h1>Plan inteligente.</h1>
        <p>El plan semanal usa ponderaciones, riesgo, material adjunto, sesiones reales y reviews de dificultad para decidir qué estudiar. Es una herramienta Plus.</p>
        <div className="pl-lock-list">
          {["Ponderaciones", "Riesgo por prueba", "Material adjunto", "Sesiones reales", "Reviews de dificultad"].map((s) => <span key={s}>{s}</span>)}
        </div>
        <a href="/student/shop" className="btn btn-primary btn-lg">Desbloquear Plan</a>
      </div>
    </section>
  );
}

Object.assign(window, { PlanHero, Availability, WeekGrid, WhyPanel, RiskPanel, PlanLocked });
