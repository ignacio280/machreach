/* Page bootstrap — Reviews.
   Lifted from the design project's "MachReach Reviews.html" inline App(); the
   ReactDOM.createRoot call is appended by build-app.mjs, not written here. */

const PAGE_ID = "reviews";
const PAGE_TITLE = "Reviews";
const PAGE_SUB = "Reseñas anónimas de ramos";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": false
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const plus = data.live ? !!data.plus : !!tweaks.plus;
  const [writing, setWriting] = React.useState(false);

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
            <ReviewsHead onWrite={() => setWriting(true)} />
            <ReviewsBoard plus={plus} data={data.reviews} />
          </div>
        </main>
      </div>
      <TabBar active={PAGE_ID} />
      {writing && <ReviewModal data={data.reviews || {}} onClose={() => setWriting(false)} />}

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
