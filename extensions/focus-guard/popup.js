// MachReach Focus Guard — popup logic (MV3: external script, no inline JS).

// ── Focus-session status badge ──
chrome.storage.local.get(["focusActive", "focusExpiresAt"], (d) => {
  const active = d.focusActive && (!d.focusExpiresAt || Date.now() < d.focusExpiresAt);
  const el = document.getElementById("state");
  if (!el) return;
  if (active) {
    el.className = "state active";
    el.innerHTML = '<span class="dot on"></span>Activo - sitios bloqueados';
  } else {
    el.className = "state idle";
    el.innerHTML = '<span class="dot off"></span>Sin sesion activa';
  }
});

// ── Canvas course sync ──
// Runs inside the active tab (the student's Canvas), reading the course list
// from their own logged-in session, then POSTs it to MachReach using the
// signed connect token the content script captured from a MachReach page.
function fetchCanvasCourses() {
  return (async () => {
    try {
      const url = location.origin +
        "/api/v1/courses?per_page=100&enrollment_state=active&include[]=term";
      const r = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
      if (!r.ok) return { ok: false, status: r.status };
      const text = await r.text();
      // Canvas guards some JSON responses with a `while(1);` prefix.
      const data = JSON.parse(text.replace(/^while\(1\);/, ""));
      if (!Array.isArray(data)) return { ok: false, notCanvas: true };
      const courses = data
        .filter((c) => c && c.id && !c.access_restricted_by_date)
        .map((c) => ({
          id: c.id,
          name: c.name,
          course_code: c.course_code,
          term: c.term ? { name: c.term.name } : null
        }));
      return { ok: true, courses };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  })();
}

function setMsg(text, kind) {
  const el = document.getElementById("sync-msg");
  if (!el) return;
  el.hidden = false;
  el.className = "syncmsg" + (kind ? " " + kind : "");
  el.textContent = text;
}

const syncBtn = document.getElementById("sync-canvas");
if (syncBtn) {
  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    setMsg("Buscando tus cursos en esta pestaña…", "");
    try {
      const { mrConnectToken, mrOrigin } = await chrome.storage.local.get(["mrConnectToken", "mrOrigin"]);
      if (!mrConnectToken || !mrOrigin) {
        setMsg("Abre MachReach con tu sesión iniciada (página Enfoque) y vuelve a intentar.", "err");
        syncBtn.disabled = false;
        return;
      }

      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id || !/^https?:/i.test(tab.url || "")) {
        setMsg("Abre tu Canvas en esta pestaña y vuelve a pulsar el botón.", "err");
        syncBtn.disabled = false;
        return;
      }

      const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fetchCanvasCourses });
      const out = results && results[0] && results[0].result;

      if (!out || !out.ok) {
        if (out && (out.status === 401 || out.status === 403)) {
          setMsg("Canvas pidió iniciar sesión. Entra a tu Canvas en esta pestaña y reintenta.", "err");
        } else if (out && out.notCanvas) {
          setMsg("Esta pestaña no parece ser Canvas. Ábrelo y vuelve a pulsar.", "err");
        } else {
          setMsg("No pude leer los cursos. Asegúrate de estar en tu Canvas con sesión iniciada.", "err");
        }
        syncBtn.disabled = false;
        return;
      }

      if (!out.courses.length) {
        setMsg("No encontré cursos activos en tu Canvas.", "err");
        syncBtn.disabled = false;
        return;
      }

      setMsg("Enviando " + out.courses.length + " cursos a MachReach…", "");
      const resp = await fetch(mrOrigin + "/api/student/canvas/extension-import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: mrConnectToken, courses: out.courses })
      });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok && body.ok) {
        setMsg("✓ Listo: " + (body.saved || 0) + " cursos sincronizados con MachReach.", "ok");
      } else if (resp.status === 401) {
        setMsg("Tu enlace con MachReach expiró. Abre la página Enfoque otra vez y reintenta.", "err");
      } else {
        setMsg("MachReach rechazó la importación. Inténtalo de nuevo.", "err");
      }
    } catch (e) {
      setMsg("Error inesperado: " + String(e), "err");
    } finally {
      syncBtn.disabled = false;
    }
  });
}
