"""Explicitly restore this Tk application without stealing recording focus."""
from __future__ import annotations

import os


def _windows_geometry(window):
    """Return the target window's outer bounds and nearest monitor work area."""
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    handle = user32.GetAncestor(int(window.winfo_id()), 2) or int(window.winfo_id())
    monitor = user32.MonitorFromWindow(handle, 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    bounds = wintypes.RECT()
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError('Cannot read the target monitor work area')
    if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
        raise OSError('Cannot read the target window bounds')
    return ((bounds.left, bounds.top, bounds.right, bounds.bottom),
            (info.work.left, info.work.top, info.work.right, info.work.bottom))


def _geometry_and_work_area(window):
    if os.name == 'nt':
        try:
            return _windows_geometry(window)
        except (OSError, AttributeError):
            pass
    x, y = window.winfo_x(), window.winfo_y()
    width, height = window.winfo_width(), window.winfo_height()
    left, top = window.winfo_vrootx(), window.winfo_vrooty()
    screen_width = window.winfo_vrootwidth() or window.winfo_screenwidth()
    screen_height = window.winfo_vrootheight() or window.winfo_screenheight()
    return (x, y, x + width, y + height), (left, top, left + screen_width, top + screen_height)


def _place_window(window, width, height, x, y):
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            handle = user32.GetAncestor(int(window.winfo_id()), 2) or int(window.winfo_id())
            window.geometry(f'{width}x{height}')
            window.update_idletasks()
            # Native absolute coordinates preserve negative-coordinate monitors;
            # Tk's "-x" geometry notation instead means an offset from the right.
            if not user32.SetWindowPos(handle, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010):
                raise OSError('Cannot place the target window')
            return
        except (OSError, AttributeError):
            pass
    window.geometry(f'{width}x{height}{x:+d}{y:+d}')


def _restore_window(window):
    window.deiconify()
    window.state('normal')
    window.update_idletasks()
    bounds, area = _geometry_and_work_area(window)
    x, y, right, bottom = bounds
    left, top, work_right, work_bottom = area
    width, height = window.winfo_width(), window.winfo_height()
    frame_width = max(0, right - x - width)
    frame_height = max(0, bottom - y - height)
    margin = 8
    max_width = max(1, work_right - left - frame_width - 2 * margin)
    max_height = max(1, work_bottom - top - frame_height - 2 * margin)
    new_width, new_height = min(width, max_width), min(height, max_height)
    new_x = min(max(x, left + margin), max(left + margin, work_right - margin - new_width - frame_width))
    new_y = min(max(y, top + margin), max(top + margin, work_bottom - margin - new_height - frame_height))
    if (new_x, new_y, new_width, new_height) != (x, y, width, height):
        minimum_width, minimum_height = window.minsize()
        if minimum_width > max_width or minimum_height > max_height:
            window.minsize(min(minimum_width, max_width), min(minimum_height, max_height))
        _place_window(window, new_width, new_height, new_x, new_y)
        window.update_idletasks()
    window.lift()


def restore_app_window(root):
    """Restore root and its active modal, preserving grabs and keyboard focus.

    Invoke only in response to an explicit user action. This function installs
    no timer or map/unmap binding, so later manual minimization stays intact.
    The returned target is the modal top-level when one is active, else root.
    """
    from tkinter import TclError

    try:
        grabbed = root.grab_current()
        modal = grabbed.winfo_toplevel() if grabbed is not None else None
        if modal is not None and modal.tk is not root.tk:
            modal = None
    except (TclError, AttributeError):
        modal = None
    _restore_window(root)
    if modal is not None and modal is not root:
        try:
            if modal.winfo_exists():
                _restore_window(modal)
                return modal
        except TclError:
            # An application callback may have destroyed the modal already.
            pass
    return root
