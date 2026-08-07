/* Ranking page */
const SCOPES = [
  // `h` is only the heading of last resort. On a live page the server sends
  // the real name of the group being ranked (scope_label); these strings are
  // what the design preview shows, and what a student sees when they have not
  // set a carrera or universidad yet. None of them may name a specific one:
  // this list used to say "Ingeniería Civil", which every student saw over a
  // board that was correctly filtered to their own degree.
  { id: "country", ic: "🏳️", label: "País", eye: "Liga por país", h: "Tu país", sub: "Todos los estudiantes de MachReach en tu país", arena: "#4A5FC1" },
  { id: "university", ic: "🎓", label: "Universidad", eye: "Liga universitaria", h: "Tu universidad", sub: "Solo estudiantes de tu casa de estudios", arena: "#2E7F8F" },
  { id: "major", ic: "📚", label: "Carrera", eye: "Liga por carrera", h: "Tu carrera", sub: "Compites contra tu misma carrera, en cualquier universidad", arena: "#6B4AA8" },
  { id: "friends", ic: "🤝", label: "Amigos", eye: "Liga de amigos", h: "Tus amigos", sub: "Solo la gente que aceptaste como amigo", arena: "#B45A3C" },
  { id: "retirement", ic: "🏖️", label: "Egresados", eye: "Liga de egresados", h: "Egresados", sub: "Quienes ya terminaron la carrera — sin premios, pura gloria", arena: "#3F7A55" },
  { id: "global", ic: "🌎", label: "Global", eye: "Liga global", h: "Global", sub: "Disponible cuando MachReach abra en más países", arena: "#4A5FC1", off: true },
];
const PERIODS = [{ id: "all", l: "Histórico" }, { id: "week", l: "Semana" }, { id: "month", l: "Mes" }];
const PRIZES = { global: [500, 300, 200, 100, 50], country: [300, 200, 100, 60, 30], university: [150, 100, 60, 40, 20], major: [80, 50, 30, 20, 10] };

const NAMES = [
  ["Matías Fuentes", "U. de Chile · Ing. Civil", 9840],
  ["Antonia Salas", "PUC · Ing. Civil", 9120],
  ["Benjamín Cortés", "USM · Ing. Civil", 8760],
  ["Isidora Muñoz", "UdeC · Ing. Civil", 8210],
  ["Joaquín Herrera", "PUC · Ing. Comercial", 7890],
  ["Camila Rojas", "PUC · Ing. Civil", 4820],
  ["Tomás Vidal", "PUC · Ing. Civil", 4315],
  ["Sofía Peña", "PUC · Ing. Civil", 4180, true],
  ["Ignacio Soto", "U. de Chile · Ing. Civil", 3970],
  ["Valentina Cruz", "USM · Ing. Civil", 3640],
];
const initials2 = (n) => { const p = n.split(" "); return (p[0][0] + (p[1] ? p[1][0] : "")).toUpperCase(); };
const fmt = (n) => n.toLocaleString("es-CL");

function useRankRows(scope, period, live) {
  const [state, setState] = React.useState({ rows: live ? [] : NAMES, loading: !!live, label: "" });
  React.useEffect(() => {
    if (!live) { setState({ rows: NAMES, loading: false, label: "" }); return; }
    let active = true;
    setState((s) => ({ ...s, loading: true }));
    fetch("/api/academic/leaderboard?scope=" + encodeURIComponent(scope.id) + "&period=" + encodeURIComponent(period))
      .then((r) => r.json()).then((body) => {
        if (!active) return;
        // The subtitle answers the question the board is scoped by: on País
        // that is which university someone studies at, on Universidad which
        // carrera. Students who hid it fall back to their league.
        const detailFor = (r) => (scope.id === "country" ? r.university : scope.id === "university" ? r.major : "")
          || r.league_name || scope.label;
        const rows = (body.rows || []).map((r) => [r.name || "Estudiante", detailFor(r), Number(r.xp || 0), !!r.is_you, r]);
        // Empty for the boards that are not personal, and for a student who
        // has not set a carrera — Arena falls back to the generic heading.
        setState({ rows, loading: false, label: String(body.scope_label || "") });
      }).catch(() => active && setState({ rows: [], loading: false, label: "" }));
    return () => { active = false; };
  }, [scope.id, period, live]);
  return state;
}

function prizeFor(scope, rank, period) {
  if (period !== "week" && period !== "month") return 0;
  const base = (PRIZES[scope] || [])[rank - 1] || 0;
  if (!base) return 0;
  return period === "week" ? Math.floor(base / 2) : base;
}

function RankHead({ scope, setScope, period, setPeriod }) {
  return (
    <section className="rk-head">
      <div>
        <div className="pl-kicker">Ranking</div>
        <h1>Estudiar rankea.</h1>
        <p>El XP que ganas en Enfoque te sube en cinco ligas a la vez. Al cierre de cada semana y cada mes, los cinco primeros de cada liga se llevan monedas.</p>
        <div className="rk-tabs" style={{ marginTop: 16 }}>
          {SCOPES.map((s) => (
            <button key={s.id} className={"rk-tab" + (scope.id === s.id ? " on" : "")} disabled={s.off} onClick={() => !s.off && setScope(s)}>
              <span>{s.ic}</span>{s.label}
            </button>
          ))}
        </div>
      </div>
      <div className="rk-periods">
        {PERIODS.map((p) => <button key={p.id} className={period === p.id ? "on" : ""} onClick={() => setPeriod(p.id)}>{p.l}</button>)}
      </div>
    </section>
  );
}

function CountXP({ v, d = 0 }) {
  const [on, setOn] = React.useState(false);
  React.useEffect(() => { const t = setTimeout(() => setOn(true), d + 200); return () => clearTimeout(t); }, []);
  const n = useCountUp(v, on, 900);
  return <React.Fragment>{fmt(Math.round(on ? n : 0))}</React.Fragment>;
}

function Arena({ scope, period, rows = NAMES, loading = false, heading = "" }) {
  const top = rows.slice(0, 3);
  const medals = ["🥇", "🥈", "🥉"];
  const label = period === "week" ? "Cierra el domingo a medianoche" : period === "month" ? "Cierra el último día del mes" : "Sin cierre — es el acumulado de siempre";
  return (
    <section className="arena" style={{ "--arena": scope.arena }}>
      <div className="arena-sweep" />
      <div className="arena-eye">{scope.eye}</div>
      <h2>{heading || scope.h}</h2>
      <div className="arena-sub">{scope.sub}</div>
      <div className="arena-close"><IconTimer size={13} /> {label}</div>
      <div className="podium">
        {!loading && top.length === 0 && <div className="fr-empty">Todavía no hay XP en esta liga.</div>}
        {top.map(([n, u, xp, , raw], i) => {
          const pz = prizeFor(scope.id, i + 1, period);
          return (
            <div className={"pod p" + (i + 1)} key={n} style={{ "--pd": [180, 0, 340][i] + "ms" }}>
              <div className="pod-medal">{medals[i]}</div>
              <div className="pod-av" style={{ background: raw?.avatar_color || AV[i % AV.length] }}>{initials2(n)}</div>
              <div className="pod-n">{n}</div>
              <div className="pod-u">{u}</div>
              <div className="pod-xp num"><CountXP v={xp} d={[180, 0, 340][i]} /> XP</div>
              {pz > 0
                ? <div className="pod-prize" title="Premio si esta posición se mantiene al cierre del período">🪙 +{pz} monedas</div>
                : <div className="pod-prize empty">Gloria histórica</div>}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Board({ scope, period, rows: allRows = NAMES, loading = false }) {
  const rows = allRows.slice(3);
  const meIndex = allRows.findIndex((r) => !!r[3]);
  const me = meIndex >= 0 ? allRows[meIndex] : null;
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFF3CF" }}><IconTrophy size={17} /></span>
        <h3>Tabla completa</h3>
        <span className="mono">{allRows.length} estudiantes en esta liga</span>
      </div>
      {rows.map(([n, u, xp, me, raw], i) => {
        const rank = i + 4;
        const pz = prizeFor(scope.id, rank, period);
        return (
          <React.Fragment key={n}>
            {i === 2 && <div className="lb-gap" style={{ "--rd": i * 70 + 40 + "ms" }}>· · · 38 estudiantes más · · ·</div>}
            <div className={"lbrow" + (me ? " me" : "")} style={{ "--rd": i * 70 + "ms" }}>
              <span className="rk num">{rank}º</span>
              <span className="av2" style={{ background: raw?.avatar_color || AV[(i + 3) % AV.length] }}>{initials2(n)}</span>
              <span className="who"><span className="nm2">{n}{me && " · tú"}</span><span className="mt">{u}</span></span>
              {pz > 0 && <span className="prz">🪙 +{pz}</span>}
              <span className="xp2 num"><CountXP v={xp} d={i * 70} /></span>
            </div>
          </React.Fragment>
        );
      })}
      <div className="mine" style={{ "--rd": rows.length * 70 + 80 + "ms" }}>
        <span className="rk num">{me ? meIndex + 1 + "º" : "—"}</span>
        <div><div className="lbl">Tu posición</div><div className="val">{me ? fmt(me[2]) + " XP · " + me[1] : "Sin XP en este período"}</div></div>
        <div className="grow"><div className="lbl">Para el top 5</div><div className="val">{me && allRows[4] ? "+" + fmt(Math.max(0, allRows[4][2] - me[2] + 1)) + " XP" : "—"}</div></div>
      </div>
    </section>
  );
}

function PrizeTable({ scope, period }) {
  const base = PRIZES[scope.id];
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFE0CB" }}><IconCoin size={17} /></span>
        <h3>Premios de {scope.label.toLowerCase()}</h3>
        <span className="mono">{period === "week" ? "semanal" : period === "month" ? "mensual" : "sin premios"}</span>
      </div>
      {base ? (
        <div className="prizes">
          {base.map((c, i) => {
            const v = prizeFor(scope.id, i + 1, period);
            return (
              <div className={"pz" + (i === 0 ? " gold" : "")} key={i}>
                <div className="r">{i + 1}º lugar</div>
                <div className="c num">{v || "—"}</div>
                <div className="u">monedas</div>
              </div>
            );
          })}
        </div>
      ) : (
        <p style={{ fontSize: 13.5, color: "var(--ink-2)", fontWeight: 700, lineHeight: 1.45 }}>
          Esta liga no reparte monedas. Se juega por la posición y nada más.
        </p>
      )}
      <p style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 13, fontWeight: 700, lineHeight: 1.45 }}>
        El premio semanal es la mitad del mensual. Se paga solo si mantienes la posición al cierre del período, y se abona automáticamente en tu billetera.
      </p>
    </section>
  );
}

function HowItScores() {
  const rows = [
    ["⏱", "Minutos de enfoque", "1 XP por minuto guardado desde una sesión real"],
    ["🎯", "Bloques del plan", "+25 XP al completar un bloque de tu plan semanal"],
    ["🧠", "Quizzes y flashcards", "+30 XP por quiz terminado, más bonus por puntaje"],
    ["🔥", "Racha", "Multiplicador que sube con los días seguidos"],
  ];
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconSparkle size={17} /></span>
        <h3>Cómo se gana XP</h3>
      </div>
      <div className="why">
        {rows.map(([ic, t, s]) => (
          <div className="why-row" key={t}>
            <span className="why-n">{ic}</span>
            <div><div className="why-t">{t}</div><div className="why-s">{s}</div></div>
          </div>
        ))}
      </div>
    </section>
  );
}

Object.assign(window, { RankHead, Arena, Board, PrizeTable, HowItScores, SCOPES });
