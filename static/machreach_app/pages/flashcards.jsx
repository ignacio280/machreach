/* Page bootstrap — Flashcards (dentro del deck).
   Lifted from the design project's "MachReach Flashcards.html" inline App();
   the ReactDOM.createRoot call is appended by build-app.mjs, not written here. */

const PAGE_ID = "tools";
const PAGE_TITLE = "Estudiando deck";
const PAGE_SUB = "Herramientas · Flashcards";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": true,
  "view": "study"
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
          <FlashcardsPage key={data.live ? "live" : tweaks.view + tweaks.plus}
            view={tweaks.view} plus={plus} data={data} />
        </main>
      </div>
      <TabBar active={PAGE_ID} />

      {!data.live && <TweaksPanel title="Tweaks">
        <TweakSection label="Vista">
          <TweakSelect label="Momento" value={tweaks.view} onChange={(v) => setTweak("view", v)}
            options={[{ value: "study", label: "Estudiando" }, { value: "done", label: "Fin de la vuelta" }, { value: "edit", label: "Editando tarjetas" }]} />
        </TweakSection>
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
