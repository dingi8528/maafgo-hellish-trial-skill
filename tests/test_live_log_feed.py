import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/maafgo-hellish-trial/scripts"
sys.path.insert(0, str(SCRIPTS))
import live_log_feed as feed
from trial_live import init_session, read_state


class LogFeedTests(unittest.TestCase):
    def test_turn_phase_and_submitted_are_observations(self):
        self.assertEqual(feed.caption_for_line("[AutoBattle] Turn 4 | scene=MAIN_BATTLE")['turn'], 'T4')
        self.assertEqual(feed.caption_for_line("[试炼策略] 职阶确认血条 2→3，上一条剩余颜色攻击=0")['phase'], '阶段 3')
        event = feed.caption_for_line("[试炼策略] 已确认提交 turn4，血条=3，剩余颜色攻击=1")
        self.assertEqual(event['kind'], 'commentary')
        self.assertNotIn('mission', event)

    def test_win_and_loss_do_not_confirm_missions(self):
        for line in ("[AutoBattle] Victory. turns=7", "[AutoBattle] Defeat. turns=5"):
            event = feed.caption_for_line(line)
            self.assertEqual(event['status'], 'paused')
            self.assertNotIn('mission', event)

    def test_unrelated_lines_and_private_paths_are_not_published(self):
        self.assertIsNone(feed.caption_for_line("some user's secret path C:/private/token"))
        self.assertIsNone(feed.caption_for_line("MCP connected to another controller"))

    def test_cursor_handles_partial_utf8_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'log'
            cursor = feed.LogCursor(path)
            self.assertEqual(cursor.read(), [])
            path.write_bytes(b'old\n')
            self.assertEqual(cursor.read(), [])
            with path.open('ab') as out: out.write('阶段'.encode()[:4])
            self.assertEqual(cursor.read(), [])
            with path.open('ab') as out: out.write('阶段'.encode()[4:] + b'\n')
            self.assertEqual(cursor.read(), ['阶段'])
            path.write_bytes(b'new\n')
            self.assertEqual(cursor.read(), ['new'])

    def test_selected_file_feeds_http_state_without_touching_other_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_session(root / 'session')
            log = root / 'selected.log'
            log.write_text('[AutoBattle] Turn 3 | scene=MAIN_BATTLE\n[AutoBattle] Victory. turns=3\n', encoding='utf-8')
            (root / 'other.log').write_text('[AutoBattle] Turn 99 | scene=MAIN_BATTLE\n', encoding='utf-8')
            cursor = feed.LogCursor(log, from_start=True)
            self.assertEqual(feed.feed_once(cursor, root / 'session'), 2)
            state = read_state(root / 'session')
            self.assertEqual(state['session']['turn'], 'T3')
            self.assertEqual(state['missions']['confirmed_count'], 0)
            self.assertEqual(feed.feed_once(cursor, root / 'session'), 0)


if __name__ == '__main__':
    unittest.main()
