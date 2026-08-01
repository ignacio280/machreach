/* ======== Sections: How it works, Canvas callout, Leaderboard showcase, Quiz, Stats, FAQ ======== */

/* -------- HOW IT WORKS -------- */
function HowItWorks() {
  const steps = [
    { n: "01", t: "Conecta tus ramos", d: "Trae tus ramos con la extensión de MachReach o agrégalos a mano en segundos. Sin configuración técnica y sin tocar tus credenciales.", icon: <IconBook/> },
    { n: "02", t: "Estudia con Focus", d: "Elige ramo, prueba y dale start. El reloj cuenta y la app te suma XP.", icon: <IconTimer/> },
    { n: "03", t: "Sube de liga", d: "Compite cada semana con tu universidad y mira cómo sube tu XP.", icon: <IconTrophy/> },
  ];
  return (
    <section id="how" style={{ background: "var(--bg-2)", borderTop: "2px solid var(--line)", borderBottom: "2px solid var(--line)" }}>
      <div className="container">
        <div className="section-head">
          <h2>De cero a estudiando<br/>en menos de un minuto.</h2>
        </div>
        <div className="how-grid" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 22 }}>
          {steps.map((s, i) => (
            <div key={i} style={{ position: "relative" }}>
              <div className="card" style={{ padding: 28, height: "100%" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700, color: "var(--ink-3)", letterSpacing: ".1em" }}>{s.n}</span>
                  <div style={{
                    width: 48, height: 48, borderRadius: 14,
                    background: "var(--brand-soft)", border: "2px solid var(--brand)",
                    display: "grid", placeItems: "center", color: "var(--brand-ink)",
                  }}>{React.cloneElement(s.icon, { size: 24 })}</div>
                </div>
                <h3 style={{ fontSize: 24, marginBottom: 10 }}>{s.t}</h3>
                <p style={{ color: "var(--ink-2)", fontSize: 16 }}>{s.d}</p>
              </div>
              {i < 2 && (
                <div className="how-arrow" style={{
                  position: "absolute", top: "50%", right: -22, transform: "translateY(-50%)",
                  width: 28, height: 28, borderRadius: "50%",
                  background: "var(--brand)", border: "2px solid var(--ink)",
                  display: "grid", placeItems: "center", color: "white", zIndex: 2,
                  boxShadow: "0 3px 0 0 var(--ink)",
                }}>
                  <IconArrow size={14} strokeWidth={3}/>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      <style>{`
        @media (max-width: 880px) {
          .how-grid { grid-template-columns: 1fr !important; }
          .how-arrow { display: none !important; }
        }
      `}</style>
    </section>
  );
}

/* -------- STUDY PLAN CALLOUT (one-shot planner build) -------- */
function CanvasCallout() {
  // Weekly planner: subject blocks assemble once when the section enters view.
  const DAYS = ["L", "M", "M", "J", "V", "S", "D"];
  const PURPLE = "#5B4694", GREEN = "#1E9E72", BLUE = "#3B6FE0", ORANGE = "#F0922E", PINK = "#EC4899";
  // col 1-7, row 1-3, rowSpan, label, color — listed in drop order.
  const blocks = [
    { col: 1, row: 1, span: 2, t: "Cálculo II", c: PURPLE },
    { col: 3, row: 1, span: 1, t: "Inglés",     c: BLUE   },
    { col: 5, row: 1, span: 1, t: "Física",     c: PINK   },
    { col: 2, row: 2, span: 1, t: "Química",    c: GREEN  },
    { col: 4, row: 2, span: 2, t: "Cálculo II", c: PURPLE },
    { col: 3, row: 3, span: 1, t: "Historia",   c: ORANGE },
    { col: 6, row: 3, span: 1, t: "Química",    c: GREEN  },
    { col: 7, row: 1, span: 2, t: "Repaso",     c: ORANGE },
  ];

  const wrapRef = React.useRef(null);
  const [isReady, setIsReady] = React.useState(false);
  React.useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setIsReady(true);
      return;
    }
    const io = new IntersectionObserver((ents) => {
      if (!ents[0].isIntersecting) return;
      setIsReady(true);
      io.disconnect();
    }, { threshold: 0.34, rootMargin: "0px 0px -10% 0px" });
    io.observe(wrap);
    return () => io.disconnect();
  }, []);

  return (
    <section ref={wrapRef} className={"plan-once-wrap" + (isReady ? " is-ready" : "")} style={{ position: "relative" }}>
      <div className="plan-once">
      <div className="container" style={{ width: "100%" }}>
        <div style={{ maxWidth: 960, margin: "0 auto", position: "relative" }}>
          {/* Heading on top, then the planner builds once. */}
          <p style={{
            textAlign: "center", margin: "0 0 24px", fontSize: "clamp(22px,3vw,32px)",
            fontWeight: 800, color: "var(--ink)", letterSpacing: "-.02em",
          }}>Te arma el plan de estudios perfecto.</p>

          <div className="plan-card" style={{
            background: "#FFFFFF", borderRadius: 24, padding: "22px 24px 20px",
            border: "1px solid #ECE6D8", boxShadow: "0 24px 60px rgba(20,18,30,.12)",
          }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 9, marginBottom: 11 }}>
              {DAYS.map((d, i) => (
                <div key={i} style={{ textAlign: "center", fontSize: 12, fontWeight: 800, color: "#9A948A", letterSpacing: ".08em" }}>{d}</div>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gridTemplateRows: "repeat(3, 64px)", gap: 9 }}>
              {Array.from({ length: 21 }).map((_, i) => (
                <div key={"c" + i} style={{ border: "1.5px dashed #E4DECF", borderRadius: 12 }}/>
              ))}
              {blocks.map((b, i) => {
                return (
                  <div key={"b" + i} className="plan-block" style={{
                    "--delay": (90 + i * 62) + "ms",
                    gridColumn: b.col, gridRow: b.row + " / span " + b.span,
                    background: b.c, color: "white", borderRadius: 12, padding: "10px 13px",
                    fontWeight: 800, fontSize: 13, lineHeight: 1.2,
                    display: "flex", alignItems: "flex-start",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    boxShadow: "0 8px 18px rgba(0,0,0,.14)",
                  }}>{b.t}</div>
                );
              })}
            </div>
          </div>

          {/* "Tu plan está listo" badge below. */}
          <div style={{ display: "flex", justifyContent: "center", marginTop: 24 }}>
            <div className="plan-ready-badge" style={{
              background: GREEN, color: "white", fontWeight: 800, fontSize: 15,
              padding: "11px 22px", borderRadius: 999, whiteSpace: "nowrap",
              boxShadow: "0 12px 28px rgba(30,158,114,.4)",
            }}>✓ Tu plan está listo</div>
          </div>
        </div>
      </div>
      </div>
      <style>{`
        .plan-once-wrap {
          padding: 72px 0 84px;
          margin: -16px 0 0;
        }
        .plan-once {
          position: relative;
          display: block;
        }
        .plan-card {
          opacity: 0.001;
          transform: translateY(18px) scale(0.985);
          transition: opacity .42s ease, transform .52s cubic-bezier(.2,.86,.2,1);
        }
        .plan-block {
          opacity: 0;
          transform: translateY(-16px) scale(0.9);
          transition: opacity .28s ease, transform .42s cubic-bezier(.34,1.56,.64,1);
          transition-delay: var(--delay);
        }
        .plan-ready-badge {
          opacity: 0;
          transform: translateY(10px) scale(.82);
          transition: opacity .34s ease, transform .46s cubic-bezier(.34,1.56,.64,1);
          transition-delay: 680ms;
        }
        .plan-once-wrap.is-ready .plan-card,
        .plan-once-wrap.is-ready .plan-block,
        .plan-once-wrap.is-ready .plan-ready-badge {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
        @media (max-width: 900px) {
          .plan-once-wrap { margin: 0; padding: 48px 0 58px; }
        }
        @media (prefers-reduced-motion: reduce) {
          .plan-card,
          .plan-block,
          .plan-ready-badge {
            opacity: 1;
            transform: none;
            transition: none;
          }
        }
      `}</style>
    </section>
  );
}

/* -------- LEADERBOARDS SHOWCASE — with podium -------- */
function LeaderboardShowcaseLegacy() {
  const [scope, setScope] = React.useState("uni");
  const data = {
    pais:    [{ n: "Catalina",  u: "UC",   xp: 28400, c: "#fb923c" }, { n: "Joaquín",  u: "PUCV", xp: 24100, c: "#a78bfa" }, { n: "tú", u: "Tu universidad", xp: 22850, c: "var(--brand)", you: true }, { n: "Renata", u: "USS",  xp: 20180, c: "#22d3ee" }, { n: "Diego",    u: "UAI",  xp: 18910, c: "#34d399" }],
    uni:     [{ n: "Sofia_Db",  u: "Tu universidad",  xp: 24100, c: "#fb923c" }, { n: "tú",       u: "Tu universidad",  xp: 22850, c: "var(--brand)", you: true }, { n: "Antonia",  u: "Tu universidad", xp: 19200, c: "#a78bfa" }, { n: "Tomás",   u: "Tu universidad",  xp: 17640, c: "#22d3ee" }, { n: "Sofía",    u: "Tu universidad",  xp: 14720, c: "#34d399" }],
    carrera: [{ n: "Magdalena", u: "Ing.", xp: 18900, c: "#fb923c" }, { n: "tú",       u: "Ing.", xp: 16200, c: "var(--brand)", you: true }, { n: "Pablo",    u: "Ing.", xp: 14820, c: "#a78bfa" }, { n: "Camila",  u: "Ing.", xp: 12410, c: "#22d3ee" }, { n: "Benja",    u: "Ing.", xp: 10940, c: "#34d399" }],
  };

  /* Podium uses top 3 */
  const podium = [...data[scope]].slice(0, 3);
  // arrange as [#2, #1, #3]
  const podiumOrder = [podium[1], podium[0], podium[2]];
  const podiumHeights = [80, 110, 64];
  const podiumMedals = ["var(--silver)", "var(--gold)", "var(--bronze)"];
  const podiumRanks = [2, 1, 3];

  return (
    <section style={{ background: "var(--bg-2)", borderTop: "2px solid var(--line)", borderBottom: "2px solid var(--line)" }}>
      <div className="container">
        <div className="section-head">
          <h2>Rankings semanales que<br/>te sacan a estudiar.</h2>
          <p>Tres niveles: tu carrera, tu universidad, tu país. Se reinician cada lunes para que siempre tengas una meta nueva.</p>
        </div>
        <div className="lb-wrap" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 36, alignItems: "center" }}>
          <div>
            <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
              {[
                { k: "carrera", l: "Por carrera" },
                { k: "uni",     l: "Por universidad" },
                { k: "pais",    l: "Por país" },
              ].map(t => (
                <button key={t.k} onClick={() => setScope(t.k)} style={{
                  padding: "10px 18px", borderRadius: 12, border: "2px solid var(--ink)",
                  background: scope === t.k ? "var(--brand)" : "var(--surface)",
                  color: scope === t.k ? "white" : "var(--ink)",
                  fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14,
                  boxShadow: scope === t.k ? "0 3px 0 0 var(--ink)" : "0 2px 0 0 var(--ink)",
                  cursor: "pointer",
                }}>{t.l}</button>
              ))}
            </div>
            <h3 style={{ fontSize: 28, marginBottom: 10 }}>Tres ligas, un objetivo: <span style={{ color: "var(--brand)" }}>quedar arriba.</span></h3>
            <p style={{ color: "var(--ink-2)", fontSize: 16, marginBottom: 16 }}>
              Cada semana arranca un nuevo ranking. Acumula XP estudiando con Focus y sube por rangos reales como Iniciados, Aprendices, Estudiosos e Investigadores.
            </p>
            <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                { t: "Top 3 semanal: constancia destacada en el ranking", c: "var(--gold)" },
                { t: "Top 10 mensual: progreso acumulado del mes", c: "var(--silver)" },
                { t: "Investigadores: rango visible por XP y racha", c: "var(--secondary)" },
              ].map((r, i) => (
                <li key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 600, fontSize: 15 }}>
                  <span style={{
                    width: 22, height: 22, borderRadius: 6,
                    background: r.c, border: "2px solid var(--ink)",
                    color: "white", display: "grid", placeItems: "center",
                  }}><IconCheck size={14} strokeWidth={3}/></span>
                  {r.t}
                </li>
              ))}
            </ul>
          </div>

          {/* Right: leaderboard card */}
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            {/* Header */}
            <div style={{
              padding: "16px 20px",
              background: "linear-gradient(180deg, var(--brand-soft), transparent)",
              borderBottom: "2px solid var(--line)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 18 }}>
                    Investigadores · {scope === "pais" ? "Chile" : scope === "uni" ? "Tu universidad" : "Ingeniería"}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "var(--font-mono)", letterSpacing: ".06em", marginTop: 2 }}>
                    SEMANA 26 · CIERRA LUN 09:00
                  </div>
                </div>
                <span className="tag" style={{ borderColor: "var(--good)", color: "var(--good)", background: "color-mix(in oklab, var(--good) 12%, var(--surface))" }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--good)" }}/> live
                </span>
              </div>
            </div>

            {/* Podium */}
            <div style={{
              padding: "24px 20px 12px",
              background: "var(--bg-2)",
              borderBottom: "2px dashed var(--line)",
            }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", alignItems: "end", gap: 8 }}>
                {podiumOrder.map((p, idx) => {
                  if (!p) return <div key={idx}/>;
                  const rank = podiumRanks[idx];
                  return (
                    <div key={p.n + scope + idx} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
                      <Avatar name={p.n} color={p.c} size={rank === 1 ? 50 : 40} you={p.you}/>
                      <div style={{ fontWeight: 800, fontSize: 12, fontFamily: "var(--font-display)" }}>{p.n}</div>
                      <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--ink-3)", letterSpacing: ".05em" }}>{p.xp.toLocaleString("es-CL")} XP</div>
                      <div style={{
                        width: "100%", height: podiumHeights[idx],
                        background: podiumMedals[idx],
                        border: "2px solid var(--ink)",
                        borderRadius: "10px 10px 0 0",
                        boxShadow: "0 -3px 0 0 color-mix(in oklab, black 12%, transparent) inset",
                        display: "grid", placeItems: "center",
                        fontFamily: "var(--font-display)", fontWeight: 800, color: "white", fontSize: 28,
                        position: "relative",
                      }}>
                        {rank}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Rest of rows (4-5) */}
            <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 4 }}>
              {data[scope].slice(3).map((r, i) => (
                <div key={r.n + scope} style={{
                  display: "grid", gridTemplateColumns: "28px 36px 1fr auto",
                  alignItems: "center", gap: 12,
                  padding: "8px 10px", borderRadius: 12,
                  background: r.you ? "var(--brand-soft)" : "transparent",
                  border: r.you ? "2px solid var(--brand)" : "2px solid transparent",
                }}>
                  <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 16, color: "var(--ink-3)", textAlign: "center" }}>{i + 4}</div>
                  <Avatar name={r.n} color={r.c} size={32} you={r.you}/>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 13 }}>{r.n}</div>
                    <div style={{ fontSize: 10, color: "var(--ink-3)", fontFamily: "var(--font-mono)", letterSpacing: ".06em" }}>{r.u}</div>
                  </div>
                  <div style={{ textAlign: "right", fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14 }}>
                    {r.xp.toLocaleString("es-CL")} <span style={{ fontSize: 9, color: "var(--ink-3)" }}>XP</span>
                  </div>
                </div>
              ))}
              {/* Your row callout if not in top */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8, padding: "8px 10px", background: "var(--ink)", color: "white", borderRadius: 10 }}>
                <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", letterSpacing: ".08em" }}>TU POSICIÓN ACTUAL</span>
                <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14, color: "var(--accent)" }}>#{data[scope].findIndex(r => r.you) + 1}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <style>{`@media (max-width: 880px) { .lb-wrap { grid-template-columns: 1fr !important; } }`}</style>
    </section>
  );
}

function MobileLeaderboardDemo() {
  const rows = [
    { rank: 1, name: "Sofia_Db", xp: "24.100", tone: "#F59E0B" },
    { rank: 2, name: "tú", xp: "22.850", tone: "var(--brand)", you: true },
    { rank: 3, name: "Antonia", xp: "19.200", tone: "#8B5CF6" },
  ];
  return (
    <div className="mobile-leaderboard-demo" aria-label="Ejemplo simplificado de ranking semanal">
      <div style={{
        border: "2px solid var(--ink)", borderRadius: 24, overflow: "hidden",
        background: "var(--surface)", boxShadow: "0 5px 0 var(--ink)",
      }}>
        <div style={{
          padding: "15px 16px", display: "flex", justifyContent: "space-between",
          alignItems: "center", gap: 12, background: "var(--brand-soft)",
          borderBottom: "2px solid var(--ink)",
        }}>
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 800 }}>Tu universidad</div>
            <div style={{ fontSize: 11, color: "var(--ink-2)", fontWeight: 800 }}>SEMANA 26 · EN VIVO</div>
          </div>
          <span className="tag" style={{ background: "var(--surface)", color: "var(--brand-ink)" }}>+220 XP hoy</span>
        </div>
        <div style={{ padding: 10, display: "grid", gap: 8 }}>
          {rows.map((row) => (
            <div key={row.rank} style={{
              display: "grid", gridTemplateColumns: "34px minmax(0, 1fr) auto",
              alignItems: "center", gap: 10, padding: "12px 10px", borderRadius: 14,
              background: row.you ? "var(--brand-soft)" : "var(--bg-2)",
              border: row.you ? "2px solid var(--brand)" : "2px solid transparent",
            }}>
              <span style={{
                width: 28, height: 28, borderRadius: 9, display: "grid", placeItems: "center",
                background: row.tone, color: "white", fontFamily: "var(--font-display)",
                fontWeight: 900, border: "2px solid var(--ink)",
              }}>{row.rank}</span>
              <span style={{ minWidth: 0, fontWeight: 900 }}>
                {row.name}{row.you && <small style={{ marginLeft: 7, color: "var(--brand-ink)" }}>TU POSICIÓN</small>}
              </span>
              <strong style={{ fontFamily: "var(--font-display)", whiteSpace: "nowrap" }}>{row.xp} XP</strong>
            </div>
          ))}
        </div>
      </div>
      <p style={{ marginTop: 16, color: "var(--ink-2)", fontSize: 15 }}>
        Estudia con Focus, suma XP y vuelve a competir desde cero cada lunes.
      </p>
    </div>
  );
}

function LeaderboardShowcase() {
  const [scope, setScope] = React.useState("uni");
  const [previewScope, setPreviewScope] = React.useState(null);
  const [selected, setSelected] = React.useState(null);
  const [pulseIndex, setPulseIndex] = React.useState(0);
  const data = {
    pais: [
      { n: "Catalina", u: "UC", xp: 28400, c: "#fb923c", delta: 320, streak: 14 },
      { n: "Joaquín", u: "PUCV", xp: 24100, c: "#a78bfa", delta: 210, streak: 9 },
      { n: "tú", u: "Tu universidad", xp: 22850, c: "var(--brand)", you: true, delta: 180, streak: 11 },
      { n: "Renata", u: "USS", xp: 20180, c: "#22d3ee", delta: 145, streak: 6 },
      { n: "Diego", u: "UAI", xp: 18910, c: "#34d399", delta: 120, streak: 5 },
    ],
    uni: [
      { n: "Sofia_Db", u: "Tu universidad", xp: 24100, c: "#fb923c", delta: 260, streak: 12 },
      { n: "tú", u: "Tu universidad", xp: 22850, c: "var(--brand)", you: true, delta: 220, streak: 11 },
      { n: "Antonia", u: "Tu universidad", xp: 19200, c: "#a78bfa", delta: 170, streak: 7 },
      { n: "Tomás", u: "Tu universidad", xp: 17640, c: "#22d3ee", delta: 130, streak: 6 },
      { n: "Sofía", u: "Tu universidad", xp: 14720, c: "#34d399", delta: 95, streak: 4 },
    ],
    carrera: [
      { n: "Magdalena", u: "Ing.", xp: 18900, c: "#fb923c", delta: 190, streak: 8 },
      { n: "tú", u: "Ing.", xp: 16200, c: "var(--brand)", you: true, delta: 160, streak: 11 },
      { n: "Pablo", u: "Ing.", xp: 14820, c: "#a78bfa", delta: 120, streak: 5 },
      { n: "Camila", u: "Ing.", xp: 12410, c: "#22d3ee", delta: 100, streak: 4 },
      { n: "Benja", u: "Ing.", xp: 10940, c: "#34d399", delta: 80, streak: 3 },
    ],
  };
  const themes = {
    carrera: {
      primary: "#8B5CF6",
      accent: "#22D3EE",
      title: "Ingeniería",
      headline: "tu carrera.",
      micro: "Rivales del mismo ramo",
      stats: ["12 carreras", "+160 XP hoy", "racha 11"],
    },
    uni: {
      primary: "#FF7A3D",
      accent: "#9BEE43",
      title: "Tu universidad",
      headline: "tu universidad.",
      micro: "Tu universidad está prendida",
      stats: ["42 cursos", "+220 XP hoy", "top 3 cerca"],
    },
    pais: {
      primary: "#14B8D6",
      accent: "#FF7A3D",
      title: "Chile",
      headline: "todo Chile.",
      micro: "Ranking nacional en vivo",
      stats: ["12 Ues", "+180 XP hoy", "#3 actual"],
    },
  };
  const displayScope = previewScope || scope;
  const rows = data[displayScope];
  const theme = themes[displayScope];
  const selectedRow = rows.find(r => r.n === selected) || rows.find(r => r.you) || rows[0];
  const selectedRank = rows.findIndex(r => r.n === selectedRow.n) + 1;
  const yourRank = rows.findIndex(r => r.you) + 1;
  const podium = rows.slice(0, 3);
  const podiumOrder = [podium[1], podium[0], podium[2]];
  const podiumHeights = [80, 110, 64];
  const podiumRanks = [2, 1, 3];
  const podiumMedals = [
    "linear-gradient(180deg, color-mix(in oklab, var(--lb-primary) 24%, white), var(--silver))",
    "linear-gradient(180deg, var(--lb-accent), var(--lb-primary))",
    "linear-gradient(180deg, color-mix(in oklab, var(--lb-primary) 38%, var(--bronze)), var(--bronze))",
  ];

  React.useEffect(() => {
    const id = setInterval(() => setPulseIndex(i => (i + 1) % data[scope].length), 2200);
    return () => clearInterval(id);
  }, [scope]);

  React.useEffect(() => {
    setSelected(null);
    setPulseIndex(0);
  }, [scope]);

  const activateScope = (k) => {
    setScope(k);
    setPreviewScope(null);
  };

  return (
    <section id="leaderboard" className="lb-showcase" style={{
      "--lb-primary": theme.primary,
      "--lb-accent": theme.accent,
      background: "linear-gradient(135deg, color-mix(in oklab, var(--lb-primary) 10%, var(--bg-2)) 0%, var(--bg-2) 42%, color-mix(in oklab, var(--lb-accent) 10%, var(--bg-2)) 100%)",
      borderTop: "2px solid var(--line)",
      borderBottom: "2px solid var(--line)",
      transition: "background .28s ease",
    }}>
      <div className="container">
        <div className="section-head">
          <h2>Rankings semanales que<br/>te sacan a estudiar.</h2>
          <p>Tres niveles: tu carrera, tu universidad, tu país. Se reinician cada lunes para que siempre tengas una meta nueva.</p>
        </div>
        <MobileLeaderboardDemo/>
        <div className="lb-wrap" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 36, alignItems: "center" }}>
          <div>
            <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
              {[
                { k: "carrera", l: "Por carrera" },
                { k: "uni", l: "Por universidad" },
                { k: "pais", l: "Por país" },
              ].map(t => (
                <button
                  key={t.k}
                  className="lb-scope-btn"
                  onClick={() => activateScope(t.k)}
                  onMouseEnter={() => setPreviewScope(t.k)}
                  onMouseLeave={() => setPreviewScope(null)}
                  onFocus={() => setPreviewScope(t.k)}
                  onBlur={() => setPreviewScope(null)}
                  style={{
                    padding: "10px 18px",
                    borderRadius: 12,
                    border: "2px solid var(--ink)",
                    background: displayScope === t.k ? "var(--lb-primary)" : "var(--surface)",
                    color: displayScope === t.k ? "white" : "var(--ink)",
                    fontFamily: "var(--font-display)",
                    fontWeight: 800,
                    fontSize: 14,
                    boxShadow: scope === t.k ? "0 4px 0 0 var(--ink)" : displayScope === t.k ? "0 3px 0 0 var(--ink)" : "0 2px 0 0 var(--ink)",
                    cursor: "pointer",
                  }}
                >
                  {t.l}
                </button>
              ))}
            </div>
            <h3 style={{ fontSize: 28, marginBottom: 10 }}>Tres ligas, un objetivo: <span style={{ color: "var(--lb-primary)" }}>{theme.headline}</span></h3>
            <p style={{ color: "var(--ink-2)", fontSize: 16, marginBottom: 16 }}>
              Cada semana arranca un nuevo ranking. Acumula XP estudiando con Focus y sube por rangos reales como Iniciados, Aprendices, Estudiosos e Investigadores.
            </p>
            <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                { t: "Top 3 semanal: constancia destacada en el ranking", c: "var(--gold)" },
                { t: "Top 10 mensual: progreso acumulado del mes", c: "var(--silver)" },
                { t: "Investigadores: rango visible por XP y racha", c: "var(--lb-primary)" },
              ].map((r, i) => (
                <li key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 600, fontSize: 15 }}>
                  <span style={{
                    width: 22,
                    height: 22,
                    borderRadius: 6,
                    background: r.c,
                    border: "2px solid var(--ink)",
                    color: "white",
                    display: "grid",
                    placeItems: "center",
                  }}><IconCheck size={14} strokeWidth={3}/></span>
                  {r.t}
                </li>
              ))}
            </ul>
            <div className="lb-insight" style={{
              marginTop: 20,
              padding: 16,
              border: "2px solid var(--ink)",
              borderRadius: 18,
              background: "linear-gradient(135deg, var(--surface), color-mix(in oklab, var(--lb-primary) 11%, var(--surface)))",
              boxShadow: "0 4px 0 0 var(--ink)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: ".1em", textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 800 }}>{theme.micro}</div>
                  <div style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 800, marginTop: 3 }}>{selectedRow.n} <span style={{ color: "var(--lb-primary)" }}>#{selectedRank}</span></div>
                </div>
                <div style={{ minWidth: 88, textAlign: "right", fontFamily: "var(--font-display)", color: "var(--lb-primary)", fontWeight: 800, fontSize: 18 }}>+{selectedRow.delta} XP</div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                {theme.stats.map(s => (
                  <span key={s} style={{
                    border: "1.5px solid color-mix(in oklab, var(--lb-primary) 50%, var(--ink))",
                    background: "color-mix(in oklab, var(--lb-primary) 10%, var(--surface))",
                    color: "var(--ink)",
                    borderRadius: 999,
                    padding: "5px 10px",
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                    fontWeight: 800,
                    letterSpacing: ".04em",
                  }}>{s}</span>
                ))}
              </div>
              <div style={{ marginTop: 12, height: 8, borderRadius: 999, border: "1.5px solid var(--ink)", background: "var(--surface)", overflow: "hidden" }}>
                <div className="lb-meter" style={{
                  width: `${Math.min(94, 34 + selectedRow.delta / 4)}%`,
                  height: "100%",
                  background: "linear-gradient(90deg, var(--lb-primary), var(--lb-accent))",
                }}/>
              </div>
            </div>
          </div>

          <div key={displayScope} className="card lb-arena" style={{
            padding: 0,
            overflow: "hidden",
            background: "var(--surface)",
            boxShadow: "0 6px 0 0 var(--ink), 0 22px 50px color-mix(in oklab, var(--lb-primary) 20%, transparent)",
          }}>
            <div style={{
              padding: "16px 20px",
              background: "linear-gradient(135deg, color-mix(in oklab, var(--lb-primary) 16%, var(--surface)), color-mix(in oklab, var(--lb-accent) 10%, var(--surface)))",
              borderBottom: "2px solid var(--line)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 18 }}>Investigadores · {theme.title}</div>
                  <div style={{ fontSize: 11, color: "var(--ink-3)", fontFamily: "var(--font-mono)", letterSpacing: ".06em", marginTop: 2 }}>SEMANA 26 · CIERRA LUN 09:00</div>
                </div>
                <span className="tag lb-live-pill" style={{ borderColor: "var(--lb-primary)", color: "var(--lb-primary)", background: "color-mix(in oklab, var(--lb-primary) 12%, var(--surface))" }}>
                  <span className="lb-live-dot" style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--lb-primary)" }}/> live
                </span>
              </div>
            </div>

            <div className="lb-stage" style={{
              padding: "24px 20px 12px",
              background: "linear-gradient(180deg, color-mix(in oklab, var(--lb-primary) 7%, var(--bg-2)), var(--bg-2))",
              backgroundImage: "linear-gradient(180deg, color-mix(in oklab, var(--lb-primary) 7%, var(--bg-2)), var(--bg-2)), linear-gradient(135deg, color-mix(in oklab, var(--lb-primary) 16%, transparent) 0 1px, transparent 1px 18px)",
              borderBottom: "2px dashed var(--line)",
            }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", alignItems: "end", gap: 8 }}>
                {podiumOrder.map((p, idx) => {
                  if (!p) return <div key={idx}/>;
                  const rank = podiumRanks[idx];
                  const isSelected = selectedRow.n === p.n;
                  const isPulsing = rows[pulseIndex] && rows[pulseIndex].n === p.n;
                  return (
                    <div
                      role="button"
                      tabIndex={0}
                      key={p.n + displayScope + idx}
                      className={`lb-podium-cell ${isSelected ? "is-selected" : ""} ${isPulsing ? "is-pulsing" : ""}`}
                      onClick={() => setSelected(p.n)}
                      onMouseEnter={() => setSelected(p.n)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSelected(p.n); }}
                      style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, animationDelay: `${idx * 90}ms` }}
                    >
                      <Avatar name={p.n} color={p.c} size={rank === 1 ? 50 : 40} you={p.you}/>
                      <div style={{ fontWeight: 800, fontSize: 12, fontFamily: "var(--font-display)" }}>{p.n}</div>
                      <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--ink-3)", letterSpacing: ".05em" }}>{p.xp.toLocaleString("es-CL")} XP</div>
                      <div className="lb-podium-bar" style={{
                        width: "100%",
                        height: podiumHeights[idx],
                        background: podiumMedals[idx],
                        border: "2px solid var(--ink)",
                        borderRadius: "10px 10px 0 0",
                        boxShadow: isSelected ? "0 0 0 4px color-mix(in oklab, var(--lb-primary) 22%, transparent), 0 -3px 0 0 color-mix(in oklab, black 12%, transparent) inset" : "0 -3px 0 0 color-mix(in oklab, black 12%, transparent) inset",
                        display: "grid",
                        placeItems: "center",
                        fontFamily: "var(--font-display)",
                        fontWeight: 800,
                        color: "white",
                        fontSize: 28,
                        position: "relative",
                      }}>
                        {rank}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 4 }}>
              {rows.slice(3).map((r, i) => {
                const isSelected = selectedRow.n === r.n;
                const isPulsing = rows[pulseIndex] && rows[pulseIndex].n === r.n;
                return (
                  <div
                    role="button"
                    tabIndex={0}
                    key={r.n + displayScope}
                    className={`lb-row-item ${isSelected ? "is-selected" : ""} ${isPulsing ? "is-pulsing" : ""}`}
                    onClick={() => setSelected(r.n)}
                    onMouseEnter={() => setSelected(r.n)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSelected(r.n); }}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "28px 36px 1fr auto",
                      alignItems: "center",
                      gap: 12,
                      padding: "8px 10px",
                      borderRadius: 12,
                      background: isSelected ? "color-mix(in oklab, var(--lb-primary) 14%, var(--surface))" : r.you ? "var(--brand-soft)" : "transparent",
                      border: isSelected ? "2px solid var(--lb-primary)" : r.you ? "2px solid var(--brand)" : "2px solid transparent",
                      animationDelay: `${i * 80 + 220}ms`,
                    }}
                  >
                    <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 16, color: "var(--ink-3)", textAlign: "center" }}>{i + 4}</div>
                    <Avatar name={r.n} color={r.c} size={32} you={r.you}/>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{r.n}</div>
                      <div style={{ fontSize: 10, color: "var(--ink-3)", fontFamily: "var(--font-mono)", letterSpacing: ".06em" }}>{r.u}</div>
                    </div>
                    <div style={{ textAlign: "right", fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14 }}>
                      {r.xp.toLocaleString("es-CL")} <span style={{ fontSize: 9, color: "var(--ink-3)" }}>XP</span>
                    </div>
                  </div>
                );
              })}
              <div className="lb-you-callout" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8, padding: "8px 10px", background: "linear-gradient(90deg, var(--ink), color-mix(in oklab, var(--lb-primary) 35%, var(--ink)))", color: "white", borderRadius: 10 }}>
                <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", letterSpacing: ".08em" }}>TU POSICIÓN ACTUAL</span>
                <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14, color: "var(--lb-accent)" }}>#{yourRank}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <style>{`
        .lb-scope-btn,
        .lb-row-item,
        .lb-podium-cell,
        .lb-arena,
        .lb-insight {
          transition: transform .18s ease, box-shadow .18s ease, background .18s ease, border-color .18s ease, color .18s ease;
        }
        .lb-scope-btn:hover,
        .lb-scope-btn:focus-visible {
          transform: translate(-1px, -2px);
          outline: none;
        }
        .lb-arena:hover,
        .lb-insight:hover {
          transform: translateY(-3px);
        }
        .lb-live-dot {
          animation: lbLivePulse 1.2s ease-in-out infinite;
        }
        .lb-live-pill {
          box-shadow: 0 0 0 0 color-mix(in oklab, var(--lb-primary) 30%, transparent);
          animation: lbTagPulse 2.4s ease infinite;
        }
        .lb-podium-cell {
          cursor: pointer;
          text-align: center;
          animation: lbPodiumIn .48s cubic-bezier(.2,.8,.2,1) both;
        }
        .lb-podium-cell:hover,
        .lb-podium-cell.is-selected {
          transform: translateY(-5px);
        }
        .lb-podium-cell.is-pulsing .lb-podium-bar,
        .lb-row-item.is-pulsing {
          box-shadow: 0 0 0 4px color-mix(in oklab, var(--lb-accent) 24%, transparent), 0 10px 24px color-mix(in oklab, var(--lb-primary) 18%, transparent);
        }
        .lb-podium-bar {
          transform-origin: bottom;
          animation: lbBarBuild .5s cubic-bezier(.2,.8,.2,1) both;
        }
        .lb-row-item {
          cursor: pointer;
          width: 100%;
          text-align: left;
          animation: lbRowIn .42s cubic-bezier(.2,.8,.2,1) both;
        }
        .lb-row-item:hover,
        .lb-row-item.is-selected {
          transform: translateX(5px);
        }
        .lb-meter {
          transform-origin: left center;
          animation: lbMeterSweep .46s ease both;
          transition: width .22s ease;
        }
        .lb-you-callout {
          animation: lbRowIn .48s cubic-bezier(.2,.8,.2,1) both;
        }
        @keyframes lbPodiumIn {
          from { opacity: 0; transform: translateY(18px) scale(.96); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes lbRowIn {
          from { opacity: 0; transform: translateX(18px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes lbBarBuild {
          from { transform: scaleY(.25); filter: saturate(.7); }
          to { transform: scaleY(1); filter: saturate(1); }
        }
        @keyframes lbMeterSweep {
          from { transform: scaleX(.2); }
          to { transform: scaleX(1); }
        }
        @keyframes lbLivePulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.55); opacity: .62; }
        }
        @keyframes lbTagPulse {
          0%, 100% { box-shadow: 0 0 0 0 color-mix(in oklab, var(--lb-primary) 22%, transparent); }
          50% { box-shadow: 0 0 0 8px transparent; }
        }
        @media (max-width: 880px) {
          .lb-wrap { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </section>
  );
}

/* -------- QUIZ DEMO -------- */
function QuizDemo() {
  const questions = [
    { q: "¿Cuál es el límite de sin(x)/x cuando x → 0?", opts: ["0", "1", "∞", "no existe"], correct: 1, ramo: "Cálculo I" },
    { q: "Una matriz cuadrada es invertible si...", opts: ["su determinante es 0", "su determinante es ≠ 0", "es simétrica", "tiene filas iguales"], correct: 1, ramo: "Álgebra Lineal" },
    { q: "La aceleración en MAS es máxima en...", opts: ["el equilibrio", "los extremos", "la mitad", "es constante"], correct: 1, ramo: "Física II" },
  ];
  const [i, setI] = React.useState(0);
  const [picked, setPicked] = React.useState(null);
  const q = questions[i];

  const choose = (k) => {
    if (picked !== null) return;
    setPicked(k);
  };
  const next = () => {
    setPicked(null);
    setI((i + 1) % questions.length);
  };

  return (
    <section>
      <div className="container">
        <div className="section-head">
          <h2>Quizzes con IA, hechos<br/>por tu propia universidad.</h2>
          <p>Practica con cuestionarios generados a partir del material real de tus cursos. Son herramientas privadas de estudio, no una red comunitaria.</p>
        </div>
        <div className="quiz-wrap" style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 36, alignItems: "center" }}>
          <div>
            <h3 style={{ fontSize: 26, marginBottom: 14 }}>Material desordenado entra. Quiz ordenado sale.</h3>
            <p style={{ color: "var(--ink-2)", fontSize: 16, marginBottom: 22 }}>
              Sube un PDF o tus propios apuntes. La IA genera preguntas relevantes para tu próxima prueba en segundos.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[
                { ic: <IconBook/>,   t: "Aprende del material real, no de internet aleatorio" },
                { ic: <IconPeople/>, t: "Mantén tus materiales y preguntas dentro de tu cuenta" },
                { ic: <IconCoin/>,   t: "El progreso real viene del Focus, no de farmear quizzes" },
              ].map((r, k) => (
                <div key={k} style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <div style={{
                    width: 40, height: 40, borderRadius: 12,
                    background: "var(--secondary-soft)", border: "2px solid var(--secondary)",
                    color: "var(--secondary)", display: "grid", placeItems: "center",
                  }}>{React.cloneElement(r.ic, { size: 20 })}</div>
                  <span style={{ fontWeight: 600 }}>{r.t}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            {/* Quiz top bar */}
            <div className="quiz-top-bar" style={{
              padding: "14px 20px",
              background: "var(--ink)", color: "white",
              display: "grid", gridTemplateColumns: "auto 1fr", alignItems: "center", gap: 14,
            }}>
              <span style={{
                fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700,
                background: "var(--brand)", padding: "4px 10px", borderRadius: 6,
                letterSpacing: ".06em",
              }}>{q.ramo}</span>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {/* progress bar */}
                <div style={{ flex: 1, height: 8, background: "color-mix(in oklab, white 14%, transparent)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: ((i + 1) / questions.length) * 100 + "%", height: "100%", background: "var(--accent)", transition: "width .3s ease" }}/>
                </div>
                <span className="quiz-counter" style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "color-mix(in oklab, white 70%, transparent)" }}>{i + 1}/{questions.length}</span>
              </div>
            </div>

            <div style={{ padding: 26 }}>
              <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 24, marginBottom: 20, lineHeight: 1.25 }}>{q.q}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {q.opts.map((o, k) => {
                  const isPicked = picked === k;
                  const isCorrect = k === q.correct;
                  let bg = "var(--surface)", bd = "var(--line-2)", color = "var(--ink)", shadow = "0 2px 0 0 var(--line-2)";
                  if (picked !== null) {
                    if (isCorrect) { bg = "color-mix(in oklab, var(--good) 18%, var(--surface))"; bd = "var(--good)"; shadow = "0 2px 0 0 var(--good)"; }
                    else if (isPicked) { bg = "color-mix(in oklab, var(--bad) 14%, var(--surface))"; bd = "var(--bad)"; shadow = "0 2px 0 0 var(--bad)"; }
                  }
                  return (
                    <button key={k} onClick={() => choose(k)} style={{
                      padding: "14px 18px", borderRadius: 14,
                      border: "2px solid " + bd, background: bg, color,
                      boxShadow: shadow,
                      textAlign: "left", fontWeight: 700, fontSize: 16,
                      display: "grid", gridTemplateColumns: "28px 1fr auto", alignItems: "center", gap: 12,
                      cursor: picked === null ? "pointer" : "default",
                      transition: "all .2s ease",
                    }}>
                      <span style={{
                        fontFamily: "var(--font-mono)", fontSize: 12,
                        width: 28, height: 28, borderRadius: 8,
                        background: picked !== null && isCorrect ? "var(--good)" : picked !== null && isPicked ? "var(--bad)" : "var(--bg-2)",
                        color: (picked !== null && (isCorrect || isPicked)) ? "white" : "var(--ink-2)",
                        border: "2px solid var(--ink)",
                        display: "grid", placeItems: "center", fontWeight: 800,
                      }}>{["A","B","C","D"][k]}</span>
                      <span>{o}</span>
                      {picked !== null && isCorrect && <IconCheck size={20} color="var(--good)" strokeWidth={3}/>}
                      {picked !== null && isPicked && !isCorrect && <IconClose size={20} color="var(--bad)" strokeWidth={3}/>}
                    </button>
                  );
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 20, paddingTop: 16, borderTop: "2px dashed var(--line)" }}>
                <span style={{ fontSize: 13, color: picked === null ? "var(--ink-3)" : picked === q.correct ? "var(--good)" : "var(--bad)", fontFamily: "var(--font-mono)", fontWeight: 700 }}>
                  {picked === null ? "Elige una respuesta" : picked === q.correct ? "✓ Correcto" : "Revisa la respuesta correcta"}
                </span>
                <button onClick={next} className="btn btn-ghost btn-sm">
                  Siguiente <IconArrow size={14}/>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <style>{`@media (max-width: 880px) { .quiz-wrap { grid-template-columns: 1fr !important; } }`}</style>
    </section>
  );
}

/* -------- STATS STRIP -------- */
function useCountUp(target, active) {
  const [v, setV] = React.useState(0);
  React.useEffect(() => {
    if (!active) return;
    let start = null;
    const dur = 1400;
    const step = (ts) => {
      if (!start) start = ts;
      const p = Math.min(1, (ts - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      setV(Math.floor(target * eased));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [target, active]);
  return v;
}
function StatsStrip() {
  const ref = React.useRef(null);
  const [active, setActive] = React.useState(false);
  React.useEffect(() => {
    const obs = new IntersectionObserver((es) => es.forEach(e => e.isIntersecting && setActive(true)), { threshold: 0.3 });
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, []);
  const stats = [
    { v: useCountUp(2347, active), l: "estudiantes activos esta semana", suf: "" },
    { v: useCountUp(184,  active), l: "horas estudiadas hoy", suf: "h" },
    { v: useCountUp(12,   active), l: "universidades conectadas", suf: "" },
    { v: useCountUp(98,   active), l: "satisfacción de usuarios", suf: "%" },
  ];
  return (
    <section ref={ref} style={{ paddingTop: 64, paddingBottom: 64 }}>
      <div className="container">
        <div className="stats-grid" style={{
          display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 0,
          background: "var(--surface)", border: "2px solid var(--ink)", borderRadius: 24,
          boxShadow: "0 6px 0 0 var(--ink)", overflow: "visible", marginBottom: 10,
        }}>
          {stats.map((s, i) => (
            <div key={i} style={{
              padding: "32px 24px", textAlign: "center",
              borderRight: i < stats.length - 1 ? "2px solid var(--line)" : "none",
            }}>
              <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: "clamp(36px, 4vw, 52px)", color: "var(--brand)" }}>
                {s.v.toLocaleString("es-CL")}{s.suf}
              </div>
              <div style={{ color: "var(--ink-2)", fontSize: 14, fontWeight: 600, marginTop: 6 }}>{s.l}</div>
            </div>
          ))}
        </div>
      </div>
      <style>{`
        @media (max-width: 880px) { .stats-grid { grid-template-columns: 1fr 1fr !important; }
          .stats-grid > div:nth-child(2) { border-right: none !important; }
          .stats-grid > div:nth-child(-n+2) { border-bottom: 2px solid var(--line); }
        }
      `}</style>
    </section>
  );
}

/* -------- FAQ -------- */
function FAQ() {
  const [open, setOpen] = React.useState(0);
  const faqs = [
    { q: "¿Por qué usar MachReach si ya tengo mis apuntes?", a: "MachReach no solo guarda material: mide tu estudio por curso, te da Focus con XP, quizzes IA, flashcards, analytics y rankings en un mismo lugar." },
    { q: "¿Mi universidad usa Canvas?", a: "La mayoría de las grandes en Chile sí: UC, UDP, UAndes, UAI, USACH, USS, PUCV. Si la tuya no, igual puedes subir cursos a mano y usar el resto de la app." },
    { q: "¿Es seguro conectar mi cuenta Canvas?", a: "Sí. La extensión de MachReach detecta tus cursos desde tu sesión abierta de Canvas. No pedimos tu contraseña ni tocamos tus tareas, notas o contenido." },
    { q: "¿Qué pasa con mis datos de estudio?", a: "Son tuyos. No vendemos datos a terceros. Puedes exportar todo o borrar tu cuenta en cualquier momento." },
    { q: "¿Cómo funciona la economía de monedas?", a: "Ganas monedas estudiando con Focus. Las gastas en cosméticos, banners y temas. Pura cosa estética — no afecta tu desempeño académico." },
    { q: "¿Puedo cancelar la suscripción cuando quiera?", a: "Sí. Sin contratos ni letra chica. La cancelas con un click y mantienes acceso hasta el fin del ciclo pagado." },
  ];
  return (
    <section>
      <div className="container" style={{ maxWidth: 820 }}>
        <div className="section-head">
          <h2>Lo que preguntan tus<br/>compañeros antes de entrar.</h2>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {faqs.map((f, i) => (
            <div key={i} className="card-soft" style={{
              padding: 0, overflow: "hidden",
              borderColor: open === i ? "var(--ink)" : "var(--line)",
              boxShadow: open === i ? "0 4px 0 0 var(--ink)" : "none",
              transition: "all .2s ease",
            }}>
              <button onClick={() => setOpen(open === i ? -1 : i)} style={{
                width: "100%", padding: "20px 24px", textAlign: "left",
                display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16,
                fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 18,
              }}>
                <span>{f.q}</span>
                <span style={{
                  width: 32, height: 32, flexShrink: 0,
                  borderRadius: 10, background: open === i ? "var(--brand)" : "var(--bg-2)",
                  border: "2px solid var(--ink)", display: "grid", placeItems: "center",
                  color: open === i ? "white" : "var(--ink)",
                  transform: open === i ? "rotate(180deg)" : "rotate(0)",
                  transition: "transform .2s ease",
                }}>
                  <IconChevron size={16} strokeWidth={3}/>
                </span>
              </button>
              {open === i && (
                <div style={{ padding: "0 24px 22px", color: "var(--ink-2)", fontSize: 16, lineHeight: 1.6 }}>
                  {f.a}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function LandingMotion() {
  React.useEffect(() => {
    const root = document.documentElement;
    root.classList.add("motion-ready");

    const progress = document.getElementById("landing-progress-bar");
    const sections = Array.from(document.querySelectorAll("section"));
    const heroCard = document.querySelector(".hero-anim-card");
    let frame = 0;
    const updateScene = () => {
      frame = 0;
      const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
      const pct = Math.min(1, Math.max(0, window.scrollY / max));
      if (progress) progress.style.transform = `scaleX(${pct})`;
      root.style.setProperty("--landing-paper-y", `${Math.min(28, window.scrollY * 0.035).toFixed(2)}px`);
      root.style.setProperty("--hero-shift", `${Math.min(1, window.scrollY / Math.max(480, window.innerHeight * 0.72)).toFixed(3)}`);
      if (heroCard) heroCard.style.setProperty("--hero-card-shift", `${Math.min(28, window.scrollY * 0.045).toFixed(2)}px`);

      const vh = Math.max(1, window.innerHeight);
      sections.forEach((section) => {
        const rect = section.getBoundingClientRect();
        const sectionProgress = Math.min(1, Math.max(0, (vh - rect.top) / Math.max(vh, rect.height)));
        section.style.setProperty("--section-progress", sectionProgress.toFixed(3));
        section.classList.toggle("section-live", rect.top < vh * 0.8 && rect.bottom > vh * 0.2);
      });
    };

    const requestScene = () => {
      if (!frame) frame = requestAnimationFrame(updateScene);
    };

    const revealSelectors = [
      ".stats-grid > *",
      ".feat-grid > *",
      ".how-grid > *",
      ".canvas-cta",
      ".lb-wrap > *",
      ".quiz-wrap > *",
      ".price-grid > *",
      ".faq-list > *",
      ".foot-grid > *",
      "footer .container > div:last-child",
    ].join(",");
    const revealTargets = Array.from(document.querySelectorAll(revealSelectors));
    const groupOrder = new Map();
    revealTargets.forEach((el) => {
      const parent = el.parentElement;
      const order = groupOrder.get(parent) || 0;
      groupOrder.set(parent, order + 1);
      const variant = el.matches(".canvas-cta, .lb-arena, .quiz-wrap > *, .price-grid > *")
        ? "motion-stage"
        : el.parentElement && el.parentElement.matches(".stats-grid, .lb-wrap")
          ? "motion-score"
          : "motion-rise";
      el.classList.add("motion-reveal", variant);
      el.style.setProperty("--reveal-delay", `${Math.min(order, 4) * 75}ms`);
    });

    /* highlighter sweep: wrap heading text so the marker hugs each line */
    try {
      document.querySelectorAll("section .section-head h2").forEach((h) => {
        if (!h.querySelector(".mh-hl")) h.innerHTML = '<span class="mh-hl">' + h.innerHTML + "</span>";
      });
    } catch (e) { /* non-fatal */ }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
    revealTargets.forEach(el => observer.observe(el));
    const markVisible = () => {
      revealTargets.forEach((el) => {
        if (el.classList.contains("is-visible")) return;
        const rect = el.getBoundingClientRect();
        if (rect.top < window.innerHeight * 0.94 && rect.bottom > -window.innerHeight * 0.12) {
          el.classList.add("is-visible");
        }
      });
    };
    const sectionObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        entry.target.classList.toggle("section-live", entry.isIntersecting);
      });
    }, { threshold: 0.12, rootMargin: "-8% 0px -14% 0px" });
    sections.forEach((section, i) => {
      section.style.setProperty("--section-order", i);
      sectionObserver.observe(section);
    });
    updateScene();
    requestAnimationFrame(markVisible);
    setTimeout(markVisible, 320);
    window.addEventListener("scroll", requestScene, { passive: true });
    window.addEventListener("scroll", markVisible, { passive: true });
    window.addEventListener("resize", requestScene);
    window.addEventListener("resize", markVisible);

    return () => {
      window.removeEventListener("scroll", requestScene);
      window.removeEventListener("scroll", markVisible);
      window.removeEventListener("resize", requestScene);
      window.removeEventListener("resize", markVisible);
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      sectionObserver.disconnect();
    };
  }, []);

  return (
    <>
      <div id="landing-progress" aria-hidden="true">
        <span id="landing-progress-bar"/>
        <span id="landing-progress-flame">
          <svg viewBox="0 0 100 125" width="22" height="22" style={{ display: "block", overflow: "visible" }}>
            <path d="M50 0 C62 28 92 44 92 76 C92 103 73 122 50 122 C27 122 8 103 8 76 C8 44 38 28 50 0 Z" fill="#FF7A3D"/>
            <path d="M50 45 C57 60 74 68 74 86 C74 102 63 112 50 112 C37 112 26 102 26 86 C26 68 43 60 50 45 Z" fill="#F2A156"/>
            <path d="M50 72 C54 80 62 84 62 93 C62 102 56 107 50 107 C44 107 38 102 38 93 C38 84 46 80 50 72 Z" fill="#FFE9C9"/>
          </svg>
        </span>
      </div>
      <div className="landing-edge-pattern" aria-hidden="true"/>
    </>
  );
}

window.HowItWorks = HowItWorks;
window.CanvasCallout = CanvasCallout;
window.LeaderboardShowcase = LeaderboardShowcase;
window.QuizDemo = QuizDemo;
window.StatsStrip = StatsStrip;
window.FAQ = FAQ;
window.LandingMotion = LandingMotion;
