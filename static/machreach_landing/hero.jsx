/* ======== Hero — leaderboard-led, animated, polished ======== */

const heroStyles = {
  wrap: { position: "relative", paddingTop: 64, paddingBottom: 96, overflow: "hidden" },
  blob1: { position: "absolute", top: -120, right: -120, width: 460, height: 460, background: "var(--brand-soft)", borderRadius: "50%", filter: "blur(8px)", zIndex: 0 },
  blob2: { position: "absolute", bottom: -200, left: -100, width: 360, height: 360, background: "var(--secondary-soft)", borderRadius: "50%", filter: "blur(8px)", zIndex: 0 },
  grid: { position: "relative", zIndex: 1, display: "grid", gridTemplateColumns: "1.05fr 1fr", gap: 56, alignItems: "center" },
  h1: { fontFamily: "var(--font-display)", fontSize: "clamp(44px, 6vw, 76px)", fontWeight: 800, letterSpacing: "-0.03em", lineHeight: 0.98, marginTop: 22 },
  h1Highlight: { background: "var(--brand)", color: "white", padding: "0 .12em", borderRadius: 12, display: "inline-block", transform: "rotate(-1.5deg)", boxShadow: "0 4px 0 0 var(--ink)", border: "2px solid var(--ink)", margin: "0 .04em" },
  sub: { color: "var(--ink-2)", fontSize: 20, marginTop: 22, maxWidth: 520, lineHeight: 1.5 },
  ctaRow: { display: "flex", flexWrap: "wrap", gap: 14, marginTop: 32, alignItems: "center" },
  trustRow: { display: "flex", alignItems: "center", gap: 18, marginTop: 28, color: "var(--ink-3)", fontSize: 14, fontWeight: 600 },
  avatars: { display: "flex" },
  avatar: (i) => ({ width: 32, height: 32, borderRadius: "50%", border: "2px solid var(--surface)", background: ["#fb923c", "#a78bfa", "#22d3ee", "#facc15", "#34d399"][i], marginLeft: i === 0 ? 0 : -10, fontWeight: 800, fontSize: 13, color: "white", display: "grid", placeItems: "center", fontFamily: "var(--font-display)" }),
};

/* avatar with personality — uses initials but adds geometric mark */
function Avatar({ name, color, size = 36, you }) {
  const initials = name.replace(/_/g, " ").split(" ").map(s => s[0]).join("").slice(0, 2).toUpperCase();
  return (
    <div style={{
      width: size, height: size, borderRadius: size * 0.32,
      background: color, border: "2px solid var(--ink)",
      display: "grid", placeItems: "center",
      fontFamily: "var(--font-display)", fontWeight: 800,
      fontSize: size * 0.36, color: "white",
      position: "relative",
      boxShadow: you ? "0 0 0 3px var(--brand-soft)" : "none",
    }}>
      {initials}
    </div>
  );
}

function HeroLeaderboard() {
  const [board, setBoard] = React.useState([
    { name: "Sofia_Db",   uni: "UC",     xp: 12480, color: "#fb923c", delta: 0 },
    { name: "tu_lugar",   uni: "UDP",    xp: 9120,  color: "var(--brand)", you: true, delta: 0 },
    { name: "matiasv",    uni: "UAndes", xp: 8740,  color: "#a78bfa", delta: 0 },
    { name: "fernandapz", uni: "USACH",  xp: 7610,  color: "#22d3ee", delta: 0 },
    { name: "ignacio_m",  uni: "UC",     xp: 7185,  color: "#34d399", delta: 0 },
  ]);
  const [tick, setTick] = React.useState(0);
  const [pulseIndex, setPulseIndex] = React.useState(-1);

  React.useEffect(() => {
    const id = setInterval(() => {
      setBoard(prev => {
        const next = prev.map(r => {
          const inc = Math.floor(Math.random() * (r.you ? 24 : 18)) + 2;
          return { ...r, xp: r.xp + inc, delta: inc };
        });
        next.sort((a, b) => b.xp - a.xp);
        return next;
      });
      const idx = Math.floor(Math.random() * 5);
      setPulseIndex(idx);
      setTimeout(() => setPulseIndex(-1), 800);
      setTick(t => t + 1);
    }, 1800);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{
      position: "relative",
      background: "var(--surface)",
      border: "2px solid var(--ink)",
      borderRadius: 28,
      boxShadow: "0 8px 0 0 var(--ink)",
      padding: 22,
      transform: "rotate(1.4deg)",
    }}>
      {/* Floating XP badge */}
      <div style={{
        position: "absolute", top: -22, right: -14, zIndex: 2,
        background: "var(--accent)", color: "var(--accent-ink)",
        border: "2px solid var(--ink)", borderRadius: 14,
        padding: "8px 14px", fontFamily: "var(--font-display)", fontWeight: 800,
        boxShadow: "0 4px 0 0 var(--ink)",
        animation: "float 2.6s ease-in-out infinite",
        display: "flex", alignItems: "center", gap: 6,
      }}>
        <IconBolt size={16}/> +24 XP
      </div>
      {/* Floating streak */}
      <div style={{
        position: "absolute", bottom: -18, left: -16, zIndex: 2,
        background: "var(--surface)",
        border: "2px solid var(--ink)", borderRadius: 14,
        padding: "10px 14px", fontFamily: "var(--font-display)", fontWeight: 800,
        boxShadow: "0 4px 0 0 var(--ink)",
        display: "flex", alignItems: "center", gap: 8,
        animation: "float 3.2s ease-in-out infinite",
      }}>
        <span style={{ color: "var(--bad)" }}><IconFire size={20}/></span>
        <span>18</span>
        <span style={{ color: "var(--ink-3)", fontWeight: 600, fontSize: 13 }}>días seguidos</span>
      </div>

      {/* Header bar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 38, height: 38, borderRadius: 11,
            background: "var(--brand-soft)", border: "2px solid var(--brand)",
            display: "grid", placeItems: "center", color: "var(--brand-ink)",
          }}>
            <IconTrophy size={20}/>
          </div>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 16, display: "flex", alignItems: "center", gap: 6 }}>
              Liga Diamante
              <span style={{
                fontFamily: "var(--font-mono)", fontSize: 10,
                padding: "2px 6px", borderRadius: 5,
                background: "var(--ink)", color: "white", letterSpacing: ".06em",
              }}>S26</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "var(--font-mono)", letterSpacing: ".05em" }}>
              {(2300 + tick * 7).toLocaleString("es-CL")} estudiantes · 2d 14h restantes
            </div>
          </div>
        </div>
        <span className="tag" style={{ borderColor: "var(--good)", color: "var(--good)", background: "color-mix(in oklab, var(--good) 12%, var(--surface))" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--good)", animation: "pulse-ring 1.4s infinite" }}/>
          live
        </span>
      </div>

      {/* Rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {board.map((r, i) => {
          const medal = i === 0 ? "var(--gold)" : i === 1 ? "var(--silver)" : i === 2 ? "var(--bronze)" : null;
          return (
            <div key={r.name} style={{
              display: "grid",
              gridTemplateColumns: "28px 40px 1fr auto",
              alignItems: "center", gap: 12,
              padding: "10px 12px",
              borderRadius: 14,
              background: r.you
                ? "var(--brand-soft)"
                : (i === 0 ? "color-mix(in oklab, var(--gold) 14%, var(--surface))" : "var(--bg-2)"),
              border: r.you ? "2px solid var(--brand)" : "2px solid transparent",
              transition: "transform .5s cubic-bezier(.5,1.6,.4,1), background .3s",
              position: "relative",
            }}>
              <div style={{
                fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 18,
                color: medal || "var(--ink-3)",
                textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                {medal ? (
                  <span style={{
                    width: 22, height: 22, borderRadius: "50%",
                    background: medal, border: "2px solid var(--ink)",
                    color: "white", display: "grid", placeItems: "center",
                    fontSize: 11,
                  }}>{i + 1}</span>
                ) : (i + 1)}
              </div>
              <Avatar name={r.name} color={r.color} size={36} you={r.you}/>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}>
                  {r.you ? "tú" : r.name}
                  {r.you && <span style={{
                    fontSize: 10, fontWeight: 800, padding: "2px 6px", borderRadius: 6,
                    background: "var(--brand)", color: "white", letterSpacing: ".06em",
                  }}>YOU</span>}
                  {pulseIndex === i && (
                    <span style={{
                      fontSize: 10, fontWeight: 800,
                      color: "var(--good)", fontFamily: "var(--font-mono)",
                      animation: "count-up .4s ease",
                    }}>+{r.delta}</span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "var(--font-mono)", textTransform: "uppercase", letterSpacing: ".06em" }}>{r.uni}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 16 }}>
                  {r.xp.toLocaleString("es-CL")}
                </div>
                <div style={{ fontSize: 10, color: "var(--ink-3)", fontFamily: "var(--font-mono)", letterSpacing: ".08em" }}>XP</div>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{
        marginTop: 14, paddingTop: 12, borderTop: "2px dashed var(--line)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
        fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--ink-3)",
      }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <IconCalendar size={13}/> CIERRA LUN 09:00
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--brand-ink)", fontWeight: 800 }}>
          PREMIO TOP 3 · 500 <IconCoin size={14}/>
        </span>
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section style={heroStyles.wrap}>
      <div style={heroStyles.blob1}/>
      <div style={heroStyles.blob2}/>
      <div className="container">
        <div style={heroStyles.grid} className="hero-grid">
          <div>
            <span className="eyebrow"><span className="dot"/> Hecho por estudiantes para estudiantes</span>
            <h1 style={heroStyles.h1}>
              Estudiar deja<br/>de ser una<br/>
              <span style={heroStyles.h1Highlight}>lata.</span>
            </h1>
            <p style={heroStyles.sub}>
              Sincroniza Canvas, mide cada minuto que estudias, gana XP y compite con tu universidad.
              MachReach convierte tu semestre en un juego que <em style={{ color: "var(--ink)", fontStyle: "normal", fontWeight: 700, textDecoration: "underline wavy var(--brand)", textUnderlineOffset: 4 }}>sí quieres ganar</em>.
            </p>
            <div style={heroStyles.ctaRow}>
              <a href="/register" className="btn btn-primary btn-lg">
                <IconCanvas size={20}/> Conectar Canvas
              </a>
              <a href="#how" className="btn btn-ghost btn-lg">
                Ver cómo funciona <IconArrow size={18}/>
              </a>
            </div>
            <div style={heroStyles.trustRow}>
              <div style={heroStyles.avatars}>
                {["VR","MA","FP","IM","SG"].map((t, i) => (
                  <div key={i} style={heroStyles.avatar(i)}>{t}</div>
                ))}
              </div>
              <span>+2.300 estudiantes ganando XP esta semana</span>
            </div>
          </div>
          <div>
            <HeroLeaderboard/>
          </div>
        </div>
      </div>
      <style>{`
        @media (max-width: 920px) {
          .hero-grid { grid-template-columns: 1fr !important; gap: 48px !important; }
        }
      `}</style>
    </section>
  );
}

window.Hero = Hero;
window.Avatar = Avatar;
