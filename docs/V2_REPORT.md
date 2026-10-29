# hotel-curator v2: final report

_Branch `v2`, pull request
[amit-badave-04/hotel-curator#1](https://github.com/amit-badave-04/hotel-curator/pull/1)
(merged into `main`; the public deployment that followed is planned in
`docs/deploy/gap-audit.md`). Every number below
comes from a command that was run; the command or the file that records it
is named next to it. Anything not done or not verified says so._

## 1. What v2 is

v1 was a notebook prototype: synthetic data, an untested ranking formula and
a classifier scored on labels it had been tuned on (`docs/v1-audit.md`). v2
is a product built on real data. A classifier labels each part of 515,212
Booking.com reviews ("Liked" and "Disliked" texts) with praise or complaints
about eight attributes; per hotel, those labels become counts, grades with
stated certainty, and a tier with its reasons, its evidence and what would
change it. An API and a web app serve the results; `docker compose up`
starts the whole product.

## 2. Delivered

| Area | Delivered | Evidence |
|---|---|---|
| Data | Booking.com 515K European hotel reviews from a pinned public mirror (SHA-256), canonical `hotels` and `reviews` tables with pandera contracts, hotel-disjoint train/validation/test splits, manifest, data card, EDA | `curator data build`: 515,738 raw rows, 515,212 reviews, 1,494 hotels (1,048/220/226); rebuild byte-identical; 47 s with cached downloads (`docs/data/DATA_CARD.md`) |
| Labels | Written guideline; a panel of three LLM annotators; gold-test 1,200 and gold-dev 594 reviews (majority of three), training sets of 4,006 and 4,000 reviews (one annotator); agreement and an external check against human annotators; a 100-review human audit sheet for the owner | Fleiss' kappa 0.943-0.997 per label (`docs/labeling/AGREEMENT.md`, `EXTERNAL_CHECK.md`) |
| Models | 14 rungs scored by one evaluation harness: v1 checkpoint in four configurations, TF-IDF + logistic regression and LightGBM, bge-small embeddings + logistic regression, fine-tuned ELECTRA-small, mmBERT-small and roberta-base; calibration; a promotion rule written before results; ONNX packaging with int8 only if it loses at most 0.005 dev macro-F1 | `docs/model/COMPARISON.md`, `artifacts/evals/promotion.json`, model card, `docs/model/ERRORS.md` |
| Tiers | Pre-registered scheme (ADR 006): Beta posteriors, grades only at 95% certainty, essentials cleanliness/room/staff; explanations, quotes for and against (with a name guard), trends, traveller segments, what would change the tier, comparisons | `docs/model/TIERS.md` |
| Backend | FastAPI, SQLAlchemy 2, Alembic (Postgres or SQLite), typed settings, structured logs, request ids, security headers, body limits, rate limits, one error schema, OpenAPI, self-hosted Swagger UI | `tests/api/` |
| Frontend | React 19 + TypeScript + Vite + Tailwind: explorer, hotel page, compare, about and "try the model"; light and dark themes; keyboard and screen-reader support | `web/`; 74 unit tests, 37 end-to-end and accessibility checks |
| Operations | `docker compose up --build` (Postgres, API, nginx web), Makefile, CI on pushes to `main` and `v2` and on every pull request, `.env.example`, `uv.lock`, `environment.yml` | `README.md`, `.github/workflows/ci.yml` |
| Security | Requirements SR-1..SR-14 with tests, threat table, secret, dependency, SAST and container-image scans, two independent reviews | `docs/security/v2-security-review.md` |
| Architecture | Offline pipeline (stages, commands, artifacts, reproducibility), the committed hand-off, online deployment with trust boundaries, start-up, API surface, request flows, CI, system context, code modules | `docs/architecture/` (README, offline.md, online.md in Mermaid; index.html with inline SVG) |

## 3. Headline results

| Result | Value | Source |
|---|---|---|
| Promoted classifier | `roberta-base-ft-train2` (roberta-base fine-tuned on 8,006 labelled reviews; 4,596 s of training on 4 CPU threads) | `curator model compare` |
| Gold-test review macro-F1 (1,200 reviews, 182 hotels no model saw), 95% interval | 0.922 [0.911, 0.932] | `docs/model/COMPARISON.md` |
| v1 checkpoint on the same reviews (its best configuration) | 0.687 [0.668, 0.705] | same |
| Paired difference (hotel-clustered bootstrap) | +0.236 [+0.216, +0.255] | same |
| Calibration, mean review-level ECE | 0.010 (v1: 0.029) | same |
| Served package (ONNX int8) | gold-test 0.920 [0.909, 0.930], ECE 0.012; int8 lost 0.00188 dev macro-F1 | same, "Packaged models" |
| Throughput | 224 parts/s on 4 threads in a 20,000-part benchmark (corpus estimated at 1.07 h); measured full run: 865,732 parts in 5,987 s (1.66 h) | `throughput.json`, `curator score parts` |
| Tiers (1,494 hotels) | Good 58, Fair 960, Caution 271, not enough evidence 129, not enough reviews 76, Excellent 0 | `curator score hotels`, `docs/model/TIERS.md` |
| Out of time (2015-2016 grades vs 2017 reviews) | Spearman 0.43 (wifi) to 0.72 (room), all 95% intervals above 0; 2017 mean site score by 2016 tier: Caution 7.81 < Fair 8.52 < Good 9.11 | `docs/model/TIERS.md` §4 |
| Stability | a hotel keeps its tier in 88% of resampling replicates | §6 |
| False claims in simulation | at most 2.8% (target 5%) | §7 |
| Agreement with the site's own average score | v2 tiers 0.685; the v1 formula on v2 labels 0.671; share of reviews with nothing disliked 0.749 | §5 |

**Why no hotel is Excellent.** Excellent needs all three essentials
(cleanliness, room, staff) graded Strong, and Strong needs 95% certainty that
at least 80% of a hotel's opinions on the attribute are praise. Cleanliness is
Strong for 121 hotels, staff for 354, both for 64, and the room for none: the
closest room is 0.941 certain (TIERS.md, tier mix). Booking.com asks every
guest what they disliked, and the room collects most complaints. The lines
were registered before any hotel was scored and were not moved; TIERS.md §8
shows that Excellent stays at 0 even with a 75% line.

**Agreement with the site's score is not the goal.** A simple "nothing
disliked" share tracks the site's average score more closely (0.749) than
the tiers do (0.685). The tiers answer a different question (which
attributes are confirmed strong or weak, with what certainty), and their
validation is the out-of-time and simulation checks above.

## 4. Quality and security gates

| Gate | Result |
|---|---|
| Python tests (`pytest --cov`) | Final tree, without the weights as in a fresh clone: 399 passed, 2 skipped (the two eval gates that need the weights; next row), coverage 90%; with the weights present locally, 401 passed |
| Model evaluation gates (`pytest -m eval`) | 3 passed with the weights present; 1 passed, 2 skipped in a checkout without them (B8) |
| ruff (lint and format, incl. notebooks), mypy strict (89 files) | clean |
| Web: eslint, tsc, vitest, vite build, prettier | clean; 74 unit tests passed |
| End-to-end and accessibility (Playwright + axe, light and dark) | 37 passed (17 journeys, 20 axe checks, no serious or critical violations) |
| `docker compose up` | db, api, web healthy in 24-36 s across the runs; search, hotel pages, compare and docs served; analyze answers 503 without weights |
| CI on GitHub | Two runs on the M6 commit `d51988d`. The push run 40391731894: security, web and containers passed; python failed one test, `test_committed_e2e_data_is_current`, because 6 floats in the e2e fixture differed from the runner's by at most 59 units in the last place (relative 7.6e-15; every string, tier and count matched), so e2e, which needs python, was skipped. numpy picks a CPU-specific SIMD version of `exp` and `log1p`; regenerating the fixture here with numpy's AVX-512 paths disabled (`NPY_DISABLE_CPU_FEATURES="X86_V4 AVX512_ICL AVX512_SPR"`) reproduces exactly those 6 values. The pull request run 40392302682 on the same commit passed all five jobs (python 398 passed, 2 skipped, coverage 89.8%; e2e 37 passed), so that runner computed the same values as this machine. This commit compares the fixture's floats with `math.isclose` (relative 1e-9, absolute 1e-12) and adds a test that real changes still fail; with AVX-512 disabled the old check fails and the new one passes; its own CI run is on the pull request |
| Secrets: gitleaks (PR range, all refs, tree), trufflehog (history, tree) | 0 findings in the pull request; 1 false positive (a file hash) on a backup branch only, which has since been deleted |
| Dependencies: pip-audit (188), osv-scanner (780 locked packages), npm audit (600) | 0 known vulnerabilities |
| SAST: bandit, semgrep | 0 findings / 286 results triaged, 0 security issues |
| Container images (osv-scanner) | pip removed from the API image (12 fixable advisories); remaining base-package advisories have no upstream fix or are not reachable (security review §4.3) |
| Independent automated reviews | four: a four-area security review with a skeptic per finding (3 low findings, fixed); a separate security review (Postgres moved off the network); a structured security-review pass (nothing reportable); a verification of the whole diff by five reviewers and five skeptics (44 confirmed findings, each fixed or documented, in two rounds) |
| Red-team exercise (four attacker teams, live proofs of concept, a skeptic each) | 6 findings reproduced, 2 confirmed low: the API's database role was a superuser (fixed: a non-superuser role owns the database), and the compose setup puts all host clients in one rate-limit bucket (documented); 74 attacks resisted (security review §4.6) |

## 5. Not done, not verified, and deviations

Not done:

- Overture Maps display enrichment (ADR 001, priority P2): not built; 17
  hotels without coordinates stay without, and there are no website links.
- Out-of-domain evaluation on Booking.com 2023 reviews (ADR 001, P3): not
  done.
- ELECTRA-small and mmBERT-small were not retrained on the second training
  set (CPU time); only TF-IDF, embeddings and roberta-base were.
- No modern encoder beyond mmBERT-small (ModernBERT-base, DeBERTa-v3):
  huggingface.co is blocked (B1).
- Hysteresis for trend alerts (ADR 006 erratum E3) is described, not
  implemented; trends compare the two windows alone.
- A dedicated inference thread pool (plan §7.2 abuse cases): not built;
  rate limits and size caps bound `analyze` instead.
- trufflehog, osv-scanner, semgrep and the image scan are not in CI; they
  ran locally for the security review. (The deployment branch later added
  a Trivy image scan, workflow linting and Dependabot to CI.)
- The owner's own `/red-team` procedure (B7) was not run here; an
  equivalent red-team exercise was (section 4).

Not verified in this environment:

- The promoted model's weights are not pushed (B8): `lfs.github.com` is
  blocked. A fresh clone cannot run `POST /v1/analyze` or the two eval gates
  that need the file until the owner adds it.
- Human ground truth: gold labels are an LLM panel's majority, checked
  against human annotators on an external set; the 100-review human audit
  is prepared but needs a person (B3).

Deviations and disclosures:

- **Rate limits** cover every `/v1` route; health probes, docs and the
  OpenAPI schema are deliberately not limited (the plan said "all
  endpoints").
- **Tie-break timing.** The promotion rule breaks near-ties by size. The
  note that reports when measured speed would pick differently was added at
  20:10 UTC on 2026-10-24, after roberta-base-ft and mmbert-small-ft dev
  scores were known (0.9195 vs 0.9157). It changes no decision, and no tie
  occurred for the final winner (next best dev 0.9195 vs 0.936).
- **Package safeguard.** Rules 1 and 3 are re-checked on the shipped ONNX
  package's own gold-test scores ("package_holds"). This was added at 21:01
  UTC, before any package existed; it can only block a promotion.
- **Threshold ties.** Each tuned threshold equals one gold-dev score
  exactly (16 ties per rung), so the fp32 ONNX packages' differences of at
  most 1e-5 flip 6-10 tied gold-dev part decisions (ELECTRA 9, mmBERT 6,
  roberta-base 10); gold-test has no ties, so the fp32 packages flip none
  there. The int8 package's quantisation differences are reported
  separately in COMPARISON.md. Thresholds were not re-tuned.
- **Name guard measurements.** Parts that probably name a person are not
  quoted: 0 of the staff names the security review found remain in the
  31,485 served quotes; the guard skips 6.1% of parts; 96 of 11,104 graded
  attributes have no quote. A manual check of 90 candidates found no staff
  name; it ran with an earlier, fixed 500-part cut-off, so 8 of the final
  quotes differ from the checked run.
- **History, M3.** The M3 commit message says no hotel's room praise share
  reaches the 80% line. The posterior mean does exceed 0.80 for 6 hotels (room `mean` >= 0.80
  in `data/demo/hotel_results.json.gz`);
  what none reaches is the 95% certainty Strong requires (closest 0.941). The
  README, plan and TIERS.md state it correctly.
- **History, M5.** The M5 commit message lists end-to-end tests in CI, but its
  CI file lacked that job (the staging step that held back the security job
  also removed it). The M6 commit adds both.
- **History, rewrite.** On 2026-10-26, before the pull request, the
  milestone commits were rewritten once: commit metadata (author and message
  trailers) and wording in documentation and two docstrings
  (`labeling/panel.py`, `scripts/collect_panel_answers.py`). No behaviour,
  data or test changed; a script checked that each new commit differs from
  the old one only in those files. The manifests' `code_commit` fields (M0,
  M1, M2 and M5) and the first CI run (40149142437, M5) name commits as they
  were before the rewrite. Old to new: `8029999` -> `1a79b39` (M0), `ccdd0b7`
  -> `f8ce452` (M1), `6ca3973` -> `6c8ede0` (M2), `771c6c3` -> `fb933e6`
  (M3), `a462966` -> `2647ee2` (M4), `806332b` -> `24606cb` (M5). The
  manifests' `-dirty` suffix means the stage ran from that commit with the
  next milestone's work not yet committed.

## 6. Issues found and fixed during the build

- mmBERT-small loaded with random token embeddings (its tied embedding is
  stored only as `decoder.weight`); caught one minute into training,
  restored from the tied copy, and any other missing pretrained weight now
  raises (`tests/unit/test_hf.py`).
- mmBERT ONNX export failed with `return_dict=False`; exported through a
  logits-only wrapper (regression test with a tiny ModernBERT).
- Rung names ignored the training set and would have overwritten results;
  fixed before any second-set run. After review, encoder rung names also
  carry every non-default setting (`-frac0.5`, `-seed7`, `-lr2e-05`,
  `-ep3`), so a learning-curve or hyperparameter run without `--name` cannot
  overwrite a rung either; every committed encoder rung used the defaults
  and keeps its name.
- After `curator model reassess`, compare could judge an old package with
  new thresholds (found by an independent reviewer and reproduced); packages
  now record the rules and file hashes they embed, stale packages are not
  judged, and publish refuses unjudged files.
- Calibration-in-the-large was computed on the wrong stratum; fixed with
  sampling weights (headline metrics unchanged).
- The out-of-time validation test was rebuilt so that all seven window
  mutants fail it.
- Quotes named hotel staff (security review); the name guard was added
  before the NER mask.
- A backup snapshot was pushed although gitleaks reported a finding (the
  script used `;` instead of `&&`); the finding was a file hash, the report
  format was changed, and backups now stop on any finding.
- The API image shipped the base image's unused pip with 12 fixable
  advisories; removed.
- Notebook cells were outside the lint scope and had unused imports; ruff now
  covers `notebooks/` in the Makefile and CI.
- Postgres was reachable from processes on the Linux Docker host, as a
  superuser with `trust` authentication (an independent review; reproduced).
  The database now has no network and the API reaches it through a Unix
  socket in a volume only the two containers mount. The red-team exercise
  then showed the API's role was still the superuser; a non-superuser role
  now owns the database, and only it may connect over the socket.
- The final automated verification of the whole diff (five reviewers, each
  finding re-checked by a skeptic) confirmed 44 of 45 findings. They were fixed in
  three batches on disjoint files, each checked by another skeptic; the
  skeptics found eight small regressions and one failing integration test in
  those fixes, which a second round fixed and re-checked. The main findings:
  - CI's gitleaks step scanned every fetched branch, so the backup branch's
    false positive would have failed every run; it now scans the history of
    the commit under test.
  - Hotel pages said "No catch to set aside" next to a catch, said a single
    catch "holds back" Fair hotels (Good allows one), described Fair as
    having no Weak essential (one can be Weak on probabilities but not on
    clear-cut labels), and the About page listed the tier rules in the wrong
    order; each now follows `tiers.py`.
  - 33 served texts printed a share next to its line as the line itself
    ("now 80%; the line is 80%"); fixed and regenerated, with every tier,
    grade and number unchanged.
  - The claim that one more praise never lowers a tier is false in two
    edge cases at the 10-opinion floor; no rule was changed, the cases are
    documented (ADR 006 E6) and pinned by tests, and no served hotel is
    affected.
  - CI downloaded 500 MB of unused v1 weights per job; shell scripts could
    be checked out with CRLF on Windows; several documents overstated what
    was tested or committed.

## 7. Owner actions

1. Allow `lfs.github.com` in the environment's network settings, then add the
   weights (BLOCKERS B8 has the exact commands and SHA-256).
2. Optional: run your own `/red-team` on `v2` as well (B7); an equivalent
   exercise already ran (security review, section 4.6).
3. Label the 100-review human audit sheet (B3,
   `docs/labeling/HUMAN_AUDIT.md`).
4. Optionally allow huggingface.co to add a modern encoder to the ladder
   (B1); the promotion rule decides.
5. Legal review of the data licence before any commercial use (`NOTICE`).
6. Review and merge the pull request.

## 8. Reproduce

CPU only. `make setup-ml` installs everything from `uv.lock` (`make setup`
is enough to serve the API and run `make data`, not the other targets);
`make data` (about a minute with cached downloads), `make models` (the full
ladder takes hours on 4 CPU threads; roberta-base alone 4,596 s), `make
scores` (corpus scoring 1.7 h measured, validation 4.5 min in the last run),
`make check` (lint, types and tests; `pytest -m "not eval"` reported 136 s,
141 s and 258 s in three runs during this build, the last while other
jobs shared the CPU).
`docker compose up --build` serves the committed results without any of
these steps.

## 9. Commits on `v2`

| Milestone | Commit | Subject |
|---|---|---|
| M0 | `1a79b39` | docs: v2 plan, v1 audit, decisions |
| M1 | `f8ce452` | feat(data): real-hotel data foundation |
| M2 | `6c8ede0` | feat(ml): labels, model ladder, evaluation and promotion |
| M3 | `fb933e6` | feat(api): tiers, scoring and backend |
| M4 | `2647ee2` | feat(web): explorer, hotel, compare and about pages |
| M5 | `24606cb` | docs: product polish (README, docs index, compose, CI) |
| M6 | `d51988d` | fix: quality and security gates |
| M7 | this commit | docs: architecture views |
