/* v6 Stats + Features — exact live copy */

function StatsStrip() {
  const [ref, seen] = useReveal({ threshold: 0.25 });
  const s1 = useCountUp(2347, seen), s2 = useCountUp(184, seen), s3 = useCountUp(12, seen), s4 = useCountUp(98, seen);
  const items = [
    { v: Math.round(s1).toLocaleString("es-CL"), l: "estudiantes activos esta semana" },
    { v: Math.round(s2) + "h", l: "horas estudiadas hoy" },
    { v: Math.round(s3), l: "universidades conectadas" },
    { v: Math.round(s4) + "%", l: "satisfacción de usuarios" },
  ];
  return (
    <section ref={ref} style={{ paddingTop: 0 }}>
      <div className="container">
        <div className={"stats " + (seen ? "in" : "")}>
          {items.map((s, i) => (
            <div key={i} className={"stat pop " + (seen ? "in" : "")} style={{ "--d": `${i * 110}ms` }}>
              <div className="stat-v num">{s.v}</div>
              <div className="stat-l">{s.l}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function MiniCanvas() {
  return (
    <div className="mini">
      <div className="mini-top"><span className="mono" style={{ fontSize: 9.5 }}>MIS RAMOS · 2026-1</span><span className="mono" style={{ fontSize: 9.5, color: "var(--good)" }}>● synced</span></div>
      {[{ c: "Cálculo I", p: 78, k: "var(--brand)", n: "P2 en 4d" }, { c: "Álgebra Lineal", p: 54, k: "var(--plum)", n: "T1 en 9d" }, { c: "Física II", p: 32, k: "var(--sky)", n: "P1 en 12d" }].map((r, i) => (
        <div key={i} className="mini-row">
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12, fontWeight: 800 }}><span style={{ width: 9, height: 9, borderRadius: 3, background: r.k, border: "1.5px solid var(--ink)" }} />{r.c}</div>
            <div className="mini-bar"><i style={{ width: r.p + "%", background: r.k, animationDelay: i * 130 + "ms" }} /></div>
          </div>
          <span className="mono" style={{ fontSize: 9 }}>{r.n}</span>
        </div>
      ))}
    </div>
  );
}

function MiniFocus() {
  const total = 1500;
  const [t, setT] = React.useState(1247);
  React.useEffect(() => { const id = setInterval(() => setT((x) => (x <= 0 ? total : x - 1)), 1000); return () => clearInterval(id); }, []);
  const pct = ((total - t) / total) * 100, C = 2 * Math.PI * 29;
  return (
    <div className="mini" style={{ flexDirection: "row", alignItems: "center", gap: 15 }}>
      <div style={{ position: "relative", width: 72, height: 72, flexShrink: 0 }}>
        <svg width="72" height="72" viewBox="0 0 72 72" style={{ transform: "rotate(-90deg)" }}>
          <circle cx="36" cy="36" r="29" fill="none" stroke="var(--paper-2)" strokeWidth="8" />
          <circle cx="36" cy="36" r="29" fill="none" stroke="var(--brand)" strokeWidth="8" strokeDasharray={C} strokeDashoffset={C * (1 - pct / 100)} strokeLinecap="round" style={{ transition: "stroke-dashoffset 1s linear" }} />
          <circle cx="36" cy="36" r="33.5" fill="none" stroke="var(--ink)" strokeWidth="2.5" />
          <circle cx="36" cy="36" r="24.5" fill="none" stroke="var(--ink)" strokeWidth="2.5" />
        </svg>
        <div className="num" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 12.5, whiteSpace: "nowrap" }}>{String(Math.floor(t / 60)).padStart(2, "0")}:{String(t % 60).padStart(2, "0")}</div>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="mono" style={{ color: "var(--good)", fontSize: 9.5, display: "flex", alignItems: "center", gap: 5 }}><i className="live-dot" /> ENFOCADO</div>
        <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 15, marginTop: 3 }}>Cálculo I</div>
        <div className="mono" style={{ fontSize: 9.5, textTransform: "none" }}>Prueba 2 · sesión 3</div>
        <div style={{ display: "flex", gap: 4, marginTop: 8 }}>{[1, 1, 1, 0].map((on, i) => <span key={i} style={{ flex: 1, height: 6, borderRadius: 4, border: "1.5px solid var(--ink)", background: on ? "var(--brand)" : "var(--paper-2)" }} />)}</div>
      </div>
    </div>
  );
}

function MiniChart() {
  const bars = [3.2, 5.1, 2.4, 7.0, 4.3, 8.2, 6.1], labels = ["L", "M", "M", "J", "V", "S", "D"], max = 8.2;
  return (
    <div className="mini">
      <div className="mini-top">
        <span><b className="num" style={{ fontFamily: "var(--font-display)", fontSize: 19 }}>36.3h</b> <span className="mono" style={{ fontSize: 9.5, textTransform: "none" }}>esta semana</span></span>
        <span className="mono" style={{ fontSize: 10, color: "var(--good)" }}>↑ +24%</span>
      </div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 5, height: 76 }}>
        {bars.map((v, i) => (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 5 }}>
            <div style={{ width: "100%", height: 56, display: "flex", alignItems: "flex-end", position: "relative" }}>
              {i === 5 && <span style={{ position: "absolute", top: -13, left: "50%", transform: "translateX(-50%)", fontFamily: "var(--font-mono)", fontSize: 8.5, fontWeight: 700, background: "var(--ink)", color: "var(--paper)", padding: "1px 4px", borderRadius: 4, whiteSpace: "nowrap" }}>8.2h</span>}
              <i className="mini-col" style={{ height: (v / max) * 100 + "%", background: i === 5 ? "var(--brand)" : "var(--amber)", animationDelay: i * 70 + "ms" }} />
            </div>
            <span className="mono" style={{ fontSize: 9 }}>{labels[i]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MiniQuiz() {
  return (
    <div className="mini">
      <div className="mini-top"><span className="chip-brand">CÁLCULO I</span><span className="mono" style={{ fontSize: 9.5 }}>3/10</span></div>
      <div style={{ fontSize: 12.5, fontWeight: 800, lineHeight: 1.3 }}>¿Cuál es la derivada de sin(x)?</div>
      {[{ t: "cos(x)", ok: true }, { t: "−sin(x)" }, { t: "tan(x)" }].map((o, i) => (
        <div key={i} className={"mini-opt" + (o.ok ? " ok" : "")}>
          <span style={{ display: "flex", gap: 7, alignItems: "center" }}><span className="mini-key">{["A", "B", "C"][i]}</span>{o.t}</span>
          {o.ok && <IconCheck size={13} color="var(--good)" strokeWidth={3.2} />}
        </div>
      ))}
    </div>
  );
}

function MiniBadges() {
  const items = [{ c: "var(--gold)", l: "7d", ic: <IconFire size={15} /> }, { c: "var(--plum)", l: "30d", ic: <IconFire size={15} /> }, { c: "var(--brand)", l: "100h", ic: <IconTimer size={15} /> }, { c: "", l: "?", ic: <IconLock size={15} />, locked: true }];
  return (
    <div className="mini">
      <div className="mono" style={{ fontSize: 9.5 }}>LOGROS · 12 / 24</div>
      <div style={{ display: "flex", gap: 9 }}>
        {items.map((b, i) => (
          <div key={i} className={"badge-tile" + (b.locked ? " locked" : "")} style={{ background: b.c, color: i === 0 ? "var(--ink)" : undefined, animationDelay: i * 110 + "ms" }}>
            {b.ic}<span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 10.5 }}>{b.l}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MiniCoins() {
  const items = [{ n: "Banner Neón", owned: true, c: "var(--brand)" }, { n: "Tema Dorado", owned: true, c: "var(--gold)" }, { n: "Marco Diamante", cost: 800, c: "var(--sky)" }];
  return (
    <div className="mini">
      <div className="coin-balance"><span className="mono lbl" style={{ fontSize: 9.5 }}>SALDO</span><span className="num" style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14 }}><span style={{ color: "var(--gold)" }}><IconCoin size={14} /></span>1.240</span></div>
      {items.map((it, i) => (
        <div key={i} className="mini-row" style={{ gridTemplateColumns: "auto 1fr auto", gap: 9 }}>
          <span style={{ width: 22, height: 22, borderRadius: 7, background: it.c, border: "2px solid var(--ink)" }} />
          <span style={{ fontSize: 11.5, fontWeight: 800 }}>{it.n}</span>
          {it.owned ? <span className="mono" style={{ fontSize: 9, color: "var(--good)" }}>✓ TUYO</span> : <span className="mono num" style={{ fontSize: 9.5, display: "flex", alignItems: "center", gap: 3 }}><span style={{ color: "var(--gold)" }}><IconCoin size={11} /></span>{it.cost}</span>}
        </div>
      ))}
    </div>
  );
}

function Features() {
  const items = [
    { icon: <IconCanvas />, title: "Canvas por extensión", desc: "La extensión de MachReach detecta tus cursos desde tu Canvas abierto para organizar tu estudio por ramo.", mini: <MiniCanvas /> },
    { icon: <IconTimer />, title: "Modo Focus", desc: "Pomodoro pegado a tu ramo y tu prueba. Cada minuto cuenta como XP real.", mini: <MiniFocus /> },
    { icon: <IconChart />, title: "Analítica de estudio", desc: "Cuántas horas, qué día rindes más, cuánto tiempo le diste a cada examen.", mini: <MiniChart /> },
    { icon: <IconBrain />, title: "Quizzes con IA", desc: "Genera preguntas desde tus apuntes o pruebas oficiales para practicar sin perder tiempo.", mini: <MiniQuiz /> },
    { icon: <IconMedal />, title: "Badges, ligas y rachas", desc: "Sistema de logros que premia constancia, no solo talento. Sube de liga semanal.", mini: <MiniBadges /> },
    { icon: <IconCoin />, title: "Economía de monedas", desc: "Gana monedas estudiando y cámbialas por cosméticos, banners y temas exclusivos.", mini: <MiniCoins /> },
  ];
  return (
    <section id="features">
      <div className="container">
        <div className="sec-head">
          <h2><Lines lines={["Tu semestre completo,", "convertido en progreso."]} /></h2>
          <Reveal as="p" d={220}>Seis piezas que se hablan entre sí para que estudiar sea ordenado, medible y, por fin, divertido.</Reveal>
        </div>
        <div className="feat-grid">
          {items.map((it, i) => (
            <Reveal key={i} d={(i % 3) * 110} variant="pop" className="feat card">
              <div className="feat-icon">{React.cloneElement(it.icon, { size: 24 })}</div>
              <h3 className="feat-title">{it.title}</h3>
              <p className="feat-desc">{it.desc}</p>
              <div className="feat-mini">{it.mini}</div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

Object.assign(window, { StatsStrip, Features });
