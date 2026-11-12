import json
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_schedule as schedule
import lesson_notes_automation as app


class ScheduleTests(unittest.TestCase):
    def test_boundaries_and_cross_midnight(self):
        for clock, start, end, expected in [
            ('00:59:59', '01:00', '07:00', '26 01:00:00'),
            ('01:00:00', '01:00', '07:00', '26 01:00:00'),
            ('06:59:59', '01:00', '07:00', '26 06:59:59'),
            ('07:00:00', '01:00', '07:00', '27 01:00:00'),
            ('23:00:00', '23:00', '03:00', '26 23:00:00'),
            ('02:59:59', '23:00', '03:00', '26 02:59:59'),
            ('03:00:00', '23:00', '03:00', '26 23:00:00'),
        ]:
            with self.subTest(clock=clock, start=start):
                now = datetime.fromisoformat('2026-10-26T' + clock)
                self.assertEqual(schedule.next_allowed(now, start, end).strftime('%d %H:%M:%S'), expected)
        self.assertEqual(schedule.next_allowed(datetime(2026, 12, 31, 23), '01:00', '07:00'), datetime(2027, 1, 1, 1))

    def test_invalid_and_equal(self):
        for value in ['1:00', '24:00', '01:60', 'bad', ' 01:00', None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                schedule.validate_window(value, '07:00')
        with self.assertRaises(ValueError):
            schedule.validate_window('01:00', '01:00')

    def test_retry_after_and_window(self):
        now = datetime(2026, 10, 26, 2)
        self.assertEqual(schedule.next_retry(now, '01:00'), datetime(2026, 10, 27, 1))
        later = schedule.next_retry(now, '01:00', '172800')
        self.assertEqual(later, now + timedelta(days=2))
        self.assertEqual(schedule.next_retry(now, '01:00', 'bad'), datetime(2026, 10, 27, 1))
        self.assertEqual(schedule.next_allowed(now, '01:00', '07:00', datetime(2026, 10, 27, 8)), datetime(2026, 10, 28, 1))

    def test_limit_classification(self):
        self.assertTrue(schedule.is_usage_limit({'message': 'The ChatGPT user has reached their Subscription Sharing usage limit.'}))
        self.assertTrue(schedule.is_usage_limit({'code': 'rate_limit_exceeded'}))
        self.assertFalse(schedule.is_usage_limit({'code': 'invalid_api_key'}))

    def make_controller(self, folder, **kwargs):
        values = dict(folder=str(folder), note_name='Test', use_clash=False, ai_schedule_enabled=True, ai_schedule_start='01:00', ai_schedule_end='07:00')
        values.update(kwargs)
        return app.SessionController(app.Settings(**values), '', lambda _: None, lambda *_: None, lambda *_: None, Path(folder))

    def test_wait_cancel_and_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = self.make_controller(folder)
            with patch.object(app, 'datetime') as clock:
                clock.now.return_value = datetime(2026, 10, 26, 8)
                def cancel(_):
                    controller.generation_cancel_event.set()
                    return True
                with patch.object(controller.generation_cancel_event, 'wait', side_effect=cancel):
                    with self.assertRaises(app.GenerationCancelled):
                        controller._generation_request_gate()
                self.assertEqual(json.loads(controller.manifest_path.read_text(encoding='utf-8'))['state'], 'generation_waiting')
            controller.generation_cancel_event.clear()
            controller.settings.ai_schedule_enabled = False
            controller._generation_request_gate()

    def test_wait_opens_and_pause_cannot_bypass(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = self.make_controller(folder)
            moments = iter([datetime(2026, 10, 26, 0, 59, 59), datetime(2026, 10, 26, 1)])
            with patch.object(app, 'datetime') as clock, patch.object(controller.generation_cancel_event, 'wait', return_value=False):
                clock.now.side_effect = lambda: next(moments)
                controller._write_manifest = lambda *args: None
                controller._generation_request_gate()
            controller.generation_pause_event.set()
            with patch.object(app.time, 'sleep', side_effect=lambda _: controller.generation_cancel_event.set()):
                with self.assertRaises(app.GenerationCancelled):
                    controller._generation_request_gate()

    def test_limit_deferred_and_bounded_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = self.make_controller(folder, ai_retry_limits=True)
            error = app.AIUsageLimitError('limit', '10')
            for i in range(3):
                controller._defer_usage_limit(error)
                self.assertEqual(controller.limit_retries, i + 1)
                self.assertGreater(controller.generation_not_before, datetime.now(timezone.utc))
            with self.assertRaises(app.AIUsageLimitError):
                controller._defer_usage_limit(error)
            restored = self.make_controller(folder, ai_retry_limits=True)
            self.assertEqual(restored.generation_not_before.replace(microsecond=0), controller.generation_not_before.replace(microsecond=0))
            controller.settings.ai_retry_limits = False
            with self.assertRaises(app.AIUsageLimitError):
                controller._defer_usage_limit(error)

    def test_both_clients_retry_through_same_gate(self):
        for client in [app.OpenAICompatibleClient('http://localhost', 'test', '', None), app.ChatGPTOAuthClient('test', '', None)]:
            calls = []
            client.before_request = lambda: calls.append('gate')
            client.on_usage_limit = lambda error: calls.append('defer')
            with patch.object(client, '_call', side_effect=[app.AIUsageLimitError('limit'), 'done']):
                self.assertEqual(client._scheduled_call([]), 'done')
            self.assertEqual(calls, ['gate', 'defer', 'gate'])
            with patch.object(client, '_call', side_effect=RuntimeError('network')):
                with self.assertRaises(RuntimeError):
                    client._scheduled_call([])

    def test_worker_transmits_limit_details(self):
        class Pipe:
            def send(self, value): self.value = value
            def close(self): pass
        class Response:
            ok = False
            status_code = 429
            text = 'rate limited'
            headers = {'Retry-After': '3600'}
            def json(self): return {'error': {'code': 'rate_limit_exceeded'}}
            def close(self): pass
        pipe = Pipe()
        with patch('requests.post', return_value=Response()):
            app._api_request_worker(pipe, 'http://localhost', {}, {}, 1, None, 'json')
        with self.assertRaises(app.AIUsageLimitError) as error:
            app.OpenAICompatibleClient._request_result(pipe.value)
        self.assertEqual(error.exception.retry_after, '3600')
        Response.ok = True
        Response.iter_lines = lambda self: [b'data: {"type":"response.failed","response":{"error":{"code":"usage_limit_reached"}}}']
        with patch('requests.post', return_value=Response()):
            app._api_request_worker(pipe, 'http://localhost', {}, {}, 1, None, 'responses')
        self.assertTrue(pipe.value['usage_limit'])

    def test_credentials_refresh_again_after_waiting_across_close(self):
        class Receiver:
            def poll(self, *args): return True
            def recv(self): return {'content': 'done'}
            def close(self): pass
        class Sender:
            def close(self): pass
        class Worker:
            def start(self): pass
            def join(self, **kwargs): pass
            def is_alive(self): return False
        class Context:
            def Pipe(self, **kwargs): return Receiver(), Sender()
            def Process(self, **kwargs):
                self.headers = kwargs['args'][2]
                return Worker()
        context = Context()
        client = app.OpenAICompatibleClient('', 'test', '', None)
        gate = iter([False, True, False, False])
        client.before_request = lambda: next(gate)
        refreshed = []
        def headers():
            refreshed.append(len(refreshed) + 1)
            return {'Authorization': str(refreshed[-1])}
        with patch.object(app.multiprocessing, 'get_context', return_value=context):
            self.assertEqual(client._cancellable_request('http://localhost', {}, {}, 1, refresh_headers=headers), 'done')
        self.assertEqual(refreshed, [1, 2])
        self.assertEqual(context.headers['Authorization'], '2')

    def test_checkpoints_resume_without_repeating_completed_work(self):
        with tempfile.TemporaryDirectory() as folder:
            images = [Path(folder) / f'{i}.png' for i in range(7)]
            for image in images: image.write_bytes(b'test')
            checkpoint = Path(folder) / 'generation_checkpoint.json'
            client = app.OpenAICompatibleClient('', 'test', '', None)
            client._image_data = lambda path: 'data:image/jpeg;base64,test'
            with patch.object(client, '_scheduled_call', side_effect=['group one', RuntimeError('interrupted')]) as call:
                with self.assertRaises(RuntimeError):
                    client.create_notes(images, 'text', 'Test', 'Markdown (.md)', '', lambda _: None, checkpoint, 'same', generation_mode='分组分析后合成')
                self.assertEqual(call.call_count, 2)
            with patch.object(client, '_scheduled_call', side_effect=['group two', 'final']) as call:
                self.assertEqual(client.create_notes(images, 'text', 'Test', 'Markdown (.md)', '', lambda _: None, checkpoint, 'same', generation_mode='分组分析后合成'), 'final')
                self.assertEqual(call.call_count, 2)
            client.before_request = lambda: self.fail('Cached final text should export without waiting')
            with patch.object(client, '_scheduled_call', side_effect=AssertionError('must not resend')):
                self.assertEqual(client.create_notes(images, 'text', 'Test', 'Markdown (.md)', '', lambda _: None, checkpoint, 'same', generation_mode='分组分析后合成'), 'final')

    def test_ui_settings_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = app.Settings(folder=folder, model='test', use_clash=False)
            config = Path(folder) / 'settings.json'
            config.write_text(json.dumps(asdict(settings)), encoding='utf-8')
            root = app.Tk()
            root.withdraw()
            try:
                with patch.object(app, 'CONFIG_FILE', config):
                    ui = app.LessonNotesApp(root)
                    ui.key_var.set('test-key')
                    ui.ai_schedule_enabled_var.set(True)
                    ui.ai_schedule_start_var.set('23:00')
                    ui.ai_schedule_end_var.set('03:00')
                    ui.ai_retry_limits_var.set(True)
                    collected = ui._collect_settings()
                    self.assertTrue(collected.ai_schedule_enabled)
                    self.assertEqual(collected.ai_schedule_start, '23:00')
                    self.assertTrue(collected.ai_retry_limits)
                    ui.ai_schedule_end_var.set('23:00')
                    with self.assertRaises(ValueError): ui._collect_settings()
            finally:
                root.destroy()

    def test_latex_math_and_unsafe_commands(self):
        markdown = r"""正文含 \(Y\)、$\alpha$ 和 50%。

\[
Y = A K^{\alpha}(hL)^{1-\alpha}
\]

\[
\begin{aligned}
\text{增长率} &= \frac{\alpha}{1-\alpha} \\
&+ \text{其他贡献}
\end{aligned}
\]

\[\input{secret.txt}\]
"""
        tex = app._markdown_to_latex(markdown, 'Test')
        self.assertIn(r'\(Y\)', tex)
        self.assertIn(r'\(\alpha\)', tex)
        self.assertIn(r'\begin{aligned}', tex)
        self.assertIn(r'50\%', tex)
        self.assertNotIn(r'\input{secret.txt}', tex)
        self.assertNotIn('LESSONMATHBLOCK', tex)

    def test_physics_formulas_and_windows_newlines(self):
        markdown = "\\[\r\n\\boxed{\\hbar \\longrightarrow 60^\\circ}\\]\r\n\\[\\angle \\kappa \\xrightarrow{E} \\mathsf{T}\\]\n\\[\\begin{vmatrix}a&b\\\\c&d\\end{vmatrix}=0\\]"
        tex = app._markdown_to_latex(markdown, 'Physics')
        self.assertNotIn('\r', tex)
        self.assertIn(r'\boxed{\hbar \longrightarrow 60^\circ}', tex)
        self.assertIn(r'\angle \kappa \xrightarrow{E} \mathsf{T}', tex)
        self.assertIn(r'\begin{vmatrix}a&b\\c&d\end{vmatrix}=0', tex)
        self.assertNotIn(r'\textbackslash', tex)
        self.assertNotIn('LESSONMATHBLOCK', tex)

    def test_latex_emphasis_and_numbered_questions(self):
        tex = app._markdown_to_latex('**重点**\n\n1. 第一题\n2. 第二题', 'Test')
        self.assertIn(r'\textbf{重点}', tex)
        self.assertIn(r'\item[2.] 第二题', tex)
        self.assertNotIn(r'\item 2.', tex)

    def test_angstrom_units_and_roman_semiconductor_groups(self):
        tex = app._markdown_to_latex('Ⅲ-Ⅴ族和Ⅰ族杂质\n\\[a=5.43\\,\\text{\\AA}\\]', 'Test')
        self.assertIn('III-V族和I族杂质', tex)
        self.assertIn(r'a=5.43\,\text{\AA}', tex)
        self.assertNotIn(r'\textbackslash', tex)

    def test_math_table_pipes_and_chinese_labels(self):
        markdown = r'''| 参数 | \(\left|x\right|\) | $|y|$ |
| --- | --- | --- |
| \(\mathrm{扩}\) | \(n、p\) | \(\text{浓度、温度}\) |

\[\begin{array}{c|c}a&b\\\hline c&d\end{array}\rightleftarrows\text{①}\]
'''
        rows = json.loads(app._markdown_blocks(markdown)[0][1])
        self.assertEqual([len(row) for row in rows], [3, 3])
        self.assertEqual(rows[0][1], r'\(\left|x\right|\)')
        tex = app._markdown_to_latex(markdown, 'Test')
        self.assertIn(r'\text{扩}', tex)
        self.assertIn(r'\(n,\,p\)', tex)
        self.assertIn(r'\text{浓度、温度}', tex)
        self.assertIn(r'\rightleftarrows\text{(1)}', tex)
        self.assertNotIn(r'\textbackslash', tex)


if __name__ == '__main__':
    unittest.main()
