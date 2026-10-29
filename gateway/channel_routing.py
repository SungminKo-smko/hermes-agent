"""채널의 명시적 대체 체인은 전역/세션 설정보다 우선하는 닫힌 정책이다."""
from hermes_cli.auth import AuthError

CHANNEL_UNAVAILABLE = (
    "⚠️ 이 채널에 허용된 모델을 모두 사용할 수 없습니다. "
    "채널 모델의 인증·사용량 설정을 확인해 주세요. 다른 모델로 전환하지 않았습니다."
)


class ChannelRouteError(RuntimeError):
    """예외 원문 없이 사용자에게 전달할 수 있는 정책 거절."""


def channel_policy(runner, source):
    """미지정은 None, 명시적 []는 빈 체인으로 구별한다."""
    from gateway.run import _get_channel_override
    config = getattr(runner, "config", None)
    if config is None or source is None:
        return None
    if getattr(config, "multiplex_profiles", False):
        from gateway.session_identity import canonical_identity, IdentityUnresolved
        identity = canonical_identity(source, runner=runner)
        if identity is None:
            raise IdentityUnresolved("channel policy requires a resolved runtime profile")
        primary = getattr(runner, "_primary_profile_name", None) or "default"
        if identity.runtime_profile != primary:
            config = (getattr(runner, "_profile_configs", None) or {}).get(identity.runtime_profile)
            if config is None:
                raise IdentityUnresolved("channel policy runtime profile is not served")
    override = _get_channel_override(
        config, source.platform, str(source.chat_id or ""),
        thread_id=str(source.thread_id) if source.thread_id else None,
        parent_id=str(source.parent_chat_id) if source.parent_chat_id else None,
    )
    if override is None or override.fallback_providers is None:
        return None
    return override


def require_managed_channel_support(authority, ref):
    """Managed workers cannot enforce closed channel routes yet; refuse before spawning/bootstrapping."""
    source = authority.sessions[ref.session_id].source
    if channel_policy(authority.runner, source) is not None:
        from hermes_state_runtime import RuntimeStoreError
        raise RuntimeStoreError("unsupported_managed_channel_policy")


def channel_entries(override):
    """일부 항목만 버려서 정책 의미를 바꾸지 않고 전체 선언을 검증한다."""
    raw = override.fallback_providers
    if not isinstance(raw, list):
        raise ChannelRouteError("⚠️ 채널 fallback_providers 설정이 올바르지 않아 실행하지 않았습니다.")
    from agent.auxiliary_client import _normalize_aux_provider
    entries = [{"provider": override.provider, "model": override.model}, *raw]
    for entry in entries:
        if (not isinstance(entry, dict)
                or any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in ("provider", "model"))
                or entry["provider"].strip().lower().removeprefix("custom:") in {"auto", "main"}):
            raise ChannelRouteError("⚠️ 채널 모델 설정에 명시적인 provider/model 쌍이 필요합니다.")
        if _normalize_aux_provider(entry["provider"]) == "custom":
            from urllib.parse import urlsplit
            endpoint = entry.get("base_url")
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise ChannelRouteError("⚠️ 채널 custom 모델 설정에는 구체적인 endpoint(base_url)가 필요합니다.")
            url = urlsplit(endpoint)
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ChannelRouteError("⚠️ 채널 custom 모델의 endpoint(base_url) 설정이 올바르지 않습니다.")
    return [dict(e, provider=e["provider"].strip(), model=e["model"].strip()) for e in entries]


def agent_fallback_chain(runner, source, route):
    """준비에 이미 실패한 경로는 반복하지 않고 남은 채널 경로만 넘긴다."""
    override = channel_policy(runner, source)
    if override is None:
        return runner._refresh_fallback_model()
    from hermes_cli.providers import normalize_provider
    runtime = route["runtime"]
    provider = normalize_provider(runtime.get("requested_provider") or runtime.get("provider") or "")
    entries = channel_entries(override)
    selected = route.get("_channel_route_index")
    for index, entry in enumerate(entries):
        if selected is not None and index != selected:
            continue
        if (normalize_provider(entry["provider"]) == provider and entry["model"] == route["model"]
                and (not entry.get("base_url") or entry["base_url"] == runtime.get("base_url"))):
            remaining = entries[index + 1:]
            from gateway.session_policy import policy_for_source
            if policy_for_source(runner, source) is not None:
                # Re-resolving the same frozen route must not pick up a rotated profile key/URL.
                remaining = [dict(e, **{k: runtime.get(k) for k in ("api_key", "base_url", "api_mode")})
                             for e in remaining]
            return remaining
    raise ChannelRouteError("⚠️ 채널에 허용되지 않은 모델 경로이므로 실행하지 않았습니다.")


def resolve_turn_runtime(turn, policy):
    """채널 경로와 API 잠금을 함께 검증하고 충돌하면 호출 전에 거절한다."""
    runner, ctx = turn._runner, turn._ctx
    model, runtime = runner._resolve_session_agent_runtime(
        source=ctx.source, session_key=ctx.session_key, user_config=ctx.user_config)
    notice = getattr(runner, "_pre_agent_fallback_notice", None)
    runner._pre_agent_fallback_notice = None
    from gateway.session_api_turn import prepare_api_runtime
    prepared_model, prepared_runtime = model, dict(runtime)
    model, runtime = prepare_api_runtime(model, runtime)
    channel = channel_policy(runner, ctx.source)
    if channel is not None:
        from hermes_cli.providers import normalize_provider
        provider = normalize_provider(runtime.get("provider") or "")
        if (not any(normalize_provider(entry["provider"]) == provider and entry["model"] == model
                    and (not entry.get("base_url") or entry["base_url"] == runtime.get("base_url"))
                    for entry in channel_entries(channel))
                or (model == prepared_model and provider == normalize_provider(prepared_runtime.get("provider") or "")
                    and runtime.get("base_url") != prepared_runtime.get("base_url"))):
            raise ChannelRouteError("⚠️ API 모델 선택과 채널 모델 정책이 충돌하여 실행하지 않았습니다.")
        return model, runtime, notice
    if policy and policy.model and not notice:
        from gateway.session_authorities import active_authority
        model = policy.current_model(active_authority(runner))
    from gateway.session_cli_turn import one_turn_route
    model, runtime = one_turn_route(turn, policy, model, runtime)
    return model, runtime, notice


def resolve_policy_channel_runtime(runner, channel, policy):
    """채널 선택으로 frozen launch 경로나 취소된 자격증명을 우회하지 않는다."""
    from gateway.run_turn_prepare import _resolve_policy_agent_runtime
    from gateway.session_authorities import active_authority
    from gateway.session_policy import route_launch_key
    from hermes_cli.providers import normalize_provider
    from hermes_cli.fallback_config import resolve_entry_api_key
    authority = active_authority(runner)
    frozen, model = policy.effective(authority)
    key = route_launch_key(authority, policy, frozen)
    route = frozen.get("model") or {}
    for entry in channel_entries(channel):
        if (normalize_provider(entry["provider"]) != normalize_provider(route.get("provider") or "")
                or entry["model"] != model
                or any(entry.get(k) and entry[k] != route.get(k) for k in ("base_url", "api_mode"))
                or (resolve_entry_api_key(entry) and resolve_entry_api_key(entry) != (key or route.get("api_key")))):
            raise ChannelRouteError("⚠️ launch 설정과 채널 모델 정책이 충돌하여 실행하지 않았습니다.")
    # Frozen credentials belong to this route, never the profile's current credentials.
    return _resolve_policy_agent_runtime(runner, policy, fallback=False)


def resolve_channel_runtime(runner, override, *, fallback=True):
    """전역 자동 감지 없이 각 provider/model을 한 쌍으로 준비한다."""
    from gateway.run import _runtime_agent_kwargs
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_cli.fallback_config import effective_runtime_provider, resolve_entry_api_key
    entries = channel_entries(override)
    for index, entry in enumerate(entries if fallback else entries[:1]):
        try:
            runtime = resolve_runtime_provider(
                requested=entry["provider"], target_model=entry["model"],
                explicit_base_url=entry.get("base_url"), explicit_api_key=resolve_entry_api_key(entry))
            runtime["provider"] = effective_runtime_provider(entry, runtime)
            if entry.get("api_mode"):
                runtime["api_mode"] = entry["api_mode"]
        except Exception as exc:
            from agent.error_classifier import classify_api_error
            if isinstance(exc, (TypeError, ValueError, LookupError, AttributeError, AssertionError)):
                raise
            if not isinstance(exc, AuthError) and not classify_api_error(
                    exc, provider=entry["provider"], model=entry["model"]).should_fallback:
                raise
            continue
        if index:
            runner._pre_agent_fallback_notice = "⚠️ 채널 우선 모델을 사용할 수 없어 지정된 대체 모델을 사용합니다."
        kwargs = _runtime_agent_kwargs(runtime)
        kwargs["_channel_route_index"] = index
        return entry["model"], kwargs
    raise ChannelRouteError(CHANNEL_UNAVAILABLE)


def arm_channel_surface_turn(runner, source, agent, notes=(), *, voice_turn=False):
    """음성 메모는 보존하되 별도 voice_chat 모델로 채널 정책을 우회하지 않는다."""
    from gateway.session_surface import arm_surface_turn
    arm_surface_turn(agent, notes, voice_turn=voice_turn)
    if channel_policy(runner, source) is not None:
        agent._voice_turn_pending = False


def finish_channel_result(runner, source, result, agent=None):
    """허용 체인 실패 안내에는 공급자의 원문이나 비밀값을 담지 않는다."""
    if channel_policy(runner, source) is None or not result.get("failed") or result.get("interrupted"):
        return result
    causes = {
        "auth": "인증 오류: 채널 공급자의 로그인과 키를 확인해 주세요.",
        "auth_permanent": "인증 오류: 채널 공급자의 로그인과 키를 확인해 주세요.",
        "billing": "결제·잔액 부족: 채널 공급자의 결제 설정을 확인해 주세요.",
        "rate_limit": "사용량 제한: 할당량을 확인하고 제한 해제 후 다시 시도해 주세요.",
        "upstream_rate_limit": "사용량 제한: 상위 모델의 제한 해제 후 다시 시도해 주세요.",
        "upstream_blocked": "공급자 연결 차단: 네트워크와 공급자 접근 정책을 확인해 주세요.",
        "overloaded": "공급자 과부하: 잠시 후 다시 시도해 주세요.",
        "server_error": "공급자 서버 오류: 상태를 확인하고 잠시 후 다시 시도해 주세요.",
        "timeout": "응답 지연: 연결 상태를 확인하고 잠시 후 다시 시도해 주세요.",
        "model_not_found": "모델 접근 오류: 채널 모델 ID와 제공 여부를 확인해 주세요.",
        "model_entitlement": "모델 접근 오류: 채널 계정의 모델 사용 권한을 확인해 주세요.",
        "provider_policy_blocked": "공급자 정책 제한: 계정의 데이터·접근 정책을 확인해 주세요.",
    }
    cause = causes.get(result.get("failure_reason"))
    if cause is None:
        return result
    if agent is None or getattr(agent, "_fallback_index", 0) < len(agent._fallback_chain):
        return result
    text = f"⚠️ 이 채널에 허용된 모델을 모두 사용할 수 없습니다. {cause} 다른 모델로 전환하지 않았습니다."
    old_response = result.get("final_response")
    messages = [
        {**message, "content": text}
        if old_response and message.get("role") == "assistant" and message.get("content") == old_response
        else message
        for message in result.get("messages", [])
    ]
    return {**result, "final_response": text, "error": "channel_routes_unavailable",
            "messages": messages, "completed": False}
