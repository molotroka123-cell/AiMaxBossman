"""Validate visual documentation only; never certify runtime, models or release.

Run from the repository or this documentation-only export. --full-repo also
requires every local Markdown target and every advertised script to exist.
No downloads, writes, subprocesses, credentials or third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
VISUALS = Path(__file__).resolve().parent
START = '<!-- BOSSMAN_LIVE_SCORECARD_START -->'
END = '<!-- BOSSMAN_LIVE_SCORECARD_END -->'


def fenced(text: str) -> list[str]:
    blocks, lines = [], None
    for line in text.splitlines():
        if line.startswith('```'):
            if lines is None:
                lines = []
            else:
                blocks.append('\n'.join(lines))
                lines = None
        elif lines is not None:
            lines.append(line)
    if lines is not None:
        raise ValueError('unclosed code fence')
    return blocks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--full-repo', action='store_true')
    args = ap.parse_args(argv)
    report = []

    def check(name, passed, **details):
        report.append({'check': name, 'passed': bool(passed), **details})

    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    check('readme_below_github_500kib', len(readme.encode()) < 500 * 1024)
    check('canonical_scorecard_markers', readme.count(START) == 1 and readme.count(END) == 1
          and readme.index(START) < readme.index(END))
    anchors = re.findall(r'<a id="([^"]+)"', readme)
    internal = re.findall(r'\]\(#([^)]+)\)', readme)
    check('explicit_section_anchors', len(anchors) == len(set(anchors))
          and all(a in anchors for a in internal), anchors=len(anchors))
    paths = set()
    for block in fenced(readme):
        paths.update(re.findall(r'^python\s+(?:-m\s+pytest\s+)?([\w./-]+\.py)', block, re.M))
    check('advertised_python_paths_not_silently_lost', len(paths) >= 6, count=len(paths))
    check('vision_and_live_boundaries', 'FUTURE' in readme and 'THEORETICAL' in readme
          and 'offline' in readme and 'Полный V8 Total ещё не принят' in readme)
    html_images = re.findall(r'<img\s+([^>]+)>', readme)
    check('all_images_have_alt_text', bool(html_images)
          and all(re.search(r'alt="[^"\s][^"]*"', x) for x in html_images))
    image_paths = re.findall(r'<img[^>]+src="([^"]+)"', readme)
    check('images_local_and_present', len(image_paths) == 8 and all(
        not urlsplit(p).scheme and (ROOT / p).is_file() for p in image_paths))
    check('visuals_small', sum((ROOT / p).stat().st_size for p in image_paths) < 100_000)

    provenance = json.loads((VISUALS / 'art-provenance.json').read_text(encoding='utf-8'))
    for name, data in provenance.items():
        raw = (VISUALS / name).read_bytes()
        git_id = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        check('concept:' + name, raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'
              and len(raw) == data['bytes'] and hashlib.sha256(raw).hexdigest() == data['sha256']
              and git_id == data['git_blob_sha']
              and data['classification'] == 'AI_CONCEPT_NOT_SCREENSHOT')
    for file in sorted(VISUALS.glob('*.svg')):
        svg = ET.fromstring(file.read_bytes())
        nodes = list(svg.iter())
        safe = all(n.tag.rsplit('}', 1)[-1] not in {'script', 'foreignObject', 'iframe'} for n in nodes)
        safe = safe and all(not k.lower().startswith('on') and not (
            k.rsplit('}', 1)[-1] in {'href', 'src'} and v and not v.startswith('#'))
            for n in nodes for k, v in n.attrib.items())
        check('safe_accessible_svg:' + file.name, safe and 'viewBox' in svg.attrib
              and any(n.tag.endswith('title') for n in nodes)
              and any(n.tag.endswith('desc') for n in nodes))

    missing = set()
    for file in [ROOT / 'README.md', VISUALS / 'README.md',
                 ROOT / 'docs/v8/audit-20260910-20260918/AUDIT_RU.md']:
        for target in re.findall(r'\]\(([^\s)]+)\)', file.read_text(encoding='utf-8')):
            if target.startswith('#') or urlsplit(target).scheme:
                continue
            resolved = (file.parent / unquote(target.split('#')[0])).resolve()
            if not resolved.is_relative_to(ROOT):
                missing.add('OUTSIDE_REPOSITORY:' + target)
            elif not resolved.exists():
                missing.add(str(resolved.relative_to(ROOT)))
    if args.full_repo:
        missing.update(p for p in paths if not (ROOT / p).is_file())
        check('full_repository_local_targets', not missing, missing=sorted(missing))
    else:
        check('documentation_export_targets', all(not x.startswith('OUTSIDE_REPOSITORY:') for x in missing),
              repository_targets_not_checked=sorted(missing))
    result = {'scope': 'DOCUMENTATION_ONLY_NOT_PRODUCT_ACCEPTANCE',
              'full_repo': args.full_repo, 'checks': report,
              'passed': all(x['passed'] for x in report)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        print('DOCUMENTATION_VALIDATION_FAILED: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
