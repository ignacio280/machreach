/* App shell: extra icons, sidebar, topbar, mobile tabbar */
const IconHome = (p) => <Icon {...p}><path d="M4 11l8-7 8 7M6.5 9.5V20h11V9.5M10 20v-5h4v5" /></Icon>;
const IconCal = (p) => <Icon {...p}><rect x="3.5" y="5" width="17" height="15" rx="3" /><path d="M3.5 10h17M8 3v4M16 3v4" /></Icon>;
const IconGrid = (p) => <Icon {...p}><rect x="4" y="4" width="6.5" height="6.5" rx="2" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="2" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="2" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="2" /></Icon>;
const IconStore = (p) => <Icon {...p}><path d="M4 9l1.5-5h13L20 9M4 9h16v10a1 1 0 01-1 1H5a1 1 0 01-1-1V9zM4 9a3 3 0 004 0 3 3 0 004 0 3 3 0 004 0 3 3 0 004 0" /></Icon>;
const IconBell = (p) => <Icon {...p}><path d="M18 15V10a6 6 0 10-12 0v5l-2 3h16l-2-3zM10 21h4" /></Icon>;
const IconMore = (p) => <Icon {...p}><circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" /></Icon>;
const IconGear = (p) => <Icon {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 13.5a7.7 7.7 0 000-3l1.7-1.3-1.9-3.3-2 .8a7.6 7.6 0 00-2.6-1.5L14.3 3h-3.8l-.3 2.2a7.6 7.6 0 00-2.6 1.5l-2-.8L3.7 9.2l1.7 1.3a7.7 7.7 0 000 3l-1.7 1.3 1.9 3.3 2-.8a7.6 7.6 0 002.6 1.5l.3 2.2h3.8l.3-2.2a7.6 7.6 0 002.6-1.5l2 .8 1.9-3.3-1.7-1.3z" /></Icon>;
const IconLogout = (p) => <Icon {...p}><path d="M14.5 8V5.5a1.5 1.5 0 00-1.5-1.5H6a1.5 1.5 0 00-1.5 1.5v13A1.5 1.5 0 006 20h7a1.5 1.5 0 001.5-1.5V16M10 12h9.5M17 9l3 3-3 3" /></Icon>;

const AV = ["#FF8AA5", "#8DACFF", "#B29BFF", "#5DE3B0", "#FFB37A", "#9CD9F0", "#FFC857"];

/* The topbar sets backdrop-filter, which makes it the containing block for any
   position:fixed descendant — screen-anchored overlays have to leave it. */
function Overlay({ children }) {
  if (typeof document === "undefined" || !ReactDOM.createPortal) return children;
  return ReactDOM.createPortal(children, document.body);
}
const SHELL_DATA = window.__MACHREACH_APP__ || window.__MACHREACH_DASHBOARD__ || {};
const SHELL_EN = SHELL_DATA.lang === "en";

function Ring({ pct, size = 34, sw = 5, color = "var(--brand)", label }) {
  const r = (size - sw) / 2 - 1, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ overflow: "visible" }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--paper-2)" strokeWidth={sw} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c - (c * pct) / 100} transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dashoffset 1.2s var(--spring)" }} />
      {label && <text x="50%" y="53%" textAnchor="middle" dominantBaseline="middle" style={{ font: "800 10px var(--font-display)", fill: "var(--ink)" }}>{label}</text>}
    </svg>
  );
}

const NAV_MAIN = [
  { id: "home", label: SHELL_EN ? "Home" : "Inicio", Ic: IconHome, href: "/student" },
  { id: "focus", label: SHELL_EN ? "Focus" : "Enfoque", Ic: IconTimer, tick: SHELL_DATA.focus_tick || null, href: "/student/focus" },
  { id: "plan", label: "Plan", Ic: IconCal, plusOnly: true, href: "/student/planner" },
  { id: "courses", label: SHELL_EN ? "My courses" : "Mis cursos", Ic: IconBook, href: "/student/courses" },
  { id: "tools", label: SHELL_EN ? "Tools" : "Herramientas", Ic: IconBrain, href: "/student/quizzes" },
  { id: "notas", label: SHELL_EN ? "Grades" : "Notas", Ic: IconGrid, href: "/student/gpa" },
];
const NAV_SOCIAL = [
  { id: "rank", label: SHELL_EN ? "Leaderboard" : "Ranking", Ic: IconTrophy, href: "/student/leaderboard" },
  { id: "friends", label: SHELL_EN ? "Friends" : "Amigos", Ic: IconPeople, tick: SHELL_DATA.friend_tick || null, href: "/student/friends" },
  { id: "reviews", label: "Reviews", Ic: IconStar, href: "/student/reviews" },
  { id: "stats", label: SHELL_EN ? "Analytics" : "Analíticas", Ic: IconChart, plusOnly: true, href: "/student/analytics" },
  { id: "shop", label: SHELL_EN ? "Shop" : "Tienda", Ic: IconStore, href: "/student/shop" },
];

function NavItem({ n, active, plus }) {
  const locked = n.plusOnly && !plus;
  return (
    <a href={n.href || "#"} data-tour={n.id} className={"nv" + (active === n.id ? " on" : "")}>
      <n.Ic size={18} />{n.label}
      {locked ? <span className="lockic"><IconLock size={14} /></span> : n.tick ? <span className="tick">{n.tick}</span> : null}
    </a>
  );
}

function Sidebar({ active = "home", plus = false }) {
  return (
    <aside className="side">
      <a href="/" className="side-logo"><span className="logo-mark"><Mark size={30} uid="side" live /></span><Wordmark size={19} /></a>
      <nav className="side-nav">
        <div className="side-group">{SHELL_EN ? "Study" : "Estudiar"}</div>
        {NAV_MAIN.map((n) => <NavItem key={n.id} n={n} active={active} plus={plus} />)}
        <div className="side-group">{SHELL_EN ? "Compete" : "Competir"}</div>
        {NAV_SOCIAL.map((n) => <NavItem key={n.id} n={n} active={active} plus={plus} />)}
      </nav>
      <div className="side-foot">
        {plus ? (
          <div className="plus-card active">
            <h4>{SHELL_EN ? "Plus active" : "Plus activo"} <PlusBadge small /></h4>
            <p>{SHELL_DATA.plus_copy || (SHELL_EN ? "Your Plus tools are ready." : "Tus herramientas Plus están listas.")}</p>
            <a href="/student/shop" className="btn btn-ghost btn-sm">{SHELL_EN ? "Manage plan" : "Gestionar plan"}</a>
          </div>
        ) : (
          <div className="plus-card">
            <h4>MachReach Plus</h4>
            <p>{SHELL_EN ? "Smart plan, analytics and more study tools." : "Plan inteligente, analíticas y más herramientas de estudio."}</p>
            <a href="/student/shop" className="btn btn-primary btn-sm">{SHELL_EN ? "Unlock" : "Desbloquear"}</a>
          </div>
        )}
      </div>
    </aside>
  );
}

/* ---- Background AI generation ------------------------------------------
   Quiz and flashcard generation runs in the worker, so it keeps going while
   the student moves around the app. This watcher rides along in the topbar
   (every page renders one) and reports the result wherever they end up.
   The pending flag lives in sessionStorage so a page load mid-job still
   knows a result is owed — otherwise a reload would swallow the toast. */
const GEN_JOBS = [
  {
    key: "quiz",
    url: "/api/student/quizzes/generate/status",
    href: "/student/quizzes",
    icon: "🧠",
    done: SHELL_EN ? "Your quiz is ready" : "Tu quiz está listo",
    failed: SHELL_EN ? "Quiz generation failed" : "No se pudo generar el quiz",
    cta: SHELL_EN ? "Open quizzes" : "Ver quizzes",
  },
  {
    key: "cards",
    url: "/api/student/flashcards/generate/status",
    href: "/student/flashcards",
    icon: "🗃",
    done: SHELL_EN ? "Your flashcards are ready" : "Tus flashcards están listas",
    failed: SHELL_EN ? "Flashcard generation failed" : "No se pudieron generar las flashcards",
    cta: SHELL_EN ? "Open flashcards" : "Ver flashcards",
  },
];
const GEN_FLAG = (key) => "mr_gen_pending_" + key;

function markGenerationQueued(key) {
  try { sessionStorage.setItem(GEN_FLAG(key), "1"); } catch (e) { /* private mode */ }
  dispatchEvent(new CustomEvent("mr-generation-queued"));
}

function GenerationWatcher() {
  const [notes, setNotes] = React.useState([]);
  React.useEffect(() => {
    if (!SHELL_DATA.live) return undefined;
    let alive = true;
    let timer = 0;
    const wasPending = (key) => {
      try { return sessionStorage.getItem(GEN_FLAG(key)) === "1"; } catch (e) { return false; }
    };
    const clearPending = (key) => {
      try { sessionStorage.removeItem(GEN_FLAG(key)); } catch (e) { /* private mode */ }
    };
    const poll = async () => {
      let running = false;
      for (const job of GEN_JOBS) {
        let status = "idle";
        try {
          const response = await fetch(job.url, { credentials: "same-origin" });
          if (!response.ok) continue;
          status = (await response.json()).status || "idle";
        } catch (e) { continue; }
        if (status === "queued" || status === "running") {
          try { sessionStorage.setItem(GEN_FLAG(job.key), "1"); } catch (e) { /* private mode */ }
          running = true;
        } else if (wasPending(job.key)) {
          clearPending(job.key);
          // Anything other than done/error (a cleared or expired job record)
          // is not a result worth interrupting the student for.
          if (status === "done" || status === "error") {
            setNotes((current) => [...current, { id: job.key + Date.now(), job, ok: status === "done" }]);
          }
        }
      }
      if (!alive) return;
      // Idle costs one request per page load; only an in-flight job keeps polling.
      if (running) timer = setTimeout(poll, 5000);
    };
    const kick = () => { clearTimeout(timer); poll(); };
    poll();
    addEventListener("mr-generation-queued", kick);
    return () => { alive = false; clearTimeout(timer); removeEventListener("mr-generation-queued", kick); };
  }, []);
  if (!notes.length) return null;
  return (
    <Overlay><div className="gen-toasts" role="status" aria-live="polite">
      {notes.map((note) => (
        <div className={"gen-toast" + (note.ok ? "" : " bad")} key={note.id}>
          <span className="gen-ic">{note.ok ? note.job.icon : "⚠️"}</span>
          <div className="gen-b">
            <b>{note.ok ? note.job.done : note.job.failed}</b>
            {note.ok && <a href={note.job.href}>{note.job.cta}</a>}
          </div>
          <button type="button" aria-label={SHELL_EN ? "Dismiss" : "Cerrar"}
            onClick={() => setNotes((current) => current.filter((n) => n.id !== note.id))}><IconClose size={14} /></button>
        </div>
      ))}
    </div></Overlay>
  );
}

/* A block started on the focus page keeps running while the student walks
   around the app. This floats bottom-right so the running block is impossible
   to lose track of — and stays out of the way on the focus page itself, where
   the real timer is already on screen. */
/* Was the last MachReach page closed rather than navigated away from? The old
   heartbeat-gap guess (no stamp for 15s = closed) resumed the block when the
   app was closed and reopened inside those 15 seconds. Three signals now
   decide, and only one of them is a guess:
   - sessionStorage marker: survives navigation and reload in this tab, dies
     with the tab. Present = this tab was already on MachReach, so keep going.
   - BroadcastChannel ping: a tab with no marker asks whether any other
     MachReach tab is alive. Silence = the app really was closed — the block
     resets even if the reopen came one second later.
   - heartbeat stamp (legacy, still written every 3s): the fallback verdict
     where BroadcastChannel does not exist, and the tell for a marker the
     browser resurrected hours later ("reopen closed tab", session restore).
   Shared through `window` because shell.jsx and focus.jsx ship in the same
   bundle: whichever module runs first must make the one and only reading,
   or the second would see a marker the first had just written. */
function mrFocusPresence() {
  if (window.__mrFocusPresence) return window.__mrFocusPresence;
  const state = { hadTab: true, known: true, abandoned: false, channel: null, verdict: null };
  window.__mrFocusPresence = state;
  try {
    state.hadTab = sessionStorage.getItem("mr_focus_tab_v1") === "1";
    sessionStorage.setItem("mr_focus_tab_v1", "1");
  } catch (e) { state.hadTab = true; }   // cannot tell: never wipe a block on a guess
  let store = null;
  try { store = JSON.parse(localStorage.getItem("mr_focus_timer_v1") || "null"); } catch (e) { store = null; }
  const heartbeatStale = () => {
    try {
      const last = Number(localStorage.getItem("mr_focus_alive_v1") || 0);
      return !!last && Date.now() - last > 15000;
    } catch (e) { return false; }
  };
  if (typeof BroadcastChannel !== "undefined") {
    try {
      state.channel = new BroadcastChannel("mr_focus_presence_v1");
      state.channel.addEventListener("message", (event) => {
        if (event.data === "ping") state.channel.postMessage("pong");
      });
    } catch (e) { state.channel = null; }
  }
  const running = !!(store && store.running);
  if (!running || state.hadTab) {
    // A marked tab still resets when the heartbeat is long dead: that marker
    // came out of a tab the browser restored, not one that stayed open.
    state.abandoned = running && state.hadTab && heartbeatStale();
    state.verdict = Promise.resolve(state.abandoned);
    return state;
  }
  // No marker, block running: a reopen after closing, or a second tab while
  // the app is open elsewhere. Only a live tab can tell those apart.
  state.known = false;
  state.verdict = new Promise((resolve) => {
    const settle = (abandoned) => { state.known = true; state.abandoned = abandoned; resolve(abandoned); };
    if (!state.channel) return settle(heartbeatStale());
    const timer = setTimeout(() => settle(true), 350);
    state.channel.addEventListener("message", (event) => {
      if (event.data === "pong") { clearTimeout(timer); settle(false); }
    });
    state.channel.postMessage("ping");
  });
  return state;
}
const SHELL_FOCUS_PRESENCE = mrFocusPresence();

/* Acting on the verdict: hiding the float was not enough — the record still
   said "running", so the next page resumed the block. The decision has to be
   written down, not re-derived. */
SHELL_FOCUS_PRESENCE.verdict.then((abandoned) => {
  if (!abandoned) return;
  try {
    const raw = localStorage.getItem("mr_focus_timer_v1");
    if (!raw) return;
    const state = JSON.parse(raw);
    if (!state || !state.running) return;
    const pending = Array.isArray(state.pending) ? state.pending : [];
    if (!pending.length) {
      localStorage.removeItem("mr_focus_timer_v1");
      return;
    }
    // Finished blocks are still owed their XP, so the record survives with the
    // running block stripped out of it. Durations mirror FOCUS_MODES.
    const full = state.mode === "custom" ? 50 * 60 : 25 * 60;
    localStorage.setItem("mr_focus_timer_v1", JSON.stringify({
      ...state, running: false, endsAt: 0, phase: "work", round: 1,
      phaseId: "", left: full,
    }));
  } catch (e) { /* private mode: nothing to repair */ }
});

function FocusFloat() {
  const [left, setLeft] = React.useState(0);
  const [phase, setPhase] = React.useState("work");
  // Every page holding the float is a page that is open, so it keeps the
  // heartbeat alive — that is what makes navigation not look like a close.
  React.useEffect(() => {
    const beat = () => {
      try { localStorage.setItem("mr_focus_alive_v1", String(Date.now())); } catch (e) { /* private mode */ }
    };
    beat();
    const timer = setInterval(beat, 3000);
    return () => clearInterval(timer);
  }, []);
  React.useEffect(() => {
    if (location.pathname === "/student/focus") return undefined;
    const read = () => {
      let state = null;
      try { state = JSON.parse(localStorage.getItem("mr_focus_timer_v1") || "null"); } catch (e) { state = null; }
      if (!state || !state.running || !state.endsAt) return setLeft(0);
      // A record left behind by a closed tab is not a running timer. Until the
      // presence verdict is in (350ms at worst) the float stays hidden.
      if (!SHELL_FOCUS_PRESENCE.known || SHELL_FOCUS_PRESENCE.abandoned) return setLeft(0);
      setPhase(state.phase === "break" ? "break" : "work");
      setLeft(Math.max(0, Math.ceil((state.endsAt - Date.now()) / 1000)));
    };
    read();
    const timer = setInterval(read, 1000);
    return () => clearInterval(timer);
  }, []);
  if (!left) return null;
  const mm = String(Math.floor(left / 60)).padStart(2, "0");
  const ss = String(left % 60).padStart(2, "0");
  const label = phase === "break"
    ? (SHELL_EN ? "Break" : "Descanso")
    : (SHELL_EN ? "Focus block" : "Bloque de enfoque");
  return (
    <Overlay>
      <a href="/student/focus" className={"focus-float" + (phase === "break" ? " brk" : "")}
        title={SHELL_EN ? "Back to Focus" : "Volver a Enfoque"}>
        <span className="ff-ic"><IconTimer size={17} color={phase === "break" ? "var(--good)" : "var(--brand)"} /></span>
        <span className="ff-b"><b>{mm}:{ss}</b><small>{label}</small></span>
      </a>
    </Overlay>
  );
}

function Topbar({ title, sub, streak, xp, coins, freezes, plus = false, tweaks, setTweak, avatar = "MR" }) {
  const freezeCount = freezes ?? SHELL_DATA.freezes;
  return (
    <header className="topbar">
      <div className="topbar-in">
        <div className="crumb">
          <span className="mono">{sub}</span>
          <b>{title}</b>
        </div>
        {plus && <PlusBadge />}
        <div className="tb-spacer" />
        <div className="tb-stats">
          <span className="chip fire hide-sm"><IconFire size={16} color="var(--brand)" /><span className="num">{streak}</span></span>
          <span className="chip xp hide-sm"><IconBolt size={16} color="var(--plum)" /><span className="num">{xp}</span></span>
          <a href="/student/shop?section=coins" className="chip coin hide-sm" title={SHELL_EN ? "Coins — go to the shop" : "Monedas — ir a la tienda"}><IconCoin size={16} color="#B58309" /><span className="num">{coins}</span></a>
          <a href="/student/shop?section=coins" className="chip freeze hide-sm" title={SHELL_EN ? "Streak freezes — go to the shop" : "Congeladores de racha — ir a la tienda"}>❄️<span className="num">{freezeCount ?? 0}</span></a>
          <button className="icon-btn" aria-label="Cambiar tema" onClick={() => setTweak("theme", tweaks.theme === "dark" ? "light" : "dark")}>
            {tweaks.theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
          </button>
          {/* Labelled, because a bare gear glyph read as decoration. */}
          <a href="/student/settings" className="tb-settings" title={SHELL_EN ? "Settings" : "Ajustes"}><IconGear size={16} /><span>{SHELL_EN ? "Settings" : "Ajustes"}</span></a>
          <form method="post" action="/logout" className="tb-logout">
            <input type="hidden" name="csrf_token" value={SHELL_DATA.csrf || ""} />
            <button type="submit" className="icon-btn" aria-label={SHELL_EN ? "Log out" : "Cerrar sesión"} title={SHELL_EN ? "Log out" : "Cerrar sesión"}><IconLogout size={17} /></button>
          </form>
          <a href="/student/profile" className="avatar" style={{ background: SHELL_DATA.avatar_color || "#FFD3A8" }} aria-label={SHELL_EN ? "Profile" : "Perfil"}>
            {SHELL_DATA.avatar_url
              ? <img src={SHELL_DATA.avatar_url} alt="" style={{ width: "100%", height: "100%", borderRadius: "inherit", objectFit: "cover", display: "block" }} />
              : avatar}
          </a>
        </div>
      </div>
      <GenerationWatcher />
      <FocusFloat />
      {SHELL_DATA.show_tour && <Tour />}
    </header>
  );
}


/* ---- First-run walkthrough ---------------------------------------------
   Shown once, on the page the student lands on straight after setup. Each
   step spotlights the real sidebar entry it is talking about rather than a
   screenshot, so what they learn is where things actually are. It ends on the
   referral, which is the only way a free account can try the Plus tools. */
const TOUR_STEPS = [
  {
    title: "Bienvenido a MachReach",
    body: "Un minuto y sabes dónde está todo. Puedes saltarlo cuando quieras.",
  },
  {
    anchor: "home", title: "Inicio",
    body: "Tu misión del día: qué te toca estudiar hoy, tu racha y lo que viene esta semana.",
  },
  {
    anchor: "focus", title: "Enfoque",
    body: "El temporizador de estudio. Con la extensión instalada bloquea los sitios que te distraen mientras corre, y cada bloque terminado te da XP y monedas.",
  },
  {
    anchor: "courses", title: "Mis cursos",
    body: "Tus ramos, sus evaluaciones y el material que subes. Cuando termina el semestre, aquí lo cierras y guardas tus notas finales.",
  },
  {
    anchor: "tools", title: "Herramientas",
    body: "Quizzes y flashcards generados con IA desde tu propio material: subes la guía y practicas con ella.",
  },
  {
    anchor: "notas", title: "Notas",
    body: "Tu planilla en escala 1,0 a 7,0. Te dice cuánto necesitas en lo que queda para aprobar.",
  },
  {
    anchor: "rank", title: "Ranking",
    body: "Compites por horas de estudio con tu país, tu universidad y tu carrera. Las notas nunca afectan tu posición: solo el estudio real.",
  },
  {
    anchor: "friends", title: "Amigos",
    body: "Tu liga privada. Ves quién está estudiando ahora y compites solo con ellos.",
  },
  {
    anchor: "shop", title: "Tienda",
    body: "Las monedas que ganas estudiando se gastan aquí: banderas, portadas y congeladores para no perder la racha.",
  },
  {
    anchor: "plan", title: "Plan y Analíticas", plusPitch: true,
    body: "Estos dos son de Plus: el plan de estudio que la IA arma según la dificultad, la fecha y la ponderación de cada prueba, y las analíticas de tu rendimiento real.",
  },
  { final: true, title: "Pruébalo gratis invitando a un amigo" },
];

function Tour() {
  const [step, setStep] = React.useState(0);
  const [rect, setRect] = React.useState(null);
  const [done, setDone] = React.useState(false);
  const current = TOUR_STEPS[step];

  React.useEffect(() => {
    const measure = () => {
      if (!current.anchor) return setRect(null);
      const node = document.querySelector(`[data-tour="${current.anchor}"]`);
      // On a phone the sidebar is not on screen; the card then stands alone
      // rather than pointing at nothing.
      if (!node || !node.getClientRects().length) return setRect(null);
      const box = node.getBoundingClientRect();
      setRect({ top: box.top, left: box.left, width: box.width, height: box.height });
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [step]);

  const finish = () => {
    setDone(true);
    try {
      fetch("/api/student/tour/done", {
        method: "POST", headers: { "X-CSRFToken": SHELL_DATA.csrf || "" },
      });
    } catch (e) { /* the tour is not worth blocking the app over */ }
  };

  if (done) return null;
  const last = step === TOUR_STEPS.length - 1;
  // Rendered into <body>: the topbar has a backdrop-filter, which makes it the
  // containing block for position:fixed, and the spotlight would be measured
  // against the viewport but drawn against the header.
  const card = rect
    ? { top: Math.min(window.innerHeight - 260, Math.max(16, rect.top - 8)), left: rect.left + rect.width + 18 }
    : null;

  return ReactDOM.createPortal((
    <div className="tour" role="dialog" aria-modal="true" aria-label="Tutorial">
      <div className="tour-veil" onClick={finish} />
      {rect && (
        <div className="tour-ring" style={{
          top: rect.top - 6, left: rect.left - 6,
          width: rect.width + 12, height: rect.height + 12,
        }} />
      )}
      <div className={"tour-card" + (card ? " anchored" : "")}
        style={card ? { top: card.top, left: card.left } : undefined}>
        <div className="tour-step">Paso {step + 1} de {TOUR_STEPS.length}</div>
        <h3>{current.title}</h3>
        {current.final ? (
          <>
            <p>Plan y Analíticas son de Plus. No hace falta pagar para probarlos:
               <b> cada amigo que se une con tu enlace te da 7 días de Plus gratis.</b></p>
            {SHELL_DATA.referral_link && (
              <div className="tour-link">
                <input readOnly value={SHELL_DATA.referral_link}
                  onFocus={(e) => e.target.select()} />
                <button type="button" className="btn btn-primary btn-sm"
                  onClick={() => { try { navigator.clipboard.writeText(SHELL_DATA.referral_link); } catch (e) {} }}>
                  Copiar
                </button>
              </div>
            )}
          </>
        ) : (
          <p>{current.body}</p>
        )}
        {current.plusPitch && <p className="tour-note">Sin Plus se ven con candado — en un momento te digo cómo probarlos gratis.</p>}
        <div className="tour-foot">
          <button type="button" className="tour-skip" onClick={finish}>
            {last ? "Cerrar" : "Saltar tutorial"}
          </button>
          {step > 0 && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setStep(step - 1)}>Atrás</button>}
          {last
            ? <a className="btn btn-primary btn-sm" href="/student/friends" onClick={finish}>Invitar a un amigo</a>
            : <button type="button" className="btn btn-primary btn-sm" onClick={() => setStep(step + 1)}>Siguiente</button>}
        </div>
      </div>
    </div>
  ), document.body);
}

function TabBar({ active = "home" }) {
  const [moreOpen, setMoreOpen] = React.useState(false);
  const items = [NAV_MAIN[0], NAV_MAIN[2], NAV_SOCIAL[0], NAV_SOCIAL[1]];
  const moreItems = [
    { id: "courses", label: SHELL_EN ? "Courses" : "Cursos", Ic: IconBook, href: "/student/courses" },
    { id: "notas", label: SHELL_EN ? "Grades" : "Notas", Ic: IconGrid, href: "/student/gpa" },
    { id: "quizzes", label: "Quiz", Ic: IconBrain, href: "/student/quizzes" },
    { id: "flashcards", label: "Flashcards", Ic: IconBook, href: "/student/flashcards" },
    { id: "stats", label: SHELL_EN ? "Analytics" : "Analíticas", Ic: IconChart, href: "/student/analytics" },
    { id: "shop", label: SHELL_EN ? "Shop" : "Tienda", Ic: IconStore, href: "/student/shop" },
    { id: "account", label: SHELL_EN ? "Account" : "Cuenta", Ic: IconPeople, href: "/student/profile" },
  ];
  const moreActive = moreItems.some((n) => n.id === active) || active === "tools" || active === "profile";
  React.useEffect(() => {
    if (!moreOpen) return undefined;
    const onKey = (event) => event.key === "Escape" && setMoreOpen(false);
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [moreOpen]);
  return (
    <React.Fragment>
      {moreOpen && <div className="mobile-more-backdrop" onClick={() => setMoreOpen(false)} aria-hidden="true" />}
      <div id="mobile-more-menu" className={"mobile-more-menu" + (moreOpen ? " open" : "")} role="dialog" aria-modal="true" aria-label={SHELL_EN ? "More pages" : "Más páginas"}>
        <div className="mobile-more-head"><b>{SHELL_EN ? "More pages" : "Más páginas"}</b><button type="button" onClick={() => setMoreOpen(false)} aria-label={SHELL_EN ? "Close" : "Cerrar"}><IconClose size={17} /></button></div>
        <div className="mobile-more-grid">
          {moreItems.map((n) => <a key={n.id} href={n.href} className={active === n.id || (n.id === "account" && active === "profile") || (active === "tools" && (n.id === "quizzes" || n.id === "flashcards")) ? "on" : ""}><span><n.Ic size={19} /></span>{n.label}</a>)}
        </div>
      </div>
      <nav className="tabbar" aria-label={SHELL_EN ? "Main navigation" : "Navegación principal"}>
        {items.map((n) => <a key={n.id} href={n.href || "#"} className={"tb" + (active === n.id ? " on" : "")}><n.Ic size={20} />{n.label}</a>)}
        <button type="button" className={"tb" + (moreOpen || moreActive ? " on" : "")} aria-expanded={moreOpen} aria-controls="mobile-more-menu" onClick={() => setMoreOpen((open) => !open)}><IconMore size={20} />{SHELL_EN ? "More" : "Más"}</button>
      </nav>
    </React.Fragment>
  );
}

function Modal({ title, sub, onClose, children, foot }) {
  React.useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="mdl-back" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mdl" role="dialog" aria-modal="true">
        <div className="mdl-h">
          <div><h3>{title}</h3>{sub && <p>{sub}</p>}</div>
          <button className="mdl-x" onClick={onClose} aria-label="Cerrar"><IconClose size={15} /></button>
        </div>
        {children}
        {foot && <div className="mdl-foot">{foot}</div>}
      </div>
    </div>
  );
}

Object.assign(window, { IconHome, IconCal, IconGrid, IconStore, IconBell, AV, Ring, NavItem, Sidebar, Topbar, TabBar, Modal, GenerationWatcher, markGenerationQueued, NAV_MAIN, NAV_SOCIAL });
