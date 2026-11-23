# EmoLabel 2.0 — 새 로컬 프로젝트 스타터

이 폴더는 **완전히 새로 시작하는 독립 프로젝트**입니다. 이전 연구실 원본, GitHub 복사본, 기존 자동화 파일, 실제 영상 또는 라벨 데이터를 포함하지 않습니다.

## 목표
권한이 확보된 한국어 영상 → 음성·장면·대사 분석 → 3~12초 후보 클립 → 로컬 LLM 감정 **사전 예측** → 사람이 승인/수정/거절 → 승인 데이터만 저장 → 런타임·품질 평가.

이 ZIP은 **기획·Codex 맥락·테스트용 기초 코드**입니다. YouTube 수집, 실제 AI 감정 분류, 동영상 클립 추출이 이미 작동하는 완성품이 아닙니다.

## 설치 (Windows + WSL Ubuntu)
1. ZIP 파일을 Windows에서 압축 해제합니다.
2. 압축 해제된 `emolabel2` 폴더 전체를 Windows 파일 탐색기의 다음 주소로 복사합니다.
   `\\wsl.localhost\Ubuntu\home\mango\`
3. 최종적으로 `\\wsl.localhost\Ubuntu\home\mango\emolabel2` 경로가 되어야 합니다. 이전 폴더에 합치지 마세요.
4. 새 PowerShell을 열고 다음 명령을 차례로 실행합니다.

```powershell
wsl -d Ubuntu
```

```bash
cd ~/emolabel2
bash scripts/bootstrap.sh
bash scripts/run_tests.sh
python3 scripts/doctor.py
codex
```

`codex: command not found`이면 WSL의 Node.js/npm을 확인한 후 `npm install -g @openai/codex`로 설치할 수 있습니다. Codex 로그인은 ChatGPT 계정으로 진행할 수 있고, 별도 OpenAI API 키가 필수는 아닙니다.

5. Codex 새 대화에 **`CODEX_FIRST_MESSAGE.txt` 내용 전체**를 붙여넣습니다. 첫 대화는 **문서 검토만** 하게 설계되어 있습니다.
6. Codex가 제시한 계획을 승인한 뒤 `CODEX_PHASE1_MESSAGE.txt`를 보내 실제 개발을 시작하세요.

## 주요 파일
- `AGENTS.md` — Codex가 지켜야 하는 개발·보안·연구 원칙.
- `PROJECT_CONTEXT.md` — 우리 대화의 중요한 맥락과 이전 실패·회의 피드백.
- `REQUIREMENTS.md` — 무엇을 구현할지, 무엇이 성공인지.
- `ARCHITECTURE.md` — 새 시스템 구성/데이터 구조.
- `ROADMAP.md` — 단계별 작업.
- `EVALUATION_PLAN.md` — 테스트케이스, 정확도, 런타임, 자동화 효과.
- `OSS_REVIEW.md` — 검토한 오픈소스와 Hugging Face 관련 위험.
- `DECISIONS.md` — 최종 확정된 결정.
- `CODEX_FIRST_MESSAGE.txt` — 새 Codex 세션의 첫 프롬프트.
- `CODEX_PHASE1_MESSAGE.txt` — 첫 구현 프롬프트.
- `src/`, `tests/` — **모델 없이 돌아가는 작은 분석 코드와 테스트**.

## 원본 코드 안전 보관
이전 경로는 과거 대화에서 보고된 값이며 새 프로젝트는 확인하거나 변경하지 않습니다.
- `~/MAGO/emolabel-youtube/` — 연구실 원본.
- `~/EmoLabel_WebAuto_Companion/` — 예전에 작동한 자동화.
- `~/emolabel-team-repo/` — 예전 GitHub 관련 복사본.

**이전 폴더는 이동·복사·삭제·덮어쓰기 하지 마세요.** 새 작업은 `~/emolabel2` 안에서만 합니다. Codex에도 해당 제한을 넣었습니다.

## 현재 MVP 실행

모든 명령은 프로젝트 루트에서 기존 Python 3.12 환경을 활성화한 뒤 실행합니다.

```bash
cd ~/emolabel2
source .venv-vad/bin/activate
export PYTHONPATH=src
python -m emolabel2.cli init-db
```

권한 있는 영상 파일을 `data/inbox/`에 직접 배치한 후 등록합니다.

```bash
python -m emolabel2.cli register \
  --video-id demo-001 \
  --path data/inbox/demo.mp4 \
  --rights-basis "연구 사용 허가 확보" \
  --allowed-use "감정 라벨링 연구"
```

VAD, 장면 감지, 한국어 STT, 스마트 선택, 로컬 Ollama 사전 분류를 실행합니다.

```bash
python -m emolabel2.cli analyze \
  --video-id demo-001 \
  --whisper-model base \
  --whisper-model-path models/whisper/base.pt \
  --ollama-model qwen2.5:7b
```

후보를 확인하고 사람이 최종 검수합니다. AI 예측은 gold label이 아닙니다.

```bash
python -m emolabel2.cli candidates
python -m emolabel2.cli review --candidate-id CANDIDATE_ID \
  --run-id RUN_ID --status accepted --emotion happy \
  --review-started-at "2026-11-23T10:00:00+09:00" \
  --review-ended-at "2026-11-23T10:00:08.200000+09:00"
```

`accepted`와 사람이 정한 감정이 모두 있어야 `data/final_dataset/`으로 export됩니다.

수동 방식과 자동 방식을 비교하려면 동일한 `test-case-id`로 실제 측정한 수동 기준을
기록합니다. 측정하지 않은 값은 입력하지 않습니다.

```bash
python -m emolabel2.cli record-manual-baseline \
  --run-id manual-sample-001 \
  --video-id sample-001 \
  --test-case-id automatic:sample-001 \
  --processing-s 30.0 \
  --human-review-s 20.0 \
  --manual-interventions 4
```

## 승인 영상 배치 처리

권한을 확인한 MP4만 `data/inbox/batch/`에 배치합니다. 같은 `batch-id`를 다시 실행하면
완료 항목은 건너뛰고 실패·중단 항목만 재시도합니다.
완료 후 같은 경로의 파일 내용이 바뀌면 기존 SHA-256 결과를 재사용하지 않고 해당 항목을
`InputChangedError`로 기록합니다. 변경 파일은 새 `batch-id`로 실행해야 합니다.

```bash
python -m emolabel2.cli batch \
  --batch-id study-batch-001 \
  --rights-basis "연구 사용 허가 확보" \
  --allowed-use "한국어 감정 라벨링 연구" \
  --whisper-model base \
  --whisper-model-path models/whisper/base.pt \
  --ollama-model qwen2.5:1.5b \
  --json-output data/logs/study-batch-001.json \
  --csv-output data/logs/study-batch-001.csv
```

```bash
python -m emolabel2.cli batch-status --batch-id study-batch-001
```

배치 결과는 모두 검수 대기 후보이며 명시적인 사람 `accepted` 없이는 final dataset에
저장되지 않습니다. 설정을 바꾸어 새 실험을 할 때는 새 `batch-id`를 사용합니다.

## YouTube Shorts URL intake

`data/inbox/youtube_urls.txt`에 Shorts URL을 한 줄에 하나씩 적습니다. URL 등록만 할 때는
네트워크를 사용하지 않습니다. `--fetch-metadata`를 지정한 경우에만 API 키가 필요 없는
YouTube가 호스팅하는 공개 oEmbed endpoint에서 제목·채널 메타데이터를 best-effort로
시도합니다. 이는 API 키 기반 YouTube Data API를 대체하는 안정성 보장 API가 아닙니다.
실패해도 URL은 `unknown/missing` 상태로 남습니다.

```bash
python -m emolabel2.cli youtube-intake \
  --input data/inbox/youtube_urls.txt \
  --fetch-metadata \
  --json-output data/logs/youtube-intake-001.json \
  --csv-output data/logs/youtube-intake-001.csv

python -m emolabel2.cli youtube-intake \
  --retry-failed
```

파일 연결 manifest의 열은 `youtube_video_id,local_path,rights_status,rights_basis,allowed_use`
입니다. `rights_status=approved`이고 `data/inbox/` 아래에 이미 합법적으로 확보된 MP4만
연결할 수 있습니다. 이 기능은 YouTube 영상을 다운로드하지 않습니다.

```bash
python -m emolabel2.cli youtube-link \
  --manifest data/inbox/youtube_file_links.csv \
  --batch-id youtube-batch-001 \
  --whisper-model base \
  --whisper-model-path models/whisper/base.pt \
  --ollama-model qwen2.5:1.5b
```

## 중요 사실
- Hugging Face Hub·토큰·허브 모델 다운로드는 새 개발에서 사용하지 않습니다.
- YouTube Data API는 **영상 목록·메타데이터** 용도이지 MP4 다운로드 API가 아닙니다.
- 얼굴 검출은 실제 말하는 사람을 판별하는 것과 다릅니다.
- `annotation.json` 존재만으로 최종 승인 데이터라고 보지 않습니다. 명시적 `accepted` 상태가 필요합니다.
- AI가 예측한 감정은 사전 라벨이지 검증된 정답이 아닙니다.
- 실행 시간·채택률·정확도는 실험 전까지 수치로 단정하지 않습니다.
