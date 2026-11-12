# npm audit (web/), 2026-11-12 — prepared by the agent; no `npm audit fix` was run, nothing was upgraded

Raw output: `npm-audit-20261112-0939-all.txt` / `.json` (everything) and `npm-audit-20261112-0939-prod-only.txt` (`--omit=dev`).
The three files were produced by three separate `npm audit` calls a moment apart.

| Package | Severity | Direct? | Ships to the browser? | Advisory (title as printed by npm) |
|---|---|---|---|---|
| react-router-dom, react-router | moderate (2) | react-router-dom direct | **yes** (runtime dependency) | open redirect via backslash in `<Link>`/`useNavigate`; arbitrary constructor injection in `deserializeErrors()` (SSR hydration) |
| vitest | critical | direct | no (dev only) | arbitrary file read/execute when the Vitest UI server listens; via @vitest/mocker |
| tinypool, vite-node, @vitest/mocker | critical / moderate | transitive of vitest | no (dev only) | prototype pollution to RCE in worker options; mocker issues |
| vite | high | direct | no (dev server and build tool) | path traversal in optimized deps `.map` handling; launch-editor UNC path (Windows) |
| esbuild | moderate | transitive of vite | no (dev server) | any website can send requests to the dev server |
| @playwright/test, playwright | high | direct / transitive | no (test tool) | browser download without SSL certificate verification |

Counts: 10 vulnerable packages in total (the journal earlier said "10 dev-dependency vulnerabilities"; that was wrong: 2 of the 10 are runtime dependencies).
`--omit=dev`: 2 moderate (the react-router pair). Everything else is development or test tooling that is not in the built bundle or the nginx image.

What I did not check (owner's call): whether the react-router advisories are reachable in this app. The app is a client-only SPA (no server rendering,
so `deserializeErrors()` is not used by us as far as I can tell from the code, which I did not audit for this), but it does use `<Link>`/`useNavigate`
with paths built from the URL in places (`/sign-in?reason=…`, run ids); I did not test the backslash case.
Every fix npm offers is a major-version upgrade (react-router-dom 7, vite 8, vitest 5) and was not tried.
