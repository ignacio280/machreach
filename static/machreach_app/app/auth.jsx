/* MachReach — Auth screens (crear cuenta / iniciar sesión) */
const EyeIcon = ({ off }) => (
  <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    {off
      ? <><path d="M3 3l18 18" /><path d="M10.6 5.2A9.6 9.6 0 0112 5c5.5 0 9 5.2 9 7a11 11 0 01-2.6 3.4M6.3 6.7C3.9 8.2 3 10.3 3 12c0 1.8 3.5 7 9 7 1.6 0 3-.4 4.2-1" /><path d="M9.9 9.9a3 3 0 004.2 4.2" /></>
      : <><path d="M3 12s3.5-7 9-7 9 7 9 7-3.5 7-9 7-9-7-9-7z" /><circle cx="12" cy="12" r="3" /></>}
  </svg>
);
const Spinner = () => (
  <svg className="spin" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
    <path d="M12 3a9 9 0 019 9" /><path opacity=".35" d="M21 12a9 9 0 11-9-9" />
  </svg>
);

const emailOk = (v) => /^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(v.trim());
function pwScore(v) {
  let s = 0;
  if (v.length >= 10) s++;
  if (v.length >= 14) s++;
  if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s++;
  if (/\d/.test(v) || /[^\w\s]/.test(v)) s++;
  return Math.min(s, 4);
}
const PW_LABEL = ["Muy débil", "Débil", "Aceptable", "Buena", "Fuerte"];
const PW_COLOR = ["var(--bad)", "var(--bad)", "var(--amber)", "var(--lime)", "var(--good)"];

function Field({ id, name, label, type = "text", value, onChange, placeholder, err, autoComplete, toggle }) {
  const [show, setShow] = React.useState(false);
  const t = toggle ? (show ? "text" : "password") : type;
  return (
    <div className={"fld" + (err ? " bad" : "")}>
      <label htmlFor={id}>{label}</label>
      <div className="wrap">
        <input id={id} name={name || id} type={t} value={value} placeholder={placeholder} autoComplete={autoComplete}
          onChange={(e) => onChange(e.target.value)} />
        {toggle && (
          <button type="button" className="eye" onClick={() => setShow((s) => !s)} aria-label={show ? "Ocultar" : "Mostrar"}>
            <EyeIcon off={show} />
          </button>
        )}
      </div>
      {err && <span className="err">✕ {err}</span>}
    </div>
  );
}

const LEFT_COPY = {
  signup: {
    pill: "Empieza limpio",
    title: ["Estudiar deja", "de ser una lata."],
    sub: "Crea tu cuenta, activa la extensión de MachReach y convierte tu semestre en progreso visible.",
    mockLabel: "Flujo por extensión",
    rows: [["Cálculo I", "Canvas detectado", "+120 XP", false], ["Álgebra Lineal", "Herramientas listas", "3/4", true]],
  },
  login: {
    pill: "Vuelve a tu ritmo",
    title: ["Retoma donde", "lo dejaste."],
    sub: "Tus cursos, bloques de enfoque, notas y rankings siguen listos en un solo lugar.",
    mockLabel: "Sesión lista",
    rows: [["Cálculo I", "Racha de enfoque", "+120 XP", false], ["Álgebra Lineal", "Cursos ordenados", "3/4", true]],
  },
};

function AuthLeft({ mode }) {
  const c = LEFT_COPY[mode];
  return (
    <div className="auth-left" key={mode}>
      <div className="auth-glow" />
      <span className="auth-pill rv" style={{ "--d": "60ms" }}><i />{c.pill}</span>
      <h1 className="auth-h1">
        {c.title.map((l, i) => (
          <span className="lmask" key={i} style={{ "--d": 140 + i * 90 + "ms" }}><span>{l}</span></span>
        ))}
      </h1>
      <p className="auth-sub rv" style={{ "--d": "340ms" }}>{c.sub}</p>
      <div className="auth-chips">
        {["Focus + XP", "Quizzes IA", "Ranking"].map((t, i) => (
          <span className="auth-chip pop" key={t} style={{ "--d": 400 + i * 70 + "ms" }}><b>✓</b>{t}</span>
        ))}
      </div>
      <div className="auth-mock rv" style={{ "--d": "560ms" }}>
        <div className="auth-mockbar">
          <span className="auth-dots"><i /><i /><i /></span>
          <span className="auth-mocklabel">{c.mockLabel}</span>
        </div>
        <div className="auth-rows">
          {c.rows.map(([h, p, v, plain]) => (
            <div className="auth-row" key={h}>
              <div><h4>{h}</h4><p>{p}</p></div>
              <span className={"val" + (plain ? " plain" : "")}>{v}</span>
            </div>
          ))}
        </div>
        <div className="auth-bars">
          {[72, 46, 88, 34].map((w, i) => (
            <span key={i}><i style={{ "--w": w + "%", "--d": 700 + i * 120 + "ms" }} /></span>
          ))}
        </div>
      </div>
    </div>
  );
}

function AuthPage({ initial = "signup", live = null }) {
  const [mode, setMode] = React.useState(initial);
  const [f, setF] = React.useState({ name: "", email: "", pw: "", pw2: "" });
  const [err, setErr] = React.useState({});
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [remember, setRemember] = React.useState(true);
  const [shown, setShown] = React.useState(false);
  const set = (k) => (v) => { setF((s) => ({ ...s, [k]: v })); setErr((e) => (e[k] ? { ...e, [k]: null } : e)); };

  React.useEffect(() => { const t = setTimeout(() => setShown(true), 60); return () => clearTimeout(t); }, []);
  const swap = (m) => {
    if (live) { window.location.href = m === "signup" ? "/register" : "/login"; return; }
    setMode(m); setErr({}); setDone(false); setF({ name: "", email: "", pw: "", pw2: "" });
  };

  const score = pwScore(f.pw);
  function submit(e) {
    e.preventDefault();
    const E = {};
    if (mode === "signup" && f.name.trim().length < 2) E.name = "Escribe tu nombre";
    if (!emailOk(f.email)) E.email = "Correo no válido";
    if (mode === "signup") {
      if (f.pw.length < 10) E.pw = "Mínimo 10 caracteres";
      if (f.pw2 !== f.pw) E.pw2 = "Las contraseñas no coinciden";
    } else if (!f.pw) E.pw = "Escribe tu contraseña";
    setErr(E);
    if (Object.keys(E).length) return;
    if (live) {
      setBusy(true);
      e.currentTarget.submit();
      return;
    }
    setBusy(true);
    setTimeout(() => { setBusy(false); setDone(true); }, 1250);
  }

  const isSignup = mode === "signup";
  return (
    <div className={"auth-wrap" + (shown ? " in" : "")}>
      <div className="auth-bg" />
      <div className="auth-card">
        <AuthLeft mode={mode} />
        <div className="auth-right">
          {done ? (
            <div className="auth-done swapin">
              <div className="tick"><svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 12.5l5.2 5.2L20 7" /></svg></div>
              <h3>{isSignup ? "¡Cuenta creada!" : "¡Bienvenido de vuelta!"}</h3>
              <p>{isSignup
                ? "Te enviamos un correo a " + f.email + " para confirmarla. Después instala la extensión y listo."
                : "Sesión iniciada. Te llevamos a tu panel de hoy."}</p>
              <a className="btn btn-lg btn-primary" href="dashboard">Ir al panel <IconArrow size={18} /></a>
              <button className="auth-swap" onClick={() => swap(mode)} style={{ background: "none" }}>
                <span className="muted">Volver a </span><span style={{ color: "var(--brand-2)" }}>{isSignup ? "crear cuenta" : "iniciar sesión"}</span>
              </button>
            </div>
          ) : (
            <div className="swapin" key={mode}>
              <h2>{isSignup ? "Crea tu cuenta" : "Bienvenido de vuelta"}</h2>
              <p className="lead">{isSignup ? "Empieza a estudiar mejor en minutos." : "Inicia sesión para seguir estudiando."}</p>
              {live?.messages?.map((message, i) => (
                <div className="err" role="alert" key={i} style={{ marginBottom: 12 }}>{Array.isArray(message) ? message[1] : String(message)}</div>
              ))}
              <form className="auth-form" method="post" action={isSignup ? "/register" : "/login"} onSubmit={submit} noValidate>
                {live && <input type="hidden" name="csrf_token" value={live.csrf || ""} />}
                {live?.ref && <input type="hidden" name="ref" value={live.ref} />}
                {isSignup && <Field id="name" name="name" label="Nombre" value={f.name} onChange={set("name")} placeholder="Tu nombre" autoComplete="name" err={err.name} />}
                <Field id="email" name="email" label={isSignup ? "Correo" : "Correo electrónico"} type="email" value={f.email} onChange={set("email")} placeholder="tu@correo.com" autoComplete="username" err={err.email} />
                <Field id="pw" name="password" label="Contraseña" toggle value={f.pw} onChange={set("pw")} placeholder={isSignup ? "Mínimo 10 caracteres" : "Tu contraseña"} autoComplete={isSignup ? "new-password" : "current-password"} err={err.pw} />
                {isSignup && f.pw.length > 0 && (
                  <div>
                    <div className="pwbar">{[0, 1, 2, 3].map((i) => <i key={i} className={i < score ? "on" : ""} style={{ "--lv": PW_COLOR[score] }} />)}</div>
                    <div className="pwnote">Seguridad: {PW_LABEL[score]}</div>
                  </div>
                )}
                {isSignup && <Field id="pw2" name="password2" label="Confirmar contraseña" toggle value={f.pw2} onChange={set("pw2")} placeholder="Repite tu contraseña" autoComplete="new-password" err={err.pw2} />}
                {!isSignup && (
                  <div className="remember">
                    <label className="chk">
                      <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
                      <i><IconCheck size={13} /></i>Mantener sesión iniciada
                    </label>
                  </div>
                )}
                <button className="btn btn-lg btn-primary auth-submit" type="submit" disabled={busy}>
                  {busy ? <><Spinner /> {isSignup ? "Creando…" : "Entrando…"}</> : (isSignup ? "Crea tu cuenta" : "Iniciar sesión")}
                </button>
              </form>

              {isSignup ? (
                <>
                  <p className="auth-fine">Te enviaremos un correo para confirmar tu cuenta. La extensión se activa después de crearla.</p>
                  <p className="auth-fine">Al crear una cuenta, aceptas nuestros <a href="/terms">Términos del Servicio</a> y la <a href="/privacy">Política de Privacidad</a>.</p>
                </>
              ) : (
                <div className="auth-links">
                  <a href="/forgot-password">¿Olvidaste tu contraseña?</a>
                  <a href="/login" className="muted" style={{ color: "var(--ink-2)" }}>¿No recibiste el correo de verificación?</a>
                </div>
              )}
              <hr className="auth-hr" />
              <p className="auth-swap">
                {isSignup ? "¿Ya tienes cuenta? " : "¿No tienes cuenta? "}
                <button type="button" onClick={() => swap(isSignup ? "login" : "signup")}>
                  {isSignup ? "Iniciar sesión" : "Regístrate gratis"}
                </button>
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { AuthPage, AuthLeft, Field });
