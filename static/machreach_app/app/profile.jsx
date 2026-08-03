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
const PF_PRIVACY = [
  ["Perfil público", "Otros estudiantes pueden ver tu nivel, insignias y cursos.", true],
  ["Aparecer en rankings", "Si lo apagas sales de todos los rankings, incluso el de carrera.", true],
  ["Estado en línea", "Muestra a tus amigos cuándo estás en una sesión de enfoque.", true],
  ["Permitir solicitudes", "Cualquiera con tu ID puede enviarte solicitud de amistad.", false],
];
const PF_NOTIF = [
  ["Recordatorio de bloques", "Aviso 10 minutos antes de cada bloque planificado.", true],
  ["Racha en riesgo", "Te avisamos si no has estudiado antes de las 21:00.", true],
  ["Resultados de evaluaciones", "Cuando se publique una nota nueva en Canvas.", true],
  ["Resumen semanal", "Correo con tu progreso, XP y ranking cada domingo.", false],
  ["Novedades de MachReach", "Funciones nuevas y consejos de estudio.", false],
];

function Sw({ on, onChange, label, disabled = false }) {
  return <button type="button" className={"sw" + (on ? " on" : "")} onClick={() => onChange(!on)} role="switch" aria-checked={on} aria-label={label} disabled={disabled}><i /></button>;
}

function ToggleRows({ rows, initial = {}, keys = [], onSave, disabled = false }) {
  const [st, setSt] = React.useState(() => rows.map((r, i) => Object.prototype.hasOwnProperty.call(initial, keys[i]) ? !!initial[keys[i]] : r[2]));
  return (
    <div>
      {rows.map(([t, s], i) => (
        <div className="pf-row" key={t}>
          <div className="who"><b>{t}</b><p>{s}</p></div>
          <Sw label={t} disabled={disabled} on={st[i]} onChange={(v) => {
            setSt((a) => a.map((x, j) => (j === i ? v : x)));
            if (onSave && keys[i]) onSave(keys[i], v);
          }} />
        </div>
      ))}
    </div>
  );
}

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

function ProfileTabPerfil({ avatar, setAvatar, name, setName, plus, data, csrf }) {
  const [saved, setSaved] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [bio, setBio] = React.useState(data.bio || "");
  const original = React.useRef({ name: data.name || "", bio: data.bio || "", avatar: data.avatar_color || PF_AVATARS[0] });
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
  const save = async () => {
    setSaving(true); setError("");
    try {
      const response = await fetch("/api/student/profile", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ name, bio, avatar_color: avatar }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "No se pudieron guardar los cambios.");
      original.current = { name, bio, avatar };
      setSaved(true);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };
  const discard = () => { setName(original.current.name); setBio(original.current.bio); setAvatar(original.current.avatar); setSaved(false); setError(""); };
  return (
    <div className="pf-tabpanel pf-grid">
      <div className="col">
        <section className="pnl" id="editar">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "var(--brand-tint)" }}><IconEdit size={16} /></span><h3>Datos del perfil</h3></div>
          <div className="pf-form">
            <div className="pf-f"><label htmlFor="pf-n">Nombre para mostrar</label><input id="pf-n" value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} /></div>
            <div className="pf-f"><label htmlFor="pf-u">Usuario</label><input id="pf-u" value={(data.handle || "@").replace(/^@/, "")} readOnly /><span className="hint">Tu identificador se genera desde tu correo.</span></div>
            <div className="pf-f"><label htmlFor="pf-c">Carrera</label>
              <select id="pf-c" value="current" disabled><option value="current">{data.major || "Carrera sin configurar"}</option></select></div>
            <div className="pf-f"><label htmlFor="pf-s">Semestre</label>
              <select id="pf-s" value="current" disabled><option value="current">{data.semester || "I"}</option></select></div>
            <div className="pf-f full"><label htmlFor="pf-b">Sobre ti</label>
              <textarea id="pf-b" maxLength="180" value={bio} onChange={(e) => { setBio(e.target.value); setSaved(false); }} />
              <span className="hint">Máximo 180 caracteres. Visible solo si tu perfil es público.</span></div>
            <div className="pf-f full"><label>Color de avatar</label>
              <div className="pf-avpick">{PF_AVATARS.map((c) => (
                <button key={c} className={"pf-av" + (c === avatar ? " on" : "")} style={{ background: c }} onClick={() => { setAvatar(c); setSaved(false); }} aria-label={"Avatar " + c} />
              ))}</div></div>
          </div>
          <div className="pf-save">
            {saved && <span className="ok">✓ Cambios guardados</span>}
            {error && <span className="pf-error" role="alert">{error}</span>}
            <button className="btn btn-ghost btn-sm" onClick={discard}>Descartar</button>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? "Guardando…" : "Guardar cambios"}</button>
          </div>
        </section>

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

function ProfileTabCuenta({ data, csrf, plus }) {
  return (
    <div className="pf-tabpanel pf-grid">
      <div className="col">
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#DCEEFB" }}><IconMail size={16} /></span><h3>Correo y acceso</h3></div>
          <form method="post" action="/settings/change-password">
            <input type="hidden" name="csrf_token" value={csrf} />
            <div className="pf-form">
              <div className="pf-f full"><label htmlFor="ac-e">Correo institucional</label><input id="ac-e" value={data.email || ""} readOnly /><span className="hint">Correo asociado a tu cuenta MachReach.</span></div>
              <div className="pf-f"><label htmlFor="ac-p1">Contraseña actual</label><input id="ac-p1" name="current_password" type="password" autoComplete="current-password" required placeholder="••••••••••" /></div>
              <div className="pf-f"><label htmlFor="ac-p2">Nueva contraseña</label><input id="ac-p2" name="new_password" type="password" autoComplete="new-password" minLength="10" required placeholder="Mínimo 10 caracteres" /></div>
              <div className="pf-f full"><label htmlFor="ac-p3">Confirmar contraseña</label><input id="ac-p3" name="confirm_password" type="password" autoComplete="new-password" minLength="10" required /></div>
            </div>
            <div className="pf-save"><button className="btn btn-primary btn-sm" type="submit">Actualizar acceso</button></div>
          </form>
        </section>

        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#E4F7EE" }}><IconShield size={16} /></span><h3>Conexiones</h3></div>
          <div className="pf-row"><span className="pf-dot" style={{ background: "#FFE7D6" }}>🎓</span>
            <div className="who"><b>{data.university || "Universidad"}</b><p>{data.canvas_connected ? "Canvas conectado mediante la extensión." : "La extensión detectará Canvas cuando esté disponible."}</p></div>
            <span className="val" style={{ color: data.canvas_connected ? "var(--good)" : "var(--ink-2)" }}>{data.canvas_connected ? "Conectado" : "Pendiente"}</span></div>
          <div className="pf-row"><span className="pf-dot" style={{ background: "#DCEEFB" }}>🧩</span>
            <div className="who"><b>Extensión de navegador</b><p>Conecta tus cursos y actividad de Canvas.</p></div>
            <a className="fr-btn orange" href="/student">Usar extensión</a></div>
        </section>

        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#FFF2C9" }}><IconTimer size={16} /></span><h3>Sesiones activas</h3></div>
          <div className="pf-row"><span className="pf-dot" style={{ background: "#E4F7EE" }}>💻</span>
            <div className="who"><b>Este navegador</b><p>Tu sesión actual de MachReach.</p></div><span className="val" style={{ color: "var(--good)" }}>Ahora</span></div>
        </section>

        <section className="pf-danger">
          <h3>Zona delicada</h3>
          <p>Puedes descargar todo lo que MachReach guarda sobre ti, o eliminar tu cuenta. Eliminar borra XP, rachas, notas y materiales sin vuelta atrás.</p>
          <div className="acts">
            <a className="btn btn-ghost btn-sm" href="/api/export-my-data">Descargar mis datos</a>
            <form method="post" action="/settings/delete-account" className="pf-delete-form" onSubmit={(e) => { if (!window.confirm("Esta acción elimina tu cuenta y no se puede deshacer. ¿Continuar?")) e.preventDefault(); }}>
              <input type="hidden" name="csrf_token" value={csrf} />
              <input name="confirm" required placeholder="Escribe ELIMINAR" aria-label="Escribe ELIMINAR para confirmar" />
              <button className="btn btn-sm btn-danger" type="submit">Eliminar cuenta</button>
            </form>
          </div>
        </section>
      </div>

      <div className="col">
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "var(--brand-tint)" }}><IconShield size={16} /></span><h3>Seguridad</h3></div>
          <div className="pf-row"><div className="who"><b>Sesiones protegidas</b><p>Los cambios de contraseña cierran las otras sesiones activas.</p></div><span className="val" style={{ color: "var(--good)" }}>Activa</span></div>
        </section>
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#FFF2C9" }}><IconCoin size={16} /></span><h3>Facturación</h3></div>
          <div className="pf-row"><div className="who"><b>{plus ? "MachReach Plus" : "Plan Gratis"}</b><p>{plus ? "Tu suscripción está activa." : "No tienes una suscripción de pago activa."}</p></div><span className="val">{plus ? "Activo" : "Gratis"}</span></div>
          <a className="lnk" href="/student/shop?section=plan" style={{ display: "inline-flex", marginTop: 14 }}>Gestionar facturación <IconArrow size={14} /></a>
        </section>
      </div>
    </div>
  );
}

function ProfileTabPrivacidad({ data, csrf }) {
  const [prefs, setPrefs] = React.useState(data.preferences || {});
  const [status, setStatus] = React.useState("");
  const savePreference = async (key, value) => {
    setPrefs((p) => ({ ...p, [key]: value })); setStatus("Guardando…");
    try {
      const response = await fetch("/api/student/profile/preferences", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ [key]: value }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "No se pudo guardar.");
      setStatus("✓ Guardado");
    } catch (e) { setStatus(e.message); }
  };
  const privacyInitial = { ...prefs, allow_requests: data.friend_discovery };
  return (
    <div className="pf-tabpanel pf-grid">
      <div className="col">
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#E6E4FB" }}><IconShield size={16} /></span><h3>Privacidad</h3></div>
          <ToggleRows rows={PF_PRIVACY} initial={privacyInitial} keys={["profile_public", "appear_in_rankings", "show_online", "allow_requests"]} onSave={savePreference} />
        </section>
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#FFE7D6" }}><IconBell size={16} /></span><h3>Notificaciones</h3></div>
          <ToggleRows rows={PF_NOTIF} initial={prefs} keys={["block_reminders", "streak_risk", "grade_results", "weekly_summary", "product_news"]} onSave={savePreference} disabled />
          <p className="pf-coming">Estas preferencias estarán disponibles cuando se activen sus canales de notificación.</p>
          {status && <div className="pf-pref-status" role="status">{status}</div>}
        </section>
      </div>
      <div className="col">
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#DCEEFB" }}><IconPeople size={16} /></span><h3>Tu ID de amigo</h3></div>
          <div className="fr-id" style={{ width: "100%", justifyContent: "space-between" }}>ID<b>{data.friend_id || "—"}</b></div>
          <p style={{ color: "var(--ink-2)", fontSize: 13, fontWeight: 700, marginTop: 12, lineHeight: 1.45 }}>Compártelo para que te agreguen sin buscarte por nombre.</p>
          <button className="btn btn-ghost btn-sm" style={{ marginTop: 12 }} onClick={() => { navigator.clipboard?.writeText(data.friend_id || ""); setStatus("✓ ID copiado"); }}>Copiar ID</button>
        </section>
        <section className="pnl">
          <div className="pnl-h"><span className="ico-badge" style={{ background: "#E4F7EE" }}><IconTimer size={16} /></span><h3>Focus Guard</h3></div>
          <div className="pf-row"><div className="who"><b>Bloquear sitios distractores</b><p>Se configura desde Focus Guard en la extensión.</p></div><Sw label="Bloquear sitios distractores" disabled on={true} onChange={() => {}} /></div>
          <div className="pf-row"><div className="who"><b>Silenciar notificaciones</b><p>Disponible próximamente en Focus Guard.</p></div><Sw label="Silenciar notificaciones" disabled on={false} onChange={() => {}} /></div>
        </section>
      </div>
    </div>
  );
}

const PF_TABS = [
  { id: "perfil", label: "Perfil", Ic: IconEdit },
  { id: "cuenta", label: "Cuenta", Ic: IconShield },
  { id: "privacidad", label: "Privacidad y avisos", Ic: IconBell },
];

function ProfilePage({ plus, data = {}, csrf = "" }) {
  const [tab, setTab] = React.useState("perfil");
  const [avatar, setAvatar] = React.useState(data.avatar_color || PF_AVATARS[0]);
  const [name, setName] = React.useState(data.name || "Estudiante MachReach");
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
      <div className="pf-tabs rv" style={{ "--d": "260ms" }}>
        {PF_TABS.map((t) => (
          <button key={t.id} className={"pf-tab" + (tab === t.id ? " on" : "")} onClick={() => setTab(t.id)}><t.Ic size={15} />{t.label}</button>
        ))}
      </div>
      {tab === "perfil" && <ProfileTabPerfil key="p" avatar={avatar} setAvatar={setAvatar} name={name} setName={setName} plus={plus} data={data} csrf={csrf} />}
      {tab === "cuenta" && <ProfileTabCuenta key="c" data={data} csrf={csrf} plus={plus} />}
      {tab === "privacidad" && <ProfileTabPrivacidad key="v" data={data} csrf={csrf} />}
    </div>
  );
}

Object.assign(window, { ProfilePage, Sw, IconEdit, IconMail });
