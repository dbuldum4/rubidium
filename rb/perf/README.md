# Rb performance harness

`speedometer.py` runs [Speedometer 3.1](https://browserbench.org/Speedometer3.1/)
against any Chromium-based browser and records enough to reproduce the run. It
uses only the Python standard library and drives the browser over the DevTools
protocol, so it needs no chromedriver and no virtualenv.

```sh
rb/perf/speedometer.py setup   --browser cft:156.0.8078.47   # fetch only
rb/perf/speedometer.py run     --browser cft:156.0.8078.47
rb/perf/speedometer.py compare --base cft:156.0.8078.47 --new out/Release/chrome
```

`--browser`, `--base` and `--new` accept a binary, a macOS `.app` bundle, or
`cft:<version>` (a Chrome for Testing build, downloaded once into
`out/rb-perf/browsers/`).

## What is held fixed

- **Benchmark source**: `third_party/speedometer/v3.1` at the revision pinned by
  this repo's gitlink (the same pin `DEPS` uses). The harness fetches it if
  missing and refuses to run if the checkout is at another revision or dirty.
- **No network**: the benchmark is served from `127.0.0.1`.
- **Browser state**: a fresh profile per launch and a fixed flag list
  (`BROWSER_FLAGS` in the script). The flags disable first-run UI and background
  services; none changes how the engine runs the benchmark. Extra flags given
  with `--browser-arg` apply to every browser and are recorded.
- **Workload**: `iterationCount=10`, Speedometer's default. One launch is one
  full Speedometer run; `--runs` (default 5) is the number of launches.
- **No observer effect**: the harness starts the benchmark with one DevTools
  call and waits for it to resolve; it does not poll the page while it runs.
- **Display**: on Linux, a private Xvfb server per invocation.

`compare` alternates launches (base, new, base, new, ...) so that slow drift in
the machine falls on both browsers.

## Output

`out/rb-perf/results/<UTC time>-<command>[-label]/result.json` contains every
launch (score, per-iteration scores, every Speedometer metric, duration, load
average, host CPU steal on Linux), the browser versions, flags, machine, source
commit, and the summary. It is rewritten after every launch, so an interrupted
session keeps what it measured.

The summary reports mean, median, standard deviation and a 95% Student-t
interval per browser; `compare` adds the difference of means with a Welch 95%
interval. "Significant" means only that this interval excludes zero.

## Noise floor on the agent VM

Measured on the Devin Linux VM (8 vCPU, Cloud Hypervisor guest, no GPU) by
comparing Chrome for Testing 156.0.8078.47 **against itself**, 6 launches per
side, 10 iterations each. The true difference is zero, so the interval is the
harness's resolution. These are all the A/A sessions that were run:

| Session (UTC) | base mean (CV) | new mean (CV) | new vs base, 95% | CPU steal |
| --- | --- | --- | --- | --- |
| 2026-11-25 01:44 | 16.41 (5.3%) | 16.80 (6.7%) | +2.38% ± 7.99% | not recorded |
| 2026-11-25 01:53 | 15.96 (11.7%) | 15.88 (12.8%) | -0.50% ± 15.98% | not recorded |
| 2026-11-25 02:01 | 14.06 (4.1%) | 13.89 (6.2%) | -1.25% ± 6.96% | 27–36% of busy time |

Between the second and third session the harness gained steal recording and
process-group cleanup; the benchmark, flags and analysis were unchanged. Three
sessions cannot show whether that cleanup affected the noise.

What this means:

- **With 6 launches per side this VM resolves roughly ±7% to ±16%.** Smaller
  effects cannot be detected at that sample size.
- **Absolute scores are not comparable across sessions.** The same browser
  averaged about 16 in two sessions and 14 in the third. Only use differences
  from a single `compare` invocation.
- **The hypervisor is the main known cause.** In the one session where it was
  recorded, 27–36% of the CPU time the guest wanted was stolen by the host, and
  score correlated with steal at r = -0.88 over 12 launches (r = -0.79 without
  the lowest-scoring launch). That is one session and the relationship was
  examined after seeing the data; treat it as a lead, not an established model.
  An earlier guess, that a background `git fetch` caused the noise, was wrong:
  the second session ran on an idle machine and was the noisiest.
- **Scores here are low (14–17)** because the VM rasterises in software. They
  say nothing about absolute speed on a Mac.

Rough sizing, computed from the observed per-launch CV and assuming independent
launches (session drift violates this, so take it as optimistic): at 6% CV,
resolving ±2% needs about 70 launches per side, around 1.7 hours for both; at
12% CV, about four times that.

## Rules for using the numbers

- Do not drop, trim or rerun launches because of their score. If a launch must
  be excluded (browser crash, machine event), the rule has to be decided before
  looking at scores and the exclusion reported.
- Report every comparison run for a change, not the best one.
- The steal figure is recorded for diagnosis. It is not used to filter or
  adjust scores. Adjusting for it may be worth adding, but only as a declared
  option validated on fresh A/A sessions, with the unadjusted result kept as
  the headline.

## Not verified

- **macOS.** The `.app` bundle handling, the `ditto` extraction of Chrome for
  Testing and the `sysctl` machine info have never been executed. macOS is the
  product target, so this is the most important gap.
- **An Rb build.** Only Chrome for Testing has been measured; no browser built
  from this tree has been run through the harness.
- **GPU machines and other Linux hosts.**
