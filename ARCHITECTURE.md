# 신규 독립 아키텍처 (설계안, 아직 미구현)

```text
[사용권 있는 로컬 영상 / 선택적 YouTube 메타데이터]
                   ↓
      [입력 검증 + 중복 제거 + 작업 큐]
                   ↓
  [미디어 분석 (길이/코덱/음질/처리시간)]
                   ↓
       ┌─ VAD (발화 구간)
       ├─ 한국어 STT (대사/단어 시간)
       ├─ PySceneDetect (장면 컷)
       └─ 품질/화자 판단 (검증된 것만)
                   ↓
     [Smart Clip Selector 3~12초]
                   ↓
       [후보 파일 + 선정 근거]
                   ↓
       [로컬 LLM 감정 사전 예측]
                   ↓
   [사람의 승인/수정/거절/판단보류]
                   ↓
      [승인된 최종 데이터셋만]
                   ↓
     [검증/런타임/모델성능 대시보드]
```

## 이후 구현할 폴더 (현재는 설계만)
- `src/emolabel2/intake/`: 로컬 자료 등록 및 향후 API 메타데이터 어댑터.
- `src/emolabel2/analysis/`: 영상/오디오 분석, VAD, STT, 장면 컷.
- `src/emolabel2/selection/`: 시간 경계/품질 점수/설명 가능한 거절 규칙.
- `src/emolabel2/emotion/`: Ollama 연동/프롬프트/JSON 결과 검증.
- `src/emolabel2/review/`: 사람이 확정한 검수 이력 및 최종 데이터 추출.
- `src/emolabel2/analytics/`: 단계별 이벤트·실험 결과 비교.
- 현재 존재하는 `src/emolabel2/metrics.py`는 **표준 라이브러리 기반의 작은 계산 예제**일 뿐, 최종 엔진이 아님.

## 권장 데이터 구조
```text
data/
  inbox/         승인된 입력 영상만
  work/          임시 계산 산출물 (원본 보호)
  candidates/    AI 후보 (미승인 포함)
  final_dataset/ 사람이 명시적으로 승인한 데이터만
  logs/          처리시간 및 실패 원인
  manifests/     원본/클립 provenance CSV
  test_cases/    사용 권한 확인된 테스트 영상
```

## 필수 레코드
Video: video_id, file_hash, rights_status, source, duration, added_at.
Candidate: candidate_id, video_id, start_ms, end_ms, transcript, quality, reject_reasons, algorithm_version.
Prediction: candidate_id, emotion_or_null, model_name, prompt_version, elapsed_sec, needs_review.
Review: candidate_id, review_status, accepted_gold_emotion_or_null, human_time_sec, reject_reason, reviewed_at.
Telemetry: run_id, stage, elapsed_sec, video_duration_sec, success, hardware, error_message.

`annotation.json` 파일의 존재만으로 `review_status=accepted`로 추론하면 안 됨.
