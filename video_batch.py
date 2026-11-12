"""Sequential video jobs and a queue editor; no additional runtime dependencies."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

VIDEO_FILETYPES = [("视频文件", "*.mp4 *.mkv *.mov *.avi *.wmv *.webm *.m4v *.ts *.mts *.m2ts *.flv *.mpg *.mpeg"),
                   ("所有文件", "*.*")]


@dataclass
class VideoJob:
    source: Path
    selection: dict = field(default_factory=dict)
    state: str = "等待处理"
    session_dir: Path | None = None
    output_path: Path | None = None
    issue: str | None = None


class VideoBatch:
    """All transitions run on the UI thread via dispatch; only one job is active."""
    def __init__(self, jobs, settings, api_key, controller_factory, dispatch,
                 on_change, on_done, *, generate=True, review=None):
        self.jobs = list(jobs)
        self.settings = replace(settings)
        self.api_key = api_key
        self.controller_factory = controller_factory
        self.dispatch = dispatch
        self.on_change = on_change
        self.on_done = on_done
        self.generate = generate
        self.review = review
        self.index = -1
        self.current = None
        self.running = False
        self.cancelled = False
        self.message = ""
        self.report_error = ""
        self.reviewing = False
        self.report_path = Path(settings.folder) / (
            f"批量导入_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.json")

    def start(self):
        if self.running or self.index != -1:
            raise RuntimeError("此批量队列已经开始。")
        if not self.jobs:
            raise ValueError("请至少选择一个视频。")
        self.running = True
        self._save_report()
        self.dispatch(self._next)

    def _save_report(self):
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "state": "处理中" if self.running else "已停止" if self.cancelled else "已完成",
            "auto_generate": self.generate,
            "material_mode": self.settings.material_mode,
            "screenshot_difference_percent": self.settings.screenshot_difference_percent,
            "jobs": [{"source": str(job.source), "selection": job.selection, "state": job.state,
                      "session_dir": str(job.session_dir) if job.session_dir else None,
                      "output_path": str(job.output_path) if job.output_path else None,
                      "issue": job.issue} for job in self.jobs],
        }
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.report_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.report_path)
            self.report_error = ""
        except OSError as exc:
            self.report_error = f"批量清单保存失败：{exc}"

    def _notify(self, message=""):
        if message:
            self.message = message
        self._save_report()
        self.on_change(self)

    def _status(self, message):
        if self.running:
            self.message = message
            self.on_change(self)

    def _next(self):
        if not self.running:
            return
        if self.cancelled or self.index + 1 >= len(self.jobs):
            self._finish()
            return
        self.index += 1
        job = self.jobs[self.index]
        self.current = None
        job.state = "导入中"
        try:
            settings = replace(self.settings, note_name=job.source.stem)
            self.current = self.controller_factory(
                settings, self.api_key,
                lambda message: self.dispatch(lambda: self._status(message)),
                lambda issue, folder: self.dispatch(lambda: self._imported(job, issue, folder)),
                lambda path, issue, folder: self.dispatch(lambda: self._generated(job, path, issue, folder)),
            )
            job.session_dir = self.current.session_dir
            self.current.import_video(job.source, job.selection)
            self._notify("正在提取视频素材…")
        except Exception as exc:
            self._complete(job, "导入失败", str(exc))

    def _imported(self, job, issue, folder):
        if not self.running or job is not self.jobs[self.index]:
            return
        job.session_dir = Path(folder)
        if self.cancelled:
            self._complete(job, "已停止", issue)
        elif issue:
            self._complete(job, "导入失败", issue)
        elif not self.generate:
            self._complete(job, "素材已保存")
        else:
            try:
                approved = True
                if self.review:
                    self.reviewing = True
                    try:
                        approved = self.review(job.session_dir)
                    finally:
                        self.reviewing = False
                if self.cancelled:
                    self._complete(job, "已停止")
                    return
                if not approved:
                    self._complete(job, "素材已保存", "已跳过本视频的笔记生成。")
                    return
                if self.cancelled or not self.running:
                    self._complete(job, "已停止")
                    return
                settings = self.current.settings
                images = list(self.current.images_dir.glob("*.png")) if settings.material_mode != "仅转写" else []
                text_path = job.session_dir / "transcript.txt"
                text_length = len(text_path.read_text(encoding="utf-8")) if text_path.exists() and settings.material_mode != "仅截图" else 0
                if (settings.generation_mode == "整章一次生成"
                        and (len(images) > 60 or sum(p.stat().st_size for p in images) > 40 * 1024 * 1024
                             or text_length > 100000)):
                    settings = replace(settings, generation_mode="分组分析后合成")
                    job.issue = "素材较多，已自动改用分组分析后合成。"
                job.state = "生成中"
                self.current.generate_notes(settings, self.api_key)
                self._notify("正在生成本视频的笔记…")
            except Exception as exc:
                self._complete(job, "生成失败", str(exc))

    def _generated(self, job, path, issue, folder):
        if not self.running or job is not self.jobs[self.index]:
            return
        job.session_dir = Path(folder)
        job.output_path = Path(path) if path else None
        state = "笔记已生成" if path else "已停止" if self.cancelled else "生成失败"
        self._complete(job, state, issue)

    def _complete(self, job, state, issue=None):
        job.state = state
        if issue:
            job.issue = "\n".join(filter(None, [job.issue, issue]))
        self._notify(state)
        self.dispatch(self._next)

    def cancel(self):
        if not self.running or self.cancelled:
            return
        self.cancelled = True
        self._notify("正在停止整批处理；已保存的素材和笔记会保留。")
        if self.current and self.current.importing:
            self.current.stop_recording("用户停止批量处理")
        elif self.current and self.current.generating:
            self.current.cancel_generation()
        elif not self.reviewing:
            self.dispatch(self._next)

    def _finish(self):
        if not self.running:
            return
        self.running = False
        if self.cancelled:
            for job in self.jobs:
                if job.state == "等待处理":
                    job.state = "未处理（批量已停止）"
        self._save_report()
        self.on_done(self)


def select_video_batch(parent, sources, *, screenshots=True, default_material_mode='转写＋截图'):
    from tkinter import BooleanVar, StringVar, Toplevel, filedialog, messagebox, ttk
    from video_import import timestamp
    from video_region import select_video_region, _scrollable_dialog

    jobs = [VideoJob(Path(source)) for source in dict.fromkeys(sources)]
    dialog = Toplevel(parent)
    dialog.title("批量导入视频 — 队列与处理方式")
    dialog.transient(parent)
    body, footer, bounds = _scrollable_dialog(dialog, parent, 960, 640)
    wrap = max(180, bounds[0] - 60)
    ttk.Label(body, text="按队列顺序处理，每个视频独立保存，以视频文件名命名。", font=("Microsoft YaHei UI", 12, "bold"), wraplength=wrap).pack(anchor="w")
    ttk.Label(body, text="默认处理完整视频和画面；选中一项可单独设置时间段及截图区域。", foreground="#555", wraplength=wrap).pack(anchor="w", pady=(4, 10))
    modes = {
        '截图＋转写并生成笔记': ('转写＋截图', True),
        '仅保存截图（不生成笔记）': ('仅截图', False),
        '仅转写音频（不生成笔记）': ('仅转写', False),
        '仅保存截图和转写（不生成笔记）': ('转写＋截图', False),
    }
    initial = {'仅截图': '仅保存截图（不生成笔记）', '仅转写': '仅转写音频（不生成笔记）'}.get(default_material_mode, next(iter(modes)))
    mode = StringVar(master=dialog, value=initial)
    mode_line = ttk.Frame(body)
    mode_line.pack(fill='x', pady=(0, 8))
    ttk.Label(mode_line, text='处理方式').pack(side='left', padx=(0, 8))
    ttk.Combobox(mode_line, name='processing_mode', textvariable=mode, values=list(modes), state='readonly', width=34).pack(side='left', fill='x', expand=True)

    def active_material():
        return modes[mode.get()][0]

    table = ttk.Frame(body)
    table.pack(fill="both", expand=True)
    table.columnconfigure(0, weight=1)
    table.rowconfigure(0, weight=1)
    tree = ttk.Treeview(table, columns=("video", "range", "region"), show="headings", selectmode="extended", height=6)
    for key, title, width in [("video", "视频文件", 400), ("range", "时间段", 200), ("region", "截图范围", 150)]:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=90)
    scrollbar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(table, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky='nsew')
    scrollbar.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')

    def render(selected=()):
        tree.delete(*tree.get_children())
        for index, job in enumerate(jobs):
            selection = job.selection
            first, last = selection.get("start_seconds", 0), selection.get("end_seconds")
            span = f"{timestamp(first)} → {timestamp(last) if last is not None else '视频结束'}"
            region = selection.get("capture_region")
            tree.insert("", "end", iid=str(index), values=(f"{index + 1}. {job.source.name}", span,
                        '仅音轨' if active_material() == '仅转写' else f"{region[2]}×{region[3]} 选区" if region else "完整画面"))
        tree.selection_set([str(i) for i in selected if i < len(jobs)])

    actions = ttk.Frame(body)
    actions.pack(fill="x", pady=8)

    def add():
        known = {str(job.source.resolve()).casefold() for job in jobs}
        for source in filedialog.askopenfilenames(title="添加视频到批量队列", filetypes=VIDEO_FILETYPES, parent=dialog):
            path = Path(source)
            key = str(path.resolve()).casefold()
            if key not in known:
                jobs.append(VideoJob(path))
                known.add(key)
        render()

    def remove():
        for index in sorted((int(i) for i in tree.selection()), reverse=True):
            del jobs[index]
        render()

    def move(offset):
        selected = sorted(int(i) for i in tree.selection())
        if len(selected) != 1:
            return
        index, target = selected[0], selected[0] + offset
        if 0 <= target < len(jobs):
            jobs[index], jobs[target] = jobs[target], jobs[index]
            render([target])

    def configure():
        selected = tree.selection()
        if len(selected) != 1:
            messagebox.showinfo("选择一个视频", "请先选中一个视频，再设置时间段和截图区域。", parent=dialog)
            return
        index = int(selected[0])
        try:
            selection = select_video_region(dialog, jobs[index].source, screenshots=active_material() != '仅转写',
                                             initial_selection=jobs[index].selection)
            if selection is not None:
                jobs[index].selection = selection
                render([index])
        except Exception as exc:
            messagebox.showerror("无法预览此视频", str(exc), parent=dialog)
        finally:
            if dialog.winfo_exists():
                dialog.grab_set()

    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform='queue_actions')
    for index, (title, callback) in enumerate([("添加视频…", add), ("移除所选", remove), ("上移", lambda: move(-1)),
                            ("下移", lambda: move(1)), ("设置时间 / 框选…", configure)]):
        row, column = divmod(index, 3)
        ttk.Button(actions, text=title, command=callback).grid(row=row, column=column,
            columnspan=2 if index == 4 else 1, sticky='ew', padx=(0, 6), pady=3)
    tree.bind("<Double-1>", lambda _event: configure())
    generate = BooleanVar(master=dialog, value=modes[initial][1])
    review = BooleanVar(master=dialog, value=False)
    ttk.Checkbutton(body, text="导入后自动生成笔记（按当前 AI 设置提交截图和转写）", variable=generate).pack(anchor="w", pady=(6, 2))
    review_check = ttk.Checkbutton(body, text="每个视频生成前暂停，逐个检查截图（需要手动继续）", variable=review)
    review_check.pack(anchor="w", pady=2)
    updating = False

    def sync_mode(*_):
        nonlocal updating
        if updating:
            return
        updating = True
        try:
            material, auto_generate = modes[mode.get()]
            generate.set(auto_generate)
            if not auto_generate or material == '仅转写':
                review.set(False)
            review_check.state(['!disabled'] if auto_generate and material != '仅转写' else ['disabled'])
            render()
        finally:
            updating = False

    def sync_generate(*_):
        nonlocal updating
        if updating:
            return
        mode.set(next(iter(modes)) if generate.get() else '仅保存截图和转写（不生成笔记）')

    mode.trace_add('write', sync_mode)
    generate.trace_add('write', sync_generate)
    ttk.Label(body, text="自动过滤沿用主界面的差异比例。素材过多时自动分组生成；单个视频失败会保留素材并继续下一项。\n取消勾选自动生成时，只导入素材，稍后可在主界面选择已有记录生成。",
              wraplength=wrap, foreground="#555").pack(anchor="w", pady=8)
    result = None

    def accept():
        nonlocal result
        if not jobs:
            messagebox.showinfo("队列为空", "请至少添加一个视频。", parent=dialog)
            return
        material, auto_generate = modes[mode.get()]
        result = {"jobs": jobs, "generate": bool(auto_generate and generate.get()),
                  "review": bool(auto_generate and generate.get() and review.get()), 'material_mode': material}
        dialog.destroy()

    ttk.Button(footer, text="开始批量处理", command=accept).pack(side="right")
    ttk.Button(footer, text="取消", command=dialog.destroy).pack(side="right", padx=8)
    sync_mode()
    dialog.bind('<Return>', lambda _event: accept())
    dialog.bind('<Escape>', lambda _event: dialog.destroy())
    dialog.grab_set()
    parent.wait_window(dialog)
    return result
