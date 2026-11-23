import unittest

from emolabel2.scene.policy import (
    ScenePolicyMode,
    SceneTransition,
    evaluate_scene_policy,
)
from emolabel2.vad.segments import CandidateProposal, SpeechSegment


class ScenePolicyTests(unittest.TestCase):
    def test_no_transition_preserves_candidate(self):
        result = evaluate_scene_policy(
            CandidateProposal(0, 6000, True), [SpeechSegment(0, 6000)], [],
            mode=ScenePolicyMode.STRICT,
        )
        self.assertEqual((result[0].status, result[0].start_ms, result[0].end_ms), ("accepted", 0, 6000))

    def test_strict_rejects_transition_inside_speech(self):
        transition = SceneTransition(4000, "hard_cut", "AdaptiveDetector")
        result = evaluate_scene_policy(
            CandidateProposal(0, 8000, True), [SpeechSegment(0, 8000)], [transition],
            mode=ScenePolicyMode.STRICT,
        )
        self.assertEqual(result[0].status, "rejected")
        self.assertEqual(result[0].reason, "scene_cut_inside_speech")

    def test_warn_mode_keeps_transition_inside_speech(self):
        transition = SceneTransition(4000, "fade", "ThresholdDetector")
        result = evaluate_scene_policy(
            CandidateProposal(0, 8000, True), [SpeechSegment(0, 8000)], [transition],
            mode=ScenePolicyMode.WARN,
        )
        self.assertEqual(result[0].status, "warning")
        self.assertEqual(result[0].reason, "scene_transition_inside_speech")

    def test_silence_transition_splits_without_joining_speech(self):
        transition = SceneTransition(4000, "hard_cut", "AdaptiveDetector")
        result = evaluate_scene_policy(
            CandidateProposal(0, 8000, True),
            [SpeechSegment(0, 3500), SpeechSegment(4500, 8000)], [transition],
            mode=ScenePolicyMode.STRICT,
        )
        self.assertEqual([(item.start_ms, item.end_ms, item.status) for item in result], [
            (0, 4000, "accepted"), (4000, 8000, "accepted")
        ])

    def test_too_short_adjusted_piece_is_rejected_with_original_preserved(self):
        transition = SceneTransition(2500, "hard_cut", "AdaptiveDetector")
        result = evaluate_scene_policy(
            CandidateProposal(0, 7000, True),
            [SpeechSegment(0, 2000), SpeechSegment(3000, 7000)], [transition],
            mode=ScenePolicyMode.STRICT,
        )
        self.assertEqual(result[0].reason, "scene_adjusted_too_short")
        self.assertEqual((result[0].start_ms, result[0].end_ms), (0, 2500))
        self.assertEqual(result[1].status, "accepted")


if __name__ == "__main__":
    unittest.main()
