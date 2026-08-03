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
    <a href={n.href || "#"} className={"nv" + (active === n.id ? " on" : "")}>
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
function FocusFloat() {
  const [left, setLeft] = React.useState(0);
  const [phase, setPhase] = React.useState("work");
  React.useEffect(() => {
    if (location.pathname === "/student/focus") return undefined;
    const read = () => {
      let state = null;
      try { state = JSON.parse(localStorage.getItem("mr_focus_timer_v1") || "null"); } catch (e) { state = null; }
      if (!state || !state.running || !state.endsAt) return setLeft(0);
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
          <a href="/student/profile" className="avatar" style={{ background: "#FFD3A8" }} aria-label={SHELL_EN ? "Profile" : "Perfil"}>
            {SHELL_DATA.avatar_url
              ? <img src={SHELL_DATA.avatar_url} alt="" style={{ width: "100%", height: "100%", borderRadius: "inherit", objectFit: "cover", display: "block" }} />
              : avatar}
          </a>
        </div>
      </div>
      <GenerationWatcher />
      <FocusFloat />
    </header>
  );
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
