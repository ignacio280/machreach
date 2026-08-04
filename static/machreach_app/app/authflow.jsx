/* Auth side-flows — verificar correo, recuperar contraseña.
   The design's markup, wired to the Flask endpoints: every view that submits
   posts a real form (with CSRF) rather than flipping client state, and the
   server decides which view opens. */
const AfMail = (p) => <Icon {...p}><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="M3.5 7l8.5 6 8.5-6" /></Icon>;
const AfKey = (p) => <Icon {...p}><circle cx="8" cy="12" r="4" /><path d="M12 12h9M18 12v3.5M15.5 12v3" /></Icon>;

/* Flask flashes arrive as nested pairs — ("message", ("error", text)). */
const afFlashText = (m) => {
  while (Array.isArray(m) && m.length) m = m[m.length - 1];
  return String(m == null ? "" : m);
};
const AfFlashes = ({ messages }) => (messages || []).map((message, i) => (
  <div className="af-flash warn" role="alert" key={i}>
    <IconTimer size={17} /><span>{afFlashText(message)}</span>
  </div>
));

function VerifyPendingPage({ state = "created", email = "", live = null }) {
  const [value, setValue] = React.useState(email);
  const [cool, setCool] = React.useState(state === "sent" ? 45 : 0);
  React.useEffect(() => {
    if (!cool) return;
    const t = setTimeout(() => setCool((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cool]);

  const sent = state === "sent";
  const delayed = state === "delayed";
  const created = state === "created";
  const head = created ? "¡Cuenta creada!" : delayed ? "El correo va en camino" : "Verifica tu correo para continuar";
  const sub = delayed
    ? "El envío está tardando más de lo normal. Tu cuenta ya está creada y seguimos reintentando."
    : created
    ? "Te enviamos un enlace de verificación. Ábrelo y entras directo a MachReach."
    : sent
    ? "Un enlace nuevo va en camino. Revisa tu bandeja y también el spam."
    : "Tu correo todavía no está verificado. Abre el enlace que te enviamos para poder iniciar sesión.";

  return (
    <div className="af-page">
      <div className="af-shell">
        <div className="af-card">
          <div className={"af-ico" + (delayed ? " warn" : created ? " good" : "")}>{created ? <IconCheck size={30} /> : <AfMail size={30} />}</div>
          <h1>{head}</h1>
          <p className="af-sub">{sub}</p>
          <AfFlashes messages={live?.messages} />
          {(sent || delayed) && (
            <div className={"af-flash af-swap" + (delayed ? " warn" : "")}>
              {delayed ? <IconTimer size={17} /> : <IconCheck size={17} />}
              <span>{delayed ? "No hace falta crear otra cuenta. Puedes reintentar el envío aquí abajo." : "Enlace de verificación reenviado."}</span>
            </div>
          )}
          {!!email && <div className="af-mail"><span className="k">Enviado a</span><span className="v">{email}</span></div>}
          <ol className="af-steps">
            <li><span className="n">1</span><span>Abre el correo de <b>MachReach</b></span></li>
            <li><span className="n">2</span><span>Toca <b>Verificar correo</b></span></li>
            <li><span className="n">3</span><span>Inicia sesión y empieza a estudiar</span></li>
          </ol>
          <form className="af-form" method="post" action="/resend-verification">
            {live && <input type="hidden" name="csrf_token" value={live.csrf || ""} />}
            <div className="af-row">
              <div>
                <label className="af-label" htmlFor="af-em">¿No llegó? Reenvíalo</label>
                <input id="af-em" name="email" className="af-in" type="email" value={value} onChange={(e) => setValue(e.target.value)} placeholder="tu@correo.com" required />
              </div>
              <button className="btn btn-primary" type="submit" disabled={cool > 0}>{cool > 0 ? `Espera ${cool}s` : "Reenviar"}</button>
            </div>
          </form>
          <div className="af-foot">¿Ya lo verificaste? <a href="/login">Inicia sesión</a></div>
        </div>
        <p className="af-note">Los enlaces caducan en 24 horas. Si no llega nada, escríbenos a hola@machreach.com.</p>
      </div>
    </div>
  );
}

function ForgotPasswordPage({ state = "form", email = "", live = null, token = "" }) {
  const [value, setValue] = React.useState(email);
  const [pw, setPw] = React.useState("");
  const [pw2, setPw2] = React.useState("");
  const [err, setErr] = React.useState("");
  const [view, setView] = React.useState(state);
  React.useEffect(() => { setView(state); setErr(""); }, [state]);

  const csrf = live ? <input type="hidden" name="csrf_token" value={live.csrf || ""} /> : null;
  const checkEmail = (e) => {
    if (!/^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(value.trim())) { e.preventDefault(); setErr("Escribe un correo válido."); }
  };
  const checkPasswords = (e) => {
    if (pw.length < 10) { e.preventDefault(); return setErr("Usa al menos 10 caracteres."); }
    if (pw !== pw2) { e.preventDefault(); return setErr("Las contraseñas no coinciden."); }
  };

  return (
    <div className="af-page">
      <div className="af-shell">
        <div className="af-card af-swap" key={view}>
          {view === "form" && (
            <>
              <div className="af-ico"><AfKey size={30} /></div>
              <h1>Recupera tu contraseña</h1>
              <p className="af-sub">Escribe el correo de tu cuenta y te mandamos un enlace para crear una nueva.</p>
              <form className="af-form" method="post" action="/forgot-password" onSubmit={checkEmail} noValidate>
                {csrf}
                <div>
                  <label className="af-label" htmlFor="af-fe">Correo de tu cuenta</label>
                  <input id="af-fe" name="email" className="af-in" type="email" value={value} onChange={(e) => setValue(e.target.value)} placeholder="tu@correo.com" autoComplete="email" />
                </div>
                {err && <span className="af-err">{err}</span>}
                <button className="btn btn-primary" type="submit">Enviar enlace<IconArrow size={17} /></button>
              </form>
              <div className="af-foot">¿Ya la recordaste? <a href="/login">Inicia sesión</a></div>
            </>
          )}
          {view === "sent" && (
            <>
              <div className="af-ico good"><AfMail size={30} /></div>
              <h1>Revisa tu correo</h1>
              <p className="af-sub">Si existe una cuenta con ese correo, el enlace para restablecer tu contraseña ya va en camino.</p>
              {!!email && <div className="af-mail"><span className="k">Enviado a</span><span className="v">{email}</span></div>}
              <ol className="af-steps">
                <li><span className="n">1</span><span>Abre el correo de <b>MachReach</b></span></li>
                <li><span className="n">2</span><span>Toca <b>Restablecer contraseña</b></span></li>
                <li><span className="n">3</span><span>Elige una nueva y vuelve a entrar</span></li>
              </ol>
              <div className="af-foot"><a href="/forgot-password">Usar otro correo</a></div>
              <p className="af-note">El enlace caduca en 1 hora y solo funciona una vez.</p>
            </>
          )}
          {view === "reset" && (
            <>
              <div className="af-ico"><AfKey size={30} /></div>
              <h1>Elige una nueva contraseña</h1>
              <p className="af-sub">Mínimo 10 caracteres. Usa algo que no repitas en otras cuentas.</p>
              <AfFlashes messages={live?.messages} />
              <form className="af-form" method="post" action={`/reset-password/${token}`} onSubmit={checkPasswords} noValidate>
                {csrf}
                <div>
                  <label className="af-label" htmlFor="af-p1">Nueva contraseña</label>
                  <input id="af-p1" name="password" className="af-in" type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="••••••••••" autoComplete="new-password" />
                </div>
                <div>
                  <label className="af-label" htmlFor="af-p2">Repítela</label>
                  <input id="af-p2" name="password2" className="af-in" type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} placeholder="••••••••••" autoComplete="new-password" />
                </div>
                {err && <span className="af-err">{err}</span>}
                <button className="btn btn-primary" type="submit">Guardar contraseña<IconArrow size={17} /></button>
              </form>
            </>
          )}
          {view === "expired" && (
            <>
              <div className="af-ico warn"><IconTimer size={30} /></div>
              <h1>Ese enlace ya caducó</h1>
              <p className="af-sub">Los enlaces duran 1 hora por seguridad. Pide uno nuevo y lo mandamos al instante.</p>
              <div className="af-flash warn"><IconTimer size={17} /><span>Tu contraseña actual sigue funcionando mientras tanto.</span></div>
              <form className="af-form" method="post" action="/forgot-password" onSubmit={checkEmail} noValidate>
                {csrf}
                <div>
                  <label className="af-label" htmlFor="af-fe2">Correo de tu cuenta</label>
                  <input id="af-fe2" name="email" className="af-in" type="email" value={value} onChange={(e) => setValue(e.target.value)} placeholder="tu@correo.com" autoComplete="email" />
                </div>
                {err && <span className="af-err">{err}</span>}
                <button className="btn btn-primary" type="submit">Enviar otro enlace<IconArrow size={17} /></button>
              </form>
            </>
          )}
          {view === "ok" && (
            <>
              <div className="af-ico good"><IconCheck size={30} /></div>
              <h1>Contraseña actualizada</h1>
              <p className="af-sub">Cerramos las demás sesiones abiertas. Entra de nuevo con tu contraseña nueva.</p>
              <div className="af-form"><a className="btn btn-primary btn-lg" href="/login">Iniciar sesión<IconArrow size={18} /></a></div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { VerifyPendingPage, ForgotPasswordPage });
