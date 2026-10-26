# Security review: hotel-curator v2

_Status: final for the v2 pull request. Gates ran on 2026-10-26 against the
tree of the quality-and-security commit; every claim below names the
command or test that backs it. A red-team exercise ran on 2026-10-26
(section 4.6); the owner's own red-team procedure remains available._

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
| Secrets | none are needed; the goal is to keep it that way |

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
| Clickjacking, MIME sniffing | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`; nginx adds a strict CSP | `::test_security_headers_and_request_id`, `web/nginx.conf` |
| Malicious model files | weights only as safetensors or ONNX; `torch.load(weights_only=True)` for the one pickle conversion; `local_files_only=True`; no `trust_remote_code` | `tests/unit/test_security_invariants.py`, `tests/unit/test_bases.py::test_convert_refuses_pickled_code` |
| Tampered downloads | HTTPS only, SHA-256 pins on every data file and weight file, Docker layers by digest; tar extraction reads only named regular files | `tests/unit/test_download.py`, `tests/unit/test_bases.py` |
| Prompt injection into the labelling LLM | review text in a delimited block, explicit "data, not instructions", closing delimiter neutralised; no LLM at serving time | `tests/unit/test_prompt.py` |
| Tier poisoning | `analyze` never writes; tiers come only from the offline pipeline | architecture; `routes/analyze.py` |
| De-anonymising reviewers, naming staff | quotes show month and traveler type only; parts that probably name a person are not quoted (a person-introducing word before a corpus-rare word, or a rare capitalised word in a part about staff; ADR 006 E5), names spaCy finds in the rest are masked, long digit runs too; nationality, room type and nights never served. Best effort: on the 31,485 served quotes none of the staff names the independent review found remains, and a manual check of 90 quotes with rare capitalised words found no staff name (checked on the run with a fixed 500-part cut-off, 8 quotes different from the final one); some residual risk stays | `tests/unit/test_names.py` (real corpus parts the NER missed), `tests/unit/test_explain_evidence.py::test_a_part_naming_someone_is_replaced_by_the_next`, `::test_redactor_masks_names_numbers_and_emails`, `tests/unit/test_security_invariants.py::test_sr9_*` |
| Container breakout / privilege | non-root users, pinned base images by digest, healthchecks, read-only API filesystem, `no-new-privileges` (API, web and db), a database with no network at all (Unix socket shared only with the API), and an API database role that is not a superuser, so a foothold in the API cannot run `COPY ... PROGRAM` or read server files in the db container | `Dockerfile`, `web/Dockerfile`, `docker-compose.yml`, `docker/db-init/10-app-role.sh`, `tests/unit/test_compose_config.py` |

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
| SR-12 | Non-root, pinned, healthchecked containers | met | `docker inspect`: API user `curator` (uid 10001) with a `/healthz` healthcheck, web user `101` with a `wget /` healthcheck; every base image pinned by digest |
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
| db (`postgres:16-alpine`) | 62, all in `gosu` (Go 1.24.6 standard library, `golang.org/x/sys` 0.1.0, `moby/sys/user` 0.1.0) | none | 62 | `gosu` runs once in the entrypoint to drop root and then execs Postgres; it never handles network input. The database has no network at all (Unix socket only). The digest is the newest `postgres:16-alpine` build |

Image scanning is not in CI (section 6).

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
  volumes (root on the host) is beyond this protection. A real deployment
  points `CURATOR_DATABASE_URL` at its own database and credentials.
- **In the laptop compose setup the rate limit is shared by all clients.**
  Docker publishes the ports on 127.0.0.1 through its proxy, so every
  request reaches nginx and the API from the bridge gateway address and
  every host client falls in one bucket (red team, section 4.6). The
  limiter itself is per client: deployed with nginx as the entry point and
  a front end that preserves client addresses (and
  `CURATOR_FORWARDED_ALLOW_IPS` set to that proxy), each client gets its
  own bucket. Never expose `:8000` directly.
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
- **Not in CI:** trufflehog, osv-scanner, semgrep and the image scan ran
  locally for this review (section 4); CI runs gitleaks, pip-audit, bandit
  and npm audit. No Dependabot or Renovate configuration.
