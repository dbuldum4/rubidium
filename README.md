# Rubidium (Rb)

Rubidium is a macOS-only browser forked from Chromium. Its single goal is to be
vastly faster than Chromium without losing functionality.

- **macOS only.** Other platforms are not product targets.
- **Ambitious by design.** Large architectural changes are in scope if they are
  measured and keep the web working.
- **Independent project.** Rb does not send changes to Chromium. All work lands
  through pull requests on
  [dbuldum4/rubidium](https://github.com/dbuldum4/rubidium).

## Where things are

| Path | What |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Rules for anyone (human or agent) changing this repo |
| [rb/ROADMAP.md](rb/ROADMAP.md) | The hillclimbing guide: how performance work is chosen, measured and landed |
| [rb/perf/](rb/perf/README.md) | Speedometer 3.1 harness and its measured noise floor |
| everything else | The Chromium tree at the fork point (see `chrome/VERSION`) |

Rb-specific files live under `rb/`. Chromium's own documentation is still rooted
at [docs/README.md](docs/README.md).

## Measuring

```sh
# Baseline: a pinned Chrome for Testing build, downloaded on first use.
rb/perf/speedometer.py run --browser cft:156.0.8078.47

# A/B: alternate launches of two browsers and report the difference.
rb/perf/speedometer.py compare --base <browser> --new <browser>
```

Read [rb/perf/README.md](rb/perf/README.md) before trusting a number.

## Building

Rb has not been built from this fork yet; there are no Rb-specific build
instructions that have been exercised. Chromium's instructions for the fork
point are in [docs/mac_build_instructions.md](docs/mac_build_instructions.md).
Establishing and documenting the build is the first item in the
[roadmap](rb/ROADMAP.md).

## License

BSD-style, see [LICENSE](LICENSE). Rubidium is not affiliated with Google or the
Chromium project.
