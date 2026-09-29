"""RC19 owner run: a 40-step Studio job showed «30 шагов» while the engine ran 40.

The job row (what Studio shows) kept table defaults; only the dispatch plane
carried the request. The row now records the requested size/steps/seed.
"""


async def test_job_row_shows_the_requested_steps_size_and_seed(env):
    r = await env.client.post('/api/studio/jobs', json={
        'model': 'mock:image', 'prompt': 'starfish royal flush',
        'settings': {'width': 768, 'height': 512, 'steps': 40, 'seed': 777}})
    assert r.status_code == 200, r.text
    job = (await env.client.get(f"/api/studio/jobs/{r.json()['id']}")).json()
    assert (job['steps'], job['width'], job['height'], job['seed']) == (40, 768, 512, 777)
    assert job['studio']['plane']['settings']['steps'] == 40
