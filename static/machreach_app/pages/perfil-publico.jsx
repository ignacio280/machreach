/* Page bootstrap — Perfil de otro estudiante.

   Live-only: this page always renders one real student from the payload, so it
   carries no sample profile and no design-preview tweaks. The sidebar stays on
   Ranking because that (or the friends list) is where you clicked from. */

const PAGE_ID = "rank";
const PAGE_TITLE = "Perfil";
const PAGE_SUB = "Perfil de estudiante";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral"
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  // The topbar's theme toggle writes through this, so it stays even without a
  // tweaks panel to open.
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const plus = !!data.plus;

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  return (
    <div className="app">
      <Sidebar active={PAGE_ID} plus={plus} />
      <div>
        <Topbar title={data.title || PAGE_TITLE} sub={data.sub || PAGE_SUB} streak={data.streak ?? 0} xp={data.xp || "0"} coins={data.coins ?? 0} avatar={data.avatar || "MR"} plus={plus} tweaks={tweaks} setTweak={setTweak} />
        <main className="page">
          <PubProfilePage data={data.public_profile || {}} />
        </main>
      </div>
      <TabBar active={PAGE_ID} />
    </div>
  );
}
