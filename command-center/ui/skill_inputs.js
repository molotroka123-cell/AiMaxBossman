/* Typed Skills input contract. No network, execution, or permissive coercion. */
const owns = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const dangerous = new Set(['__proto__', 'prototype', 'constructor']);

export function skillFieldKind(spec = {}) {
  if (Array.isArray(spec.enum)) return 'enum';
  if (['array', 'object', 'number', 'integer', 'boolean'].includes(spec.type)) return spec.type;
  return 'string';
}

export function validateSkillValue(value, spec = {}, path = 'input') {
  const fail = reason => { throw new Error(`${path}: ${reason}`); };
  if (Array.isArray(spec.enum) && !spec.enum.some(v => JSON.stringify(v) === JSON.stringify(value))) fail('выберите значение из списка');
  if (owns(spec, 'const') && JSON.stringify(value) !== JSON.stringify(spec.const)) fail('неверное значение');
  const types = Array.isArray(spec.type) ? spec.type : spec.type ? [spec.type] : [];
  const matches = type => ({string: typeof value === 'string', number: typeof value === 'number' && Number.isFinite(value),
    integer: typeof value === 'number' && Number.isSafeInteger(value), boolean: typeof value === 'boolean',
    array: Array.isArray(value), object: value !== null && typeof value === 'object' && !Array.isArray(value), null: value === null})[type];
  if (types.length && !types.some(matches)) fail(`ожидается ${types.join(' / ')}`);
  if (typeof value === 'number') {
    if (owns(spec, 'minimum') && value < spec.minimum) fail(`минимум ${spec.minimum}`);
    if (owns(spec, 'maximum') && value > spec.maximum) fail(`максимум ${spec.maximum}`);
  }
  if (typeof value === 'string') {
    if (owns(spec, 'minLength') && value.length < spec.minLength) fail('слишком короткий текст');
    if (owns(spec, 'maxLength') && value.length > spec.maxLength) fail('слишком длинный текст');
    // Only the two simple locale patterns used by bundled skills are evaluated.
    // Arbitrary user-authored regex is not run on the UI event loop.
    if (spec.pattern === '^[a-z]{2}$' && !/^[a-z]{2}$/.test(value)) fail('нужны две строчные латинские буквы');
    if (spec.pattern === '^[A-Z]{2}$' && !/^[A-Z]{2}$/.test(value)) fail('нужны две заглавные латинские буквы');
  }
  if (Array.isArray(value)) {
    if (owns(spec, 'minItems') && value.length < spec.minItems) fail('слишком мало элементов');
    if (owns(spec, 'maxItems') && value.length > spec.maxItems) fail('слишком много элементов');
    if (spec.items) value.forEach((item, i) => validateSkillValue(item, spec.items, `${path}[${i}]`));
  } else if (value !== null && typeof value === 'object') {
    for (const key of Object.keys(value)) {
      if (dangerous.has(key)) fail('служебное имя поля запрещено');
      if (spec.additionalProperties === false && !owns(spec.properties || {}, key)) fail(`неизвестное поле ${key}`);
      if (owns(spec.properties || {}, key)) validateSkillValue(value[key], spec.properties[key], `${path}.${key}`);
    }
    for (const key of spec.required || []) if (!owns(value, key)) fail(`нужно поле ${key}`);
  }
  return value;
}

export function collectSkillInputs(schema = {}, raw = {}) {
  const data = Object.create(null);
  for (const [name, spec] of Object.entries(schema.properties || {})) {
    if (dangerous.has(name)) throw new Error('Служебное имя поля запрещено');
    const text = raw[name];
    // Blank optional fields are omitted, not submitted as empty strings.
    if (text === undefined || (typeof text === 'string' && text.trim() === '')) continue;
    const kind = skillFieldKind(spec);
    let value = text;
    if (['array', 'object', 'boolean', 'number', 'integer'].includes(kind)) {
      try { value = JSON.parse(text); } catch { throw new Error(`${name}: требуется корректный JSON (${kind})`); }
    } else if (kind === 'enum' && spec.type !== 'string') {
      try { value = JSON.parse(text); } catch { throw new Error(`${name}: неверное значение списка`); }
    }
    data[name] = validateSkillValue(value, spec, name);
  }
  validateSkillValue(data, schema);
  // Support the bundled conditional required-fields contract without inventing defaults.
  for (const clause of schema.allOf || []) {
    const condition = clause.if;
    if (!condition || !condition.properties) continue;
    const applies = Object.entries(condition.properties).every(([key, spec]) => owns(data, key) && (!owns(spec, 'const') || data[key] === spec.const));
    if (applies && clause.then) validateSkillValue(data, clause.then);
  }
  if (Array.isArray(schema.oneOf)) {
    const count = schema.oneOf.filter(clause => { try { validateSkillValue(data, clause); return true; } catch { return false; } }).length;
    if (count !== 1) throw new Error('Заполните ровно один из альтернативных вариантов входа');
  }
  return data;
}

export function createSkillField(name, spec, widgets) {
  const kind = skillFieldKind(spec);
  const attrs = {value: '', placeholder: spec.description || '', 'aria-label': name};
  if (kind === 'enum') return widgets.select([{value: '', label: '— не задано —'}, ...spec.enum.map(v => ({value: String(v), label: String(v)}))], attrs);
  if (kind === 'boolean') return widgets.select([{value: '', label: '— не задано —'}, {value: 'true', label: 'Да'}, {value: 'false', label: 'Нет'}], attrs);
  if (['array', 'object'].includes(kind) || name === 'markdown') return widgets.textarea({...attrs, rows: 8, class: 'textarea mono', placeholder: spec.description || (kind === 'array' ? '[ ... ]' : kind === 'object' ? '{ ... }' : '')});
  return widgets.input({...attrs, ...(kind === 'number' || kind === 'integer' ? {type: 'number', step: kind === 'integer' ? '1' : 'any', min: spec.minimum, max: spec.maximum} : {})});
}
