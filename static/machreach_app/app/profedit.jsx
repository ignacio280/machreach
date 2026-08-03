/* MachReach — Editar perfil: foto, banner, bandera e insignias equipables */
const IconCamera = (p) => <Icon {...p}><path d="M3.5 8.5h3.2l1.6-2.4h7.4l1.6 2.4h3.2v11H3.5v-11z" /><circle cx="12" cy="13.5" r="3.4" /></Icon>;
const PE_BANNERS = {
  default: { name: "Default", css: "linear-gradient(135deg,#475569,#1e293b)" },
  ocean: { name: "Ocean Wave", css: "linear-gradient(135deg,#06b6d4,#3b82f6)" },
  sunset: { name: "Sunset", css: "linear-gradient(135deg,#f97316,#ec4899)" },
  forest: { name: "Forest", css: "linear-gradient(135deg,#10b981,#065f46)" },
  lavender: { name: "Lavender Dream", css: "linear-gradient(135deg,#a78bfa,#7c3aed)" },
  gold: { name: "Gold Rush", css: "linear-gradient(135deg,#facc15,#b45309)" },
  galaxy: { name: "Galaxy", css: "linear-gradient(135deg,#1e1b4b,#7c3aed,#ec4899)" },
  candy: { name: "Candy Pop", css: "linear-gradient(135deg,#fbcfe8,#f9a8d4,#a5b4fc)" },
  obsidian: { name: "Obsidian", css: "linear-gradient(135deg,#0f172a,#1e293b,#334155)" },
  sapphire: { name: "Sapphire", css: "linear-gradient(135deg,#1e3a8a,#3b82f6,#93c5fd)" },
  tropical: { name: "Tropical Sunset", css: "linear-gradient(180deg,#0c4a6e 0%,#1e3a8a 30%,#7c3aed 55%,#ec4899 80%,#f59e0b 100%)" },
  lofi_dawn: { name: "Lofi Dawn", css: "linear-gradient(180deg,#fb7185 0%,#fda4af 30%,#fbcfe8 55%,#a78bfa 80%,#6366f1 100%)" },
  cherry: {
    name: "Cherry Blossom (PLUS)", anim: "bnr-anim-cherry",
    css: "radial-gradient(circle at 20% 30%, #fbcfe8 0%, transparent 30%), radial-gradient(circle at 80% 60%, #f9a8d4 0%, transparent 30%), radial-gradient(2px 3px at 15% 20%, #ec4899 50%, transparent 55%), radial-gradient(3px 2px at 75% 70%, #db2777 50%, transparent 55%), radial-gradient(2px 2px at 45% 40%, #f472b6 50%, transparent 55%), radial-gradient(3px 3px at 90% 25%, #be185d 50%, transparent 55%), linear-gradient(135deg,#fff1f2,#fbcfe8)",
  },
  plus_gold: { name: "24K Gold (PLUS)", css: "linear-gradient(135deg,#fde68a 0%,#fbbf24 25%,#b45309 50%,#fbbf24 75%,#fde68a 100%)" },
};

const PE_FLAGS = {
  none: { name: "Sin bandera", css: "transparent" },
  void: { name: "Void Walker", css: "linear-gradient(90deg, #000 0%, #6d28d9 60%, #c084fc 100%)" },
  void_blue: { name: "Void Walker — Tide", css: "linear-gradient(90deg, #000 0%, #1e3a8a 60%, #93c5fd 100%)" },
  racing: { name: "Racing Stripes", css: "repeating-linear-gradient(90deg, #ef4444 0 14px, #fafafa 14px 28px)" },
  checker_anim: { name: "Checker Flag (Anim)", anim: "flag-anim-checker", css: "repeating-conic-gradient(#0f172a 0% 25%, #f8fafc 25% 50%) 0 0 / 24px 24px" },
  stripes_3: { name: "Tricolor", css: "linear-gradient(90deg, #ef4444 0% 33%, #fafafa 33% 66%, #2563eb 66% 100%)" },
  stripes_5: { name: "Pentaband", css: "linear-gradient(90deg, #ef4444 0% 20%, #f59e0b 20% 40%, #facc15 40% 60%, #22c55e 60% 80%, #3b82f6 80% 100%)" },
  candy: { name: "Candy", css: "repeating-linear-gradient(90deg, #ec4899 0 18px, #fbcfe8 18px 36px)" },
  polar_lights: { name: "Polar Lights", anim: "flag-anim-shimmer", css: "linear-gradient(90deg, #052e16 0%, #16a34a 25%, #22d3ee 50%, #a855f7 75%, #052e16 100%)" },
  heartbeat: { name: "Heartbeat (PLUS)", anim: "flg-heartbeat", css: "linear-gradient(90deg, #450a0a 0%, #7f1d1d 25%, #ef4444 55%, #fecaca 85%, #450a0a 100%)" },
  flow_rainbow: { name: "Flow Rainbow (PLUS)", anim: "flag-anim-fast", css: "linear-gradient(90deg, #ef4444, #f97316, #facc15, #22c55e, #06b6d4, #6366f1, #a855f7, #ef4444)" },
};

const PE_BADGES = [
  { key: "streak_7", emoji: "🔥", name: "Racha de 7" },
  { key: "quiz_10", emoji: "🎯", name: "10 quizzes" },
  { key: "night_owl", emoji: "🌙", name: "Sesión nocturna" },
  { key: "courses_6", emoji: "📚", name: "6 cursos" },
  { key: "xp_5000", emoji: "⚡", name: "5.000 XP" },
  { key: "friends_10", emoji: "🤝", name: "10 amigos" },
];

const PE_USER = { name: "Sofía Pérez", email: "sofia.perez@uc.cl", rank: "#12", xp: "4.180", coins: 320, freezes: 5, streak: 17 };

/* The mock keys its catalogs by name; the server sends arrays of unlocked
   cosmetics. Both collapse to the same shape so the markup stays single-branch. */
const peList = (dict) => Object.entries(dict).map(([key, cfg]) => ({ key, ...cfg }));

function PeCard({ eq, anim, onClick, children, className = "" }) {
  return (
    <button className={"pe-cos" + (eq ? " eq" : "") + (className ? " " + className : "")} onClick={onClick} type="button">
      {children}
      {anim ? <span className="pe-anim">ANIM</span> : null}
      {eq ? <span className="pe-pill">EQUIPADO</span> : null}
    </button>
  );
}

const PE_AVATARS = ["#FFD3A8", "#FF8AA5", "#8DACFF", "#B29BFF", "#5DE3B0", "#9CD9F0", "#FFC857"];

function ProfileEditPage({ plus, data = {}, csrf = "", profile = {} }) {
  const live = !!data.live;
  const user = live ? data : PE_USER;
  const initials = (user.name || "MR").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  const [pic, setPic] = React.useState(live ? data.picture_url || null : null);
  const [picBusy, setPicBusy] = React.useState(false);
  const [picNote, setPicNote] = React.useState("");
  const fileRef = React.useRef(null);
  const onPick = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    if (!live) {
      const r = new FileReader();
      r.onload = () => setPic(r.result);
      r.readAsDataURL(f);
      return;
    }
    setPicBusy(true); setPicNote("");
    try {
      const body = new FormData();
      body.append("photo", f);
      const response = await fetch("/api/student/profile/picture", { method: "POST", headers: { "X-CSRFToken": csrf }, body });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "No se pudo subir la foto.");
      setPic(result.url);
    } catch (err) {
      setPicNote(err.message);
    } finally {
      setPicBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const removePic = async () => {
    if (live) {
      setPicBusy(true);
      try {
        const response = await fetch("/api/student/profile/picture", { method: "DELETE", headers: { "X-CSRFToken": csrf } });
        if (!response.ok) throw new Error("No se pudo quitar la foto.");
      } catch (err) {
        setPicNote(err.message);
        setPicBusy(false);
        return;
      }
      setPicBusy(false);
    }
    setPic(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const banners = live ? (data.banners || []) : peList(PE_BANNERS);
  const flags = live ? (data.flags || []) : peList(PE_FLAGS);
  const badges = live ? (data.badges || []) : PE_BADGES;

  const [banner, setBanner] = React.useState(live ? data.selected_banner || "default" : "galaxy");
  const [flag, setFlag] = React.useState(live ? data.selected_flag || "none" : "void");
  const [side, setSide] = React.useState("right");
  const [slots, setSlots] = React.useState(live
    ? { left: (data.equipped || {}).left || "", right: (data.equipped || {}).right || "" }
    : { left: "", right: "streak_7" });

  const bcfg = banners.find((b) => b.key === banner) || banners[0] || { css: "" };
  const noneEq = !slots.left && !slots.right;

  // Equipping is optimistic: the card lights up immediately and rolls back if
  // the server rejects it (e.g. a cosmetic that is no longer unlocked).
  const post = async (url, body, rollback) => {
    if (!live) return;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "No se pudo aplicar.");
    } catch (err) {
      rollback();
      alert(err.message);
    }
  };
  const [avatarColor, setAvatarColor] = React.useState(profile.avatar_color || PE_AVATARS[0]);
  const pickAvatarColor = async (colour) => {
    const prev = avatarColor;
    setAvatarColor(colour);
    if (!live) return;
    try {
      // The identity fields ride along unchanged — this endpoint saves the
      // whole profile row, and the rest of it is edited in Ajustes.
      const response = await fetch("/api/student/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ name: profile.name || user.name, bio: profile.bio || "", avatar_color: colour }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "No se pudo guardar el color.");
    } catch (err) {
      setAvatarColor(prev);
      alert(err.message);
    }
  };
  const pickBanner = (k) => { const prev = banner; setBanner(k); post("/api/student/wallet/set-banner", { banner_key: k }, () => setBanner(prev)); };
  const pickFlag = (k) => { const prev = flag; setFlag(k); post("/api/student/wallet/set-flag", { flag_key: k }, () => setFlag(prev)); };
  const equipBadge = (k) => {
    const prev = slots;
    const next = k === "" ? { left: "", right: "" } : { ...slots, [side]: k };
    setSlots(next);
    post("/api/student/wallet/set-badge", { badge_key: k, side }, () => setSlots(prev));
  };

  const badgeOf = (key) => badges.find((b) => b.key === key);
  const fcfg = flags.find((f) => f.key === flag) || { css: "transparent" };
  const joined = profile.created_at
    ? new Date(profile.created_at + "T12:00:00").toLocaleDateString("es-CL", { month: "long", year: "numeric" })
    : "MachReach";

  return (
    <div className="pe-wrap in">
      <a className="pe-back" href={live ? "/student/profile" : "MachReach Perfil.html"}><IconArrow size={15} style={{ transform: "rotate(180deg)" }} /> Volver al perfil</a>

      {/* Live preview: the same hero the public profile renders, so every
          change below is seen exactly as other students will see it. */}
      <section className="pf-hero rv" style={{ "--d": "0ms" }}>
        <div className={"pf-cover bnr-anim-host has-banner " + (bcfg.anim || "")} style={{ background: bcfg.css }}>
          {flag !== "none" && <span className={"pe-flagband " + (fcfg.anim || "")} style={{ background: fcfg.css }} />}
        </div>
        <div className="pf-id">
          <div className="pf-face" style={{ background: avatarColor }}>
            {pic ? <img className="pf-facepic" src={pic} alt="" /> : initials}
            <span className="lvl">{profile.level_number || 1}</span>
            <button type="button" className="pf-facecam" disabled={picBusy}
              onClick={() => fileRef.current.click()}
              aria-label={pic ? "Cambiar foto de perfil" : "Subir foto de perfil"}>
              <IconCamera size={15} />
            </button>
          </div>
          <div className="pf-meta">
            <div className="pf-name">
              <h1>
                {slots.left ? <span className="pe-slotbadge">{badgeOf(slots.left)?.emoji || "🏅"}</span> : null}
                {user.name}
                {slots.right ? <span className="pe-slotbadge">{badgeOf(slots.right)?.emoji || "🏅"}</span> : null}
              </h1>
              {plus && <PlusBadge />}
            </div>
            <div className="pf-handle">{profile.handle || user.email} · Se unió en {joined}</div>
            <div className="pf-tags">
              <span className="pf-tag"><IconBook size={14} /> {profile.major || "Carrera sin configurar"}</span>
              <span className="pf-tag">🎓 {profile.semester || "I"} semestre</span>
              <span className="pf-tag"><IconPeople size={14} /> {profile.friend_count || 0} amigos</span>
            </div>
          </div>
          <div className="pf-idacts">
            {pic && <button className="btn btn-ghost btn-sm" type="button" disabled={picBusy} onClick={removePic}>Quitar foto</button>}
            <a className="btn btn-primary btn-sm" href={live ? "/student/profile" : "MachReach Perfil.html"}>Ver mi perfil</a>
          </div>
        </div>
        <div className="pe-picnote">{picNote || (picBusy ? "Subiendo…" : pic ? "Foto guardada." : "Sin foto: mostramos tus iniciales. Toca la cámara para subir una (PNG, JPG o WebP, máx. 2 MB).")}</div>
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={onPick} style={{ display: "none" }} />
      </section>

      <section className="pe-card rv" style={{ "--d": "60ms" }}>
        <h2>🎨 Color del avatar</h2>
        <p>Se usa como fondo de tus iniciales cuando no tienes foto.</p>
        <div className="pf-avpick">
          {PE_AVATARS.map((c) => (
            <button key={c} type="button" className={"pf-av" + (c === avatarColor ? " on" : "")} style={{ background: c }}
              onClick={() => pickAvatarColor(c)} aria-label={"Color " + c} />
          ))}
        </div>
      </section>

      <section className="pe-card rv" style={{ "--d": "110ms" }}>
        <h2>🎨 Profile banner</h2>
        <p>Click any banner you've unlocked to equip it. <a href={live ? "/student/shop" : "MachReach Tienda.html"}>Visit the Shop</a> to unlock more.</p>
        <div className="pe-grid">
          {banners.map((cfg) => (
            <PeCard key={cfg.key} eq={cfg.key === banner} anim={cfg.anim} onClick={() => pickBanner(cfg.key)}>
              <span className={"prev bnr-anim-host " + (cfg.anim || "")} style={{ background: cfg.css }} />
              <span className="nm">{cfg.name}</span>
            </PeCard>
          ))}
        </div>
      </section>

      <section className="pe-card rv" style={{ "--d": "140ms" }}>
        <h2>🚩 Leaderboard flag</h2>
        <p>Click any flag you've unlocked to equip it. Your flag fades in behind your row on the leaderboard.</p>
        <div className="pe-grid">
          {flags.map((cfg) => (
            <PeCard key={cfg.key} eq={cfg.key === flag} onClick={() => pickFlag(cfg.key)}>
              {cfg.key === "none"
                ? <span className="prevflag" style={{ background: "transparent" }} />
                : <span className={"prevflag " + (cfg.anim || "")} style={{ background: cfg.css }} />}
              <span className="nm">{cfg.name}</span>
            </PeCard>
          ))}
        </div>
      </section>

      <section className="pe-card rv" style={{ "--d": "200ms" }}>
        <h2>🏅 Leaderboard badges</h2>
        <p>You can equip <b>two</b> badges — one on each side of your name. Pick which slot to fill, then click a badge. The same badge can fill both sides.</p>
        <div className="pe-sides">
          <button type="button" className={"pe-side" + (side === "left" ? " active" : "")} onClick={() => setSide("left")}>◀ Left slot</button>
          <button type="button" className={"pe-side" + (side === "right" ? " active" : "")} onClick={() => setSide("right")}>Right slot ▶</button>
        </div>
        <div className="pe-grid">
          <PeCard className="badge none" eq={noneEq} onClick={() => equipBadge("")}>
            <span className="em">🚫</span><span className="nm">None</span>
          </PeCard>
          {badges.map((b) => {
            const isL = b.key === slots.left, isR = b.key === slots.right;
            return (
              <button key={b.key} type="button" className={"pe-cos badge" + (isL || isR ? " eq" : "")} onClick={() => equipBadge(b.key)}>
                <span className="em">{b.emoji}</span>
                <span className="nm">{b.name}</span>
                {(isL || isR) && (
                  <span className="pe-slots">{isL && <span className="l">L</span>}{isR && <span className="r">R</span>}</span>
                )}
              </button>
            );
          })}
        </div>
        {live && !badges.length && <p className="pe-empty">Aún no has desbloqueado insignias.</p>}
      </section>
    </div>
  );
}

Object.assign(window, { ProfileEditPage, PeCard, IconCamera, PE_BANNERS, PE_FLAGS, PE_BADGES });
