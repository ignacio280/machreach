/* v6 Hero — exact live copy */

function Avatar({ name, color, size = 36, you, initials }) {
  const ini = initials || name.replace(/_/g, " ").split(" ").map((s) => s[0]).join("").slice(0, 2).toUpperCase();
  return <div style={{ width: size, height: size, borderRadius: size * 0.32, background: color, border: "2.5px solid var(--ink)", display: "grid", placeItems: "center", fontFamily: "var(--font-display)", fontWeight: 800, fontSize: size * 0.36, color: "#fff", flexShrink: 0, boxShadow: you ? "0 0 0 3px var(--amber)" : "none" }}>{ini}</div>;
}

const SEED = [
  { name: "Sofia_Db", ini: "SD", uni: "UC", xp: 12480, color: "#F97316" },
  { name: "tú", ini: "TL", uni: "Tu universidad", xp: 9120, color: "var(--brand)", you: true },
  { name: "matiasv", ini: "M", uni: "UAndes", xp: 8740, color: "var(--plum)" },
  { name: "fernandapz", ini: "F", uni: "USACH", xp: 7610, color: "var(--sky)" },
  { name: "ignacio_m", ini: "IM", uni: "UC", xp: 7185, color: "var(--good)" },
];

function LiveBoard() {
  const [board, setBoard] = React.useState(SEED);
  const [hot, setHot] = React.useState(null);
  const [toast, setToast] = React.useState(0);
  const bind = useFlip(board.map((r) => r.name).join("|"));
  React.useEffect(() => {
    const id = setInterval(() => {
      setBoard((prev) => {
        const next = prev.map((r) => ({ ...r, xp: r.xp + Math.floor(Math.random() * (r.you ? 26 : 19)) + 2 }));
        next.sort((a, b) => b.xp - a.xp);
        return next;
      });
      setHot(SEED[Math.floor(Math.random() * SEED.length)].name);
      setTimeout(() => setHot(null), 850);
      setToast((t) => t + 1);
    }, 2400);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="hero-panel">
      <div className="hp-head">
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <div className="hp-badge"><IconTrophy size={19} /></div>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 16, display: "flex", alignItems: "center", gap: 7 }}>Investigadores <span className="hp-season">S26</span></div>
            <div className="mono" style={{ fontSize: 10, marginTop: 2, textTransform: "none", letterSpacing: 0 }}>Temporada activa - 2d 14h restantes</div>
          </div>
        </div>
        <span className="tagchip"><i className="live-dot" /> live</span>
      </div>
      <div className="hp-rows">
        {board.map((r, i) => (
          <div key={r.name} ref={bind(r.name)} className={"hp-row" + (r.you ? " is-you" : "") + (hot === r.name ? " is-hot" : "")}>
            <span className={"hp-rank" + (i < 3 ? " medal r" + (i + 1) : "")}>{i + 1}</span>
            <Avatar name={r.name} initials={r.ini} color={r.color} size={34} you={r.you} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 800, fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}>{r.name}{r.you && <span className="hp-you">YOU</span>}</div>
              <div className="hp-uni">{r.uni}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className="num" style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 15 }}>{Math.round(r.xp).toLocaleString("es-CL")}</div>
              <div className="mono" style={{ fontSize: 9 }}>XP</div>
            </div>
          </div>
        ))}
      </div>
      <div className="hp-foot"><span>CIERRA LUN 09:00</span><span style={{ display: "flex", alignItems: "center", gap: 5 }}>PREMIO TOP 3 · 500 <IconCoin size={13} /></span></div>
      <div key={toast} className="hp-toast"><IconBolt size={15} /> +24 XP</div>
      <div className="hp-streak"><span className="hp-flame"><IconFire size={19} /></span><b>18</b><span style={{ color: "var(--ink-3)", fontWeight: 700, fontSize: 12.5 }}>días seguidos</span></div>
    </div>
  );
}

function Hero() {
  const tiltRef = useTilt(4);
  const ctaRef = useMagnetic(0.22, 9);
  const [ref, seen] = useReveal({ threshold: 0.05 });
  return (
    <section className="hero" ref={ref}>
      <Doodles />
      <div className="container hero-grid">
        <div className={seen ? "in" : ""}>
          <h1 className="hero-h1">
            <Lines lines={["Estudiar deja", "de ser una"]} d={120} />
            <span className="hero-lata-line"><span className="hero-lata" style={{ "--d": "420ms" }}>lata.</span></span>
          </h1>
          <p className="hero-sub rv" style={{ "--d": "560ms" }}>MachReach convierte tu semestre en un juego que <em>sí quieres ganar</em>.</p>
          <div className="hero-cta rv" style={{ "--d": "650ms" }}>
            <span ref={ctaRef} style={{ display: "inline-flex" }}><a href="https://machreach.com/register" className="btn btn-primary btn-lg"><IconCanvas size={19} /> Usar la extensión</a></span>
            <a href="#how" className="btn btn-ghost btn-lg">Ver cómo funciona <IconArrow size={17} /></a>
          </div>
          <div className="hero-trust rv" style={{ "--d": "750ms" }}>
            <div className="stack">{["VR", "MA", "FP", "IM", "SG"].map((t, i) => <span key={i} style={{ background: ["var(--brand)", "var(--plum)", "var(--sky)", "var(--amber)", "var(--lime)"][i], color: i > 2 ? "var(--ink)" : "#fff" }}>{t}</span>)}</div>
            <span>Beta abierta para estudiantes en Chile</span>
          </div>
        </div>
        <div className={"hero-panel-wrap rv-scale " + (seen ? "in" : "")} style={{ "--d": "360ms" }}><div className="hero-tilt" ref={tiltRef}><LiveBoard /></div></div>
      </div>
    </section>
  );
}

Object.assign(window, { Avatar, Hero, LiveBoard });
