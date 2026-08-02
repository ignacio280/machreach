/* App shell: extra icons, sidebar, topbar, mobile tabbar */
const IconHome = (p) => <Icon {...p}><path d="M4 11l8-7 8 7M6.5 9.5V20h11V9.5M10 20v-5h4v5" /></Icon>;
const IconCal = (p) => <Icon {...p}><rect x="3.5" y="5" width="17" height="15" rx="3" /><path d="M3.5 10h17M8 3v4M16 3v4" /></Icon>;
const IconGrid = (p) => <Icon {...p}><rect x="4" y="4" width="6.5" height="6.5" rx="2" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="2" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="2" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="2" /></Icon>;
const IconStore = (p) => <Icon {...p}><path d="M4 9l1.5-5h13L20 9M4 9h16v10a1 1 0 01-1 1H5a1 1 0 01-1-1V9zM4 9a3 3 0 004 0 3 3 0 004 0 3 3 0 004 0 3 3 0 004 0" /></Icon>;
const IconBell = (p) => <Icon {...p}><path d="M18 15V10a6 6 0 10-12 0v5l-2 3h16l-2-3zM10 21h4" /></Icon>;

const AV = ["#FF8AA5", "#8DACFF", "#B29BFF", "#5DE3B0", "#FFB37A", "#9CD9F0", "#FFC857"];
const SHELL_DATA = window.__MACHREACH_DASHBOARD__ || {};
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

function Topbar({ title, sub, streak, xp, coins, plus = false, tweaks, setTweak, avatar = "MR" }) {
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
          <span className="chip coin hide-sm"><IconCoin size={16} color="#B58309" /><span className="num">{coins}</span></span>
          <a href="/student/achievements" className="icon-btn" aria-label={SHELL_EN ? "Notifications" : "Notificaciones"}><IconBell size={17} /></a>
          <button className="icon-btn" aria-label="Cambiar tema" onClick={() => setTweak("theme", tweaks.theme === "dark" ? "light" : "dark")}>
            {tweaks.theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
          </button>
          <a href="/student/profile" className="avatar" style={{ background: "#FFD3A8" }} aria-label={SHELL_EN ? "Profile" : "Perfil"}>{avatar}</a>
        </div>
      </div>
    </header>
  );
}

function TabBar({ active = "home" }) {
  const items = [NAV_MAIN[0], NAV_MAIN[1], NAV_MAIN[2], NAV_SOCIAL[0], NAV_SOCIAL[1]];
  return (
    <nav className="tabbar">
      {items.map((n) => <a key={n.id} href={n.href || "#"} className={"tb" + (active === n.id ? " on" : "")}><n.Ic size={20} />{n.label}</a>)}
    </nav>
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

Object.assign(window, { IconHome, IconCal, IconGrid, IconStore, IconBell, AV, Ring, NavItem, Sidebar, Topbar, TabBar, Modal, NAV_MAIN, NAV_SOCIAL });
