"""Validate preparation files only; this NEVER accepts Bossman or freezes a release."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
GENERATED = {'RUN_CHECKPOINT.json', 'SCENARIOS.json', 'BUGS.md',
             'PERFORMANCE.json', 'OWNER_SUMMARY_RU.md'}


def check():
    text = (ROOT / 'OPENCODE_OWNER_RUN_RU.md').read_text(encoding='utf-8')
    ids = re.findall(r'^### (G\d{2}) — ', text, re.M)
    state = json.loads((ROOT / 'RUN_CHECKPOINT.template.json').read_text(encoding='utf-8'))
    results = []
    def record(name, passed, **details):
        results.append({'check': name, 'passed': bool(passed), **details})
    record('20_unique_GUI_scenarios', ids == [f'G{i:02}' for i in range(20)]
           and ids == [x['id'] for x in state['scenarios']])
    record('template_all_NOT_RUN', all(x['status'] == 'NOT_RUN' and not x['evidence']
           and not x['attempts'] for x in state['scenarios']))
    record('no_fake_candidate_or_freeze', all(v is None for v in state['candidate'].values())
           and state['release']['freeze_requested'] is False
           and state['release']['actual_freeze_verdict'] is None)
    record('unsafe_permissions_default_false', all(v is False for k, v in state['boundaries'].items()
           if k.endswith('_allowed')))
    files = [f for f in sorted(ROOT.iterdir()) if f.is_file() and f.name != 'PREPARATION_FILE_CHECKS.json']
    secret = re.compile(r'sk-or-v1-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}')
    record('utf8_and_no_secret_markers', all('\x00' not in f.read_text(encoding='utf-8')
           and not secret.search(f.read_text(encoding='utf-8')) for f in files))
    refs = set(re.findall(r'`([A-Z_]+(?:\.template)?\.(?:md|json))`',
                         '\n'.join(f.read_text(encoding='utf-8') for f in ROOT.glob('*.md'))))
    missing = sorted(x for x in refs if x not in GENERATED and not (ROOT / x).is_file())
    record('local_document_references_exist', not missing, missing=missing)
    report = {'scope': 'PREPARATION_DOCUMENTS_ONLY_NOT_WINDOWS_OR_MODEL_ACCEPTANCE',
              'checks': results,
              'file_sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
    (ROOT / 'PREPARATION_FILE_CHECKS.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(check())
