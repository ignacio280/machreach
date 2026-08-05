/* Focus — session reward claim.

   Imported from the design project. The mock hands the overlay its final
   numbers up front; here the server decides them, so the overlay opens on the
   click, the request goes out alongside it, and the reveal waits for the
   answer. The design already had a word for that wait — the button reads
   "Sumando…" until the totals land — so the deviation is invisible.

   Nothing is ever animated that the server did not actually pay out. */

function useRoll(target, run, dur = 900, delay = 0) {
  const [v, setV] = React.useState(0);
  React.useEffect(() => {
    if (!run) { setV(0); return; }
    let raf, t0 = null;
    const to = setTimeout(() => {
      const step = (t) => {
        if (t0 === null) t0 = t;
        const p = Math.min(1, (t - t0) / dur);
        if (p >= 1) { setV(target); return; }
        setV(Math.round(target * (1 - Math.pow(1 - p, 3))));
        raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    }, delay);
    // A hidden tab never paints, so the frame callback never runs. The figure
    // still has to be the real one.
    const guard = setTimeout(() => setV(target), delay + dur + 60);
    return () => { clearTimeout(to); clearTimeout(guard); cancelAnimationFrame(raf); };
  }, [run, target]);
  return v;
}

/* Throw a handful of orbs from the seal to a topbar chip, then pop the chip.
   Purely decorative: the chip's own number is server state and is refreshed
   by the page, not by this. */
function flyOrbs(from, sel, kind, count, delay) {
  const el = document.querySelector(sel);
  if (!el || !from) return;
  const r = el.getBoundingClientRect(), s = from.getBoundingClientRect();
  const ox = s.left + s.width / 2, oy = s.top + s.height / 2;
  for (let i = 0; i < count; i++) {
    const o = document.createElement("i");
    o.className = "clm-orb " + kind;
    o.style.left = ox + "px"; o.style.top = oy + "px";
    o.style.setProperty("--dx", r.left + r.width / 2 - ox + "px");
    o.style.setProperty("--dy", r.top + r.height / 2 - oy + "px");
    o.style.setProperty("--sx", (Math.random() * 150 - 75) + "px");
    o.style.setProperty("--sy", (-50 - Math.random() * 90) + "px");
    o.style.animationDelay = delay + i * 55 + "ms";
    document.body.appendChild(o);
    setTimeout(() => o.remove(), delay + i * 55 + 1000);
  }
  setTimeout(() => {
    el.classList.add("chip-pop");
    setTimeout(() => el.classList.remove("chip-pop"), 620);
  }, delay + (count - 1) * 55 + 620);
}

function ClaimRows({ result, run }) {
  const xp = useRoll(result ? result.xp : 0, run, 950, 60);
  const co = useRoll(result ? result.coins : 0, run, 800, 320);
  return (
    <div className={"clm-rows" + (run ? " on" : "")}>
      <div className="clm-row xp" style={{ "--d": "60ms" }}>
        <span className="ico-badge"><IconBolt size={17} color="var(--plum)" /></span>
        <div className="l">Experiencia</div>
        <div className="v num">+{xp}<small>XP</small></div>
      </div>
      <div className="clm-row coin" style={{ "--d": "220ms" }}>
        <span className="ico-badge"><IconCoin size={17} color="#B58309" /></span>
        <div className="l">Monedas</div>
        <div className="v num">+{co}<small>🪙</small></div>
      </div>
      {result && result.streak > 0 && (
        <div className="clm-row fire" style={{ "--d": "380ms" }}>
          <span className="ico-badge"><IconFire size={17} color="var(--brand)" /></span>
          <div className="l">Racha</div>
          <div className="v num">{result.streak}<small>días</small></div>
        </div>
      )}
    </div>
  );
}

function ClaimOverlay({ preview, result, onClose }) {
  const [phase, setPhase] = React.useState("enter");
  const seal = React.useRef(null);
  const on = phase !== "enter";
  const settled = !!result;

  // The opening beats are pure choreography and run on their own.
  React.useEffect(() => {
    const ts = [
      setTimeout(() => setPhase("burst"), 260),
      setTimeout(() => setPhase("reveal"), 780),
    ];
    return () => ts.forEach(clearTimeout);
  }, []);

  // Everything past the reveal needs real numbers, so it waits for them. A
  // slow request just holds the card on "Sumando…" instead of animating a
  // total that might not be what was banked.
  //
  // `on` is a dependency, not just a guard: a local server can answer inside
  // the 260ms entrance, and an effect that only watched `settled` would bail
  // out on the still-"enter" phase and never run again — leaving the card
  // stuck on "Sumando…" with the orbs unthrown.
  React.useEffect(() => {
    if (!settled || !on) return undefined;
    const ts = [
      setTimeout(() => {
        flyOrbs(seal.current, ".chip.xp", "xp", 7, 0);
        if (result.coins > 0) flyOrbs(seal.current, ".chip.coin", "coin", 5, 220);
        if (result.streak > 0) flyOrbs(seal.current, ".chip.fire", "fire", 3, 440);
      }, 1120),
      setTimeout(() => setPhase("done"), 1870),
    ];
    return () => ts.forEach(clearTimeout);
  }, [settled, on]);

  const mins = preview.mins;
  const rounds = preview.rounds;
  return (
    <div className="clm-back" data-phase={phase}>
      <div className="clm-card" role="dialog" aria-label="Recompensa de sesión">
        <div className="clm-eye">Bloque completado</div>
        <h2 className="clm-t big">{mins} {mins === 1 ? "minuto" : "minutos"} de enfoque real</h2>
        <p className="clm-s">{preview.course}{rounds ? ` · ${rounds} ${rounds === 1 ? "bloque" : "bloques"}` : ""}</p>

        <div className="clm-sealwrap big">
          <i className="clm-glow" />
          {on && <><i className="clm-shock" /><i className="clm-shock d2" /></>}
          {on && <div className="clm-rays">{Array.from({ length: 12 }).map((_, i) => <i key={i} style={{ "--a": i * 30 + "deg", "--d": i * 18 + "ms" }} />)}</div>}
          {on && <div className="clm-sparks">{Array.from({ length: 14 }).map((_, i) => <i key={i} className={i % 3 === 0 ? "c" : i % 3 === 1 ? "p" : "b"} style={{ "--a": i * 25.7 + "deg", "--r": (95 + Math.random() * 70) + "px", "--d": (Math.random() * 160) + "ms" }} />)}</div>}
          <div className="clm-seal" ref={seal}><IconBolt size={46} color="#fff" /></div>
        </div>

        <ClaimRows result={result} run={settled && (phase === "reveal" || phase === "done")} />

        <div className="clm-foot">
          <button className="btn btn-lg clm-btn btn-primary" disabled={phase !== "done"} onClick={onClose}>
            {phase === "done" ? "Seguir estudiando" : "Sumando…"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* The sealed card that sits in the timer once a block is waiting to be
   claimed. It only announces the intent: the overlay is deliberately NOT a
   child of this component, because a successful claim empties the pending
   list, which unmounts this panel — and would take a half-played animation
   down with it. The Timer owns the overlay and outlives both. */
function ClaimPanel({ preview, onStart, busy, expiresAt }) {
  return (
    <section className="clm" data-phase="sealed">
      <div className="clm-hd">
        <div className="clm-sealwrap">
          <i className="clm-glow" />
          <div className="clm-seal"><IconBolt size={34} color="#fff" /></div>
        </div>
        <div className="clm-txt">
          <div className="clm-eye">Sesión completada</div>
          <h3 className="clm-t">{preview.mins} {preview.mins === 1 ? "minuto" : "minutos"} de enfoque real</h3>
          <p className="clm-s">
            {preview.course}{preview.rounds ? ` · ${preview.rounds} ${preview.rounds === 1 ? "bloque" : "bloques"}` : ""}
            {expiresAt ? <React.Fragment> · <ClaimCountdown until={expiresAt} /></React.Fragment> : null}
          </p>
        </div>
      </div>
      <div className="clm-foot">
        <button className="btn btn-primary btn-lg clm-btn" onClick={onStart} disabled={busy}>
          <IconBolt size={18} color="#fff" /> Reclamar recompensa
        </button>
      </div>
    </section>
  );
}

Object.assign(window, { ClaimPanel, ClaimOverlay, ClaimRows, useRoll, flyOrbs });
