/* v6 fx — floating doodles + confetti burst */
const DL = ["var(--brand)", "var(--plum)", "var(--sky)", "var(--amber)", "var(--lime)", "var(--pink)"];

function Doodles() {
  const shapes = [
    { t: "star", x: "3%", y: "6%", s: 38, c: "var(--amber)", dr: "-8deg", dt: "6s" },
    { t: "squig", x: "90%", y: "10%", s: 60, c: "var(--plum)", dr: "10deg", dt: "8s" },
    { t: "ring", x: "84%", y: "58%", s: 46, c: "var(--sky)", dr: "0deg", dt: "7s", spin: true },
    { t: "star", x: "94%", y: "86%", s: 30, c: "var(--pink)", dr: "6deg", dt: "5.5s" },
    { t: "blob", x: "62%", y: "-3%", s: 36, c: "var(--lime)", dr: "-6deg", dt: "9s" },
  ];
  return (
    <div className="doodles" aria-hidden="true">
      {shapes.map((sh, i) => (
        <div key={i} className={"doodle" + (sh.spin ? " spin" : "")} style={{ left: sh.x, top: sh.y, "--dr": sh.dr, "--dt": sh.dt }}>
          <svg width={sh.s} height={sh.s} viewBox="0 0 40 40" style={{ overflow: "visible" }}>
            {sh.t === "star" && <path d="M20 2l4.5 10.5L36 15l-9 8 2.4 12L20 29l-9.4 6L13 23 4 15l11.5-2.5z" fill={sh.c} stroke="var(--ink)" strokeWidth="2.5" strokeLinejoin="round" />}
            {sh.t === "ring" && <circle cx="20" cy="20" r="15" fill="none" stroke={sh.c} strokeWidth="6" strokeDasharray="6 7" />}
            {sh.t === "squig" && <path d="M2 24c6-14 10 8 16-4s10 8 20-6" fill="none" stroke={sh.c} strokeWidth="5" strokeLinecap="round" />}
            {sh.t === "blob" && <path d="M20 3c9 0 16 6 16 15s-5 19-16 19S4 27 4 18 11 3 20 3z" fill={sh.c} stroke="var(--ink)" strokeWidth="2.5" />}
          </svg>
        </div>
      ))}
    </div>
  );
}

function Confetti({ fire }) {
  if (!fire) return null;
  const bits = Array.from({ length: 18 }, (_, i) => {
    const a = (i / 18) * Math.PI * 2 + Math.random();
    const d = 90 + Math.random() * 140;
    return { cx: Math.cos(a) * d + "px", cy: Math.sin(a) * d - 40 + "px", rot: (Math.random() * 720 - 360) + "deg", c: DL[i % DL.length], delay: Math.random() * 60 };
  });
  return (
    <div className="confetti" key={fire}>
      {bits.map((b, i) => <i key={i} style={{ background: b.c, "--cx": b.cx, "--cy": b.cy, "--crot": b.rot, animationDelay: b.delay + "ms" }} />)}
    </div>
  );
}

Object.assign(window, { Doodles, Confetti });
