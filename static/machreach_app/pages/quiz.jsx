/* Page bootstrap — Quiz en curso.
   Lifted from the design project's "MachReach Quiz.html" inline App();
   the ReactDOM.createRoot call is appended by build-app.mjs, not written here.

   The design mock cycles through the three moments with a tweak; live, the
   phase is the student's own progress, so the tweak only exists offline. */

const PAGE_ID = "tools";
const PAGE_TITLE = "Quiz en curso";
const PAGE_SUB = "Herramientas · Quizzes";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": true
}/*EDITMODE-END*/;

/* Sample deck for the offline design preview at /design/quiz. Live pages get
   the real questions in the payload and never read this. */
const QZ_DEMO = {
  id: 0,
  title: "Termodinámica — Primera ley y entalpía",
  course_name: "FIS-2103 Termodinámica",
  difficulty: "medium",
  attempts: 1,
  questions: [
    { id: 1, topic: "Primera ley", question: "En un proceso adiabático, ¿qué relación describe correctamente el cambio de energía interna?", option_a: "ΔU = Q − W", option_b: "ΔU = −W", option_c: "ΔU = Q", option_d: "ΔU = 0", correct: "b", explanation: "En un proceso adiabático no hay transferencia de calor (Q = 0), así que la primera ley ΔU = Q − W se reduce a ΔU = −W." },
    { id: 2, topic: "Entalpía", question: "¿Por qué la entalpía es más útil que la energía interna en procesos a presión constante?", option_a: "Porque no depende de la temperatura", option_b: "Porque incluye el trabajo de expansión PV", option_c: "Porque siempre es positiva", option_d: "Porque se mide con un termómetro", correct: "b", explanation: "H = U + PV. A presión constante, ΔH equivale al calor intercambiado." },
    { id: 3, topic: "Primera ley", question: "Un gas absorbe 500 J de calor y realiza 200 J de trabajo. ¿Cuál es ΔU?", option_a: "700 J", option_b: "−300 J", option_c: "300 J", option_d: "−700 J", correct: "c", explanation: "ΔU = Q − W = 500 − 200 = 300 J." },
    { id: 4, topic: "Procesos", question: "¿Cuál de estos procesos mantiene el volumen constante?", option_a: "Isobárico", option_b: "Isotérmico", option_c: "Isocórico", option_d: "Adiabático", correct: "c", explanation: "Isocórico significa volumen constante, por lo que W = 0 y ΔU = Q." },
  ],
};

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
          <QuizPage data={data.live ? data : { quiz: QZ_DEMO }} plus={plus} />
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
