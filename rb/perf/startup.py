#!/usr/bin/env python3
"""Startup-time comparison: spawn browser to first contentful paint of a trivial page.
Warm page-cache (dyld cached after first launch); measures process init + first render.
Alternating reps in one invocation, paired log-ratio analysis."""
import json, pathlib, subprocess, sys, tempfile, time, shutil, statistics, random, math
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import speedometer as harness

def measure_once(binary, extra_flags=None):
  profile = pathlib.Path(tempfile.mkdtemp(prefix='rb-startup-'))
  t0 = time.monotonic()
  cmd = [str(binary), *harness.BROWSER_FLAGS, *(extra_flags or []), f'--user-data-dir={profile}',
         'data:text/html,<html><body><h1>startup probe</h1></body></html>']
  proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
  fcp = None
  try:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
      fp = profile / 'DevToolsActivePort'
      if fp.exists():
        port = fp.read_text().split()[0]
        import urllib.request
        try:
          with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=1) as r:
            targets = json.load(r)
          page = next((t for t in targets if t['type'] == 'page'), None)
          if page:
            devtools = harness.DevTools(page['webSocketDebuggerUrl'])
            try:
              for attempt in range(50):
                v = devtools.evaluate(
                    'performance.getEntriesByType("paint").filter(e=>e.name==="first-contentful-paint").length > 0',
                    timeout=2)
                if v:
                  fcp = time.monotonic() - t0
                  break
                time.sleep(0.05)
            finally:
              devtools.close()
          break
        except Exception:
          pass
      time.sleep(0.05)
  finally:
    harness.stop_browser(proc)
    shutil.rmtree(profile, ignore_errors=True)
  return fcp

def resolve_binary(app):
  macos = pathlib.Path(app) / 'Contents/MacOS'
  if macos.is_dir():
    bins = list(macos.iterdir())
    if bins: return bins[0]
  return pathlib.Path(app) / 'Contents/MacOS/Chromium'

def main():
  import argparse
  p = argparse.ArgumentParser()
  p.add_argument('--base', required=True)
  p.add_argument('--new', required=False)
  p.add_argument('--extra-flags', default='')
  p.add_argument('--reps', type=int, default=10)
  p.add_argument('--seed', type=int, default=1)
  p.add_argument('--label', default='startup')
  args = p.parse_args()
  base = resolve_binary(args.base)
  extra = args.extra_flags.split() if args.extra_flags else []
  if not args.new:
    measure_once(base, extra)
    vals=[]
    for i in range(args.reps):
      v=measure_once(base, extra)
      if v: vals.append(v*1000)
      print('rep%d: %.0fms'%(i,(v or 0)*1000), flush=True)
    import statistics as st
    print('mean %.0fms median %.0fms n=%d'%(st.mean(vals),st.median(vals),len(vals)))
    return
  new = resolve_binary(args.new)
  # warm dyld cache once per binary, discarded
  measure_once(base, extra); measure_once(new)
  results = {'base': [], 'new': []}
  order = [('base', i) for i in range(args.reps)] + [('new', i) for i in range(args.reps)]
  random.Random(args.seed).shuffle(order)
  for which, rep in order:
    b = base if which == 'base' else new
    v = measure_once(b)
    if v: results[which].append(v)
    print(f'{which} rep{rep}: {v*1000 if v else 0:.0f}ms', flush=True)
  pairs = list(zip(results['base'], results['new']))
  ratios = [math.log(n/b) for b, n in pairs]
  out = {}
  if len(ratios) >= 2:
    mean = statistics.mean(ratios); sd = statistics.stdev(ratios)
    ci = 1.96*sd/math.sqrt(len(ratios))
    out = {'pct_diff': (math.exp(mean)-1)*100, 'ci95': (math.exp(ci)-1)*100, 'n': len(ratios),
           'base_mean_ms': statistics.mean(results['base'])*1000, 'new_mean_ms': statistics.mean(results['new'])*1000}
  print(json.dumps(out, indent=2))
  resdir = pathlib.Path('/Users/devin/rb/pageload-results'); resdir.mkdir(exist_ok=True)
  path = resdir / f'{args.label}-{int(time.time())}.json'
  path.write_text(json.dumps({'results': results, 'analysis': out}, indent=2))
  print('wrote', path)

if __name__ == '__main__':
  main()
