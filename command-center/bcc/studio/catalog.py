"""Validated, unverified declarations; never a substitute for capability probes."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[3] / 'tools' / 'studio_models.json'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _value(field, schema, value):
    if value is None and schema.get('nullable') is True:
        return
    kind = schema.get('type')
    valid = False
    if kind == 'enum':
        valid = any(type(value) is type(x) and value == x for x in schema['values'])
    elif kind == 'boolean':
        valid = type(value) is bool
    elif kind == 'range':
        valid = (_number(value) and schema['min'] <= value <= schema['max']
                 and (not schema.get('integer') or type(value) is int))
        if valid and 'multiple_of' in schema:
            valid = value % schema['multiple_of'] == 0
    if not valid:
        raise ValueError(f'studio setting {field}: unsupported value')


def validate_settings(model, values):
    if not isinstance(values, dict):
        raise ValueError('settings must be an object')
    schemas = model['settings']
    for field in values:
        if field not in schemas:
            raise ValueError(f'studio setting {field}: unknown field')
    resolved = {name: values.get(name, schema['default']) for name, schema in schemas.items()}
    for name, value in resolved.items():
        _value(name, schemas[name], value)
    return resolved


def load(path: Path = REGISTRY):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('catalog schema_version: unsupported')
    models = data.get('models')
    if not isinstance(models, list) or not models:
        raise ValueError('catalog models: non-empty list required')
    seen = set()
    for model in models:
        if not isinstance(model, dict):
            raise ValueError('catalog model: object required')
        ident = model.get('id')
        if not isinstance(ident, str) or ':' not in ident or ident in seen:
            raise ValueError('catalog id: missing or duplicate')
        seen.add(ident)
        if model.get('provider') != ident.split(':',1)[0]:
            raise ValueError(f'{ident}: provider mismatch')
        if model.get('surface') not in ('image','video','audio'):
            raise ValueError(f'{ident}: surface invalid')
        if model.get('enabled') is not False:
            raise ValueError(f'{ident}: enabled must be false in declarations')
        answers = model.get('answers')
        if not isinstance(answers,dict) or set(answers) != {'BUNDLED','CONFIGURED','MODEL_REQUIRED','EXTERNAL_SERVICE_REQUIRED','VERIFIED'}:
            raise ValueError(f'{ident}: answers missing')
        if answers['VERIFIED'] is not False or model.get('verified_capabilities') != {}:
            raise ValueError(f'{ident}: VERIFIED requires runtime probe, never a declaration')
        if type(model.get('deadline_seconds')) is not int or model['deadline_seconds'] <= 0:
            raise ValueError(f'{ident}: deadline_seconds invalid')
        for field in ('label','license','source','status'):
            if not isinstance(model.get(field),str) or not model[field].strip():
                raise ValueError(f'{ident}: {field} required')
        if type(model.get('free')) is not bool:
            raise ValueError(f'{ident}: free must be boolean')
        price = model.get('price')
        if not isinstance(price,dict) or 'usd' not in price or not price.get('kind') or not price.get('source'):
            raise ValueError(f'{ident}: price.usd/source/kind required')
        if price['usd'] is not None and (not _number(price['usd']) or price['usd'] < 0):
            raise ValueError(f'{ident}: price.usd must be finite and non-negative')
        roles = model.get('roles')
        if not isinstance(roles,dict) or any(not isinstance(k,str) or type(v) is not int or v < 1 for k,v in roles.items()):
            raise ValueError(f'{ident}: roles invalid')
        settings = model.get('settings')
        if not isinstance(settings,dict):
            raise ValueError(f'{ident}: settings required')
        for name, schema in settings.items():
            if not isinstance(schema,dict) or 'default' not in schema:
                raise ValueError(f'{name}: schema/default required')
            kind = schema.get('type')
            if kind not in ('enum','range','boolean'):
                raise ValueError(f'{name}: unsupported type')
            if kind == 'enum' and (not isinstance(schema.get('values'),list) or not schema['values']):
                raise ValueError(f'{name}: values required')
            if kind == 'range':
                if not all(_number(schema.get(k)) for k in ('min','max')) or schema['min'] > schema['max']:
                    raise ValueError(f'{name}: invalid range')
                if 'multiple_of' in schema and (not _number(schema['multiple_of']) or schema['multiple_of'] <= 0):
                    raise ValueError(f'{name}: invalid multiple_of')
            _value(name,schema,schema['default'])
        validate_settings(model,{})
    return data


def auto_eligible(model):
    # Catalog declarations alone cannot authorize routing, even when price is zero.
    return False


def summary(data):
    n = len(data['models'])
    return f'BOSSMAN_STUDIO_CATALOG={n} verified=0 unverified={n}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',type=Path,default=REGISTRY)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true')
    group.add_argument('--json',action='store_true')
    args = parser.parse_args()
    try:
        data = load(args.registry)
    except (ValueError,OSError) as exc:
        parser.exit(1,f'{exc}\n')
    print(json.dumps(data,ensure_ascii=False,indent=2) if args.json else summary(data))

if __name__ == '__main__':
    main()
