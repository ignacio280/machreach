/* Analíticas page (Plus) */
const DAY14 = [
  ["L", 45], ["M", 90], ["M", 0], ["J", 120], ["V", 60], ["S", 150], ["D", 30],
  ["L", 105], ["M", 75], ["M", 135], ["J", 90], ["V", 45], ["S", 165], ["D", 58],
];
const COURSE_TIME = [
  { n: "Cálculo II", m: 2530, c: "#FF6A2B" },
  { n: "Química Orgánica", m: 1880, c: "#6E4CD8" },
  { n: "Álgebra Lineal", m: 1670, c: "#2FA8C6" },
  { n: "Termodinámica", m: 1605, c: "#6FB03A" },
  { n: "Intro. a la Programación", m: 845, c: "#D2528B" },
];
const XP_WEEKS = [["S-5", 1240], ["S-4", 1610], ["S-3", 980], ["S-2", 1875], ["S-1", 2140], ["Ahora", 1480]];
const HEAT35 = [0, 2, 3, 1, 4, 0, 2, 3, 3, 4, 2, 1, 0, 3, 4, 4, 2, 3, 3, 0, 1, 2, 4, 3, 4, 4, 2, 3, 3, 4, -1, -1, -1, -1, -1];
const hm = (m) => Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";

function useLiveAnalytics(live, initial = null) {
  const [data, setData] = React.useState(initial || null);
  React.useEffect(() => {
    if (!live) return;
    Promise.all([
      fetch("/api/academic/analytics").then((r) => r.json()),
      fetch("/api/academic/ranks?period=all").then((r) => r.json()),
    ]).then(([analytics, ranks]) => setData({ ...analytics, rank_info: ranks })).catch(() => setData({}));
  }, [live]);
  return data;
}

function AnHero({ data = null }) {
  const totals = data?.totals || { hours: 142, sessions: 106, minutes: 5088, streak: 17 };
  const average = totals.sessions ? Math.round((totals.minutes || 0) / totals.sessions) : 0;
  const topCourse = data?.top_courses?.[0]?.course || "—";
  const rank = data?.rank_info?.ranks?.country || data?.rank_info?.ranks?.global || null;
  const activeDays = (data?.last_14_days || []).filter((day) => Number(day.minutes || 0) > 0).length;
  const bestDay = data?.best_dow?.day || "—";
  return (
    <div className="an-hero">
      <section className="an-title">
        <div className="pl-kicker">Analytics de estudio</div>
        <h1>Tu rendimiento, sin humo.</h1>
        <p>Minutos de enfoque, XP, cursos dominantes y consistencia real. Esto es para ver si estás estudiando de verdad o solo abriendo la app.</p>
        <div className="an-quick">
          <div><b className="num">{totals.hours || 0}h</b><span>Tiempo total</span></div>
          <div><b className="num">{totals.sessions || 0}</b><span>Sesiones</span></div>
          <div><b className="num">{average}m</b><span>Promedio</span></div>
          <div><b className="num">{totals.streak || 0}</b><span>Racha 🔥</span></div>
        </div>
      </section>
      <aside className="an-rank">
        <div className="e">Tu posición</div>
        <div className="n num">{rank?.rank ? `${rank.rank}º` : "—"}</div>
        <div className="s">{rank?.total ? `de ${rank.total} en la liga de tu país` : "Aún sin posición"}</div>
        <div className="b">{rank?.rank && rank?.total ? `Estás en el ${Math.max(1, Math.round((rank.rank / rank.total) * 100))}% superior` : "Gana XP para entrar al ranking"}</div>
        <div className="an-ins-mini">
          <div><span>Curso fuerte</span><strong>{topCourse}</strong></div>
          <div><span>Mejor día</span><strong>{bestDay}</strong></div>
          <div><span>Consistencia</span><strong>{activeDays}/14 días</strong></div>
        </div>
      </aside>
    </div>
  );
}

const AN_VIEWS = [
  { id: "trend", ic: "⏱", label: "Enfoque", sub: "Minutos estudiados durante los últimos 14 días." },
  { id: "course", ic: "📘", label: "Por curso", sub: "Dónde se está yendo tu energía." },
  { id: "xp", ic: "⚡", label: "Ritmo de XP", sub: "Últimas ganancias registradas, por semana." },
  { id: "heat", ic: "🔥", label: "Constancia", sub: "Últimos 35 días. Más intenso significa más minutos." },
];

function TrendChart({ rows = DAY14 }) {
  const max = Math.max(1, ...rows.map((d) => d[1]));
  return (
    <React.Fragment>
      <div className="an-days">
        {rows.map(([d, m], i) => (
          <div className="an-day" key={i}>
            <span className="v">{m || ""}</span>
            <div className={"an-bar" + (m === 0 ? " dim" : "")} style={{ height: Math.max(4, (m / max) * 100) + "%", "--bd2": i * 45 + "ms" }} />
            <span className="d">{d}</span>
          </div>
        ))}
      </div>
      <div className="an-days-foot" />
    </React.Fragment>
  );
}

function CourseChart({ courses = COURSE_TIME }) {
  const max = Math.max(1, ...courses.map((c) => c.m));
  return courses.map((c, i) => (
    <div className="cbar" key={c.n}>
      <div className="cbar-h"><b>{c.n}</b><span className="mono">{hm(c.m)}</span></div>
      <div className="cbar-t"><i style={{ width: (c.m / max) * 100 + "%", background: c.c, "--bd2": i * 90 + "ms" }} /></div>
    </div>
  ));
}

function XpChart({ rows = XP_WEEKS }) {
  const max = Math.max(1, ...rows.map((x) => x[1]));
  return (
    <React.Fragment>
      <div className="xp-row">
        {rows.map(([l, v], i) => (
          <div className="xp-col" key={l}>
            <span className="v num">{v.toLocaleString("es-CL")}</span>
            <div className="xp-b" style={{ height: (v / max) * 100 + "%", "--bd2": i * 70 + "ms" }} />
          </div>
        ))}
      </div>
      <div className="xp-labels">{rows.map(([l]) => <span key={l}>{l}</span>)}</div>
    </React.Fragment>
  );
}

function HeatChart({ heat = HEAT35, minutes = [] }) {
  const active = minutes.filter((v) => v > 0).length;
  const attendance = minutes.length ? Math.round((active / minutes.length) * 100) : 0;
  const best = minutes.length ? Math.max(...minutes) : 0;
  return (
    <div className="an-heat-wrap">
      <div className="an-heat">
        {heat.map((v, i) => (
          <div key={i} className={"hc " + (v < 0 ? "future" : "l" + v) + (i === 29 ? " today" : "")}
            style={{ animation: "hcIn .4s var(--ease) both", animationDelay: i * 16 + "ms" }} title={v < 0 ? "" : v * 35 + " min"} />
        ))}
      </div>
      <div className="wk">{["L", "M", "M", "J", "V", "S", "D"].map((d, i) => <span key={i}>{d}</span>)}</div>
      <div className="srow">
        <div><div className="srn num">{active}</div><div className="srl">Días activos</div></div>
        <div><div className="srn num">{attendance}%</div><div className="srl">Asistencia</div></div>
        <div><div className="srn num">{hm(best)}</div><div className="srl">Mejor día</div></div>
      </div>
    </div>
  );
}

function AnBoard({ data = null }) {
  const courses = data?.top_courses?.map((c, i) => ({ n: c.course, m: Number(c.minutes || 0), c: ["#FF6A2B", "#6E4CD8", "#2FA8C6", "#6FB03A", "#D2528B"][i % 5], sessions: Number(data?.course_sessions?.[c.course] || 0) })) || COURSE_TIME;
  const days = data?.last_14_days?.map((d) => [String(d.label || "").slice(0, 1), Number(d.minutes || 0)]) || DAY14;
  const heat = data?.last_14_days ? Array(21).fill(0).concat(data.last_14_days.map((d) => Math.min(4, Math.ceil(Number(d.minutes || 0) / 35)))) : HEAT35;
  const xpRows = data?.xp_weeks?.map((row) => [row.label, Number(row.xp || 0)]) || XP_WEEKS;
  const heatMinutes = data?.last_14_days?.map((d) => Number(d.minutes || 0)) || [];
  const [view, setView] = React.useState("trend");
  const v = AN_VIEWS.find((x) => x.id === view);
  const total = courses.reduce((a, c) => a + c.m, 0);
  return (
    <section className="pnl an-board">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFE0CB" }}><IconChart size={17} /></span>
        <h3>Rendimiento</h3>
        <div className="an-tabs">
          {AN_VIEWS.map((t) => (
            <button key={t.id} className={view === t.id ? "on" : ""} onClick={() => setView(t.id)}><span>{t.ic}</span>{t.label}</button>
          ))}
        </div>
      </div>
      <p className="an-sub">{v.sub}</p>
      <div className="an-stage" key={view}>{view === "trend" ? <TrendChart rows={days} /> : view === "course" ? <CourseChart courses={courses} /> : view === "xp" ? <XpChart rows={xpRows} /> : <HeatChart heat={heat} minutes={heatMinutes} />}</div>
      <div className="an-tablehead">
        <h4>Detalle por curso</h4>
        <span className="mono">{hm(total)} en total</span>
      </div>
      <table className="an-table">
        <thead><tr><th>Curso</th><th>Sesiones</th><th>Promedio</th><th style={{ textAlign: "right" }}>Total</th></tr></thead>
        <tbody>
          {courses.map((c, i) => (
            <tr key={c.n}>
              <td><span className="an-swatch" style={{ background: c.c }} />{c.n}</td>
              <td className="num">{c.sessions || "—"}</td>
              <td className="num">{c.sessions ? Math.round(c.m / c.sessions) + "m" : "—"}</td>
              <td className="num">{hm(c.m)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function AnalyticsLocked() {
  return (
    <section className="pl-locked">
      <div className="pl-lock-card">
        <span className="pl-lock-ic"><IconLock size={28} /></span>
        <div className="pl-kicker">Plus</div>
        <h1>Tu rendimiento, sin humo.</h1>
        <p>Minutos de enfoque, XP, cursos dominantes y consistencia real. Las analíticas completas son una herramienta Plus.</p>
        <div className="pl-lock-list">
          {["Tendencia de 14 días", "Tiempo por curso", "Ritmo de XP", "Mapa de constancia", "Detalle por curso"].map((s) => <span key={s}>{s}</span>)}
        </div>
        <a href="/student/shop" className="btn btn-primary btn-lg">Desbloquear analíticas</a>
      </div>
    </section>
  );
}

Object.assign(window, { AnHero, AnBoard, AnalyticsLocked, useLiveAnalytics });
