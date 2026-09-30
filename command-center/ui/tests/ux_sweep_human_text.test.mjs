// UX-05/06/08: сырой технический текст не доходит до владельца.
// Запуск: node --test --experimental-detect-module command-center/ui/tests/ux_sweep_human_text.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = { getElementById: () => null };
const { humanHint } = await import('../api.js');
const { policyNote } = await import('../pages/apps.js');
const { opencodeReason } = await import('../pages/mobile.js');
const { BINARY_LABEL, withCode } = await import('../pages/file_intelligence.js');

const RAW_API = /\b(GET|POST|PUT|PATCH|DELETE)\s+\/api\/|\{\s*"|ConnectError|Traceback|undefined|\[object/;

test('apps policy: the API-call hint becomes a human sentence for the known code', () => {
  const backend = 'управление приложениями выключено политикой владельца';
  const hint = 'управление приложениями по умолчанию выключено; включить — PUT /api/apps/control/policy {"enabled": true} (действует сразу, перезапуск не нужен)';
  const out = humanHint(hint, 'APPS_CONTROL_DISABLED');
  assert.doesNotMatch(out, RAW_API);
  assert.match(out, /Разрешить запуск приложений/);
  assert.ok(backend);
});

test('humanHint drops only the clause that names an API request and keeps plain Russian', () => {
  const out = humanHint('управление выключено владельцем; включить — PUT /api/apps/control/policy {"enabled": true}', '');
  assert.equal(out, 'управление выключено владельцем');
  assert.doesNotMatch(out, RAW_API);
});

test('humanHint leaves ordinary hints alone (negative control)', () => {
  assert.equal(humanHint('Откройте «Агенты»: выберите включённого агента', ''), 'Откройте «Агенты»: выберите включённого агента');
  assert.equal(humanHint('', ''), '');
  assert.equal(humanHint(undefined, ''), undefined);
  assert.equal(humanHint('Проверьте /api в адресной строке', ''), 'Проверьте /api в адресной строке');
});

test('policyNote never prints an API call or JSON and covers every policy source', () => {
  const policies = [
    { enabled: false, source: 'default', can_change: true,
      hint: 'управление приложениями по умолчанию выключено; включить — PUT /api/apps/control/policy {"enabled": true}' },
    { enabled: false, source: 'owner_setting', can_change: true,
      hint: 'управление выключено владельцем; включить — PUT /api/apps/control/policy {"enabled": true}' },
    { enabled: true, source: 'owner_setting', can_change: true, hint: 'управление включено владельцем' },
    { enabled: false, source: 'owner_setting_unreadable', can_change: true,
      hint: 'saved owner policy cannot be read; app effects are denied. Set the policy explicitly again or repair local storage' },
    { enabled: true, source: 'environment', can_change: true, hint: 'значение по умолчанию взято из BOSSMAN_APPS_CONTROL_ENABLED' },
    { enabled: false, source: 'deployment_lock', locked: true, can_change: false, hint: 'политика закреплена переменной X' },
    null,
  ];
  for (const policy of policies) {
    const text = policyNote(policy);
    assert.ok(text && text.length > 20, JSON.stringify(policy));
    assert.doesNotMatch(text, RAW_API);
    assert.doesNotMatch(text, /cannot be read|apps_control/i);
  }
});

test('OpenCode: the raw probe trail (ConnectError) goes to the tooltip, the human reason is shown', () => {
  const health = {
    status: 'unavailable',
    detail: '/api/info: ConnectError; /api/session: ConnectError; /session: ConnectError',
    hint: 'запустите `opencode serve` на этой машине (см. docs/v2-pack/MCP_SKILLS_OPENCODE.md)',
  };
  const { text, detail } = opencodeReason(health, '');
  assert.doesNotMatch(text, /ConnectError|\/api\/info/);
  assert.match(text, /OpenCode не запущен/);
  assert.match(text, /opencode serve/);
  assert.match(detail, /ConnectError/);
  assert.match(opencodeReason({ status: 'unauthorized' }, '').text, /пароль/);
  assert.match(opencodeReason({ status: 'incompatible_version' }, '').text, /не поддерживается/);
  assert.match(opencodeReason({}, '').text, /не отвечает/);
  assert.deepEqual(opencodeReason({}, 'Нет связи'), { text: 'Нет связи', detail: '' });
});

test('File Intelligence: binary codes get a Russian label with the code kept in brackets', () => {
  for (const code of ['AVAILABLE', 'NOT_INSTALLED', 'WRONG_VERSION', 'PROTOCOL_FAILED', 'BUSY']) {
    assert.ok(BINARY_LABEL[code], code);
    assert.doesNotMatch(BINARY_LABEL[code], /[A-Z_]{4,}/);
  }
  assert.equal(withCode('Не установлен', 'NOT_INSTALLED'), 'Не установлен (NOT_INSTALLED)');
  assert.equal(withCode(undefined, '1.2.3'), '1.2.3');
  assert.equal(withCode('недоступна', 'UNAVAILABLE'), 'недоступна (UNAVAILABLE)');
});
