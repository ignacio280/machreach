/* Page bootstrap — Verificar correo. */

const PAGE_TITLE = "Verifica tu correo";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "state": "created"
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  const state = data.live ? (data.state || "blocked") : tweaks.state;

  return (
    <div className="pub-shift">
      <PubTop tag="Verificar correo" tweaks={tweaks} setTweak={setTweak}
        action={<a className="pub-link" href="/login">Iniciar sesión</a>} />
      <VerifyPendingPage key={state} state={state}
        email={data.live ? (data.email || "") : "ignacio@machreach.com"}
        live={data.live ? data : null} />

      {!data.live && <TweaksPanel title="Tweaks">
        <TweakSection label="Estado">
          <TweakSelect label="Cómo llegó aquí" value={tweaks.state} onChange={(v) => setTweak("state", v)}
            options={[{ value: "created", label: "Acaba de registrarse" }, { value: "blocked", label: "Login sin verificar" }, { value: "sent", label: "Reenviado" }, { value: "delayed", label: "Envío retrasado" }]} />
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
