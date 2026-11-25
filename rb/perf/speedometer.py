#!/usr/bin/env python3
# Copyright 2026 The Rubidium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Reproducible Speedometer 3.1 runs for Rubidium (Rb).

Standard library only, so it runs unchanged on macOS and Linux and needs no
chromedriver: the browser is driven over the DevTools protocol.

  rb/perf/speedometer.py run --browser cft:156.0.8078.47
  rb/perf/speedometer.py run --browser out/Release/Rubidium.app
  rb/perf/speedometer.py compare --base cft:156.0.8078.47 --new out/Release/chrome
  rb/perf/speedometer.py setup --browser cft:156.0.8078.47

See rb/perf/README.md.
"""

import argparse
import base64
import datetime
import http.server
import json
import math
import os
import pathlib
import platform
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

SRC = pathlib.Path(__file__).resolve().parents[2]
SPEEDOMETER_DIR = SRC / 'third_party' / 'speedometer' / 'v3.1'
SPEEDOMETER_SUBMODULE = 'third_party/speedometer/v3.1'
SPEEDOMETER_URL = ('https://chromium.googlesource.com/external/github.com/'
                   'WebKit/Speedometer')
DEFAULT_OUT = SRC / 'out' / 'rb-perf'
CFT_URL = ('https://storage.googleapis.com/chrome-for-testing-public/'
           '{version}/{platform}/chrome-{platform}.zip')

# Every run uses exactly these flags plus a fresh profile. They only remove
# sources of background work and first-run UI; none of them changes how the
# engine executes the benchmark.
BROWSER_FLAGS = (
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-default-apps',
    '--disable-extensions',
    '--disable-search-engine-choice-screen',
    '--disable-features=Translate,MediaRouter,OptimizationHints',
    '--password-store=basic',
    '--use-mock-keychain',
    '--window-position=0,0',
    '--window-size=1280,1000',
    '--remote-debugging-port=0',
)

# Two-sided 95% Student t critical values, indexed by degrees of freedom.
_T95 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262,
        2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052,
        2.048, 2.045, 2.042)


# Beyond the table, use the value for the nearest tabulated df below, which
# errs towards a wider interval.
_T95_LARGE = ((120, 1.980), (60, 2.000), (40, 2.021), (30, 2.042))


def t95(df):
  if df < 1:
    return math.nan
  if df < len(_T95) + 1:
    return _T95[int(df) - 1]
  return next(value for floor, value in _T95_LARGE if df >= floor)


def summarize(values):
  n = len(values)
  mean = statistics.fmean(values)
  stdev = statistics.stdev(values) if n > 1 else 0.0
  return {
      'n': n,
      'mean': mean,
      'median': statistics.median(values),
      'stdev': stdev,
      'cv_percent': 100 * stdev / mean if mean else math.nan,
      'ci95': t95(n - 1) * stdev / math.sqrt(n) if n > 1 else math.nan,
      'min': min(values),
      'max': max(values),
  }


def welch(base, new):
  """Difference of means (new - base) with a Welch 95% interval."""
  b, n = summarize(base), summarize(new)
  diff = n['mean'] - b['mean']
  result = {'diff': diff, 'diff_percent': 100 * diff / b['mean']}
  vb, vn = b['stdev']**2 / b['n'], n['stdev']**2 / n['n']
  if b['n'] < 2 or n['n'] < 2 or vb + vn == 0:
    return {**result, 'ci95': math.nan, 'significant': False}
  df = (vb + vn)**2 / (vb**2 / (b['n'] - 1) + vn**2 / (n['n'] - 1))
  ci = t95(df) * math.sqrt(vb + vn)
  return {
      **result,
      'ci95': ci,
      'ci95_percent': 100 * ci / b['mean'],
      'significant': abs(diff) > ci,
  }


def git(*args, cwd=SRC):
  return subprocess.run(('git', *args), cwd=cwd, check=True,
                        capture_output=True, text=True).stdout.strip()


def pinned_speedometer_revision():
  # The gitlink is the same pin gclient uses (DEPS speedometer_3.1_revision).
  return git('ls-tree', 'HEAD', SPEEDOMETER_SUBMODULE).split()[2]


def ensure_speedometer():
  """Checks out Speedometer 3.1 at the pinned revision; returns the revision."""
  want = pinned_speedometer_revision()
  if not (SPEEDOMETER_DIR / '.git').exists():
    print(f'Fetching Speedometer 3.1 @ {want}', file=sys.stderr)
    SPEEDOMETER_DIR.mkdir(parents=True, exist_ok=True)
    git('init', '-q', '.', cwd=SPEEDOMETER_DIR)
    git('fetch', '-q', '--depth', '1', SPEEDOMETER_URL, want,
        cwd=SPEEDOMETER_DIR)
    git('checkout', '-q', 'FETCH_HEAD', cwd=SPEEDOMETER_DIR)
  have = git('rev-parse', 'HEAD', cwd=SPEEDOMETER_DIR)
  if have != want:
    sys.exit(f'{SPEEDOMETER_SUBMODULE} is at {have}, expected {want}. '
             'Run `gclient sync` or delete the directory.')
  if git('status', '--porcelain', cwd=SPEEDOMETER_DIR):
    sys.exit(f'{SPEEDOMETER_SUBMODULE} has local modifications.')
  return want


def cft_platform():
  machine = platform.machine().lower()
  if sys.platform == 'darwin':
    return 'mac-arm64' if machine == 'arm64' else 'mac-x64'
  if sys.platform.startswith('linux') and machine in ('x86_64', 'amd64'):
    return 'linux64'
  sys.exit(f'No Chrome for Testing build for {sys.platform}/{machine}')


def ensure_cft(version, out_dir):
  plat = cft_platform()
  root = out_dir / 'browsers' / f'cft-{version}-{plat}'
  if plat.startswith('mac'):
    binary = (root / f'chrome-{plat}' / 'Google Chrome for Testing.app' /
              'Contents' / 'MacOS' / 'Google Chrome for Testing')
  else:
    binary = root / f'chrome-{plat}' / 'chrome'
  if binary.exists():
    return binary
  url = CFT_URL.format(version=version, platform=plat)
  print(f'Downloading {url}', file=sys.stderr)
  root.mkdir(parents=True, exist_ok=True)
  archive = root / 'chrome.zip'
  urllib.request.urlretrieve(url, archive)
  if sys.platform == 'darwin':
    # zipfile drops symlinks and modes that the .app bundle needs.
    subprocess.run(('ditto', '-x', '-k', archive, root), check=True)
  else:
    with zipfile.ZipFile(archive) as z:
      for info in z.infolist():
        path = z.extract(info, root)
        mode = info.external_attr >> 16
        if mode:
          os.chmod(path, mode & 0o777)
  archive.unlink()
  return binary


def resolve_browser(spec, out_dir):
  """Accepts cft:<version>, a binary, or a macOS .app bundle."""
  if spec.startswith('cft:'):
    return ensure_cft(spec[4:], out_dir)
  path = pathlib.Path(spec).expanduser().resolve()
  if path.suffix == '.app':
    binaries = [p for p in (path / 'Contents' / 'MacOS').iterdir()
                if p.is_file() and os.access(p, os.X_OK)]
    if len(binaries) != 1:
      sys.exit(f'Expected one executable in {path}/Contents/MacOS')
    path = binaries[0]
  if not (path.is_file() and os.access(path, os.X_OK)):
    sys.exit(f'Not an executable browser: {spec}')
  return path


class _QuietHandler(http.server.SimpleHTTPRequestHandler):

  def log_message(self, *args):
    pass

  def handle(self):
    try:
      super().handle()
    except (BrokenPipeError, ConnectionResetError):
      pass  # The browser dropped the connection, e.g. while shutting down.

  def end_headers(self):
    self.send_header('Cache-Control', 'no-store')
    super().end_headers()


class Server:
  """Serves the pinned Speedometer checkout on 127.0.0.1."""

  def __enter__(self):
    handler = lambda *a, **kw: _QuietHandler(
        *a, directory=str(SPEEDOMETER_DIR), **kw)
    self._httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{self._httpd.server_address[1]}'

  def __exit__(self, *exc):
    self._httpd.shutdown()
    self._httpd.server_close()


class Xvfb:
  """A private X display so runs do not share a compositor with the desktop."""

  def __enter__(self):
    read_fd, write_fd = os.pipe()
    self._proc = subprocess.Popen(
        ('Xvfb', '-displayfd', str(write_fd), '-screen', '0', '1920x1200x24',
         '-nolisten', 'tcp'),
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    os.close(write_fd)
    with os.fdopen(read_fd) as f:
      number = f.readline().strip()
    if not number:
      sys.exit('Xvfb failed to start')
    return f':{number}'

  def __exit__(self, *exc):
    self._proc.terminate()
    self._proc.wait()


class DevTools:
  """Minimal DevTools-protocol client over a hand-rolled WebSocket."""

  def __init__(self, ws_url):
    address, self._path = ws_url[len('ws://'):].split('/', 1)
    host, port = address.split(':')
    self._sock = socket.create_connection((host, int(port)))
    self._buffer = b''
    self._next_id = 0
    key = base64.b64encode(os.urandom(16)).decode()
    self._sock.sendall(
        (f'GET /{self._path} HTTP/1.1\r\nHost: {address}\r\n'
         'Upgrade: websocket\r\nConnection: Upgrade\r\n'
         f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
         ).encode())
    while b'\r\n\r\n' not in self._buffer:
      self._buffer += self._recv()
    head, self._buffer = self._buffer.split(b'\r\n\r\n', 1)
    if b' 101 ' not in head.split(b'\r\n')[0]:
      raise RuntimeError(f'WebSocket handshake failed: {head!r}')

  def _recv(self):
    data = self._sock.recv(1 << 16)
    if not data:
      raise RuntimeError('Browser closed the DevTools connection')
    return data

  def _read(self, n):
    while len(self._buffer) < n:
      self._buffer += self._recv()
    data, self._buffer = self._buffer[:n], self._buffer[n:]
    return data

  def _send(self, payload, opcode=1):
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
      head = struct.pack('!BB', 0x80 | opcode, 0x80 | n)
    elif n < 1 << 16:
      head = struct.pack('!BBH', 0x80 | opcode, 0x80 | 126, n)
    else:
      head = struct.pack('!BBQ', 0x80 | opcode, 0x80 | 127, n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    self._sock.sendall(head + mask + masked)

  def _read_message(self):
    message = b''
    while True:
      first, second = self._read(2)
      n = second & 0x7f
      if n == 126:
        n, = struct.unpack('!H', self._read(2))
      elif n == 127:
        n, = struct.unpack('!Q', self._read(8))
      payload = self._read(n)
      opcode = first & 0x0f
      if opcode == 8:
        raise RuntimeError('Browser closed the DevTools connection')
      if opcode == 9:
        self._send(payload, opcode=10)
        continue
      if opcode == 10:
        continue
      message += payload
      if first & 0x80:
        return message

  def call(self, method, timeout, **params):
    self._next_id += 1
    self._sock.settimeout(timeout)
    self._send(json.dumps({'id': self._next_id, 'method': method,
                           'params': params}).encode())
    while True:
      reply = json.loads(self._read_message())
      if reply.get('id') != self._next_id:
        continue
      if 'error' in reply:
        raise RuntimeError(f'{method}: {reply["error"]}')
      return reply['result']

  def evaluate(self, expression, timeout=30, await_promise=False):
    result = self.call('Runtime.evaluate', timeout, expression=expression,
                       returnByValue=True, awaitPromise=await_promise)
    if 'exceptionDetails' in result:
      raise RuntimeError(f'JS exception: {result["exceptionDetails"]}')
    return result['result'].get('value')

  def close(self):
    self._sock.close()


# Starts the benchmark and resolves when it ends. Nothing else talks to the
# page while it runs, so the harness adds no work to the measured thread.
_RUN_JS = '''
new Promise((resolve) => {
  const client = globalThis.benchmarkClient;
  const finish = client.didFinishLastIteration.bind(client);
  const fail = client.handleError.bind(client);
  client.didFinishLastIteration = (metrics) => {
    finish(metrics);
    resolve(JSON.stringify({metrics, iterations: client._measuredValuesList}));
  };
  client.handleError = (error) => {
    fail(error);
    resolve(JSON.stringify({error: String(error && error.stack || error)}));
  };
  client.start();
})
'''


def cpu_times():
  """Host-wide CPU jiffies from /proc/stat, or None where unavailable."""
  try:
    fields = pathlib.Path('/proc/stat').read_text().split('\n', 1)[0].split()
  except OSError:
    return None
  values = [int(v) for v in fields[1:9]]
  return {'total': sum(values), 'idle': values[3] + values[4],
          'steal': values[7]}


def cpu_delta(before, after):
  """Share of CPU time stolen by the hypervisor / used, over a window."""
  if not before or not after or after['total'] == before['total']:
    return {}
  total = after['total'] - before['total']
  steal = after['steal'] - before['steal']
  busy = total - (after['idle'] - before['idle'])
  return {
      'steal_percent': 100 * steal / total,
      # Of the CPU time the guest wanted, how much the host took away.
      'steal_of_busy_percent': 100 * steal / busy if busy else 0.0,
      'busy_percent': 100 * busy / total,
  }


def wait_for(predicate, timeout, what):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    value = predicate()
    if value:
      return value
    time.sleep(0.1)
  raise RuntimeError(f'Timed out waiting for {what}')


# macOS reports EPERM, not ESRCH, for a group whose members are all zombies.
_GROUP_GONE = (ProcessLookupError, PermissionError)


def stop_browser(proc):
  """Ends the whole browser process group and waits until it is gone."""
  for sig in (signal.SIGTERM, signal.SIGKILL):
    try:
      os.killpg(proc.pid, sig)
    except _GROUP_GONE:
      break
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
      try:
        os.killpg(proc.pid, 0)
      except _GROUP_GONE:
        break
      proc.poll()
      time.sleep(0.1)
    else:
      continue
    break
  proc.wait()


def run_once(binary, url, args, env):
  """Launches a fresh browser, runs Speedometer once, returns its metrics."""
  profile = pathlib.Path(tempfile.mkdtemp(prefix='rb-perf-profile-'))
  cmd = [str(binary), *BROWSER_FLAGS, f'--user-data-dir={profile}',
         *args.browser_arg, 'about:blank']
  proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
  try:
    port_file = profile / 'DevToolsActivePort'

    def read_port():
      if proc.poll() is not None:
        raise RuntimeError(f'Browser exited with {proc.returncode}')
      lines = port_file.read_text().split() if port_file.exists() else []
      return lines[0] if lines else None

    port = wait_for(read_port, 60, 'DevToolsActivePort')

    def find_page():
      with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as r:
        pages = [t for t in json.load(r) if t['type'] == 'page']
      return pages[0]['webSocketDebuggerUrl'] if pages else None

    devtools = DevTools(wait_for(find_page, 30, 'a page target'))
    try:
      version = devtools.call('Browser.getVersion', 30)
      devtools.call('Page.enable', 30)
      devtools.call('Page.navigate', 30, url=url)
      wait_for(
          lambda: devtools.evaluate(
              'document.readyState === "complete" && '
              '!!globalThis.benchmarkClient'), 60, 'Speedometer to load')
      time.sleep(args.settle_seconds)
      load_before = os.getloadavg()[0]
      cpu_before = cpu_times()
      started = time.monotonic()
      result = json.loads(
          devtools.evaluate(_RUN_JS, timeout=args.timeout,
                            await_promise=True))
      duration = time.monotonic() - started
      cpu = cpu_delta(cpu_before, cpu_times())
    finally:
      devtools.close()
  finally:
    stop_browser(proc)
    shutil.rmtree(profile, ignore_errors=True)
  if 'error' in result:
    raise RuntimeError(f'Speedometer failed: {result["error"]}')
  metrics = result['metrics']
  return {
      'score': metrics['Score']['mean'],
      'iteration_scores': metrics['Score']['values'],
      'duration_seconds': round(duration, 1),
      'loadavg_before': load_before,
      'host_cpu': cpu,
      'browser_version': version['product'],
      'user_agent': version['userAgent'],
      'js_version': version['jsVersion'],
      'metrics': {
          name: {'mean': m['mean'], 'unit': m['unit'], 'values': m['values']}
          for name, m in metrics.items()
          if not name.startswith('Iteration-')
      },
  }


def machine_info():
  info = {
      'platform': platform.platform(),
      'machine': platform.machine(),
      'cpu_count': os.cpu_count(),
  }
  try:
    if sys.platform == 'darwin':
      sysctl = lambda key: subprocess.run(
          ('sysctl', '-n', key), capture_output=True, text=True).stdout.strip()
      info['cpu'] = sysctl('machdep.cpu.brand_string')
      info['model'] = sysctl('hw.model')
      info['memory_bytes'] = int(sysctl('hw.memsize'))
    else:
      cpuinfo = pathlib.Path('/proc/cpuinfo').read_text()
      info['cpu'] = next(
          line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
          if line.startswith('model name'))
      info['virtualized'] = ' hypervisor' in cpuinfo
      meminfo = pathlib.Path('/proc/meminfo').read_text()
      info['memory_bytes'] = int(meminfo.split()[1]) * 1024
  except (OSError, StopIteration, ValueError):
    pass
  return info


def source_info():
  return {
      'commit': git('rev-parse', 'HEAD'),
      'dirty': bool(git('status', '--porcelain', '--untracked-files=no')),
  }


def print_summary(label, scores):
  s = summarize(scores)
  print(f'{label}: {s["mean"]:.2f} ± {s["ci95"]:.2f} (95% CI, n={s["n"]}, '
        f'median {s["median"]:.2f}, CV {s["cv_percent"]:.2f}%)')


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  sub = parser.add_subparsers(dest='command', required=True)
  common = argparse.ArgumentParser(add_help=False)
  common.add_argument('--runs', type=int, default=5,
                      help='browser launches per browser (default 5)')
  common.add_argument('--warmup-runs', type=int, default=0,
                      help='unscored warmup launches per browser (default 0); '
                      'recorded separately and excluded from the summary')
  common.add_argument('--iterations', type=int, default=10,
                      help='Speedometer iterationCount (default 10, the '
                      'official value)')
  common.add_argument('--out', type=pathlib.Path, default=DEFAULT_OUT)
  common.add_argument('--label', help='name for the result directory')
  common.add_argument('--browser-arg', action='append', default=[],
                      help='extra browser flag, applied to every browser; '
                      'recorded in the result')
  common.add_argument('--display', choices=('auto', 'inherit', 'xvfb'),
                      default='auto',
                      help='auto: private Xvfb on Linux, native elsewhere')
  common.add_argument('--settle-seconds', type=float, default=3)
  common.add_argument('--timeout', type=float, default=1800,
                      help='seconds allowed for one Speedometer run')
  run = sub.add_parser('run', parents=[common], help='measure one browser')
  run.add_argument('--browser', required=True,
                   help='binary, .app bundle, or cft:<version>')
  compare = sub.add_parser(
      'compare', parents=[common],
      help='measure two browsers, alternating launches')
  compare.add_argument('--base', required=True)
  compare.add_argument('--new', required=True)
  setup = sub.add_parser(
      'setup', help='fetch Speedometer and browsers without measuring')
  setup.add_argument('--browser', action='append', default=[])
  setup.add_argument('--out', type=pathlib.Path, default=DEFAULT_OUT)
  args = parser.parse_args()

  revision = ensure_speedometer()
  if args.command == 'setup':
    print(f'Speedometer 3.1 @ {revision}')
    for spec in args.browser:
      print(f'{spec}: {resolve_browser(spec, args.out)}')
    return
  if args.command == 'run':
    specs = {'browser': args.browser}
  else:
    specs = {'base': args.base, 'new': args.new}
  binaries = {k: resolve_browser(v, args.out) for k, v in specs.items()}

  stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
      '%Y%m%dT%H%M%SZ')
  result_dir = args.out / 'results' / '-'.join(
      filter(None, (stamp, args.command, args.label)))
  result_dir.mkdir(parents=True)

  use_xvfb = args.display == 'xvfb' or (args.display == 'auto' and
                                        sys.platform.startswith('linux'))
  env = dict(os.environ)
  runs = {k: [] for k in binaries}
  warmup_runs = {k: [] for k in binaries}
  report = {
      'benchmark': 'Speedometer 3.1',
      'speedometer_revision': revision,
      'iterations': args.iterations,
      'started': stamp,
      'command': sys.argv[1:],
      'browser_flags': [*BROWSER_FLAGS, *args.browser_arg],
      'display': 'xvfb' if use_xvfb else 'native',
      'machine': machine_info(),
      'source': source_info(),
      'browsers': {k: {'spec': specs[k], 'binary': str(binaries[k])}
                   for k in binaries},
      'warmup_runs': warmup_runs,
      'runs': runs,
  }

  with Server() as origin, (Xvfb() if use_xvfb else
                            tempfile.TemporaryDirectory()) as display:
    if use_xvfb:
      env['DISPLAY'] = display
      env.pop('WAYLAND_DISPLAY', None)
    url = f'{origin}/?iterationCount={args.iterations}'

    def record_result(name, result, index, total, warmup=False):
      destination = warmup_runs if warmup else runs
      destination[name].append(result)
      steal = result['host_cpu'].get('steal_of_busy_percent')
      steal_text = '' if steal is None else f', steal {steal:.1f}%'
      label = (f'[warmup {index + 1}/{total}]' if warmup else
               f'[{index + 1}/{total}]')
      print(f'{label} {name}: {result["score"]:.2f} '
            f'({result["browser_version"]}, {result["duration_seconds"]}s, '
            f'load {result["loadavg_before"]:.2f}{steal_text})', flush=True)
      (result_dir / 'result.json').write_text(json.dumps(report, indent=1))

    for i in range(args.warmup_runs):
      for name, binary in binaries.items():
        record_result(name, run_once(binary, url, args, env), i,
                      args.warmup_runs, warmup=True)

    for i in range(args.runs):
      # Alternating launches spreads machine drift evenly over both browsers.
      for name, binary in binaries.items():
        record_result(name, run_once(binary, url, args, env), i, args.runs)

  scores = {k: [r['score'] for r in v] for k, v in runs.items()}
  report['summary'] = {k: summarize(v) for k, v in scores.items()}
  print()
  for name, values in scores.items():
    print_summary(f'{name} ({specs[name]})', values)
  if args.command == 'compare':
    delta = welch(scores['base'], scores['new'])
    report['summary']['delta'] = delta
    verdict = 'significant' if delta['significant'] else 'not significant'
    print(f'new vs base: {delta["diff_percent"]:+.2f}% '
          f'± {delta.get("ci95_percent", math.nan):.2f}% ({verdict} at 95%)')
  (result_dir / 'result.json').write_text(json.dumps(report, indent=1))
  print(f'\nWrote {result_dir / "result.json"}')


if __name__ == '__main__':
  main()
