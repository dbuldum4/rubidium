# Rubidium (Rb) — agent rules

Rb is a macOS-only Chromium fork whose goal is to be vastly faster than Chromium
without losing functionality. Read [rb/ROADMAP.md](rb/ROADMAP.md) before doing
performance work.

## Hard rules

1. **Never send anything to Chromium.** No PRs to `chromium/chromium`, no
   `git cl upload`, no Gerrit. This repo is a GitHub fork, so `gh` defaults PRs
   to the parent: always pass `--repo dbuldum4/rubidium --base main`, and check
   the PR URL after creating it.
2. **Functionality is not traded for speed.** A change that breaks web
   behaviour, a web-platform test, or a user-visible feature is not a
   performance win.
3. **No benchmark special-casing.** Nothing may detect Speedometer or its
   workloads, by URL, content or shape. A change must be a general improvement.
4. **Every performance claim carries its measurement.** Follow "Reporting a
   result" below.

## Scope

- Product target: macOS only. Code for other platforms may be changed or
  removed when that serves the goal.
- The agent machine is a macOS VM (Apple M4 Pro, virtual), so building and
  measuring happen on macOS too. No other platform is kept for measurement.
- Large, invasive changes are welcome. Prefer the change that is right for Rb
  over the one that merges cleanly with upstream.

## Layout

- `rb/` holds everything Rb-specific that is not a change to Chromium code:
  docs, tooling, perf harness.
- Outside `rb/` this is the Chromium tree. Chromium's style guides
  (`styleguide/`) apply to code there.
- Upstream agent material in `agents/`, `.agents/`, `.github/` describes
  Chromium's workflow. Where it conflicts with this file, this file wins.

## Measuring

The harness is `rb/perf/speedometer.py` (Python standard library only); details
and the measured noise floor are in [rb/perf/README.md](rb/perf/README.md).

- Compare with `compare`, never with two separate `run` invocations: on the
  agent VM absolute scores shifted by more than 10% between sessions with the
  same browser.
- Before measuring, stop background load on the agent VM (booted iOS
  simulators, Spotlight, software update) as described in
  [rb/perf/README.md](rb/perf/README.md), and check `uptime`.
- The agent VM is a virtual Mac. Its scores say nothing about absolute speed on
  a physical Mac.

## Reporting a result

State all of the following, including when the result is disappointing:

- the exact command, both browsers, and the `result.json` path;
- the difference **with its 95% interval** and the number of launches;
- every comparison you ran for this change, not only the one being quoted
  (if you measured four times, say four and give all four);
- anything excluded or rerun, and why, decided before looking at the scores;
- what was not checked (for example: not measured on macOS, functionality
  tests not run).

A difference whose interval includes zero is "not detected", not "no change"
and not a win. If the harness or the analysis was changed to produce the
number, say so.

## Git

- Branch from `main`; open PRs against `dbuldum4/rubidium`.
- Do not edit git configuration.
- `out/` is ignored; benchmark results and downloaded browsers live in
  `out/rb-perf/` and are not committed.
