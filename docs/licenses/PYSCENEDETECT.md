# PySceneDetect 도입 기록

- 공식 프로젝트: https://github.com/Breakthrough/PySceneDetect
- 공식 배포: https://pypi.org/project/scenedetect-headless/
- 고정 버전: `scenedetect-headless==0.7.3`
- 라이선스: BSD 3-Clause
- Python: 3.10 이상; 프로젝트는 기존 `.venv-vad`의 Python 3.12 사용
- wheel: `scenedetect_headless-0.7.3-py3-none-any.whl`, 159,649 bytes
- wheel SHA-256: `8d49befc609cd57cd19c204ee98bb5808ad0275f405ea60da8b5ce6abfca995f`
- 직접 의존성: click, numpy, opencv-python-headless, platformdirs, tqdm

GUI가 필요 없으므로 headless OpenCV 변형만 사용한다. 다른 `opencv-python*` 변형과
동시에 설치하지 않는다. 모델 가중치와 Hugging Face 다운로드는 없다. 실제 설치 전에
uv dry-run 결과에서 PyTorch와 Silero VAD가 변경되지 않는지 확인한다.
