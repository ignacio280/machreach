/* MachReach mark — open book cradling the streak flame */
const LOGO_COLORS = { orange:"#FF6A2B", orangeDp:"#D94B14", amber:"#F2A156", core:"#FFE9C9", cream:"#FFF6E4", creamDim:"#F0DFC0", ink:"#181320", purple:"#5B4694", purpleDk:"#3A2A6E", purpleSh:"#4A3880" };
const LOGO_PATHS = {
  squircle:"M50 3 C19 3 3 19 3 50 C3 81 19 97 50 97 C81 97 97 81 97 50 C97 19 81 3 50 3 Z",
  pageL:"M50 82 C41 73 29 70 16 72 L16 50 C29 48 41 51 50 58 Z",
  pageR:"M50 82 C59 73 71 70 84 72 L84 50 C71 48 59 51 50 58 Z",
  flameOut:"M50 14 C55 26 67 33 67 46 C67 57 59 64 50 64 C41 64 33 57 33 46 C33 33 45 26 50 14 Z",
  flameIn:"M50 33 C53 39 60 42 60 49 C60 55 55 59 50 59 C45 59 40 55 40 49 C40 42 47 39 50 33 Z",
  flameCore:"M50 45 C52 49 56 51 56 54 C56 57 53 59 50 59 C47 59 44 57 44 54 C44 51 48 49 50 45 Z",
  spine:"M50 58 L50 82",
};

function Mark({ size = 36, variant = "flat", flick = 0, uid = "m", live = false }) {
  const K = LOGO_COLORS, P = LOGO_PATHS;
  const isApp = variant === "app";
  const gid = `mk-${uid}`;
  const fs = 1 + flick;
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" style={{ display: "block", overflow: "visible" }} aria-hidden="true">
      <defs>
        <linearGradient id={gid + "-f"} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={K.orange} /><stop offset="1" stopColor={K.orangeDp} />
        </linearGradient>
        {isApp && (
          <linearGradient id={gid} x1="0" y1="0" x2="0.3" y2="1">
            <stop offset="0" stopColor={K.purpleDk} /><stop offset="0.55" stopColor={K.purple} /><stop offset="1" stopColor={K.orange} />
          </linearGradient>
        )}
      </defs>
      {isApp && <path d={P.squircle} fill={`url(#${gid})`} />}
      <g className={live ? "mk-flame" : ""} transform={`translate(50 64) scale(${2 - fs} ${fs}) translate(-50 -64)`}>
        <path d={P.flameOut} fill={`url(#${gid}-f)`} />
        <path d={P.flameIn} fill={K.amber} />
        <path d={P.flameCore} fill={K.core} />
      </g>
      <path d={P.pageL} fill={isApp ? K.cream : K.purple} />
      <path d={P.pageR} fill={isApp ? K.creamDim : K.purpleSh} />
      <path d={P.spine} stroke={isApp ? "rgba(58,42,110,.35)" : "rgba(255,246,228,.45)"} strokeWidth="1.6" />
    </svg>
  );
}

function Wordmark({ size = 21 }) {
  return <span className="logo-text" style={{ fontSize: size }}>Mach<em>Reach</em></span>;
}

/* Short, restrained intro: mark assembles, wordmark masks up, curtain lifts. */
function Intro({ onDone, enabled = true }) {
  const K = LOGO_COLORS, P = LOGO_PATHS;
  const DUR = 2150, LIFT = 720;
  const [t, setT] = React.useState(0);
  const [gone, setGone] = React.useState(!enabled);
  const [lift, setLift] = React.useState(false);
  const done = React.useRef(false);

  const finish = React.useCallback(() => {
    if (done.current) return; done.current = true;
    setLift(true); onDone && onDone();
    setTimeout(() => setGone(true), LIFT + 60);
  }, [onDone]);

  React.useEffect(() => {
    if (!enabled) { onDone && onDone(); return; }
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    let raf, t0 = performance.now();
    const tick = (now) => { const e = now - t0; setT(e); if (e >= DUR) return finish(); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    const safety = setTimeout(finish, DUR + 1200);
    const key = (e) => { if (["Escape", "Enter", " "].includes(e.key)) finish(); };
    addEventListener("keydown", key);
    return () => { cancelAnimationFrame(raf); clearTimeout(safety); removeEventListener("keydown", key); };
  }, [enabled]);

  if (gone) return null;
  const cl = (v, a, b) => Math.min(b, Math.max(a, v));
  const oc = (p) => 1 - Math.pow(1 - p, 3);
  const ob = (p) => { const c = 1.5; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); };
  const pl = ob(cl((t - 120) / 620, 0, 1));
  const pr = ob(cl((t - 220) / 620, 0, 1));
  const lit = t >= 760;
  const fl = ob(cl((t - 760) / 520, 0, 1));
  const fs = 1 + (lit ? Math.sin(t / 110) * 0.045 : 0);

  return (
    <div className={"intro" + (lift ? " out" : "")} onClick={finish} role="button" aria-label="Saltar intro">
      <div className="intro-veil" />
      <div className="intro-stage">
        <svg width="132" height="132" viewBox="0 0 100 100" style={{ overflow: "visible", filter: lit ? "drop-shadow(0 14px 40px rgba(255,106,43,.35))" : "none" }}>
          <defs><linearGradient id="ig-f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={K.orange} /><stop offset="1" stopColor={K.orangeDp} /></linearGradient></defs>
          <g style={{ opacity: lit ? 1 : 0 }} transform={`translate(50 64) scale(${(2 - fs) * fl} ${fs * fl}) translate(-50 -64)`}>
            <path d={P.flameOut} fill="url(#ig-f)" /><path d={P.flameIn} fill={K.amber} /><path d={P.flameCore} fill={K.core} />
          </g>
          <g transform={`translate(${(1 - pl) * -34} ${(1 - pl) * 14})`} opacity={cl(pl * 1.5, 0, 1)}><path d={P.pageL} fill={K.purple} /></g>
          <g transform={`translate(${(1 - pr) * 34} ${(1 - pr) * 14})`} opacity={cl(pr * 1.5, 0, 1)}><path d={P.pageR} fill={K.purpleSh} /></g>
          <path d={P.spine} stroke="rgba(255,246,228,.45)" strokeWidth="1.6" opacity={cl((pl - .8) * 5, 0, 1)} />
        </svg>
        <div className="intro-word" style={{ clipPath: `inset(0 0 ${100 - oc(cl((t - 980) / 700, 0, 1)) * 100}% 0)` }}>
          <span>Mach</span><em>Reach</em>
        </div>
        <div className="intro-tag" style={{ opacity: oc(cl((t - 1400) / 600, 0, 1)) }}>Estudiar deja de ser una lata.</div>
      </div>
      <button className="intro-skip" onClick={finish}>Saltar intro →</button>
    </div>
  );
}

Object.assign(window, { LOGO_COLORS, LOGO_PATHS, Mark, Wordmark, Intro });
