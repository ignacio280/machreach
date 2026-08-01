/* v6 — cinematic intro: spark → ignition → pages → wordmark → the book opens */
function IntroV6({ onDone, enabled = true }) {
  const K = LOGO_COLORS, P = LOGO_PATHS;
  const DUR = 2750, OPEN = 1060;
  const [t, setT] = React.useState(0);
  const [gone, setGone] = React.useState(!enabled);
  const [open, setOpen] = React.useState(false);
  const done = React.useRef(false);

  const finish = React.useCallback(() => {
    if (done.current) return; done.current = true;
    setOpen(true); onDone && onDone();
    setTimeout(() => setGone(true), OPEN + 60);
  }, [onDone]);

  React.useEffect(() => {
    if (!enabled) { onDone && onDone(); return; }
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    let raf, t0 = performance.now();
    const tick = (now) => { const e = now - t0; setT(e); if (e >= DUR) return finish(); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    const safety = setTimeout(finish, DUR + 1400);
    const key = (e) => { if (["Escape", "Enter", " "].includes(e.key)) finish(); };
    addEventListener("keydown", key);
    return () => { cancelAnimationFrame(raf); clearTimeout(safety); removeEventListener("keydown", key); };
  }, [enabled]);

  if (gone) return null;

  const cl = (v, a, b) => Math.min(b, Math.max(a, v));
  const oc = (p) => 1 - Math.pow(1 - p, 3);
  const ob = (p) => { const c = 1.7; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); };

  const T_SPARK = 120, T_HIT = 720, T_PAGES = 880, T_WORD = 1280, T_TAG = 2050;
  const sp = cl((t - T_SPARK) / (T_HIT - T_SPARK), 0, 1);
  const sparkY = -230 + sp * sp * 190;
  const lit = t >= T_HIT;
  const flame = ob(cl((t - T_HIT) / 620, 0, 1));
  const flash = lit ? Math.max(0, Math.exp(-(t - T_HIT) / 150)) : 0;
  const pl = ob(cl((t - T_PAGES) / 720, 0, 1));
  const pr = ob(cl((t - T_PAGES - 110) / 720, 0, 1));
  const fs = 1 + (lit ? Math.sin(t / 95) * 0.05 : 0);
  const day = oc(cl((t - T_HIT) / 700, 0, 1));
  const tag = oc(cl((t - T_TAG) / 520, 0, 1));
  const letters = "MachReach".split("");

  return (
    <div className={"intro" + (open ? " open" : "")} onClick={finish} role="button" aria-label="Saltar intro">
      <div className="intro-sky" style={{ opacity: 1 - day }} />
      <div className="intro-flash" style={{ opacity: flash * 0.85 }} />
      <div className="intro-stage">
        <div className="intro-mark" style={{ filter: lit ? `drop-shadow(0 16px 44px rgba(255,106,43,${0.2 + flash * 0.3}))` : "none" }}>
          <svg width="150" height="150" viewBox="0 0 100 100" style={{ overflow: "visible" }}>
            <defs>
              <linearGradient id="iv6-f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={K.orange} /><stop offset="1" stopColor={K.orangeDp} /></linearGradient>
            </defs>
            {t < T_HIT && (
              <g>
                <line x1="50" y1={64 + sparkY - 26} x2="50" y2={64 + sparkY} stroke={K.amber} strokeWidth="2.4" strokeLinecap="round" opacity=".55" />
                <circle cx="50" cy={64 + sparkY} r="5.5" fill={K.core} />
                <circle cx="50" cy={64 + sparkY} r="11" fill="rgba(255,170,90,.35)" />
              </g>
            )}
            <g style={{ opacity: lit ? 1 : 0 }} transform={`translate(50 64) scale(${(2 - fs) * flame} ${fs * flame}) translate(-50 -64)`}>
              <path d={P.flameOut} fill="url(#iv6-f)" /><path d={P.flameIn} fill={K.amber} /><path d={P.flameCore} fill={K.core} />
            </g>
            <g transform={`translate(${(1 - pl) * -60} ${(1 - pl) * 26}) rotate(${(1 - pl) * -26} 50 78)`} opacity={cl(pl * 1.6, 0, 1)}><path d={P.pageL} fill={K.purple} /></g>
            <g transform={`translate(${(1 - pr) * 60} ${(1 - pr) * 26}) rotate(${(1 - pr) * 26} 50 78)`} opacity={cl(pr * 1.6, 0, 1)}><path d={P.pageR} fill={K.purpleSh} /></g>
            <path d={P.spine} stroke="rgba(255,246,228,.5)" strokeWidth="1.6" opacity={cl((pl - .82) * 6, 0, 1)} />
          </svg>
          {lit && t < T_HIT + 1300 && (
            <div className="intro-burst" aria-hidden="true">
              {Array.from({ length: 16 }, (_, i) => {
                const a = (i / 16) * Math.PI * 2 + 0.4;
                const d = 110 + (i % 4) * 46;
                return <i key={i} style={{ background: [K.orange, K.amber, K.core, K.orangeDp][i % 4], "--bx": Math.cos(a) * d + "px", "--by": Math.sin(a) * d * .8 - 20 + "px", animationDelay: (i % 5) * 22 + "ms" }} />;
              })}
            </div>
          )}
        </div>

        <div className="intro-word" aria-label="MachReach">
          {letters.map((ch, i) => {
            const p = ob(cl((t - T_WORD - i * 62) / 620, 0, 1));
            return <span key={i} style={{ opacity: cl(p * 1.6, 0, 1), transform: `translateY(${(1 - p) * 46}px) rotate(${(1 - p) * -14}deg) scale(${0.6 + p * 0.4})`, color: i < 4 ? "var(--ink)" : "var(--brand)" }}>{ch}</span>;
          })}
          <svg className="intro-swash" viewBox="0 0 300 20" preserveAspectRatio="none" aria-hidden="true">
            <path d="M4 14 C70 4 150 3 214 8 C250 11 278 8 296 3" fill="none" stroke="var(--brand)" strokeWidth="7" strokeLinecap="round"
              style={{ strokeDasharray: 320, strokeDashoffset: 320 - 320 * oc(cl((t - 1850) / 620, 0, 1)) }} />
          </svg>
        </div>
        <div className="intro-tag" style={{ opacity: tag, transform: `translateY(${(1 - tag) * 10}px)` }}>Estudiar deja de ser una lata.</div>
      </div>
      <div className="intro-wipe" aria-hidden="true" />
      <button className="intro-skip" onClick={finish}>Saltar intro →</button>
    </div>
  );
}
window.IntroV6 = IntroV6;
