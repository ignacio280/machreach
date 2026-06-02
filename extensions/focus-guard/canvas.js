// Runs on the student's Canvas (UC: cursos.canvas.uc.cl, and *.instructure.com).
// Injects a floating button that reads the course list from the student's own
// logged-in session and sends it to MachReach via the background worker — no
// API token and no institution admin key required.

(function () {
  if (window.__mrCanvasSyncInjected) return;
  window.__mrCanvasSyncInjected = true;

  function el(tag, css, text) {
    const e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text) e.textContent = text;
    return e;
  }

  const btn = el(
    "button",
    "position:fixed;right:18px;bottom:18px;z-index:2147483647;" +
      "background:#FF7A3D;color:#fff;border:none;border-radius:12px;" +
      "padding:12px 16px;font:700 14px/1 'Nunito',system-ui,sans-serif;" +
      "box-shadow:0 6px 20px rgba(20,18,30,.28);cursor:pointer;",
    "📚 Sincronizar cursos con MachReach"
  );

  const msg = el(
    "div",
    "position:fixed;right:18px;bottom:64px;z-index:2147483647;max-width:300px;" +
      "background:#1A1A1F;color:#FFF8E1;border-radius:10px;padding:10px 12px;" +
      "font:700 12px/1.4 'Nunito',system-ui,sans-serif;" +
      "box-shadow:0 6px 20px rgba(20,18,30,.3);display:none;"
  );

  function show(text, ok) {
    msg.textContent = text;
    msg.style.background = ok === true ? "#1E6B43" : ok === false ? "#A22" : "#1A1A1F";
    msg.style.display = "block";
  }

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    show("Leyendo tus cursos…");
    try {
      const r = await fetch(
        location.origin + "/api/v1/courses?per_page=100&enrollment_state=active&include[]=term",
        { credentials: "same-origin", headers: { Accept: "application/json" } }
      );
      if (!r.ok) {
        show(r.status === 401 || r.status === 403
          ? "Inicia sesión en Canvas y vuelve a intentarlo."
          : "No pude leer tus cursos desde Canvas.", false);
        btn.disabled = false;
        return;
      }
      // Canvas guards some JSON responses with a `while(1);` prefix.
      const data = JSON.parse((await r.text()).replace(/^while\(1\);/, ""));
      const courses = (Array.isArray(data) ? data : [])
        .filter((c) => c && c.id && !c.access_restricted_by_date)
        .map((c) => ({
          id: c.id,
          name: c.name,
          course_code: c.course_code,
          term: c.term ? { name: c.term.name } : null
        }));
      if (!courses.length) {
        show("No encontré cursos activos en tu Canvas.", false);
        btn.disabled = false;
        return;
      }
      show("Enviando " + courses.length + " cursos a MachReach…");
      chrome.runtime.sendMessage({ type: "mrImportCourses", courses }, (resp) => {
        if (resp && resp.ok) {
          show("✓ " + (resp.saved || 0) + " cursos sincronizados con MachReach.", true);
        } else if (resp && resp.noToken) {
          show("Abre MachReach (página Enfoque) con tu sesión iniciada y reintenta.", false);
        } else {
          show("No se pudo importar. Inténtalo de nuevo.", false);
        }
        btn.disabled = false;
      });
    } catch (e) {
      show("Error inesperado: " + String(e), false);
      btn.disabled = false;
    }
  });

  function mount() {
    if (!document.body) return;
    document.body.appendChild(msg);
    document.body.appendChild(btn);
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
