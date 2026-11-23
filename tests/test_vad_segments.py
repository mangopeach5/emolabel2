import math
import unittest

from emolabel2.vad.segments import (
    ShortSpeechPaddingConfig, SpeechSegment, build_proposals,
    expand_short_speech, merge_segments,
)
from emolabel2.vad.silero import VadConfig


class VadSegmentTests(unittest.TestCase):
    def test_silence_produces_no_candidates(self):
        self.assertEqual(build_proposals(merge_segments([], max_gap_s=0.8)), [])

    def test_configurable_gap_merges_nearby_speech(self):
        values = [SpeechSegment(0, 2000), SpeechSegment(2500, 5000), SpeechSegment(7000, 9000)]
        self.assertEqual(merge_segments(values, max_gap_s=0.6), [
            SpeechSegment(0, 5000), SpeechSegment(7000, 9000)
        ])
        self.assertEqual(len(merge_segments(values, max_gap_s=2.0)), 1)

    def test_short_speech_is_rejected(self):
        proposal = build_proposals([SpeechSegment(0, 2999)])[0]
        self.assertFalse(proposal.accepted)
        self.assertEqual(proposal.reject_reason, "speech_too_short")

    def test_three_and_twelve_second_boundaries_are_accepted(self):
        proposals = build_proposals([SpeechSegment(0, 3000), SpeechSegment(5000, 17000)])
        self.assertTrue(all(item.accepted for item in proposals))

    def test_long_speech_is_split_and_short_remainder_rejected(self):
        proposals = build_proposals([SpeechSegment(0, 25000)])
        self.assertEqual([(p.end_ms - p.start_ms, p.accepted) for p in proposals], [
            (12000, True), (12000, True), (1000, False)
        ])
        self.assertEqual(proposals[-1].reject_reason, "split_remainder_too_short")

    def test_invalid_merge_gap_and_config_are_rejected(self):
        for value in (-1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                merge_segments([], max_gap_s=value)
        with self.assertRaises(ValueError):
            VadConfig(threshold=1.0)

    def test_2_9_second_speech_is_padded_to_3_2_seconds(self):
        speech = SpeechSegment(8300, 11200)
        result = expand_short_speech(speech, [speech], video_end_ms=11400)
        self.assertTrue(result.accepted)
        self.assertEqual(result.end_ms - result.start_ms, 3200)
        self.assertGreaterEqual(result.start_ms, 0)
        self.assertLessEqual(result.end_ms, 11400)
        self.assertGreater(result.speech_ratio, 0.9)

    def test_large_padding_is_rejected(self):
        speech = SpeechSegment(700, 2300)
        result = expand_short_speech(speech, [speech], video_end_ms=11400)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "padding_limit_exceeded")

    def test_video_start_boundary_shifts_padding_to_the_end(self):
        speech = SpeechSegment(0, 2900)
        result = expand_short_speech(speech, [speech], video_end_ms=11400)
        self.assertTrue(result.accepted)
        self.assertEqual((result.start_ms, result.end_ms), (0, 3200))

    def test_padding_does_not_absorb_unrelated_speech(self):
        speech = SpeechSegment(8300, 11200)
        neighbor = SpeechSegment(8100, 8250)
        result = expand_short_speech(speech, [neighbor, speech], video_end_ms=11400)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "padding_overlaps_other_speech")


if __name__ == "__main__":
    unittest.main()
