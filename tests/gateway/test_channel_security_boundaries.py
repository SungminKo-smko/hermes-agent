"""Closed channel routes intersect with credential and model-lock boundaries."""
from types import SimpleNamespace

import pytest

from tests.gateway.test_channel_exclusive_fallback import make_execution, make_runner


@pytest.mark.parametrize("locked_model", ["locked", "sonnet"])
def test_confirmed_api_lock_conflict_refuses_before_agent_call(monkeypatch, locked_model):
    from gateway.channel_routing import ChannelRouteError, resolve_turn_runtime
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.session_api_turn import api_execution

    runner, source = make_runner([])
    turn = make_execution(runner, source)
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: {
        "provider": kw["requested"], "requested_provider": kw["requested"], "api_key": "test"})
    selections, calls = [], []
    adapter = object.__new__(APIServerAdapter)
    adapter._model_routes = {}
    adapter._apply_provider_runtime = lambda runtime, provider, **kw: (
        selections.append(provider), runtime.update(provider=provider), True)[-1]
    adapter._recover_or_record_model = lambda model, *args: model
    turn._ctx.AIAgent = lambda **kw: calls.append(kw)
    token = api_execution.set({"adapter": adapter, "settings": {
        "confirmed_runtime_lock": True, "route": {"provider": "openai", "model": locked_model}}})
    try:
        with pytest.raises(ChannelRouteError, match="충돌"):
            resolve_turn_runtime(turn, None)
        result = turn._run_sync_scoped()
        assert result["failed"] is True and result["api_calls"] == 0
        assert "충돌" in result["final_response"]
    finally:
        api_execution.reset(token)
    assert selections == ["openai", "openai"]  # real prepare_api_runtime and selector executed
    assert calls == []


def local_channel_policy(tmp_path, monkeypatch, *, revoked=False):
    from dataclasses import replace
    from gateway.config import Platform
    from gateway.session_local import LocalSessionAdapter
    from gateway.session_policy import build_policy

    runner, source = make_runner([])
    source = replace(source, platform=Platform.LOCAL)
    runner.config.platforms[Platform.LOCAL] = runner.config.platforms.pop(Platform.SLACK)
    authority = SimpleNamespace(_local_launch_keys={}, sessions={"sid": SimpleNamespace(source=source)})
    policy = build_policy({"source": "cli", "cwd": str(tmp_path), "model": "sonnet"},
                          {"model": {"provider": "anthropic", "default": "sonnet"}})
    if revoked:
        policy = replace(policy, credential_ref="owner:epoch:sid#fingerprint")
    adapter = LocalSessionAdapter(authority)
    adapter.register_source(source)
    adapter.policies[source.chat_id] = policy
    runner._adapter_for_source = lambda source: adapter
    monkeypatch.setattr("gateway.session_authorities.active_authority", lambda runner: authority)
    return runner, source, policy


def test_channel_cannot_bypass_revoked_launch_key(tmp_path, monkeypatch):
    from hermes_state_runtime import RuntimeStoreError
    runner, source, _ = local_channel_policy(tmp_path, monkeypatch, revoked=True)
    calls = []
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: (
        calls.append(kw), {"provider": kw["requested"], "api_key": "test"})[-1])
    with pytest.raises(RuntimeStoreError, match="launch_credentials_unavailable"):
        runner._resolve_session_agent_runtime(source=source)
    assert calls == []


@pytest.mark.parametrize("change", ["primary", "fallback", "endpoint", "credential"])
def test_channel_launch_policy_conflict_is_fail_closed(tmp_path, monkeypatch, change):
    from gateway.channel_routing import ChannelRouteError
    runner, source, _ = local_channel_policy(tmp_path, monkeypatch)
    channel = runner.config.platforms[source.platform].channel_overrides["room"]
    if change == "primary":
        channel.model = "outside"
    else:
        entry = {"provider": "anthropic", "model": "sonnet"}
        entry.update({"model": "outside"} if change == "fallback" else
                     {"base_url": "https://outside.invalid"} if change == "endpoint" else
                     {"api_key": "other-key"})
        channel.fallback_providers = [entry]
    calls = []
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: (
        calls.append(kw), {"provider": kw["requested"], "api_key": "test"})[-1])
    with pytest.raises(ChannelRouteError, match="충돌"):
        runner._resolve_session_agent_runtime(source=source)
    assert calls == []


@pytest.mark.parametrize("provider", ["custom", "custom:", "main", "vllm"])
def test_anonymous_custom_without_endpoint_is_rejected_before_detection(monkeypatch, provider):
    from gateway.channel_routing import ChannelRouteError
    detections = []
    monkeypatch.setattr("agent.auxiliary_client._resolve_api_key_provider", lambda: (
        detections.append("other-provider"), (None, None))[-1])
    runner, source = make_runner([{"provider": provider, "model": "allowed-secondary"}])
    turn = make_execution(runner, source)
    route = {"model": "sonnet", "runtime": {"provider": "anthropic", "api_key": "test"}}
    with pytest.raises(ChannelRouteError, match="endpoint|설정"):
        agent = turn._build_fresh_agent(route, "slack", None, 2, None, {}, True)
        try:
            agent._try_activate_fallback()
        finally:
            agent.release_clients()
    assert detections == []


@pytest.mark.parametrize("reason", ["session_persistence_failed", "guardrail", "context_overflow",
                                     "content_policy_blocked", "data_policy_denied", "loop_error"])
def test_non_model_failure_keeps_cause_and_guidance(reason):
    from gateway.channel_routing import finish_channel_result
    runner, source = make_runner([])
    result = {"failed": True, "completed": False, "failure_reason": reason,
              "error": reason, "final_response": "저장·안전·문맥 설정을 확인해 주세요.",
              "messages": [{"role": "assistant", "content": "저장·안전·문맥 설정을 확인해 주세요."}]}
    agent = SimpleNamespace(_fallback_chain=[], _fallback_index=0)
    assert finish_channel_result(runner, source, result, agent) == result


@pytest.mark.parametrize("agent", [None, SimpleNamespace(_fallback_chain=[{}], _fallback_index=0)])
def test_model_failure_without_chain_exhaustion_evidence_is_not_relabelled(agent):
    from gateway.channel_routing import finish_channel_result
    runner, source = make_runner([{"provider": "deepseek", "model": "secondary"}])
    result = {"failed": True, "failure_reason": "auth", "error": "auth",
              "final_response": "현재 모델의 인증을 확인해 주세요."}
    assert finish_channel_result(runner, source, result, agent) == result


def test_api_cannot_retarget_allowed_pair_to_another_endpoint(monkeypatch):
    from gateway.channel_routing import ChannelRouteError, resolve_turn_runtime
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.session_api_turn import api_execution
    runner, source = make_runner([])
    turn = make_execution(runner, source)
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: {
        "provider": kw["requested"], "api_key": "test", "base_url": "https://primary.invalid/v1"})
    adapter = object.__new__(APIServerAdapter)
    adapter._model_routes = {}
    adapter._apply_provider_runtime = lambda runtime, provider, **kw: True
    adapter._recover_or_record_model = lambda model, *args: model
    token = api_execution.set({"adapter": adapter, "settings": {
        "confirmed_runtime_lock": True, "route": {"provider": "anthropic", "model": "sonnet",
            "base_url": "https://outside.invalid/v1"}}})
    try:
        with pytest.raises(ChannelRouteError, match="충돌"):
            resolve_turn_runtime(turn, None)
    finally:
        api_execution.reset(token)


@pytest.mark.parametrize("reason,label", [("auth", "인증 오류"), ("rate_limit", "사용량 제한"),
                                         ("timeout", "응답 지연"), ("model_not_found", "모델 접근 오류")])
def test_exhausted_model_failure_keeps_safe_korean_cause(reason, label):
    from gateway.channel_routing import finish_channel_result
    runner, source = make_runner([])
    agent = SimpleNamespace(_fallback_chain=[], _fallback_index=0)
    result = {"failed": True, "failure_reason": reason, "error": "private-provider-secret",
              "final_response": "private-provider-secret"}
    finished = finish_channel_result(runner, source, result, agent)
    assert label in finished["final_response"]
    assert finished["failure_reason"] == reason
    assert "private-provider-secret" not in finished["final_response"]
    assert finished["error"] == "channel_routes_unavailable"


def test_matching_channel_keeps_launch_credential_on_every_route(tmp_path, monkeypatch):
    from dataclasses import replace
    from gateway.channel_routing import agent_fallback_chain
    runner, source, policy = local_channel_policy(tmp_path, monkeypatch)
    adapter = runner._adapter_for_source(source)
    adapter.authority._local_launch_keys["owner:epoch:sid"] = "launch-only-key"
    adapter.policies[source.chat_id] = replace(policy, credential_ref="owner:epoch:sid#fingerprint")
    runner.config.platforms[source.platform].channel_overrides["room"].fallback_providers = [
        {"provider": "anthropic", "model": "sonnet"}]
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    assert runtime["api_key"] == "launch-only-key"
    chain = agent_fallback_chain(runner, source, {"model": model, "runtime": runtime})
    assert len(chain) == 1
    assert chain[0].get("api_key") == "launch-only-key"
    assert chain[0].get("base_url") == runtime["base_url"]


def test_prepared_duplicate_pair_keeps_exact_selected_position(monkeypatch):
    from gateway.channel_routing import agent_fallback_chain
    from hermes_cli.auth import AuthError

    runner, source = make_runner([
        {"provider": "deepseek", "model": "backup-middle"},
        {"provider": "anthropic", "model": "sonnet", "base_url": "https://alternate.invalid/v1"},
    ])
    attempts = []

    def resolve(**kwargs):
        attempts.append((kwargs["requested"], kwargs.get("explicit_base_url")))
        if kwargs.get("explicit_base_url") != "https://alternate.invalid/v1":
            raise AuthError("test-unavailable")
        return {"provider": "anthropic", "api_key": "test", "base_url": kwargs["explicit_base_url"]}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    route = runner._resolve_turn_agent_config("hello", model, runtime)
    assert len(attempts) == 3
    assert agent_fallback_chain(runner, source, route) == []
    assert "_channel_route_index" not in route["runtime"]
