# Building Rb on macOS

Rb is a Chromium fork and uses Chromium's `gclient`/GN/Siso build system. The
commands below are the exact dependency and baseline-build recipe used on the
macOS agent VM. They require Xcode and about 100 GB of free disk. On the 16 GB
VM, use `-local_jobs=7`: higher parallelism caused memory compression and much
slower builds.

## One-time machine and dependency setup

Install Xcode (the verified VM used Xcode 26.6 with the macOS 26.5 SDK), then:

```sh
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git \
  "$HOME/depot_tools"
export PATH="$HOME/depot_tools:$PATH"

mkdir -p "$HOME/rb"
cat > "$HOME/rb/.gclient" <<'EOF'
solutions = [
  {
    "name": "src",
    "url": "https://github.com/dbuldum4/rubidium.git",
    "managed": False,
    "custom_deps": {
      # Test-only repositories that are not needed to build or run the browser.
      "src/chrome/test/data/perf/canvas_bench": None,
      "src/chrome/test/data/perf/frame_rate/content": None,
      "src/chrome/test/data/xr/webvr_info": None,
      "src/testing/libfuzzer/fuzzers/wasm_corpus": None,
      "src/docs/website": None,
      "src/third_party/crossbench": None,
      "src/third_party/crossbench-web-tests": None,
      "src/third_party/webgl/src": None,
      "src/third_party/webgpu-cts/src": None,
      "src/third_party/webpagereplay": None,
    },
    "custom_vars": {
      "checkout_configuration": "small",
      "checkout_js_coverage_modules": False,
      "checkout_pgo_profiles": True,
    },
  },
]
EOF

git clone --depth=1 https://github.com/dbuldum4/rubidium.git "$HOME/rb/src"
cd "$HOME/rb/src"
git fetch --deepen=20 origin main
cd "$HOME/rb"
gclient sync --no-history
```

Notes:

- `src` must be the real checkout directory, not a symlink: gclient refuses
  symlinked dependency paths. A symlink may point at `$HOME/rb/src` from
  elsewhere, but do not invoke gclient through the symlink.
- `git fetch --deepen=20` is needed because Chromium's `lastchange.py` must
  find a commit containing a `Change-Id`; a depth-1 clone otherwise produces a
  zero revision.
- `gclient sync` also runs the hooks that download the compiler, SDK support
  files, Rust toolchain and PGO profiles.
- Do not replace the clone with Chromium upstream. This is the Rb fork; never
  upload anything to Chromium/Gerrit.

## Baseline release build

This is the unoptimized-but-release Rb baseline used before performance
changes. It is a release static build with Chromium's normal production
security hardening and Chrome-equivalent codec support. `use_lld` is not set;
Chromium's macOS default is true.

```sh
cd "$HOME/rb/src"
mkdir -p out/Baseline
cp rb/build/baseline.args.gn out/Baseline/args.gn
gn gen out/Baseline
caffeinate -i autoninja -C out/Baseline chrome -local_jobs=7
```

The browser is `out/Baseline/Chromium.app` (the executable inside it is
`Contents/MacOS/Chromium`).

## Checks

```sh
rb/perf/smoke.py --browser out/Baseline/Chromium.app
```

Before benchmarking, quiet the VM as described in
[../perf/README.md](../perf/README.md), then use `rb/perf/speedometer.py
compare` in one alternating invocation.
