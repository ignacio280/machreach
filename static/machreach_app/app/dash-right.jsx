/* Dashboard — right column */
const BOARD = [
  { r: 3, n: "Camila Rojas", xp: "4.820" },
  { r: 4, n: "Tomás Vidal", xp: "4.315" },
  { r: 5, n: "Sofía Peña", xp: "4.180", me: true },
  { r: 6, n: "Ignacio Soto", xp: "3.970" },
  { r: 7, n: "Valentina Cruz", xp: "3.640" },
];

const FRIENDS = [
  { n: "Camila Rojas", s: "Estudiando ahora · Cálculo II", on: true, xp: "4.820" },
  { n: "Tomás Vidal", s: "Racha de 22 días", xp: "4.315" },
  { n: "Ignacio Soto", s: "Enfoque hace 2 h", xp: "3.970" },
  { n: "Valentina Cruz", s: "Subió a nivel 10", xp: "3.640" },
];

const HEAT = [0, 2, 3, 1, 4, 0, 2, 3, 3, 4, 2, 1, 0, 3, 4, 4, 2, 3, 3, 0, 1, 2, 4, 3, 4, 4, 2, 3, 3, 4, -1, -1, -1, -1, -1];

function initials(n) { const p = n.split(" "); return (p[0][0] + (p[1] ? p[1][0] : "")).toUpperCase(); }

function League({ data = {} }) {
  const board = data.board || BOARD;
  const [ref, seen] = useReveal({ threshold: 0.2 });
  return (
    <section className={"league rv" + (seen ? " in" : "")} ref={ref}>
      <div className="eye">{data.eyebrow || "Liga semanal"}</div>
      <h3><IconTrophy size={22} color="var(--amber)" /> {data.title || "Liga Oro"}</h3>
      <div className="league-rank"><b className="num">{data.rank || "5º"}</b><span>{data.rank_copy || "de 48 en tu liga"}</span></div>
      <div className="league-promo">{data.promo || "Top 3 sube a Liga Platino · quedan 2 días"}</div>
      <div className="board">
        {board.length ? board.map((b, i) => (
          <div className={"lb" + (b.me ? " me" : "")} key={i}>
            <span className="r">{b.r}º</span>
            <span className="av" style={{ background: AV[i % AV.length] }}><AvatarFace url={b.pic}>{initials(b.n)}</AvatarFace></span>
            <span className="n">{b.n}</span>
            <span className="x num">{b.xp}</span>
          </div>
        )) : <div className="dash-empty">{data.empty || "Sé el primero en sumar XP."}</div>}
      </div>
    </section>
  );
}

function StreakCard({ plus = false, data = {} }) {
  const heat = data.heat || HEAT;
  const [ref, seen] = useReveal({ threshold: 0.2 });
  return (
    <section className={"pnl rv" + (seen ? " in" : "")} ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFE0CB" }}><IconFire size={17} color="var(--brand)" /></span>
        <h3>{data.title || "Tu racha"}</h3>
        <PlusLink locked={!plus} href={data.href || "/student/analytics"}>{data.detail_label || "Detalle"}</PlusLink>
      </div>
      <div className="streak-lead">
        <div className="streak-num num">{data.days == null ? 17 : data.days}</div>
        <div className="streak-copy">
          <strong>{data.days_label || "días seguidos"}</strong>
          <span>{data.range_label || "últimas 5 semanas"}</span>
          <div className="streak-meter"><i style={{ width: (seen ? (data.attendance == null ? 86 : data.attendance) : 0) + "%" }} /></div>
        </div>
      </div>
      <div className="heat">
        {heat.map((v, i) => (
          <div key={i} className={"hc " + (v < 0 ? "future" : "l" + v) + (i === 29 ? " today" : "")} title={v < 0 ? "" : v * 35 + " min"} />
        ))}
      </div>
      <div className="wk">{(data.weekdays || ["L", "M", "M", "J", "V", "S", "D"]).map((d, i) => <span key={i}>{d}</span>)}</div>
      <div className="srow">
        <div><div className="srn num">{data.active_days == null ? 29 : data.active_days}</div><div className="srl">{data.active_label || "Días activos"}</div></div>
        <div><div className="srn num">{data.freezes == null ? (plus ? 5 : 2) : data.freezes}</div><div className="srl">{data.freezes_label || "Congeladores"}</div></div>
        <div><div className="srn num">{data.attendance == null ? 86 : data.attendance}%</div><div className="srl">{data.attendance_label || "Asistencia"}</div></div>
      </div>
    </section>
  );
}

function LevelCard({ data = {} }) {
  const [ref, seen] = useReveal({ threshold: 0.3 });
  return (
    <div className={"lvl rv" + (seen ? " in" : "")} ref={ref}>
      <Ring pct={seen ? (data.pct == null ? 72 : data.pct) : 0} size={54} sw={7} color="var(--plum)" label={String(data.level == null ? 11 : data.level)} />
      <div>
        <div className="lvl-t">{data.title || "Nivel 11 · Constante"}</div>
        <div className="lvl-s">{data.copy || "85 XP para el nivel 12"}</div>
      </div>
    </div>
  );
}

function Friends({ data = {} }) {
  const friends = data.items || FRIENDS;
  const [ref, seen] = useReveal({ threshold: 0.2 });
  return (
    <section className={"pnl rv" + (seen ? " in" : "")} ref={ref}>
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconPeople size={17} /></span>
        <h3>{data.title || "Tus amigos"}</h3>
        <a href={data.href || "/student/friends"} className="lnk">{data.all_label || "Ver todos"} <IconArrow size={14} /></a>
      </div>
      <div>
        {friends.length ? friends.map((f, i) => (
          <div className="fr" key={i}>
            <div className="fr-av" style={{ background: AV[(i + 2) % AV.length] }}><AvatarFace url={f.pic}>{initials(f.n)}</AvatarFace><span className={"pres" + (f.on ? " on" : "")} /></div>
            <div style={{ minWidth: 0 }}>
              <div className="fr-n">{f.n}</div>
              <div className={"fr-s" + (f.on ? " on" : "")}>{f.s}</div>
            </div>
            <div className="fr-x num">{f.xp}</div>
          </div>
        )) : <div className="dash-empty">{data.empty || "Aún no tienes amigos."}</div>}
      </div>
    </section>
  );
}

Object.assign(window, { League, StreakCard, LevelCard, Friends });
