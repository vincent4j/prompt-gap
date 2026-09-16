import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class LiveStateTests(unittest.TestCase):
    def test_stale_independent_progress_does_not_erase_other_transport(self):
        path = Path(__file__).parent / 'manual/m4_live.py'
        spec = importlib.util.spec_from_file_location('live_state_test', path)
        live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with patch.object(live, 'PROJECT', project):
                live.pg.Recorder(project).run('enable')
                live.save_state({'tasks': {'api': {}}, 'steps': {'api-generate': {'attempt_id': 'a'}}})
                live.save_state({'tasks': {'builtin': {}}, 'steps': {'builtin-generate': {'attempt_id': 'b'}}})
                state = live.pg.read_json(project / 'state.json')
                self.assertEqual({'api-generate', 'builtin-generate'}, set(state['steps']))
                self.assertEqual({'api', 'builtin'}, set(state['tasks']))
