/* ======== Sections: How it works, Canvas callout, Leaderboard showcase, Quiz, Stats, FAQ ======== */

/* -------- HOW IT WORKS -------- */
function HowItWorks() {
  const steps = [
    { n: "01", t: "Conecta Canvas", d: "OAuth en 30 segundos. Bajamos tus cursos, syllabus y fechas de prueba.", icon: <IconCanvas/> },
    { n: "02", t: "Estudia con Focus", d: "Elige ramo, prueba y dale start. El reloj cuenta y la app te suma XP.", icon: <IconTimer/> },
    { n: "03", t: "Sube de liga", d: "Compite cada semana con tu universidad. Premios reales en monedas.", icon: <IconTrophy/> },
  ];
  return (
    <section id="how" style={{ background: "var(--bg-2)", borderTop: "2px solid var(--line)", borderBottom: "2px solid var(--line)" }}>
      <div className="container">
        <div className="section-head">
          <span className="eyebrow"><span className="dot"/> Cero fricción</span>
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

/* -------- CANVAS CALLOUT -------- */
function CanvasCallout() {
  const lines = [
    { c: "var(--accent)", t: "→ canvas.uc.cl/oauth/login", d: 0 },
    { c: "color-mix(in oklab, white 50%, transparent)", t: "  ✓ autenticado como s.diaz@uc.cl", d: 200 },
    { c: "color-mix(in oklab, white 50%, transparent)", t: "  ✓ 6 cursos detectados", d: 400 },
    { c: "color-mix(in oklab, white 50%, transparent)", t: "  ✓ 14 fechas de prueba", d: 600 },
    { c: "color-mix(in oklab, white 50%, transparent)", t: "  ✓ 38 archivos sincronizados", d: 800 },
    { c: "var(--brand)", t: "→ listo para estudiar.", d: 1100 },
  ];
  const [shown, setShown] = React.useState(0);
  React.useEffect(() => {
    const id = setInterval(() => setShown(s => (s >= lines.length ? 0 : s + 1)), 600);
    return () => clearInterval(id);
  }, []);
  return (
    <section>
      <div className="container">
        <div className="canvas-cta" style={{
          display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 48,
          alignItems: "center", background: "var(--ink)", color: "white",
          borderRadius: 36, padding: "56px 56px",
          border: "2px solid var(--ink)", boxShadow: "0 8px 0 0 var(--brand)",
          position: "relative", overflow: "hidden",
        }}>
          <div>
            <span className="eyebrow" style={{ background: "color-mix(in oklab, white 14%, transparent)", borderColor: "white", color: "white" }}>
              <span className="dot" style={{ background: "var(--accent)" }}/> Integración nativa
            </span>
            <h2 style={{ fontSize: "clamp(34px, 4.5vw, 56px)", marginTop: 18, color: "white" }}>
              Si tu uni usa Canvas,<br/>nosotros también.
            </h2>
            <p style={{ color: "color-mix(in oklab, white 75%, transparent)", fontSize: 18, marginTop: 16, maxWidth: 480 }}>
              OAuth estándar. Una vez conectado, todo se sincroniza solo: cursos, módulos, fechas y materiales. Sin copiar y pegar nada.
            </p>
            <div style={{ display: "flex", gap: 12, marginTop: 28, flexWrap: "wrap" }}>
        <a href="/register" className="btn btn-primary btn-lg" style={{ borderColor: "white" }}>
                <IconCanvas size={20}/> Conectar Canvas
              </a>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 14, color: "color-mix(in oklab, white 70%, transparent)" }}>
                <IconCheck size={16}/> UC, UDP, USACH, UAndes, UAI…
              </span>
            </div>
          </div>
          <div>
            <div style={{
              background: "color-mix(in oklab, white 6%, var(--ink))",
              border: "2px solid color-mix(in oklab, white 18%, transparent)",
              borderRadius: 20, padding: 20,
              fontFamily: "var(--font-mono)", fontSize: 13, minHeight: 220,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, paddingBottom: 10, borderBottom: "1px solid color-mix(in oklab, white 12%, transparent)" }}>
                <div style={{ display: "flex", gap: 6 }}>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#ff5f56" }}/>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#ffbd2e" }}/>
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#27c93f" }}/>
                </div>
                <span style={{ fontSize: 11, color: "color-mix(in oklab, white 50%, transparent)" }}>~/machreach/sync</span>
              </div>
              {lines.slice(0, shown).map((l, i) => (
                <div key={i} style={{ color: l.c, lineHeight: 1.9, animation: "count-up .25s ease both" }}>{l.t}</div>
              ))}
              {shown < lines.length && (
                <span style={{ color: "var(--brand)", animation: "pulse-ring 1s infinite" }}>▍</span>
              )}
            </div>
          </div>
        </div>
      </div>
      <style>{`@media (max-width: 900px) { .canvas-cta { grid-template-columns: 1fr !important; padding: 40px 28px !important; } }`}</style>
    </section>
  );
}

/* -------- LEADERBOARDS SHOWCASE — with podium -------- */
function LeaderboardShowcase() {
  const [scope, setScope] = React.useState("uni");
  const data = {
    pais:    [{ n: "Catalina",  u: "UC",   xp: 28400, c: "#fb923c" }, { n: "Joaquín",  u: "PUCV", xp: 24100, c: "#a78bfa" }, { n: "tú", u: "UDP", xp: 22850, c: "var(--brand)", you: true }, { n: "Renata", u: "USS",  xp: 20180, c: "#22d3ee" }, { n: "Diego",    u: "UAI",  xp: 18910, c: "#34d399" }],
    uni:     [{ n: "Sofia_Db",  u: "UDP",  xp: 24100, c: "#fb923c" }, { n: "tú",       u: "UDP",  xp: 22850, c: "var(--brand)", you: true }, { n: "Antonia",  u: "UDP", xp: 19200, c: "#a78bfa" }, { n: "Tomás",   u: "UDP",  xp: 17640, c: "#22d3ee" }, { n: "Sofía",    u: "UDP",  xp: 14720, c: "#34d399" }],
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
          <span className="eyebrow"><span className="dot"/> Compite sano</span>
          <h2>Rankings semanales que<br/>te sacan a estudiar.</h2>
          <p>Tres niveles: tu carrera, tu universidad, tu país. Se cierran cada lunes con premios en monedas.</p>
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
              Cada semana arranca un nuevo ranking. Acumula XP estudiando con Focus o haciendo quizzes y sube hasta la Liga Diamante.
            </p>
            <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                { t: "Top 3 semanal: 500 monedas + badge exclusivo", c: "var(--gold)" },
                { t: "Top 10 mensual: 2.000 monedas + cosmético dorado", c: "var(--silver)" },
                { t: "Liga Diamante: acceso a quizzes premium", c: "var(--secondary)" },
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
                    Liga Diamante · {scope === "pais" ? "Chile" : scope === "uni" ? "UDP" : "Ingeniería"}
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
                        {rank === 1 && (
                          <div style={{
                            position: "absolute", top: -16, left: "50%", transform: "translateX(-50%)",
                            color: "var(--gold)",
                          }}>
                            <IconStar size={22}/>
                          </div>
                        )}
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

/* -------- QUIZ DEMO — gamified, lives + streak + xp -------- */
function QuizDemo() {
  const questions = [
    { q: "¿Cuál es el límite de sin(x)/x cuando x → 0?", opts: ["0", "1", "∞", "no existe"], correct: 1, ramo: "Cálculo I" },
    { q: "Una matriz cuadrada es invertible si...", opts: ["su determinante es 0", "su determinante es ≠ 0", "es simétrica", "tiene filas iguales"], correct: 1, ramo: "Álgebra Lineal" },
    { q: "La aceleración en MAS es máxima en...", opts: ["el equilibrio", "los extremos", "la mitad", "es constante"], correct: 1, ramo: "Física II" },
  ];
  const [i, setI] = React.useState(0);
  const [picked, setPicked] = React.useState(null);
  const [xp, setXp] = React.useState(120);
  const [lives, setLives] = React.useState(3);
  const q = questions[i];

  const choose = (k) => {
    if (picked !== null) return;
    setPicked(k);
    if (k === q.correct) setXp(x => x + 10);
    else setLives(l => Math.max(0, l - 1));
  };
  const next = () => {
    setPicked(null);
    setI((i + 1) % questions.length);
  };

  return (
    <section>
      <div className="container">
        <div className="section-head">
          <span className="eyebrow"><span className="dot"/> Training</span>
          <h2>Quizzes con IA, hechos<br/>por tu propia universidad.</h2>
          <p>Practica con cuestionarios generados a partir del material real de tus cursos. Comparte los tuyos y gana monedas.</p>
        </div>
        <div className="quiz-wrap" style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 36, alignItems: "center" }}>
          <div>
            <h3 style={{ fontSize: 26, marginBottom: 14 }}>Material desordenado entra. Quiz ordenado sale.</h3>
            <p style={{ color: "var(--ink-2)", fontSize: 16, marginBottom: 22 }}>
              Sube un PDF, un syllabus o pega tus apuntes. La IA genera preguntas relevantes para tu próxima prueba en segundos.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[
                { ic: <IconBook/>,   t: "Aprende del material real, no de internet aleatorio" },
                { ic: <IconPeople/>, t: "Comparte tus quizzes con compañeros de tu universidad" },
                { ic: <IconCoin/>,   t: "Cada quiz que otros usen te paga monedas" },
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
            <div style={{
              padding: "14px 20px",
              background: "var(--ink)", color: "white",
              display: "grid", gridTemplateColumns: "auto 1fr auto", alignItems: "center", gap: 14,
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
                <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "color-mix(in oklab, white 70%, transparent)" }}>{i + 1}/{questions.length}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14 }}>
                  <span style={{ color: "var(--bad)" }}>{Array.from({ length: 3 }).map((_, k) => k < lives ? "❤" : "♡").join("")}</span>
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: 4, fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14, color: "var(--accent)" }}>
                  <IconBolt size={14}/>{xp}
                </span>
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
                  {picked === null ? "Elige una respuesta" : picked === q.correct ? "✓ Correcto · +10 XP" : "✗ Incorrecto · −1 vida"}
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
          boxShadow: "0 6px 0 0 var(--ink)", overflow: "hidden",
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
    { q: "¿Por qué pagaría por estudiar si tengo Notion gratis?", a: "Notion no se conecta a Canvas, no mide tu tiempo de estudio por ramo, no tiene quizzes generados por IA, ni rankings con tu universidad. MachReach hace todo eso integrado." },
    { q: "¿Mi universidad usa Canvas?", a: "La mayoría de las grandes en Chile sí: UC, UDP, UAndes, UAI, USACH, USS, PUCV. Si la tuya no, igual puedes subir cursos a mano y usar el resto de la app." },
    { q: "¿Es seguro conectar mi cuenta Canvas?", a: "Usamos OAuth oficial de Canvas — el mismo protocolo que usan cientos de apps integradas. Nunca vemos tu contraseña, y puedes revocar el acceso cuando quieras desde Canvas." },
    { q: "¿Qué pasa con mis datos de estudio?", a: "Son tuyos. No vendemos datos a terceros. Puedes exportar todo o borrar tu cuenta en cualquier momento." },
    { q: "¿Cómo funciona la economía de monedas?", a: "Ganas monedas estudiando con Focus, completando quizzes, manteniendo racha y subiendo en el ranking. Las gastas en cosméticos, banners y temas. Pura cosa estética — no afecta tu desempeño académico." },
    { q: "¿Puedo cancelar la suscripción cuando quiera?", a: "Sí. Sin contratos ni letra chica. La cancelas con un click y mantienes acceso hasta el fin del ciclo pagado." },
  ];
  return (
    <section>
      <div className="container" style={{ maxWidth: 820 }}>
        <div className="section-head">
          <span className="eyebrow"><span className="dot"/> Preguntas frecuentes</span>
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

window.HowItWorks = HowItWorks;
window.CanvasCallout = CanvasCallout;
window.LeaderboardShowcase = LeaderboardShowcase;
window.QuizDemo = QuizDemo;
window.StatsStrip = StatsStrip;
window.FAQ = FAQ;
