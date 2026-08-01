/* v6 — "Plan de estudios" as its own hero-scale section.
   The week deals out of a single stacked deck at centre, lands with a spring,
   a highlighter sweeps the row, then the stamp lands. Loops so it always catches the eye. */

const PLAN_DAYS = [
  { d: "L", items: [{ t: "Cálculo II", c: "var(--brand)" }] },
  { d: "M", items: [{ t: "Inglés", c: "var(--plum)" }] },
  { d: "M", items: [{ t: "Física", c: "var(--sky)" }] },
  { d: "J", items: [{ t: "Química", c: "#C2497E" }] },
  { d: "V", items: [{ t: "Cálculo II", c: "var(--brand)" }] },
  { d: "S", items: [{ t: "Historia", c: "var(--amber)", dark: true }] },
  { d: "D", items: [{ t: "Química", c: "#C2497E" }, { t: "Repaso", c: "var(--good)" }] },
];

function PlanSection() {
  const [ref, seen] = useReveal({ threshold: 0.25 });
  let n = 0;
  const total = PLAN_DAYS.reduce((a, d) => a + d.items.length, 0);

  return (
    <section id="plan" className="plan-sec" ref={ref}>
      <div className="plan-glow" aria-hidden="true" />
      <div className="container">
        <div className="sec-head plan-head">
          <h2><Lines lines={["Te arma el plan de", "estudios perfecto."]} /></h2>
        </div>
        <div className={"plan-stage " + (seen ? "go" : "")}>
          <div className="plan-deck" aria-hidden="true"><i /><i /><i /></div>
          <div className="plan-grid">
            {PLAN_DAYS.map((day, ci) => (
              <div className="plan-day" key={ci}>
                <div className="plan-dl" style={{ "--cd": `${ci * 55}ms` }}>{day.d}</div>
                <div className="plan-slot">
                  {day.items.map((it, k) => {
                    const delay = 420 + n++ * 95;
                    return (
                      <div key={k} className="plan-cell"
                        style={{ background: it.c, color: it.dark ? "var(--ink)" : "#fff", "--ci": ci, "--fy": `${-18 - k * 10}px`, "--fr": `${(ci % 2 ? 1 : -1) * (6 + k * 5)}deg`, "--cd": `${delay}ms` }}>
                        {it.t}
                        <span className="plan-spark" style={{ "--cd": `${delay + 260}ms` }} />
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
          <span className="plan-sweep" style={{ "--cd": `${420 + total * 95 + 120}ms` }} aria-hidden="true" />
          <div className="plan-done" style={{ "--cd": `${420 + total * 95 + 460}ms` }}>
            <span className="plan-ring" aria-hidden="true" />
            <IconCheck size={17} strokeWidth={3.2} /> Tu plan está listo
          </div>
        </div>
      </div>
    </section>
  );
}
window.PlanSection = PlanSection;
