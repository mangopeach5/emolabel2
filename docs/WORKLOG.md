# 작업 기록

| 날짜 | 작업 | 변경한 파일 | 테스트 결과 | 실제 런타임 | 남은 문제 |
|---|---|---|---|---|---|
| 2026-11-23 | 신규 독립 프로젝트 스타터 작성 | 문서/stdlib metrics/테스트 | 설치 후 로컬에서 확인 필요 | 미측정 | Codex 계획 검토, Phase 1 진행 |
| 2026-11-23 | Phase 1A 데이터 구조·경로·매니페스트·해시·SQLite 기반 구현 | domain.py, paths.py, intake/, storage.py, 합성 단위 테스트, 매니페스트 템플릿 | 사용자 Ubuntu 실행: 26개 전체 통과 | 0.005초 | Phase 1B 진행 |
| 2026-11-23 | Phase 1B 검수 이력·승인 export·불일치 추적 구현 | storage.py, paths.py, review/, Phase 1B 합성 단위 테스트 | 사용자 Ubuntu 실행: 39개 전체 통과 | 0.021초 | Phase 1C 진행 |
| 2026-11-23 | Phase 1C 런타임·자동화 지표·유효성·비교·보고서·CLI 구현 | storage.py, metrics.py, analytics/, cli.py, 합성 단위 테스트 | 사용자 Ubuntu 실행: 55개 전체 통과 | 0.038초 | Phase 2A 진행 |
| 2026-11-23 | Phase 2A FFprobe 분석·수동 구간 클립 추출·후보/런타임/검수 통합 구현 | storage.py, media/, 미디어 합성 통합 테스트 | 사용자 Ubuntu 실행: 63개 전체 통과, 건너뜀 없음 | 1.357초 | Phase 2B 진행 |
| 2026-11-23 | Phase 2B Silero VAD 어댑터·구간 병합/분할·후보 추출·근거 저장 구현 | storage.py, vad/, requirements-vad.txt, setup/smoke script, 합성 테스트 | Codex WSL 실행이 E_ACCESSDENIED로 차단되어 미실행; 정적 검토만 완료 | 미측정 | 사용자 Ubuntu에서 설치·전체 테스트·오프라인 smoke test 필요; 한국어 정확도 평가는 추후 수행 |
| 2026-11-23 | Phase 2B 실제 환경 검증 | 기존 Phase 2B 구현 | 사용자 Ubuntu 실행: 74개 전체 통과; Silero 무음 smoke test 통과 | 1.760초 | 실제 한국어 정확도 평가는 추후 수행 |
| 2026-11-23 | Phase 2C-1 PySceneDetect 장면 감지·VAD 후처리·판정 근거 저장 구현 | storage.py, scene/, vad/pipeline.py, 설치 스크립트, 합성 테스트 | Codex WSL 실행이 E_ACCESSDENIED로 차단되어 미실행 | 미측정 | 사용자 Ubuntu에서 dry-run 검토, 설치, 전체 테스트 필요 |
| 2026-11-23 | Phase 2C-1 실제 검증 | 기존 Phase 2C-1 구현 | 사용자 Ubuntu 실행: 86개 전체 통과 | 2.408초 | 완료 |
| 2026-11-23 | 통합 MVP: Whisper STT·스마트 선택·Ollama 사전 분류·CLI | storage.py, stt/, selector/, emotion/, pipeline.py, cli.py, 설치/모델 스크립트, 테스트, README | Codex WSL E_ACCESSDENIED로 미실행; 모의 테스트 작성 | 미측정 | Ubuntu 전체 테스트와 실제 모델 smoke/integration 검증 필요 |
| 2026-11-23 | 짧은 VAD 발화 패딩 후 STT Selector 연결 | vad/segments.py, vad/pipeline.py, storage.py, 관련 테스트 | MSYS Python 단위/스키마 테스트 통과; FFmpeg 통합 테스트는 해당 환경에 FFmpeg가 없어 건너뜀; Ubuntu 미실행 | 미측정 | Ubuntu에서 전체 102개 및 sample-001 재분석 필요 |
| 2026-11-23 | 첫 실제 end-to-end 검증 | sample-001 11.4초 → 3.2초 후보 → Whisper → Qwen → 사람 sad → export | 사용자 Ubuntu 실행: 102개 통과; 전체 흐름 성공 | 전체 8.957초, RTF 0.786, STT 4.778초, scene 1.773초, VAD 0.389초, Qwen 0.111초 | 평가 n=1, AI neutral/사람 sad, macro F1 0.0은 일반화 불가 |
| 2026-11-23 | 통계 attribution·미측정값·검수시간 개선 | storage.py, analytics/, review/, cli.py, 관련 테스트 | MSYS Python 핵심 회귀 테스트 통과; 전체 Ubuntu 미실행 | 미측정 | Ubuntu 전체 회귀 테스트 필요; 과거 미측정 시간은 null 유지 |
| 2026-11-23 | analytics v2 실제 검증 | 기존 analytics v2 구현 | 사용자 Ubuntu 실행: 112개 전체 통과 | 사용자 실측 유지 | 완료 |
| 2026-11-23 | 재시작 가능한 승인 MP4 Batch Pipeline | storage.py, batch/, cli.py, batch/마이그레이션 테스트, README | 사용자 Ubuntu 실행: 121개 전체 통과, 실제 batch 완료, 재실행 attempt_count=1 유지 | 사용자 실측 결과 유지 | 완료 |
| 2026-11-23 | YouTube URL intake·메타데이터·승인 MP4 연결 | schema v11, youtube/intake.py, CLI, 단위 테스트, README | 사용자 기준선 121/121 통과; 신규 테스트는 Codex WSL 실행 가능 여부 확인 전 | 네트워크 없는 모의 메타데이터 및 합성 MP4 | Ubuntu 전체 회귀와 선택적 oEmbed 실조회 필요 |

실측이 없는 값은 기입하지 않는다. 구현 때마다 이 문서를 갱신한다.
