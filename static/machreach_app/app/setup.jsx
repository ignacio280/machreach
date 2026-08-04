/* Setup — onboarding académico obligatorio (/student/setup).
   The design's markup, wired to the academic API: countries, universities and
   majors all come from the server, and finishing posts the profile. */
const SuSearch = (p) => <Icon {...p}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4 4" /></Icon>;
const SU_STEPS = ["País", "Universidad", "Carrera"];

/* Design-preview fallbacks. The live page never uses these — it lists whatever
   the server has seeded. */
const SU_COUNTRIES = [
  { iso: "MX", fl: "🇲🇽", name: "México" },
  { iso: "ES", fl: "🇪🇸", name: "España" },
  { iso: "CO", fl: "🇨🇴", name: "Colombia" },
  { iso: "AR", fl: "🇦🇷", name: "Argentina" },
  { iso: "CL", fl: "🇨🇱", name: "Chile" },
  { iso: "PE", fl: "🇵🇪", name: "Perú" },
  { iso: "US", fl: "🇺🇸", name: "Estados Unidos" },
  { iso: "EC", fl: "🇪🇨", name: "Ecuador" },
];
const SU_UNIVS = [
  { id: 1, name: "Pontificia Universidad Católica de Chile", city: "Santiago", tag: "PUC" },
  { id: 2, name: "Universidad de Chile", city: "Santiago", tag: "UCH" },
  { id: 3, name: "Universidad Técnica Federico Santa María", city: "Valparaíso", tag: "USM" },
];
const SU_MAJORS = [
  { id: 101, name: "Ingeniería Civil", area: "Ingeniería" },
  { id: 102, name: "Medicina", area: "Ciencias de la salud" },
  { id: 103, name: "Derecho", area: "Ciencias sociales" },
];

/* The catalogue endpoints answer on every keystroke, so debounce and keep the
   last good list on screen instead of flashing an empty state. */
function useCatalogue(fetcher, deps, live) {
  const [rows, setRows] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  React.useEffect(() => {
    if (!live) return undefined;
    let alive = true;
    setLoading(true);
    const timer = setTimeout(() => {
      fetcher()
        .then((result) => { if (alive) { setRows(result || []); setLoading(false); } })
        .catch(() => { if (alive) { setRows([]); setLoading(false); } });
    }, 180);
    return () => { alive = false; clearTimeout(timer); };
  }, deps);
  return [rows, loading];
}

const suInitials = (name) => (name || "?")
  .split(/\s+/).filter((w) => w.length > 2).slice(0, 2)
  .map((w) => w[0]).join("").toUpperCase() || (name || "?").slice(0, 2).toUpperCase();

function SetupPage({ initialStep = 0, state = "results", live = false, csrf = "" }) {
  const [step, setStep] = React.useState(initialStep);
  const [country, setCountry] = React.useState(initialStep > 0 && !live ? SU_COUNTRIES[4] : null);
  const [univ, setUniv] = React.useState(initialStep > 1 && !live ? SU_UNIVS[0] : null);
  const [major, setMajor] = React.useState(initialStep > 2 && !live ? SU_MAJORS[0] : null);
  const [cq, setCq] = React.useState("");
  const [uq, setUq] = React.useState("");
  const [mq, setMq] = React.useState("");
  const [err, setErr] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const empty = state === "empty";

  const [liveCountries] = useCatalogue(
    () => fetch("/api/academic/countries").then((r) => r.json()).then((b) => (b.countries || []).map((c) => ({
      iso: c.iso_code, name: c.name, fl: c.flag_emoji || "🏳️",
    }))),
    [live], live);
  const [liveUnivs, univsLoading] = useCatalogue(
    () => (country
      ? fetch(`/api/academic/universities?country=${encodeURIComponent(country.iso)}&q=${encodeURIComponent(uq)}`)
        .then((r) => r.json()).then((b) => (b.universities || []).map((u) => ({
          id: u.id, name: u.name, city: u.short_name || u.city || "", tag: u.short_name || suInitials(u.name),
        })))
      : Promise.resolve([])),
    [live, country?.iso, uq], live);
  const [liveMajors, majorsLoading] = useCatalogue(
    () => (univ
      ? fetch(`/api/academic/majors?university_id=${univ.id}&q=${encodeURIComponent(mq)}`)
        .then((r) => r.json()).then((b) => (b.majors || []).map((m) => ({
          id: m.id, name: m.name, area: m.university_id ? "Carrera de tu universidad" : "Carrera general",
        })))
      : Promise.resolve([])),
    [live, univ?.id, mq], live);

  const countries = live
    ? liveCountries.filter((c) => c.name.toLowerCase().includes(cq.toLowerCase()))
    : SU_COUNTRIES.filter((c) => c.name.toLowerCase().includes(cq.toLowerCase()));
  const univs = live ? liveUnivs : (empty ? [] : SU_UNIVS);
  const majors = live ? liveMajors : (empty ? [] : SU_MAJORS);

  const finish = async () => {
    if (!live) return setStep(3);
    setSaving(true);
    try {
      const response = await fetch("/api/academic/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ country_iso: country.iso, university_id: univ.id, major_id: major.id }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || "No pudimos guardar tu perfil académico.");
      setStep(3);
    } catch (error) {
      setErr(error.message);
    } finally {
      setSaving(false);
    }
  };

  const next = () => {
    if (step === 0 && !country) return setErr("Elige tu país para continuar.");
    if (step === 1 && !univ) return setErr("Elige tu universidad para continuar.");
    if (step === 2 && !major) return setErr("Elige tu carrera para continuar.");
    setErr("");
    if (step === 2) return finish();
    setStep(step + 1);
  };

  return (
    <div className="su-page">
      <div className="su-shell">
        <div className="su-steps">
          {SU_STEPS.map((s, i) => (
            <React.Fragment key={s}>
              <div className={"su-dot" + (i === step ? " on" : "") + (i < step ? " done" : "")}>
                <span className="su-num">{i < step ? <IconCheck size={15} /> : i + 1}</span>
                <span className="su-name">{s}</span>
              </div>
              {i < 2 && <span className={"su-bar" + (i < step ? " on" : "")} />}
            </React.Fragment>
          ))}
        </div>

        <div className="su-card">
          {step < 3 ? (
            <React.Fragment>
              <span className="su-kicker">Paso {step + 1} de 3 · obligatorio</span>
              <h1 className="su-h1">{step === 0 ? "¿Desde dónde estudias?" : step === 1 ? "¿En qué universidad?" : "¿Qué carrera estudias?"}</h1>
              <p className="su-sub">
                {step === 0
                  ? "Tu país define en qué ranking compites y con quién se comparan tus horas de estudio."
                  : step === 1
                  ? "Compartirás tabla de posiciones, apuntes y grupos de estudio con gente de tu campus."
                  : "Con tu carrera armamos tu plan de estudio y te recomendamos apuntes de materias que llevas."}
              </p>

              {step === 0 && (
                <div className="su-panel">
                  <label className="su-label" htmlFor="su-cq">Buscar país</label>
                  <div className="su-field">
                    <SuSearch size={17} />
                    <input id="su-cq" className="su-input" placeholder="Chile, México, España…" value={cq} onChange={(e) => setCq(e.target.value)} autoComplete="off" />
                  </div>
                  <div className="su-grid" id="su-country-list" role="listbox" aria-live="polite" aria-label="Países">
                    {countries.map((c) => (
                      <button key={c.iso} role="option" aria-selected={country?.iso === c.iso} className={"su-chip" + (country?.iso === c.iso ? " sel" : "")} onClick={() => { setCountry(c); setUniv(null); setMajor(null); setErr(""); }}>
                        <span className="fl">{c.fl}</span>{c.name}
                        {country?.iso === c.iso && <span className="tick"><IconCheck size={16} /></span>}
                      </button>
                    ))}
                  </div>
                  {live && !countries.length && <div className="su-empty"><b>Sin resultados</b>Revisa la escritura del país.</div>}
                </div>
              )}

              {step === 1 && (
                <div className="su-panel">
                  <label className="su-label" htmlFor="su-uq">Universidades en {country?.name}</label>
                  <div className="su-field">
                    <SuSearch size={17} />
                    <input id="su-uq" className="su-input" placeholder="Busca por nombre o siglas…" value={uq} onChange={(e) => setUq(e.target.value)} autoComplete="off" />
                  </div>
                  <div className="su-list" id="su-univ-list" role="listbox" aria-live="polite" aria-label="Universidades">
                    {univs.length ? univs.map((u) => (
                      <button key={u.id} role="option" aria-selected={univ?.id === u.id} className={"su-row" + (univ?.id === u.id ? " sel" : "")} onClick={() => { setUniv(u); setMajor(null); setErr(""); }}>
                        <span className="av">{(u.tag || "").slice(0, 2)}</span>
                        <span className="tx"><b>{u.name}</b><span>{u.city}</span></span>
                        <span className="tag">{univ?.id === u.id ? "Elegida" : u.tag}</span>
                      </button>
                    )) : (
                      <div className="su-empty"><b>{univsLoading ? "Buscando…" : "Sin resultados"}</b>{univsLoading ? "Un segundo." : "Prueba con las siglas o revisa la escritura."}</div>
                    )}
                  </div>
                  <p className="su-hint">¿No aparece tu universidad? Escríbenos y la agregamos en 24 h.</p>
                </div>
              )}

              {step === 2 && (
                <div className="su-panel">
                  <label className="su-label" htmlFor="su-mq">Carreras en {univ?.tag}</label>
                  <div className="su-field">
                    <SuSearch size={17} />
                    <input id="su-mq" className="su-input" placeholder="Busca tu carrera…" value={mq} onChange={(e) => setMq(e.target.value)} autoComplete="off" />
                  </div>
                  <div className="su-list" id="su-major-list" role="listbox" aria-live="polite" aria-label="Carreras">
                    {majors.length ? majors.map((m) => (
                      <button key={m.id} role="option" aria-selected={major?.id === m.id} className={"su-row" + (major?.id === m.id ? " sel" : "")} onClick={() => { setMajor(m); setErr(""); }}>
                        <span className="av"><IconBook size={16} /></span>
                        <span className="tx"><b>{m.name}</b><span>{m.area}</span></span>
                        {major?.id === m.id && <span className="tag">Elegida</span>}
                      </button>
                    )) : (
                      <div className="su-empty"><b>{majorsLoading ? "Buscando…" : "Sin resultados"}</b>{majorsLoading ? "Un segundo." : "Escribe el nombre completo de tu carrera."}</div>
                    )}
                  </div>
                  <p className="su-hint">Podrás cambiarla después en Ajustes.</p>
                </div>
              )}

              <div className="su-foot">
                {err ? <span className="su-err" id="su-err" role="alert" aria-live="assertive">{err}</span> : <span className="su-of">{step === 2 ? "Último paso" : `Falta${step === 0 ? "n" : ""} ${2 - step} paso${step === 0 ? "s" : ""}`}</span>}
                <button className="btn btn-ghost" disabled={step === 0 || saving} onClick={() => { setErr(""); setStep(step - 1); }}>Atrás</button>
                <button className="btn btn-primary" onClick={next} disabled={saving}>{saving ? "Guardando…" : step === 2 ? "Terminar" : "Siguiente"}<IconArrow size={17} /></button>
              </div>
            </React.Fragment>
          ) : (
            <div className="su-done">
              <div className="ring"><IconCheck size={34} /></div>
              <h1 className="su-h1">Todo listo, {univ?.tag} te espera</h1>
              <p className="su-sub" style={{ margin: "0 auto" }}>Ya apareces en el ranking de tu universidad y tu plan de estudio está armado.</p>
              <div className="su-recap" style={{ textAlign: "left" }}>
                <div><span className="k">País</span>{country?.fl} {country?.name}</div>
                <div><span className="k">Universidad</span>{univ?.name}</div>
                <div><span className="k">Carrera</span>{major?.name}</div>
              </div>
              <div className="su-foot">
                <span className="su-of">Puedes editarlo en Ajustes</span>
                <a className="btn btn-primary btn-lg" href={live ? "/student" : "MachReach Dashboard.html"}>Entrar a MachReach<IconArrow size={18} /></a>
              </div>
            </div>
          )}
        </div>

        <p className="su-note">Necesitamos estos tres datos antes de darte acceso. No los compartimos con nadie.</p>
      </div>
    </div>
  );
}

Object.assign(window, { SetupPage, PubTop: window.PubTop });
