/* Page bootstrap — Inicio (dashboard).
   One of these per app page. The build concatenates the shared toolkit, the
   app/ components and exactly one of these, then prerenders <App/>. */

const PAGE_ID = "home";
const PAGE_TITLE = "Inicio";
const PAGE_SUB = "Panel del estudiante";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": false
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_DASHBOARD__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const plus = data.live ? !!data.plus : !!tweaks.plus;
  React.useEffect(() => {
    const savedTheme = localStorage.getItem("machreach-theme");
    const resolvedTheme = (data.live ? (savedTheme === "dark" ? "dark" : "light") : tweaks.theme) || "light";
    document.documentElement.dataset.theme = resolvedTheme;
    document.body.dataset.theme = resolvedTheme;
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent, data.live]);

  const toggleTheme = (key, value) => {
    if (key === "theme" && data.live) {
      document.documentElement.dataset.theme = value;
      document.body.dataset.theme = value;
      localStorage.setItem("machreach-theme", value);
      localStorage.setItem("mr_theme", "default");
    }
    setTweak(key, value);
  };

  return (
    <div className="app">
      <Sidebar active={PAGE_ID} plus={plus} />
      <div>
        <Topbar title={data.title || PAGE_TITLE} sub={data.sub || PAGE_SUB} streak={data.streak == null ? "17" : data.streak} xp={data.xp || "4.180"} coins={data.coins == null ? "320" : data.coins} avatar={data.avatar || "SP"} plus={plus} tweaks={{...tweaks, theme: document.documentElement.dataset.theme || tweaks.theme}} setTweak={toggleTheme} />
        <main className="page">
          <div className="dash">
            <div className="col">
              <Mission data={data.mission} />
              <Stats items={data.stats} />
              <TodayPlan plus={plus} data={data.plan} />
              <MyCourses data={data.courses} />
              <Exams data={data.exams} />
            </div>
            <div className="col">
              <League data={data.league} />
              <LevelCard data={data.level} />
              <StreakCard plus={plus} data={data.streak_data} />
              <Friends data={data.friends} />
            </div>
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
