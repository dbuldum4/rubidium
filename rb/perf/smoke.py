#!/usr/bin/env python3
# Copyright 2026 The Rubidium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Small browser-functionality smoke test for Rb performance candidates.

This is not a web-platform-test replacement. It checks that one real browser
launch can execute representative web platform features whose breakage would
make a performance change unacceptable.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

from speedometer import BROWSER_FLAGS, DevTools, Server, resolve_browser, stop_browser, wait_for


_SMOKE_JS = r'''
(async () => {
  const results = {};
  const test = async (name, fn) => {
    try {
      const value = await fn();
      results[name] = {ok: value === undefined ? true : !!value};
    } catch (error) {
      results[name] = {ok: false, error: String(error && error.stack || error)};
    }
  };

  await test('dom-layout', () => {
    const host = document.createElement('div');
    host.innerHTML = '<style>#rb-smoke{width:37px;height:19px}</style><div id="rb-smoke"></div>';
    document.body.appendChild(host);
    const rect = document.getElementById('rb-smoke').getBoundingClientRect();
    host.remove();
    return rect.width === 37 && rect.height === 19;
  });

  await test('js-math-and-strings', () => {
    const values = [];
    for (let i = 0; i < 1000; i++)
      values.push(Math.sqrt(i).toString(16));
    return values.length === 1000 && JSON.parse(JSON.stringify(values))[999] === values[999];
  });

  await test('wasm', async () => {
    // (module (func (export "add") (param i32 i32) (result i32)
    //   local.get 0 local.get 1 i32.add))
    const bytes = new Uint8Array([
      0,97,115,109,1,0,0,0,1,7,1,96,2,127,127,1,127,3,2,1,0,
      7,7,1,3,97,100,100,0,0,10,9,1,7,0,32,0,32,1,106,11
    ]);
    const module = await WebAssembly.compile(bytes);
    return new WebAssembly.Instance(module).exports.add(19, 23) === 42;
  });

  await test('canvas2d', () => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 2;
    const context = canvas.getContext('2d');
    context.fillStyle = '#f00';
    context.fillRect(0, 0, 2, 2);
    return Array.from(context.getImageData(0, 0, 1, 1).data).join(',') === '255,0,0,255';
  });

  await test('webgl', () => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl)
      return false;
    gl.clearColor(1, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const pixel = new Uint8Array(4);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    return pixel[0] > 200 && pixel[3] === 255;
  });

  await test('workers', async () => {
    const source = 'onmessage = event => postMessage(event.data * 2)';
    const worker = new Worker(URL.createObjectURL(new Blob([source], {type: 'text/javascript'})));
    try {
      return await new Promise((resolve, reject) => {
        worker.onmessage = event => resolve(event.data === 42);
        worker.onerror = reject;
        worker.postMessage(21);
      });
    } finally {
      worker.terminate();
    }
  });

  await test('webcrypto', async () => {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('abc'));
    return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('') ===
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';
  });

  await test('storage', async () => {
    const key = 'rb-smoke';
    localStorage.setItem(key, 'ok');
    const result = localStorage.getItem(key) === 'ok';
    localStorage.removeItem(key);
    const databases = indexedDB ? await indexedDB.databases?.() ?? [] : [];
    return result && Array.isArray(databases);
  });

  await test('media-capabilities', () => {
    const video = document.createElement('video');
    return video.canPlayType('video/mp4; codecs="avc1.42E01E"') !== '' &&
      video.canPlayType('video/webm; codecs="vp9"') !== '';
  });

  return results;
})()
'''


def smoke(binary, browser_args):
  profile = pathlib.Path(tempfile.mkdtemp(prefix='rb-smoke-profile-'))
  cmd = [str(binary), *BROWSER_FLAGS, f'--user-data-dir={profile}',
         *browser_args, 'about:blank']
  proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
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
      import urllib.request
      with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as r:
        pages = [t for t in json.load(r) if t['type'] == 'page']
      return pages[0]['webSocketDebuggerUrl'] if pages else None

    devtools = DevTools(wait_for(find_page, 30, 'a page target'))
    try:
      with Server() as origin:
        devtools.call('Page.enable', 30)
        devtools.call('Page.navigate', 30, url=origin)
        wait_for(lambda: devtools.evaluate(
            'document.readyState === "complete"'), 30, 'the smoke page')
        return devtools.evaluate(_SMOKE_JS, timeout=60, await_promise=True)
    finally:
      devtools.close()
  finally:
    stop_browser(proc)
    shutil.rmtree(profile, ignore_errors=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  parser.add_argument('--browser', required=True,
                      help='binary, .app bundle, or cft:<version>')
  parser.add_argument('--browser-arg', action='append', default=[])
  parser.add_argument('--out', type=pathlib.Path,
                      default=pathlib.Path(__file__).resolve().parents[2] /
                      'out' / 'rb-perf')
  args = parser.parse_args()
  binary = resolve_browser(args.browser, args.out)
  results = smoke(binary, args.browser_arg)
  failed = {name: result for name, result in results.items()
            if not result.get('ok')}
  print(json.dumps({'browser': str(binary), 'results': results}, indent=1))
  if failed:
    print('FAILED: ' + ', '.join(failed), file=sys.stderr)
    return 1
  print(f'PASS: {len(results)} smoke checks')
  return 0


if __name__ == '__main__':
  sys.exit(main())
