/* Page bootstrap — Configuración inicial. */

const PAGE_TITLE = "Configuración inicial";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "step": "0",
  "state": "results"
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  const step = data.live ? 0 : (parseInt(tweaks.step, 10) || 0);

  return (
    <div className="pub-shift">
      <PubTop tag={PAGE_TITLE} tweaks={tweaks} setTweak={setTweak}
        action={data.live
          ? <form method="post" action="/logout" style={{ margin: 0 }}>
              <input type="hidden" name="csrf_token" value={data.csrf || ""} />
              <button type="submit" className="pub-link" style={{ background: "none", border: 0, cursor: "pointer" }}>Cerrar sesión</button>
            </form>
          : <a className="pub-link" href="MachReach Cuenta.html">Cerrar sesión</a>} />
      {/* The key only exists so the design preview can jump between steps; on
          the live page it would throw away the student's progress. */}
      <SetupPage key={data.live ? "live" : tweaks.step + tweaks.state} initialStep={step} state={tweaks.state}
        live={!!data.live} csrf={data.csrf || ""} />

      {!data.live && <TweaksPanel title="Tweaks">
        <TweakSection label="Vista">
          <TweakSelect label="Paso" value={tweaks.step} onChange={(v) => setTweak("step", v)}
            options={[{ value: "0", label: "1 · País" }, { value: "1", label: "2 · Universidad" }, { value: "2", label: "3 · Carrera" }, { value: "3", label: "Listo" }]} />
          <TweakRadio label="Resultados" value={tweaks.state} onChange={(v) => setTweak("state", v)}
            options={[{ value: "results", label: "Con datos" }, { value: "empty", label: "Sin resultados" }]} />
        </TweakSection>
        <TweakSection label="Tema">
          <TweakRadio label="Modo" value={tweaks.theme} onChange={(v) => setTweak("theme", v)}
            options={[{ value: "light", label: "Claro" }, { value: "dark", label: "Oscuro" }]} />
          <TweakSelect label="Acento" value={tweaks.accent} onChange={(v) => setTweak("accent", v)}
            options={[{ value: "coral", label: "Coral (marca)" }, { value: "lime", label: "Lima" }, { value: "violet", label: "Violeta" }, { value: "sky", label: "Cielo" }]} />
        </TweakSection>
      </TweaksPanel>}
    </div>
  );
}
