/* Page bootstrap — Notas.
   Lifted from the design project's "MachReach Notas.html" inline App(); the
   ReactDOM.createRoot call is appended by build-app.mjs, not written here. */

const PAGE_ID = "notas";
const PAGE_TITLE = "Notas";
const PAGE_SUB = "Planilla de notas";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": false
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const plus = data.live ? !!data.plus : !!tweaks.plus;

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  return (
    <div className="app">
      <Sidebar active={PAGE_ID} plus={plus} />
      <div>
        <Topbar title={data.title || PAGE_TITLE} sub={data.sub || PAGE_SUB} streak={data.streak ?? "17"} xp={data.xp || "4.180"} coins={data.coins ?? "320"} avatar={data.avatar || "MR"} plus={plus} tweaks={tweaks} setTweak={setTweak} />
        <main className="page">
          <div className="col">
            <Planilla />
          </div>
        </main>
      </div>
      <TabBar active={PAGE_ID} />

      {!data.live && <TweaksPanel title="Tweaks">
        <PlanTweak plus={plus} setTweak={setTweak} />
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
