/* ======== Pricing + final CTA + Footer ======== */

function Pricing() {
  const [period, setPeriod] = React.useState("month");
  const [currency, setCurrency] = React.useState("CLP");

  const usdToClp = 950;
  const fmt = (usd) => {
    if (currency === "USD") return "$" + usd.toFixed(2);
    const clp = Math.round(usd * usdToClp / 100) * 100;
    return "$" + clp.toLocaleString("es-CL");
  };
  const tiers = [
    {
      key: "free",
      name: "Free",
      tag: "Para probar",
      m: 0, y: 0,
      blurb: "Lo justo para arrancar el semestre.",
      features: ["Conectar 1 cuenta Canvas", "Modo Focus ilimitado", "Analítica básica de estudio", "Acceso a rankings", "5 quizzes / mes"],
      cta: "Empezar gratis", primary: false,
    },
    {
      key: "plus",
      name: "PLUS",
      tag: "Más popular",
      m: 4.99, y: 39.99,
      blurb: "Para los que estudian en serio cada semana.",
      features: ["Todo lo de Free", "+150 monedas mensuales", "Quizzes ilimitados", "Cosméticos exclusivos PLUS", "Analítica avanzada por prueba", "Soporte prioritario"],
      cta: "Subirme a PLUS", primary: true,
    },
    {
      key: "pro",
      name: "PRO",
      tag: "Para tryhards",
      m: 9.99, y: 79.99,
      blurb: "Para quienes apuntan al top 1.",
      features: ["Todo lo de PLUS", "+400 monedas mensuales", "Acceso a Liga Diamante", "Quizzes premium con IA avanzada", "Banners y badges PRO", "Estadísticas comparativas"],
      cta: "Subirme a PRO", primary: false,
    },
  ];

  return (
    <section id="pricing">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow"><span className="dot"/> Precios</span>
          <h2>Empieza gratis.<br/>Sube cuando lo sientas.</h2>
          <p>Cancela cuando quieras. Sin contratos. Sin letra chica.</p>
        </div>

        <div style={{ display: "flex", justifyContent: "center", gap: 12, marginBottom: 36, flexWrap: "wrap" }}>
          {/* period toggle */}
          <div style={{
            display: "inline-flex", padding: 4, borderRadius: 14,
            background: "var(--surface)", border: "2px solid var(--ink)",
            boxShadow: "0 3px 0 0 var(--ink)",
          }}>
            {[
              { k: "month", l: "Mensual" },
              { k: "year", l: "Anual · −33%" },
            ].map(p => (
              <button key={p.k} onClick={() => setPeriod(p.k)} style={{
                padding: "8px 18px", borderRadius: 10,
                background: period === p.k ? "var(--brand)" : "transparent",
                color: period === p.k ? "white" : "var(--ink-2)",
                fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14,
                cursor: "pointer", transition: "all .15s ease",
              }}>{p.l}</button>
            ))}
          </div>
          {/* currency toggle */}
          <div style={{
            display: "inline-flex", padding: 4, borderRadius: 14,
            background: "var(--surface)", border: "2px solid var(--ink)",
            boxShadow: "0 3px 0 0 var(--ink)",
          }}>
            {["CLP", "USD"].map(c => (
              <button key={c} onClick={() => setCurrency(c)} style={{
                padding: "8px 18px", borderRadius: 10,
                background: currency === c ? "var(--ink)" : "transparent",
                color: currency === c ? "white" : "var(--ink-2)",
                fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14,
                cursor: "pointer", transition: "all .15s ease",
              }}>{c}</button>
            ))}
          </div>
        </div>

        <div className="price-grid" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 }}>
          {tiers.map(t => {
            const price = period === "month" ? t.m : (t.y / 12);
            return (
              <div key={t.key} style={{
                position: "relative",
                background: t.primary ? "var(--brand)" : "var(--surface)",
                color: t.primary ? "white" : "var(--ink)",
                border: "2px solid var(--ink)",
                borderRadius: 28,
                padding: 30,
                boxShadow: t.primary ? "0 8px 0 0 var(--ink)" : "0 4px 0 0 var(--ink)",
                transform: t.primary ? "translateY(-8px)" : "none",
                display: "flex", flexDirection: "column",
              }}>
                {t.primary && (
                  <div style={{
                    position: "absolute", top: -16, right: 22,
                    background: "var(--accent)", color: "var(--accent-ink)",
                    padding: "6px 14px", borderRadius: 999,
                    fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: 11,
                    letterSpacing: ".1em", textTransform: "uppercase",
                    border: "2px solid var(--ink)",
                    boxShadow: "0 3px 0 0 var(--ink)",
                  }}>★ {t.tag}</div>
                )}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
                  <h3 style={{ fontSize: 28, color: "inherit" }}>{t.name}</h3>
                  {!t.primary && (
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", letterSpacing: ".1em", textTransform: "uppercase" }}>{t.tag}</span>
                  )}
                </div>
                <p style={{ color: t.primary ? "color-mix(in oklab, white 80%, transparent)" : "var(--ink-2)", fontSize: 14, marginBottom: 18 }}>{t.blurb}</p>
                <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 4 }}>
                  <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 48, letterSpacing: "-0.03em" }}>
                    {fmt(price)}
                  </span>
                  <span style={{ fontSize: 14, color: t.primary ? "color-mix(in oklab, white 70%, transparent)" : "var(--ink-3)" }}>/mes</span>
                </div>
                {period === "year" && t.y > 0 && (
                  <div style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: t.primary ? "color-mix(in oklab, white 70%, transparent)" : "var(--ink-3)", marginBottom: 18 }}>
                    facturado anual: {fmt(t.y)}
                  </div>
                )}
                {(period === "month" || t.y === 0) && <div style={{ height: 18 }}/>}
                <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 10, marginBottom: 24, flex: 1 }}>
                  {t.features.map((f, i) => (
                    <li key={i} style={{ display: "flex", alignItems: "flex-start", gap: 10, fontSize: 14, fontWeight: 600 }}>
                      <span style={{
                        flexShrink: 0, width: 20, height: 20, borderRadius: 6,
                        background: t.primary ? "white" : "var(--brand-soft)",
                        border: "2px solid " + (t.primary ? "white" : "var(--brand)"),
                        color: t.primary ? "var(--brand)" : "var(--brand-ink)",
                        display: "grid", placeItems: "center", marginTop: 2,
                      }}><IconCheck size={12} strokeWidth={3.5}/></span>
                      {f}
                    </li>
                  ))}
                </ul>
                <a href="#cta" className="btn btn-lg" style={{
                  background: t.primary ? "white" : "var(--ink)",
                  color: t.primary ? "var(--brand)" : "white",
                  borderColor: t.primary ? "white" : "var(--ink)",
                  width: "100%",
                  boxShadow: "0 4px 0 0 " + (t.primary ? "color-mix(in oklab, var(--ink) 30%, transparent)" : "color-mix(in oklab, var(--ink) 50%, var(--brand))"),
                }}>{t.cta}</a>
              </div>
            );
          })}
        </div>
      </div>
      <style>{`
        @media (max-width: 980px) { .price-grid { grid-template-columns: 1fr !important; }
          .price-grid > div:nth-child(2) { transform: none !important; }
        }
      `}</style>
    </section>
  );
}

/* -------- FINAL CTA -------- */
function FinalCTA() {
  return (
    <section id="cta" style={{ paddingTop: 32, paddingBottom: 32 }}>
      <div className="container">
        <div style={{
          background: "var(--brand)",
          color: "white",
          border: "2px solid var(--ink)",
          borderRadius: 36,
          padding: "64px 48px",
          textAlign: "center",
          boxShadow: "0 8px 0 0 var(--ink)",
          position: "relative",
          overflow: "hidden",
        }}>
          <div style={{
            position: "absolute", top: 24, left: 24, opacity: .35,
          }}>
            <IconBolt size={48} color="white" strokeWidth={3}/>
          </div>
          <div style={{
            position: "absolute", bottom: 24, right: 24, opacity: .35,
            transform: "rotate(20deg)",
          }}>
            <IconTrophy size={56} color="white" strokeWidth={3}/>
          </div>
          <h2 style={{ fontSize: "clamp(36px, 5vw, 60px)", color: "white", marginBottom: 16 }}>
            Estudia distinto<br/>este semestre.
          </h2>
          <p style={{ fontSize: 18, color: "color-mix(in oklab, white 80%, transparent)", maxWidth: 540, margin: "0 auto 32px" }}>
            Conecta Canvas en 30 segundos y empieza a sumar XP hoy mismo. Tu yo del lunes te lo va a agradecer.
          </p>
          <div style={{ display: "flex", gap: 14, justifyContent: "center", flexWrap: "wrap" }}>
            <a className="btn btn-lg" href="#" style={{
              background: "var(--ink)", color: "white", borderColor: "var(--ink)",
              boxShadow: "0 4px 0 0 color-mix(in oklab, black 50%, transparent)",
            }}>
              <IconCanvas size={20}/> Conectar Canvas
            </a>
            <a className="btn btn-lg" href="#features" style={{
              background: "white", color: "var(--brand-ink)", borderColor: "var(--ink)",
              boxShadow: "0 4px 0 0 var(--ink)",
            }}>
              Ver features <IconArrow size={18}/>
            </a>
          </div>
          <div style={{ marginTop: 28, fontSize: 13, fontFamily: "var(--font-mono)", color: "color-mix(in oklab, white 70%, transparent)", letterSpacing: ".06em" }}>
            GRATIS PARA SIEMPRE · NO PEDIMOS TARJETA · CANCELA CUANDO QUIERAS
          </div>
        </div>
      </div>
    </section>
  );
}

/* -------- FOOTER -------- */
function Footer() {
  return (
    <footer>
      <div className="container">
        <div style={{
          display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: 32,
          paddingBottom: 40,
        }} className="foot-grid">
          <div>
            <div className="logo">
              <div className="logo-mark"><IconLogo size={20} color="white"/></div>
              <span className="logo-text">Mach<span className="dot">Reach</span></span>
            </div>
            <p style={{ marginTop: 14, color: "var(--ink-2)", fontSize: 14, maxWidth: 320 }}>
              Estudiar deja de ser una lata. Hecho desde Santiago, Chile, por estudiantes para estudiantes.
            </p>
          </div>
          {[
            { h: "Producto", l: ["Features", "Cómo funciona", "Precios", "Roadmap"] },
            { h: "Empresa", l: ["Sobre", "Blog", "Contacto", "Prensa"] },
            { h: "Legal", l: ["Términos", "Privacidad", "Cookies", "Status"] },
          ].map((c, i) => (
            <div key={i}>
              <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14, marginBottom: 12 }}>{c.h}</div>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
                {c.l.map((x, k) => <li key={k}><a href="#" style={{ color: "var(--ink-2)", fontSize: 14 }}>{x}</a></li>)}
              </ul>
            </div>
          ))}
        </div>
        <div style={{
          paddingTop: 24, borderTop: "2px solid var(--line)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          color: "var(--ink-3)", fontSize: 13, fontFamily: "var(--font-mono)",
          flexWrap: "wrap", gap: 12,
        }}>
          <span>© 2026 MACHREACH · SANTIAGO, CL</span>
          <span>BUILT WITH ☕ &amp; STREAKS</span>
        </div>
      </div>
      <style>{`@media (max-width: 720px) { .foot-grid { grid-template-columns: 1fr 1fr !important; } }`}</style>
    </footer>
  );
}

window.Pricing = Pricing;
window.FinalCTA = FinalCTA;
window.Footer = Footer;
