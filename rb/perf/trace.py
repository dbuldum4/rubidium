#!/usr/bin/env python3
# Copyright 2026 The Rubidium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Capture and summarize a Chrome tracing profile of one Speedometer run."""

import argparse
import collections
import gzip
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import speedometer as harness


def wait_port(profile, proc):
  def read_port():
    if proc.poll() is not None:
      raise RuntimeError(f'Browser exited with {proc.returncode}')
    port_file = profile / 'DevToolsActivePort'
    lines = port_file.read_text().split() if port_file.exists() else []
    return lines[0] if lines else None
  return harness.wait_for(read_port, 60, 'DevToolsActivePort')


def json_url(port, path):
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/{path}') as reply:
    return json.load(reply)


def pump_until(devtools, method, timeout=30):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if any(event.get('method') == method for event in devtools.events):
      return
    try:
      devtools.events.append(json.loads(devtools._read_message()))
    except (OSError, RuntimeError) as error:
      raise RuntimeError(f'waiting for {method}: {error}') from error
  raise RuntimeError(f'Timed out waiting for {method}')


def traced_events(browser, url, args):
  profile = pathlib.Path(tempfile.mkdtemp(prefix='rb-perf-trace-'))
  cmd = [str(browser), *harness.BROWSER_FLAGS, f'--user-data-dir={profile}',
         *args.browser_arg, 'about:blank']
  proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
  try:
    port = wait_port(profile, proc)
    browser_client = harness.DevTools(
        json_url(port, 'json/version')['webSocketDebuggerUrl'])
    page_client = None
    try:
      pages = [target for target in json_url(port, 'json/list')
               if target['type'] == 'page']
      page_client = harness.DevTools(pages[0]['webSocketDebuggerUrl'])
      page_client.call('Page.enable', 30)
      page_client.call('Page.navigate', 30, url=url)
      harness.wait_for(
          lambda: page_client.evaluate(
              'document.readyState === "complete" && '
              '!!globalThis.benchmarkClient'), 60, 'Speedometer to load')
      time.sleep(args.settle_seconds)
      # Start after the benchmark harness itself has loaded. Startup parsing and
      # compilation are important for end-to-end latency, but this profile is
      # meant to attribute the scored interaction loop.
      browser_client.call('Tracing.start', 30,
                          categories='devtools.timeline,blink.user_timing',
                          transferMode='ReportEvents')
      result = json.loads(
          page_client.evaluate(harness._RUN_JS, timeout=args.timeout,
                               await_promise=True))
      browser_client.call('Tracing.end', 30)
      pump_until(browser_client, 'Tracing.tracingComplete')
    finally:
      if page_client is not None:
        page_client.close()
      browser_client.close()
  finally:
    harness.stop_browser(proc)
    shutil.rmtree(profile, ignore_errors=True)
  events = []
  for event in browser_client.events:
    if event.get('method') == 'Tracing.dataCollected':
      events.extend(event['params'].get('value', ()))
  return result, events


def event_end(event):
  return event.get('ts', 0) + event.get('dur', 0)


def add_self_time(events, self_time):
  """Computes exclusive complete-event time assuming nested thread slices."""
  by_thread = collections.defaultdict(list)
  for event in events:
    if event.get('ph') == 'X' and 'ts' in event and 'dur' in event:
      by_thread[event.get('tid')].append(event)
      self_time[event['name']] += event['dur']
  child_time = collections.Counter()
  for thread_events in by_thread.values():
    stack = []
    for event in sorted(thread_events,
                        key=lambda item: (item['ts'], -item['dur'])):
      while stack and event['ts'] >= event_end(stack[-1]):
        stack.pop()
      if stack:
        child_time[id(stack[-1])] += event['dur']
      stack.append(event)
  for thread_events in by_thread.values():
    for event in thread_events:
      self_time[event['name']] -= child_time[id(event)]



def subtract_children(events):
  """Returns (name, start, end) leaf/self segments for nested thread slices."""
  events = sorted(events, key=lambda item: (item['ts'], -item['dur']))
  segments = []

  def emit(node, index):
    start, end = node['ts'], event_end(node)
    cursor = start
    while index < len(events):
      child = events[index]
      if child['ts'] >= end:
        break
      if child is node:
        index += 1
        continue
      child_end = event_end(child)
      if child_end <= end and child['ts'] >= cursor:
        if child['ts'] > cursor:
          segments.append((node['name'], cursor, child['ts']))
        index = emit(child, index)
        cursor = max(cursor, child_end)
      else:
        index += 1
    if cursor < end:
      segments.append((node['name'], cursor, end))
    return index

  index = 0
  while index < len(events):
    index = emit(events[index], index + 1)
  return segments


def intersect_ms(start, end, windows):
  total = 0
  for window_start, window_end in windows:
    total += max(0, min(end, window_end) - max(start, window_start))
  return total


def scored_attribution(events):
  pending = collections.defaultdict(list)
  windows = {'sync': collections.defaultdict(list),
             'async': collections.defaultdict(list)}
  for event in sorted((e for e in events if e.get('cat') == 'blink.user_timing'),
                      key=lambda item: item.get('ts', 0)):
    name = event.get('name', '')
    if not (name.endswith('-sync') or name.endswith('-async')):
      continue
    key = (event.get('pid'), event.get('tid'), name)
    if event.get('ph') == 'b':
      pending[key].append(event['ts'])
    elif event.get('ph') == 'e' and pending[key]:
      start = pending[key].pop(0)
      kind = 'sync' if name.endswith('-sync') else 'async'
      windows[kind][(event.get('pid'), event.get('tid'))].append(
          (start, event['ts']))
  result = {}
  for kind, by_thread in windows.items():
    for pid_tid, spans in by_thread.items():
      spans.sort()
      thread_events = [
          event for event in events
          if event.get('ph') == 'X' and 'ts' in event and 'dur' in event and
          (event.get('pid'), event.get('tid')) == pid_tid
      ]
      attribution = collections.Counter()
      busy = 0
      for name, start, end in subtract_children(thread_events):
        overlap = intersect_ms(start, end, spans)
        if overlap:
          attribution[name] += overlap
          busy += overlap
      total = sum(end - start for start, end in spans)
      result.setdefault(kind, {
          'measured_milliseconds': round(total / 1000, 3),
          'attributed_busy_milliseconds': round(busy / 1000, 3),
          'unattributed_or_idle_milliseconds': round((total - busy) / 1000, 3),
          'self_time_by_event': [],
      })
      result[kind]['self_time_by_event'] = [{
          'name': name,
          'milliseconds': round(value / 1000, 3),
      } for name, value in attribution.most_common(30)]
  return result

def summarize(events):
  process_names = {}
  thread_names = {}
  for event in events:
    if event.get('ph') != 'M':
      continue
    if event.get('name') == 'process_name':
      process_names[event.get('pid')] = event.get('args', {}).get('name', '')
    elif event.get('name') == 'thread_name':
      thread_names[(event.get('pid'), event.get('tid'))] = (
          event.get('args', {}).get('name', ''))

  self_time = collections.Counter()
  counts = collections.Counter()
  thread_time = collections.Counter()
  for event in events:
    if event.get('ph') == 'X' and 'dur' in event:
      counts[event['name']] += 1
  add_self_time(events, self_time)
  for event in events:
    if event.get('ph') == 'X' and 'dur' in event:
      pid_tid = (event.get('pid'), event.get('tid'))
      thread_time[pid_tid] += event['dur']

  def top(counter, limit=40):
    return [{
        'name': name,
        'milliseconds': round(value / 1000, 3),
    } for name, value in counter.most_common(limit)]

  threads = []
  for pid_tid, total in thread_time.most_common(30):
    pid, tid = pid_tid
    threads.append({
        'process': process_names.get(pid, str(pid)),
        'thread': thread_names.get(pid_tid, str(tid)),
        'inclusive_slice_milliseconds': round(total / 1000, 3),
    })
  return {
      'events': len(events),
      'process_names': sorted(set(process_names.values())),
      'threads_by_inclusive_slice_time': threads,
      # These are exclusive/leaf times within nested complete slices. They are
      # the useful first-pass attribution; inclusive totals double-count nesting.
      'self_time_by_event': top(self_time),
      'scored_attribution': scored_attribution(events),
      'count_by_event': [{
          'name': name,
          'count': count,
      } for name, count in counts.most_common(40)],
  }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--browser', required=True)
  parser.add_argument('--output', type=pathlib.Path)
  parser.add_argument('--iterations', type=int, default=1)
  parser.add_argument('--settle-seconds', type=float, default=1)
  parser.add_argument('--timeout', type=int, default=240)
  parser.add_argument('--browser-arg', action='append', default=[])
  args = parser.parse_args()

  revision = harness.ensure_speedometer()
  browser = harness.resolve_browser(args.browser, harness.DEFAULT_OUT)
  if args.output is None:
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    args.output = harness.DEFAULT_OUT / 'traces' / f'{stamp}-speedometer.json.gz'
  args.output.parent.mkdir(parents=True, exist_ok=True)

  with harness.Server() as origin:
    url = f'{origin}/?iterationCount={args.iterations}'
    result, events = traced_events(browser, url, args)
  report = {
      'benchmark': 'Speedometer 3.1',
      'speedometer_revision': revision,
      'iterations': args.iterations,
      'browser': str(browser),
      'score': result.get('metrics', {}).get('Score', {}).get('mean'),
      'summary': summarize(events),
      'trace_events': events,
  }
  with gzip.open(args.output, 'wt', encoding='utf-8') as output:
    json.dump(report, output, separators=(',', ':'))
  summary = report['summary']
  print(f'Wrote {args.output} ({summary["events"]} events)')
  print('Top exclusive event time:')
  for row in summary['self_time_by_event'][:15]:
    print(f'  {row["milliseconds"]:8.1f} ms  {row["name"]}')


if __name__ == '__main__':
  main()
