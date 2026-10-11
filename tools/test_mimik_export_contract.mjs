// Offline compatibility test of exact pinned upstream source. No extension,
// screenshot/AI/network execution. Node >=22.13 is needed for type stripping.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';

const root = new URL('../', import.meta.url);
const pin = JSON.parse(readFileSync(new URL('integrations/mimik/UPSTREAM.json', root), 'utf8'));
const source = (name) => {
  const entry = pin.reference_files.find(x => x.local_path.endsWith('/' + name));
  assert(entry);
  const bytes = readFileSync(new URL(entry.local_path, root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), entry.sha256);
  return stripTypeScriptTypes(bytes.toString('utf8').replace(/^import .*;\n/gm, ''))
    .replace(/^export /gm, '');
};
const sandbox = {
  i18n: { t(key, args = []) { return ({'export.stepLabel': 'Step ' + args[0],
    'export.stepsCount': args[0] + ' steps', 'export.createdLabel': 'Created ' + args[0],
    'blocks.variantSuccess': 'Success'})[key] ?? key; } },
  extractDomain: () => '', formatDate: () => '2026-09-20',
  renderScreenshot: () => { throw Error('screenshots must not be rendered'); },
};
vm.createContext(sandbox);
vm.runInContext(source('blocks.ts') + '\n' + source('markdown-export.ts') +
  '\nglobalThis.exporter = exportGuideAsMarkdown;', sandbox, {timeout: 1000});
const steps = [
  {id: 'heading', blockType: 'heading', description: 'Preparation'},
  {id: 'a', description: 'Click Models'},
  {id: 'note', blockType: 'callout', calloutVariant: 'success', description: 'Claimed success is not evidence.'},
  {id: 'b', description: 'Click Save'},
];
const actual = await sandbox.exporter({title: 'Mimik contract fixture', createdAt: 0}, steps, new Map());
const expected = readFileSync(new URL('integrations/mimik/reference/export.fixture.md', root), 'utf8');
assert.equal(actual, expected);
assert(actual.includes('## Step 01: Click Models'));
assert(actual.includes('## Step 02: Click Save'));
assert(!actual.includes('Step 03'));
console.log('MIMIK_UPSTREAM_EXPORT_CONTRACT=PASS (fixture data, not live capture)');
