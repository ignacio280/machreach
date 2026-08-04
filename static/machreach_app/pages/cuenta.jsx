/* Page bootstrap — Cuenta (auth).
   Lifted from the design project's "MachReach Cuenta.html" inline App(); the
   ReactDOM.createRoot call is appended by build-app.mjs, not written here.
   Unlike the other pages this one has no Sidebar/Topbar — it is the signed-out
   shell, and it is a static mock: nothing here authenticates anybody. */

const PAGE_ID = "cuenta";
const PAGE_TITLE = "Crea tu cuenta";
const PAGE_SUB = "Registro e inicio de sesión";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "screen": "login"
}/*EDITMODE-END*/;

function App() {
  const live = window.__MACHREACH_APP__ || null;
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const screen = live?.live ? (live.mode || "login") : tweaks.screen;
  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  return (
    <div className="pub-shift">
      <PubTop tag={screen === "login" ? "Iniciar sesión" : "Crear cuenta"} tweaks={tweaks} setTweak={setTweak}
        action={<a className="pub-link" href="/">Volver al inicio</a>} />
      <AuthPage key={screen} initial={screen || "signup"} live={live?.live ? live : null} />
      {!live?.live && <TweaksPanel title="Tweaks">
        <TweakSection label="Pantalla">
          <TweakRadio label="Vista" value={tweaks.screen} onChange={(v) => setTweak("screen", v)}
            options={[{ value: "signup", label: "Crear cuenta" }, { value: "login", label: "Iniciar sesión" }]} />
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
