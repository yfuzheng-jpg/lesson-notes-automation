"""Local video material preparation, without playback or a system FFmpeg install."""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Callable


class VideoImportCancelled(RuntimeError):
    pass


def timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class VideoImporter:
    def __init__(self, source: Path, settings, session_dir: Path,
                 status: Callable[[str], None], stop: threading.Event,
                 pause: threading.Event, model_factory=None, capture_region=None, frame_size=None,
                 start_seconds: float = 0, end_seconds: float | None = None):
        self.source = Path(source)
        self.settings = settings
        self.session_dir = Path(session_dir)
        self.status = status
        self.stop = stop
        self.pause = pause
        self.model_factory = model_factory
        self.capture_region = capture_region
        self.frame_size = frame_size
        self.start_seconds = start_seconds
        self.end_seconds = end_seconds
        self.frames: list[dict] = []
        self.metadata: dict = {}

    def gate(self) -> None:
        while self.pause.is_set():
            if self.stop.wait(0.2):
                raise VideoImportCancelled("视频导入已取消；已保存的截图和转写可以检查后生成，或重新导入完整视频。")
        if self.stop.is_set():
            raise VideoImportCancelled("视频导入已取消；已保存的截图和转写可以检查后生成，或重新导入完整视频。")

    def probe(self) -> dict:
        import av

        if not self.source.is_file():
            raise ValueError("所选视频文件不存在。")
        with av.open(str(self.source)) as container:
            if not container.streams.video:
                raise ValueError("文件中没有可读取的视频轨道，请选择视频文件。")
            video = container.streams.video[0]
            duration = float(container.duration / av.time_base) if container.duration else (
                float(video.duration * video.time_base) if video.duration is not None else 0.0
            )
            origin = float(container.start_time / av.time_base) if container.start_time is not None else (
                float(video.start_time * video.time_base) if video.start_time is not None else 0.0
            )
            from video_region import validate_time_range
            self.start_seconds, self.end_seconds = validate_time_range(
                self.start_seconds, self.end_seconds, duration)
            stat = self.source.stat()
            if self.capture_region is not None:
                from video_region import validate_region
                if self.frame_size and list(self.frame_size) != [video.width, video.height]:
                    raise ValueError("视频尺寸已改变，请重新导入并框选。")
                self.capture_region = validate_region(self.capture_region, (video.width, video.height))
            self.metadata = {
                "source_type": "video_file", "source_path": str(self.source.resolve()),
                "source_size": stat.st_size, "source_modified_ns": stat.st_mtime_ns,
                "duration_seconds": duration, "timeline_origin_seconds": origin,
                "start_seconds": self.start_seconds, "end_seconds": self.end_seconds,
                "selected_duration_seconds": (self.end_seconds - self.start_seconds if self.end_seconds is not None
                                              else max(0, duration - self.start_seconds) if duration else None),
                "width": video.width, "height": video.height,
                "capture_region": self.capture_region,
                "frame_size": [video.width, video.height],
                "has_audio": bool(container.streams.audio), "warnings": [],
                "screenshots_complete": False, "transcript_complete": False,
            }
        return self.metadata

    def progress(self, stage: str, seconds: float) -> None:
        end = self.end_seconds if self.end_seconds is not None else self.metadata.get("duration_seconds", 0)
        total = f" → {timestamp(end)}" if end else " → 视频结束"
        self.status(f"{stage}：原视频 {timestamp(seconds)}（所选范围 {timestamp(self.start_seconds)}{total}）")

    def seek_start(self, container, stream) -> bool:
        """Seek backward to a keyframe; decoding gates below discard all preroll."""
        if self.start_seconds <= 0 or stream.start_time is None or not stream.time_base:
            return False
        import av
        # Audio demuxers may seek to the packet just after the requested instant.
        # Decode a short preroll for codec priming/resampling, then slice exact samples.
        seek_seconds = max(0, self.start_seconds - 1) if stream.type == "audio" else self.start_seconds
        offset = (self.metadata["timeline_origin_seconds"] + seek_seconds) / float(stream.time_base)
        try:
            container.seek(int(offset), stream=stream, backward=True)
            return True
        except (av.error.FFmpegError, OSError):
            # Some local containers have no usable index. Decode from the start instead.
            container.seek(int(self.metadata["timeline_origin_seconds"] * av.time_base), backward=True)
            return False

    def run(self) -> dict:
        self.gate()
        self.probe()
        mode = self.settings.material_mode
        if mode == "仅转写" and not self.metadata["has_audio"]:
            raise ValueError("此视频没有音轨，无法使用‘仅转写’；请选择‘仅截图’或‘转写＋截图’后重新导入。")
        try:
            if mode != "仅转写":
                self.extract_screenshots()
                self.metadata["screenshots_complete"] = True
            self.gate()
            if mode != "仅截图":
                if self.metadata["has_audio"]:
                    self.transcribe_audio()
                    self.metadata["transcript_complete"] = True
                else:
                    self.metadata["warnings"].append("视频没有音轨，本次使用视频截图生成笔记。")
            self.gate()
            return self.metadata
        finally:
            self.save_frame_index()

    def save_frame_index(self) -> None:
        target = self.session_dir / "video_frames.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.frames, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def extract_screenshots(self) -> None:
        import av
        import numpy as np

        images_dir = self.session_dir / "screenshots"
        images_dir.mkdir(parents=True, exist_ok=True)
        reference = candidate = candidate_image = None
        candidate_since = candidate_time = next_sample = next_interval = 0.0
        last_report = -10.0
        sample_step = 0.5 if self.settings.capture_mode == "画面变化时" else max(
            1, int(self.settings.capture_interval_seconds))

        def difference(left, right):
            return float(np.mean(np.abs(left - right)))

        def save(image, seconds):
            name = f"frame_{len(self.frames) + 1:06d}_{round(seconds * 1000):010d}ms.png"
            image.save(images_dir / name)
            self.frames.append({"file": name, "seconds": round(seconds, 3), "timestamp": timestamp(seconds)})
            if len(self.frames) % 25 == 0:
                self.save_frame_index()

        with av.open(str(self.source)) as container:
            stream = container.streams.video[0]
            rate = float(stream.average_rate) if stream.average_rate else 25.0
            count = 0
            seeked = self.seek_start(container, stream)
            for frame in container.decode(stream):
                self.gate()
                if seeked and frame.time is None:
                    raise ValueError("此视频跳转后的画面缺少时间戳，无法可靠截取指定时间段。")
                seconds = max(0.0, float(frame.time) - self.metadata["timeline_origin_seconds"]) if frame.time is not None else count / rate
                count += 1
                if seconds < self.start_seconds:
                    continue
                if self.end_seconds is not None and seconds >= self.end_seconds:
                    break
                if seconds + 0.0001 < next_sample:
                    continue
                next_sample = seconds + sample_step
                # Crop before change detection as well as saving: motion outside the ROI is ignored.
                image = frame.to_image()
                if self.capture_region is not None:
                    if image.size != (self.metadata["width"], self.metadata["height"]):
                        raise ValueError("视频中途改变画面尺寸，无法继续使用固定选区。已保存的素材仍然保留。")
                    x, y, width, height = self.capture_region
                    image = image.crop((x, y, x + width, y + height))
                image.thumbnail((1920, 1080))
                thumbnail = np.asarray(image.convert("L").resize((160, 90)), dtype=np.int16)
                changed = reference is None or difference(thumbnail, reference) >= 3.5
                if self.settings.capture_mode == "定时截图":
                    if seconds + 0.0001 >= next_interval:
                        if not (self.settings.skip_duplicate_screenshots and not changed):
                            save(image, seconds)
                            reference = thumbnail.copy()
                        next_interval = seconds + sample_step
                elif reference is None:
                    save(image, seconds)
                    reference = thumbnail.copy()
                elif changed:
                    if candidate is None or difference(thumbnail, candidate) >= 1.4:
                        candidate = thumbnail.copy()
                        candidate_since = seconds
                    candidate_image = image
                    candidate_time = seconds
                    if seconds - candidate_since >= 0.9:
                        save(candidate_image, candidate_time)
                        reference = candidate.copy()
                        candidate = candidate_image = None
                else:
                    candidate = candidate_image = None
                if seconds - last_report >= 5:
                    self.progress(f"提取视频画面（已保存 {len(self.frames)} 张）", seconds)
                    last_report = seconds
            # Preserve a changed final page even when EOF arrives before its stability delay.
            if candidate_image is not None:
                save(candidate_image, candidate_time)
        if not self.frames:
            raise ValueError("所选时间段内未能读取任何视频画面，请检查起止时间或视频文件。")
        self.save_frame_index()

    def transcribe_audio(self) -> None:
        import av
        import numpy as np

        self.status("正在加载本地 Whisper 语音模型；首次运行可能需要下载模型…")
        self.gate()
        if self.model_factory is None:
            from faster_whisper import WhisperModel
            factory = WhisperModel
        else:
            factory = self.model_factory
        model = factory("base", device="cpu", compute_type="int8")
        self.gate()
        sample_rate = 16000
        chunk_samples = sample_rate * 30
        pending = np.empty(0, dtype=np.float32)
        consumed = 0
        audio_origin = None
        decode_origin = 0.0
        timeline_samples = 0
        reached_end = False
        detected_language = None
        decoded_samples = 0

        def transcribe(samples, offset):
            nonlocal detected_language
            self.gate()
            self.progress("正在转写视频音轨", offset)
            if np.max(np.abs(samples)) < 1e-5:
                return
            segments, info = model.transcribe(samples, language=detected_language,
                                              vad_filter=True, beam_size=3)
            lines = []
            for segment in segments:
                self.gate()
                text = segment.text.strip()
                if text:
                    first = max(self.start_seconds, offset, offset + segment.start)
                    last = min(offset + len(samples) / sample_rate, offset + segment.end)
                    if self.end_seconds is not None:
                        last = min(last, self.end_seconds)
                    if last > first:
                        lines.append(f"[{timestamp(first)} - {timestamp(last)}] {text}\n")
            self.gate()
            if lines:
                detected_language = getattr(info, "language", detected_language)
                with (self.session_dir / "transcript.txt").open("a", encoding="utf-8") as target:
                    target.writelines(lines)

        with av.open(str(self.source)) as container:
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
            seeked = self.seek_start(container, stream)

            def append_frames(frames):
                nonlocal pending, consumed, decoded_samples, timeline_samples, audio_origin, reached_end
                for output in frames:
                    self.gate()
                    data = output.to_ndarray().reshape(-1).astype(np.float32, copy=False)
                    frame_start = decode_origin + timeline_samples / sample_rate
                    timeline_samples += data.size
                    # Slice actual PCM samples, not just transcript timestamps, before inference.
                    left = max(0, math.ceil((self.start_seconds - frame_start) * sample_rate - 1e-6))
                    right = data.size
                    if self.end_seconds is not None:
                        right = min(right, math.ceil((self.end_seconds - frame_start) * sample_rate - 1e-6))
                        reached_end = frame_start + data.size / sample_rate >= self.end_seconds
                    if right <= left:
                        continue
                    if audio_origin is None:
                        audio_origin = frame_start + left / sample_rate
                    data = data[left:right]
                    decoded_samples += data.size
                    pending = np.concatenate((pending, data))
                    while pending.size >= chunk_samples:
                        transcribe(pending[:chunk_samples], audio_origin + consumed / sample_rate)
                        pending = pending[chunk_samples:].copy()
                        consumed += chunk_samples

            first_frame = True
            for frame in container.decode(stream):
                self.gate()
                if first_frame:
                    if seeked and frame.time is None:
                        raise ValueError("此视频跳转后的音频缺少时间戳，无法可靠转写指定时间段。")
                    decode_origin = (float(frame.time) - self.metadata["timeline_origin_seconds"]
                                     if frame.time is not None else 0.0)
                    first_frame = False
                # Some containers have discontinuous audio PTS; use decoded sample counts.
                frame.pts = None
                append_frames(resampler.resample(frame))
                if reached_end:
                    break
            append_frames(resampler.resample(None))
            if pending.size:
                transcribe(pending, audio_origin + consumed / sample_rate)
        if not decoded_samples:
            raise ValueError("所选时间段内没有可读取的音频，请检查起止时间或改用‘仅截图’。")
        self.progress("视频音轨转写完成", audio_origin + decoded_samples / sample_rate)
