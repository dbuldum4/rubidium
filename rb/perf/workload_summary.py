#!/usr/bin/env python3
"""Print workload time shares from a speedometer.py result.json."""

import collections
import json
import sys


def main():
  if len(sys.argv) < 2 or len(sys.argv) > 3:
    print('usage: workload_summary.py RESULT_JSON [base|new]', file=sys.stderr)
    return 2

  path = sys.argv[1]
  with open(path) as f:
    report = json.load(f)
  side = sys.argv[2] if len(sys.argv) == 3 else (
      'new' if 'new' in report.get('runs', {}) else 'base')
  runs = report['runs'][side]
  count = len(runs)

  print(f'{path} {side} launches {count}')
  print('scores', [round(run['score'], 2) for run in runs])
  if 'summary' in report and 'delta' in report['summary']:
    delta = report['summary']['delta']
    print('delta %+.2f%% ± %.2f%%' %
          (delta['diff_percent'], delta.get('ci95_percent', float('nan'))))

  # Speedometer keys are Suite/Test/Sync and Suite/Test/Async. Sum per-test
  # means, then average those totals across launches. This is wall-clock
  # workload time, not CPU time.
  totals = collections.defaultdict(float)
  suite_totals = collections.defaultdict(float)
  for run in runs:
    suites = collections.defaultdict(float)
    for key, metric in run['metrics'].items():
      parts = key.split('/')
      if len(parts) == 3 and parts[-1] in ('Sync', 'Async'):
        totals[parts[-1]] += metric['mean']
        totals['Total'] += metric['mean']
        suites[parts[0]] += metric['mean']
    for suite, value in suites.items():
      suite_totals[suite] += value

  for mapping in (totals, suite_totals):
    for key in list(mapping):
      mapping[key] /= count

  print('\nphase shares:')
  for key in ('Sync', 'Async', 'Total'):
    print('%5s %8.2f ms' % (key, totals[key]))
  print('sync %.1f%% async %.1f%%' %
        (100 * totals['Sync'] / totals['Total'],
         100 * totals['Async'] / totals['Total']))
  print('\ntop suites by measured test time:')
  for suite, value in sorted(suite_totals.items(),
                             key=lambda item: item[1], reverse=True)[:15]:
    print('%8.2f ms  %s' % (value, suite))
  return 0


if __name__ == '__main__':
  sys.exit(main())
