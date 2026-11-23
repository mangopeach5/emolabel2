# 확정된 프로젝트 결정 기록

- **2026-11-23: 완전히 새로운 로컬 폴더 `~/emolabel2`에서 시작. GitHub 사용 안 함.**
- 이전 원본, 이전 자동화, 기존 GitHub 클론과 모든 실제 라벨 데이터는 **읽거나 복사·수정하지 않음** (추후 사용자가 별도로 허용한 경우 제외).
- **Hugging Face 계정/토큰/Hub/Hub 다운로드 불사용**. 다른 저장소를 통해 모델을 사용할 때도 출처 및 설치 경로 확인.
- 개발 목표: 영상 수집/추출/한국어 감정 사전 라벨/사람 검수/승인 데이터 보관/성능 분석을 단계적으로 구축.
- 기본 4감정 `neutral`, `angry`, `sad`, `happy`; AI가 모르면 판단 보류, 연구 정답으로 기록하지 않음.
- 최종 보관은 **명시적 human accepted** 기준. `annotation.json` 단순 존재로 최종 승인 판단하지 않음.
- 자동화와 속도를 중시하지만 **품질 검증은 필수 합격 조건**.
- 처음에는 권한 있는 로컬 파일만 처리. YouTube Data API는 나중에 메타데이터 수집 어댑터로 사용.
- AutoClip, ETRI KMSAV, CoreaSpeech는 참고/검토 대상이지 즉시 통합할 것이 아님.
- 처음 Codex 대화는 읽기 전용 분석. 이후 사용자가 승인한 단계부터 수정.

이 기록보다 **새로운 사용자 지시가 항상 우선**한다.

## 2026-11-23 — Phase 1A
- Python 표준 라이브러리의 `sqlite3`를 Phase 1 내부 정본 저장소로 사용한다.
- 매니페스트의 영상 경로는 프로젝트의 `data/inbox/` 아래 상대경로만 허용한다.
- Phase 1 등록은 `rights_status=approved_research`이며 권리 근거와 허용 범위가 있는 입력만 허용한다.
- SHA-256을 파일 중복 식별자로 사용하고 데이터베이스에서도 고유성을 강제한다.

## 2026-11-23 — Phase 1B
- `reviews`는 현재 검수 상태, `review_events`는 덮어쓰지 않는 상태 변경 이력으로 분리한다.
- AI 예측은 `predictions`, 사람의 확정 감정은 `reviews.gold_emotion`에 별도로 저장한다.
- export는 명시적으로 `accepted`된 후보만 허용하며 `annotation.json`을 승인 근거로 사용하지 않는다.
- 후보와 복사본의 SHA-256이 일치해야 완료로 기록하고, 후보별 완료 export는 하나만 허용한다.
- 완료 후 검수 상태나 gold emotion이 바뀌면 파일을 자동 삭제·덮어쓰지 않고 `export_issues`에 불일치를 기록한다.

## 2026-11-23 — Phase 1C
- 전체 실행은 `runs`, 8개 단계 측정은 `stage_events`, 수동 개입은 `manual_interventions`에 기록한다.
- 전체 및 단계 경과시간은 단조 시계로 측정하고 시작·종료 시각은 timezone이 포함된 UTC로 저장한다.
- 유효 데이터는 final 폴더 존재가 아니라 완료 export, 현재 accepted, 동일 gold emotion, 미해결 issue 없음, 실제 파일 hash 일치를 모두 만족해야 한다.
- 수동/자동 비교는 동일한 `test_case_id`가 있는 경우에만 생성한다.
- 사람 gold가 없으면 분류 정확도 보고서는 `null`이며 임의 점수를 만들지 않는다.
- JSON은 구조화 전체 보고서, CSV는 실행별 요약으로 출력하며 기존 보고서를 덮어쓰지 않는다.

## 2026-11-23 — Phase 2A
- 로컬 `ffprobe` JSON 출력으로 영상 길이·해상도·프레임레이트·오디오·코덱과 파일 유효성을 확인한다.
- 수동 지정 구간은 3~12초만 허용하고 실제 영상 길이를 넘는 요청은 FFmpeg 실행 전에 거부한다.
- 정확한 구간을 위해 스트림 복사 대신 영상 `mpeg4`, 오디오 `aac`로 다시 인코딩하며 결과를 다시 ffprobe한다.
- 후보는 임시 파일 검증과 SHA-256 확인 후 `data/candidates/`에 배타적으로 확정하고 DB 등록과 run 연결을 한 트랜잭션으로 처리한다.
- 동일 영상의 동일 millisecond 구간은 SQLite 고유 인덱스로 중복 등록을 차단한다.
- 미디어 분석은 `preprocess`, 지정 구간 추출은 `clip_selection` 단계로 기록한다.

## 2026-11-23 — Phase 2B
- 공식 PyPI의 `silero-vad==6.2.3` MIT 배포본을 고정하고 wheel 내부 JIT 모델만 로딩한다.
- Hugging Face Hub와 `torch.hub.load()`는 사용하지 않으며 입력은 로컬 FFmpeg로 16 kHz mono PCM으로 변환한다.
- 원시 발화, 병합 발화, 3~12초 후보와 거절 사유를 별도 SQLite 행으로 보존한다.
- VAD는 음성 존재 구간 탐지일 뿐 화자 수·화자 신원·감정 판정 기능으로 간주하지 않는다.
- 합성 타임스탬프 테스트는 파이프라인의 병합·분할·저장을 검증하며 실제 한국어 탐지 정확도 평가를 대신하지 않는다.

## 2026-11-23 — Phase 2C-1
- `scenedetect-headless==0.7.3` BSD 3-Clause 배포본을 기존 Python 3.12 `.venv-vad`에서 사용한다.
- AdaptiveDetector 하드컷과 ThresholdDetector 페이드를 별도 행으로 기록한다.
- 기본 strict 정책은 발화 도중 전환을 거절하지만, warn 정책은 후보를 유지하고 사람 검수용 경고를 기록한다.
- 장면 경계를 없애기 위해 서로 떨어진 발화를 이어 붙이지 않는다. 무음 경계 분할 결과도 각각 3~12초와 음성 포함 조건을 통과해야 한다.
- VAD 제안 수, 장면 적용 후 생성 수, 장면 사유 거절 수와 이후 사람 검수 채택률을 분리하여 계산한다.

## 2026-11-23 — MVP STT·선택·사전 분류
- 공식 `openai-whisper==20250625`와 OpenAI 공식 Azure 모델 URL만 사용하며 모델은 `models/whisper/`에 checksum 검증 후 저장한다.
- 한국어는 `language=ko`, `task=transcribe`, word timestamps로 처리하고 저신뢰·무음·반복 결과는 보류한다.
- 문장부호만으로 완결성을 확정하지 않고 VAD/STT 시간 겹침과 양쪽 경계를 함께 사용한다.
- Ollama 호출은 127.0.0.1로 고정하고 Qwen/Llama 결과는 `predictions`에만 저장한다. 사람 gold는 기존 `reviews`에만 존재한다.
- 후보 파일이 먼저 생성되더라도 STT 선택에서 거절·보류된 항목은 final dataset으로 자동 export하지 않는다.
- 3초 미만 VAD 발화는 원본 `speech_too_short` 기록을 유지하되, 기본 최대 0.8초 패딩·최소 음성 비율 0.7·영상/인접 발화 경계를 통과하면 3.2초 임시 후보로 만들어 STT Selector가 최종 판정한다.
- `candidate_run_links`는 실행별 신규 추출 수가 아니라 참조 관계다. 현재 review 상태와 명시적으로 기록한 run 당시 outcome을 통계에서 분리한다.
- 기록되지 않은 검수시간과 수동 개입 횟수는 0이 아니라 `null/not_measured`로 보고한다.
- 영상당 시간 외삽 생산량은 관측 영상 수와 안정성 표지를 함께 제공하며, 수동/자동 비교는 양쪽 실측 검수시간이 있을 때만 생성한다.
- Batch Pipeline은 같은 batch_id의 완료 SHA-256 항목을 재실행하지 않고 실패·중단 항목만 새 attempt로 재시도한다. 동일 해시의 다른 경로는 `skipped_duplicate`로 보존한다.
- batch 설정이 달라지면 기존 결과 의미를 바꾸지 않도록 새 batch_id를 요구한다. 후보는 자동 승인하지 않는다.
- 완료된 batch 입력 경로의 SHA-256이 달라지거나 파일이 사라지면 완료 기록을 재사용하지 않고 해당 항목만 실패 처리한다. 다른 영상은 계속 처리하며 변경 입력은 새 batch_id로 실행한다.
- YouTube URL은 11자 video ID와 canonical watch URL로 정규화하며 권한 상태와 파일 확보 상태를 분리한다. API 키 없는 YouTube-hosted oEmbed는 안정성이 보장된 Data API가 아닌 선택적 best-effort 조회로만 사용하고 실패해도 URL을 유지한다.
- YouTube 미디어 우회 다운로드는 구현하지 않는다. 승인 manifest로 연결한 프로젝트 내부 MP4만 SHA-256 검증 후 batch inbox에 복사하며 동일 파일을 서로 다른 YouTube ID에 연결하지 않는다.
