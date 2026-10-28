# hotel-curator web

The web frontend: an explorer, a hotel page, a compare view and an "about the
model" page over the hotel-curator API. It is a static single-page app (React
19, TypeScript, Vite, Tailwind 4, Base UI, TanStack Query, React Router,
Recharts); the stack and its exact versions are decided in
[ADR 009](../docs/decisions/009-frontend-stack.md) and pinned in
`package-lock.json`.

The browser only ever talks to its own origin: every API call goes to `/api/...`,
which the dev server, `vite preview` and nginx (in Docker) forward to the
FastAPI app with the `/api` prefix removed.

## Requirements

- Node.js 22.22.2 or newer (`engines` in `package.json`; jsdom 30 needs it).
  The Docker build pins 22.23.4; CI asks `actions/setup-node` for `"22"`, so
  it runs whatever Node 22 release the runner resolves. Older 22.22 releases
  print an engine warning on `npm ci`.
- The Python API from the repository root (`.venv/bin/curator`), for `dev`,
  `preview` and the end-to-end tests.

```sh
cd web
npm ci
```

## Run it locally

Start the API with the synthetic dev data (5 hotels, committed at
`web/e2e/data/hotel_results.json.gz`) in one terminal:

```sh
cd ..   # repository root
export CURATOR_DATABASE_URL=sqlite:////tmp/curator-dev.db
.venv/bin/curator db upgrade
.venv/bin/curator db seed --path web/e2e/data/hotel_results.json.gz
.venv/bin/curator serve --port 8000
```

Then the frontend in another:

```sh
npm run dev        # http://127.0.0.1:5173, /api -> http://127.0.0.1:8000
```

Set `CURATOR_API_URL` to proxy to another API address. The API docs are at
<http://127.0.0.1:5173/api/docs>.

## Scripts

| Script                            | What it does                                                                         |
| --------------------------------- | ------------------------------------------------------------------------------------ |
| `npm run dev`                     | Vite dev server with hot reload and the `/api` proxy                                 |
| `npm run build`                   | Type-check (`tsc -b`) and build the static bundle into `dist/`                       |
| `npm run preview`                 | Serve `dist/` on port 4173 with the `/api` proxy and the production security headers |
| `npm run lint`                    | ESLint (flat config: typescript-eslint, react-hooks, jsx-a11y), no warnings allowed  |
| `npm run format` / `format:check` | Prettier                                                                             |
| `npm run typecheck`               | TypeScript in strict mode, app and tooling projects                                  |
| `npm test`                        | Unit and component tests (Vitest, Testing Library, MSW)                              |
| `npm run test:e2e`                | Builds, then runs Playwright against the real API (see below)                        |
| `npm run generate:api`            | Regenerates the typed client in `src/api/generated/` from `openapi.json`             |
| `npm run audit:deps`              | `npm audit`, failing on high or critical advisories (needs the registry)             |
| `npm run check`                   | lint + typecheck + test + build                                                      |

## Tests

**Unit and component tests** (`src/**/*.test.ts(x)`, `test/*.test.ts`) render
whole pages through the real router and providers. Network calls are answered
by MSW handlers (`src/test/handlers.ts`) built from fixtures
(`src/test/fixtures.ts`) that are typed with the generated OpenAPI models, so a
fixture that no longer matches `openapi.json` fails the type check. They cover
explorer filters and the URL, the hotel page sections, compare highlighting,
the analyze states (200, 422, 429, 503), error states with request ids, the
theme toggle, and that the nginx site (`nginx/site.conf`) sends the same
security headers as `security-headers.ts`.

**End-to-end tests** (`e2e/`) run in Chromium against the production build
and the real API with the synthetic dev data:

```sh
npm run test:e2e
```

`playwright.config.ts` starts both servers and stops them afterwards:

1. the API, from the repository root:
   `curator db upgrade && curator db seed --path web/e2e/data/hotel_results.json.gz && curator serve --port 8000`
   with `CURATOR_DATABASE_URL=sqlite:////tmp/curator-e2e.db` (override with
   `CURATOR_E2E_DATABASE_URL`) and the per-client rate limits raised;
2. `vite preview` on port 4173 (same proxy and CSP as nginx).

Locally, servers already running on those ports are reused; with `CI=true`
they are always started fresh. The journeys are J1 (explorer filters to a
hotel), J2 (hotel page shows why and evidence), J3 (compare two hotels) and J5
(analyze returns labels, or says the model is unavailable: the dev API has no
model package and answers 503). Every page and its interactive states are
checked with axe (`@axe-core/playwright`) in light and dark mode; any serious
or critical violation fails the run, and every test also fails on a page
error or a Content-Security-Policy violation.

The seed file is the synthetic data the e2e assertions rely on (5 made-up
hotels, 4 KB); it lives here because the repository ignores `var/`.

Playwright 1.63.0 is pinned to match the preinstalled Chromium build. Do not
run `playwright install` in the build environment; the browser is found
through `PLAYWRIGHT_BROWSERS_PATH`, or set `PLAYWRIGHT_CHROMIUM_PATH` to a
Chromium binary.

## The API client

`src/api/generated/` is generated by orval from `openapi.json` and committed.
It contains typed fetch functions and TanStack Query hooks (`useListHotels`,
`useGetHotel`, `useCompareHotels`, `useListCities`, `useGetMeta`,
`useAnalyzeReview`); readable operation names are mapped in
`orval.config.ts`, and an unmapped new operation fails generation on purpose.
Every call goes through `src/api/fetcher.ts`, which prefixes nothing itself
(orval's `baseUrl` is `/api`), turns the API's `{error: {code, message,
request_id, fields}}` body into an `ApiError`, and reads `Retry-After`.

When the backend changes:

```sh
cd ..                                  # repository root
.venv/bin/python scripts/export_openapi.py   # writes web/openapi.json
cd web
npm run generate:api
git diff --exit-code src/api/generated # what CI checks for drift
```

## Docker

```sh
docker build -t hotel-curator-web .
```

A two-stage build: `node:22.23.4-alpine3.24` runs `npm ci` and `npm run build`,
then `nginxinc/nginx-unprivileged:1.30.6-alpine3.24` (both pinned by digest)
serves `dist/` as a non-root user on port 8080. At start,
`nginx/15-curator-site.sh` renders the site for `CURATOR_EDGE` (`local` for
compose, `fly` behind Fly's edge, `cloudflare` behind Cloudflare; ADR 011)
from `nginx/`: SPA fallback to `index.html`, `/api/` proxied to
`CURATOR_API_UPSTREAM` (default `api:8000`) with the prefix stripped and
the client address in `X-Real-IP`, one HSTS value, gzip, long caching for hashed assets, and security headers (a strict
Content-Security-Policy with no inline scripts or styles,
`X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors 'none'`).
API answers get a deny-all CSP (`default-src 'none'`). The API docs page
(`/api/docs`) is Swagger UI served by the API from a pinned copy (no CDN),
so its policy allows scripts and styles from this origin only (styles also
inline, which Swagger UI needs), requests to this origin only, never framed.
`docker compose up --build` at the repository root runs it next to the API.

## Dependency audit

`npm audit` reports 0 vulnerabilities. orval's `@scalar/json-magic@0.15.7`
pins `undici@7.29.1` (dev-only, used when generating the client; advisories
GHSA-xjj6-f33q-929c, GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472), so
`package.json` overrides it to `7.30.1`; the generated client is byte-identical
with the override. Run `npm run audit:deps` after changing dependencies.

## Layout

```
src/
  api/          fetcher (error handling), query client, generated/ (orval)
  components/   badges, interval chart, states, layout/ (shell, route error)
    ui/         shadcn-style components on Base UI: button, card, badge,
                table, select (native), tabs, tooltip, skeleton, checkbox, input
  lib/          wording for tiers and grades, URL filters, compare list, theme
  pages/        Explorer, Hotel, Compare, About, NotFound (+ tests)
  test/         fixtures, MSW handlers, render helper, setup
e2e/            Playwright journeys and axe checks
test/           tooling tests (nginx headers)
```

## Design and accessibility

- Light and dark themes from design tokens in `src/index.css`; the theme
  follows `prefers-color-scheme` until the header toggle is used, and the
  choice is kept in `localStorage` (guarded, so the app works without it).
- Grades and tiers are always text plus an icon plus colour. The two "not
  enough" tiers read as "Not enough evidence to judge", styled neutrally.
- Every chart has a heading, a caption or summary line, and a table with the
  same numbers. The interval charts (points, ranges, grade lines) have a
  legend; the review-volume chart has a single series, so no legend.
  Chart colours: blue `#2a78d6` for points, orange `#eb6834` for the
  highlighted mark (a credible leader, the served model), grey for intervals;
  dark mode uses the dark steps of the same hues.
- Skip link, landmarks, ordered headings, labels on every input, visible focus
  rings, focus moved to the main region on page change, reduced motion
  respected, no horizontal page scroll down to 360 px.
- Filter changes keep the scroll position. When the control just used goes
  away (a page link, an empty-state button, a compare "Remove"), focus moves
  to the results heading, the next Remove button or the page heading; the
  e2e tests check this.
- The "i" buttons are popovers (click, tap, Enter, hover), labelled dialogs
  that Escape closes; the About page states the same facts. Tooltips are only
  used to repeat an icon button's name.
- Compare tags hotels A to D in the cards, table and charts, so hotels of one
  chain never get identical chart labels; long names wrap instead of being
  cut.
- System font stack only; the app makes no external requests at runtime
  (`connect-src 'self'`) and has no analytics. The one exception on the same
  origin is the API docs page, which is FastAPI's (see Docker above).
