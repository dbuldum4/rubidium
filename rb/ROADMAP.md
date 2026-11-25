# Rb hillclimbing guide

The goal: Rb is vastly faster than Chromium on macOS, with no loss of
functionality. This document is the method for getting there. It contains no
performance results, because none exist yet: nothing in the engine has been
profiled or changed. The directions in "Where to look" are hypotheses.

## The loop

Every performance change goes through the same steps.

1. **Profile.** Find where time goes on the workload, on the baseline build.
   Write down the share of time the target accounts for; that bounds the
   possible gain.
2. **Hypothesise.** State the change and the gain expected from it before
   building it, and what would count as failure.
3. **Decide the measurement first.** Browsers, number of launches, metric. Size
   the run from the noise floor in [perf/README.md](perf/README.md) so the
   expected effect is larger than the interval.
4. **Implement** the general change. No benchmark detection of any kind.
5. **Measure** with `rb/perf/speedometer.py compare`, base versus new, in one
   invocation.
6. **Check functionality** (see "Gates").
7. **Record** the result in the PR, as required by `AGENTS.md`: the interval,
   the launch count, every comparison run, anything excluded, what was not
   checked.
8. **Land or drop.** Land if the gain is detected and the gates pass. If the
   interval includes zero the effect was not detected: either measure with
   more launches (decided before looking further) or drop it. Record dropped
   attempts in "Log" below so they are not retried blindly.

A change that is architecturally necessary for later gains may land without a
detected gain, if it does not regress and the PR says plainly that no gain was
measured.

## Gates

- **Speed**: Speedometer 3.1 score, base versus new.
- **Functionality**: the minimum gate is that `rb/perf/smoke.py` passes on the
  candidate browser. Targeted Chromium unit/browser tests for the touched code
  are required when they exist and are practical to run. Chromium's web tests
  and `content_browsertests` have not yet been run in this fork; the smoke test
  is a floor, not evidence of full web compatibility.
- **Proposed guardrails, not yet adopted**: a second benchmark family so that
  Speedometer gains are not bought elsewhere (JetStream, MotionMark, page
  load, memory, startup). Which of these bind is a decision for the project
  owner.

## Phase 0 — make measurement trustworthy

Nothing else is worth doing until these are done, because gains smaller than
the noise cannot be told from zero.

- [x] Speedometer 3.1 harness with pinned benchmark and baseline browser.
- [x] Noise floor of the agent VM measured (see [perf/README.md](perf/README.md)).
- [x] **Build Rb unmodified** from the fork point and document the exact
      steps (see [build/README.md](build/README.md)). Its score against Chrome
      for Testing shows what the build configuration alone costs or gains, and
      it becomes the real baseline for every later comparison.
- [x] **Measure on macOS.** The agent VM is now a virtual Mac; its noise
      floor is recorded in [perf/README.md](perf/README.md). A physical Mac
      has not been measured.
- [ ] **Reduce or route around VM noise.** Stopping background services on
      the macOS VM is done (see [perf/README.md](perf/README.md)). Not tried:
      more launches per comparison; a quieter or dedicated host; a physical
      Mac. Each must be validated by A/A sessions, with all sessions reported.
- [x] Decide whether a Linux build is kept purely as a measurement target: no,
      the agent machine is macOS.
- [x] Define the minimum functionality gate (`rb/perf/smoke.py`; broader
      Chromium suites remain open).

Exit: an unmodified Rb build has a recorded baseline on the machine used for
decisions, and that machine's A/A interval is known and small enough for the
effects being sought.

## Phase 1 — configuration

Changes that alter how the same code is built or configured, with no source
changes. Cheap to try and easy to revert, so they come first: compiler and
linker optimisation settings, profile-guided optimisation, and removing
features or instrumentation that Rb does not ship. Each is measured through the
loop like any other change.

## Phase 2 — profile-driven engine work

Profile Speedometer on the baseline and rank subsystems by time. Work the
largest first. The split between script execution, style, layout, paint, DOM
bindings and inter-process overhead has not been measured in this project and
should not be assumed.

## Phase 3 — architectural changes

Changes that Chromium cannot make because it must serve every platform and
configuration, and Rb can because it serves one. Candidates come out of Phase 2
profiles. Each needs a written design with its expected gain and its risk to
functionality before work starts.

## Where to look

Unmeasured hypotheses, listed so they are profiled rather than assumed:

- cost of cross-platform abstraction and unused platform code on a macOS-only
  build;
- build configuration (optimisation level, PGO, link-time optimisation);
- process and IPC overhead on the paths Speedometer exercises;
- macOS- and Apple-silicon-specific tuning.

## Log

Record every attempt here, including failures: date, change, comparison
command, result with interval, outcome.

| Date | Change | Result | Outcome |
| --- | --- | --- | --- |
| 2026-11-25 | Harness A/A, Chrome for Testing 156 vs itself, 3 sessions | +2.38% ± 7.99%, -0.50% ± 15.98%, -1.25% ± 6.96% | Noise floor recorded; no engine change measured yet |
| 2026-11-25 | Unmodified Rb baseline; `python3 rb/perf/speedometer.py compare --base cft:156.0.8078.47 --new out/Baseline/Chromium.app --runs 6 --label cft-vs-rb-baseline`; `out/rb-perf/results/20261125T054642Z-compare-cft-vs-rb-baseline/result.json` | Rb -22.61% ± 22.15% vs Chrome for Testing, 6 launches/side; nothing excluded | Baseline recorded; non-official build configuration gap detected; smoke test passed |
| 2026-11-25 | Diagnostic extreme V8 tiering configuration: archived non-official baseline versus the same binary wrapped with `--js-flags=--stress-maglev`; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-baseline.app --new /Users/devin/rb/wrappers/rb-stress-maglev --runs 6 --label baseline-vs-stress-maglev`; `out/rb-perf/results/20261125T070155Z-compare-baseline-vs-stress-maglev/result.json` | -1.01%, 95% interval [-1.46%, -0.56%], 6 launches/side; nothing excluded; no warmup; analysis used the new balanced ordering and paired log-ratio method | Dropped as a candidate: earlier Maglev tiering was a small detected regression, not a win. Smoke test not run because this was a diagnostic wrapper, not a landed candidate. The concurrently running build was suspended during measurement |
| 2026-11-25 | Paired-analysis A/A validation attempt, archived non-official baseline versus itself; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-baseline.app --new out/rb-perf/browsers/rb-baseline.app --runs 6 --label aa-paired-baseline`; `out/rb-perf/results/20261125T070814Z-compare-aa-paired-baseline/result.json` | -8.85%, 95% interval [-22.66%, +7.43%], 6 launches/side; nothing excluded; no warmup | Not a valid quiet-machine noise-floor measurement: the build was suspended rather than finished, its memory remained resident, and the machine became busy mid-session (scores slowed from about 37 to 22-34). It is retained as evidence that measurements must wait for a finished build, not as a browser difference |
| 2026-11-25 | Official build configuration (`is_official_build=true`, PGO phase 2, ThinLTO and ThinLTO optimizations, lld) versus archived non-official Rb; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-baseline.app --new out/Baseline/Chromium.app --runs 6 --label rb-nonofficial-vs-official`; `out/rb-perf/results/20261125T080524Z-compare-rb-nonofficial-vs-official/result.json` | +41.71%, 95% interval [+32.34%, +51.75%], 6 launches/side; nothing excluded; no warmup | Large detected win; smoke passed 9/9. Harness ordering/analysis changed since the first baseline (balanced pairs and paired log ratios), so percentages are not comparable with the earlier Welch analysis |
| 2026-11-25 | Same official Rb versus pinned Chrome for Testing; `python3 rb/perf/speedometer.py compare --base cft:156.0.8078.47 --new out/rb-perf/browsers/rb-official.app --runs 6 --label cft-vs-rb-official`; `out/rb-perf/results/20261125T081051Z-compare-cft-vs-rb-official/result.json` | +1.49%, 95% interval [-1.76%, +4.84%], 6 launches/side; nothing excluded; no warmup | Improvement over Chrome for Testing not detected; official Rb is at rough parity. Smoke passed 9/9. This was the only CFT-versus-official comparison run |
