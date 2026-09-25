"""Phase-0 contract: accepted values paired with named refusals."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('studio_catalog', ROOT/'command-center/bcc/studio/catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def test_catalog_is_unverified_and_disabled():
    models = catalog.load()['models']
    # The exact shipped set, so a model added to the registry is an explicit decision here
    # (FLUX.1-schnell and SDXL: 5d3fd551; FLUX.2-klein-4B: d15e6531;
    # Seedance 2.5 for the owner-approved Bossfield 30 s run: 981f61ef;
    # Qwen-Image-2.1 is the local-only research/evaluation image path.
    assert [m['id'] for m in models] == [
        'comfyui:*', 'openrouter:google/gemini-2.5-flash-image', 'openrouter:google/gemini-3.1-flash-image',
        'openrouter:minimax/hailuo-3-max', 'openrouter:bytedance/seedance-2.0-mini',
        'openrouter:bytedance/seedance-2.5', 'higgsfield:owner-required',
        'sdcpp:wan2.2-ti2v-5b', 'sdcpp:qwen-image-2.1', 'sdcpp:z-image-turbo', 'sdcpp:flux1-schnell', 'sdcpp:flux2-klein-4b',
        'sdcpp:sdxl-base']
    assert len(models) == 13
    assert all(not m['answers']['VERIFIED'] and not m['enabled'] for m in models)
    # A local free engine legitimately carries usd==0 (see load(): free is a
    # required boolean and auto_eligible stays False even at zero). Keep the
    # teeth: no model may carry a committed non-zero owner charge, and a
    # zero price is only honest when the model is actually declared free.
    assert all(m['price']['usd'] in (None, 0) for m in models)
    assert all(m['price']['usd'] is None or m['free'] for m in models)
    assert catalog.summary(catalog.load()) == 'BOSSMAN_STUDIO_CATALOG=13 verified=0 unverified=13'
    qwen = next(m for m in models if m['id'] == 'sdcpp:qwen-image-2.1')
    assert qwen['provider'] == 'sdcpp' and qwen['free'] is True
    assert qwen['answers']['VERIFIED'] is False and qwen['enabled'] is False
    assert not catalog.auto_eligible(qwen)

@pytest.mark.parametrize('field,value', [('width',512),('height',1024),('steps',30),('seed',0)])
def test_local_settings_accept(field,value):
    model = catalog.load()['models'][0]
    assert catalog.validate_settings(model,{field:value})[field] == value

@pytest.mark.parametrize('field,value', [('width',257),('height',True),('steps',201),('seed',-1),('foreign',1),('width',float('nan'))])
def test_local_settings_refuse_named_field(field,value):
    with pytest.raises(ValueError,match=field):
        catalog.validate_settings(catalog.load()['models'][0],{field:value})

@pytest.mark.parametrize('mutation,field', [('duplicate','id'),('verified','VERIFIED'),('price','usd'),('default','width'),('enabled','enabled')])
def test_catalog_corruption_refused(tmp_path,mutation,field):
    data = copy.deepcopy(catalog.load())
    m = data['models'][0]
    if mutation == 'duplicate': data['models'].append(copy.deepcopy(m))
    if mutation == 'verified': m['answers']['VERIFIED'] = True
    if mutation == 'price': m['price']['usd'] = -1
    if mutation == 'default': m['settings']['width']['default'] = 257
    if mutation == 'enabled': m['enabled'] = True
    path = tmp_path/'catalog.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match=field): catalog.load(path)


def test_cloud_unknown_price_not_auto_eligible():
    assert not any(catalog.auto_eligible(m) for m in catalog.load()['models'])


def test_higgsfield_has_no_guessed_endpoints():
    model = next(m for m in catalog.load()['models'] if m['provider'] == 'higgsfield')
    assert model['status'] == 'OWNER_REQUIRED: official REST contract not supplied'
    assert not any(word in json.dumps(model) for word in ('https://','Authorization','/requests/'))
