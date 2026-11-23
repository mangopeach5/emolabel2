# EmoLabel 2.0

**한국어 영상의 음성 구간을 자동으로 찾고, 감정 라벨링용 클립을 제안하는 로컬 연구 도구**

EmoLabel 2.0은 영상에서 대화 구간을 감지하고, 장면 전환과 대사 경계를 확인해 클립을 추출한 뒤, AI가 감정을 **사전 예측(pre-label)** 하는 파이프라인입니다. **최종 정답 라벨(gold label)은 사람이 검수·승인한 경우에만 저장**합니다.

- 저장소: <https://github.com/mangopeach5/emolabel2> (비공개 — 접근 권한 필요)
- 개발·검증 환경: **Ubuntu / WSL Ubuntu, Python 3.12**
- 구현 방식: 로컬 영상 처리 + 로컬 AI 모델. 일반적인 분석에는 유료 API 키가 필요하지 않습니다.
- 현재 단계: **작동하는 CLI 기반 MVP**. 팀원용 GUI는 제공되지 않습니다.

> [!IMPORTANT]
> **YouTube 링크만 입력하면 영상을 자동 다운로드하는 도구가 아닙니다.** URL·메타데이터 등록은 지원하지만, 영상 파일은 권한을 확인한 뒤 별도로 준비해야 합니다. 원본 영상, 추출 클립, DB, 모델은 GitHub에 포함하지 않습니다.

## 1. 주요 기능

| 기능 | 설명 | 상태 |
| --- | --- | --- |
| 영상 등록·해시 중복 검출 | 로컬 MP4 등록, SHA-256으로 동일 파일 식별 | 구현됨 |
| Silero VAD | 사람이 말하는 음성 구간 자동 탐지 | 구현됨 |
| 짧은 발화 보정 | 3초 미만 발화도 제한적인 앞뒤 패딩으로 3~12초 클립 후보 생성 가능 | 구현됨 |
| PySceneDetect | 컷·페이드 등을 감지해 장면 경계 고려 | 구현됨 |
| Whisper STT | 한국어 대사 텍스트 및 단어 타임스탬프 추출 | 구현됨 |
| Smart Clip Selector | 음성·자막·장면 정보를 결합해 후보 선정/거절/보류 | 구현됨 |
| Ollama + Qwen | `neutral` / `angry` / `sad` / `happy` 감정 사전 예측 | 구현됨 |
| 사람 검수·내보내기 | 명시적으로 `accepted` 처리한 클립만 `final_dataset`에 export | 구현됨 |
| Batch Pipeline | 여러 MP4 자동 등록·분석, SHA 중복 제거, 실패 후 재시작 | 구현됨 |
| YouTube URL Intake | Shorts/watch/youtu.be URL 정규화, 중복 제거, 공개 oEmbed 메타데이터 조회 | 구현됨 |
| YouTube 파일 연결 | 권한 승인 manifest로 로컬 MP4와 YouTube ID 연결 후 배치 실행 | 구현됨 |
| 실행·평가 통계 | 단계별 소요시간, RTF, 데이터 유효성, 혼동행렬, macro-F1 | 구현됨 |
| YouTube MP4 자동 다운로드 | URL로부터 파일 자체를 자동 확보 | **미구현** |
| 수백 개 영상의 실제 성능 검증 | 다양한 신규 영상으로 정확도·생산성 일반화 | **추가 검증 필요** |

### 처리 흐름

```text
권한이 확인된 MP4
   ↓
영상 등록 / SHA-256 중복 검사
   ↓
Silero VAD ── 장면 전환 검사(PySceneDetect)
   ↓
Whisper 한국어 STT
   ↓
Smart Clip Selector → 후보 MP4 생성
   ↓
Ollama / Qwen 감정 사전 예측
   ↓
사람의 클립 확인 및 감정 라벨 지정
   ↓
accepted 클립만 data/final_dataset/ 저장
   ↓
실행 통계 및 AI 예측 ↔ 사람 라벨 평가
```

## 2. 팀원 시작하기

### 2-1. 저장소 내려받기

먼저 저장소 관리자로부터 **비공개 GitHub 저장소 접근 권한**을 받아야 합니다.

```bash
git clone https://github.com/mangopeach5/emolabel2.git
cd emolabel2
```

WSL에서 작업하는 경우 프로젝트를 Ubuntu 홈 폴더에 두는 것을 권장합니다. Windows 파일 탐색기에서는 `\\wsl.localhost\Ubuntu\home\<사용자명>\emolabel2`로 접근할 수 있습니다.

### 2-2. 필수 프로그램 준비

- **Python 3.12**, `uv` (Python 환경 관리)
- **FFmpeg / ffprobe** (영상·오디오 처리)
- **Ollama** 및 `qwen2.5:1.5b` (감정 분류)
- **Whisper base 모델 파일** (한국어 STT)
- 프로젝트의 Python 의존성 (아래 설치 절차)

Ubuntu 기본 패키지:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg zstd curl git
```

처음 사용하는 PC라면 먼저 `uv`를 설치합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

Python 환경 생성 (이미 `.venv-vad`가 준비되어 있으면 건너뛰기):

```bash
uv python install 3.12
uv venv --python 3.12 .venv-vad
source .venv-vad/bin/activate
uv pip install --python .venv-vad/bin/python \
  -r requirements.txt \
  -r requirements-vad.txt \
  -r requirements-scene.txt
```

Whisper 의존성은 프로젝트에서 제공하는 설치 스크립트를 사용합니다.

```bash
bash scripts/install_whisper_dependencies.sh       # 설치 계획 확인
INSTALL_WHISPER=1 bash scripts/install_whisper_dependencies.sh
python scripts/download_whisper_model.py base
```

위 명령으로 `models/whisper/base.pt`가 준비됩니다. Whisper 모델은 공식 OpenAI 다운로드 출처를 사용하며, Hugging Face 로그인을 요구하지 않습니다. 다운로드와 초기 설치에는 인터넷이 필요합니다.

Ollama는 <https://ollama.com/download/linux>의 공식 안내에 따라 설치하고 모델을 준비합니다.

```bash
ollama pull qwen2.5:1.5b
ollama list
```

Ollama 서버에 연결되지 않으면 `ollama serve`로 실행 상태를 확인합니다. WSL에서 Ollama를 설치했다면 **WSL에서 서버에 접근할 수 있어야** 합니다.

마지막으로 테스트:

```bash
export PYTHONPATH="$PWD/src"
bash scripts/run_tests.sh
```

> 개발 환경에서 **2026-11-23 기준 131개 테스트가 통과**했습니다. 다른 PC에서 동일하게 통과하는지는 설치 환경과 의존성 버전을 확인해야 합니다.

## 3. 가장 쉬운 사용법: 영상 1개 분석

예시는 **사용 권한을 확보한** `sample.mp4` 파일을 기준으로 합니다. 본인의 파일을 `data/inbox/sample.mp4`에 넣으세요.

### 3-1. DB 초기화 및 등록

```bash
cd ~/emolabel2                        # 실제 프로젝트 위치에 맞게 변경
source .venv-vad/bin/activate
export PYTHONPATH="$PWD/src"

python -m emolabel2.cli init-db

python -m emolabel2.cli register \
  --video-id sample-001 \
  --path data/inbox/sample.mp4 \
  --source-name local-authorized \
  --rights-basis "제작자에게 연구 이용 허가 받음" \
  --allowed-use "한국어 감정 라벨링 연구"
```

`--rights-basis`는 예시 문구입니다. **실제로 확인한 사용 권한 근거**로 바꿔야 합니다. 같은 ID로 이미 등록했다면 새로운 영상 ID를 사용하세요.

### 3-2. 자동 분석

```bash
python -m emolabel2.cli analyze \
  --video-id sample-001 \
  --run-id sample-001-emotion-v1 \
  --whisper-model base \
  --whisper-model-path models/whisper/base.pt \
  --device cpu \
  --scene-policy strict \
  --ollama-model qwen2.5:1.5b
```

- `--run-id`는 **새 분석마다 고유하게** 지정합니다.
- `--skip-emotion`을 붙이면 Qwen 감정 예측 없이 음성·STT·클립 선정만 검증할 수 있습니다.
- `--device cpu`는 CPU에서 Whisper를 실행합니다. CUDA 사용은 해당 PC의 GPU/드라이버 환경에 따라 별도 검증이 필요합니다.

### 3-3. 자동 생성 후보 확인

```bash
python -m emolabel2.cli candidates --run-id sample-001-emotion-v1
```

클립 MP4는 `data/candidates/` 폴더에서 직접 재생해 확인합니다. 응답에는 후보 ID, 시간 구간, 자막, 선정 상태와 감정 예측 등이 표시될 수 있습니다.

### 3-4. 사람이 검수하고 최종 내보내기

후보의 실제 감정을 직접 판단한 뒤 승인합니다.

```bash
python -m emolabel2.cli review \
  --candidate-id <candidates 명령에서 확인한 ID> \
  --run-id sample-001-emotion-v1 \
  --status accepted \
  --emotion sad
```

- 가능한 감정: `neutral`, `angry`, `sad`, `happy`
- 검수 상태: `accepted`, `rejected`, `deferred`
- 검수 시간을 **실제로 측정했다면** `--human-time 8.5` 등으로 추가 가능
- `accepted`이고 필요한 검증을 통과한 결과만 `data/final_dataset/`으로 내보냅니다.
- **Qwen의 자동 예측은 사람의 정답 라벨을 대체하지 않습니다.**

기존 결과가 이미 승인된 경우, 예제 명령을 그대로 재실행해 중복 검수를 만들지 마세요.

## 4. 영상 여러 개 자동 분석 (Batch)

사용 권한이 확인된 서로 다른 MP4를 `data/inbox/batch/` 폴더에 넣습니다.

```bash
mkdir -p data/inbox/batch data/logs

python -m emolabel2.cli batch \
  --batch-id team-batch-001 \
  --rights-basis "실제 확인한 권한 근거" \
  --allowed-use "한국어 감정 라벨링 연구" \
  --whisper-model base \
  --whisper-model-path models/whisper/base.pt \
  --ollama-model qwen2.5:1.5b \
  --json-output data/logs/team-batch-001.json \
  --csv-output data/logs/team-batch-001.csv
```

배치 진행 상태:

```bash
python -m emolabel2.cli batch-status --batch-id team-batch-001
```

- 완료된 동일 파일은 같은 배치를 다시 실행할 때 불필요하게 재분석하지 않습니다.
- 개별 파일이 실패해도 다른 파일을 계속 처리할 수 있습니다.
- 재실행 시 **같은 `batch-id`와 설정**을 유지합니다. 이미 생성된 JSON/CSV 파일은 덮어쓰지 않으므로 재실행 때 출력 옵션을 빼거나 새 파일명을 사용합니다.
- **배치 자동 분석만으로 최종 정답 데이터가 생성되는 것은 아닙니다.** 후보 검수가 필요합니다.

## 5. YouTube Shorts URL 기능 (영상 다운로드와 별개)

### 5-1. URL 목록 등록

`data/inbox/youtube_urls.txt` 파일을 만듭니다.

```text
https://www.youtube.com/shorts/nayRZA-Iu-o
https://www.youtube.com/watch?v=nayRZA-Iu-o
```

동일 영상 ID로 정규화·중복 제거됩니다.

```bash
python -m emolabel2.cli youtube-intake \
  --input data/inbox/youtube_urls.txt \
  --fetch-metadata \
  --json-output data/logs/youtube-intake-team-001.json \
  --csv-output data/logs/youtube-intake-team-001.csv
```

`--fetch-metadata`는 공개 YouTube oEmbed를 통해 제목·채널 등의 조회를 **시도**합니다. 조회 실패 시에도 URL 등록을 유지할 수 있습니다. 네트워크 조회가 필요 없으면 이 옵션을 생략합니다.

이 기능을 실행해도 **동영상 MP4는 내려받지 않습니다**. 기본 권한 상태는 `unknown`, 파일 상태는 `missing`입니다.

### 5-2. 권한이 확인된 로컬 MP4 연결

`data/inbox/youtube_file_links.csv` 파일 형식:

```csv
youtube_video_id,local_path,rights_status,rights_basis,allowed_use
nayRZA-Iu-o,data/inbox/approved/video-01.mp4,approved,실제 연구 사용 허가 근거,한국어 감정 라벨링 연구
```

위 경로에는 **해당 YouTube 영상과 일치하며 이용이 허가된 MP4**가 있어야 합니다. 단순히 같은 이름의 파일을 연결하면 안 됩니다.

```bash
python -m emolabel2.cli youtube-link \
  --manifest data/inbox/youtube_file_links.csv \
  --batch-id youtube-batch-001 \
  --whisper-model base \
  --whisper-model-path models/whisper/base.pt \
  --ollama-model qwen2.5:1.5b
```

> [!WARNING]
> 현재 `youtube-link --batch-id`는 `data/inbox/batch/` **폴더 전체 MP4**를 배치 입력으로 사용할 수 있습니다. 다른 실험의 파일이 섞여 있지 않은지 먼저 확인하세요. 기존 파일을 임의 삭제하지 말고 실험별 작업 폴더를 관리하세요.

URL만 등록하면 사용 권한이 자동으로 발생하는 것은 아닙니다. 권한 미확인 영상은 `approved`로 표시하지 마세요.

## 6. 통계 및 성능 평가

```bash
python -m emolabel2.cli stats \
  --json-output data/logs/team-stats-001.json \
  --csv-output data/logs/team-stats-001.csv
```

주요 지표:

- **processing_s / RTF**: 처리 소요시간과 영상 길이 대비 처리 비율
- **stage events**: VAD, 장면 검출, Whisper, Qwen 등 단계별 소요시간
- **candidate_links**: 해당 실행에서 *연결한* 후보 수. 신규 추출 클립 수와 동일하다고 가정하면 안 됨
- **current_linked_state**: 현재 검수 상태. 해당 실행 당시 검수 결과와 별도
- **human_review_s / manual_interventions**: 실제 기록이 없으면 `null`(미측정). `0`으로 해석 금지
- **confusion_matrix / macro_f1**: 사람의 정답 라벨과 모델 예측 비교
- **comparisons**: 실제로 수동·자동 양쪽을 측정했을 때만 비교 생성

현재 실측 데모(2026-11-23): **11.4초 영상 1개**, 전체 처리 약 **8.96초**, 자동 선정 클립 **1개**, 사람 정답 `sad`, Qwen 예측 `neutral`. 이 사례에서는 예측이 불일치했습니다. **영상 1개 결과를 일반적인 정확도나 생산성으로 해석해서는 안 됩니다.**

## 7. 폴더 구조

```text
emolabel2/
├── src/emolabel2/          # 파이프라인 · CLI · DB · 모델 연동 코드
├── tests/                  # 자동 테스트
├── scripts/                # 설치·검증 보조 스크립트
├── docs/                   # 구현 기록, 모델/라이선스 참고 자료
├── requirements*.txt       # Python 의존성
├── README_START_HERE.md    # 개발용 초기 안내
├── PROJECT_CONTEXT.md     # 프로젝트 배경과 제약
├── DECISIONS.md           # 설계 결정
├── EVALUATION_PLAN.md     # 성능평가 계획
├── data/                   # 로컬 영상·DB·보고서 (Git 업로드 제외)
│   ├── inbox/             # 입력 영상, URL 목록, 연결 manifest
│   │   └── batch/         # 일괄 분석할 MP4
│   ├── candidates/        # 자동 추출 후보 클립
│   ├── final_dataset/     # 사람이 승인한 최종 클립
│   └── logs/              # 실행 JSON/CSV 보고서
└── models/                 # 로컬 AI 모델 (Git 업로드 제외)
```

실제 폴더는 최초 실행 때 생성될 수 있습니다. `data/`, `models/`, `.venv-vad/`는 `.gitignore`로 제외됩니다.

## 8. 팀 협업 시 주의사항

1. **비공개 저장소 접근 권한**이 있는 팀원만 코드를 확인할 수 있습니다.
2. 영상·음성·감정 라벨·DB·개인정보 및 AI 모델 파일을 GitHub에 올리지 마세요.
3. 사용 권한이 확인된 영상만 입력하고, 권한 근거를 정확히 기록하세요.
4. 감정 예측은 **AI 추천값**, 최종 gold label은 **사람이 직접 검수한 값**으로 구분하세요.
5. 임의로 이전 실험 DB를 지우거나 후보 파일을 덮어쓰지 마세요.
6. 코드 변경 후 `bash scripts/run_tests.sh`를 실행하고 변경 사항을 검증하세요.
7. 문제 발생 시 **실행 명령, 오류 메시지, Python 버전, OS 환경**을 함께 공유하세요. 토큰이나 개인정보가 포함된 로그는 제거하세요.

## 9. 자주 생기는 문제

| 증상 | 확인 방법 |
| --- | --- |
| `ModuleNotFoundError: emolabel2` | `source .venv-vad/bin/activate` 및 `export PYTHONPATH="$PWD/src"` 확인 |
| `ffmpeg` / `ffprobe`를 찾을 수 없음 | `ffmpeg -version`, `ffprobe -version`, Ubuntu 패키지 설치 확인 |
| Whisper 모델 파일이 없음 | `ls -lh models/whisper/base.pt`, 공식 모델 다운로드 스크립트 실행 |
| Ollama 연결 실패 | `ollama list` 및 서버 구동 상태 확인 (`ollama serve`) |
| 분석 결과 후보가 0개 | VAD 발화 길이·패딩 거절 사유·STT 경계 검사 결과 확인. 정상적으로 0개일 수도 있음 |
| `final_dataset`이 비어 있음 | 정상: 후보를 사람이 `accepted` 처리하기 전에는 export하지 않음 |
| GitHub 저장소가 보이지 않음 | 저장소 관리자에게 GitHub Collaborator 초대 요청 |
| 생성한 보고서 파일명 충돌 | JSON/CSV 출력 경로를 새 파일명으로 바꾸거나 출력 옵션 생략 |

## 10. 개발 현황과 한계

- **검증한 범위**: 단일 실제 한국어 영상의 전체 흐름, 승인 후 export, 중복 입력으로 배치 재실행, YouTube URL 메타데이터 등록, Ubuntu 단위/통합 테스트 **131/131 통과**.
- **아직 부족한 검증**: 서로 다른 영상 다수, 강제 중단·재개 실험, 대량 데이터 처리 안정성, 감정별 정확도, 여러 사람의 검수 일치도, 수동 방식 대비 절감 효과.
- **미구현**: YouTube 영상 MP4 자동 다운로드 및 완전 자동 영상 확보. URL 메타데이터 기능과 실제 영상 파일 확보는 별개입니다.
- **품질상 주의**: Whisper가 대사를 잘못 인식하거나 Qwen이 감정을 틀릴 수 있습니다. 중요한 연구 데이터는 반드시 사람이 검수해야 합니다.

---

**팀원에게 권장하는 순서:** 저장소 클론 → 환경 설치 → 테스트 확인 → 승인된 샘플 영상 1개 분석 → 자동 생성 클립 직접 재생 → 사람 검수 → 통계 확인 → 배치 실험.
