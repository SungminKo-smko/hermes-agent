# 채널 전용 대체 모델 정책

`platforms.<플랫폼>.channel_overrides.<채널>.fallback_providers`를 명시하면
해당 채널은 **우선 provider/model과 지정한 대체 provider/model만** 사용합니다.
다른 채널과 전역 설정은 변경하지 않습니다.

## DevOps 적용 예시

다음은 적용용 예시이며 이 변경에서 운영 설정을 수정하거나 재시작하지 않았습니다.
모델 접근 권한과 공급자의 실제 모델 ID는 적용 전에 확인해야 합니다.

```yaml
platforms:
  slack:
    channel_overrides:
      C0CC936G6G7:
        provider: anthropic
        model: claude-sonnet-5-5
        fallback_providers:
          - provider: openai-codex
            model: gpt-6.1-sol
```

인증 정보는 기존 공급자 인증 저장소/프로필 비밀값에서 해결합니다.
예시에 인증 키나 토큰을 넣지 않습니다. 대체 항목은 기존 엔진과 같이
`base_url`, `api_key` 또는 `key_env`/`api_key_env`, `api_mode`도 지원합니다.
준비 단계에서 선택한 항목의 endpoint와 인증은 해당 항목에만 적용됩니다.

## 우선순위와 거절 규칙

- `fallback_providers` **미지정**: 기존 정책을 유지합니다. 세션 `/model`이
  채널 모델보다 우선하며 기존 전역 대체 체인도 사용합니다.
- **명시적 목록**: 채널 우선 모델 → 목록 순서입니다. 세션 `/model`의 메모리·영속
  설정, 마지막 정상 모델 복구, 일회성 모델 선택은 이를 덮어쓰지 않습니다.
  API 모델 선택(확정 잠금 포함)은 기존 실행 전 검증을 거치며 채널의 provider/model·endpoint
  정책과 충돌하면 모델 호출 전에 거절합니다. 확정 API 잠금은 대체 모델을 사용하지 않습니다.
  LOCAL launch 정책의 자격증명 유효성 검증도 생략하지 않습니다. frozen launch 경로와
  채널 선언이 충돌하면 거절하며, 일치하는 경로는 frozen 인증·endpoint를 유지합니다.
  기존 세션의 `/model` 값은 삭제하지 않으며 정책이 있는 동안 사용하지 않습니다.
  음성 턴도 별도의 `auxiliary.voice_chat` 대화 모델로 바꾸지 않고 채널 경로를 사용합니다.
  음성 메모와 턴 부가 문맥은 그대로 보존합니다.
- **`[]`**: 우선 모델만 허용합니다. 전역 `fallback_providers` 및 구형
  `fallback_model`을 붙이지 않습니다.
- **잘못된 값**: `null`, 문자열, 객체, provider/model이 빠진 항목,
  자동 공급자 `auto`와 동적 `main`은 설정 오류로 거절합니다. 일부 항목만 버리거나 전역 체인을
  상속하지 않습니다. 익명 `custom`(동등 별칭 포함)은 구체적인 HTTP(S) `base_url`이
  없으면 거절하여 타 공급자 자동 탐색으로 빠지지 않습니다. 주 모델은 provider/model만
  받으므로 custom endpoint는 이름 있는 공급자로 등록해야 합니다.
  명시적 정책은 우선 모델에도 provider/model 쌍이 필요합니다.
  파싱된 `null`은 직렬화 시 `false`로 보존되어 미지정으로 바뀌지 않습니다.
- 채널 조회는 기존과 같이 chat ID → thread ID → parent ID 순으로 첫 일치 항목을
  사용합니다. 부모와 자식의 필드를 합치지 않습니다.
- 준비 단계의 `AuthError`, 기존 분류기가 대체 가능하다고 판정한 429 등의 오류만
  다음 항목을 시도합니다. 프로그래밍 오류는 대체 사유로 삼키지 않습니다.
- 준비 단계에서 이미 실패한 경로를 호출 단계 체인에 다시 넣지 않습니다.
  실제 모델 요청의 복구·대체는 기존 agent 엔진이 담당하며 전달받는 체인은
  해당 채널의 남은 경로뿐입니다. 기존 재시도/대기 시간 정책은 유지됩니다.

## 캐시와 실패 안내

기존 에이전트의 signature가 같아도 활성 대체 상태 또는 실제 provider/model이
이번 준비 경로와 다른 에이전트는 재사용하지 않습니다. 전역 대체의 cooldown이
남아 있어도 예외가 없습니다. 정상 우선 경로의 에이전트는 계속 재사용하며
대체 목록만 현재 채널 정책에 맞춥니다. 채널 정책 때문에 매 턴 시스템 프롬프트를
바꾸거나 새 모델 도구를 추가하지 않습니다.

허용 경로를 사용할 수 없으면 실패 상태를 유지하고 다음과 같이 안내합니다.

> ⚠️ 이 채널에 허용된 모델을 모두 사용할 수 없습니다. 채널 모델의 인증·사용량
> 설정을 확인해 주세요. 다른 모델로 전환하지 않았습니다.

준비 실패는 안전한 `ChannelRouteError` 문구를 사용합니다. 호출 단계에서는 **명시적인
모델 실패 분류와 실제 남은 체인 소진이 모두 확인된 경우에만** 최종 응답과 `error`를
안전한 한국어 안내와 `channel_routes_unavailable` 코드로 바꿉니다. `failure_reason`은
보존하며 인증·결제·사용량·연결·공급자 장애·모델 접근 오류별 원인과 조치를 안내합니다.
저장소 실패, 안전장치 거절, 문맥 초과 등 비모델 원인과 기존 안내는 덮어쓰지 않습니다.
성공 응답이나 사용자의 중단도 실패로 바꾸지 않습니다. 설정 오류는 별도의 한국어
설정 안내를 제공합니다.

## 반영과 재시작

**설치된 Python 코드 변경은 게이트웨이 재시작이 필요합니다.** 실행 중인 프로세스의
이미 import된 모듈이 디스크 수정만으로 교체되지는 않습니다.

**YAML의 채널 설정만 수정해도 일반적으로 재시작이 필요합니다.** 근거는 다음과 같습니다.

1. `gateway/run.py:GatewayRunner.__init__`가 `load_gateway_config_for_runner()` 결과를
   `self.config`에 보관합니다. 별도 프로필도 `_profile_configs`에 시작 시 설정을 보관합니다.
2. `gateway/channel_routing.py:channel_policy`와 기존 `_get_channel_override`는
   그 메모리의 `GatewayConfig.platforms[].channel_overrides`를 조회합니다.
3. `gateway/run_config_loaders.py:_refresh_fallback_model`의 디스크 재조회는 **전역 체인**용이며
   채널 설정을 다시 파싱하거나 메모리의 `channel_overrides`를 갱신하지 않습니다.

관리 경로가 **메모리의 해당 `ChannelOverride`까지 실제로 교체했다면**, 이 구현은 다음 턴에
이를 조회하고 캐시의 체인도 갱신하므로 그 값 자체를 반영하기 위한 추가 재시작은 필요하지
않습니다. 이는 자동 YAML hot reload나 공개 관리 명령을 새로 제공한다는 의미가 아닙니다.
운영에서는 설치 코드와 YAML을 정식 배포 경로로 반영한 뒤 계획된 재시작을 수행하는 편이
확실합니다. 이 작업에서는 배포·운영 설정 변경·재시작을 하지 않았습니다.

## 검증 범위

`tests/gateway/test_channel_exclusive_fallback.py`는 파싱/roundtrip, 준비 단계
인증·429 실패, 빈 목록·잘못된 설정, 세션 `/model`과 캐시 cooldown의 우회 방지,
준비 후 남은 체인, 다른 채널의 기존 동작을 검증합니다. 실제 `AIAgent`의 요청/분류/대체
경로는 HTTP 전송 경계만 가짜로 바꾸어 검증하며 유료 공급자에 연결하지 않습니다.
테스트 실행은 항상 `scripts/run_tests.sh`를 사용합니다.
