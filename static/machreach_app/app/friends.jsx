/* Amigos page */
const MY_ID = 4182;
const FRIENDS_LIST = [
  { id: 3910, n: "Camila Rojas", on: true, s: "Estudiando ahora · Cálculo II", xp: "4.820" },
  { id: 4021, n: "Tomás Vidal", s: "Racha de 22 días · última sesión hace 3 h", xp: "4.315" },
  { id: 3877, n: "Ignacio Soto", s: "Enfoque hace 2 h · 45 min hoy", xp: "3.970" },
  { id: 4155, n: "Valentina Cruz", on: true, s: "Estudiando ahora · Álgebra Lineal", xp: "3.640" },
  { id: 3742, n: "Diego Márquez", s: "Sin sesiones esta semana", xp: "2.180" },
];
const INCOMING = [
  { id: 4310, n: "Fernanda Lira", s: "PUC · Ing. Civil · 2 amigos en común" },
  { id: 4288, n: "Martín Godoy", s: "USM · Ing. Civil · 1 amigo en común" },
];
const SEARCHABLE = [
  { id: 4402, n: "Josefa Álvarez", s: "PUC · Ing. Civil" },
  { id: 4377, n: "Cristóbal Pinto", s: "U. de Chile · Ing. Civil" },
  { id: 4520, n: "Amanda Silva", s: "UdeC · Ing. Comercial" },
];
const ini3 = (n) => { const p = n.split(" "); return (p[0][0] + (p[1] ? p[1][0] : "")).toUpperCase(); };

function FriendRow({ u, kind, onAct, i = 0 }) {
  const [menu, setMenu] = React.useState(false);
  React.useEffect(() => {
    if (!menu) return;
    const c = () => setMenu(false);
    addEventListener("click", c);
    return () => removeEventListener("click", c);
  }, [menu]);
  return (
    <div className="fr-row" style={{ animation: "rowIn .5s var(--spring) both", animationDelay: i * 60 + "ms" }}>
      {/* Avatar and name open the public profile; the action buttons beside
          them stay outside the link so Aceptar/Bloquear cannot navigate. */}
      <a className="fr-open" href={"/student/profile/" + u.id} title={"Ver el perfil de " + u.n}>
        <div className="fr-avatar" style={{ background: AV[u.id % AV.length] }}>
          {ini3(u.n)}
          {kind === "friend" && <span className={"dot2" + (u.on ? " on" : "")} />}
        </div>
        <div className="fr-who">
          <div className="fr-name">{u.n} <span className="id">#{u.id}</span></div>
          <div className={"fr-state" + (u.on ? " on" : "")}>{u.on ? "En línea" : u.s}</div>
        </div>
      </a>
      {kind === "incoming" && (
        <div className="fr-actions">
          <button className="fr-btn orange" onClick={() => onAct("accept", u)}>Aceptar</button>
          <button className="fr-btn" onClick={() => onAct("reject", u)}>Rechazar</button>
        </div>
      )}
      {kind === "search" && (
        <div className="fr-actions">
          <button className="fr-btn orange" onClick={() => onAct("send", u)}>Agregar</button>
        </div>
      )}
      {kind === "friend" && (
        <div className="fr-actions">
          <span className="mono num">{u.xp} XP</span>
          <div className="fr-menu" onClick={(e) => e.stopPropagation()}>
            <button className="fr-btn" onClick={() => setMenu(!menu)} aria-label="Más acciones">···</button>
            {menu && (
              <div className="fr-menu-pop">
                <button onClick={() => { setMenu(false); onAct("remove", u); }}>Eliminar amigo</button>
                <button onClick={() => { setMenu(false); onAct("report", u); }}>Reportar</button>
                <button className="bad" onClick={() => { setMenu(false); onAct("block", u); }}>Bloquear</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function FriendsHero({ data = {} }) {
  return (
    <section className="fr-hero">
      <div>
        <div className="pl-kicker">Amigos</div>
        <h1>Estudiar solo cuesta más.</h1>
        <p>Agrega compañeros por nombre o por ID, mira quién está estudiando ahora mismo y compite con ellos en la liga de amigos.</p>
      </div>
      <div className="fr-id">Tu ID <b>#{data.my_id || MY_ID}</b></div>
    </section>
  );
}

function SearchCard({ onSend, data = {} }) {
  const [q, setQ] = React.useState("");
  const [res, setRes] = React.useState(null);
  const [disc, setDisc] = React.useState(data.discovery !== false);
  const run = async () => {
    if (!q.trim()) return setRes([]);
    if (!data.live) return setRes(SEARCHABLE.filter((u) => u.n.toLowerCase().includes(q.trim().toLowerCase()) || String(u.id).includes(q.trim())));
    const response = await fetch("/api/student/friends/search?q=" + encodeURIComponent(q.trim()));
    const body = await response.json();
    setRes((body.results || []).map((u) => ({ id: u.id, n: u.name || "Estudiante", s: [u.university, u.field_of_study].filter(Boolean).join(" · ") })));
  };
  const toggleDiscovery = async () => {
    const next = !disc;
    setDisc(next);
    if (data.live) await fetch("/api/student/friends/discovery", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ enabled: next }) });
  };
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconPeople size={17} /></span>
        <h3>Buscar personas</h3>
      </div>
      <div className="fr-searchrow">
        <input className="fr-input" placeholder="Nombre o ID de MachReach (#4402)" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} />
        <button className="btn btn-primary" onClick={run}>Buscar</button>
      </div>
      {res && (
        <div style={{ marginTop: 14 }}>
          {res.length ? res.map((u, i) => <FriendRow key={u.id} u={u} kind="search" i={i} onAct={(a, x) => { onSend(x); setRes(res.filter((r) => r.id !== x.id)); }} />)
            : <div className="fr-empty">Sin resultados para «{q}»</div>}
        </div>
      )}
      <div className="fr-privacy">
        <div>
          <div className="t">Aparecer en las búsquedas</div>
          <div className="s">Si lo apagas, solo pueden encontrarte con tu ID exacto.</div>
        </div>
        <button className={"sw" + (disc ? " on" : "")} style={{ marginLeft: "auto" }} onClick={toggleDiscovery} aria-label="Aparecer en búsquedas"><i /></button>
      </div>
    </section>
  );
}

function Referral({ data = {} }) {
  const [copied, setCopied] = React.useState(false);
  const link = data.referral_link || "machreach.com/r/SOFIA4182";
  const copy = () => { navigator.clipboard && navigator.clipboard.writeText(link.startsWith("http") ? link : "https://" + link); setCopied(true); setTimeout(() => setCopied(false), 1800); };
  return (
    <section className="ref">
      <div>
        <span className="ref-badge">🎁 1 amigo = 7 días Plus</span>
        <h3>Invita amigos, gana Plus</h3>
        <p className="ref-note">Cada amigo real que se une con tu enlace te da 7 días de Plus. Este bloque queda aquí siempre para que puedas seguir compartiendo.</p>
        <div className="ref-label">Tu enlace de invitación</div>
        <div className="ref-copy">
          <input className="ref-link" readOnly value={link} />
          <button className="btn btn-primary btn-sm" onClick={copy}>{copied ? "¡Copiado!" : "Copiar enlace"}</button>
        </div>
      </div>
      <div className="ref-side">
        <div className="ref-stat"><div className="ref-num num">{data.referral_joined ?? 3}</div><div className="l">Amigos que se unieron</div></div>
        <div className="ref-stat"><div className="ref-num num">{data.referral_joined ?? 3}</div><div className="l">Semanas ganadas</div></div>
        <div className="ref-stat"><div className="l" style={{ marginTop: 0 }}>Plus activo hasta</div><div className="ref-status">{data.plus_until || "Sin días de referido activos"}</div></div>
      </div>
    </section>
  );
}

function FriendsPanels({ data = {} }) {
  const [friends, setFriends] = React.useState(data.friends || FRIENDS_LIST);
  const [incoming, setIncoming] = React.useState(data.incoming || INCOMING);
  const [sent, setSent] = React.useState([]);
  const act = async (a, u) => {
    if (data.live) {
      const endpoint = a === "accept" || a === "send" ? "add" : a === "remove" || a === "reject" ? "remove" : a;
      await fetch("/api/student/friends/" + endpoint, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ friend_id: u.id, reason: a === "report" ? "Reportado desde Amigos" : "" }) });
    }
    if (a === "accept") { setIncoming((x) => x.filter((y) => y.id !== u.id)); setFriends((f) => [{ ...u, xp: "0", s: "Recién agregado" }, ...f]); }
    if (a === "reject" || a === "block") setIncoming((x) => x.filter((y) => y.id !== u.id));
    if (a === "remove" || a === "block") setFriends((f) => f.filter((y) => y.id !== u.id));
  };
  const online = friends.filter((f) => f.on).length;
  return (
    <React.Fragment>
      <SearchCard data={data} onSend={(u) => { setSent((s) => [...s, u.id]); act("send", u); }} />
      <Referral data={data} />
      {incoming.length > 0 && (
        <section className="pnl">
          <div className="pnl-h">
            <span className="ico-badge" style={{ background: "#FFF3CF" }}><IconBell size={17} /></span>
            <h3>Solicitudes de amistad</h3>
            <span className="mono">{incoming.length} pendiente{incoming.length === 1 ? "" : "s"}</span>
          </div>
          {incoming.map((u, i) => <FriendRow key={u.id} u={u} kind="incoming" i={i} onAct={act} />)}
        </section>
      )}
      <section className="pnl">
        <div className="pnl-h">
          <span className="ico-badge" style={{ background: "#E1F3D6" }}><IconPeople size={17} /></span>
          <h3>Tus amigos</h3>
          <span className="mono">{friends.length} · {online} en línea</span>
          <a href="/student/leaderboard" className="lnk">Liga de amigos <IconArrow size={14} /></a>
        </div>
        {friends.length ? friends.map((u, i) => <FriendRow key={u.id} u={u} kind="friend" i={i} onAct={act} />)
          : <div className="fr-empty">Todavía no tienes amigos en MachReach</div>}
      </section>
    </React.Fragment>
  );
}

Object.assign(window, { FriendsHero, FriendsPanels, FriendRow });
