# Silero VAD 도입 기록

- 공식 프로젝트: https://github.com/snakers4/silero-vad
- 배포 출처: https://pypi.org/project/silero-vad/
- 고정 버전: `silero-vad==6.2.3`
- 라이선스: MIT
- PyPI wheel 크기: 약 11.3 MB
- PyPI wheel SHA-256: `7b7f5436cfcb02fae583a05b512ea96467fd449fe54cb49a5e4f06c51a1e43b8`
- 기본 의존성: `packaging`, `torch>=1.12.0`
- 모델: wheel 내부 `silero_vad.data/silero_vad.jit`
- 로딩: `load_silero_vad()`; Hugging Face와 `torch.hub.load()` 사용 금지
- 오디오 입력: 프로젝트의 FFmpeg로 16 kHz mono PCM 생성; torchaudio/torchcodec 불필요

설치 후 `scripts/setup_vad_env.sh`가 실제 Torch 버전, 모델 크기, 모델 SHA-256을 출력한다.
그 값을 실험 기록에 보존한다. VAD는 음성 존재만 탐지하며 화자 수·신원·감정을 판정하지 않는다.

Python 3.14에서는 프로젝트 내부 `.venv-vad`를 먼저 시도한다. 해당 환경에서 PyTorch
wheel 해결 또는 실행이 실패하면 시스템 환경을 변경하지 않고 Python 3.12로 같은 이름의
가상환경을 새로 만드는 방식을 사용한다. 설치 스크립트는 PyPI 설치 계획을
`work/silero-install-plan.json`에 먼저 기록하고, 설치 후 wheel에 포함된 모델을 디지털
무음 1초로 오프라인 smoke test한다. 이 검사는 설치·모델 로딩의 정상 여부만 확인하며
한국어 음성 탐지 정확도나 배경음악 강건성을 입증하지 않는다.
