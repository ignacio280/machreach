/* Tienda */
const COINS = 320, XP_TOTAL = 4180, FREEZES = 2, FREE_CAP = 3, PLUS_CAP = 5, FRZ_PRICE = 10, FRZ_BUNDLE_QTY = 3, FRZ_BUNDLE_PRICE = 25;

const PACKS = [
  { k: "small", n: "Pocket Pack", c: 250, b: 0, clp: 990, ic: "🪙", tag: "" },
  { k: "medium", n: "Stash Pack", c: 800, b: 50, clp: 2990, ic: "💰", tag: "+6%" },
  { k: "large", n: "Vault Pack", c: 2000, b: 300, clp: 6990, ic: "🏦", tag: "+15%" },
  { k: "mega", n: "Treasury Pack", c: 5500, b: 1000, clp: 14990, ic: "👑", tag: "+18%", best: true },
  { k: "ultra", n: "Whale Pack", c: 15000, b: 4000, clp: 34990, ic: "🐋", tag: "+27%" },
];

const BANNERS = [
  { n: "Peach Fizz", p: 40, xp: 50, css: "linear-gradient(135deg,#FFD3A5,#FD6585)" },
  { n: "Lagoon", p: 40, xp: 50, css: "linear-gradient(135deg,#43C6AC,#191654)" },
  { n: "Ocean Wave", p: 50, xp: 100, css: "linear-gradient(135deg,#2E3192,#1BFFFF)" },
  { n: "Sunset", p: 50, xp: 100, css: "linear-gradient(135deg,#FF512F,#F09819)" },
  { n: "Forest", p: 75, xp: 250, css: "linear-gradient(135deg,#134E5E,#71B280)" },
  { n: "Lavender Dream", p: 100, xp: 500, css: "linear-gradient(135deg,#B993D6,#8CA6DB)" },
  { n: "Gold Rush", p: 200, xp: 1000, css: "linear-gradient(135deg,#F7971E,#FFD200)" },
  { n: "Galaxy", p: 300, xp: 2500, css: "linear-gradient(135deg,#41295A,#2F0743)" },
  { n: "Cherry Blossom", p: 280, xp: 0, plus: true, css: "linear-gradient(135deg,#FFAFBD,#FFC3A0)" },
  { n: "Matrix Rain", p: 800, xp: 0, plus: true, css: "linear-gradient(135deg,#0F2027,#0B9A50)" },
  { n: "Champion (Elite)", p: 500, xp: 5000, css: "linear-gradient(135deg,#EFBC42,#8E2DE2)" },
  { n: "Neon Rift", p: 450, xp: 1500, css: "linear-gradient(135deg,#FC466B,#3F5EFB)" },
];

const FLAGS = [
  { n: "Candy", p: 120, xp: 400, css: "repeating-linear-gradient(90deg,#FF6A9C 0 14px,#FFD166 14px 28px)" },
  { n: "Tricolor", p: 180, xp: 800, css: "linear-gradient(90deg,#FF6A2B 0 33%,#F4B740 33% 66%,#6FB03A 66%)" },
  { n: "Racing Stripes", p: 200, xp: 1000, css: "repeating-linear-gradient(115deg,#1A1A1F 0 18px,#E8505B 18px 30px)" },
  { n: "Checker Flag", p: 250, xp: 1500, css: "repeating-conic-gradient(#fff 0 25%,#1A1A1F 0 50%) 0 0/22px 22px" },
  { n: "Vortex Spiral", p: 300, xp: 1100, css: "conic-gradient(from 0deg,#6E4CD8,#2FA8C6,#6E4CD8)" },
  { n: "Polar Lights", p: 320, xp: 1300, css: "linear-gradient(100deg,#00C9A7,#845EC2,#2FA8C6)" },
  { n: "Void Walker", p: 350, xp: 2500, css: "radial-gradient(circle at 20% 50%,#7A4FEA,#150B2E)" },
  { n: "Black Hole", p: 480, xp: 2200, css: "radial-gradient(circle at 30% 50%,#000,#3A2450)" },
  { n: "Heartbeat", p: 260, xp: 0, plus: true, css: "linear-gradient(90deg,#E8505B,#FF9257)" },
  { n: "Flow Lava", p: 600, xp: 0, plus: true, css: "linear-gradient(100deg,#DC4B14,#F4B740,#DC4B14)" },
  { n: "Holo Sweep", p: 1200, xp: 0, plus: true, css: "linear-gradient(100deg,#A0E9FF,#FFC3F0,#C8FFD4,#A0E9FF)" },
  { n: "Prism Bar", p: 1000, xp: 0, plus: true, css: "linear-gradient(90deg,#FF6A2B,#F4B740,#6FB03A,#2FA8C6,#6E4CD8)" },
];

const OWNED = ["Ocean Wave", "Peach Fizz", "Tricolor"];

async function shopPost(path, payload = {}) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify(payload) });
  const body = await response.json();
  if (!response.ok || body.ok === false) { alert(body.error || "No se pudo completar la operación."); return null; }
  if (body.checkout_url) location.href = body.checkout_url;
  return body;
}

function CosCard({ c, plus, kind, data = {} }) {
  const owned = (data.owned || OWNED).includes(c.n) || (c.k && (data.owned_keys || []).includes(c.k));
  const plusLocked = c.plus && !plus;
  const xpOk = Number(data.xp ?? XP_TOTAL) >= c.xp;
  const canAfford = Number(data.coins ?? COINS) >= c.p;
  let tag, btn;
  if (owned) { tag = ["ok", "Comprado"]; btn = null; }
  else if (plusLocked) { tag = ["lock", "Requiere suscripción PLUS"]; btn = ["Solo PLUS", true]; }
  else if (!xpOk) { tag = ["dim", "Alcanza " + c.xp.toLocaleString("es-CL") + " XP para desbloquear"]; btn = null; }
  else if (!canAfford) { tag = ["no", "Necesitas " + c.p + " monedas"]; btn = ["Comprar (" + c.p + " 🪙)", true]; }
  else { tag = ["dim", c.xp ? c.xp + " XP desbloqueado" : "Sin requisito de XP"]; btn = ["Comprar (" + c.p + " 🪙)", false]; }
  return (
    <div className="cos">
      <div className={"cos-prev bnr-anim-host" + (kind === "flag" ? " flag" : "") + (kind !== "flag" && c.anim ? " " + c.anim : "")}
        style={kind === "flag" ? null : { background: c.css }}>
        {kind === "flag" && <React.Fragment><i className={c.anim || ""} style={{ background: c.css }} /><span className="pv">VISTA PREVIA</span></React.Fragment>}
      </div>
      <div className="cos-b">
        <div className="cos-n">{c.n}{c.plus && <PlusBadge small />}</div>
        <div className={"cos-t " + tag[0]}>{tag[1]}</div>
        {btn && <button className={"btn btn-sm " + (btn[1] ? "btn-ghost" : "btn-primary")} disabled={btn[1]} onClick={async () => { const body = await shopPost("/api/student/wallet/buy-" + kind, { [kind + "_key"]: c.k }); if (body) location.reload(); }}>{btn[0]}</button>}
      </div>
    </div>
  );
}

function ShopHead({ data = {} }) {
  const coins = Number(data.coins ?? COINS), freezes = Number(data.freezes ?? FREEZES), xp = Number(data.xp ?? XP_TOTAL);
  return (
    <section className="sh-head">
      <div>
        <div className="pl-kicker">Tienda</div>
        <h1>Gasta lo que te ganaste.</h1>
        <p>Las monedas salen de tus posiciones en el ranking y de las misiones diarias. Úsalas en congeladores de racha o en cosméticos para tu perfil.</p>
      </div>
      <div className="wallet">
        <div className="wal coins"><div className="n num">🪙 {coins}</div><div className="l">Monedas</div></div>
        <div className="wal frz"><div className="n num">🧊 {freezes}</div><div className="l">Congeladores</div></div>
        <div className="wal"><div className="n num">⚡ {xp.toLocaleString("es-CL")}</div><div className="l">XP total</div></div>
      </div>
    </section>
  );
}

function PlanSection({ plus, data = {} }) {
  const freeCap = Number(data.free_cap ?? FREE_CAP), plusCap = Number(data.plus_cap ?? PLUS_CAP);
  const free = ["Sesiones de enfoque ilimitadas", "Cursos desde Canvas con la extensión", "Ranking, ligas y premios", "Hasta " + freeCap + " congeladores de racha"];
  const plusFeat = ["Todo lo del plan gratis", "Plan semanal inteligente", "Analíticas completas de estudio", "Benchmark anónimo por ramo", "Explicaciones en cada quiz", "Hasta " + plusCap + " congeladores de racha"];
  return (
    <div className="sh-stage">
      <div className="plans">
        <div className="plan">
          <div className="plan-label">{plus ? "Plan anterior" : "Plan actual · Gratis"}</div>
          <h3>Gratis</h3>
          <div className="plan-price">$0<small> / mes</small></div>
          <div className="plan-sub">Para siempre, sin tarjeta.</div>
          <ul>{free.map((f) => <li key={f}><IconCheck size={15} color="var(--good)" />{f}</li>)}</ul>
          {plus ? <span className="plan-current">Plan anterior</span> : <span className="plan-current"><IconCheck size={15} /> Tu plan actual</span>}
        </div>
        <div className="plan plus">
          <div className="plan-label">Recomendado</div>
          <h3>MachReach Plus</h3>
          <div className="plan-price">$4.990<small> / mes</small></div>
          <div className="plan-sub">Cancela cuando quieras. 1 amigo invitado = 7 días gratis.</div>
          <div className="plan-terms">CLP · cobro mensual · IVA calculado y mostrado en checkout · renovación automática.</div>
          <ul>{plusFeat.map((f) => <li key={f}><IconCheck size={15} color="var(--plum)" />{f}</li>)}</ul>
          {plus
            ? <span className="plan-current"><IconCheck size={15} /> {data.plus_until ? `Activo hasta ${data.plus_until}` : "Plus activo"}</span>
            : <button className="btn btn-primary btn-lg" disabled={data.billing_ready === false} onClick={() => shopPost("/api/student/subscription/change", { tier: "plus" })}>{data.billing_ready === false ? "Pagos no disponibles" : "Desbloquear Plus"}</button>}
        </div>
      </div>
      <div className="restore">
        <span className="ico-badge" style={{ background: "var(--surface)" }}><IconShield size={16} /></span>
        <div>
          <div className="t">¿Ya pagaste Plus?</div>
          <div className="s">Restaura y reconcilia tu estado con el proveedor de pagos.</div>
        </div>
        <button className="btn btn-ghost btn-sm" disabled={data.billing_ready === false} onClick={async () => { const body = await shopPost("/api/student/subscription/reconcile"); if (body) { alert(body.message || "Compra restaurada."); location.reload(); } }}>Restaurar compra</button>
      </div>
    </div>
  );
}

function CoinsSection({ plus, data = {} }) {
  const coins = Number(data.coins ?? COINS), freezes = Number(data.freezes ?? FREEZES);
  const cap = plus ? Number(data.plus_cap ?? PLUS_CAP) : Number(data.free_cap ?? FREE_CAP);
  const singleOff = coins < FRZ_PRICE || freezes >= cap;
  const bundleOff = coins < FRZ_BUNDLE_PRICE || freezes + FRZ_BUNDLE_QTY > cap;
  const save = FRZ_PRICE * FRZ_BUNDLE_QTY - FRZ_BUNDLE_PRICE;
  return (
    <div className="sh-stage">
      <div className="packs">
        {PACKS.map((p, i) => (
          <div className={"pack" + (p.best ? " best" : "")} key={p.k} style={{ "--pt": (i % 2 ? 1 : -1) * 1.2 + "deg" }}>
            {p.tag && <span className="pack-tag">{p.tag}</span>}
            {p.best && <span className="pack-best">Mejor valor</span>}
            <div className="pack-ic">{p.ic}</div>
            <div className="pack-n">{p.n}</div>
            <div className="pack-c num">{(p.c + p.b).toLocaleString("es-CL")} 🪙</div>
            <div className="pack-b">{p.b ? "+" + p.b.toLocaleString("es-CL") + " de regalo" : ""}</div>
            <div className="pack-terms">CLP · pago único · IVA mostrado en checkout</div>
            <button className={"btn btn-sm " + (p.best ? "btn-primary" : "btn-ghost")} disabled={data.billing_ready === false} onClick={() => shopPost("/api/student/wallet/buy-coin-pack", { pack_key: p.k })}>{data.billing_ready === false ? "No disponible" : "$" + p.clp.toLocaleString("es-CL")}</button>
          </div>
        ))}
      </div>
      <section className="pnl" style={{ marginTop: 18 }}>
        <div className="pnl-h">
          <span className="ico-badge" style={{ background: "#DCEFF6" }}>🧊</span>
          <h3>Congeladores de racha</h3>
          <span className="mono">{freezes}/{cap} guardados</span>
        </div>
        <p className="an-sub">Se usan automáticamente si te saltas un día. Gratis puede guardar hasta {FREE_CAP}; Plus hasta {PLUS_CAP}.</p>
        <div className="frz-card">
          <div className="frz-opt">
            <span className="ic">🧊</span>
            <div><div className="t">1 congelador</div><div className="s">{FRZ_PRICE} monedas</div></div>
            <button className={"btn btn-sm " + (singleOff ? "btn-ghost" : "btn-primary")} disabled={singleOff} onClick={async () => { const body = await shopPost("/api/student/wallet/buy-freeze"); if (body) location.reload(); }}>{freezes >= cap ? "Tope alcanzado" : "Comprar"}</button>
          </div>
          <div className="frz-opt">
            <span className="ic">🧊🧊🧊</span>
            <div><div className="t">Pack de {FRZ_BUNDLE_QTY}</div><div className="s">{FRZ_BUNDLE_PRICE} monedas <b>· ahorras {save}</b></div></div>
            <button className={"btn btn-sm " + (bundleOff ? "btn-ghost" : "btn-primary")} disabled={bundleOff} onClick={async () => { const body = await shopPost("/api/student/wallet/buy-freeze-bundle"); if (body) location.reload(); }}>{freezes + FRZ_BUNDLE_QTY > cap ? "Supera el tope" : "Comprar"}</button>
          </div>
        </div>
      </section>
    </div>
  );
}

function CosmeticsSection({ plus, data = {} }) {
  const banners = data.banners || BANNERS;
  const flags = data.flags || FLAGS;
  return (
    <div className="sh-stage">
      <div className="cos-grid">{banners.map((c) => <CosCard key={c.k || c.n} c={c} plus={plus} kind="banner" data={{ ...data, owned_keys: data.unlocked_banners }} />)}</div>
      <div className="cos-sub">
        <h4>Banderas del ranking</h4>
        <span className="mono">se muestran detrás de tu nombre en la tabla</span>
      </div>
      <div className="cos-grid">{flags.map((c) => <CosCard key={c.k || c.n} c={c} plus={plus} kind="flag" data={{ ...data, owned_keys: data.unlocked_flags }} />)}</div>
    </div>
  );
}

const SH_TABS = [{ id: "plan", ic: "✨", l: "Plan" }, { id: "coins", ic: "🪙", l: "Monedas" }, { id: "cosmetics", ic: "🎨", l: "Cosméticos" }];

function ShopBoard({ plus, data = {} }) {
  const [tab, setTab] = React.useState("plan");
  return (
    <React.Fragment>
      <div className="sh-tabs">
        {SH_TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}><span>{t.ic}</span>{t.l}</button>
        ))}
      </div>
      <div key={tab}>
        {tab === "plan" && <PlanSection plus={plus} data={data} />}
        {tab === "coins" && <CoinsSection plus={plus} data={data} />}
        {tab === "cosmetics" && <CosmeticsSection plus={plus} data={data} />}
      </div>
    </React.Fragment>
  );
}

Object.assign(window, { ShopHead, ShopBoard });
