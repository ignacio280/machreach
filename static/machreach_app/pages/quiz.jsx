/* Page bootstrap — Quiz en curso.
   Lifted from the design project's "MachReach Quiz.html" inline App();
   the ReactDOM.createRoot call is appended by build-app.mjs, not written here.

   Live-only: this page always renders one real quiz from the payload, so it
   carries no sample deck and no design-preview tweaks. The design mock's view
   switcher has no meaning here either — the moment shown is the student's own
   progress through the attempt. */

const PAGE_ID = "tools";
const PAGE_TITLE = "Quiz en curso";
const PAGE_SUB = "Herramientas · Quizzes";

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
          <QuizPage data={data} plus={plus} />
        </main>
      </div>
      <TabBar active={PAGE_ID} />
    </div>
  );
}
