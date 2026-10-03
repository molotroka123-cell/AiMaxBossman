import unittest

from tools.k1m6a_smart_frames import parse_asr_segments, parse_vtt, score_moments


class SmartFrameTests(unittest.TestCase):
    def test_vtt_timestamp_and_markup_parsing(self):
        cues = parse_vtt("""WEBVTT

00:00:12.000 --> 00:00:14.000
<c>Watch the CVD divergence at value area low.</c>

00:01:03.500 --> 00:01:04.500
Giveaway link in chat
""")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0][0], 12.0)
        self.assertIn("CVD divergence", cues[0][2])

    def test_selects_content_moments_and_rejects_promo_not_uniform(self):
        cues = [(20, 22, "Giveaway, subscribe and use my promo code"),
                (137, 140, "CVD divergence at value area low, wait for reclaim"),
                (513, 516, "Move stop to break even after acceptance")]
        result = score_moments(cues, max_frames=8, min_gap_s=35)
        times = [x.time_s for x in result]
        self.assertEqual(times, [137, 513])
        self.assertGreater(times[1] - times[0], 35)

    def test_scene_change_only_nudges_relevant_anchor(self):
        result = score_moments([(100, 102, "single print retest entry")],
                               scene_times=[102], max_frames=2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].time_s, 102)
        self.assertIn("near_scene_change", result[0].reasons)

    def test_local_asr_can_add_a_semantic_anchor_missing_from_captions(self):
        asr = parse_asr_segments('[{"start": 91, "end": 95, "text": "wait for bearish retest entry"}]')
        self.assertEqual(len(asr), 1)
        result = score_moments(asr, max_frames=4)
        self.assertEqual([item.time_s for item in result], [91])
        self.assertIn("LOCAL ASR", result[0].evidence[0])

    def test_invalid_local_asr_rows_are_ignored(self):
        rows = parse_asr_segments('[{"start":"bad","end":2,"text":"entry"},null]')
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
