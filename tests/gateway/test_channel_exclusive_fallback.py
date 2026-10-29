"""AutoSci #27: 명시적 채널 라우팅은 닫힌 정책이다."""
import pytest
from gateway.config import ChannelOverride, PlatformConfig
from gateway.run import GatewayRunner
from run_agent import AIAgent


def test_channel_fallback_roundtrip_preserves_absent_empty_and_routes():
    for declaration in ([], [{"provider": "openai-codex", "model": "gpt-6.1-sol"}]):
        raw = {"model": "sonnet", "provider": "anthropic", "fallback_providers": declaration}
        platform = PlatformConfig.from_dict({"channel_overrides": {"room": raw}})
        assert platform.to_dict()["channel_overrides"]["room"] == raw
    assert "fallback_providers" not in ChannelOverride().to_dict()


def make_runner(declared):
    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionSource
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.SLACK: PlatformConfig.from_dict({
        "channel_overrides": {"room": {"provider": "anthropic", "model": "sonnet",
                                      "fallback_providers": declared}}})})
    source = SessionSource(platform=Platform.SLACK, chat_id="room", chat_type="group")
    return runner, source


def test_preparation_uses_only_channel_chain_after_auth_failure(monkeypatch):
    from hermes_cli.auth import AuthError
    calls = []

    def resolve(*, requested=None, target_model=None, **kwargs):
        calls.append((requested, target_model))
        if requested == "anthropic":
            raise AuthError("비밀값은 안내문에 나오면 안 됨")
        assert requested == "openai-codex"
        return {"provider": requested, "api_key": "test", "base_url": "https://example.invalid"}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    runner, source = make_runner([{"provider": "openai-codex", "model": "gpt-6.1-sol"}])
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    assert (runtime["provider"], model) == ("openai-codex", "gpt-6.1-sol")
    assert calls == [("anthropic", "sonnet"), ("openai-codex", "gpt-6.1-sol")]


@pytest.mark.parametrize("declared", [None, False, "auto", {}, [None], [{"model": "orphan"}], [{"provider": "auto", "model": "anything"}]])
def test_malformed_policy_is_rejected_before_provider_access(declared, monkeypatch):
    from gateway.channel_routing import ChannelRouteError
    calls = []
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: calls.append(kw))
    runner, source = make_runner(declared)
    with pytest.raises(ChannelRouteError, match="설정"):
        runner._resolve_session_agent_runtime(source=source)
    assert calls == []
    assert "fallback_providers" in runner.config.platforms[source.platform].to_dict()["channel_overrides"]["room"]


def test_preparation_rate_limit_walks_channel_chain(monkeypatch):
    import httpx
    from openai import RateLimitError
    calls = []

    def resolve(*, requested=None, target_model=None, **kw):
        calls.append((requested, target_model))
        if requested == "anthropic":
            response = httpx.Response(429, request=httpx.Request("GET", "https://example.invalid"))
            raise RateLimitError("private-429", response=response, body=None)
        return {"provider": requested, "api_key": "test"}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    runner, source = make_runner([{"provider": "openai-codex", "model": "gpt-6.1-sol"}])
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    assert (runtime["provider"], model) == ("openai-codex", "gpt-6.1-sol")
    assert len(calls) == 2


def make_execution(runner, source):
    import threading
    from gateway.run_turn_runner import TurnRunner
    from gateway.turn_context import TurnContext
    from run_agent import AIAgent
    runner._session_db = runner._prefill_messages = None
    runner._service_tier = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    ctx = TurnContext(source=source, message="hi", history=[], session_id="sid", session_key="key",
                      user_config={}, enabled_toolsets=[], AIAgent=AIAgent)
    return TurnRunner(runner, ctx)


def test_fresh_agent_receives_channel_chain_not_global(monkeypatch):
    chain = [{"provider": "deepseek", "model": "secondary"}]
    runner, source = make_runner(chain)
    turn = make_execution(runner, source)
    runner.config.platforms[source.platform].channel_overrides["room"].provider = "openai"
    route = {"model": "sonnet", "runtime": {"provider": "openai", "api_key": "test",
             "base_url": "https://primary.invalid/v1"}}
    agent = turn._build_fresh_agent(route, "slack", None, 2, None, {}, True)
    try:
        assert agent._fallback_chain == chain
    finally:
        agent.release_clients()


def test_cached_active_global_fallback_cannot_bypass_channel_policy():
    import time
    chain = [{"provider": "deepseek", "model": "secondary"}]
    runner, source = make_runner(chain)
    runner.config.platforms[source.platform].channel_overrides["room"].provider = "openai"
    turn = make_execution(runner, source)
    route = {"model": "sonnet", "runtime": {"provider": "openai", "api_key": "test",
             "base_url": "https://primary.invalid/v1"}}
    warmup, _ = turn._resolve_turn_agent(route, "slack", None, 2, None, {})
    original, _ = turn._resolve_turn_agent(route, "slack", None, 2, None, {})
    original.provider, original.model = "openrouter", "outside-policy"
    original._fallback_activated = True
    original._rate_limited_until = time.monotonic() + 3600
    original._fallback_chain = [{"provider": "openrouter", "model": "outside-policy"}]
    agent, reused = turn._resolve_turn_agent(route, "slack", None, 2, None, {})
    try:
        assert (agent.provider, agent.model) == ("openai", "sonnet")
        assert agent._fallback_chain == chain
        assert reused is False
    finally:
        agent.release_clients()
        original.release_clients()
        warmup.release_clients()


@pytest.mark.parametrize("declared,expected_calls", [([], 1), ([{"provider": "openai-codex", "model": "gpt-6.1-sol"}], 2)])
def test_exhausted_preparation_is_safe_korean_failure(declared, expected_calls, monkeypatch):
    from gateway.channel_routing import ChannelRouteError
    from gateway.run_turn_runner import _resolution_failure_reply
    from hermes_cli.auth import AuthError
    calls = []

    def unavailable(**kwargs):
        calls.append(kwargs)
        raise AuthError("private-secret-error")

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", unavailable)
    runner, source = make_runner(declared)
    with pytest.raises(ChannelRouteError) as caught:
        runner._resolve_session_agent_runtime(source=source)
    text, _, _ = _resolution_failure_reply(caught.value, source.platform)
    assert "이 채널에 허용된 모델" in text
    assert "다른 모델로 전환하지 않았습니다" in text
    assert "private-secret" not in text
    assert len(calls) == expected_calls


def test_channel_route_wins_over_launch_and_one_turn_policy(monkeypatch):
    from gateway.channel_routing import resolve_turn_runtime
    runner, source = make_runner([])
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: {
        "provider": kw["requested"], "api_key": "test"})
    runner._session_state("key").conversation.model_override = {
        "model": "outside", "provider": "openrouter", "api_key": "old"}
    runner._session_state("*").conversation.last_resolved_model = "outside-last"
    turn = make_execution(runner, source)
    model, runtime, notice = resolve_turn_runtime(turn, object())
    assert (runtime["provider"], model, notice) == ("anthropic", "sonnet", None)


@pytest.mark.parametrize("both_fail", [False, True])
@pytest.mark.parametrize("fallback_provider", ["deepseek", "custom"])
def test_provider_call_fallback_stays_inside_channel_chain(monkeypatch, both_fail, fallback_provider):
    import json
    import httpx
    from openai import OpenAI
    chain = [{"provider": fallback_provider, "model": "deepseek-v4-flash",
              "base_url": "https://secondary.invalid/v1", "api_key": "test"}]
    runner, source = make_runner(chain)
    override = runner.config.platforms[source.platform].channel_overrides["room"]
    override.provider, override.model = "openai", "gpt-4o-mini"
    turn = make_execution(runner, source)
    route = {"model": override.model, "runtime": {"provider": "openai", "api_key": "test",
             "base_url": "https://primary.invalid/v1"}}
    calls = []

    def transport(request):
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content)
        calls.append((request.url.host, body["model"]))
        if request.url.host == "primary.invalid" or both_fail:
            return httpx.Response(401, json={"error": {"message": "private-primary", "type": "authentication_error"}})
        if body.get("stream"):
            chunk = {"id": "test", "object": "chat.completion.chunk", "created": 1,
                     "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                     "delta": {"role": "assistant", "content": "채널 대체 성공"}}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "채널 대체 성공"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: transport(request))
    primary = OpenAI(api_key="test", base_url="https://primary.invalid/v1", max_retries=0,
                     http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    detections = []
    monkeypatch.setattr("agent.auxiliary_client._resolve_api_key_provider", lambda: (
        detections.append("other-provider"), (None, None))[-1])
    agent = turn._build_fresh_agent(route, "slack", None, 3, None, {}, True)
    agent.client = primary
    try:
        result = agent.run_conversation("안녕")
        if both_fail:
            from gateway.channel_routing import finish_channel_result
            result = finish_channel_result(runner, source, result, agent)
            assert result["failed"] is True
            assert "이 채널에 허용된 모델" in result["final_response"]
            assert "private-primary" not in result["final_response"]
            assert "private-primary" not in result["error"]
        else:
            assert result["final_response"] == "채널 대체 성공"
        assert calls == [("primary.invalid", "gpt-4o-mini"), ("secondary.invalid", "deepseek-v4-flash")]
        assert agent._try_activate_fallback() is False
        assert detections == []
    finally:
        agent.release_clients()
        primary.close()


def test_preparation_preserves_explicit_fallback_endpoint(monkeypatch):
    from hermes_cli.auth import AuthError
    calls = []

    def resolve(**kw):
        calls.append(kw)
        if kw["requested"] == "anthropic":
            raise AuthError("unavailable")
        assert kw["explicit_base_url"] == "https://fallback.invalid/v1"
        assert kw["explicit_api_key"] == "test-key"
        return {"provider": "custom", "requested_provider": "custom", "api_key": "test-key",
                "base_url": kw["explicit_base_url"]}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    runner, source = make_runner([{"provider": "custom", "model": "alt",
                                  "base_url": "https://fallback.invalid/v1", "api_key": "test-key"}])
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    assert (runtime["provider"], model, runtime["base_url"]) == ("custom", "alt", "https://fallback.invalid/v1")


@pytest.mark.parametrize("error", [TypeError("429 rate limit exceeded"), ValueError("401 unauthorized"), KeyError("model not found")])
def test_preparation_does_not_swallow_programming_errors(error, monkeypatch):
    calls = []

    def broken(**kw):
        calls.append(kw)
        raise error

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", broken)
    runner, source = make_runner([{"provider": "openai-codex", "model": "gpt-6.1-sol"}])
    with pytest.raises(type(error)) as caught:
        runner._resolve_session_agent_runtime(source=source)
    assert caught.value is error
    assert len(calls) == 1


def test_turn_runner_propagates_channel_programming_error(monkeypatch):
    def broken(**kw):
        raise TypeError("programming bug")

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", broken)
    runner, source = make_runner([])
    turn = make_execution(runner, source)
    with pytest.raises(TypeError, match="programming bug"):
        turn._run_sync_scoped()


def test_persisted_model_override_is_not_rehydrated_over_channel(tmp_path, monkeypatch):
    from gateway.session import SessionStore
    runner, source = make_runner([])
    store = SessionStore(tmp_path / "sessions", runner.config)
    entry = store.get_or_create_session(source)
    store.set_model_override(entry.session_key, {"provider": "openrouter", "model": "outside"})
    runner.session_store = store
    calls = []

    def resolve(**kw):
        calls.append((kw["requested"], kw["target_model"]))
        return {"provider": kw["requested"], "api_key": "test"}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    try:
        model, runtime = runner._resolve_session_agent_runtime(source=source, session_key=entry.session_key)
        assert (runtime["provider"], model) == ("anthropic", "sonnet")
        assert calls == [("anthropic", "sonnet")]
        assert runner._peek_session_state(entry.session_key) is None
        assert store.get_model_override(entry.session_key)["model"] == "outside"
    finally:
        store._db.close()


def test_other_channel_keeps_global_chain_and_session_model(tmp_path, monkeypatch):
    from dataclasses import replace
    from hermes_constants import get_hermes_home
    import hermes_yaml as yaml
    runner, source = make_runner([])
    source = replace(source, chat_id="other")
    config = {"model": {"provider": "openai", "default": "global"},
              "fallback_providers": [{"provider": "deepseek", "model": "global-backup"}]}
    (get_hermes_home() / "config.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: {"provider": "openai", "api_key": "test"})
    runner._session_state("key").conversation.model_override = {
        "provider": "openai", "model": "session", "api_key": "test", "credential_pool": object()}
    model, runtime = runner._resolve_session_agent_runtime(source=source, session_key="key", user_config=config)
    assert (runtime["provider"], model) == ("openai", "session")
    from gateway.channel_routing import agent_fallback_chain
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(get_hermes_home())
    try:
        assert agent_fallback_chain(runner, source, {"model": model, "runtime": runtime}) == config["fallback_providers"]
    finally:
        reset_hermes_home_override(token)


def test_prepared_fallback_does_not_retry_failed_primary(monkeypatch):
    from gateway.channel_routing import agent_fallback_chain
    from hermes_cli.auth import AuthError
    runner, source = make_runner([{"provider": "openai-codex", "model": "gpt-6.1-sol"}])

    def resolve(**kw):
        if kw["requested"] == "anthropic":
            raise AuthError("unavailable")
        return {"provider": kw["requested"], "api_key": "test"}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve)
    model, runtime = runner._resolve_session_agent_runtime(source=source)
    assert agent_fallback_chain(runner, source, {"model": model, "runtime": runtime}) == []


def test_memory_policy_edit_clears_cached_primary_fallback_chain():
    runner, source = make_runner([{"provider": "deepseek", "model": "secondary"}])
    override = runner.config.platforms[source.platform].channel_overrides["room"]
    override.provider = "openai"
    turn = make_execution(runner, source)
    route = {"model": "sonnet", "runtime": {"provider": "openai", "api_key": "test",
             "base_url": "https://primary.invalid/v1"}}
    warmup, _ = turn._resolve_turn_agent(route, "slack", None, 2, None, {})
    original, _ = turn._resolve_turn_agent(route, "slack", None, 2, None, {})
    override.fallback_providers = []
    agent, reused = turn._resolve_turn_agent(route, "slack", None, 2, None, {})
    try:
        assert agent is original and reused
        assert agent._fallback_chain == []
        assert agent._try_activate_fallback() is False
    finally:
        agent.release_clients()
        warmup.release_clients()


@pytest.mark.parametrize("field,value", [("model", ""), ("model", None), ("provider", "auto"), ("provider", None)])
def test_missing_primary_pair_cannot_recover_last_model(field, value, monkeypatch):
    from gateway.channel_routing import ChannelRouteError
    runner, source = make_runner([])
    setattr(runner.config.platforms[source.platform].channel_overrides["room"], field, value)
    runner._session_state("*").conversation.last_resolved_model = "outside"
    calls = []
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **kw: calls.append(kw))
    with pytest.raises(ChannelRouteError, match="설정"):
        runner._resolve_session_agent_runtime(source=source)
    assert calls == []


def test_voice_surface_cannot_replace_closed_channel_model():
    from gateway.channel_routing import arm_channel_surface_turn
    from gateway.session_surface import surface_turn_scope
    runner, source = make_runner([])
    agent = object.__new__(AIAgent)
    with surface_turn_scope({"voice_turn": True}):
        arm_channel_surface_turn(runner, source, agent, ["유지할 메모"], voice_turn=True)
    assert agent._voice_turn_pending is False
    assert agent._gateway_turn_context_notes == "유지할 메모"


def test_legacy_empty_model_still_uses_catalog_then_last_known_model():
    from gateway.run_turn_prepare import _recover_or_remember_model
    from hermes_cli.models import get_default_model_for_provider
    runner, _ = make_runner([])
    expected = get_default_model_for_provider("openai")
    model = _recover_or_remember_model(runner, "key", "", {"provider": "openai"})
    assert expected and model == expected
    assert runner._session_state("key").conversation.last_resolved_model == expected
    assert _recover_or_remember_model(runner, "key", "", {}) == expected
