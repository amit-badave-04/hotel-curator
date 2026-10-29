# Security review: hotel-curator v2

_Status: sections 1 to 6 are final for the v2 pull request (merged). Gates
ran on 2026-10-26 against the tree of the quality-and-security commit;
every claim names the command or test that backs it. A red-team exercise
ran on 2026-10-26 (section 4.6); the owner's own red-team procedure remains
available. Section 7 covers the public deployment on Fly.io and later
Cloudflare (branch `deploy/fly-cloudflare`): its gates ran on 2026-10-29,
and where it changes what sections 1 to 6 say about the public site, the
earlier text points to it._

## 1. What is being protected

hotel-curator serves derived, public-data results (hotel tiers, quotes from
published reviews) and runs a classifier on text users submit. It holds no
user accounts, no payment data and no user-submitted content at rest.

| Asset | Why it matters |
|---|---|
| Availability of the API and web app | the product |
| Integrity of tiers and evidence | a wrong or manipulated tier misleads travellers and harms hotels |
| Reviewer privacy | reviews mention staff names; reviewer nationality, room and dates could identify people |
| Build and supply chain | pinned data, pretrained weights and dependencies decide what the model learns and runs |
| Secrets | none in the compose setup. The public site needs database passwords and URLs, two deploy tokens, Cloudflare's origin key and an optional Sentry DSN, all kept outside git and images (section 7.3) |
| Visitors' privacy | nothing a visitor submits is stored or reported, or logged outside nginx's rare critical-error lines (section 7.5); the public site's nginx logs each visitor's address for the rate limits (section 7) |

## 2. Threats and abuse cases

| Threat | Mitigation | Evidence |
|---|---|---|
| Scraping or flooding the API | moving-window rate limits on every `/v1` route, keyed on the client address (per client when the API sees real client addresses; in the laptop compose setup all host clients share one bucket, section 5), page-size cap (100) | `tests/api/test_api.py::test_rate_limits`, `::test_search_rejects_bad_input` |
| CPU exhaustion via `/v1/analyze` | stricter rate limit, 2 parts, each capped at `CURATOR_MAX_PART_CHARS` (2,000) and truncated to 128 tokens; body limit 64 KB | `::test_analyze_rejects_bad_input`, `::test_oversized_body_is_rejected` |
| Injection (SQL, path, header) | SQLAlchemy with bound parameters; sort orders from an enum; `LIKE` wildcards escaped; ids and cities matched against strict patterns | `::test_search_filters` (literal `%`), `::test_search_rejects_bad_input` (SQL strings), `::test_hotel_errors` (an encoded `..` id, `%2E%2E`, reaches the hotel route and is rejected by the id pattern: 422; a literal `../` is removed by the HTTP client before it is sent, and an encoded slash never matches the one-segment route: 404) |
| Reflected input / information leaks | one error schema, no stack traces, validation errors drop submitted values; `analyze` never echoes text; unexpected errors answer 500 in the same shape with the security headers; logged tracebacks never include local variables (they would hold review text) | `::test_search_rejects_bad_input`, `::test_analyze_labels_without_echoing_text`, `::test_unexpected_errors_keep_the_error_shape_and_headers`, `tests/unit/test_logging.py` |
| Body-limit bypass with chunked uploads | bytes are counted as they stream; reading stops at the limit with the same 413 | `::test_oversized_chunked_body_gets_413` |
| Third-party script in the API docs page | Swagger UI is vendored (pinned SHA-256) and served from the API itself under a CSP that allows only `'self'` | `tests/api/test_docs.py` |
| Broken or missing model package | the API starts without it, logs why, answers 503 on `analyze`, and never retries per request | `::test_a_broken_model_package_is_reported_not_raised`, `::test_the_reason_a_model_is_missing_is_logged` |
| Cross-origin abuse | CORS allow-list from settings, no credentials; in Docker the browser sees one origin | `::test_cors_allow_list` |
| Clickjacking, MIME sniffing | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`; nginx adds a strict CSP | `::test_security_headers_and_request_id`, `web/nginx/site.conf`, `tests/edge/test_nginx.py` |
| Malicious model files | weights only as safetensors or ONNX; `torch.load(weights_only=True)` for the one pickle conversion; `local_files_only=True`; no `trust_remote_code` | `tests/unit/test_security_invariants.py`, `tests/unit/test_bases.py::test_convert_refuses_pickled_code` |
| Tampered downloads | HTTPS only, SHA-256 pins on every data file and weight file, Docker layers by digest; tar extraction reads only named regular files | `tests/unit/test_download.py`, `tests/unit/test_bases.py` |
| Prompt injection into the labelling LLM | review text in a delimited block, explicit "data, not instructions", closing delimiter neutralised; no LLM at serving time | `tests/unit/test_prompt.py` |
| Tier poisoning | `analyze` never writes; tiers come only from the offline pipeline | architecture; `routes/analyze.py` |
| De-anonymising reviewers, naming staff | quotes show month and traveler type only; parts that probably name a person are not quoted (a person-introducing word before a corpus-rare word, or a rare capitalised word in a part about staff; ADR 006 E5), names spaCy finds in the rest are masked, long digit runs too; nationality, room type and nights never served. Best effort: on the 31,485 served quotes none of the staff names the independent review found remains, and a manual check of 90 quotes with rare capitalised words found no staff name (checked on the run with a fixed 500-part cut-off, 8 quotes different from the final one); some residual risk stays | `tests/unit/test_names.py` (real corpus parts the NER missed), `tests/unit/test_explain_evidence.py::test_a_part_naming_someone_is_replaced_by_the_next`, `::test_redactor_masks_names_numbers_and_emails`, `tests/unit/test_security_invariants.py::test_sr9_*` |
| Container breakout / privilege | non-root users, pinned base images by digest, healthchecks, read-only API filesystem, `no-new-privileges` (API, web and db), a database with no network at all (Unix socket shared only with the API), and an API database role that is not a superuser, so a foothold in the API cannot run `COPY ... PROGRAM` or read server files in the db container. This is the compose setup; on Fly the read-only filesystem, `no-new-privileges` and the network-less database do not carry over, and other controls replace them (section 7) | `Dockerfile`, `web/Dockerfile`, `docker-compose.yml`, `docker/db-init/10-app-role.sh`, `tests/unit/test_compose_config.py` |

## 3. Requirements SR-1..SR-14

The requirements were registered in `docs/specs/v2-plan.md` section 7.2
before the code was written. The tests named here run in CI on pushes to
`main` and `v2` and on every pull request.

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| SR-1 | Input validation on every endpoint | met | `tests/api/test_api.py::test_search_rejects_bad_input`, `::test_analyze_rejects_bad_input`, `::test_hotel_errors` (lengths, enums, page bounds, id and city patterns: 422) |
| SR-2 | Body size limit before parsing | met | `::test_oversized_body_is_rejected`, `::test_oversized_chunked_body_gets_413`. Streamed bytes are counted on routes that read a body; a GET route ignores an oversized chunked body sent straight to `:8000` (red team, section 4.6), and nginx caps every body at 64 KB |
| SR-3 | Rate limits, stricter on analyze | met for every `/v1` route; **narrower than registered** | `::test_rate_limits` (429 with `Retry-After`). The plan says "all endpoints"; as built, `/healthz`, `/readyz`, the docs page, the OpenAPI schema and the docs assets are not limited, by design (the Docker healthcheck polls `/healthz`; the same test asserts probes are not limited). They return public metadata or static files |
| SR-4 | No echo, no stack traces | met | `::test_analyze_labels_without_echoing_text`, `::test_search_rejects_bad_input` (validation errors carry location and message only), `::test_unexpected_errors_keep_the_error_shape_and_headers`, `tests/unit/test_logging.py` |
| SR-5 | No secrets in code or history | met | gitleaks and trufflehog, section 4.1 |
| SR-6 | No known vulnerable dependencies | met for Python and npm | pip-audit, osv-scanner, npm audit, section 4.2; container OS packages in section 4.3 |
| SR-7 | SAST clean or triaged | met | semgrep and bandit, section 4.4 |
| SR-8 | No unsafe model loading | met | `tests/unit/test_security_invariants.py::test_sr8_no_unsafe_model_or_data_loading`, `tests/unit/test_bases.py::test_convert_refuses_pickled_code` |
| SR-9 | Snippets capped and redacted | met, best effort | `tests/unit/test_names.py`, `tests/unit/test_explain_evidence.py::test_redactor_masks_names_numbers_and_emails`, `::test_a_part_naming_someone_is_replaced_by_the_next`, `tests/unit/test_security_invariants.py::test_sr9_quotes_never_carry_identifying_review_metadata`; residual risk in section 2 |
| SR-10 | HTTPS + SHA-256 downloads, safe extraction | met | `tests/unit/test_download.py` (plain HTTP and redirects to HTTP refused, checksum mismatch deletes the file), `tests/unit/test_bases.py` (only listed regular files are read from an archive and written under their base name, so no member can choose its path; links refused), `tests/unit/test_security_invariants.py::test_sr10_downloads_are_https_only` |
| SR-11 | LLM labelling treats reviews as data | met | `tests/unit/test_prompt.py::test_reviews_cannot_close_the_delimiter`, `::test_tag_variants_cannot_close_the_data_block`; no LLM in the serving path |
| SR-12 | Non-root, pinned, healthchecked containers | met | `docker inspect` (v2): API user `curator` (uid 10001) with a `/healthz` healthcheck, web user `101` with a `wget` healthcheck on `/` (since the deployment branch it polls `/healthz`: `web/Dockerfile`); every base image pinned by digest. On Fly, the API's checks poll `/readyz` and `/healthz`; the web app's poll `/` in Fly mode and `/healthz` on port 8080 in Cloudflare mode (`fly.toml`, `web/fly.toml`, `web/fly.cloudflare.toml`) |
| SR-13 | CORS allow-list | met | `::test_cors_allow_list` (credentials off, allow-list from settings) |
| SR-14 | Parameterised SQL, allow-listed sorting | met | `::test_search_filters` (literal `%`), `::test_search_rejects_bad_input` (SQL strings); sort orders come from an enum mapped to fixed `ORDER BY` clauses |

## 4. Gate results

Secret, dependency and static-analysis gates last ran on 2026-10-26 at
19:50-19:53 UTC against a clean export of the staged index of the
quality-and-security commit, after all review and red-team fixes (the
results match the 17:42-17:45 UTC run before the red-team fixes, apart from
one more file scanned); two earlier runs on
2026-10-25 also found nothing in the pull request. The image scans ran on
2026-10-25 at 01:12-01:17 UTC on images built from the same Dockerfiles
and base digests. Tool versions are in `docs/TOOLING.md`. Offline
vulnerability databases were downloaded from the public OSV bucket (MD5
checked) on 2026-10-25 at 01:07-01:17 UTC. Each scanner had first caught a
planted fixture (`docs/TOOLING.md`), so "0" means no findings, not a silent
tool.

### 4.1 Secrets (SR-5)

| Gate | Scope | Result |
|---|---|---|
| `gitleaks git -c .gitleaks.toml --log-opts=main..v2` | the 6 commits of this pull request before the gates commit | 0 findings |
| `gitleaks git --staged -c .gitleaks.toml` | the gates commit itself | 0 findings |
| `gitleaks git -c .gitleaks.toml --log-opts=--all` | all 31 commits on every ref at scan time, including `main` and the session's backup branch | 1 finding, a false positive: a SHA-256 file hash in a backup snapshot (`f4bce5a`, `artifacts/evals/electra-small-ft/package_report.json`, rule `generic-api-key`). The value equals `sha256sum` of the ELECTRA package's public `tokenizer.json`. The snapshot is only on the backup branch, not in this pull request; the report format was changed so later reports do not match the rule (no allow-list entry was added) |
| `gitleaks dir -c .gitleaks.toml` | the tree (26 MB) | 0 findings |
| `trufflehog git file://<repo> --no-verification` | every branch | 0 verified, 0 unverified |
| `trufflehog filesystem <tree> --no-verification` | the tree, archives unpacked (52 MB scanned) | 0 verified, 0 unverified |

`.gitleaks.toml` uses the default rules and allow-lists only local paths
that v2 gitignores (`.venv/`, `node_modules/`, caches, `var/`, raw data).
v1 did track files under `data/processed/` (synthetic reviews, deleted in
the data commit), so the same all-refs history scan was repeated with the
default rules and no allow-list at all: it finds the same single false
positive and nothing else. CI runs gitleaks on the full history of the
commit under test (`--log-opts="--full-history HEAD"`; on a pull request
that is the merge commit, so `main` and the branch) on pushes to `main` and
`v2` and on every pull request. An independent verification found that the
first version of that step scanned every fetched branch, so the backup
branch's false positive would have failed every run; it was scoped before
the pull request was opened (the scoped command finds no leaks; the
unscoped one reproduces the failure).

### 4.2 Dependencies (SR-6)

| Gate | Scope | Result |
|---|---|---|
| `uv export --frozen --all-extras --all-groups` then `pip-audit --require-hashes` | 188 locked Python packages | 0 known vulnerabilities. One package not auditable: the spaCy model `en-core-web-sm`, installed from a GitHub release URL pinned by SHA-256 in `uv.lock`, has no PyPI advisory data |
| `osv-scanner scan source --offline -r` | `uv.lock` (193 packages) and `web/package-lock.json` (587 packages) | 0 known vulnerabilities. Excluded: `notebooks/v1/requirements.txt`, the archived v1 environment freeze, which is never installed |
| `npm audit` and `npm audit --omit=dev` in `web/` | 600 packages (62 production) | 0 vulnerabilities of any severity |

CI runs pip-audit and npm audit (fails on high or critical) on pushes to
`main` and `v2` and on every pull request.

### 4.3 Container images (beyond SR-6)

`osv-scanner scan image --archive <docker save> --offline-vulnerabilities`
on the three images `docker compose up` runs, with the Debian, Alpine, Go and
PyPI databases. Counts are unique (package, version, advisory) triples.

| Image | Before | Fix applied | After | Remaining, and why they stay |
|---|---|---|---|---|
| API (`python:3.12-slim`, Debian 13) | 93, of which 12 with a fixed version, all in the base image's own pip 25.0.1 | `Dockerfile` removes pip from the runtime stage; the app runs from `/opt/venv`, which has no pip | 81, none with a fixed version | Debian 13 base packages (glibc, util-linux, systemd, perl, ...) for which Debian has published no fix. The digest is the newest `python:3.12-slim` build (checked with `docker buildx imagetools inspect`). Bump the digest when Debian ships fixes |
| web (`nginx-unprivileged:1.30.6-alpine3.24`) | 2, both fixed in Alpine: brotli-libs 1.2.0-r1 (decoder overflow, fixed 1.2.1-r0) and zlib 1.3.2-r0 (`gzwrite`/`gzprintf` overflow, fixed 1.3.2-r1) | none | 2 | Not reachable in this image: `ldd /usr/sbin/nginx` shows no brotli library (only freetype and libcurl need it), and nginx's gzip uses zlib's streaming deflate, not the `gz*` file API. The pinned digest is the newest build of the stable line, and the newer mainline build (1.31.7) ships the same versions. An `apk upgrade` step was tried, but the Alpine mirror is blocked in the build session, so it could not be built or verified here; bump the digest when upstream rebuilds |
| db (`postgres:16-alpine`) | 62, all in `gosu` (Go 1.24.6 standard library, `golang.org/x/sys` 0.1.0, `moby/sys/user` 0.1.0) | none | 62 | `gosu` runs once in the entrypoint to drop root and then execs Postgres; it never handles network input. In compose the database has no network at all (Unix socket only); the Fly database, built on the same digest, listens on the private network (section 7). The digest is the newest `postgres:16-alpine` build |

Image scanning was not in CI for v2; since the deployment branch CI scans
the API and web images with Trivy (section 7.4).

### 4.4 Static analysis (SR-7)

| Gate | Scope | Result |
|---|---|---|
| `bandit -r src scripts` | 12,369 lines | 0 findings. 6 reviewed suppressions: `subprocess` import and two `git` calls in `data/manifest.py` (absolute path from `shutil.which`, constant arguments), three Hugging Face loads in `ml/hf.py` (`local_files_only=True`, safetensors) |
| `semgrep scan` with local clones of the python, generic/secrets, dockerfile, docker-compose, javascript and typescript rule sets (the registry is blocked) | 388 files | 286 results, 0 security issues after triage. 187 `jsx-not-internationalized` and 23 `react-props-spreading` (audit rules: the app is English-only; spreading is the shadcn/ui component pattern). 56 `string-concat-in-list`: each was read; all are one sentence or table row wrapped over several lines, no missing comma. False positives: `useless-inner-function` (3, decorator-registered handlers), `return-not-in-function` (5, lambdas in a dict), `identical-is-comparison` (`status is None`), `missing-template-string-indicator` (4, already a template string), `prototype-pollution-loop` (read-only lookup over constant keys), `python37-compatibility-importlib2` (the target is 3.12), `detect-non-literal-regexp` (a test with constant names). Accepted: `insecure-hash-algorithm-sha1` (`usedforsecurity=False`, stable ids, not security), `dangerous-subprocess-use-audit` (the two `git` calls above), `prefer-json-notation` (the healthcheck needs a shell for `\|\|`) |

CI runs bandit on the same events.

### 4.5 Independent reviews

| Review | When | Result |
|---|---|---|
| Automated security review in four areas: four read-only reviewers (API, web and edge, supply chain and CI, data protection and ML), each finding then handed to a separate skeptic | 2026-10-24 23:48-00:18 UTC, 15 review passes | 82 checks recorded as sound; 11 candidate findings; 3 upheld, all low, all fixed: the Docker build fetched an unlocked build backend (now uv's own `uv_build`; an offline build with an empty cache succeeds, so building the package downloads nothing); Postgres `trust` authentication persisted in the volume (first documented, then removed by the socket-only database below); quotes named staff (name guard, ADR 006 E5). 8 rejected with the traced reason (for example: rate limits keyed on the raw path cannot be bypassed because nothing sets a `root_path`) |
| A separate automated reviewer going over `git diff main` plus untracked files to a structured security-review standard (concrete exploit path, high confidence only) | 2026-10-25, 6.5 minutes | Nothing exploitable. One accepted risk was understated, and confirmed here: a process on the Linux Docker host could reach Postgres on the "internal" network (`bash -c 'echo > /dev/tcp/<db ip>/5432'` succeeded) as the superuser `curator`, with `trust` for every host, and tampering would survive restarts because the seed skips a matching file digest. Fixed: the database has no network (`network_mode: none`, `listen_addresses=`) and the API connects through a Unix socket in a volume only the db and API containers mount (`tests/unit/test_compose_config.py::test_database_is_reachable_only_through_the_api_socket`; the stack ran healthy this way). One documentation mismatch fixed (the gitleaks allow-list note, section 4.1) |
| A structured security-review pass over the committed diff and the uncommitted files (context, comparison and assessment phases, then a false-positive filter per candidate), by another separate automated reviewer | 2026-10-25 (interrupted by a container restart and resumed) | No vulnerability reached the reporting bar (confidence 8 of 10). Three candidates scored and dropped: Postgres `trust` (2/10 on the current tree, where the database has no TCP listener; about 5/10 on the earlier internal-network version, which is why it was fixed), quotes naming staff (3/10: public data, names skipped or masked; its scan of all 31,485 served quotes found 0 e-mail addresses, 0 URLs and 0 phone-like strings), and the API port bypassing nginx's CSP (2/10: JSON only, `nosniff`, the docs page sends its own CSP) |
| Final automated verification of the whole diff (`main..v2` plus the working tree): five reviewers (scoring and API, data and models, web, documentation claims, build and CI), each followed by a separate skeptic who tried to refute every finding | 2026-10-25, 10 review passes, 63 minutes | 45 findings, 44 confirmed. The two high ones were the same defect: the CI gitleaks step scanned every fetched branch, so the backup branch's false positive would have failed every run (fixed: the step scans the history of the commit under test, section 4.1). No other finding was a security defect: the rest were wrong or stale documentation, user-facing wording that contradicted the tier rules, an untested monotonicity claim (praise can lower a tier when it brings an attribute, or a Weak essential's hard-label opinions, to the 10-opinion floor; documented as ADR 006 erratum E6, no rule changed), CI downloading 500 MB of unused Git LFS objects, CRLF-prone shell scripts, and test gaps. Fixes were made by three implementers on disjoint files and each checked by another skeptic; the eight regressions and one failing integration test those skeptics found were fixed in a second round and checked again; the served tiers, grades and numbers are unchanged (only 33 blocker texts changed wording) and `promotion.json` is byte-identical |

### 4.6 Red-team exercise

On 2026-10-26, four automated attacker teams attacked the running stack and
the code, each on one surface: the API; the web app and nginx; data, model
and privacy; containers, CI and supply chain. Every finding needed a working
proof of concept against the local stack, and a separate skeptic per team
re-ran it and judged it. This is an equivalent exercise, not the owner's own
red-team procedure (BLOCKERS B7).

| Finding | Reproduced | Verdict | Outcome |
|---|---|---|---|
| In the compose setup every host client reaches the API from the Docker bridge gateway address, so the "per-client" rate limit is one shared bucket | yes | real, low (ports bound to 127.0.0.1; correct when nginx sees real client addresses) | documented (section 5); the limiter is unchanged |
| The API's database role was the superuser: a foothold in the API container could run commands in the db container (`COPY ... PROGRAM`) or read server files | yes (`rolsuper` true) | real, low (needs a prior foothold in the API) | fixed: a non-superuser role owns the database; the socket admits only that role; the superuser connects only from inside the db container. Verified on the live stack: `COPY ... TO PROGRAM` and `pg_read_file` are refused, the superuser is refused from the API container, migrations and seed still run |
| A GET route accepts an oversized chunked body sent straight to `:8000` | yes | not exploitable: the body is discarded, memory and CPU stay flat, the reading route (`analyze`) is capped, nginx caps every body | SR-2 wording scoped |
| nginx's own error pages under `/api/` carry fewer security headers | yes | not exploitable: `default-src 'none'` and `frame-ancestors 'none'` are present and the pages are static | none |
| Serving does not re-check the package hashes in `promotion.json` | yes | not exploitable: anyone who can change the package can change `promotion.json` in the same tree | none; a signed record would be needed for real tamper resistance |
| The db container lacks some hardening (default capabilities, writable root filesystem) | yes | not exploitable on its own (no network) | `no-new-privileges` added to the db service |

The teams recorded 74 attacks the product resisted (API 22, web 16, data
and model 22, infrastructure 14), among them: SQL and `LIKE` injection,
path traversal, validation bypass with repeated or mistyped parameters,
deeply nested JSON, `X-Forwarded-For` spoofing (which cannot create new
buckets), request smuggling between nginx and uvicorn, stored XSS through
served review text, CSP bypasses, open redirects, nginx path tricks,
tier or quote poisoning through any request, prompt injection breaking the
labelling data block, personal data in served quotes, CI script injection,
and secrets in image layers.

## 5. Accepted risks

- **The demo database trusts its socket.** Postgres runs with no network
  and listens only on a Unix socket in a volume that only the db and API
  containers mount, so no password is needed or committed. Over the socket
  only the API's role may connect; it owns the `curator` database (to
  migrate and seed) but is not a superuser. The superuser connects only
  from inside the db container. Anyone who can already read Docker's
  volumes (root on the host) is beyond this protection. The public
  deployment uses passwords over TLS instead (section 7).
- **In the laptop compose setup the rate limit is shared by all clients.**
  This risk is now limited to compose. Docker publishes the ports on
  127.0.0.1 through its proxy, so every request reaches nginx from the
  bridge gateway address and every host client falls in one bucket (red
  team, section 4.6). On Fly each client gets its own bucket (ADR 011,
  client-IP trust chain; it rests on Fly and Cloudflare behaving as
  documented, assumptions A1, A8 and A13):
  - nginx overwrites `X-Real-IP` with `Fly-Client-IP` (Fly mode) or with
    `CF-Connecting-IP` from Cloudflare's ranges behind origin pulls
    (Cloudflare mode), and blanks every other identity header;
  - the API reads only `X-Real-IP`, only from peers in
    `CURATOR_FORWARDED_ALLOW_IPS`, and never `X-Forwarded-For`;
  - IPv6 clients are counted per /64.

  Tests: `tests/edge/test_nginx.py` (real nginx in all three modes) and
  `tests/api/test_api.py::test_clients_behind_the_proxy_get_separate_buckets`
  and `::test_forged_identity_headers_from_other_peers_are_ignored`.

  What remains: on Fly the API trusts `X-Real-IP` from any peer (`*`),
  because Fly's proxy sits between nginx and the API. So another app in
  the same Fly organisation could pick its own bucket. Never expose
  `:8000` directly.
- **Rate-limit counters are per process.** With several workers each
  enforces its own limit; a shared store (Redis) is a configuration change.
- **Health probes, docs and the OpenAPI schema are not rate-limited**
  (SR-3 above). They return public metadata or static files; a flood of them
  is a volumetric attack that no per-client limit stops.
- **Container base packages with no upstream fix** (section 4.3): 81 in the
  Debian base of the API image, 2 in the web image (not reachable), 62 in
  `gosu` in the Postgres image (not network-facing). Digests are the newest
  upstream builds; bumping them is the fix.
- **Data licence unconfirmed** (CC0 as declared, originally Booking.com
  content): a legal, not a technical, risk; recorded in `NOTICE`.

## 6. Not done

- The owner's own red-team procedure (BLOCKERS B7) was not run in the
  build environment; the equivalent exercise in section 4.6 was.
- **No dedicated inference pool.** The plan's abuse cases list "inference in
  a bounded thread pool"; `analyze` runs in the server's shared default
  thread pool (40 threads). What bounds its cost is the 30-per-minute
  limit, 2 parts of at most 2,000 characters each truncated to 128 tokens,
  and the 64 KB body limit. A per-route limiter would only change which
  route degrades under a flood.
- **Not in CI:** trufflehog, osv-scanner and semgrep ran locally for this
  review (section 4); CI runs gitleaks, pip-audit, bandit and npm audit. The
  deployment branch adds Trivy, workflow linting and Dependabot (section
  7.4).

## 7. The public deployment (Fly.io, then Cloudflare)

The branch `deploy/fly-cloudflare` takes the product from a laptop to the
internet: three Fly.io apps (the web app, the only public one; the API;
Postgres), a deploy workflow, and later a Cloudflare cutover. The design is
ADR 011; operations are in `docs/RUNBOOK.md`. Nothing was run against Fly
or Cloudflare from the build environment, which cannot reach them: what
depends on their behaviour is listed as assumptions A1 to A22 in ADR 011,
each with the check that confirms it at the first deploy. The tests named
here run in CI (`tests/edge` against real nginx and `tests/postgres`
against the real database image, in their own jobs).

### 7.1 What changes

- The site faces the internet, first at `hotel-curator.fly.dev` (Fly's
  edge terminates TLS), later on a domain behind Cloudflare (Cloudflare
  terminates TLS and reaches the origin with its client certificate).
- Secrets now exist (7.3).
- The database is no longer network-less: it listens on Fly's private
  network, behind TLS and passwords.
- Code reaches production from CI, through a workflow that holds deploy
  tokens.
- Visitors' addresses are logged by nginx for the rate limits.

### 7.2 Threats and controls

| Threat | Controls | Evidence |
|---|---|---|
| Forged client identity, to dodge or exhaust another visitor's rate limit (Fly mode) | nginx computes one client address (`Fly-Client-IP`, else the TCP peer) and writes it into `X-Real-IP`, blanking `X-Forwarded-For`, `Forwarded`, `Fly-Client-IP`, `CF-Connecting-IP` and `True-Client-IP`; the API reads only `X-Real-IP`, only one valid value, only from trusted peers; IPv6 per /64; uvicorn never rewrites the peer | `tests/edge/test_nginx.py::test_fly_mode_trusts_fly_client_ip_only`, `::test_local_mode_keys_on_the_peer_and_drops_forged_headers`, `tests/unit/test_client_ip.py::test_anything_but_one_valid_x_real_ip_means_the_peer`, `::test_ipv6_clients_share_a_bucket_per_64`, `tests/api/test_api.py::test_clients_behind_the_proxy_get_separate_buckets`, `::test_forged_identity_headers_from_other_peers_are_ignored`, `tests/api/test_serve.py::test_serve_never_lets_uvicorn_rewrite_the_client_or_log_on_its_own`. That Fly's edge replaces a client-sent `Fly-Client-IP` is assumption A1, and that Fly's proxy on the Flycast hop passes nginx's `X-Real-IP` unchanged is A13 |
| Bypassing Cloudflare by reaching the origin directly (Cloudflare mode) | Fly passes TCP 443 through to nginx, which refuses the handshake for any other name (so `hotel-curator.fly.dev` serves nothing), answers 400 without Cloudflare's client certificate, 403 when the PROXY source is outside Cloudflare's committed ranges and 421 for another `Host`; port 8080 answers only `/healthz`; a weekly workflow fails when Cloudflare's ranges change; `scripts/check_edge.py` probes every origin address after the cutover | `tests/edge/test_nginx.py::test_cloudflare_mode_refuses_everything_else`, `::test_cloudflare_mode_trusts_cf_connecting_ip_from_cloudflare`, `::test_cloudflare_mode_health_port_serves_nothing_else`, `::test_check_edge_sees_the_cloudflare_origin_locked`, `tests/unit/test_check_edge.py::test_every_address_must_refuse`, `::test_an_open_unreached_or_unproven_origin_fails`; assumptions A7, A8, A14 |
| Reaching the API or the database from the internet | neither has a public address: deploys pass `--no-public-ips`, the API has only a Flycast service, the database none; `make fly-status` fails if either lists a public address | `tests/unit/test_fly_config.py::test_api_is_private_warm_and_built_with_checked_weights`, `::test_database_is_private_and_keeps_its_data_on_the_volume`, `tests/unit/test_deploy_scripts.py::test_plan_deploys_private_apps_without_public_addresses`, `::test_status_fails_when_a_private_app_has_a_public_address`; assumption A3 |
| Database access and privilege | TLS required; `hostssl` with scram for exactly two roles, neither a superuser; the serving role can only read; the superuser only by local peer; replication refused; the app roles reach only their database; production settings refuse any network URL without `sslmode=require` or stricter (a Unix socket is also accepted); the owner role's URL is removed from the server's environment; releases are serialised by an advisory lock | `tests/postgres/test_release.py::test_connections_use_tls_and_plain_ones_are_refused`, `::test_the_superuser_cannot_connect_over_the_network`, `::test_nobody_gets_a_replication_connection`, `::test_the_roles_reach_only_their_database`, `::test_neither_role_is_a_superuser`, `::test_serving_role_reads_but_cannot_write`, `::test_the_release_lock_is_held_until_the_release_ends`, `tests/unit/test_postgres_init.py::test_pg_hba_admits_exactly_two_roles_over_tls`, `tests/unit/test_config.py::test_production_refuses_plain_or_sqlite_urls`, `tests/unit/test_api_entrypoint.py::test_the_server_never_inherits_the_owner_url` |
| Secrets leaking through the repository, images, command lines or logs | 7.3 | 7.3 |
| Abuse of the deploy pipeline or its tokens | the deploy workflow runs only when CI calls it, for a push to `main` that passed every job; in the GitHub environment `production`, whose reviewer (the owner) approves each run; only for `main`'s newest commit; one app-scoped token per app, reaching only its own steps; read-only `GITHUB_TOKEN`; no `pull_request_target` or `workflow_run`; no expression pasted into a script; actions pinned by SHA; flyctl from an image pinned by digest whose version is checked first, the token passed into it by name only | `tests/unit/test_workflows.py::test_deploy_runs_only_from_ci_for_main_in_the_production_environment`, `::test_ci_deploys_after_every_other_job_on_a_push_to_main`, `::test_deploy_steps_run_in_order_only_for_mains_newest_commit`, `::test_each_token_reaches_only_the_step_that_uses_it`, `::test_workflows_read_only_and_never_run_untrusted_triggers`, `::test_no_expression_is_pasted_into_a_shell_script`, `::test_every_action_is_local_or_pinned_to_a_commit`, `::test_the_wrapper_runs_flyctl_with_the_token_by_name_only`, `::test_the_wrapper_refuses_unpinned_images_and_outside_paths`; the reviewer and branch rule are repository settings (`docs/deploy/fly.md`); assumptions A15, A16, A21 |
| A vulnerable or tampered dependency, image or tool | Trivy scans the API and web images in CI (a fixable CRITICAL fails the job); actionlint and zizmor lint the workflows; Dependabot proposes weekly updates with a 14-day cooldown for actions, Python, npm, base images and the tool images; checkouts keep no credentials unless they pull Git LFS or check `main`'s head, and `.git` never enters a build context | `tests/unit/test_workflows.py::test_the_trivy_step_runs_the_pinned_scanner_on_both_images`, `::test_tool_images_are_pinned_by_digest_where_dependabot_sees_them`, `::test_dependabot_covers_every_ecosystem_and_the_tool_images`; `.dockerignore` |
| A deploy with missing or substituted model weights | every Fly build of the API runs `curator model verify-served` against the hashes `promotion.json` judged; `scripts/deploy.py` refuses Git LFS pointers before uploading; the smoke test requires the model | `tests/unit/test_verify_served.py::test_api_image_checks_the_weights_when_required`, `::test_wrong_model_file_is_reported`, `tests/unit/test_deploy_scripts.py::test_weights_check_finds_pointers_and_missing_models`, `tests/unit/test_smoke.py::test_one_broken_thing_fails_its_own_check` |
| A stale or wrong release reaching visitors | only `main`'s newest commit deploys; a dry run of both deploys refuses missing weights or, with no mode variable set, a parked web app or a web deploy that would quietly leave Cloudflare mode or a recorded domain, before anything moves; migrations are additive, so a rollback keeps working; a failed smoke test is visible and rollback is documented | `tests/unit/test_workflows.py::test_deploy_steps_run_in_order_only_for_mains_newest_commit`, `tests/unit/test_deploy_scripts.py::test_a_refusal_comes_before_any_deploy`, `::test_a_redeploy_keeps_cloudflare_mode_unless_told_otherwise`, `::test_a_parked_web_app_needs_its_mode_named`, `tests/unit/test_smoke.py`; `docs/deploy/fly.md` ("Deploying again") |
| Visitors' data in logs, error reports or metrics | nginx's access log has the path without its query string and only id-shaped provider ids, and its error log keeps only critical errors (nginx's error messages quote the request line, query string included, so a critical one can still carry search terms: 7.5); the API logs no client address and no submitted text, and its database engines hide bound values from error messages; Sentry is off unless a DSN is set, and then reports only unexpected 500s with no integrations, breadcrumbs, local variables or request data; metric labels are a fixed method set, route templates and statuses | `tests/edge/test_nginx.py::test_the_access_log_is_json_with_fly_and_cloudflare_ids`, `::test_no_nginx_log_holds_a_query_string_even_when_the_api_is_down`, `tests/api/test_serve.py::test_serve_logs_only_json_and_finishes_a_request_on_sigterm`, `tests/api/test_observability.py::test_a_failed_search_logs_and_reports_no_search_terms`, `::test_an_unexpected_error_is_reported_without_request_data`, `::test_requests_are_counted_by_route_template_with_bounded_labels`, `tests/unit/test_logging.py` |
| The metrics endpoint as a way in or a leak | a listener of its own (port 9091) that no Fly service exposes and nginx never proxies; nothing but counters, with no request data | `tests/unit/test_fly_config.py::test_api_metrics_are_scraped_from_a_port_no_service_exposes`, `tests/api/test_observability.py::test_metrics_have_their_own_listener_that_stops_with_the_app`; assumptions A10, A18 |
| Running up cost, or flooding `analyze` | the v2 rate limits (120 a minute per client on `/v1`, 30 on `analyze`) now see real clients; one machine per app, with request concurrency limits; a WAF rate rule on `analyze` after the cutover; `make fly-stop` parks the site | `tests/api/test_api.py::test_rate_limits`, `tests/unit/test_ops.py`; the WAF rule is a dashboard setting (`docs/deploy/cloudflare.md`) |
| Licence exposure from serving third-party data, models and code | `NOTICE` (served at `/NOTICE.txt`) states each source's licence or that it is unverified, the model weights' licence status and the site's terms; the licences of the bundled packages and of the build tools that add code are served at `/THIRD-PARTY-NOTICES.txt` (UTF-8, so labelled), generated from the lock file, and a test fails when the app imports a package it does not list; the smoke test checks both files are served | `web/test/public-files.test.ts`, `tests/edge/test_nginx.py::test_text_is_served_as_utf8_and_api_answers_keep_their_type`, `tests/unit/test_smoke.py::test_one_broken_thing_fails_its_own_check`, `::test_a_missing_third_party_file_fails_the_attribution_check` |

### 7.3 Secrets

| Secret | Where it lives | How it is kept out of sight |
|---|---|---|
| Three database passwords; the serving and owner database URLs | Fly secrets of the database and API apps | written by `scripts/push_fly_secrets.py` to a git-ignored `.env.fly` created with mode 0600, checked for consistency, sent to `flyctl secrets import` on stdin, never printed; settings hold the URLs as secrets and errors name the setting, not the URL; the owner URL is unset before the server starts |
| Cloudflare's origin certificate key (with its certificate and the origin-pull CA) | Fly secrets of the web app, Cloudflare mode only | added by `push_fly_secrets.py origin-tls`, which checks the files as nginx will load them; written at start to a 0700 directory as 0600 files, never echoed |
| Two deploy tokens, one per app, 90 days | the GitHub environment `production` | piped from `flyctl tokens create` into `gh secret set`, never on screen; each reaches only its own workflow steps; flyctl gets it by name |
| A Sentry DSN (optional) | a Fly secret of the API | checked for shape and never quoted; held as a secret setting |

`.env` and `.env.*` are git-ignored and excluded from both image build
contexts. Evidence: `tests/unit/test_deploy_scripts.py::test_push_sends_values_on_stdin_and_never_prints_them`,
`::test_init_writes_a_private_file_once`, `::test_a_mangled_url_is_reported_without_its_contents`,
`::test_a_malformed_dsn_is_refused_without_being_quoted`,
`::test_origin_tls_refuses_swapped_or_partial_files`,
`tests/edge/test_nginx.py::test_cloudflare_secrets_are_written_privately_and_never_printed`,
`tests/unit/test_config.py::test_database_urls_and_the_dsn_never_appear_in_repr`,
`tests/unit/test_workflows.py::test_each_token_reaches_only_the_step_that_uses_it`.

### 7.4 Gate results

The gates ran on 2026-10-29 between 04:51 and 04:53 UTC on the branch's
code at `fe860a0`. The OSV database was downloaded fresh that morning (MD5
checked). This section's commit also changes code: the CI image-scan step
(`.github/workflows/ci.yml`), nginx's error log and charset
(`web/nginx/http.conf`), the smoke test (`scripts/smoke.py`), the model
card generator (`src/hotel_curator/ml/reports.py`), the web app's footer
and a script that writes its notices file (`web/src`, `web/scripts`,
`web/package.json`). So bandit, actionlint with shellcheck and zizmor ran again on the final
tree before committing (results below; bandit then counted 14,846 lines),
gitleaks scanned the staged changes, and the dependency gates stand
because no lock file changed.

| Gate | Scope | Result |
|---|---|---|
| `gitleaks git` | the branch's 9 commits; all 19 commits on every ref; the tree | 0 findings each |
| `trufflehog git` and `filesystem` | every branch; the tree | 0 verified; 6 unverified in each, the same 6 and all false positives: the SHA-256 of the `sentry-sdk` wheel in `uv.lock` (matched as a Sentry token), and placeholder credentials in test fixtures (`u:p`, `o:secret-pw`, `o:hunter2` in `tests/unit/test_config.py`; `user:pass` in `web/test/seo.test.ts`), there to test URL handling and redaction |
| `osv-scanner scan source --offline` | `uv.lock` (195 packages), `web/package-lock.json` (587 packages) | no known vulnerabilities |
| `pip-audit` 2.10.0 `--require-hashes` | 190 locked Python packages | 0 known vulnerabilities; the spaCy model `en-core-web-sm` is not auditable (installed from a pinned URL) |
| `npm audit` | 600 packages (62 production) | 0 of any severity |
| `bandit` 1.9.4 | `src/` and `scripts/` (14,812 lines; 14,846 on the final tree) | 0 findings each time; the scripts' reviewed suppressions are the `subprocess` calls that run flyctl with arguments they build, `scripts/check_edge.py`'s certificate-less probes (they test whether the origin refuses), and `scripts/smoke.py`'s URL open (its base address admits only https, or http on the local machine) |
| actionlint 1.7.12 (with shellcheck 0.11.0), zizmor 1.31.1 offline, medium and above | `.github/workflows`, at `fe860a0` and on the final tree | 0 findings each time |

Not re-run for this section: semgrep (its rule sets are unreachable from
the build environment) and the image scans, which run in CI with Trivy;
no Docker daemon was available here.

Preparing this section found three defects, all fixed in the same commit:
the CI image scan never scanned anything (the scanner's image and the
image being scanned shared a shell variable, so each call ran the scanned
image instead of Trivy, which would have failed the containers job and so
every deploy; no CI run had reached it yet, since CI runs on pull requests
and on pushes to `main`; `tests/unit/test_workflows.py::test_the_trivy_step_runs_the_pinned_scanner_on_both_images`
now runs the step's own script and fails on the old one); nginx's error
log, left at the base image's default, quoted the request line of every
failed request, search terms included, whenever the API did not answer
(`tests/edge/test_nginx.py::test_no_nginx_log_holds_a_query_string_even_when_the_api_is_down`
reproduced it; the error log now keeps only critical errors); and no test
covered the Sentry DSN staying out of the settings' text form, or
`curator serve` keeping uvicorn from rewriting the client address (both
added).

### 7.5 Residual risks

- **Unverified provider behaviour.** Every Fly and Cloudflare behaviour the
  controls rely on is an assumption (A1 to A22) until its first-deploy
  check passes.
- **The Fly organisation is a trust boundary.** Any app in the same
  organisation can reach the API (and choose its own rate-limit bucket and
  request id, since the API trusts `X-Real-IP` from any peer), the
  metrics listener (no authentication), nginx's port 8080 directly (and
  set its own `Fly-Client-IP`), and Postgres on 5432 (where it still needs
  a role's 32-byte random password). Keep the organisation to these three
  apps, or give them one of their own.
- **The database's identity is not verified.** The API connects with
  `sslmode=require`, which encrypts but does not check the self-signed
  server certificate; traffic between Fly hosts is assumed encrypted (A12).
- **The owner role's URL reaches every API machine.** Fly gives every
  secret to every machine of an app; the entrypoint keeps it out of the
  server's environment, but Fly's in-machine API and `ssh console` still
  reach it. A separate release app holding only that secret would close
  this.
- **Compose-only hardening is absent on Fly:** the API's root filesystem is
  writable (SQLite is refused in production, so nothing writes outside
  `/tmp`), and there is no `no-new-privileges` (each machine is its own
  microVM). Non-root users and pinned images remain.
- **Zone-level origin pulls prove "came through Cloudflare", not "came
  through this zone".** The name and `Host` checks narrow this;
  per-hostname origin pulls with the owner's own certificate would close
  it.
- **Fail-safe shared buckets.** If the address chain breaks (Cloudflare
  sends no `CF-Connecting-IP`, or a rollback to Fly mode while Cloudflare
  still proxies), visitors share one rate-limit bucket until it is fixed.
  When Cloudflare starts using a new address range, visitors routed
  through it get 403 until `web/nginx/cloudflare-ips.conf` is updated and
  the web app redeployed; the weekly range check flags the change.
- **Tampered rows survive a release.** The release skips loading when the
  committed file's digest is already recorded, so rows changed directly in
  the database (by someone holding the owner role's password) stay until
  the volume is rebuilt or the data reloaded.
- **Fly's HTTP proxy sits on the Flycast hop** between nginx and the API.
  The compose red team found no request smuggling between nginx and
  uvicorn; that hop on Fly has not been tested.
- **Checks that stay manual.** The deploy workflow does not re-check that
  the API and database have no public address (its tokens are scoped to
  one app each; `make fly-status` does). The `production` environment's
  reviewer and branch rule, and every Cloudflare setting, live outside the
  repository.
- **Scan coverage.** CI's image scan covers the images CI builds; Fly's
  remote builder makes its own from the same Dockerfiles and pinned
  digests, and the Fly database image and the flyctl image are not
  scanned. The edge tests run the site templates on the CI runner's nginx,
  not inside the shipped web image, which CI starts only in local mode.
- **Visitors' addresses** are in nginx's access log, kept as long as Fly's
  log view keeps it. nginx's critical errors (rare: a failed file or
  temporary-file write while serving a request, for example) quote the
  request line with its query string, so one could carry search terms;
  the edge test covers only the common case, an unanswered API. When Sentry is on, a report carries the exception's
  message, which no longer includes SQL parameters.
- **Data at rest:** the repository does not address encryption of the
  database volume or its snapshots; everything on it can be rebuilt from
  git.
- The v2 risks in section 5 still apply: per-process rate-limit counters
  (one API machine), unlimited probes and docs, base packages with no
  upstream fix, the unconfirmed data licence. HSTS stays at one day until
  the domain is settled.

### 7.6 Not done yet

- A red-team exercise against the deployment's code, before the branch's
  pull request (the owner's own procedure, BLOCKERS B7, also remains).
- The first-deploy checks of A1 to A22, and the owner's settings (BLOCKERS
  B9).
