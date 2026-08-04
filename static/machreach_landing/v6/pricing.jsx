/* v6 — Pricing (2 tiers, live copy) + Final CTA + Footer */

function Pricing() {
  const tiers = [
    { key: "free", name: "Free", tag: "Para probar", blurb: "Lo justo para empezar a estudiar con estructura.", price: "Gratis", per: "", features: ["Canvas, cursos y Focus", "XP, monedas, rachas y rankings", "Planilla de notas, amigos y tienda", "1 quiz IA / dia", "1 mazo de flashcards IA / dia"], cta: "Empezar gratis" },
    { key: "plus", name: "PLUS", tag: "Mas popular", blurb: "Para los que estudian en serio cada semana.", price: "$4.990", per: "/mes", features: ["Todo lo de Free", "100 generaciones IA combinadas al mes", "Quizzes y flashcards comparten el cupo", "Mas ensayos y analitica avanzada", "Streak Insurance+ y cosmeticos PLUS"], cta: "Subirme a PLUS", primary: true },
  ];
  return (
    <section id="pricing" className="band band-top">
      <div className="container">
        <div className="sec-head">
          <h2><Lines lines={["Empieza gratis.", "Sube cuando lo sientas."]} /></h2>
          <Reveal as="p" d={220}>Cancela cuando quieras. Sin contratos. Sin letra chica.</Reveal>
        </div>
        <div className="price-grid">
          {tiers.map((t, i) => (
            <Reveal key={t.key} d={i * 130} variant="pop" className={"price card" + (t.primary ? " is-plus" : "")}>
              {t.primary && <span className="price-flag">★ {t.tag}</span>}
              <div className="price-head">
                <h3>{t.name}</h3>
                {!t.primary && <span className="price-tag">{t.tag}</span>}
              </div>
              <p className="price-blurb">{t.blurb}</p>
              <div className="price-amt"><span className="amt num">{t.price}</span>{t.per && <span className="per">{t.per}</span>}</div>
              <ul className="price-feats">
                {t.features.map((f, k) => <li key={k}><i><IconCheck size={12} strokeWidth={3.4} /></i>{f}</li>)}
              </ul>
              <a href="https://machreach.com/register" className={"btn btn-lg " + (t.primary ? "btn-ink" : "btn-primary")} style={{ width: "100%" }}>{t.cta}</a>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function FinalCTA() {
  const ref = useMagnetic(0.2, 8);
  return (
    <section id="cta" style={{ paddingTop: 12 }}>
      <div className="container">
        <Reveal variant="pop" className="final">
          <div className="final-doodle" aria-hidden="true"><Doodles /></div>
          <div className="final-inner">
            <h2><Lines lines={["Estudia distinto", "este semestre."]} /></h2>
            <Reveal as="p" d={260}>Conecta tus ramos y empieza a ordenar tu estudio hoy mismo. Tu yo del lunes te lo va a agradecer.</Reveal>
            <Reveal className="final-cta" d={360}>
              <span ref={ref} style={{ display: "inline-flex" }}><a href="https://machreach.com/register" className="btn btn-lg btn-ink">Empieza gratis</a></span>
              <a href="#features" className="btn btn-lg btn-ghost">Ver features <IconArrow size={17} /></a>
            </Reveal>
            <Reveal as="div" d={440} className="final-fine mono">GRATIS PARA SIEMPRE · NO PEDIMOS TARJETA · CANCELA CUANDO QUIERAS</Reveal>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

function Footer() {
  const cols = [
    { h: "Producto", l: ["Features", "Cómo funciona", "Precios"] },
    { h: "Empresa", l: ["Sobre", "Contacto"] },
    { h: "Legal", l: ["Términos", "Privacidad", "Cookies"] },
  ];
  return (
    <footer className="foot">
      <div className="container">
        <div className="foot-grid">
          <div>
            <a href="#" className="logo"><span className="logo-mark"><Mark size={32} uid="ft" /></span><Wordmark size={21} /></a>
            <p style={{ marginTop: 14, color: "var(--ink-2)", fontSize: 14, fontWeight: 600, maxWidth: 310 }}>Estudiar deja de ser una lata. Hecho desde Santiago, Chile, por estudiantes para estudiantes.</p>
          </div>
          {cols.map((c, i) => (
            <div key={i}>
              <div className="foot-h">{c.h}</div>
              <ul>{c.l.map((x, k) => <li key={k}><a href="#">{x}</a></li>)}</ul>
            </div>
          ))}
        </div>
        <div className="foot-bar">
          <span className="mono">© 2026 MACHREACH · SANTIAGO, CL</span>
          <span className="mono">MACHREACH · SANTIAGO, CHILE</span>
        </div>
      </div>
    </footer>
  );
}

Object.assign(window, { Pricing, FinalCTA, Footer });
