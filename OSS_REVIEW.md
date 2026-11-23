# 오픈소스 도입 후보 (기존 설치나 동작 확정 아님)

| 후보 | 역할 | 도입 시 유의점 | 공식 출처 |
|---|---|---|---|
| FFmpeg/ffprobe | 영상/음성 변환 및 길이 분석 | 로컬 설치 확인 | https://ffmpeg.org/ |
| Silero VAD | 발화 구간 | 이전 원본엔 있었음. **새 모델의 Hugging Face 없는 확보 경로 확인** | https://github.com/snakers4/silero-vad |
| Whisper | 한국어 음성→텍스트 | 기존 faster-whisper 다운로드 경로에 HF 의존이 있었음; 공식 배포 등 대안 검토 | https://github.com/openai/whisper |
| PySceneDetect | 장면 전환 검출 | 로컬 Python 설치 및 성능 검증 | https://github.com/Breakthrough/PySceneDetect |
| AutoClip | LLM으로 장면/클립 선정 아이디어 | SNS 하이라이트 용도. 그대로 설치·재활용하지 말고 의존성/라이선스 조사 | https://github.com/artbyjazi/autoclip |
| ETRI KMSAV | 한국어 다화자·발화 얼굴 메타데이터, 평가 참고 | 기존 데이터의 crop 스크립트이며 새로운 영상의 완성형 active-speaker 모델이 아님. 데이터 제한 별도 확인 | https://github.com/etri/kmsav |
| CoreaSpeech | 한국어 음성 자동 정제/품질 선별 참고 | 전체 파이프라인에 HF/pyannote 의존 이력, 필요한 독립 로직만 검토 | https://github.com/CoreaSpeech/CoreaSpeech |
| Ollama | 로컬 언어 모델 서버 | 로컬만 바인딩, 모델 가중치 및 라이선스 확인 | https://ollama.com/download |
| Qwen / Llama | 한국어 대사 감정 사전 예측 | 한국 드라마 정확도 실측 비교. Qwen 우선 후보, Llama 비교 후보. 모델 크기/성능 미확정 | https://ollama.com/library/qwen3.5 / https://ollama.com/library/llama3.2 |
| pandas/scikit-learn | 통계·Macro F1 | Phase 3~4에서 필요 시 설치 | https://pandas.pydata.org/ / https://scikit-learn.org/ |
| Streamlit | 로컬 대시보드 | 백엔드가 먼저 통과한 뒤 도입 | https://streamlit.io/ |
| YouTube Data API v3 | 채널/영상 정보 | 다운로드 API 아님. API 할당량/약관 확인, 로컬 비밀 관리 | https://developers.google.com/youtube/v3 |

### 특별히 구분할 사항
- Hugging Face 로그인/토큰/Hub/숨은 모델 다운로드도 금지. 패키지 설치 전에 내부 의존성까지 조사.
- '코드 라이선스'와 '모델 가중치 라이선스', '데이터셋 이용 조건'은 다르다.
- Face Detection(얼굴 존재) ≠ Face Recognition(신원) ≠ Active Speaker Detection(실제 말하는 얼굴).
- 한국어 대사 LLM만으로 음성 억양/감정 정답을 보장할 수 없다. 동영상/음성 모델은 측정 결과가 유효할 때만 추가.
