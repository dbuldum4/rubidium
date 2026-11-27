#!/usr/bin/env python3
"""Traces a single page load of a mirrored corpus page; summarizes self-time by event name.
Usage: python3 trace_pageload.py <browser-binary> <url-path>"""
import json, pathlib, subprocess, sys, tempfile, threading, time, urllib.request, http.server, shutil, collections
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import speedometer as harness
from pageload import serve, wait_port, json_url, SITES, LCP_HOOK

def traced_load(browser_binary, url, categories='devtools.timeline'):
  profile = pathlib.Path(tempfile.mkdtemp(prefix='rb-pltrace-'))
  cmd = [str(browser_binary), *harness.BROWSER_FLAGS, f'--user-data-dir={profile}', 'about:blank']
  proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
  try:
    port = wait_port(profile, proc)
    browser = harness.DevTools(json_url(port, 'json/version')['webSocketDebuggerUrl'])
    wslist = json_url(port, 'json/list')
    page_ws = next(t['webSocketDebuggerUrl'] for t in wslist if t['type'] == 'page')
    devtools = harness.DevTools(page_ws)
    try:
      devtools.call('Page.enable', 30)
      # warm cache first (not traced)
      devtools.call('Page.navigate', 30, url=url)
      harness.wait_for(lambda: devtools.evaluate('document.readyState === "complete"', timeout=10), 30, 'warmup')
      time.sleep(1)
      browser.call('Tracing.start', 30, categories=categories, transferMode='ReportEvents')
      devtools.call('Page.navigate', 30, url=url)
      harness.wait_for(lambda: devtools.evaluate(
          'document.readyState === "complete" && performance.getEntriesByType("navigation")[0] && performance.getEntriesByType("navigation")[0].loadEventEnd > 0',
          timeout=10), 30, 'measured load')
      time.sleep(0.5)
      browser.call('Tracing.end', 30)
      deadline = time.monotonic() + 30
      while time.monotonic() < deadline:
        if any(e.get('method') == 'Tracing.tracingComplete' for e in browser.events): break
        try: browser.events.append(json.loads(browser._read_message()))
        except Exception: break
    finally:
      devtools.close(); browser.close()
  finally:
    harness.stop_browser(proc)
    shutil.rmtree(profile, ignore_errors=True)
  events = []
  for event in browser.events:
    if event.get('method') == 'Tracing.dataCollected':
      events.extend(event['params'].get('value', ()))
  return events

def summarize(events):
  # self-time per event name on the renderer main thread (busy thread with most events)
  bythread = collections.Counter()
  for e in events:
    if e.get('ph') == 'X': bythread[(e.get('pid'), e.get('tid'))] += e.get('dur', 0)
  if not bythread: return {}
  key = max(bythread, key=bythread.get)
  arr = sorted([x for x in events if x.get('ph') == 'X' and (x.get('pid'), x.get('tid')) == key], key=lambda x: (x['ts'], -x['dur']))
  stack = []; parent = {}
  for i, ev in enumerate(arr):
    while stack and arr[stack[-1]]['ts'] + arr[stack[-1]]['dur'] <= ev['ts']: stack.pop()
    if stack: parent[i] = stack[-1]
    stack.append(i)
  selfv = {i: ev['dur'] for i, ev in enumerate(arr)}
  for i, ev in enumerate(arr):
    if i in parent: selfv[parent[i]] -= ev['dur']
  out = collections.Counter()
  for i, ev in enumerate(arr):
    out[ev['name']] += max(0, selfv[i]) / 1000
  return out

def main():
  binary = sys.argv[1]
  path = sys.argv[2] if len(sys.argv) > 2 else '/www.cnn.com/index.html'
  httpd, origin = serve(SITES)
  try:
    events = traced_load(binary, origin + path)
  finally:
    httpd.shutdown()
  sc = summarize(events)
  total = sum(sc.values())
  print('total main-thread self ms: %.1f' % total)
  for name, ms in sc.most_common(25):
    print('%7.1fms %5.1f%%  %s' % (ms, 100 * ms / total if total else 0, name))

if __name__ == '__main__':
  main()
