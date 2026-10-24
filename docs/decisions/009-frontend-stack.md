# ADR 009: Frontend stack

- Status: accepted
- Date: 2026-10-24
- Versions checked against the live npm registry on 2026-10-24; a probe
  project installed, built, linted and tested the stack before this decision.

## Context

The product needs an explorer, a hotel page, a compare view and an "about the
model" page over a separate FastAPI API. It must be standard and boring,
accessible (WCAG 2.2 AA basics), responsive, and run from `docker compose up`.

## Options

| Option | For | Against |
|---|---|---|
| **React + TypeScript + Vite SPA** | Most widely used SPA toolchain (vite ~119M weekly downloads); shadcn ships a Vite template; one backend language; static bundle served by any web server | No server rendering (not needed: no SEO requirement) |
| Next.js 16 | Server rendering, SEO | A second server tier (Node) next to FastAPI; static export does not support dynamic routes like `/hotels/:id` whose ids come from the API without pre-generation |

## Decision

React + TypeScript + Vite single-page app.

| Concern | Choice | Version | Licence | Notes |
|---|---|---|---|---|
| UI library | react, react-dom | 19.3.1 | MIT | |
| Language | typescript | **6.0.3 (pinned)** | Apache-2.0 | 7.x (the native port) breaks typescript-eslint and the OpenAPI generators today |
| Build | vite, @vitejs/plugin-react | 8.3.x, 6.1.x | MIT | Rolldown-based |
| Styling | tailwindcss, @tailwindcss/vite | 4.3.x | MIT | |
| Components | shadcn/ui pattern (source copied into the repo) on Base UI primitives | @base-ui/react 1.9.x | MIT | Base UI is shadcn's default since 2026-07; components are plain source files we own |
| Data fetching | @tanstack/react-query | 5.104.x | MIT | caching, loading/error states |
| Routing | react-router | 8.5.x | MIT | v8 removed `react-router-dom`; data mode; filters live in the URL |
| API client | orval (generated from the FastAPI OpenAPI schema) | 8.44.x | MIT | generates typed fetch functions and TanStack Query hooks; generated code is committed and checked for drift in CI. openapi-fetch is in maintenance mode |
| Charts | recharts | 3.10.x | MIT | SVG with an accessibility layer on by default; every chart also has a table or text summary |
| Icons | lucide-react | 1.61.x | ISC | |
| Unit tests | vitest, @testing-library/react, @testing-library/jest-dom, msw, jsdom | 5.0.x, 16.3.x, 7.0.x, 3.0.x, 30.1.x | MIT | |
| E2E + a11y | @playwright/test **1.63.0 (pinned)**, @axe-core/playwright | 1.63.0, 4.14.x | Apache-2.0, MPL-2.0 (dev only) | 1.63.0 matches the preinstalled Chromium r1243; newer versions cannot download browsers through the build proxy |
| Lint/format | eslint 10, typescript-eslint, eslint-plugin-react-hooks, eslint-plugin-jsx-a11y, prettier | 10.13.x, 8.72.x, 7.1.x, 6.10.x, 3.9.x | MIT | jsx-a11y's peer range stops at ESLint 9 but its rules run under 10 (tested) |

Exact versions are pinned in `web/package-lock.json`.

The shadcn CLI fetches component sources from `ui.shadcn.com`, which the build
environment's network policy blocks. The handful of components the app needs
(button, card, badge, table, select, tabs, tooltip, skeleton) are therefore
written in the shadcn style directly in `web/src/components/ui/`, on the Base
UI primitives installed from npm. They are ordinary source files either way;
a contributor with normal internet can replace them with `npx shadcn add`.

## Consequences

- Two deployables in production (static bundle + API); in `docker compose`
  the web container serves the bundle and proxies `/api` to the API, so the
  browser sees one origin and CORS stays closed.
- Accessibility: axe in Playwright on every route in light and dark mode;
  keyboard focus tests for the parts of WCAG 2.2 axe cannot check.
- If SEO-indexed hotel pages become a requirement, revisit with a framework
  that renders on the server.
