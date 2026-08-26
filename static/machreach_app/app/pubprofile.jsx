/* MachReach — Perfil público de otro estudiante.

   The same visual language as the own-profile page, over the much smaller set
   of facts a student may see about someone else. Everything here comes from
   `academic.public_profile`, so this component never has private data to leak:
   there is no email, no bio, no joined date, no courses and no heatmap, and
   Focus totals arrive as null unless the two are friends. */

function PubProfileHero({ data }) {
  const name = data.name || (SHELL_EN ? "Student" : "Estudiante");
  const initials = name.split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase();
  const rank = data.rank || {};
  const pos = data.leaderboard_position || {};
  const university = (data.university && data.university.name) || "";
  const major = (data.major && data.major.name) || "";
  return (
    <section className="pf-hero rv" style={{ "--d": "0ms" }}>
      <div className="pf-cover"><i /><i /><i /></div>
      <div className="pf-id">
        <div className="pf-face" style={{ background: data.avatar_color }}>
          {data.picture_url ? <img className="pf-facepic" src={data.picture_url} alt="" /> : initials}
          <span className="lvl">{data.level_number || 1}</span>
        </div>
        <div className="pf-meta">
          <div className="pf-name">
            <h1>{name}</h1>
            {data.retired && <span className="pf-tag">🏖️ {SHELL_EN ? "Graduate" : "Egresado"}</span>}
          </div>
          <div className="pf-handle">{university || (SHELL_EN ? "No university set" : "Sin universidad")}</div>
          <div className="pf-tags">
            <span className="pf-tag"><IconBook size={14} /> {major || (SHELL_EN ? "No major set" : "Sin carrera")}</span>
            <span className="pf-tag"><IconTrophy size={14} /> {rank.full_name || (SHELL_EN ? "Unranked" : "Sin rango")}</span>
            <span className="pf-tag"><IconStar size={14} /> {data.badge_count || 0} {SHELL_EN ? "badges" : "insignias"}</span>
          </div>
        </div>
        <div className="pf-idacts">
          <button className="btn btn-ghost btn-sm" onClick={async () => {
            const url = location.origin + "/student/profile/" + data.user_id;
            if (navigator.share) await navigator.share({ title: name, url });
            else await navigator.clipboard?.writeText(url);
          }}><IconPeople size={15} /> {SHELL_EN ? "Share profile" : "Compartir perfil"}</button>
          <a className="btn btn-primary btn-sm" href="/student/leaderboard"><IconTrophy size={15} /> {SHELL_EN ? "Leaderboard" : "Ver ranking"}</a>
        </div>
      </div>
      <div className="pf-xp">
        <div>
          <div className="lab">
            <b>{rank.full_name || (SHELL_EN ? "Unranked" : "Sin rango")}</b>
            <span className="num">{Number(data.xp || 0).toLocaleString("es-CL")} XP</span>
          </div>
          <div className="bar"><i style={{ width: Math.max(0, Math.min(100, Number(rank.progress_pct || 0))) + "%" }} /></div>
        </div>
        <div className="pf-nextlvl">
          <IconTrophy size={20} color="var(--plum)" />
          <div>
            <b>{pos.rank ? "#" + pos.rank : (SHELL_EN ? "Unranked" : "Sin clasificar")}</b>
            <span style={{ display: "block" }}>
              {pos.rank
                ? (SHELL_EN ? "of " : "de ") + Number(pos.total || 0).toLocaleString("es-CL") +
                  (pos.scope === "retirement" ? (SHELL_EN ? " · retired" : " · retirados") : " · global")
                : (SHELL_EN ? "no position yet" : "aún sin posición")}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function PubProfilePage({ data = {} }) {
  const [shown, setShown] = React.useState(false);
  React.useEffect(() => { const t = setTimeout(() => setShown(true), 70); return () => clearTimeout(t); }, []);

  const pos = data.leaderboard_position || {};
  const rank = data.rank || {};
  const badges = data.badges || [];
  // Focus totals come back null for anyone who is not a friend, which is a
  // different thing from a friend who has never studied — say so rather than
  // printing a zero that reads like an insult.
  const focusPrivate = !!data.focus_private;
  const hours = Number(data.total_hours || 0);
  const stats = [
    {
      n: Number(data.xp || 0).toLocaleString("es-CL"), l: SHELL_EN ? "Total XP" : "XP total",
      d: rank.full_name || (SHELL_EN ? "Unranked" : "Sin rango"),
      Ic: IconBolt, bg: "var(--brand-tint)", c: "var(--plum)",
    },
    {
      n: pos.rank ? "#" + pos.rank : "—", l: SHELL_EN ? "Leaderboard" : "Clasificación",
      d: pos.rank ? (SHELL_EN ? "of " : "de ") + Number(pos.total || 0).toLocaleString("es-CL") : (SHELL_EN ? "Unranked" : "Sin clasificar"),
      Ic: IconTrophy, bg: "#FFF2C9", c: "#B58309",
    },
    {
      n: focusPrivate ? "—" : hours.toFixed(1) + " h", l: SHELL_EN ? "Focus time" : "Enfoque acumulado",
      d: focusPrivate ? (SHELL_EN ? "Friends only" : "Solo amigos") : (Number(data.sessions || 0).toLocaleString("es-CL") + (SHELL_EN ? " sessions" : " sesiones")),
      Ic: IconTimer, bg: "#E4F7EE", c: "var(--good)",
    },
    {
      n: String(data.badge_count || 0), l: SHELL_EN ? "Badges" : "Insignias",
      d: SHELL_EN ? "unlocked" : "desbloqueadas",
      Ic: IconStar, bg: "#E6E4FB", c: "var(--plum)",
    },
  ];

  return (
    <div className={"col" + (shown ? " in" : "")}>
      <PubProfileHero data={data} />
      <div className="pf-stats">
        {stats.map((s, i) => (
          <div className="pf-stat pop" key={s.l} style={{ "--d": 120 + i * 80 + "ms" }}>
            <div className="top"><span className="ico-badge" style={{ background: s.bg }}><s.Ic size={16} color={s.c} /></span></div>
            <div className="n num">{s.n}</div><div className="l">{s.l}</div>
            <div className="d flat">{s.d}</div>
          </div>
        ))}
      </div>

      <div className="pf-grid">
        <div className="col">
          <section className="pnl">
            <div className="pnl-h">
              <span className="ico-badge" style={{ background: "#FFF2C9" }}><IconTrophy size={16} /></span>
              <h3>{SHELL_EN ? "Badges" : "Insignias"}</h3>
              <span className="lnk">{badges.length} {SHELL_EN ? "unlocked" : "desbloqueadas"}</span>
            </div>
            {badges.length === 0 && <div className="pf-empty">{SHELL_EN ? "No badges earned yet." : "Aún no hay insignias."}</div>}
            {badges.length > 0 && (
              <div className="pf-badges">
                {badges.map((b, i) => (
                  <div className="pf-badge pop" key={b.key || b.name || i} style={{ "--d": i * 60 + "ms" }}>
                    <span className="em" style={{ background: ["#FFE7D6", "#E4F7EE", "#E6E4FB", "#DCEEFB"][i % 4] }}>{b.icon || "🏅"}</span>
                    <b>{b.name || b.key || (SHELL_EN ? "Badge" : "Insignia")}</b>
                    <small>{b.desc || (b.earned_at ? String(b.earned_at).slice(0, 10) : (SHELL_EN ? "Earned badge." : "Insignia obtenida."))}</small>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        <div className="col">
          <section className="pnl">
            <div className="pnl-h">
              <span className="ico-badge" style={{ background: "var(--brand-tint)" }}><IconBolt size={16} /></span>
              <h3>{SHELL_EN ? "Study rank" : "Rango de estudio"}</h3>
            </div>
            <div className="pf-row">
              <div className="who"><b>{rank.full_name || (SHELL_EN ? "Unranked" : "Sin rango")}</b><p>{Number(data.xp || 0).toLocaleString("es-CL")} XP</p></div>
              <span className="val" style={{ color: rank.color || "var(--brand)" }}>{Math.round(Number(rank.progress_pct || 0))}%</span>
            </div>
            <div className="pf-row">
              <div className="who"><b>{SHELL_EN ? "Leaderboard" : "Clasificación"}</b><p>{pos.scope === "retirement" ? (SHELL_EN ? "Retired" : "Retirados") : "Global"}</p></div>
              <span className="val">{pos.rank ? "#" + pos.rank + " / " + Number(pos.total || 0).toLocaleString("es-CL") : "—"}</span>
            </div>
          </section>

          <section className="pnl">
            <div className="pnl-h">
              <span className="ico-badge" style={{ background: "#E4F7EE" }}><IconTimer size={16} /></span>
              <h3>{SHELL_EN ? "Focus" : "Enfoque"}</h3>
            </div>
            {focusPrivate ? (
              <div className="pf-empty">
                {SHELL_EN
                  ? "Focus totals are visible to friends only."
                  : "Las horas de enfoque solo las ven sus amigos."}
              </div>
            ) : (
              <>
                <div className="pf-row">
                  <div className="who"><b>{SHELL_EN ? "Hours studied" : "Horas estudiadas"}</b></div>
                  <span className="val">{hours.toFixed(1)} h</span>
                </div>
                <div className="pf-row">
                  <div className="who"><b>{SHELL_EN ? "Focus sessions" : "Sesiones de enfoque"}</b></div>
                  <span className="val">{Number(data.sessions || 0).toLocaleString("es-CL")}</span>
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { PubProfilePage, PubProfileHero });
