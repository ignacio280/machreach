/* MachReach — Perfil del estudiante */
const IconEdit = (p) => <Icon {...p}><path d="M4 20h4l10-10a2.8 2.8 0 10-4-4L4 16v4z" /><path d="M13.5 6.5l4 4" /></Icon>;
const IconMail = (p) => <Icon {...p}><rect x="3" y="5.5" width="18" height="13" rx="3" /><path d="M4 7.5l8 5.5 8-5.5" /></Icon>;

const PF_AVATARS = ["#FFD3A8", "#FF8AA5", "#8DACFF", "#B29BFF", "#5DE3B0", "#9CD9F0", "#FFC857"];
const PF_STATS = [
  { n: "4.180", l: "XP total", d: "+320 esta semana", Ic: IconBolt, bg: "var(--brand-tint)", c: "var(--plum)" },
  { n: "17", l: "Racha actual", d: "Récord: 24 días", Ic: IconFire, bg: "#FFE7D6", c: "var(--brand)" },
  { n: "62 h", l: "Enfoque acumulado", d: "+4 h 20 m", Ic: IconTimer, bg: "#E4F7EE", c: "var(--good)" },
  { n: "#12", l: "Ranking carrera", d: "Sube 3 puestos", Ic: IconTrophy, bg: "#FFF2C9", c: "#B58309" },
];
function ProfileHero({ plus, avatar, name, handle, data }) {
  const initials = name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const joined = data.created_at ? new Date(data.created_at + "T12:00:00").toLocaleDateString("es-CL", { month: "long", year: "numeric" }) : "MachReach";
  const nextXp = Math.max(0, Number(data.level_ceil || 0) - Number(data.total_xp || 0));
  return (
    <section className="pf-hero rv" style={{ "--d": "0ms" }}>
      {/* With a real banner equipped the decorative blobs are hidden, so the
          hero shows exactly the banner the edit page marks as equipped. */}
      <div className={"pf-cover bnr-anim-host" + (data.cover_css ? " has-banner" : "") + (data.cover_anim_class ? " " + data.cover_anim_class : "")} style={data.cover_css ? { background: data.cover_css } : undefined}>
        {!data.cover_css && <React.Fragment><i /><i /><i /></React.Fragment>}
      </div>
      <div className="pf-id">
        <div className="pf-face" style={{ background: avatar }}>
          {data.picture_url ? <img className="pf-facepic" src={data.picture_url} alt="" /> : initials}
          <span className="lvl">{data.level_number || 1}</span>
        </div>
        <div className="pf-meta">
          <div className="pf-name"><h1>{name}</h1>{plus && <PlusBadge />}</div>
          <div className="pf-handle">{handle} · Se unió en {joined}</div>
          <div className="pf-tags">
            <span className="pf-tag"><IconBook size={14} /> {data.major || "Carrera sin configurar"}</span>
            <span className="pf-tag">🎓 {data.semester || "I"} semestre</span>
            <span className="pf-tag"><IconPeople size={14} /> {data.friend_count || 0} amigos</span>
          </div>
        </div>
        <div className="pf-idacts">
          <button className="btn btn-ghost btn-sm" onClick={async () => {
            const url = location.origin + "/student/profile/" + data.client_id;
            if (navigator.share) await navigator.share({ title: "Mi perfil MachReach", url });
            else await navigator.clipboard?.writeText(url);
          }}><IconPeople size={15} /> Compartir perfil</button>
          <a className="btn btn-primary btn-sm" href="/student/profile/edit"><IconEdit size={15} /> Editar perfil</a>
        </div>
      </div>
      <div className="pf-xp">
        <div>
          <div className="lab"><b>Nivel {data.level_number || 1} · {data.level_name || "Iniciado"}</b><span className="num">{Number(data.total_xp || 0).toLocaleString("es-CL")} / {Number(data.level_ceil || 100).toLocaleString("es-CL")} XP</span></div>
          <div className="bar"><i style={{ width: Math.max(0, Math.min(100, Number(data.level_progress || 0))) + "%" }} /></div>
        </div>
        <div className="pf-nextlvl"><IconBolt size={20} color="var(--plum)" /><div><b>{nextXp.toLocaleString("es-CL")} XP</b><span style={{ display: "block" }}>para nivel {(data.level_number || 1) + 1}</span></div></div>
      </div>
    </section>
  );
}

function ProfileBody({ plus, data }) {
  const courses = data.courses || [];
  const lockedBadges = [
    { em: "🔥", t: "Racha de 14", s: `${data.streak || 0}/14 días`, bg: "#FFE7D6", got: 0 },
    { em: "🎯", t: "10 quizzes", s: `${data.summary?.quizzes || 0}/10 creados`, bg: "#E4F7EE", got: 0 },
    { em: "🌙", t: "Sesión nocturna", s: "Aún bloqueada", bg: "#E6E4FB", got: 0 },
    { em: "📚", t: "6 cursos activos", s: `${courses.length}/6 cursos`, bg: "#DCEEFB", got: 0 },
    { em: "⚡", t: "5.000 XP", s: `Faltan ${Math.max(0, 5000 - Number(data.total_xp || 0)).toLocaleString("es-CL")} XP`, bg: "#FFF2C9", got: 0 },
    { em: "🏆", t: "Top 3", s: data.rank ? `Estás en #${data.rank}` : "Aún sin ranking", bg: "#FFE7D6", got: 0 },
    { em: "🧊", t: "Cero congelados", s: "Aún bloqueada", bg: "#DCEEFB", got: 0 },
    { em: "🤝", t: "10 amigos", s: `Tienes ${data.friend_count || 0}`, bg: "#E6E4FB", got: 0 },
  ];
  const badges = data.badges && data.badges.length ? data.badges.map((b, i) => ({ em: b.emoji, t: b.name, s: b.earned_at || "Desbloqueada", bg: ["#FFE7D6", "#E4F7EE", "#E6E4FB", "#DCEEFB"][i % 4], got: 1 })) : lockedBadges;
  return (
    <div className="pf-grid">
      <div className="col">
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#FFF2C9" }}><IconTrophy size={16} /></span><h3>Insignias</h3>
            <span className="lnk">{data.badges?.length || 0} desbloqueadas</span></div>
          <div className="pf-badges">
            {badges.map((b, i) => (
              <div className={"pf-badge pop" + (b.got ? "" : " locked")} key={b.t} style={{ "--d": i * 60 + "ms" }}>
                {b.pin ? <span className="pin"><IconStar size={13} /></span> : null}
                <span className="em" style={{ background: b.bg }}>{b.em}</span>
                <b>{b.t}</b><small>{b.s}</small>
              </div>
            ))}
          </div>
        </section>

        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#DCEEFB" }}><IconBook size={16} /></span><h3>Cursos de este semestre</h3>
            <a className="lnk" href="/student/courses">Ver cursos <IconArrow size={14} /></a></div>
          {courses.length === 0 && <div className="pf-empty">Todavía no tienes cursos en este semestre.</div>}
          {courses.map((c) => (
            <div className="pf-row" key={c.id || c.name}>
              <span className="pf-dot" style={{ background: c.bg }}><IconBook size={16} /></span>
              <div className="who"><b>{c.name}</b><p>{c.code}{c.semester ? " · " + c.semester : ""}</p></div>
              <span className="val">{c.grade}</span>
            </div>
          ))}
        </section>
      </div>

      <div className="col">
        <section className={plus ? "pf-plus" : "pnl"}>
          {plus ? (
            <>
              <h3>MachReach Plus activo</h3>
              <p>Plan inteligente, analíticas y quizzes ampliados. Revisa la fecha exacta de renovación en la tienda.</p>
              <div className="meta">
                <div><b>Plus</b><span>plan activo</span></div>
                <div><b>5</b><span>congelados máx.</span></div>
              </div>
              <a className="btn btn-primary btn-sm" href="/student/shop?section=plan">Gestionar plan</a>
            </>
          ) : (
            <>
              <div className="pnl-h"><span className="ico-badge" style={{ background: "var(--brand-tint)" }}><IconBolt size={16} /></span><h3>Plan gratuito</h3></div>
              <p style={{ color: "var(--ink-2)", fontSize: 13.5, lineHeight: 1.45 }}>Tienes 3 congelados, quizzes limitados y sin analíticas. Plus abre el plan inteligente y las estadísticas completas.</p>
              <a className="btn btn-primary btn-sm" href="/student/shop?section=plan" style={{ marginTop: 14 }}>Conocer Plus</a>
            </>
          )}
        </section>

        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#E4F7EE" }}><IconFire size={16} /></span><h3>Últimas 26 semanas</h3></div>
          <div className="pf-heat">
            {Array.from({ length: 26 * 7 }, (_, i) => {
              const v = Number((data.heat || [])[i] || 0);
              const bg = ["var(--paper-2)", "#FFE0CC", "#FFC09A", "#FF9A5C", "var(--brand)"][v];
              return <i key={i} style={{ background: bg }} />;
            })}
          </div>
          <div className="pf-heatleg">Menos <i style={{ background: "var(--paper-2)" }} /><i style={{ background: "#FFC09A" }} /><i style={{ background: "var(--brand)" }} /> Más</div>
        </section>

        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#E6E4FB" }}><IconStar size={16} /></span><h3>Resumen</h3></div>
          {[["Sesiones completadas", data.summary?.sessions || 0], ["Quizzes creados", data.summary?.quizzes || 0], ["Flashcards guardadas", data.summary?.cards || 0], ["Mazos creados", data.summary?.decks || 0], ["Amigos", data.summary?.friends || 0]].map(([t, v]) => (
            <div className="pf-row" key={t}><div className="who"><b>{t}</b></div><span className="val">{v}</span></div>
          ))}
        </section>
      </div>
    </div>
  );
}

function ProfilePage({ plus, data = {}, csrf = "" }) {
  const avatar = data.avatar_color || PF_AVATARS[0];
  const name = data.name || "Estudiante MachReach";
  const [shown, setShown] = React.useState(false);
  const stats = [
    { ...PF_STATS[0], n: Number(data.total_xp || 0).toLocaleString("es-CL"), d: "XP acumulado" },
    { ...PF_STATS[1], n: String(data.streak || 0), d: "días seguidos" },
    { ...PF_STATS[2], n: Math.round(Number(data.focus_minutes || 0) / 60) + " h", d: (data.sessions || 0) + " sesiones" },
    { ...PF_STATS[3], n: data.rank ? "#" + data.rank : "—", l: "Ranking carrera", d: data.rank ? "Posición actual" : "Aún sin ranking" },
  ];
  React.useEffect(() => { const t = setTimeout(() => setShown(true), 70); return () => clearTimeout(t); }, []);
  return (
    <div className={"col" + (shown ? " in" : "")}>
      <ProfileHero plus={plus} avatar={avatar} name={name} handle={data.handle || "@estudiante"} data={data} />
      <div className="pf-stats">
        {stats.map((s, i) => (
          <div className="pf-stat pop" key={s.l} style={{ "--d": 120 + i * 80 + "ms" }}>
            <div className="top"><span className="ico-badge" style={{ background: s.bg }}><s.Ic size={16} color={s.c} /></span></div>
            <div className="n num">{s.n}</div><div className="l">{s.l}</div>
            <div className={"d" + (i === 3 ? " flat" : "")}>{s.d}</div>
          </div>
        ))}
      </div>
      <ProfileBody plus={plus} data={data} />
    </div>
  );
}

Object.assign(window, { ProfilePage, ProfileBody, IconEdit, IconMail });
