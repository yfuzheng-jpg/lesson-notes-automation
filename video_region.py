"""Choose a source-pixel rectangle on a seekable local-video preview."""
from __future__ import annotations

import math
import re
from pathlib import Path


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


def select_video_region(parent, source: Path, screenshots: bool = True) -> dict | None:
    from tkinter import Canvas, StringVar, Toplevel, messagebox, ttk
    from PIL import ImageTk

    # Decode before creating the window so failure cannot leave a stranded modal.
    image, duration = read_preview(source)
    frame_size = image.size
    dialog = Toplevel(parent)
    dialog.title("视频导入 — 时间范围与截图区域")
    dialog.transient(parent)
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=12)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=source.name, wraplength=850).pack(anchor="w")
    ttk.Label(body, text=("设置起止时间，并拖动鼠标框选截图区域。截图和转写仅处理所选时间段；转写包含该时段的完整音轨。"
                         if screenshots else "设置需要转写的起始和终止时间；只处理所选时间段的音轨。"),
              wraplength=850).pack(anchor="w", pady=(4, 8))
    display = image.copy()
    display.thumbnail((min(960, parent.winfo_screenwidth() - 100),
                       min(500, max(120, parent.winfo_screenheight() - 420))))
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
    ttk.Label(body, textvariable=status).pack(anchor="w", pady=6)
    range_row = ttk.Frame(body)
    range_row.pack(fill="x", pady=(2, 6))
    range_start = StringVar(master=dialog, value="00:00:00")
    range_end = StringVar(master=dialog, value="")
    ttk.Label(range_row, text="起始时间").pack(side="left")
    ttk.Entry(range_row, name="range_start", textvariable=range_start, width=14).pack(side="left", padx=6)
    ttk.Label(range_row, text="终止时间").pack(side="left")
    ttk.Entry(range_row, name="range_end", textvariable=range_end, width=14).pack(side="left", padx=6)
    ttk.Label(range_row, text="留空至视频结束；秒数 / MM:SS / HH:MM:SS", foreground="#666").pack(side="left")

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
    ttk.Label(seek_row, text="预览时间（秒或 HH:MM:SS）").pack(side="left")
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
    buttons = ttk.Frame(body)
    buttons.pack(fill="x")
    confirm = ttk.Button(buttons, text="使用框选区域并导入", command=accept, state="disabled")
    if screenshots:
        confirm.pack(side="left")
    ttk.Button(buttons, text="使用完整画面并导入" if screenshots else "导入此时间段",
               command=lambda: accept(True)).pack(side="left", padx=6)
    if screenshots:
        ttk.Button(buttons, text="重新框选", command=reset).pack(side="left")
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
    if screenshots:
        canvas.bind("<ButtonPress-1>", begin)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", finish)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.grab_set()
    parent.wait_window(dialog)
    return result
