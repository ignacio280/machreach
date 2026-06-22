/* ======== Pricing + final CTA + Footer ======== */

function Pricing() {
  // CLP-only, monthly pricing. Prices are IVA-included (Lemon Squeezy is set to
  // tax-inclusive so the listed price is the final price the customer pays).
  const fmt = (clp) => "$" + clp.toLocaleString("es-CL");
  const tiers = [
    {
      key: "free",
      name: "Free",
      tag: "Para probar",
      clp: 0,
      blurb: "Lo justo para empezar a estudiar con estructura.",
      features: ["Canvas, cursos y Focus", "XP, monedas, rachas y rankings", "Planilla de notas, amigos y tienda", "1 quiz IA / dia", "1 mazo de flashcards IA / dia"],
      cta: "Empezar gratis", primary: false,
    },
    {
      key: "plus",
      name: "PLUS",
      tag: "Mas popular",
      clp: 4990,
      blurb: "Para los que estudian en serio cada semana.",
      features: ["Todo lo de Free", "Quizzes IA ilimitados", "Flashcards IA ilimitadas", "Mas ensayos y analitica avanzada", "+300 monedas mensuales", "Streak Insurance+ y cosmeticos PLUS"],
      cta: "Subirme a PLUS", primary: true,
    },
    {
      key: "ultimate",
      name: "Ultimate",
      tag: "Bloqueado",
      clp: 8990,
      blurb: "Lo abriremos cuando tenga valor real.",
      features: ["Todo lo de PLUS", "Limites maximos de IA", "Historial completo de analitica", "Mas monedas y reparaciones", "Cosmeticos Ultimate y early access"],
      cta: "Bloqueado por ahora", primary: false, locked: true,
    },
  ];
  return (
    <section id="pricing">
      <div className="container">
        <div className="section-head">
          <h2>Empieza gratis.<br/>Sube cuando lo sientas.</h2>
          <p>Cancela cuando quieras. Sin contratos. Sin letra chica.</p>
        </div>

        <div className="price-grid" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20, padding: "26px 0 12px", overflow: "visible" }}>
          {tiers.map(t => {
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
                overflow: "visible",
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
                    {t.clp ? fmt(t.clp) : "Gratis"}
                  </span>
                  {t.clp > 0 && <span style={{ fontSize: 14, color: t.primary ? "color-mix(in oklab, white 70%, transparent)" : "var(--ink-3)" }}>/mes</span>}
                </div>
                <div style={{ height: 18 }}/>
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
        {t.locked ? <button className="btn btn-lg price-cta price-cta-locked" disabled style={{
                  background: "var(--surface-2)",
                  color: "var(--ink-3)",
                  borderColor: "var(--line)",
                  width: "100%",
                  boxShadow: "none",
                  cursor: "not-allowed",
                }}>{t.cta}</button> : <a href="/register" className={`btn btn-lg price-cta ${t.primary ? "price-cta-plus" : "price-cta-standard"}`} style={{
                  background: t.primary ? "white" : "var(--ink)",
                  color: t.primary ? "var(--brand)" : "white",
                  borderColor: t.primary ? "white" : "var(--ink)",
                  width: "100%",
                  boxShadow: "0 4px 0 0 " + (t.primary ? "color-mix(in oklab, var(--ink) 30%, transparent)" : "color-mix(in oklab, var(--ink) 50%, var(--brand))"),
                }}>{t.cta}</a>}
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
            Conecta tus ramos y empieza a ordenar tu estudio hoy mismo. Tu yo del lunes te lo va a agradecer.
          </p>
          <div style={{ display: "flex", gap: 14, justifyContent: "center", flexWrap: "wrap" }}>
          <a className="btn btn-lg final-cta-primary" href="/register" style={{
              background: "var(--ink)", color: "white", borderColor: "var(--ink)",
              boxShadow: "0 4px 0 0 color-mix(in oklab, black 50%, transparent)",
            }}>
              <IconBolt size={20}/> Empieza gratis
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
          <span>MACHREACH · SANTIAGO, CHILE</span>
        </div>
      </div>
      <style>{`@media (max-width: 720px) { .foot-grid { grid-template-columns: 1fr 1fr !important; } }`}</style>
    </footer>
  );
}

window.Pricing = Pricing;
window.FinalCTA = FinalCTA;
window.Footer = Footer;
