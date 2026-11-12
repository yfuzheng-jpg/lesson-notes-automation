from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import window_restore


class WindowRestoreTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.title('Isolated window recovery test')
        self.root.geometry('260x140+30+30')
        self.root.update()
        self.addCleanup(self.root.destroy)

    def test_iconic_root_is_restored_without_forcing_keyboard_focus(self):
        self.root.iconify()
        self.root.update()
        self.assertEqual(self.root.state(), 'iconic')
        with patch.object(self.root, 'focus_force', side_effect=AssertionError('Must not force focus')), \
                patch.object(self.root, 'focus_set', side_effect=AssertionError('Must not change focus')):
            target = window_restore.restore_app_window(self.root)
        self.root.update()
        self.assertIs(target, self.root)
        self.assertEqual(self.root.state(), 'normal')
        self.assertTrue(self.root.winfo_viewable())

    def test_hidden_grab_modal_is_restored_and_raised_after_root(self):
        modal = tk.Toplevel(self.root)
        modal.geometry('220x100+40+40')
        modal.update()
        modal.grab_set()
        modal.withdraw()
        self.root.iconify()
        self.root.update()
        order = []
        with patch.object(self.root, 'lift', side_effect=lambda: order.append('root')), \
                patch.object(modal, 'lift', side_effect=lambda: order.append('modal')), \
                patch.object(modal, 'focus_force', side_effect=AssertionError('Must not force focus')):
            target = window_restore.restore_app_window(self.root)
        self.root.update()
        self.assertIs(target, modal)
        self.assertEqual(self.root.state(), 'normal')
        self.assertEqual(modal.state(), 'normal')
        self.assertEqual(order, ['root', 'modal'])
        self.assertIs(self.root.grab_current(), modal)

    def test_offscreen_bounds_are_moved_inside_work_area(self):
        self.root.geometry('260x140+10000+10000')
        self.root.update()
        bounds = (10000, 10000, 10276, 10179)
        with patch.object(window_restore, '_geometry_and_work_area', return_value=(bounds, (0, 0, 800, 600))):
            window_restore.restore_app_window(self.root)
        self.root.update()
        self.assertGreaterEqual(self.root.winfo_x(), 0)
        self.assertGreaterEqual(self.root.winfo_y(), 0)
        self.assertLessEqual(self.root.winfo_x() + self.root.winfo_width() + 16, 800)
        self.assertLessEqual(self.root.winfo_y() + self.root.winfo_height() + 39, 600)

    def test_oversized_window_and_minimum_size_fit_small_work_area(self):
        self.root.minsize(900, 650)
        self.root.geometry('1000x700+30+30')
        self.root.update()
        with patch.object(window_restore, '_geometry_and_work_area', return_value=((30, 30, 1046, 769), (0, 0, 800, 600))):
            window_restore.restore_app_window(self.root)
        self.root.update()
        self.assertLessEqual(self.root.winfo_width() + 16, 784)
        self.assertLessEqual(self.root.winfo_height() + 39, 584)

    def test_negative_monitor_coordinates_are_preserved(self):
        with patch.object(window_restore, '_geometry_and_work_area',
                          return_value=((-1500, 30, -1224, 209), (-1200, 0, -400, 600))):
            window_restore.restore_app_window(self.root)
        self.root.update()
        self.assertGreaterEqual(self.root.winfo_x(), -1200)
        self.assertLess(self.root.winfo_x(), -400)

    def test_later_manual_minimization_stays_minimized(self):
        before_timers = self.root.tk.call('after', 'info')
        window_restore.restore_app_window(self.root)
        self.assertEqual(self.root.tk.call('after', 'info'), before_timers)
        self.root.iconify()
        state = []
        self.root.after(100, lambda: (state.append(self.root.state()), self.root.quit()))
        self.root.mainloop()
        self.assertEqual(state, ['iconic'])


if __name__ == '__main__':
    unittest.main()
