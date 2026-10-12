"""Local CPU numeric OCR drafts; no cloud, ground-truth labels or promotion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

import cv2


def run(root: Path, *, ocr=None) -> dict:
    root = root.resolve(strict=True)
    if any((p / '.git').exists() for p in (root, *root.parents)):
        raise ValueError('Private data must remain outside Git')
    manifests = sorted(root.glob('*/manifest.json'))
    if not manifests:
        raise ValueError('No prepared recording manifests found')
    if (root / 'LOCAL_OCR_REPORT.json').exists() or any(
            (p.parent / 'numeric-ocr-drafts.jsonl').exists() for p in manifests):
        raise FileExistsError('OCR outputs already exist; use a fresh private run directory')
    if ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    counts = {}
    for manifest_path in manifests:
        dataset = manifest_path.parent
        output = dataset / 'numeric-ocr-drafts.jsonl'
        if output.exists():
            raise FileExistsError(output)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        annotations = [json.loads(s) for s in (dataset / 'annotations.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
        if len(annotations) != manifest['frame_count'] or not annotations:
            raise ValueError('Annotation count does not match nonempty manifest')
        count = 0
        pending = output.with_suffix('.pending')
        with pending.open('x', encoding='utf-8') as dest:
            for row in annotations:
                frame = (dataset / row['frame']).resolve(strict=True)
                if not frame.is_relative_to(dataset):
                    raise ValueError('Frame outside dataset')
                if hashlib.sha256(frame.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('Frame hash changed')
                im = cv2.imread(str(frame))
                if im is None:
                    raise ValueError('Cannot decode frame')
                h, w = im.shape[:2]
                # Full original-resolution input. Retain only numeric tokens,
                # never infer which number is a pot/stack or label card suits.
                result, _ = ocr(im)
                tokens = []
                for box, text, score in result or []:
                    if re.fullmatch(r'[$€£]?\s*\d[\d,. ]*[kKmM]?', text.strip()):
                        tokens.append({'text': text, 'confidence': float(score),
                                       'polygon_normalized': [[float(x)/w, float(y)/h] for x, y in box]})
                dest.write(json.dumps({'frame': row['frame'], 'sha256': row['sha256'],
                                       't_ms': row['t_ms'], 'numeric_tokens': tokens,
                                       'reviewed': False, 'usable_for_training': False,
                                       'field_assignment': None}) + '\n')
                dest.flush()
                count += 1
        pending.rename(output)
        counts[dataset.name] = count
        print(json.dumps({'dataset': dataset.name, 'ocr_frames': count}), flush=True)
    report = {'engine': 'RapidOCR CPU', 'datasets': counts,
              'status': 'UNVERIFIED_NUMERIC_DRAFTS', 'training': 'NOT_RUN',
              'profile_activation': 'NOT_RUN', 'cloud_calls': 0,
              'limitations': ['Numbers have no semantic field assignment',
                              'No card-suit/rank labels or GTO targets',
                              'Independent labelled holdout session still required']}
    (root / 'LOCAL_OCR_REPORT.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(run(Path(sys.argv[1])), indent=2))
