# OpenAI Whisper 도입 기록

- PyPI: `openai-whisper==20250625`
- 공식 소스/모델 정의: https://github.com/openai/whisper
- 라이선스: 코드와 공개 모델 가중치 MIT
- 한국어: 다국어 `tiny`, `base`, `small`에서 `language="ko"`, `task="transcribe"`
- 모델 호스트: `https://openaipublic.azureedge.net/main/whisper/models/`
- Hugging Face 사용 안 함

공식 URL의 상위 경로에 포함된 SHA-256을 다운로드 후 다시 계산한다. 모델은
`models/whisper/`에만 저장하고 기존 파일은 덮어쓰지 않는다. 대략적인 다운로드 크기는
tiny 75 MiB, base 142 MiB, small 461 MiB이며 실제 바이트 수는 다운로드 응답과 로컬 파일로 기록한다.
