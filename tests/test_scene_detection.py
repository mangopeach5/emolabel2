import subprocess
import tempfile
import unittest
from pathlib import Path

from emolabel2.media.probe import MediaProbeError, find_media_tool
from emolabel2.scene.detect import SceneConfig, SceneDependencyError, detect_scene_transitions


class SceneDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = find_media_tool("ffmpeg")
            import scenedetect  # noqa: F401
        except (MediaProbeError, ImportError) as exc:
            raise unittest.SkipTest(str(exc))
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.hard = root / "hard.mp4"
        cls.fade = root / "fade.mp4"
        cls.steady = root / "steady.mp4"
        commands = [
            [cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
             "color=c=red:s=160x120:r=10:d=2", "-f", "lavfi", "-i",
             "color=c=blue:s=160x120:r=10:d=2", "-filter_complex",
             "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "mpeg4", str(cls.hard)],
            [cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
             "color=c=white:s=160x120:r=10:d=4,fade=t=out:st=1:d=1",
             "-c:v", "mpeg4", str(cls.fade)],
            [cls.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
             "color=c=red:s=160x120:r=10:d=4", "-c:v", "mpeg4", str(cls.steady)],
        ]
        for command in commands:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
            if completed.returncode != 0:
                cls.temp.cleanup()
                raise unittest.SkipTest(completed.stderr[-500:])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def config(self):
        return SceneConfig(min_scene_len_frames=5, adaptive_threshold=2.0, fade_threshold=12.0)

    def test_hard_cut_is_detected_near_two_seconds(self):
        values = detect_scene_transitions(self.hard, self.config())
        hard = [item for item in values if item.transition_type == "hard_cut"]
        self.assertTrue(any(abs(item.time_ms - 2000) <= 200 for item in hard))

    def test_fade_is_recorded_separately(self):
        values = detect_scene_transitions(self.fade, self.config())
        fades = [item for item in values if item.transition_type == "fade"]
        self.assertTrue(fades)
        # White fades to black from 1.0s to 2.0s.  The FLOOR detector reports
        # the frame where brightness crosses the configured black threshold.
        self.assertTrue(any(1500 <= item.time_ms <= 2200 for item in fades))

    def test_steady_video_has_no_transition(self):
        self.assertEqual(detect_scene_transitions(self.steady, self.config()), [])


if __name__ == "__main__":
    unittest.main()
