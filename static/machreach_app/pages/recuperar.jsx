/* Page bootstrap — Recuperar contraseña. */

const PAGE_TITLE = "Recupera tu contraseña";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "state": "form"
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  const state = data.live ? (data.state || "form") : tweaks.state;

  return (
    <div className="pub-shift">
      <PubTop tag="Recuperar contraseña" tweaks={tweaks} setTweak={setTweak}
        action={<a className="pub-link" href="/login">Iniciar sesión</a>} />
      <ForgotPasswordPage state={state} email={data.email || ""} token={data.token || ""}
        live={data.live ? data : null} />

      {!data.live && <TweaksPanel title="Tweaks">
        <TweakSection label="Paso del flujo">
          <TweakSelect label="Vista" value={tweaks.state} onChange={(v) => setTweak("state", v)}
            options={[{ value: "form", label: "1 · Pedir enlace" }, { value: "sent", label: "2 · Enlace enviado" }, { value: "reset", label: "3 · Nueva contraseña" }, { value: "ok", label: "4 · Actualizada" }, { value: "expired", label: "Enlace caducado" }]} />
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
