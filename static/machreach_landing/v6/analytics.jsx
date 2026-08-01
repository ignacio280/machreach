/* v6 — Analítica de estudio, its own section. Plays once.
   The trend line is measured off the real dot positions so it always sits on them. */
const AN_BARS = [3.2, 5.1, 2.4, 7.0, 4.3, 8.2, 6.1];
const AN_LABELS = ["L", "M", "M", "J", "V", "S", "D"];
const AN_MAX = 9;

function AnalyticsSection() {
  const [ref, seen] = useReveal({ threshold: 0.28 });
  const hours = useCountUp(36.3, seen, 1800);
  const pct = useCountUp(24, seen, 1600);
  const chartRef = React.useRef(null);
  const dots = React.useRef([]);
  const pathRef = React.useRef(null);
  const [geo, setGeo] = React.useState(null);
  const [len, setLen] = React.useState(1200);

  React.useLayoutEffect(() => {
    const offsetIn = (el, root) => {
      let x = 0, y = 0, n = el;
      while (n && n !== root) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent; }
      return [x, y];
    };
    const measure = () => {
      const c = chartRef.current;
      if (!c || !c.offsetWidth) return;
      const pts = dots.current.filter(Boolean).map((d) => {
        const [x, y] = offsetIn(d, c);
        return [x + d.offsetWidth / 2, y + d.offsetHeight / 2];
      });
      if (pts.length < 2) return;
      setGeo({ w: c.offsetWidth, h: c.offsetHeight, d: pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") });
    };
    measure();
    const raf = requestAnimationFrame(measure);
    const t1 = setTimeout(measure, 700);
    const t2 = setTimeout(measure, 2000);
    let ro;
    if (typeof ResizeObserver !== "undefined" && chartRef.current) { ro = new ResizeObserver(measure); ro.observe(chartRef.current); }
    addEventListener("resize", measure);
    return () => { cancelAnimationFrame(raf); clearTimeout(t1); clearTimeout(t2); ro && ro.disconnect(); removeEventListener("resize", measure); };
  }, []);

  React.useEffect(() => {
    if (pathRef.current && geo) { try { setLen(Math.ceil(pathRef.current.getTotalLength()) + 4); } catch (e) {} }
  }, [geo]);

  return (
    <section id="features" className={"an-sec " + (seen ? "go" : "")} ref={ref}>
      <div className="an-glow" aria-hidden="true" />
      <div className="container">
        <div className="sec-head">
          <h2><Lines lines={["Analítica de estudio"]} /></h2>
          <Reveal as="p" d={200}>Cuántas horas, qué día rindes más, cuánto tiempo le diste a cada examen.</Reveal>
        </div>

        <Reveal variant="pop" d={120} className="an-panel card-2">
          <div className="an-top">
            <div className="an-total">
              <span className="an-h num">{hours.toFixed(1)}h</span>
              <span className="an-sub">esta semana</span>
            </div>
            <span className="an-delta num">↑ +{Math.round(pct)}%</span>
          </div>

          <div className="an-chart" ref={chartRef}>
            <div className="an-grid" aria-hidden="true">{[0, 1, 2, 3, 4].map((i) => <i key={i} style={{ "--gd": `${i * 70}ms` }} />)}</div>

            {geo && (
              <svg className="an-line" width={geo.w} height={geo.h} viewBox={`0 0 ${geo.w} ${geo.h}`} aria-hidden="true">
                <path ref={pathRef} d={geo.d} fill="none" stroke="var(--ink)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"
                  style={{ strokeDasharray: len, strokeDashoffset: seen ? 0 : len }} />
              </svg>
            )}

            <div className="an-cols">
              {AN_BARS.map((v, i) => (
                <div className="an-col" key={i}>
                  <div className="an-track">
                    {i === 5 && <span className="an-chip" style={{ "--bd2": `${900 + i * 90}ms`, bottom: `calc(${(v / AN_MAX) * 100}% + 14px)` }}>8.2h</span>}
                    <i className={"an-bar" + (i === 5 ? " peak" : "")} style={{ height: `${(v / AN_MAX) * 100}%`, "--bd2": `${260 + i * 90}ms` }} />
                    <span ref={(el) => (dots.current[i] = el)} className="an-dot" style={{ bottom: `calc(${(v / AN_MAX) * 100}% - 7px)`, "--bd2": `${700 + i * 90}ms` }} />
                  </div>
                  <span className="an-lab" style={{ "--bd2": `${300 + i * 90}ms` }}>{AN_LABELS[i]}</span>
                </div>
              ))}
            </div>
            <span className="an-scan" aria-hidden="true" />
          </div>
        </Reveal>
      </div>
    </section>
  );
}
window.AnalyticsSection = AnalyticsSection;
