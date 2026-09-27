import json
from pathlib import Path
import tempfile
import unittest
from progress import Progress


class ProgressTests(unittest.TestCase):
    def test_real_milestones_and_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            progress = Progress(path)
            progress.set(0, 'done')
            progress.set(1, 'running')
            progress.stop('blocked')
            states = [item['state'] for item in json.loads(path.read_text())['steps']]
            self.assertEqual(states, ['done','warning','skipped','skipped','skipped'])
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_no_progress_path_is_optional(self):
        progress = Progress()
        progress.set(2, 'skipped')
        self.assertEqual(progress.steps[2]['state'], 'skipped')
