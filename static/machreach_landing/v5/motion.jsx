/* v5 motion toolkit — reveal, count-up, magnetic, tilt, spotlight, FLIP */

const useReveal = (opts = {}) => {
  const ref = React.useRef(null);
  const [seen, setSeen] = React.useState(false);
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    const io = new IntersectionObserver((es) => {
      es.forEach((e) => { if (e.isIntersecting) { setSeen(true); io.unobserve(e.target); } });
    }, { threshold: opts.threshold ?? 0.15, rootMargin: opts.rootMargin ?? "0px 0px -8% 0px" });
    io.observe(el);
    const r = el.getBoundingClientRect();
    if (r.top < innerHeight * 0.9) setSeen(true);
    return () => io.disconnect();
  }, []);
  return [ref, seen];
};

/* <Reveal> wrapper: adds .rv/.in + stagger delay */
function Reveal({ children, d = 0, as: As = "div", variant = "rv", className = "", style, ...rest }) {
  const [ref, seen] = useReveal();
  return (
    <As ref={ref} className={`${variant} ${seen ? "in" : ""} ${className}`} style={{ "--d": `${d}ms`, ...style }} {...rest}>
      {children}
    </As>
  );
}

/* masked line reveal for headlines */
function Lines({ lines, d = 0, step = 90, className = "", style }) {
  const [ref, seen] = useReveal({ threshold: 0.3 });
  return (
    <span ref={ref} className={seen ? "in " + className : className} style={style}>
      {lines.map((l, i) => (
        <span key={i} className="lmask" style={{ "--d": `${d + i * step}ms` }}><span>{l}</span></span>
      ))}
    </span>
  );
}

const easeOutExpo = (p) => (p === 1 ? 1 : 1 - Math.pow(2, -10 * p));

function useCountUp(target, active, dur = 1600) {
  const [v, setV] = React.useState(0);
  React.useEffect(() => {
    if (!active) return;
    let raf, t0;
    const step = (ts) => {
      if (!t0) t0 = ts;
      const p = Math.min(1, (ts - t0) / dur);
      setV(target * easeOutExpo(p));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    // A hidden tab never paints, so requestAnimationFrame never runs and the
    // figure would sit at zero — the animation is a flourish, but reading
    // "0 estudiantes activos" is a lie. Land on the real number regardless.
    const settle = setTimeout(() => setV(target), dur + 200);
    return () => { cancelAnimationFrame(raf); clearTimeout(settle); };
  }, [target, active]);
  return v;
}

/* magnetic pointer pull */
function useMagnetic(strength = 0.28, max = 12) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (matchMedia("(hover: none)").matches) return;
    let raf = 0, tx = 0, ty = 0, cx = 0, cy = 0;
    const loop = () => {
      cx += (tx - cx) * 0.16; cy += (ty - cy) * 0.16;
      el.style.transform = `translate3d(${cx.toFixed(2)}px,${cy.toFixed(2)}px,0)`;
      raf = Math.abs(tx - cx) + Math.abs(ty - cy) > 0.05 ? requestAnimationFrame(loop) : 0;
    };
    const kick = () => { if (!raf) raf = requestAnimationFrame(loop); };
    const move = (e) => {
      const r = el.getBoundingClientRect();
      tx = Math.max(-max, Math.min(max, (e.clientX - (r.left + r.width / 2)) * strength));
      ty = Math.max(-max, Math.min(max, (e.clientY - (r.top + r.height / 2)) * strength));
      kick();
    };
    const leave = () => { tx = 0; ty = 0; kick(); };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerleave", leave);
    return () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerleave", leave); cancelAnimationFrame(raf); };
  }, []);
  return ref;
}

/* damped 3D tilt + scroll parallax for the hero panel */
function useTilt(amount = 5, parallax = 0) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let rx = 0, ry = 0, trx = 0, try_ = 0, py = 0, raf = 0;
    const render = () => {
      rx += (trx - rx) * 0.1; ry += (try_ - ry) * 0.1;
      el.style.transform = `perspective(1200px) rotateX(${rx.toFixed(3)}deg) rotateY(${ry.toFixed(3)}deg) translate3d(0,${py.toFixed(2)}px,0)`;
      raf = requestAnimationFrame(render);
    };
    if (!reduce) raf = requestAnimationFrame(render);
    const move = (e) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      try_ = x * amount * 2; trx = -y * amount * 2;
    };
    const leave = () => { trx = 0; try_ = 0; };
    const scroll = () => { if (parallax) py = Math.max(-70, Math.min(70, (scrollY - (el.offsetTop || 0)) * parallax * -0.06)); };
    if (!reduce) {
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerleave", leave);
      addEventListener("scroll", scroll, { passive: true });
      scroll();
    }
    return () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerleave", leave); removeEventListener("scroll", scroll); cancelAnimationFrame(raf); };
  }, []);
  return ref;
}

/* attach cursor spotlight vars to every .spot in the tree */
function useSpotlights(deps = []) {
  React.useEffect(() => {
    const els = Array.from(document.querySelectorAll(".spot"));
    const move = (e) => {
      const el = e.currentTarget, r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${e.clientX - r.left}px`);
      el.style.setProperty("--my", `${e.clientY - r.top}px`);
    };
    els.forEach((el) => el.addEventListener("pointermove", move));
    return () => els.forEach((el) => el.removeEventListener("pointermove", move));
  }, deps);
}

/* FLIP: smoothly animate list reorder */
function useFlip(keys) {
  const nodes = React.useRef(new Map());
  const prev = React.useRef(new Map());
  React.useLayoutEffect(() => {
    nodes.current.forEach((node, k) => {
      if (!node) return;
      const now = node.getBoundingClientRect().top;
      const was = prev.current.get(k);
      if (was != null && Math.abs(was - now) > 0.5) {
        node.animate(
          [{ transform: `translateY(${was - now}px)` }, { transform: "translateY(0)" }],
          { duration: 620, easing: "cubic-bezier(.16,1,.3,1)" }
        );
      }
      prev.current.set(k, now);
    });
  }, [keys]);
  return (k) => (el) => { if (el) nodes.current.set(k, el); else nodes.current.delete(k); };
}

/* scroll progress 0..1 across an element's travel through the viewport */
function useScrollProgress(ref) {
  const [p, setP] = React.useState(0);
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    let raf = 0;
    const calc = () => {
      raf = 0;
      const r = el.getBoundingClientRect();
      const total = r.height + innerHeight * 0.55;
      setP(Math.max(0, Math.min(1, (innerHeight * 0.8 - r.top) / total)));
    };
    const on = () => { if (!raf) raf = requestAnimationFrame(calc); };
    calc();
    addEventListener("scroll", on, { passive: true });
    addEventListener("resize", on);
    return () => { removeEventListener("scroll", on); removeEventListener("resize", on); cancelAnimationFrame(raf); };
  }, []);
  return p;
}

function ScrollProgressBar() {
  React.useEffect(() => {
    const bar = document.querySelector("#prog i"); if (!bar) return;
    let raf = 0;
    const calc = () => {
      raf = 0;
      const max = Math.max(1, document.documentElement.scrollHeight - innerHeight);
      bar.style.transform = `scaleX(${Math.min(1, Math.max(0, scrollY / max))})`;
    };
    const on = () => { if (!raf) raf = requestAnimationFrame(calc); };
    calc(); addEventListener("scroll", on, { passive: true }); addEventListener("resize", on);
    return () => { removeEventListener("scroll", on); removeEventListener("resize", on); };
  }, []);
  return <div id="prog" aria-hidden="true"><i /></div>;
}

Object.assign(window, { useReveal, Reveal, Lines, useCountUp, useMagnetic, useTilt, useSpotlights, useFlip, useScrollProgress, ScrollProgressBar, easeOutExpo });
