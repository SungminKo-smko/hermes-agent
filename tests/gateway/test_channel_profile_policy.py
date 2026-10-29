"""Channel policy follows canonical runtime identity, never the receiving bot."""
from pathlib import Path

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner, _profile_runtime_scope
from gateway.session import SessionSource
from gateway.session_identity import resolve_identity


def config(model=None):
    overrides = {} if model is None else {'room': {
        'provider': 'anthropic', 'model': model, 'fallback_providers': []}}
    return GatewayConfig(multiplex_profiles=True, platforms={
        Platform.SLACK: PlatformConfig.from_dict({'channel_overrides': overrides})})


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.hermes'
    home.mkdir()
    (home / '.env').write_text('ANTHROPIC_API_KEY=channel-fixture\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    for name in ('secondary', 'other'):
        (home / 'profiles' / name).mkdir(parents=True)
        (home / 'profiles' / name / 'config.yaml').write_text('{}')
        (home / 'profiles' / name / '.env').write_text('ANTHROPIC_API_KEY=channel-fixture\n')
    runner = object.__new__(GatewayRunner)
    runner.config = config()
    runner._primary_profile_name = 'default'
    runner._profile_configs = {'secondary': config('secondary-model'), 'other': config()}
    runner._profile_adapters = {}
    runner.adapters = {}
    return runner


@pytest.mark.parametrize('primary,secondary,profile,expected', [
    (None, 'secondary-model', 'secondary', 'secondary-model'),
    ('primary-model', 'secondary-model', 'secondary', 'secondary-model'),
    ('primary-model', None, 'secondary', None),
    ('primary-model', 'secondary-model', 'other', None),
    ('primary-model', 'secondary-model', None, 'primary-model'),
])
def test_runtime_profile_owns_policy(profiles, primary, secondary, profile, expected):
    from gateway.channel_routing import channel_policy, agent_fallback_chain
    runner = profiles
    runner.config = config(primary)
    runner._profile_configs['secondary'] = config(secondary)
    source = SessionSource(platform=Platform.SLACK, chat_id='room', chat_type='group', profile=profile)
    identity = resolve_identity(source, runner=runner)
    with _profile_runtime_scope(identity.runtime_home):
        policy = channel_policy(runner, source)
        assert (policy.model if policy else None) == expected
        if expected:
            model, runtime = runner._resolve_session_agent_runtime(source=source)
            assert model == expected
            assert agent_fallback_chain(runner, source, {'model': model, 'runtime': runtime}) == []


def test_unserved_profile_does_not_borrow_primary_policy(profiles):
    from gateway.channel_routing import channel_policy
    from gateway.session_identity import IdentityUnresolved
    source = SessionSource(platform=Platform.SLACK, chat_id='room', profile='missing')
    with pytest.raises(IdentityUnresolved):
        channel_policy(profiles, source)


def test_sol_explicit_credential_route_keeps_codex_identity(profiles):
    """실제 resolver의 명시적 자격증명 경로이며 OAuth/실서비스 검증은 아니다."""
    from gateway.channel_routing import agent_fallback_chain
    runner = profiles
    runner._profile_configs['secondary'].platforms[Platform.SLACK].channel_overrides['room'].provider = 'openai-codex'
    runner._profile_configs['secondary'].platforms[Platform.SLACK].channel_overrides['room'].model = 'gpt-6.1-sol'
    source = SessionSource(platform=Platform.SLACK, chat_id='room', profile='secondary')
    identity = resolve_identity(source, runner=runner)
    with _profile_runtime_scope(identity.runtime_home):
        # 실제 resolver에 fixture 키만 전달하며 외부 인증 저장소를 사용하지 않는다.
        channel = runner._profile_configs['secondary'].platforms[Platform.SLACK].channel_overrides['room']
        from hermes_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider(requested='openai-codex', target_model=channel.model,
                                           explicit_api_key='fixture-codex-not-a-real-token')
        assert runtime['provider'] == 'openai-codex'
        assert runtime['api_mode'] == 'codex_responses'
        route = runner._resolve_turn_agent_config('test', channel.model, runtime)
        assert agent_fallback_chain(runner, source, route) == []
