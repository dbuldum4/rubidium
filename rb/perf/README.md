# Rb performance harness

`speedometer.py` runs [Speedometer 3.1](https://browserbench.org/Speedometer3.1/)
against any Chromium-based browser and records enough to reproduce the run. It
uses only the Python standard library and drives the browser over the DevTools
protocol, so it needs no chromedriver and no virtualenv.

```sh
rb/perf/speedometer.py setup   --browser cft:156.0.8078.47   # fetch only
rb/perf/speedometer.py run     --browser cft:156.0.8078.47
rb/perf/speedometer.py compare --base cft:156.0.8078.47 --new out/Release/chrome
rb/perf/smoke.py       --browser out/Release/chrome
rb/perf/workload_summary.py out/rb-perf/results/<run>/result.json new
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
  full Speedometer run; `--runs` (default 5) is the number of scored launches.
  `--warmup-runs` can add unscored launches, which are recorded separately and
  never included in the summary.
- **No observer effect**: the harness starts the benchmark with one DevTools
  call and waits for it to resolve; it does not poll the page while it runs.
- **Display**: on macOS, the logged-in desktop session; on Linux, a private
  Xvfb server per invocation.

`compare` interleaves launches in pairs and randomizes which browser starts
each pair, with the number of base-first pairs balanced. The random seed and
actual order are recorded in `result.json`; pass `--seed` to reproduce an
order. Randomized ordering keeps periodic machine noise from lining up with a
fixed browser order.

## Output

`out/rb-perf/results/<UTC time>-<command>[-label]/result.json` contains every
launch (score, per-iteration scores, every Speedometer metric, duration, load
average, host CPU steal on Linux), the browser versions, flags, machine, source
commit, and the summary. It is rewritten after every launch, so an interrupted
session keeps what it measured.

The summary reports mean, median, standard deviation and a 95% Student-t
interval per browser. `compare` pairs launches by iteration and analyses the
log of each pair's score ratio (`new / base`), because machine drift scales
throughput multiplicatively; it reports the exact endpoints of the 95%
interval. "Significant" means only that this interval excludes zero.

## Functionality smoke test

`smoke.py` launches one browser and checks representative web platform behavior:
DOM and CSS layout, JavaScript, WebAssembly, canvas 2D, WebGL, workers,
WebCrypto, storage, and MP4/WebM capability detection. It exits nonzero if any
check fails. Passing it is the minimum functionality gate for a performance
candidate; it is not a substitute for Chromium's web-platform tests or targeted
tests for the touched code.

## Quiet the agent VM first

The macOS agent VM boots with background work that swamps the benchmark. Before
measuring, run:

```sh
xcrun simctl shutdown all          # the image boots two iOS simulators
sudo mdutil -a -i off              # Spotlight indexes the Chromium tree
sudo pkill -f UpdateBrainService   # software update preparing in the background
uptime                             # wait until the 1-minute load is below ~5
```

Spotlight stays off once disabled. The simulators and the update service may
return after a reboot, so check `uptime` and `ps -Ao pcpu,comm -r | head` again.
`result.json` records the load average at the start of every launch.

## Noise floor on the agent VM

Measured on the Devin macOS VM (Apple M4 Pro (Virtual), `VirtualMac2,1`, 12
cores, 16 GB, macOS 26.6.2) by comparing Chrome for Testing 156.0.8078.47
(mac-arm64) **against itself**, 6 launches per side, 10 iterations each. The
true difference is zero, so the interval is the harness's resolution. These
sessions predate the paired log-ratio analysis and used independent-sample
Welch intervals; they are kept as background-load evidence, not as the current
estimator's noise floor. These are all the A/A sessions that were run on this
VM under that analysis:

| Session (UTC) | base mean (CV) | new mean (CV) | new vs base, 95% | 1-min load at launch |
| --- | --- | --- | --- | --- |
| 2026-11-25 02:30 | 35.73 (24.9%) | 35.32 (22.2%) | -1.16% ± 30.63% | up to 91 |
| 2026-11-25 02:39 | 41.76 (5.6%) | 41.53 (5.9%) | -0.54% ± 7.49% | 11 falling to 3 |
| 2026-11-25 02:45 | 45.11 (7.0%) | 45.95 (3.6%) | +1.86% ± 7.60% | 1.6 to 3.5 |

The first session ran with two booted iOS simulators, Spotlight indexing and the
software update service busy. Those were stopped after seeing that session's
scores and load, and before the second session; the benchmark, flags and
analysis were unchanged. One launch of the third session (base, 39.05, the
first launch) is the main source of its interval; it was kept.

What this means:

- **The old Welch estimator resolved roughly ±7.5% with 6 launches per side**
  in those sessions. With randomized ordering, one warmup pair, and the paired
  estimator, a later identical-binary session had a wide interval of
  [-2.66%, +11.59%] after starting only ten minutes after VM boot. With three
  warmup pairs on a warm VM, two identical-binary sessions measured +0.12%
  [-2.16%, +2.46%] and -0.60% [-4.36%, +3.31%]. Plan on roughly ±2-4% at 6
  scored launches per side and use more launches for smaller expected effects.
  On a busy VM the old interval was ±31%.
- **Absolute scores are not comparable across sessions.** The same browser
  averaged about 42 and 45 in the two quiet sessions. Only use differences
  from a single `compare` invocation.
- **Scores are about 42–46**, against 14–17 on the earlier Linux VM. The VM
  exposes a paravirtualized GPU; whether Chrome uses it was not checked. The
  scores say nothing about absolute speed on a physical Mac.

### Earlier: Linux VM

Before the agent machine was macOS, the same A/A was run three times on a Linux
VM (8 vCPU, Cloud Hypervisor guest, no GPU): +2.38% ± 7.99%, -0.50% ± 15.98%
and -1.25% ± 6.96%, with 27–36% of busy CPU time stolen by the host in the one
session where steal was recorded. The harness still records steal on Linux.

Rough sizing, computed from the observed per-launch CV and assuming independent
launches (session drift violates this, so take it as optimistic): at 6% CV,
resolving ±2% needs about 70 launches per side; at 12% CV, about four times
that. On the macOS VM one launch runs the benchmark for about 18–21 s, plus
browser start and settle time.

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

- **A physical Mac.** Only the macOS agent VM has been measured.
- **An Rb build.** Only Chrome for Testing has been measured; no browser built
  from this tree has been run through the harness.
- **Whether Chrome uses the VM's paravirtualized GPU.**
