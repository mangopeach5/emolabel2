# Ollama/Qwen 로컬 사전 분류 기록

- 연결 대상: 로컬 루프백 `http://127.0.0.1:11434`만 사용
- 기본 모델 태그: `qwen2.5:7b`
- Ollama 배포 크기: 약 4.7 GB
- Qwen2.5 7B 라이선스: Apache 2.0
- 모델 교체: CLI의 `--ollama-model`로 Llama 등 다른 로컬 태그 사용 가능

Ollama 설치와 모델 pull은 자동 실행하지 않는다. 각 대체 모델은 사용 전에 해당
라이선스를 별도로 검토한다. 감정 출력은 사전 예측이며 gold label이 아니다.
