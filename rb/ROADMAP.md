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
- **Functionality**: not yet defined. Chromium's own suites (web tests,
  `content_browsertests`, unit tests for the touched code) are the obvious
  candidates, but none has been run in this fork and the required set is an
  open decision.
- **Proposed guardrails, not yet adopted**: a second benchmark family so that
  Speedometer gains are not bought elsewhere (JetStream, MotionMark, page
  load, memory, startup). Which of these bind is a decision for the project
  owner.

## Phase 0 — make measurement trustworthy

Nothing else is worth doing until these are done, because gains smaller than
the noise cannot be told from zero.

- [x] Speedometer 3.1 harness with pinned benchmark and baseline browser.
- [x] Noise floor of the agent VM measured: ±7% to ±16% at 6 launches per side.
- [ ] **Build Rb unmodified** from the fork point and document the exact
      steps. Not attempted yet. Its score against Chrome for Testing shows what
      the build configuration alone costs or gains, and it becomes the real
      baseline for every later comparison.
- [ ] **Measure on macOS.** The product is macOS-only and the agent VM is a
      GPU-less Linux guest; a gain there may not carry over, and Mac-specific
      work cannot be measured there at all. Run the harness on a Mac and
      record its noise floor.
- [ ] **Reduce or route around VM noise.** Options, none tried: more launches
      per comparison; a quieter or dedicated host; CPU pinning; adjusting for
      recorded CPU steal. Each must be validated by A/A sessions, with all
      sessions reported.
- [ ] Decide whether a Linux build is kept purely as a measurement target.
- [ ] Define the functionality gate.

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
