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
| 2026-11-25 | Quiet paired-analysis A/A validation, official Rb versus itself; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new out/rb-perf/browsers/rb-official.app --runs 6 --label aa-paired-official-quiet`; `out/rb-perf/results/20261125T081616Z-compare-aa-paired-official-quiet/result.json` | -3.27%, 95% interval [-5.54%, -0.95%], 6 launches/side; nothing excluded; no warmup | Harness problem detected: identical binaries produced a significant false difference because positional machine noise correlated with the deterministic AB/BA order. Small effects are not trustworthy until ordering/randomization and the noise floor are fixed; this session must be reported with every result |
| 2026-11-25 | Randomized-order A/A validation, official Rb versus itself, one warmup pair; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new out/rb-perf/browsers/rb-official.app --runs 6 --warmup-runs 1 --label aa-random-official-1`; `out/rb-perf/results/20261125T164636Z-compare-aa-random-official-1/result.json` | +4.22%, 95% interval [-2.66%, +11.59%], 6 launches/side; nothing excluded | Harness validation only: no false significance, but the interval was too wide because the VM had been up only about ten minutes and scores were still warming |
| 2026-11-25 | Randomized-order A/A validation, official Rb versus itself, three warmup pairs; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new out/rb-perf/browsers/rb-official.app --runs 6 --warmup-runs 3 --label aa-random-official-warm3`; `out/rb-perf/results/20261125T165259Z-compare-aa-random-official-warm3/result.json` | +0.12%, 95% interval [-2.16%, +2.46%], 6 launches/side; nothing excluded | Harness validation only: warm-machine protocol looks much better, but one session is not enough and 6 pairs still resolve only about ±2-4% |
| 2026-11-25 | Randomized-order A/A validation repeat, official Rb versus itself, three warmup pairs; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new out/rb-perf/browsers/rb-official.app --runs 6 --warmup-runs 3 --label aa-random-official-warm3-repeat`; `out/rb-perf/results/20261125T165953Z-compare-aa-random-official-warm3-repeat/result.json` | -0.60%, 95% interval [-4.36%, +3.31%], 6 launches/side; nothing excluded | Harness validation only: no false significance in either warm repeat; use more scored pairs when expecting effects below roughly 3% |
| 2026-11-25 | Diagnostic V8 always-Sparkplug configuration: archived official Rb versus same binary wrapped with `--js-flags=--always-sparkplug`; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new /Users/devin/rb/wrappers/rb-official-always-sparkplug --runs 10 --warmup-runs 3 --label official-vs-always-sparkplug`; `out/rb-perf/results/20261125T170806Z-compare-official-vs-always-sparkplug/result.json` | +1.95%, 95% interval [-1.89%, +5.94%], 10 launches/side; nothing excluded | Not detected, so not landed. Smoke passed 9/9. Most pairs were positive but two opposite large noise outliers dominated the interval; iterations 2-10 averaged 52.22 for the candidate versus 51.11 for base, while first iterations were slightly worse (39.66 versus 40.45). Needs a fresh predeclared confirmation measurement before any land decision |
| 2026-11-25 | Diagnostic experimental V8 API-call inlining: archived official Rb versus same binary wrapped with `--js-flags=--maglev-inline-api-calls`; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new /Users/devin/rb/wrappers/rb-official-inline-api --runs 10 --warmup-runs 3 --label official-vs-inline-api`; `out/rb-perf/results/20261125T171716Z-compare-official-vs-inline-api/result.json` | -1.82%, 95% interval [-9.61%, +6.64%], 10 launches/side; nothing excluded | Dropped/inconclusive: not detected, point estimate negative, and a large machine-noise burst hit pairs 3-6. Smoke passed 9/9. Upstream marks this as experimental with a known zapping bug, so it is not worth pursuing without evidence of a large effect |
| 2026-11-25 | Predeclared always-Sparkplug confirmation: archived official Rb versus same binary wrapped with `--js-flags=--always-sparkplug`; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new /Users/devin/rb/wrappers/rb-official-always-sparkplug --runs 20 --warmup-runs 3 --label official-vs-always-sparkplug-confirm`; `out/rb-perf/results/20261125T172832Z-compare-official-vs-always-sparkplug-confirm/result.json` | +0.07%, 95% interval [-0.49%, +0.62%], 20 launches/side; nothing excluded | Dropped: the promising first diagnostic was noise. Together with the first run, every always-Sparkplug comparison is reported; the precise confirmation detected no win. No source change landed |
| 2026-11-25 | Frame-pointer omission experiment: patched `build/config/compiler/compiler.gni` so macOS used `-fomit-frame-pointer`; full official build; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new out/rb-perf/browsers/rb-frameoff.app --runs 20 --warmup-runs 3 --label official-vs-frameoff`; `out/rb-perf/results/20261125T195011Z-compare-official-vs-frameoff/result.json` | -0.39%, 95% interval [-1.50%, +0.74%], 20 launches/side; nothing excluded | Dropped: no detected win. Smoke passed 9/9 before measurement. The source patch was reverted and was not committed; only this measurement is recorded |
| 2026-11-25 | Larger young-generation diagnostic: archived frameoff build versus same binary wrapped with `--js-flags=--max-semi-space-size=64`; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-frameoff.app --new /Users/devin/rb/wrappers/rb-frameoff-semi64 --runs 20 --warmup-runs 3 --label frameoff-vs-semi64`; `out/rb-perf/results/20261125T200618Z-compare-frameoff-vs-semi64/result.json` | -0.19%, 95% interval [-0.75%, +0.37%], 20 launches/side; nothing excluded; mean summed browser RSS 1621.9 MiB versus 1602.6 MiB | Dropped: no detected speed win and about +1.2% mean summed RSS. The wrapper was diagnostic only; no source change landed |
| 2026-11-25 | Turbolev diagnostic: archived official Rb versus same binary wrapped with `--js-flags=--turbolev`; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new /Users/devin/rb/wrappers/rb-official-turbolev --runs 20 --warmup-runs 3 --label official-vs-turbolev`; `out/rb-perf/results/20261125T202410Z-compare-official-vs-turbolev/result.json` | -0.52%, 95% interval [-1.51%, +0.48%], 20 launches/side; nothing excluded | Dropped: no detected win; point estimate was negative. Smoke passed 9/9 before measurement. Experimental wrapper only; no source change landed |
| 2026-11-25 | ThinLTO import limit experiment: changed `-import-instr-limit` from 30 to 100; full official rebuild; `python3 rb/perf/speedometer.py compare --base out/rb-perf/browsers/rb-official.app --new out/rb-perf/browsers/rb-import100.app --runs 20 --warmup-runs 3 --label official-vs-import100`; `out/rb-perf/results/20261125T224221Z-compare-official-vs-import100/result.json` | -0.49%, 95% interval [-1.10%, +0.13%], 20 launches/side; nothing excluded | Dropped: no detected win; point estimate was negative. Smoke passed 9/9. The patch was reverted and was not committed; only this measurement is recorded |
