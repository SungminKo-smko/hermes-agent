"""Actual LOCAL policy selection and bootstrap must not export a global fallback through a closed channel."""
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_local import LocalSessionAdapter
from gateway.session_managed_worker import _bootstrap, managed_policy
from gateway.session_policy import build_policy
from hermes_state_runtime import RuntimeStoreError


@pytest.fixture
def local_boot(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr('gateway.run._hermes_home', tmp_path)
    frozen = {'gateway': {'managed_workers': True},
              'model': {'provider': 'custom', 'default': 'primary', 'base_url': 'http://127.0.0.1:9/v1'},
              'fallback_providers': [{'provider': 'openai', 'model': 'outside'}]}
    (tmp_path / 'config.yaml').write_text(json.dumps(frozen))
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.LOCAL: PlatformConfig.from_dict({
        'channel_overrides': {'room': {'provider': 'anthropic', 'model': 'sonnet', 'fallback_providers': []}}})})
    source = SessionSource(platform=Platform.LOCAL, chat_id='room', user_id='u')
    authority = SimpleNamespace(runner=runner, profile_id=str(tmp_path), _local_launch_keys={},
                                sessions={'sid': SimpleNamespace(source=source, route='managed-channel')})
    adapter = LocalSessionAdapter(authority)
    adapter.register_source(source)
    runner.adapters = {Platform.LOCAL: adapter}
    runner._profile_adapters = {}
    policy = build_policy({'source': 'cli', 'cwd': str(tmp_path), 'model': 'primary',
                           'provider': 'custom', 'base_url': 'http://127.0.0.1:9/v1'}, frozen)
    adapter.policies[source.chat_id] = policy
    ref = SimpleNamespace(session_id='sid')
    row = {'payload': {'text': 'hello'}}
    scope = {'profile_id': str(tmp_path), 'session_id': 'sid', 'execution_id': 'x', 'generation': 1,
             'pid': os.getpid(), 'birth': 0, 'secret': 's', 'epoch': 1}
    return authority, ref, row, policy, scope, adapter


@pytest.mark.parametrize('declaration', [[], [{'provider': 'openai', 'model': 'outside'}]])
def test_closed_channel_refuses_managed_selection_before_spawn(local_boot, declaration):
    authority, ref, _, _, _, _ = local_boot
    channel = authority.runner.config.platforms[Platform.LOCAL].channel_overrides['room']
    channel.fallback_providers = declaration
    with pytest.raises(RuntimeStoreError, match='unsupported_managed_channel_policy'):
        managed_policy(authority, ref)


@pytest.mark.parametrize('revoked', [False, True])
def test_managed_bootstrap_cannot_export_conflicting_or_revoked_launch(local_boot, revoked):
    authority, ref, row, policy, scope, _ = local_boot
    if revoked:
        policy = replace(policy, credential_ref='owner:epoch:sid#fingerprint')
    with pytest.raises(RuntimeStoreError, match='unsupported_managed_channel_policy'):
        _bootstrap(authority, ref, row, policy, scope)


def test_unspecified_channel_keeps_managed_bootstrap_and_global_chain(local_boot):
    from agent.managed_worker import validate_bootstrap
    authority, ref, row, policy, scope, _ = local_boot
    authority.runner.config.platforms[Platform.LOCAL].channel_overrides.clear()
    assert managed_policy(authority, ref) is policy
    frame = validate_bootstrap(_bootstrap(authority, ref, row, policy, scope))
    assert json.loads(frame['policy']['request_json'])['construct_v1']['fallback_model'] == [
        {'provider': 'openai', 'model': 'outside'}]


def test_unspecified_channel_still_rejects_revoked_launch_key(local_boot):
    authority, ref, row, policy, scope, _ = local_boot
    authority.runner.config.platforms[Platform.LOCAL].channel_overrides.clear()
    policy = replace(policy, credential_ref='owner:epoch:sid#fingerprint')
    with pytest.raises(RuntimeStoreError, match='launch_credentials_unavailable'):
        _bootstrap(authority, ref, row, policy, scope)


def test_unmanaged_local_channel_is_still_supported(local_boot):
    authority, ref, _, policy, _, adapter = local_boot
    frozen = json.loads(policy.config_json)
    frozen['gateway']['managed_workers'] = False
    adapter.policies['room'] = replace(policy, config_json=json.dumps(frozen))
    assert managed_policy(authority, ref) is None


@pytest.mark.platforms('posix')
def test_recovered_managed_session_refuses_channel_before_worker_boot(tmp_path):
    """Boot the real daemon twice: a durable LOCAL session gains a closed channel policy on restart."""
    import asyncio
    from contextlib import closing
    from pathlib import Path
    import sqlite3
    from tests.gateway.fixtures.local_recovery_probe import child_env, daemon, rpc, websocket
    from tests.gateway.test_managed_worker_construct import Primary, _serve
    from tests.waiting import async_wait_until

    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir(mode=0o700)
    user.mkdir()
    server = _serve(Primary)
    url = f'http://127.0.0.1:{server.server_port}/v1'
    cfg = {'gateway': {'multiplex_profiles': False, 'managed_workers': True},
           'model': {'provider': 'custom', 'default': 'primary', 'base_url': url},
           'auxiliary': {'title_generation': {'enabled': False}}, 'platform_toolsets': {'cli': []}}
    (home / 'config.yaml').write_text(json.dumps(cfg))
    env = child_env()
    env.update(HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home), PYTHONPATH=str(root),
               OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url)

    def query(sql, args=()):
        with closing(sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True)) as db:
            return db.execute(sql, args).fetchall()

    async def create_and_run(desc):
        async with websocket(home, desc) as ws:
            created = await rpc(ws, 'session.create', request_id='closed-managed', source='cli', cwd=str(home),
                                model='primary', provider='custom', base_url=url, toolsets=[])
            assert 'result' in created, created
            sid = created['result']['session_id']
            await rpc(ws, 'prompt.submit', session_id=sid, input_id='positive-control', text='BEFORE_POLICY')
            await async_wait_until(lambda: query('SELECT status FROM session_admissions WHERE request_id=?',
                                                 ('positive-control',)) == [('terminal',)], timeout=90,
                                   what='real managed positive control')
            assert query('SELECT status FROM worker_executions WHERE session_id=?', (sid,)) == [('terminal',)]
            assert server.requests
            return sid

    async def refuse(desc, sid):
        async with websocket(home, desc) as ws:
            resumed = await rpc(ws, 'session.resume', session_id=sid)
            assert 'result' in resumed, resumed
            submitted = await rpc(ws, 'prompt.submit', session_id=sid, input_id='after-policy', text='DO_NOT_CALL')
            assert 'result' in submitted, submitted
            await async_wait_until(lambda: query('SELECT status FROM session_admissions WHERE request_id=?',
                                                 ('after-policy',)) == [('terminal',)], timeout=30,
                                   what='closed channel refused before worker boot')
            [(admission,)] = query('SELECT admission_id FROM session_admissions WHERE request_id=?', ('after-policy',))
            receipt = await rpc(ws, 'prompt.receipt', session_id=sid, admission_id=admission, include_result=True)
            result = receipt['result']['result']
            workers = query('SELECT COUNT(*) FROM worker_executions WHERE session_id=?', (sid,))
            assert workers == [(1,)], {'workers': workers, 'result': result}
            assert len(server.requests) == requests_before
            assert result['failed'] is True, receipt
            print(json.dumps({'managed_channel_refusal': result, 'worker_count': workers[0][0],
                              'model_requests_before': requests_before, 'model_requests_after': len(server.requests)}))

    try:
        with daemon(root, home, env, barrier=False) as (_, desc):
            sid = asyncio.run(create_and_run(desc))
        requests_before = len(server.requests)
        cfg['platforms'] = {'local': {'channel_overrides': {sid: {
            'provider': 'anthropic', 'model': 'not-the-launch-route', 'fallback_providers': []}}}}
        (home / 'config.yaml').write_text(json.dumps(cfg))
        with daemon(root, home, env, barrier=False) as (_, desc):
            asyncio.run(refuse(desc, sid))
    finally:
        server.shutdown()
        server.server_close()
