"""End-to-end smoke scenarios for URL and image input (run before a demo).

Each scenario runs the real pipeline and checks the risk level falls in an expected set and that the
report contains the sections the web UI reads. Network-dependent scenarios can change over time
(sites move, block bots or add login walls), so treat a failure as "look at this case", not a unit test.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SCENARIOS = [
    ('url_dcard_ig', ['--url', 'https://www.instagram.com/dcard.tw/'], {'Low', 'Unknown', 'Medium'}),
    ('url_165_listed', ['--url', 'https://bbhhshf.cc/'], {'High'}),
    ('url_lookalike', ['--url', 'https://instagram-login-verify.net/'], {'High'}),
    ('url_gov', ['--url', 'https://www.gov.tw/'], {'Low', 'Unknown', 'Medium'}),
    ('url_unresolvable', ['--url', 'https://this-domain-does-not-exist-20261115.com/'], {'Unknown'}),
    ('img_myship_fake', ['--image', 'data/demo/myship_fake.png'], {'High'}),
    ('img_myship_real', ['--image', 'data/demo/myship_real.png'], {'Low', 'Unknown', 'Medium'}),
    ('img_listed_165', ['--image', 'data/demo/listed_165.png'], {'High'}),
    ('img_line_chat', ['--image', 'data/demo/case5_line_chat.jpg'], {'Low', 'Unknown'}),
    ('img_blank', ['--image', 'data/demo/blank.png'], {'Unknown'}),
]
UI_KEYS = ['assessment', 'report_summary', 'evidence', 'content_analysis', 'link_trace', 'image_forensics', 'account_signals']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--no-llm', action='store_true', help='pass --no-llm to the pipeline')
    args = parser.parse_args()
    failures = 0
    for name, source, expected in SCENARIOS:
        if args.only and name not in args.only:
            continue
        out = ROOT / 'artifacts' / 'scenarios' / name
        out.mkdir(parents=True, exist_ok=True)
        (out / 'report.json').unlink(missing_ok=True)
        started = time.monotonic()
        # Log files instead of pipes: a lingering browser process holding a pipe open must not stall the runner.
        with (out / 'run.log').open('w', encoding='utf-8') as log:
            try:
                subprocess.run([sys.executable, str(ROOT / 'run_pipeline.py'), *source, '--output-dir', str(out),
                                *(['--no-llm'] if args.no_llm else [])], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=log, timeout=600)
            except subprocess.TimeoutExpired:
                print(f'FAIL {name}: exceeded 600s (the web job limit)', flush=True)
                failures += 1
                continue
        seconds = time.monotonic() - started
        try:
            report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            print(f'FAIL {name}: no report ({exc}); see {out / "run.log"}', flush=True)
            failures += 1
            continue
        level = (report.get('assessment') or {}).get('risk_level')
        missing = [k for k in UI_KEYS if k not in report and report.get('status') not in ('blocked', 'error', 'unusable')]
        ok = level in expected and not missing
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: status={report.get('status')} risk={level} basis={(report.get('assessment') or {}).get('basis')}"
              f" links={(report.get('link_trace') or {}).get('followed')} time={seconds:.0f}s missing={missing or '-'} error={report.get('error') or '-'}", flush=True)
    print('failures:', failures)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
