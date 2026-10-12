"""Fit existing GlyphBook from local OCR pseudo-labels; NEVER activate a profile.

Cross-recording OCR agreement is a diagnostic, not independent accuracy or GTO learning.
"""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import re
import sys

import cv2

# Direct tool invocation must resolve the sibling app package, not cwd/tools.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokervision.vision.glyphs import GlyphBook, is_sep, line_glyphs


def samples(dataset: Path):
    for line in (dataset / 'numeric-ocr-drafts.jsonl').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        frame = (dataset / row['frame']).resolve(strict=True)
        if not frame.is_relative_to(dataset) or hashlib.sha256(frame.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Frame identity mismatch')
        im = cv2.imread(str(frame))
        if im is None:
            raise ValueError('Cannot decode frame')
        h, w = im.shape[:2]
        for token in row['numeric_tokens']:
            if token['confidence'] < 0.98 or not re.fullmatch(r'\d[\d,. ]*', token['text']):
                continue
            points = token['polygon_normalized']
            x0, x1 = max(0, int(min(p[0] for p in points)*w)-1), min(w, int(max(p[0] for p in points)*w)+2)
            y0, y1 = max(0, int(min(p[1] for p in points)*h)-1), min(h, int(max(p[1] for p in points)*h)+2)
            if x1 <= x0 or y1 <= y0:
                continue
            crop = im[y0:y1, x0:x1]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            glyphs, info = line_glyphs(gray)
            median = info.get('median_h', 0)
            glyphs = [g for g in glyphs if not is_sep(g, median)]
            text = ''.join(c for c in token['text'] if c.isdigit())
            if len(text) != len(glyphs) or not text or len(text) > 10 or info.get('fg_frac', 1) > 0.42:
                continue
            yield text, glyphs, hashlib.sha256(crop.tobytes()).hexdigest()


def select_datasets(root: Path, train_sha: str, eval_sha: str):
    if train_sha == eval_sha:
        raise ValueError('Training and diagnostic recordings must differ')
    datasets = {json.loads(p.read_text(encoding='utf-8'))['source_sha256']: p.parent
                for p in root.glob('*/manifest.json')}
    if train_sha not in datasets or eval_sha not in datasets:
        raise ValueError('Requested recording SHA is absent from manifests')
    return datasets[train_sha], datasets[eval_sha]


def run(root: Path, *, train_sha: str, eval_sha: str, output_root: Path | None = None):
    root = root.resolve(strict=True)
    if any((p / '.git').exists() for p in (root, *root.parents)):
        raise ValueError('Private data must remain outside Git')
    report_path = root / 'LOCAL_OCR_REPORT.json'
    if not report_path.exists():
        raise ValueError('OCR run is not complete')
    training, evaluation = select_datasets(root, train_sha, eval_sha)
    destination = root if output_root is None else output_root.resolve()
    if any((p / '.git').exists() for p in (destination, *destination.parents)):
        raise ValueError('Private data must remain outside Git')
    output = destination / 'numeric-glyph-candidate.json'
    report_output = destination / 'NUMERIC_CANDIDATE_REPORT.json'
    if output.exists() or report_output.exists():
        raise FileExistsError('Candidate or report already exists; use a new private run directory')
    book, seen, train_count = GlyphBook(), set(), 0
    for text, glyphs, sha in samples(training):
        if sha in seen:
            continue
        seen.add(sha)
        for ch, glyph in zip(text, glyphs):
            book.add(ch, glyph, 'owner_video_pseudolabel')
        train_count += 1
    allowed = frozenset('0123456789')
    total = agreed = unknown = wrong = duplicate = 0
    for text, glyphs, sha in samples(evaluation):
        if sha in seen:
            duplicate += 1
            continue
        total += 1
        result = [book.classify(g, allowed) for g in glyphs]
        if any(ch is None or confidence < 0.10 or distance > 4.8 for ch, confidence, distance in result):
            unknown += 1
        elif ''.join(ch for ch, _, _ in result) == text:
            agreed += 1
        else:
            wrong += 1
    candidate = {'kind': 'numeric_glyph_exemplar_candidate', 'verified': False,
                 'label_source': 'RapidOCR pseudo-labels, confidence >= 0.98',
                 'glyph_book': book.to_json(), 'source_sha256': [train_sha, eval_sha],
                 'activation_allowed': False}
    destination.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as f:
        json.dump(candidate, f)
    status = 'UNVERIFIED_CANDIDATE_FITTED' if train_count and book.exemplars else 'NO_USABLE_TRAINING_SAMPLES'
    report = {'status': status, 'training_tokens': train_count,
              'classes': sorted(book.exemplars), 'exemplars': sum(map(len, book.exemplars.values())),
              'evaluation_tokens': total, 'ocr_teacher_agreed': agreed,
              'ocr_teacher_disagreed': wrong, 'unknown': unknown, 'identical_crop_excluded': duplicate,
              'ocr_teacher_agreement': agreed/total if total else None,
              'candidate_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'independent_accuracy': None, 'gto_training': 'NOT_RUN',
              'profile_activation': 'NOT_RUN', 'cloud_calls': 0,
              'limitations': ['Pseudo-label teacher may be wrong',
                              'Recording session independence is not established',
                              'Digit exemplars only: no cards, field roles, or strategy',
                              'Not neural fine-tuning; existing CPU k-NN calibration']}
    with report_output.open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--train-sha', required=True)
    parser.add_argument('--output', type=Path, help='Fresh private output folder; inputs stay unchanged')
    parser.add_argument('--eval-sha', required=True,
                        help='OCR diagnostic recording, NOT an independently labelled holdout')
    args = parser.parse_args()
    print(json.dumps(run(args.root, train_sha=args.train_sha, eval_sha=args.eval_sha,
                         output_root=args.output), indent=2))
