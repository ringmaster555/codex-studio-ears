import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]


class FileComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.results = self.folder / "Session reports"
        self.reference = self.folder / "Reference mix.wav"
        self.take = self.folder / "Take mix.wav"

    def stereo_tone(self, rate):
        time = np.arange(rate * 4) / rate
        return np.column_stack((np.sin(time * 2 * np.pi * 80),
                                np.sin(time * 2 * np.pi * 80 + 0.3))) * 0.25

    def compare(self):
        return subprocess.run(
            [ROOT / "Start Mix Listener.command", "--signal-only", "compare", self.reference, self.take],
            env={**os.environ, "MIX_LISTENER_RESULTS": str(self.results)},
            capture_output=True,
            text=True,
        )

    def test_preserves_level_delta_at_mixed_sample_rates(self):
        sf.write(self.reference, self.stereo_tone(44100), 44100, subtype="FLOAT")
        sf.write(self.take, self.stereo_tone(48000) * 0.5, 48000, subtype="FLOAT")
        result = self.compare()
        self.assertEqual(result.returncode, 0, result.stderr)
        report, = self.results.glob("*/Mix feedback.txt")
        report_text = report.read_text(encoding="utf-8")
        level, = (line for line in report_text.splitlines() if line.startswith("Level (RMS"))
        self.assertEqual(level.split()[-1], "-6.0")
        self.assertIn("44100 Hz", report_text)
        self.assertIn("48000 Hz", report_text)
        self.assertNotIn("cosine similarity", report_text)

    def test_rejects_mono_without_writing_report(self):
        stereo = self.stereo_tone(48000)
        sf.write(self.reference, stereo, 48000)
        sf.write(self.take, stereo[:, 0], 48000)
        result = self.compare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Use stereo audio", result.stderr)
        self.assertFalse(self.results.exists())

    def test_rejects_silent_passage_without_writing_report(self):
        stereo = self.stereo_tone(48000)
        sf.write(self.reference, stereo, 48000)
        sf.write(self.take, np.zeros_like(stereo), 48000)
        result = self.compare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No signal in the sampled windows", result.stderr)
        self.assertFalse(self.results.exists())
