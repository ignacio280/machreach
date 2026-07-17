# Content Security Policy hardening

Current state (17 July 2026): `unsafe-eval` has been removed and is regression
tested. `object-src 'none'`, restricted connection/frame/form origins, HSTS, and
other security headers remain enabled.

`unsafe-inline` is still required because the server-rendered student UI contains
inline `<script>` blocks, event-handler attributes, `<style>` blocks, and style
attributes. Removing the keyword before migrating those call sites would break
navigation, forms, modals, settings, quizzes, and responsive layouts.

## Removal sequence

1. Move reusable inline scripts and styles into versioned files under `static/`.
2. Replace `onclick`, `onload`, and similar attributes with event listeners in
   those static files.
3. Replace dynamic style attributes with CSS classes or narrowly scoped CSS custom
   properties.
4. Add a per-response nonce for the few unavoidable dynamic script/style blocks.
5. Run the Python HTTP journeys and all Playwright desktop/mobile journeys with a
   report-only policy that omits `unsafe-inline`.
6. Resolve every violation, enforce the strict policy, and add a regression test
   that rejects `unsafe-inline` as well as `unsafe-eval`.

The policy must not be tightened past a phase until registration, login, course
creation, AI generation, checkout redirects, account deletion, and mobile layouts
all pass with that phase's report-only policy.
