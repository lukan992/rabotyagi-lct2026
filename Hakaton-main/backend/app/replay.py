"""Publish one mounted recording as a continuously looping, scoped RTSP stream."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import signal
import subprocess

from app.services import video

log = logging.getLogger("stroykontrol.replay")
_COPY_PROFILES = frozenset(("Baseline", "Constrained Baseline"))


class ReplayError(Exception):
    """The standalone publisher cannot safely start."""


@dataclass(frozen=True)
class ReplayConfig:
    source: Path
    path: str
    destination: str


def _config_from_environment() -> ReplayConfig:
    source_value = os.environ.get("REPLAY_FILE", "")
    path = os.environ.get("REPLAY_PATH", "")
    try:
        source = Path(source_value)
        valid_source = bool(source_value) and source.is_file()
        destination = video.replay_rtsp_url(path)
    except (OSError, ValueError) as exc:
        raise ReplayError from exc
    if not valid_source or not video.is_replay_path(path):
        raise ReplayError
    return ReplayConfig(source=source, path=path, destination=destination)


def _video_stream(ffprobe: str, source: Path) -> dict[str, object]:
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,profile,pix_fmt,has_b_frames",
                "-of",
                "json",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ReplayError from exc
    if result.returncode:
        raise ReplayError
    try:
        streams = json.loads(result.stdout).get("streams", [])
        stream = streams[0]
    except (IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ReplayError from exc
    if not isinstance(stream, dict):
        raise ReplayError
    return stream


def _can_copy(stream: dict[str, object]) -> bool:
    try:
        has_b_frames = int(str(stream.get("has_b_frames", "")))
    except ValueError:
        return False
    return (
        stream.get("codec_name") == "h264"
        and stream.get("profile") in _COPY_PROFILES
        and stream.get("pix_fmt") == "yuv420p"
        and has_b_frames == 0
    )


def _input_description(stream: dict[str, object]) -> str:
    codec = stream.get("codec_name")
    profile = stream.get("profile")
    return video.redact(
        f"{codec if isinstance(codec, str) else 'unknown'} / {profile if isinstance(profile, str) else 'unknown'}"
    )


async def _stderr_detail(stderr: asyncio.StreamReader) -> str:
    maximum_bytes = 16 * 1024
    chunks: list[bytes] = []
    size = 0
    while chunk := await stderr.read(1024):
        size += len(chunk)
        if size <= maximum_bytes:
            chunks.append(chunk)
        else:
            chunks.clear()
    if size > maximum_bytes:
        return "ffmpeg error output exceeded the safe diagnostic limit"
    detail = " ".join(video.redact(b"".join(chunks).decode(errors="replace")).split())
    return detail[-1024:] or "ffmpeg did not report an error detail"


def _ffmpeg_command(ffmpeg: str, config: ReplayConfig, *, copy_video: bool) -> list[str]:
    command = [
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "error",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(config.source),
        "-map",
        "0:v:0",
        "-an",
    ]
    if copy_video:
        command.extend(("-c:v", "copy"))
    else:
        command.extend(
            (
                "-c:v",
                "libx264",
                "-vf",
                "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
                "-profile:v",
                "baseline",
                "-level:v",
                "3.1",
                "-pix_fmt",
                "yuv420p",
                "-bf",
                "0",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
            )
        )
    command.extend(("-f", "rtsp", "-rtsp_transport", "tcp", config.destination))
    return command


async def _run_ffmpeg(command: list[str], input_description: str) -> int:
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    handled_signals = (signal.SIGTERM, signal.SIGINT)
    for signum in handled_signals:
        loop.add_signal_handler(signum, stopping.set)

    wait_task: asyncio.Task[int] | None = None
    stderr_task: asyncio.Task[str] | None = None
    stop_task = asyncio.create_task(stopping.wait())
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        wait_task = asyncio.create_task(process.wait())
        stderr_task = asyncio.create_task(_stderr_detail(process.stderr))
        done, _ = await asyncio.wait((wait_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            log.info("Stopping local RTSP replay")
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=10)
            except TimeoutError:
                process.kill()
                await wait_task
            await stderr_task
            return 0
        code = wait_task.result()
        detail = await stderr_task
        if code:
            log.error("RTSP replay publisher stopped with exit status %s (%s): %s", code, input_description, detail)
        return code
    finally:
        stop_task.cancel()
        if wait_task is not None and not wait_task.done():
            wait_task.cancel()
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
        for signum in handled_signals:
            loop.remove_signal_handler(signum)


def main() -> int:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        log.error("RTSP replay requires ffprobe and ffmpeg")
        return 2
    try:
        config = _config_from_environment()
        stream = _video_stream(ffprobe, config.source)
        copy_video = _can_copy(stream)
    except ReplayError:
        log.error("RTSP replay configuration or input is invalid")
        return 2

    log.info("Starting local RTSP replay (%s)", "stream copy" if copy_video else "H.264 constrained-baseline transcode")
    try:
        return asyncio.run(
            _run_ffmpeg(_ffmpeg_command(ffmpeg, config, copy_video=copy_video), _input_description(stream))
        )
    except OSError:
        log.error("RTSP replay publisher could not start")
        return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
