#!/usr/bin/env python3
"""Page-load time comparison harness (Rb hillclimbing).

Mirrored real-world pages served from localhost. Per rep, loads every corpus
page in sequence; measurement is the paired sum of loadEventEnd across pages,
plus per-page paint/navigation timings. Warm-cache repeat loads: CPU/parse/
layout-bound, not network-bound. LCP captured via an observer injected with
Page.addScriptToEvaluateOnNewDocument.
"""
import json, pathlib, subprocess, sys, tempfile, threading, time, urllib.request, http.server, shutil, statistics, random, math
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import speedometer as harness

SITES = pathlib.Path('/Users/devin/rb/pageload-sites')
OUTDIR = pathlib.Path('/Users/devin/rb/pageload-results')
PAGES = [
  '/en.wikipedia.org/wiki/Web_browser.html',
  '/en.wikipedia.org/wiki/Barack_Obama.html',
  '/www.bbc.com/news.html',
  '/www.cnn.com/index.html',
]

class Quiet(http.server.SimpleHTTPRequestHandler):
  def log_message(self, *a): pass
  def end_headers(self):
    if self.path.split('?')[0] != '/' and not self.path.endswith('.html'):
      self.send_header('Cache-Control', 'max-age=3600')
    super().end_headers()

def serve(root):
  handler = lambda *a, **kw: Quiet(*a, directory=str(root), **kw)
  httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
  threading.Thread(target=httpd.serve_forever, daemon=True).start()
  return httpd, f'http://127.0.0.1:{httpd.server_address[1]}'

def wait_port(profile, proc):
  def read():
    if proc.poll() is not None: raise RuntimeError(f'browser exited {proc.returncode}')
    f = profile / 'DevToolsActivePort'
    return f.read_text().split()[0] if f.exists() else None
  return harness.wait_for(read, 60, 'DevToolsActivePort')

def json_url(port, path):
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/{path}') as r:
    return json.load(r)

LCP_HOOK = '''
window.__lcp = 0;
try {
  new PerformanceObserver((list) => {
    const entries = list.getEntries();
    if (entries.length) window.__lcp = entries[entries.length - 1].startTime;
  }).observe({type: 'largest-contentful-paint', buffered: true});
} catch (e) {}
'''

MEASURE_JS = '''
(() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paints = {};
  for (const e of performance.getEntriesByType('paint')) paints[e.name] = e.startTime;
  return {
    responseEnd: nav.responseEnd || 0, domInteractive: nav.domInteractive || 0,
    domContentLoaded: nav.domContentLoadedEventEnd || 0, loadEventEnd: nav.loadEventEnd || 0,
    fcp: paints['first-contentful-paint'] || 0, lcp: window.__lcp || 0,
  };
})()
'''

def load_and_measure(devtools, url, timeout=30):
  devtools.call('Page.navigate', 30, url=url)
  harness.wait_for(lambda: devtools.evaluate(
      'document.readyState === "complete" && performance.getEntriesByType("navigation")[0] && performance.getEntriesByType("navigation")[0].loadEventEnd > 0',
      timeout=10), timeout, f'load of {url}')
  time.sleep(0.4)
  return devtools.evaluate(MEASURE_JS, timeout=10)

def measure_rep(browser_binary, origin, pages, settle=1.0):
  """One repetition: fresh browser, warmup pass over all pages, then measured pass.
  Returns {'sum': total_loadEventEnd_ms, 'pages': {page: metrics}}."""
  profile = pathlib.Path(tempfile.mkdtemp(prefix='rb-pageload-'))
  cmd = [str(browser_binary), *harness.BROWSER_FLAGS, f'--user-data-dir={profile}', 'about:blank']
  proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
  try:
    port = wait_port(profile, proc)
    wslist = json_url(port, 'json/list')
    page_ws = next(t['webSocketDebuggerUrl'] for t in wslist if t['type'] == 'page')
    devtools = harness.DevTools(page_ws)
    try:
      devtools.call('Page.enable', 30)
      devtools.call('Page.addScriptToEvaluateOnNewDocument', 30, source=LCP_HOOK)
      for page in pages:  # warmup pass — primes browser cache; not measured
        devtools.call('Page.navigate', 30, url=origin + page)
        harness.wait_for(lambda: devtools.evaluate('document.readyState === "complete"', timeout=10), 30, 'warmup load')
      time.sleep(settle)
      total = 0.0
      per_page = {}
      for iteration in range(3):
        for page in pages:
          m = load_and_measure(devtools, origin + page)
          if page not in per_page or m['loadEventEnd'] < per_page[page]['loadEventEnd']:
            per_page[page] = m
          total += m['loadEventEnd']
      return {'sum': total, 'pages': per_page}
    finally:
      devtools.close()
  finally:
    harness.stop_browser(proc)
    shutil.rmtree(profile, ignore_errors=True)

def compare(base, new, reps, seed):
  httpd, origin = serve(SITES)
  try:
    results = {'base': [], 'new': [], 'pages': PAGES}
    rng = random.Random(seed)
    for which in ('base', 'new'):  # warmup passes, discarded
      binary = base if which == 'base' else new
      measure_rep(str(binary), origin, PAGES)
    order = [('base', i) for i in range(reps)] + [('new', i) for i in range(reps)]
    rng.shuffle(order)
    for which, rep in order:
      binary = base if which == 'base' else new
      r = measure_rep(str(binary), origin, PAGES)
      results[which].append(r)
      print(f'{which} rep{rep}: sum {r["sum"]:.0f}ms', flush=True)
    return results
  finally:
    httpd.shutdown()

def analyze(results):
  pairs = list(zip(results['base'], results['new']))
  out = {}
  ratios = [math.log(n['sum'] / b['sum']) for b, n in pairs if b['sum'] and n['sum']]
  if len(ratios) >= 2:
    mean = statistics.mean(ratios)
    sd = statistics.stdev(ratios)
    ci = 1.96 * sd / math.sqrt(len(ratios))
    out['sum_loadEventEnd'] = {
      'pct_diff': (math.exp(mean) - 1) * 100, 'ci95': (math.exp(ci) - 1) * 100, 'n': len(ratios),
      'base_mean': statistics.mean(b['sum'] for b, _ in pairs),
      'new_mean': statistics.mean(n['sum'] for _, n in pairs),
    }
  # per-page metrics across the corpus
  for metric in ('loadEventEnd', 'fcp', 'lcp', 'domInteractive'):
    bratios = []
    for b, n in pairs:
      for page in results['pages']:
        bv = b['pages'].get(page, {}).get(metric)
        nv = n['pages'].get(page, {}).get(metric)
        if bv and nv:
          bratios.append(math.log(nv / bv))
    if len(bratios) >= 2:
      mean = statistics.mean(bratios)
      sd = statistics.stdev(bratios)
      ci = 1.96 * sd / math.sqrt(len(bratios))
      out[metric] = {'pct_diff': (math.exp(mean) - 1) * 100, 'ci95': (math.exp(ci) - 1) * 100, 'n': len(bratios)}
  return out

def resolve_binary(app):
  app = pathlib.Path(app)
  macos = app / 'Contents/MacOS'
  if macos.is_dir():
    bins = list(macos.iterdir())
    if bins:
      return bins[0]
  return app / 'Contents/MacOS/Chromium'

def main():
  import argparse
  p = argparse.ArgumentParser()
  p.add_argument('--base', required=True)
  p.add_argument('--new', required=True)
  p.add_argument('--reps', type=int, default=8)
  p.add_argument('--seed', type=int, default=1)
  p.add_argument('--label', default='pageload')
  args = p.parse_args()
  results = compare(resolve_binary(args.base), resolve_binary(args.new), args.reps, args.seed)
  analysis = analyze(results)
  OUTDIR.mkdir(exist_ok=True)
  out = OUTDIR / f'{args.label}-{int(time.time())}.json'
  out.write_text(json.dumps({'results': results, 'analysis': analysis, 'seed': args.seed, 'reps': args.reps}, indent=2))
  print(json.dumps(analysis, indent=2))
  print('wrote', out)

if __name__ == '__main__':
  main()
