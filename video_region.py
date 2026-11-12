"""Choose a source-pixel rectangle on a seekable local-video preview."""
from __future__ import annotations

import math
import re
from pathlib import Path


def _dialog_work_area(parent):
    """Return the usable area on the parent's monitor, excluding the taskbar."""
    fallback = (0, 0, parent.winfo_screenwidth(), parent.winfo_screenheight())
    try:
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        monitor = user32.MonitorFromWindow(parent.winfo_id(), 2)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.work
            return work.left, work.top, work.right, work.bottom
    except (AttributeError, OSError, ValueError):
        pass
    return fallback


def _scrollable_dialog(dialog, parent, width=960, height=640):
    """Reserve a fixed action bar before laying out scrollable modal content."""
    from tkinter import Canvas, ttk
    left, top, right, bottom = _dialog_work_area(parent)
    available_width = max(240, right - left - 32)
    available_height = max(200, bottom - top - 60)
    width, height = min(width, available_width), min(height, available_height)
    dialog.geometry(f'{width}x{height}+{left + (right - left - width) // 2}+{top + (bottom - top - height) // 2}')
    dialog.minsize(min(480, width), min(300, height))
    dialog.resizable(True, True)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(0, weight=1)
    # A sibling window can be embedded in a canvas whose parent is its ancestor.
    # Creating this frame first also preserves existing preview-canvas discovery.
    body = ttk.Frame(dialog, padding=12)
    viewport = Canvas(dialog, name='dialog_viewport', highlightthickness=0, borderwidth=0)
    viewport.grid(row=0, column=0, sticky='nsew')
    scrollbar = ttk.Scrollbar(dialog, orient='vertical', command=viewport.yview)
    scrollbar.grid(row=0, column=1, sticky='ns')
    viewport.configure(yscrollcommand=scrollbar.set)
    window = viewport.create_window(0, 0, window=body, anchor='nw')
    body.bind('<Configure>', lambda _: viewport.configure(scrollregion=viewport.bbox('all')))
    viewport.bind('<Configure>', lambda event: viewport.itemconfigure(window, width=max(1, event.width)))
    footer = ttk.Frame(dialog, name='dialog_footer', padding=(12, 8))
    footer.grid(row=1, column=0, columnspan=2, sticky='ew')
    dialog.bind('<MouseWheel>', lambda event: viewport.yview_scroll(-int(event.delta / 120), 'units'), add='+')
    return body, footer, (width, height)


def parse_video_time(value: str) -> float:
    """Accept seconds, MM:SS, or HH:MM:SS (including fractional seconds)."""
    value = value.strip()
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", value):
        raise ValueError("时间请填写秒数、MM:SS 或 HH:MM:SS，例如 90、01:30、00:01:30.5。")
    parts = [float(part) for part in value.split(":")]
    if not all(math.isfinite(part) for part in parts) or any(part >= 60 for part in parts[1:]):
        raise ValueError("时间中的分钟和秒须小于 60。")
    return sum(part * 60 ** power for power, part in enumerate(reversed(parts)))


def validate_time_range(start: float, end: float | None, duration: float = 0) -> tuple[float, float | None]:
    if not math.isfinite(start) or start < 0:
        raise ValueError("起始时间必须为非负数。")
    if end is not None and (not math.isfinite(end) or end <= start):
        raise ValueError("终止时间必须晚于起始时间。")
    if duration > 0:
        if start >= duration:
            raise ValueError("起始时间必须早于视频结束时间。")
        if end is not None and end > duration + 0.000001:
            raise ValueError("终止时间不能超过视频时长。")
    return float(start), float(end) if end is not None else None


def pixel_region(start, end, display_size, frame_size) -> list[int]:
    """Map a drag on a scaled preview to a clamped [x, y, width, height]."""
    dw, dh = display_size
    fw, fh = frame_size
    x1, x2 = sorted(max(0, min(dw, point[0])) for point in (start, end))
    y1, y2 = sorted(max(0, min(dh, point[1])) for point in (start, end))
    left, top = math.floor(x1 * fw / dw), math.floor(y1 * fh / dh)
    right, bottom = math.ceil(x2 * fw / dw), math.ceil(y2 * fh / dh)
    return [left, top, right - left, bottom - top]


def validate_region(region, frame_size) -> list[int]:
    if (not isinstance(region, (list, tuple)) or len(region) != 4
            or any(type(value) is not int for value in region)):
        raise ValueError("视频选区必须为四个整数：x、y、宽、高。")
    x, y, width, height = region
    fw, fh = frame_size
    if x < 0 or y < 0 or width < 2 or height < 2 or x + width > fw or y + height > fh:
        raise ValueError("视频选区超出画面范围或过小，请重新导入并框选。")
    return list(region)


def read_preview(source: Path, seconds: float = 0):
    import av

    with av.open(str(source)) as container:
        if not container.streams.video:
            raise ValueError("文件中没有可读取的视频轨道。")
        stream = container.streams.video[0]
        origin = float(container.start_time / av.time_base) if container.start_time is not None else (
            float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0)
        duration = float(container.duration / av.time_base) if container.duration else (
            float(stream.duration * stream.time_base) if stream.duration is not None else 0.0)
        if seconds > 0:
            container.seek(int((origin + seconds) / stream.time_base), stream=stream, backward=True)
        last = None
        for frame in container.decode(stream):
            last = frame
            if seconds <= 0 or frame.time is None or float(frame.time) >= origin + seconds:
                break
        if last is None:
            raise ValueError("未能读取此时间的画面，请换一个预览时间或检查视频。")
        return last.to_image(), duration


def select_video_region(parent, source: Path, screenshots: bool = True,
                        initial_selection: dict | None = None) -> dict | None:
    from tkinter import Canvas, StringVar, Toplevel, messagebox, ttk
    from PIL import ImageTk

    # Decode before creating the window so failure cannot leave a stranded modal.
    image, duration = read_preview(source)
    frame_size = image.size
    initial_selection = initial_selection or {}
    dialog = Toplevel(parent)
    dialog.title("视频导入 — 时间范围与截图区域")
    dialog.transient(parent)
    body, buttons, bounds = _scrollable_dialog(dialog, parent, 1000, 740)
    wrap = max(180, bounds[0] - 60)
    ttk.Label(body, text=source.name, wraplength=wrap).pack(anchor="w")
    ttk.Label(body, text=("设置起止时间，并拖动鼠标框选截图区域。截图和转写仅处理所选时间段；转写包含该时段的完整音轨。"
                         if screenshots else "设置需要转写的起始和终止时间；只处理所选时间段的音轨。"),
              wraplength=wrap).pack(anchor="w", pady=(4, 8))
    display = image.copy()
    display.thumbnail((max(120, bounds[0] - 60), min(420, max(100, bounds[1] - 310))))
    dw, dh = display.size
    canvas = Canvas(body, width=dw, height=dh, highlightthickness=0, cursor="crosshair", background="#222")
    canvas.pack()
    photo = ImageTk.PhotoImage(display, master=dialog)
    image_item = canvas.create_image(0, 0, image=photo, anchor="nw")
    rectangle = None
    start = None
    region = None
    result = None
    status = StringVar(master=dialog, value=f"原视频 {frame_size[0]}×{frame_size[1]}；当前使用完整画面。")
    ttk.Label(body, textvariable=status, wraplength=wrap).pack(anchor="w", pady=6)
    range_row = ttk.Frame(body)
    range_row.pack(fill="x", pady=(2, 6))
    range_start = StringVar(master=dialog, value=f"{initial_selection.get('start_seconds', 0):g}")
    initial_end = initial_selection.get("end_seconds")
    range_end = StringVar(master=dialog, value=f"{initial_end:g}" if initial_end is not None else "")
    range_row.columnconfigure(1, weight=1)
    range_row.columnconfigure(3, weight=1)
    ttk.Label(range_row, text="起始时间").grid(row=0, column=0, sticky='w')
    ttk.Entry(range_row, name="range_start", textvariable=range_start, width=12).grid(row=0, column=1, sticky='ew', padx=6)
    ttk.Label(range_row, text="终止时间").grid(row=0, column=2, sticky='w')
    ttk.Entry(range_row, name="range_end", textvariable=range_end, width=12).grid(row=0, column=3, sticky='ew', padx=6)
    ttk.Label(range_row, text="终止留空至视频结束；支持秒数 / MM:SS / HH:MM:SS", foreground="#666", wraplength=wrap).grid(row=1, column=0, columnspan=4, sticky='w', pady=(4, 0))

    def reset():
        nonlocal rectangle, region, start
        if rectangle is not None:
            canvas.delete(rectangle)
        rectangle = region = start = None
        confirm.state(["disabled"])
        status.set(f"原视频 {frame_size[0]}×{frame_size[1]}；当前使用完整画面。")

    def begin(event):
        nonlocal start, rectangle, region
        reset()
        region = None
        start = (max(0, min(dw, event.x)), max(0, min(dh, event.y)))
        rectangle = canvas.create_rectangle(*start, *start, outline="#00e893", width=3)

    def drag(event):
        if start is not None:
            canvas.coords(rectangle, *start, max(0, min(dw, event.x)), max(0, min(dh, event.y)))

    def finish(event):
        nonlocal region
        if start is None:
            return
        drag(event)
        candidate = pixel_region(start, (event.x, event.y), (dw, dh), frame_size)
        try:
            region = validate_region(candidate, frame_size)
        except ValueError:
            region = None
            status.set("选区太小，请重新拖动框选。")
            return
        status.set(f"已选视频区域：x={region[0]}, y={region[1]}, {region[2]}×{region[3]} 像素")
        confirm.state(["!disabled"])

    def accept(full_frame=False):
        nonlocal result
        if not full_frame and region is None:
            return
        try:
            first = parse_video_time(range_start.get())
            last = parse_video_time(range_end.get()) if range_end.get().strip() else None
            first, last = validate_time_range(first, last, duration)
        except ValueError as exc:
            messagebox.showerror("时间范围不正确", str(exc), parent=dialog)
            return
        result = {"capture_region": None if full_frame else region, "frame_size": list(frame_size),
                  "start_seconds": first, "end_seconds": last}
        dialog.destroy()

    seek_row = ttk.Frame(body)
    seek_row.pack(fill="x", pady=(2, 8))
    ttk.Label(seek_row, text="预览时间").pack(side="left")
    position = StringVar(master=dialog, value="0")
    ttk.Entry(seek_row, textvariable=position, width=13).pack(side="left", padx=6)

    def seek():
        nonlocal photo
        try:
            seconds = parse_video_time(position.get())
            if duration and seconds >= duration:
                raise ValueError("预览时间必须在视频时长以内。")
            updated, _ = read_preview(source, seconds)
            if updated.size != frame_size:
                raise ValueError("此视频在播放中改变了画面尺寸；请使用完整画面，或先转换为固定尺寸的视频。")
            updated = updated.resize((dw, dh))
            photo = ImageTk.PhotoImage(updated, master=dialog)
            canvas.itemconfigure(image_item, image=photo)
        except Exception as exc:
            messagebox.showerror("无法预览", str(exc), parent=dialog)

    ttk.Button(seek_row, text="查看此帧", command=seek).pack(side="left")
    if duration:
        ttk.Label(seek_row, text=f"  视频时长约 {duration:.1f} 秒").pack(side="left")
    buttons.columnconfigure(0, weight=1, uniform='actions')
    buttons.columnconfigure(1, weight=1, uniform='actions')
    confirm = ttk.Button(buttons, text="使用框选区域并导入", command=accept, state="disabled")
    if screenshots:
        confirm.grid(row=0, column=0, sticky='ew', padx=(0, 4), pady=(0, 4))
    full = ttk.Button(buttons, text="使用完整画面并导入" if screenshots else "导入此时间段",
                      command=lambda: accept(True))
    full.grid(row=0, column=1 if screenshots else 0, sticky='ew', padx=(4, 0) if screenshots else (0, 4), pady=(0, 4))
    if screenshots:
        ttk.Button(buttons, text="重新框选", command=reset).grid(row=1, column=0, sticky='ew', padx=(0, 4))
    ttk.Button(buttons, text="取消", command=dialog.destroy).grid(row=1 if screenshots else 0, column=1, sticky='ew', padx=(4, 0))
    if screenshots:
        canvas.bind("<ButtonPress-1>", begin)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", finish)
        if initial_selection.get("capture_region"):
            region = validate_region(initial_selection["capture_region"], frame_size)
            x, y, width, height = region
            rectangle = canvas.create_rectangle(x * dw / frame_size[0], y * dh / frame_size[1],
                (x + width) * dw / frame_size[0], (y + height) * dh / frame_size[1], outline="#00e893", width=3)
            confirm.state(["!disabled"])
            status.set(f"已选视频区域：x={x}, y={y}, {width}×{height} 像素")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.bind("<Return>", lambda _event: accept(region is None or not screenshots))
    dialog.grab_set()
    parent.wait_window(dialog)
    return result
