# Content Security Policy hardening

Current state (17 July 2026): both `unsafe-eval` and `unsafe-inline` have been
removed from the enforced policy and are regression tested. `script-src-attr` and
`style-src-attr` are set to `'none'`; `object-src 'none'`, restricted
connection/frame/form origins, HSTS, and the other security headers remain enabled.

MachReach still has server-rendered legacy templates with inline blocks and
attributes. At response time `machreach_core.csp` now:

1. creates a cryptographically random nonce for each response;
2. authorizes script and style blocks with that nonce;
3. extracts style attributes into response-scoped CSS classes;
4. converts event-handler attributes into delegated nonce-authorized listeners;
5. applies the same transformation to HTML fragments later inserted by JavaScript.

This is an enforced migration layer, not a report-only policy. Python regression
tests assert that rendered HTML has no inline event or style attributes and that
every script/style block has the response nonce. Because server-rendered blocks
receive that nonce, all user-controlled values must remain escaped before they
enter HTML templates. The Playwright registration, login, course creation,
grade-sheet interaction, AI generation, checkout redirect, account deletion,
and mobile-layout journeys are the browser-level compatibility gate.

Longer term, reusable scripts and styles should continue moving into versioned
files under `static/`. That reduces response size and lets this compatibility layer
become progressively smaller without weakening the policy.
