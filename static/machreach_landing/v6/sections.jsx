/* v6 — How it works + study plan + leaderboards (exact live copy) */

function HowItWorks() {
  const wrap = React.useRef(null);
  const p = useScrollProgress(wrap);
  const steps = [
    { n: "01", t: "Conecta tus ramos", d: "Trae tus ramos con la extensión de MachReach o agrégalos a mano en segundos. Sin configuración técnica y sin tocar tus credenciales.", icon: <IconCanvas /> },
    { n: "02", t: "Estudia con Focus", d: "Elige ramo, prueba y dale start. El reloj cuenta y la app te suma XP.", icon: <IconTimer /> },
    { n: "03", t: "Sube de liga", d: "Compite cada semana con tu universidad y mira cómo sube tu XP.", icon: <IconTrophy /> },
  ];
  return (
    <section id="how" className="band band-top">
      <div className="container">
        <div className="sec-head">
          <h2><Lines lines={["De cero a estudiando", "en menos de un minuto."]} /></h2>
        </div>
        <div className="how" ref={wrap}>
          <div className="how-rail" aria-hidden="true"><i style={{ transform: `scaleX(${Math.min(1, p * 1.3)})` }} /></div>
          {steps.map((s, i) => (
            <div key={i} className={"how-step" + (p > 0.1 + i * 0.2 ? " on" : "")} style={{ transitionDelay: `${i * 70}ms` }}>
              <div className="how-node">{s.n}</div>
              <div className="how-icon">{React.cloneElement(s.icon, { size: 22 })}</div>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function LeaderboardShowcase() {
  const [scope, setScope] = React.useState("uni");
  const [sel, setSel] = React.useState(null);
  const tabsRef = React.useRef(null);
  const [pill, setPill] = React.useState({ left: 0, width: 0 });

  const data = {
    pais: [
      { n: "Catalina", u: "UC", xp: 28400, c: "#F97316", delta: 320 },
      { n: "Joaquín", u: "PUCV", xp: 24100, c: "var(--plum)", delta: 210 },
      { n: "tú", u: "Tu universidad", xp: 22850, c: "var(--brand)", you: true, delta: 180 },
      { n: "Renata", u: "USS", xp: 20180, c: "var(--sky)", delta: 145 },
      { n: "Diego", u: "UAI", xp: 18910, c: "var(--good)", delta: 120 },
    ],
    uni: [
      { n: "Sofia_Db", u: "Tu universidad", xp: 24100, c: "#F97316", delta: 260 },
      { n: "tú", u: "Tu universidad", xp: 22850, c: "var(--brand)", you: true, delta: 220 },
      { n: "Antonia", u: "Tu universidad", xp: 19200, c: "var(--plum)", delta: 170 },
      { n: "Tomás", u: "Tu universidad", xp: 17640, c: "var(--sky)", delta: 130 },
      { n: "Sofía", u: "Tu universidad", xp: 14720, c: "var(--good)", delta: 95 },
    ],
    carrera: [
      { n: "Magdalena", u: "Tu carrera", xp: 18900, c: "#F97316", delta: 190 },
      { n: "tú", u: "Tu carrera", xp: 16200, c: "var(--brand)", you: true, delta: 160 },
      { n: "Pablo", u: "Tu carrera", xp: 14820, c: "var(--plum)", delta: 120 },
      { n: "Camila", u: "Tu carrera", xp: 12410, c: "var(--sky)", delta: 100 },
      { n: "Benja", u: "Tu carrera", xp: 10940, c: "var(--good)", delta: 80 },
    ],
  };
  const meta = {
    carrera: { title: "Tu carrera", head: "tu carrera.", micro: "Tu carrera está prendida", stats: ["12 carreras", "+160 XP hoy", "top 3 cerca"] },
    uni: { title: "Tu universidad", head: "tu universidad.", micro: "Tu universidad está prendida", stats: ["42 cursos", "+220 XP hoy", "top 3 cerca"] },
    pais: { title: "Tu país", head: "tu país.", micro: "Tu país está prendido", stats: ["12 Ues", "+180 XP hoy", "top 3 cerca"] },
  };
  const tabs = [{ k: "carrera", l: "Por carrera" }, { k: "uni", l: "Por universidad" }, { k: "pais", l: "Por país" }];
  const rows = data[scope], m = meta[scope];
  const selRow = rows.find((r) => r.n === sel) || rows.find((r) => r.you) || rows[0];
  const selRank = rows.findIndex((r) => r.n === selRow.n) + 1;
  const podium = [rows[1], rows[0], rows[2]], heights = [78, 112, 62], ranks = [2, 1, 3];

  React.useEffect(() => { setSel(null); }, [scope]);
  React.useEffect(() => {
    const el = tabsRef.current; if (!el) return;
    const btn = el.querySelector(`[data-k="${scope}"]`); if (!btn) return;
    setPill({ left: btn.offsetLeft, width: btn.offsetWidth });
  }, [scope]);

  return (
    <section id="leaderboard">
      <div className="container">
        <div className="sec-head">
          <h2><Lines lines={["Rankings semanales que", "te sacan a estudiar."]} /></h2>
          <Reveal as="p" d={220}>Tres niveles: tu carrera, tu universidad, tu país. Se reinician cada lunes para que siempre tengas una meta nueva.</Reveal>
        </div>
        <div className="lb-grid">
          <Reveal>
            <div className="seg" ref={tabsRef}>
              <span className="seg-pill" style={{ left: pill.left, width: pill.width }} />
              {tabs.map((t) => <button key={t.k} data-k={t.k} className={scope === t.k ? "on" : ""} onClick={() => setScope(t.k)}>{t.l}</button>)}
            </div>
            <h3 style={{ fontSize: 28, marginTop: 24, marginBottom: 12 }}>Tres ligas, un objetivo: <span style={{ color: "var(--brand)" }}>{m.head}</span></h3>
            <p style={{ color: "var(--ink-2)", marginBottom: 20, fontWeight: 600 }}>Cada semana arranca un nuevo ranking. Acumula XP estudiando con Focus y sube por rangos reales como Iniciados, Aprendices, Estudiosos e Investigadores.</p>
            <ul className="ticks">
              {[["var(--gold)", "Top 3 semanal: constancia destacada en el ranking"], ["var(--silver)", "Top 10 mensual: progreso acumulado del mes"], ["var(--brand)", "Investigadores: rango visible por XP y racha"]].map(([c, t], i) => (
                <li key={i}><span style={{ background: c }}><IconCheck size={13} strokeWidth={3.4} color={i === 1 ? "var(--ink)" : "#fff"} /></span>{t}</li>
              ))}
            </ul>
            <div className="lb-insight">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 14 }}>
                <div>
                  <div className="mono" style={{ textTransform: "none", letterSpacing: 0, fontSize: 12 }}>{m.micro}</div>
                  <div style={{ fontFamily: "var(--font-display)", fontSize: 23, fontWeight: 800, marginTop: 3 }}>{selRow.n} <span style={{ color: "var(--brand)" }}>#{selRank}</span></div>
                </div>
                <div className="num" style={{ fontFamily: "var(--font-display)", color: "var(--brand)", fontWeight: 800, fontSize: 19, whiteSpace: "nowrap" }}>+{selRow.delta} XP</div>
              </div>
              <div style={{ display: "flex", gap: 7, marginTop: 13, flexWrap: "wrap" }}>{m.stats.map((s) => <span key={s} className="tagchip">{s}</span>)}</div>
              <div className="meter"><i key={scope + selRow.n} style={{ width: `${Math.min(94, 34 + selRow.delta / 4)}%` }} /></div>
            </div>
          </Reveal>

          <div>
            <Reveal d={140} variant="pop" className="lb-arena card-2">
              <div className="lb-head">
                <div style={{ minWidth: 0 }}>
                  <h4>Investigadores · {m.title}</h4>
                  <div className="mono" style={{ fontSize: 10, marginTop: 3 }}>SEMANA 26 · CIERRA LUN 09:00</div>
                </div>
                <span className="tagchip"><i className="live-dot" /> live</span>
              </div>
              <div className="podium" key={scope}>
                {podium.map((p, i) => p && (
                  <div key={p.n} className={"pod" + (selRow.n === p.n ? " on" : "")} onMouseEnter={() => setSel(p.n)} onClick={() => setSel(p.n)} style={{ animationDelay: `${i * 110}ms` }}>
                    <Avatar name={p.n} color={p.c} size={ranks[i] === 1 ? 48 : 38} you={p.you} />
                    <div style={{ fontWeight: 800, fontSize: 12.5, fontFamily: "var(--font-display)" }}>{p.n}</div>
                    <div className="mono num" style={{ fontSize: 9.5 }}>{p.xp.toLocaleString("es-CL")} XP</div>
                    <div className={"pod-bar p" + ranks[i]} style={{ height: heights[i], animationDelay: `${i * 110 + 90}ms` }}>{ranks[i]}</div>
                  </div>
                ))}
              </div>
              <div className="lb-rows">
                {rows.slice(3).map((r, i) => (
                  <div key={r.n} className={"lb-row" + (selRow.n === r.n ? " on" : "")} onMouseEnter={() => setSel(r.n)} onClick={() => setSel(r.n)} style={{ animationDelay: `${i * 90 + 300}ms` }}>
                    <span className="hp-rank">{i + 4}</span>
                    <Avatar name={r.n} color={r.c} size={32} you={r.you} />
                    <div><div style={{ fontWeight: 800, fontSize: 13 }}>{r.n}</div><div className="hp-uni">{r.u}</div></div>
                    <div className="num" style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14 }}>{r.xp.toLocaleString("es-CL")} <span style={{ fontSize: 9, color: "var(--ink-3)" }}>XP</span></div>
                  </div>
                ))}
                <div className="lb-you"><span className="mono lbl">TU POSICIÓN ACTUAL</span><span className="num" style={{ fontFamily: "var(--font-display)", fontWeight: 800, color: "var(--amber)" }}>#{rows.findIndex((r) => r.you) + 1}</span></div>
              </div>
            </Reveal>
            <Reveal as="p" d={260} className="lb-caption">Estudia con Focus, suma XP y vuelve a competir desde cero cada lunes.</Reveal>
          </div>
        </div>
      </div>
    </section>
  );
}

Object.assign(window, { HowItWorks, LeaderboardShowcase });
