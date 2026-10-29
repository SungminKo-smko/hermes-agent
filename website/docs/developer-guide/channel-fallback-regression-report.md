# 채널 fallback PR 후속 회귀 분류

## 판정 범위

이 문서는 초기 전체 gateway 비녹색 항목을 고정 PR `4456150535`와 설치 기준 `bf0203a794`에서 전수 비교한 기록입니다. **비재현을 해결 또는 기준 재현으로 간주하지 않습니다.** 현재 후속 수정의 집중 회귀 결과 및 재확인은 아래 추기에서 별도로 구분합니다.

## 초기 PR의 고정 snapshot 비교

- PR: `44561505354b0c5286f5dd587ca8526ec290a0c2`
- baseline: `bf0203a794a07aac3ff764972ab9087baa4e4f4e`
- git archive 독립 snapshot만 실행. 변경 중인 원 작업트리는 읽기 전용 git object 조회에만 사용.
- 초기 17실패 nodeid + 14 timeout 파일 = **31항목 / 28고유파일**, 코드로 중복·누락 검증. 14파일은 모두 실제 로그에 120초 SIGKILL이 있으며 단순 collection error로 바꾸지 않았다.

## 결과

- pr: `=== Summary: 28 files, 405 tests passed, 5 failed (100% complete) in 386.4s (2 workers) ===`; `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/pr-comparison.log`
- baseline: `=== Summary: 28 files, 406 tests passed, 4 failed (100% complete) in 400.6s (2 workers) ===`; `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/baseline-comparison.log`

- 기준 재현 **5항목**; 기준·변경 동일 통과의 비재현 **26항목**; 변경유발 확인 **0**; 미분류 **0**.
- 테스트 nodeid 분류: `{"baseline_reproduced": 5, "not_reproduced_same_state": 12}`
- timeout 파일 분류: `{"not_reproduced_same_state": 14}`
- 이는 전체 gateway 녹색이 아니다. 양쪽에 실패가 남고, initial 환경/부하 비재현도 해결 증거가 아니다.

## 초기 17실패 — 정확한 nodeid별

| nodeid | PR 본 비교 | baseline 본 비교 | 분류 | 근거/원인 |
|---|---|---|---|---|
| `tests/gateway/test_api_admission_off_loop.py::test_admission_waits_for_the_writer_off_the_owner_loop[chat]` | failed | failed | 기준 재현 (same_assertion) | chat admission의 SQLite writer 대기 중 owner-loop gap이 HOLD_S/3(0.5초)를 초과. PR·baseline 같은 [chat] assertion 재현. |
| `tests/gateway/test_buzz_websocket.py::test_websocket_loop_backs_off_and_publishes_retrying_on_clean_relay_close` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: clean relay close 이후 0.3초 창 안에 retrying 상태가 기록되지 않아 [] != [retrying]. 본 비교 및 교차반복 양쪽 모두 통과. 타이밍/환경 가능성이지 원인 확정은 아님. |
| `tests/gateway/test_docker_session_workspace.py::test_side_agent_runs_in_its_sessions_workspace[True]` | failed | failed | 기준 재현 (same_assertion) | persistent=True side-agent 컨테이너 namespace가 profile:developer:ws:...인데 세션 기대값은 default:ws:.... 동일 환경 양쪽 재현. |
| `tests/gateway/test_model_confirmation_clients.py::test_acp_set_session_model_asks_through_request_permission` | passed | passed | 양쪽 통과·비재현 (dependency_available_both_pass) | initial: acp import 누락. 동일 full-gateway interpreter의 의존성이 있는 본 비교에서는 양쪽 실제 실행·통과. initial 의존성 오류를 baseline 재현으로 세지 않음. |
| `tests/gateway/test_msgraph_authority.py::test_graph_boundary_commits_only_valid_notifications` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: 의도적으로 주입된 owned storage failure 로그와 별개로 peer.blocked.wait(10)이 False라 subprocess assertion 실패. 양쪽 본 비교에서는 통과. |
| `tests/gateway/test_owner_loop_writes_create_api.py::test_session_create_commit_never_runs_on_the_owner_loop_thread` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: asyncio wait_for/CancelledError 이후 TimeoutError. 본 비교에서는 양쪽 2개 테스트 통과. |
| `tests/gateway/test_parked_profile_ingress.py::test_a_coalesced_webhook_for_a_parked_profile_is_retried_not_lost` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: parked profile의 retried batch dispatch를 10초 내 관측 못함. 본 비교에서는 양쪽 16개 테스트 통과. |
| `tests/gateway/test_run_progress_topics.py::test_retryable_overflow_edit_keeps_editable_bubble_identity` | failed | passed | 기준 재현 (intermittent_same_assertion) | initial 및 PR 본 비교: retryable_edit_failures=0, 기대=1. baseline 본 비교는 통과하여 곧바로 기존 flaky로 분류하지 않고 동일 3파일/-j2/300초 교차순서 반복. crosscheck-4-baseline에서 같은 nodeid·같은 0==1 assertion이 실제 재현. 비결정적 기준 재현이며 정확한 scheduling 원인은 미확정. 테스트의 ManyProgressLinesAgent는 첫 send를 최대 5초 기다린 뒤 0.05/0.1초 sleep에 의존하며 split-edit가 관측되지 않은 증상이다. |
| `tests/gateway/test_session_client_env.py::test_configured_toolchain_names_a_local_client_omitted_never_leak_from_the_daemon` | failed | failed | 기준 재현 (same_assertion) | child-tool env PATH 맨 앞에 full-gateway/bin이 추가되어 /client/bin 기대값과 불일치. 두 nodeid 모두 동일 환경 baseline·PR 재현. |
| `tests/gateway/test_session_client_env.py::test_host_values_apply_only_over_a_gateway_proven_local_attach` | failed | failed | 기준 재현 (same_assertion) | child-tool env PATH 맨 앞에 full-gateway/bin이 추가되어 /client/bin 기대값과 불일치. 두 nodeid 모두 동일 환경 baseline·PR 재현. |
| `tests/gateway/test_session_edit_policy.py::test_edit_policy_is_a_durable_session_setting_every_viewer_reads` | passed | passed | 양쪽 통과·비재현 (dependency_available_both_pass) | initial: acp import 누락으로 두 테스트 실패. 동일 full-gateway 환경에서는 양쪽 9개 실제 실행·통과. |
| `tests/gateway/test_session_edit_policy.py::test_a_branch_of_a_dont_ask_session_starts_at_ask` | passed | passed | 양쪽 통과·비재현 (dependency_available_both_pass) | initial: acp import 누락으로 두 테스트 실패. 동일 full-gateway 환경에서는 양쪽 9개 실제 실행·통과. |
| `tests/gateway/test_session_listener_lazy_dashboard.py::test_session_listener_serves_native_ws_before_importing_the_dashboard` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: /api/status HTTP GET의 httpx.ReadTimeout으로 자식 프로세스 exit=1. 본 비교에서는 양쪽 통과. |
| `tests/gateway/test_session_policy_refusal_fields.py::test_every_create_check_names_the_field_it_read[extra25-editor.mcp_servers]` | passed | passed | 양쪽 통과·비재현 (dependency_available_both_pass) | initial: acp import 누락으로 두 nodeid 실패. 본 비교에서는 양쪽 49개 실제 실행·통과. |
| `tests/gateway/test_session_policy_refusal_fields.py::test_an_unusable_server_spec_never_echoes_its_values` | passed | passed | 양쪽 통과·비재현 (dependency_available_both_pass) | initial: acp import 누락으로 두 nodeid 실패. 본 비교에서는 양쪽 49개 실제 실행·통과. |
| `tests/gateway/test_static_token_session_mutations.py::test_static_token_http_mutations_reuse_ws_owner` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: websocket opening handshake timeout. 본 비교에서는 양쪽 통과. |
| `tests/gateway/test_stream_consumer_wecom_native.py::TestClarifyEagerReseed::test_double_clarify_boundary_reseed_chain` | passed | passed | 양쪽 통과·비재현 (timing_or_environment_possible) | initial: 두 번째 clarify boundary 후 eager seed 프레임 수가 2에서 늘지 않아 기대 3과 불일치. 본 비교·교차반복에서 초기 실패의 정확한 nodeid 포함 양쪽 모두 통과. 과거 baseline 로그의 다른 eager-seed nodeid 실패를 이 nodeid의 기준 재현으로 대체하지 않음. |

## 초기 14 timeout 파일 — 파일별

| 파일 | initial | PR | baseline | 분류 |
|---|---|---|---|---|
| `tests/gateway/test_child_notice_profile_admission.py` | 120초 SIGKILL | 128✓, 73.7초 | 128✓, 67.2초 | 양쪽 통과·비재현 |
| `tests/gateway/test_local_automation.py` | 120초 SIGKILL | 5✓, 110.8초 | 5✓, 108.8초 | 양쪽 통과·비재현 |
| `tests/gateway/test_managed_worker_images.py` | 120초 SIGKILL | 6✓, 35.6초 | 6✓, 38.6초 | 양쪽 통과·비재현 |
| `tests/gateway/test_managed_worker_lineage.py` | 120초 SIGKILL | 10✓, 55.6초 | 10✓, 61.1초 | 양쪽 통과·비재현 |
| `tests/gateway/test_native_ingress_trust.py` | 120초 SIGKILL | 4✓, 14.8초 | 4✓, 18.3초 | 양쪽 통과·비재현 |
| `tests/gateway/test_native_role_reauthorization.py` | 120초 SIGKILL | 2✓, 13.3초 | 2✓, 13.2초 | 양쪽 통과·비재현 |
| `tests/gateway/test_native_startup_recovery.py` | 120초 SIGKILL | 1✓, 14.5초 | 1✓, 14.4초 | 양쪽 통과·비재현 |
| `tests/gateway/test_session_hosted_daemon.py` | 120초 SIGKILL | 2✓, 53.2초 | 2✓, 53.1초 | 양쪽 통과·비재현 |
| `tests/gateway/test_session_kanban.py` | 120초 SIGKILL | 8✓, 29.6초 | 8✓, 30.6초 | 양쪽 통과·비재현 |
| `tests/gateway/test_session_mutation_compress.py` | 120초 SIGKILL | 3✓, 58.0초 | 3✓, 58.8초 | 양쪽 통과·비재현 |
| `tests/gateway/test_session_mutation_compress_managed.py` | 120초 SIGKILL | 2✓, 82.4초 | 2✓, 88.4초 | 양쪽 통과·비재현 |
| `tests/gateway/test_session_policy.py` | 120초 SIGKILL | 23✓, 52.0초 | 23✓, 58.3초 | 양쪽 통과·비재현 |
| `tests/gateway/test_surface_admission.py` | 120초 SIGKILL | 4✓, 14.6초 | 4✓, 21.6초 | 양쪽 통과·비재현 |
| `tests/gateway/test_unified_gateway_native_live.py` | 120초 SIGKILL | 3✓, 32.8초 | 3✓, 37.3초 | 양쪽 통과·비재현 |

## 비대칭과 교차반복

- run_progress의 PR 본 비교 실패 / baseline 본 비교 통과를 기존 플레이키로 단정하지 않고 baseline→PR→PR→baseline→baseline→PR로 동일 3파일(Buzz/WeCom/run_progress), -j2/300초 교차반복했다.
- crosscheck-4-baseline에서 정확히 같은 run_progress nodeid의 retryable_edit_failures=0 (기대 1) 실패가 실제 재현되어 간헐적 기준 재현으로 분류했다. PR 변경이 빈도에 미치는 영향까지 배제한 것은 아니다.
- Buzz와 초기 실패한 WeCom 정확한 nodeid는 본 비교와 교차반복 양쪽 모두 통과해 **비재현**이다. 과거 baseline의 다른 WeCom nodeid 실패를 초기 nodeid의 기준 재현 근거로 쓰지 않았다.

- 1 baseline: `=== Summary: 3 files, 93 tests passed, 0 failed (100% complete) in 38.6s (2 workers) ===` — `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/crosscheck-1-baseline.log`
- 2 pr: `=== Summary: 3 files, 93 tests passed, 0 failed (100% complete) in 39.5s (2 workers) ===` — `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/crosscheck-2-pr.log`
- 3 pr: `=== Summary: 3 files, 93 tests passed, 0 failed (100% complete) in 38.2s (2 workers) ===` — `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/crosscheck-3-pr.log`
- 4 baseline: `=== Summary: 3 files, 92 tests passed, 1 failed (100% complete) in 38.9s (2 workers) ===` — `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/crosscheck-4-baseline.log`
- 5 baseline: `=== Summary: 3 files, 93 tests passed, 0 failed (100% complete) in 38.3s (2 workers) ===` — `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/crosscheck-5-baseline.log`
- 6 pr: `=== Summary: 3 files, 93 tests passed, 0 failed (100% complete) in 37.8s (2 workers) ===` — `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/crosscheck-6-pr.log`

## 동일환경 및 재실행

- Python: `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/hermes-channel-policy/.venv/full-gateway/bin/python` (재사용만; 설치·변경 없음)
- HOME: `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/test-host-home` (양쪽 동일 격리 HOME; 운영홈 아님)
- PATH는 execution.json에 보존한 같은 값. canonical runner가 env -i, TZ=UTC/LANG=C.UTF-8/PYTHONHASHSEED=0로 정리. __HERMES_ACTIVATED 및 상속 PYTHONPATH 제거.
- canonical runner 2개와 대상 28개 테스트 파일은 두 snapshot에서 byte-identical. 초기 duration cache는 양쪽 없음 → 각 파일 실제 cap 300초.
- 원본 로그·과거 recheck/baseline 비교는 initial-evidence.json / historical-evidence.json으로 별도 보존. acp 누락 initial 5테스트는 full-gateway 의존성 환경에서 양쪽 통과했으며 기준재현으로 오표기하지 않았다.

```bash
/Users/kosungmin/.hermes/profiles/developer/cache/scratch/hermes-channel-policy/.venv/full-gateway/bin/python /Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/run_comparison.py
/Users/kosungmin/.hermes/profiles/developer/cache/scratch/hermes-channel-policy/.venv/full-gateway/bin/python /Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/run_crosschecks.py
```

정확한 파일 목록·env·cwd·명령 인자는 inventory.json / execution.json / crosschecks.json에 있다. 각 실행은 다음 형태다:

```bash
cd /Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/pr  # baseline 비교는 /baseline
env -u __HERMES_ACTIVATED \
  HOME=/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/test-host-home \
  HERMES_PYTHON=/Users/kosungmin/.hermes/profiles/developer/cache/scratch/hermes-channel-policy/.venv/full-gateway/bin/python \
  HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 --file-timeout 300 <inventory.json의 28파일>
```

## 제한

- 전체 1627파일 재실행이 아니라 초기 비녹색 28파일의 독립 비교이다.
- 원본 초기 -j12/120초와 비교 -j2/300초는 다르며 초기 부하 조건 원인 확정은 아니다.
- 비재현은 해결/무관/기준 재현을 뜻하지 않는다.
- run_progress는 같은 assertion 기준 재현이지만 정확한 timing root cause는 미확정이다.
- 최종 소스 수정 반영 snapshot 재확인은 부모 담당; 본 결과는 4456150535만 검증한다.
- 소스·테스트·의존성 수정, commit/push, 운영 설정/홈/재시작/인증 조회를 수행하지 않았다.


## 후속 코드 수정과 직접 검증

- 보조 프로필의 채널 정책은 수신 bot의 기본 설정이 아닌 canonical identity의 `runtime_profile` 소유 설정에서 읽습니다. 주 프로필 미지정/보조 지정, 상충 설정, 보조 미지정, 다른 프로필 비영향, 미서비스 프로필 거절을 실제 resolver 경로에서 검증했습니다.
- LOCAL 관리형 워커는 명시적 닫힌 채널 정책(`[]` 포함)과 조합되면 `unsupported_managed_channel_policy`로 spawn 전에 거절하고 bootstrap에서도 재검증합니다. 기존 worker 프로토콜을 확장한 것이 아닙니다.
- 실제 daemon/자식 worker 통합 재현에서 기존 HEAD는 제한 채널이 추가 worker와 `api_calls=1`로 완료됐습니다. 수정본은 추가 worker·모델 요청 없이 실패 처리됩니다. 신규 2파일을 기존 HEAD에 이식한 RED는 `4 passed, 10 failed`였습니다.
- 부모가 고정 후보 tree `75ca2882383b1daa3cbc09abd0a6af083bf62cce`를 git archive로 추출해 직접 집중 검증: **38파일, 388 passed, 0 failed, 8 skipped**. Linux 전용 테스트는 macOS에서 skip됐습니다.
- 저장소 `scripts/check --staged`: **12 checks, ok**. 독립 사전 검토에서도 신규 14개·추가 관련 70개·identity 7개가 통과했으며 신규 차단 문제를 발견하지 못했습니다. 이는 정식 리뷰 승인이나 운영 승인과 다릅니다.
- 집중 실행 명령 전체: `channel-revision-parent-execution.json`; 실제 로그: `channel-revision-parent-focused.log` (developer scratch).
- 전체 두 묶음을 한 foreground 도구 호출로 실행하던 최초 시도는 도구의 420초 대기 제한으로 잔여 묶음 도중 중단됐습니다. 부분 로그 `channel-revision-parent-residual.log`를 통과 증거로 사용하지 않고, 같은 고정 tree/환경에서 별도 프로세스로 재실행했습니다.

## 수정 후보의 초기 잔여 28파일 재확인

고정 후보 tree `75ca2882383b1daa3cbc09abd0a6af083bf62cce`의 **동일 소스**에서 같은 interpreter/HOME/PATH, `-j 2 --file-timeout 300`, retries=0으로 초기 잔여 28파일을 별도 프로세스로 끝까지 재실행했습니다.

- **28파일, 407 passed / 3 failed / timeout 0**, 367.2초, exit 1.
- 남은 실패는 Docker namespace 1개와 client PATH 2개로, 위 baseline에서 동일 nodeid·assertion이 재현된 3개뿐입니다.
- admission timing과 run_progress는 이번 후보에서 통과했습니다. 이 한 번의 통과를 기존 비결정성의 해결 증거로 바꾸지 않습니다.
- 초기 timeout 14파일은 후보에서도 모두 완주했습니다. initial 부하 조건의 원인까지 확정한 것은 아닙니다.
- 실제 로그: `channel-revision-parent-residual-complete.log`; 전체 명령·고정 tree·환경: `channel-revision-parent-execution.json`.
- 후보 검증 후 추가한 것은 문서와 명시적 fixture 자격증명의 `openai-codex/gpt-6.1-sol` resolver identity 검증 테스트 1개뿐입니다. 실행 소스는 동일합니다. 해당 테스트와 신규 통합 2파일을 직접 재실행하여 **15 passed**를 확인했습니다. 이는 실제 OAuth 인증·실서비스 요청의 대체 증거가 아닙니다.

## 수정 후보 잔여 파일의 정확한 재실행 명령

고정 후보 디렉터리에서 실행한 명령입니다. PATH는 로컬 `channel-revision-parent-execution.json`의 environment.PATH와 동일하게 설정합니다.

```bash
cd /Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-revision-final-75ca2882383b
env -u __HERMES_ACTIVATED \
  HOME=/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/test-host-home \
  HERMES_PYTHON=/Users/kosungmin/.hermes/profiles/developer/cache/scratch/hermes-channel-policy/.venv/full-gateway/bin/python \
  HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh -j 2 --file-timeout 300 \
  tests/gateway/test_api_admission_off_loop.py \
  tests/gateway/test_buzz_websocket.py \
  tests/gateway/test_child_notice_profile_admission.py \
  tests/gateway/test_docker_session_workspace.py \
  tests/gateway/test_local_automation.py \
  tests/gateway/test_managed_worker_images.py \
  tests/gateway/test_managed_worker_lineage.py \
  tests/gateway/test_model_confirmation_clients.py \
  tests/gateway/test_msgraph_authority.py \
  tests/gateway/test_native_ingress_trust.py \
  tests/gateway/test_native_role_reauthorization.py \
  tests/gateway/test_native_startup_recovery.py \
  tests/gateway/test_owner_loop_writes_create_api.py \
  tests/gateway/test_parked_profile_ingress.py \
  tests/gateway/test_run_progress_topics.py \
  tests/gateway/test_session_client_env.py \
  tests/gateway/test_session_edit_policy.py \
  tests/gateway/test_session_hosted_daemon.py \
  tests/gateway/test_session_kanban.py \
  tests/gateway/test_session_listener_lazy_dashboard.py \
  tests/gateway/test_session_mutation_compress.py \
  tests/gateway/test_session_mutation_compress_managed.py \
  tests/gateway/test_session_policy.py \
  tests/gateway/test_session_policy_refusal_fields.py \
  tests/gateway/test_static_token_session_mutations.py \
  tests/gateway/test_stream_consumer_wecom_native.py \
  tests/gateway/test_surface_admission.py \
  tests/gateway/test_unified_gateway_native_live.py
```

## 별도 알려진 제한과 운영 판정

- 실제 resolver의 API-key `openai` 경로가 `custom`으로 표시되어 닫힌 정책의 경로 매칭에서 거절되는 별도 가용성 문제가 기존 HEAD에서도 재현됐습니다. 이번 보조 프로필/관리형 워커 수정으로 유발된 문제는 아니고, 타 모델로 나가는 정책 우회도 아닙니다. 진단 로그: `channel-revision-openai-existing-diagnostic.log`. 일반 `openai` 공급자 지원 전체를 검증·수정했다고 주장하지 않습니다. 지정된 `openai-codex/gpt-6.1-sol`의 실제 계정 경로는 운영 적용 전 검증해야 합니다.
- 전체 1627파일을 최종 수정본에서 새로 실행한 것은 아닙니다. 초기 비녹색 전수 비교와 영향 범위 집중 회귀를 구분합니다.
- raw JSON/로그의 절대경로는 같은 개발 호스트의 developer scratch이며, 비밀정보나 운영 인증을 조회하지 않았습니다. `/Users/kosungmin/.hermes/profiles/developer/cache/scratch/channel-policy-independent/`에 인벤토리·비교·교차반복·hash 검증을 보존했습니다.
- 운영 파일 수정·병합·배포·게이트웨이 재시작·실제 Slack E2E를 수행하지 않았습니다. 운영 적용 보류 해제 여부는 리뷰어/총괄이 재검토합니다.
