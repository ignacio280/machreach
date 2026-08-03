/* Ajustes — cuenta, privacidad y datos del perfil.
   These panels used to live in tabs on the profile page; the profile is now
   just the profile, and everything you configure lives here. */
const IconEdit = (p) => <Icon {...p}><path d="M4 20h4l10-10a2.8 2.8 0 10-4-4L4 16v4z" /><path d="M13.5 6.5l4 4" /></Icon>;
const IconMail = (p) => <Icon {...p}><rect x="3" y="5.5" width="18" height="13" rx="3" /><path d="M4 7.5l8 5.5 8-5.5" /></Icon>;

const ST_PRIVACY = [
  ["Perfil público", "Otros estudiantes pueden ver tu nivel, insignias y cursos.", true],
  ["Aparecer en rankings", "Si lo apagas sales de todos los rankings, incluso el de carrera.", true],
  ["Estado en línea", "Muestra a tus amigos cuándo estás en una sesión de enfoque.", true],
  ["Permitir solicitudes", "Cualquiera con tu ID puede enviarte solicitud de amistad.", false],
  ["Mostrar mi universidad", "Aparece junto a tu nombre en el ranking por país.", true],
  ["Mostrar mi carrera", "Aparece junto a tu nombre en el ranking por universidad.", true],
];
const ST_NOTIF = [
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

function SettingsIdentity({ data, csrf }) {
  const [name, setName] = React.useState(data.name || "");
  const [saved, setSaved] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const original = React.useRef({ name: data.name || "" });
  const save = async () => {
    setSaving(true); setError("");
    try {
      const response = await fetch("/api/student/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        // Bio and avatar colour are not edited here; echo what the account
        // already has so saving a name cannot wipe them.
        body: JSON.stringify({ name, bio: data.bio || "", avatar_color: data.avatar_color || "#FFD3A8" }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "No se pudieron guardar los cambios.");
      original.current = { name };
      setSaved(true);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };
  const discard = () => { setName(original.current.name); setSaved(false); setError(""); };
  return (
    <section className="pnl">
      <div className="pnl-h"><span className="ico-badge" style={{ background: "var(--brand-tint)" }}><IconEdit size={16} /></span><h3>Datos del perfil</h3></div>
      <div className="pf-form">
        <div className="pf-f"><label htmlFor="st-n">Nombre para mostrar</label><input id="st-n" value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} /></div>
        <div className="pf-f"><label htmlFor="st-u">Usuario</label><input id="st-u" value={(data.handle || "@").replace(/^@/, "")} readOnly /><span className="hint">Tu identificador se genera desde tu correo.</span></div>
        <div className="pf-f"><label htmlFor="st-c">Carrera</label>
          <select id="st-c" value="current" disabled><option value="current">{data.major || "Carrera sin configurar"}</option></select></div>
        <div className="pf-f"><label htmlFor="st-s">Semestre</label>
          <select id="st-s" value="current" disabled><option value="current">{data.semester || "I"}</option></select></div>
      </div>
      <div className="pf-save">
        {saved && <span className="ok">✓ Cambios guardados</span>}
        {error && <span className="pf-error" role="alert">{error}</span>}
        <button className="btn btn-ghost btn-sm" onClick={discard}>Descartar</button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? "Guardando…" : "Guardar cambios"}</button>
      </div>
      <p className="pf-coming">La foto, el banner y los cosméticos se editan en <a className="lnk" href="/student/profile/edit">Editar perfil</a>.</p>
    </section>
  );
}

function SettingsAccount({ data, csrf, plus }) {
  return (
    <div className="pf-tabpanel">
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
        <div className="pnl-h"><span className="ico-badge" style={{ background: "#DCEEFB" }}><IconPeople size={16} /></span><h3>Tu ID de amigo</h3></div>
        <div className="fr-id" style={{ width: "100%", justifyContent: "space-between" }}>ID<b>{data.friend_id || "—"}</b></div>
        <p style={{ color: "var(--ink-2)", fontSize: 13, fontWeight: 700, marginTop: 12, lineHeight: 1.45 }}>Compártelo para que te agreguen sin buscarte por nombre.</p>
        <button className="btn btn-ghost btn-sm" style={{ marginTop: 12 }} onClick={() => navigator.clipboard?.writeText(data.friend_id || "")}>Copiar ID</button>
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
  );
}

function SettingsPrivacy({ data, csrf }) {
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
    <div className="pf-tabpanel">
      <section className="pnl">
        <div className="pnl-h"><span className="ico-badge" style={{ background: "#E6E4FB" }}><IconShield size={16} /></span><h3>Privacidad</h3></div>
        <ToggleRows rows={ST_PRIVACY} initial={privacyInitial} keys={["profile_public", "appear_in_rankings", "show_online", "allow_requests", "show_university", "show_major"]} onSave={savePreference} />
        {status && <div className="pf-pref-status" role="status">{status}</div>}
      </section>
      <section className="pnl">
        <div className="pnl-h"><span className="ico-badge" style={{ background: "#FFE7D6" }}><IconBell size={16} /></span><h3>Notificaciones</h3></div>
        <ToggleRows rows={ST_NOTIF} initial={prefs} keys={["block_reminders", "streak_risk", "grade_results", "weekly_summary", "product_news"]} onSave={savePreference} disabled />
        <p className="pf-coming">Estas preferencias estarán disponibles cuando se activen sus canales de notificación.</p>
      </section>
      <section className="pnl">
        <div className="pnl-h"><span className="ico-badge" style={{ background: "#E4F7EE" }}><IconTimer size={16} /></span><h3>Focus Guard</h3></div>
        <div className="pf-row"><div className="who"><b>Bloquear sitios distractores</b><p>Se configura desde Focus Guard en la extensión.</p></div><Sw label="Bloquear sitios distractores" disabled on={true} onChange={() => {}} /></div>
        <div className="pf-row"><div className="who"><b>Silenciar notificaciones</b><p>Disponible próximamente en Focus Guard.</p></div><Sw label="Silenciar notificaciones" disabled on={false} onChange={() => {}} /></div>
      </section>
      <section className="pnl">
        <div className="pnl-h"><span className="ico-badge" style={{ background: "#FFF2C9" }}><IconCoin size={16} /></span><h3>Facturación</h3></div>
        <div className="pf-row"><div className="who"><b>{data.plus ? "MachReach Plus" : "Plan Gratis"}</b><p>{data.plus ? "Tu suscripción está activa." : "No tienes una suscripción de pago activa."}</p></div><span className="val">{data.plus ? "Activo" : "Gratis"}</span></div>
        <a className="lnk" href="/student/shop?section=plan" style={{ display: "inline-flex", marginTop: 14 }}>Gestionar facturación <IconArrow size={14} /></a>
      </section>
    </div>
  );
}

const ST_TABS = [
  { id: "perfil", label: "Datos del perfil", Ic: IconEdit },
  { id: "cuenta", label: "Cuenta", Ic: IconShield },
  { id: "privacidad", label: "Privacidad y avisos", Ic: IconBell },
];

function SettingsPage({ plus, data = {}, csrf = "" }) {
  const [tab, setTab] = React.useState("perfil");
  return (
    <div className="col st-wrap">
      <section className="st-head">
        <div>
          <div className="pl-kicker">Ajustes</div>
          <h1>Tu cuenta, a tu manera.</h1>
          <p>Datos, privacidad y seguridad. Lo visual (foto, banner, banderas e insignias) vive en el editor de perfil.</p>
        </div>
      </section>
      <div className="pf-tabs">
        {ST_TABS.map((t) => (
          <button key={t.id} className={"pf-tab" + (tab === t.id ? " on" : "")} onClick={() => setTab(t.id)}><t.Ic size={15} />{t.label}</button>
        ))}
      </div>
      {tab === "perfil" && <SettingsIdentity data={data} csrf={csrf} />}
      {tab === "cuenta" && <SettingsAccount data={data} csrf={csrf} plus={plus} />}
      {tab === "privacidad" && <SettingsPrivacy data={{ ...data, plus }} csrf={csrf} />}
    </div>
  );
}

Object.assign(window, { SettingsPage, Sw, ToggleRows, ST_PRIVACY, ST_NOTIF });
