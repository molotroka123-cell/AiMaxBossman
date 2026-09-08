"""Offline pack integrity/format regressions, not runtime or model acceptance."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'tools' / 'validate_curated_agent_skills.py'
LOCK = Path('docs/skills/voltagent-agent-skills.lock.json')


class CuratedAgentSkillsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('curated_skill_check', MODULE)
        cls.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.checker)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'pack'
        self.root.mkdir()
        self.manifest = json.loads((ROOT / LOCK).read_text(encoding='utf-8'))
        # Copy only this pack, never the repository, worktrees or owner state.
        paths = {LOCK}
        for entry in self.manifest['skills']:
            paths.update((Path(entry['path']), Path(entry['license_file'])))
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def write_manifest(self):
        (self.root / LOCK).write_text(json.dumps(self.manifest), encoding='utf-8')

    def rejects(self):
        self.write_manifest()
        with self.assertRaises(self.checker.PackError):
            self.checker.validate_pack(self.root)

    def change_skill(self, transform):
        entry = self.manifest['skills'][0]
        path = self.root / entry['path']
        path.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8')
        data = path.read_bytes()
        entry.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))

    def test_valid_pack(self):
        report = self.checker.validate_pack(self.root)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['skill_count'], 8)
        self.assertEqual(report['scope'], 'PACK_INTEGRITY_ONLY')
        self.assertEqual(report['runtime_acceptance'], 'NOT_RUN')

    def test_tampered_skill_is_rejected(self):
        with (self.root / self.manifest['skills'][0]['path']).open('ab') as stream:
            stream.write(b'\nchanged\n')
        self.rejects()

    def test_wrong_byte_size_is_rejected(self):
        self.manifest['skills'][0]['bytes'] += 1
        self.rejects()

    def test_duplicate_skill_is_rejected(self):
        self.manifest['skills'].append(self.manifest['skills'][0])
        self.rejects()

    def test_empty_pack_is_rejected(self):
        self.manifest['skills'] = []
        self.rejects()

    def test_traversal_is_rejected(self):
        self.manifest['skills'][0]['path'] = '../outside.md'
        self.rejects()

    def test_symlink_is_rejected(self):
        entry = self.manifest['skills'][0]
        target = self.root / entry['path']
        outside = Path(self.tmp.name) / 'outside.md'
        target.rename(outside)
        try:
            target.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege unavailable')
            raise
        self.rejects()

    def test_missing_source_pin_is_rejected(self):
        self.manifest['skills'][0]['source']['commit'] = 'main'
        self.rejects()

    def test_mismatched_source_url_is_rejected(self):
        self.manifest['skills'][0]['source']['url'] = 'https://example.invalid/skill'
        self.rejects()

    def test_wrong_upstream_license_is_rejected(self):
        self.manifest['skills'][0]['source']['license'] = 'Apache-2.0'
        self.rejects()

    def test_missing_license_is_rejected(self):
        (self.root / self.manifest['skills'][0]['license_file']).unlink()
        self.rejects()

    def test_activation_grant_is_rejected(self):
        self.manifest['activation']['grants'] = ['terminal.run']
        self.rejects()

    def test_auto_execution_is_rejected(self):
        self.manifest['activation']['auto_execute'] = True
        self.rejects()

    def test_system_prompt_dump_is_rejected(self):
        self.manifest['activation']['inject_all_into_system_prompt'] = True
        self.rejects()

    def test_permission_frontmatter_rejected_even_with_new_hash(self):
        self.change_skill(lambda text: text.replace('---\nname:', '---\nallowed-tools: Bash\nname:', 1))
        self.rejects()

    def test_mismatched_name_rejected_even_with_new_hash(self):
        self.change_skill(lambda text: text.replace('name: systematic-debugging', 'name: wrong-skill', 1))
        self.rejects()

    def test_oversized_skill_rejected_even_with_new_hash(self):
        self.change_skill(lambda text: text + 'x' * 7000)
        self.rejects()

    def test_missing_boundary_rejected_even_with_new_hash(self):
        self.change_skill(lambda text: text.replace('## Authority and execution boundary', '## Other', 1))
        self.rejects()

    def test_duplicate_json_key_is_rejected(self):
        content = (self.root / LOCK).read_text(encoding='utf-8')
        (self.root / LOCK).write_text(content.replace('{', '{"schema_version": 999,', 1), encoding='utf-8')
        with self.assertRaises(self.checker.PackError):
            self.checker.validate_pack(self.root)


if __name__ == '__main__':
    unittest.main()
