/* Plus gating — shared across all app pages */
function PlusBadge({ small }) {
  return <span className={"plus-badge" + (small ? " sm" : "")}>PLUS</span>;
}

/* Wraps a real feature. When locked: the feature stays visible but frosted, with an unlock card on top. */
function PlusGate({ locked, title, copy, cta = "Desbloquear Plus", children, minHeight }) {
  if (!locked) return children;
  return (
    <div className="gate" style={minHeight ? { minHeight } : null}>
      <div className="gate-under" aria-hidden="true">{children}</div>
      <div className="gate-over">
        <div className="gate-card">
          <span className="gate-lock"><IconLock size={20} /></span>
          <div className="gate-eye">Función Plus</div>
          <h4>{title}</h4>
          <p>{copy}</p>
          <a href="#" className="btn btn-primary btn-sm">{cta}</a>
        </div>
      </div>
    </div>
  );
}

/* Inline "Solo Plus" pill used where a link would be */
function PlusLink({ locked, href = "#", children }) {
  if (locked) return <a href="#" className="lnk locked"><IconLock size={13} /> Solo Plus</a>;
  return <a href={href} className="lnk">{children} <IconArrow size={14} /></a>;
}

function PlanTweak({ plus, setTweak }) {
  return (
    <TweakSection label="Cuenta">
      <TweakRadio label="Plan del usuario" value={plus ? "plus" : "free"} onChange={(v) => setTweak("plus", v === "plus")}
        options={[{ value: "free", label: "Gratis" }, { value: "plus", label: "Plus" }]} />
    </TweakSection>
  );
}

Object.assign(window, { PlusBadge, PlusGate, PlusLink, PlanTweak });
