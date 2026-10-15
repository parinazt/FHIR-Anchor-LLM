"""Compare resource/property counts across repeated single and multiagent runs."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

TYPES = ('Patient', 'Condition', 'Observation', 'MedicationStatement', 'MedicationRequest')
METRICS = ('resources', 'properties')


def load_results(directory):
    """Last CSV row wins after --resume; count saved validated Bundles only."""
    latest = {}
    with (directory / 'results.csv').open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            if None in row or any(value is None for value in row.values()):
                continue
            latest[(row['file'], int(row['run-id']))] = row
    result = {}
    for (file, run), row in latest.items():
        counts = {kind: {metric: 0 for metric in METRICS} for kind in (*TYPES, 'ALL')}
        valid = row['fhir-valid'] == '1'
        if valid:
            path = directory / 'outputs' / f'{run}_{Path(file).stem}.json'
            if not path.is_file():
                raise ValueError(f'Validated Bundle is missing: {path}')
            bundle = json.loads(path.read_text(encoding='utf-8'))
            if bundle.get('resourceType') != 'Bundle' or bundle.get('type') != 'collection':
                raise ValueError(f'Not a collection Bundle: {path}')
            for entry in bundle.get('entry', []):
                resource = entry['resource']
                kind = resource['resourceType']
                if kind not in TYPES:
                    raise ValueError(f'Unsupported resource type {kind}: {path}')
                # Same definition as the previous comparison: top-level keys only.
                properties = len(set(resource) - {'resourceType', 'id'})
                for target in (kind, 'ALL'):
                    counts[target]['resources'] += 1
                    counts[target]['properties'] += properties
        result[(file, run)] = {'counts': counts, 'valid': valid}
    return result


def summarize(values):
    return {'n_runs': len(values), 'mean': statistics.mean(values),
            'std': statistics.stdev(values) if len(values) > 1 else None}


def aggregate(single, multi, expected_runs=5, allow_incomplete=False):
    datasets = {'single': single, 'multi': multi}
    keys = set(single) | set(multi)
    files = sorted({file for file, _ in keys})
    if not files:
        raise ValueError('No results to compare.')
    expected = set(range(1, expected_runs + 1))
    if any(run not in expected for _, run in keys):
        raise ValueError(f'Run IDs must be between 1 and {expected_runs}.')
    # Use only runs containing every case in BOTH methods. Totals then compare
    # identical datasets, and their SD is computed on per-run sums.
    common_runs = sorted(run for run in expected if all(
        (file, run) in data for file in files for data in datasets.values()))
    if len(common_runs) != expected_runs and not allow_incomplete:
        raise ValueError(f'Expected {expected_runs} complete paired runs; found {len(common_runs)}. '
                         'Finish both experiments or use --allow-incomplete for a provisional report.')
    if not common_runs:
        raise ValueError('No complete paired run across the same cases in both methods.')
    raw, summary = [], []
    for method, data in datasets.items():
        for case in [*files, 'TOTAL']:
            selected = files if case == 'TOTAL' else [case]
            for kind in (*TYPES, 'ALL'):
                for metric in METRICS:
                    values = []
                    for run in common_runs:
                        value = sum(data[(file, run)]['counts'][kind][metric] for file in selected)
                        values.append(value)
                        raw.append({'case': case, 'method': method, 'run_id': run,
                                    'resource_type': kind, 'metric': metric, 'value': value})
                    summary.append({'case': case, 'method': method, 'resource_type': kind,
                                    'metric': metric, **summarize(values)})
    metadata = {
        'expected_runs': expected_runs, 'included_runs': common_runs,
        'excluded_runs': sorted(expected - set(common_runs)), 'n_cases': len(files),
        'std_definition': 'Sample standard deviation across runs (ddof=1). Undefined for n=1.',
        'property_definition': 'Resource top-level keys excluding resourceType and id; '
                               'nested objects/arrays count as one property; Bundle/fullUrl excluded.',
        'failure_policy': 'Failed/invalid recorded executions contribute zero validated resources '
                          'and properties. Missing executions are not treated as zero.',
        'valid_executions': {method: sum(data[(file, run)]['valid'] for file in files
                                        for run in common_runs) for method, data in datasets.items()},
        'interpretation': 'Counts measure generated structure, not clinical completeness or correctness.',
    }
    return raw, summary, metadata


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render(summary, metadata, directory, page_size=25):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    lookup = {(r['case'], r['method'], r['resource_type'], r['metric']): r for r in summary}
    labels = {'resources': 'Number of resources', 'properties': 'Number of properties'}
    colors = {'single': '#3375b9', 'multi': '#e78935'}
    n = len(metadata['included_runs'])
    note = f'Mean ± sample SD across {n} runs; {metadata["n_cases"]} cases'
    if n == 1:
        note = f'Only one run available; SD cannot be calculated; {metadata["n_cases"]} cases'

    def save(fig, stem):
        fig.savefig(directory / f'{stem}.png', dpi=180, bbox_inches='tight')
        plt.close(fig)

    def series(cases, kind, metric, method):
        rows = [lookup[(case, method, kind, metric)] for case in cases]
        return [r['mean'] for r in rows], [r['std'] or 0 for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    for ax, metric in zip(axes, METRICS):
        for index, method in enumerate(colors):
            mean, std = series(['TOTAL'], 'ALL', metric, method)
            ax.bar(index, mean[0], yerr=std[0] if n > 1 else None,
                   capsize=5, color=colors[method], label=method)
        ax.set_xticks([0, 1], ['Single', 'Multi'])
        ax.set_title(labels[metric] + ' — dataset total')
        ax.set_ylim(bottom=0)
        ax.grid(axis='y', alpha=.2)
    fig.suptitle(note)
    fig.tight_layout()
    save(fig, 'comparison_total')

    fig, axes = plt.subplots(2, 1, figsize=(11, 9))
    for ax, metric in zip(axes, METRICS):
        for offset, method in zip((-.18, .18), colors):
            values = [lookup[('TOTAL', method, kind, metric)] for kind in TYPES]
            ax.bar([i + offset for i in range(len(TYPES))], [r['mean'] for r in values],
                   width=.36, yerr=[r['std'] for r in values] if n > 1 else None,
                   capsize=4, color=colors[method], label=method.capitalize())
        ax.set_xticks(range(len(TYPES)), TYPES)
        ax.set_title(labels[metric] + ' — by FHIR resource type, dataset total')
        ax.grid(axis='y', alpha=.2)
        ax.legend()
    fig.suptitle(note)
    fig.tight_layout()
    save(fig, 'comparison_resource_types')

    cases = sorted({r['case'] for r in summary} - {'TOTAL'})
    for start in range(0, len(cases), page_size):
        page = cases[start:start + page_size]
        fig, axes = plt.subplots(1, 2, figsize=(16, max(5, len(page) * .45)), sharey=True)
        for ax, metric in zip(axes, METRICS):
            for offset, method in zip((-.18, .18), colors):
                means, stds = series(page, 'ALL', metric, method)
                ax.barh([i + offset for i in range(len(page))], means, height=.36,
                        xerr=stds if n > 1 else None, capsize=3,
                        color=colors[method], label=method.capitalize())
            ax.set_yticks(range(len(page)), [Path(case).stem for case in page])
            ax.set_title(labels[metric])
            ax.grid(axis='x', alpha=.2)
            ax.legend()
        axes[0].invert_yaxis()
        fig.suptitle(note + f' — cases {start + 1}–{start + len(page)}')
        fig.tight_layout()
        save(fig, f'comparison_conditions_{start // page_size + 1:02d}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single-dir', type=Path, required=True)
    parser.add_argument('--multi-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('results/comparison'))
    parser.add_argument('--expected-runs', type=int, default=5)
    parser.add_argument('--allow-incomplete', action='store_true')
    parser.add_argument('--page-size', type=int, default=25)
    args = parser.parse_args()
    if args.expected_runs < 1 or args.page_size < 1:
        parser.error('--expected-runs and --page-size must be positive.')
    if any(args.output_dir.resolve() == p.resolve() or args.output_dir.resolve() in p.resolve().parents
           for p in (args.single_dir, args.multi_dir)):
        parser.error('The report directory must not contain or replace an input directory.')
    try:
        raw, summary, metadata = aggregate(load_results(args.single_dir), load_results(args.multi_dir),
                                           args.expected_runs, args.allow_incomplete)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        # Remove only charts produced by this script, to avoid obsolete pages.
        for pattern in ('comparison_*.png', 'comparison_*.pdf'):
            for path in args.output_dir.glob(pattern):
                path.unlink()
        write_csv(args.output_dir / 'per_run.csv', raw)
        write_csv(args.output_dir / 'summary.csv', summary)
        (args.output_dir / 'report.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        render(summary, metadata, args.output_dir, args.page_size)
    except (ValueError, OSError, ImportError, KeyError) as exc:
        parser.error(str(exc))
    print(f'Report: {args.output_dir.resolve()}')
    print(f'{metadata["n_cases"]} cases; paired runs: {metadata["included_runs"]}')
    if metadata['excluded_runs']:
        print(f'Provisional report: excluded runs {metadata["excluded_runs"]}')


if __name__ == '__main__':
    main()
