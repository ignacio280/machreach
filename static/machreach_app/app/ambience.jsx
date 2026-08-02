/* Live ambience scenes — canvas overlay ported from the MachReach focus page.
   rain: falling drops + splash ripples · ocean: wave bands + rising bubbles
   forest: drifting leaves (+ fireflies in dark) · fire: rising embers over a flickering glow
   brown: floating dust. Honors prefers-reduced-motion, pauses when the tab is hidden. */
function useAmbience(type) {
  const st = React.useRef({ canvas: null, ctx: null, type: "off", drops: [], splashes: [], parts: [], raf: 0, w: 0, h: 0, dpr: 1, t: 0, last: 0 });

  React.useEffect(() => {
    const fx = st.current;
    const dark = () => document.documentElement.getAttribute("data-theme") === "dark";
    const reduced = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
    const rnd = (a, b) => a + Math.random() * (b - a);

    const c = document.createElement("canvas");
    c.setAttribute("aria-hidden", "true");
    c.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:3";
    document.body.appendChild(c);
    fx.canvas = c; fx.ctx = c.getContext("2d");

    const resize = () => {
      fx.dpr = Math.min(devicePixelRatio || 1, 1.5);
      fx.w = innerWidth; fx.h = innerHeight;
      c.width = Math.round(fx.w * fx.dpr); c.height = Math.round(fx.h * fx.dpr);
      fx.ctx.setTransform(fx.dpr, 0, 0, fx.dpr, 0, 0);
      if (fx.type !== "off") spawn(fx.type);
    };

    const leaf = (init) => {
      const pal = [[34, 160, 107], [132, 204, 22], [214, 158, 46], [101, 163, 77]];
      return { x: rnd(0, fx.w), y: init ? rnd(-fx.h, fx.h) : rnd(-70, -10), size: rnd(4.5, 9.5), fall: rnd(.45, 1.25), ph: rnd(0, 6.28), swayAmp: rnd(.5, 1.6), swaySp: rnd(.6, 1.4), rot: rnd(0, 6.28), rotSp: rnd(-.035, .035), rgb: pal[Math.floor(Math.random() * pal.length)], a: rnd(.3, .62) };
    };
    const ember = (init) => ({ x: rnd(0, fx.w), y: init ? rnd(fx.h * .25, fx.h) : fx.h + rnd(4, 40), r: rnd(1.1, 3), rise: rnd(.7, 2.3), ph: rnd(0, 6.28), a: rnd(.35, .8) });

    function spawn(t) {
      const area = fx.w * fx.h; let i, n;
      fx.drops = []; fx.splashes = []; fx.parts = [];
      if (t === "rain") { n = Math.round(area / 7500); for (i = 0; i < n; i++) fx.drops.push({ x: rnd(-40, fx.w), y: rnd(-fx.h, fx.h), len: rnd(11, 24), sp: rnd(10, 17), w: rnd(.8, 1.7), a: rnd(.18, .46) }); }
      else if (t === "ocean") { n = Math.round(area / 36000); for (i = 0; i < n; i++) fx.parts.push({ x: rnd(0, fx.w), y: rnd(fx.h * .45, fx.h), r: rnd(1.2, 3.2), sp: rnd(.16, .5), ph: rnd(0, 6.28), a: rnd(.16, .38) }); }
      else if (t === "forest") { n = Math.max(14, Math.round(area / 44000)); for (i = 0; i < n; i++) fx.parts.push(leaf(true)); if (dark()) for (i = 0; i < 9; i++) fx.parts.push({ fly: 1, x: rnd(0, fx.w), y: rnd(fx.h * .1, fx.h * .85), ph: rnd(0, 6.28), dx: rnd(-.35, .35), dy: rnd(-.22, .22) }); }
      else if (t === "fire") { n = Math.round(area / 15000); for (i = 0; i < n; i++) fx.parts.push(ember(true)); }
      else if (t === "night") {
        n = Math.round(area / 6500);
        for (i = 0; i < n; i++) fx.parts.push({ x: rnd(0, fx.w), y: rnd(0, fx.h), r: rnd(.6, 2), ph: rnd(0, 6.28), tw: rnd(.5, 2.1), a: rnd(.25, .8), drift: rnd(.02, .09) });
        fx.shoot = null; fx.nextShoot = rnd(2, 6);
      }
    }

    function tick(ts) {
      fx.raf = 0;
      if (fx.type === "off") return;
      const dt = fx.last ? Math.min((ts - fx.last) / 16.7, 3) : 1;
      fx.last = ts; fx.t += dt * 0.016;
      const ctx = fx.ctx, w = fx.w, h = fx.h, t = fx.t, d = dark();
      let i, p, x;
      ctx.clearRect(0, 0, w, h);

      if (fx.type === "rain") {
        const wind = 2.1, rgb = d ? "165,200,255" : "88,134,200";
        ctx.lineCap = "round";
        for (i = 0; i < fx.drops.length; i++) {
          p = fx.drops[i];
          p.y += p.sp * dt; p.x += wind * dt * (p.sp / 14);
          if (p.y - p.len > h) { if (Math.random() < .3) fx.splashes.push({ x: p.x, y: h - rnd(2, 30), r: 0, max: rnd(5, 11), a: .38 }); p.y = rnd(-90, -10); p.x = rnd(-40, w); }
          if (p.x > w + 40) p.x = -30;
          ctx.strokeStyle = "rgba(" + rgb + "," + p.a + ")"; ctx.lineWidth = p.w;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - wind * (p.len / 14), p.y - p.len); ctx.stroke();
        }
        for (i = fx.splashes.length - 1; i >= 0; i--) {
          const s = fx.splashes[i];
          s.r += (s.max / 9) * dt; s.a -= .03 * dt;
          if (s.a <= 0 || s.r >= s.max) { fx.splashes.splice(i, 1); continue; }
          ctx.strokeStyle = "rgba(" + rgb + "," + (s.a * .8).toFixed(3) + ")"; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.ellipse(s.x, s.y, s.r, s.r * .34, 0, 0, Math.PI * 2); ctx.stroke();
        }
      } else if (fx.type === "ocean") {
        const rgb = d ? "56,189,248" : "14,165,183";
        const bands = [{ amp: 7, len: w / 2.6, sp: .45, base: h - 64, a: d ? .10 : .07 }, { amp: 10, len: w / 3.4, sp: .9, base: h - 44, a: d ? .15 : .11 }, { amp: 14, len: w / 4.6, sp: -.6, base: h - 22, a: d ? .20 : .14 }];
        for (i = 0; i < bands.length; i++) {
          const b = bands[i];
          ctx.fillStyle = "rgba(" + rgb + "," + b.a + ")";
          ctx.beginPath(); ctx.moveTo(0, h);
          for (x = 0; x <= w + 10; x += 10) ctx.lineTo(x, b.base + Math.sin((x / b.len) * 6.283 + t * b.sp * 4) * b.amp);
          ctx.lineTo(w, h); ctx.closePath(); ctx.fill();
        }
        for (i = 0; i < fx.parts.length; i++) {
          p = fx.parts[i];
          p.y -= p.sp * dt;
          if (p.y < h * .4) { p.y = h + rnd(2, 26); p.x = rnd(0, w); }
          ctx.fillStyle = "rgba(" + rgb + "," + p.a + ")";
          ctx.beginPath(); ctx.arc(p.x + Math.sin(t * 1.4 + p.ph) * 6, p.y, p.r, 0, Math.PI * 2); ctx.fill();
        }
      } else if (fx.type === "forest") {
        for (i = 0; i < fx.parts.length; i++) {
          p = fx.parts[i];
          if (p.fly) {
            p.x += (p.dx + Math.sin(t * .9 + p.ph) * .25) * dt;
            p.y += (p.dy + Math.cos(t * .7 + p.ph) * .2) * dt;
            if (p.x < -10) p.x = w + 10; if (p.x > w + 10) p.x = -10;
            if (p.y < 0) p.y = h * .8; if (p.y > h) p.y = h * .15;
            const glow = .2 + .5 * Math.abs(Math.sin(t * 1.8 + p.ph));
            ctx.save(); ctx.shadowColor = "rgba(252,211,77,.9)"; ctx.shadowBlur = 9;
            ctx.fillStyle = "rgba(252,221,120," + glow.toFixed(3) + ")";
            ctx.beginPath(); ctx.arc(p.x, p.y, 1.8, 0, Math.PI * 2); ctx.fill(); ctx.restore();
            continue;
          }
          p.y += p.fall * dt; p.x += Math.sin(t * p.swaySp + p.ph) * p.swayAmp * dt; p.rot += p.rotSp * dt;
          if (p.y > h + 16) { fx.parts[i] = leaf(false); continue; }
          ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot + Math.sin(t * p.swaySp + p.ph) * .5);
          ctx.fillStyle = "rgba(" + p.rgb[0] + "," + p.rgb[1] + "," + p.rgb[2] + "," + p.a + ")";
          ctx.beginPath(); ctx.ellipse(0, 0, p.size, p.size * .42, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
        }
      } else if (fx.type === "fire") {
        const flick = .10 + (d ? .06 : .02) + Math.sin(t * 9.3) * .018 + Math.sin(t * 23.7) * .012;
        const g = ctx.createLinearGradient(0, h - 150, 0, h);
        g.addColorStop(0, "rgba(255,122,61,0)");
        g.addColorStop(1, "rgba(255,122,61," + Math.max(flick, .04).toFixed(3) + ")");
        ctx.fillStyle = g; ctx.fillRect(0, h - 150, w, 150);
        for (i = 0; i < fx.parts.length; i++) {
          p = fx.parts[i];
          p.y -= p.rise * dt; p.x += Math.sin(t * 2.1 + p.ph) * .4 * dt;
          const life = Math.max(0, Math.min(1, p.y / h));
          if (p.y < h * .12 || life <= 0) { fx.parts[i] = ember(false); continue; }
          const ea = p.a * life;
          ctx.fillStyle = "rgba(255,122,61," + (ea * .35).toFixed(3) + ")";
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 2.4, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = (i % 3 === 0 ? "rgba(250,204,21," : "rgba(255,150,84,") + ea.toFixed(3) + ")";
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
        }
      } else if (fx.type === "night") {
        const rgb = d ? "255,252,238" : "92,74,168";
        for (i = 0; i < fx.parts.length; i++) {
          p = fx.parts[i];
          p.y += p.drift * dt;
          if (p.y > h + 4) { p.y = -4; p.x = rnd(0, w); }
          const tw = p.a * (.35 + .65 * Math.abs(Math.sin(t * p.tw + p.ph)));
          ctx.fillStyle = "rgba(" + rgb + "," + tw.toFixed(3) + ")";
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
          if (p.r > 1.5) {
            ctx.strokeStyle = "rgba(" + rgb + "," + (tw * .4).toFixed(3) + ")"; ctx.lineWidth = .9;
            ctx.beginPath();
            ctx.moveTo(p.x - p.r * 3.2, p.y); ctx.lineTo(p.x + p.r * 3.2, p.y);
            ctx.moveTo(p.x, p.y - p.r * 3.2); ctx.lineTo(p.x, p.y + p.r * 3.2);
            ctx.stroke();
          }
        }
        fx.nextShoot -= dt * .016;
        if (!fx.shoot && fx.nextShoot <= 0) { fx.shoot = { x: rnd(w * .1, w * .8), y: rnd(20, h * .45), vx: rnd(5.5, 9), vy: rnd(2, 3.6), life: 1 }; fx.nextShoot = rnd(4, 10); }
        if (fx.shoot) {
          const s = fx.shoot;
          s.x += s.vx * dt; s.y += s.vy * dt; s.life -= .015 * dt;
          if (s.life <= 0 || s.x > w + 60) fx.shoot = null;
          else {
            const g2 = ctx.createLinearGradient(s.x, s.y, s.x - s.vx * 12, s.y - s.vy * 12);
            g2.addColorStop(0, "rgba(" + rgb + "," + s.life.toFixed(3) + ")");
            g2.addColorStop(1, "rgba(" + rgb + ",0)");
            ctx.strokeStyle = g2; ctx.lineWidth = 2; ctx.lineCap = "round";
            ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(s.x - s.vx * 12, s.y - s.vy * 12); ctx.stroke();
          }
        }
      }
      fx.raf = requestAnimationFrame(tick);
    }

    fx.set = (type) => {
      fx.type = type && type !== "silence" && type !== "off" && !reduced() ? type : "off";
      if (fx.type === "off") {
        if (fx.raf) cancelAnimationFrame(fx.raf);
        fx.raf = 0; fx.last = 0;
        if (fx.ctx) fx.ctx.clearRect(0, 0, fx.w, fx.h);
        c.style.display = "none";
        return;
      }
      c.style.display = "";
      spawn(fx.type);
      if (!fx.raf) fx.raf = requestAnimationFrame(tick);
    };

    const onVis = () => {
      if (document.hidden) { if (fx.raf) cancelAnimationFrame(fx.raf); fx.raf = 0; fx.last = 0; }
      else if (fx.type !== "off" && !fx.raf) fx.raf = requestAnimationFrame(tick);
    };

    resize();
    addEventListener("resize", resize);
    document.addEventListener("visibilitychange", onVis);
    fx.set(type);
    return () => {
      removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVis);
      if (fx.raf) cancelAnimationFrame(fx.raf);
      c.remove(); fx.canvas = null;
    };
  }, []);

  React.useEffect(() => { if (st.current.set) st.current.set(type); }, [type]);
}

Object.assign(window, { useAmbience });
