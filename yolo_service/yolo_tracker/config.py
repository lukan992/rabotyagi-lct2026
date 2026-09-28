from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    model_path: Path
    backend_url: str
    api_key: str
    device: str
    conf_threshold: float
    imgsz: int
    tracker: str
    target_fps: float
    camera_reconcile_seconds: float
    snapshot_interval_seconds: float
    event_heartbeat_seconds: float
    lost_grace_seconds: float
    confirm_frames: int
    max_cameras: int
    connect_timeout_seconds: float
    request_timeout_seconds: float
    reconnect_max_seconds: float
    outbox_max_events: int
    outbox_path: Path
    mapping_path: Path
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parent.parent
        get = os.environ.get
        tracker = get("YOLO_TRACKER", "bytetrack").lower()
        if tracker not in {"bytetrack", "botsort"}:
            raise ValueError("YOLO_TRACKER must be bytetrack or botsort")
        device = get("YOLO_DEVICE", "auto")
        if device == "auto":
            device = ""  # Ultralytics chooses CUDA when available, otherwise CPU.
        api_key = get("SK_TRACKER_API_KEY", "")
        if not api_key:
            raise ValueError("SK_TRACKER_API_KEY is required")
        return cls(
            model_path=Path(get("YOLO_MODEL_PATH", "/models/yolo26m_multiscale_best.pt")),
            backend_url=get("BACKEND_URL", "http://backend:8100").rstrip("/"),
            api_key=api_key,
            device=device,
            conf_threshold=float(get("YOLO_CONF_THRESHOLD", "0.20")),
            imgsz=int(get("YOLO_IMGSZ", "960")),
            tracker=tracker,
            target_fps=float(get("YOLO_TARGET_FPS", "5")),
            camera_reconcile_seconds=float(get("CAMERA_RECONCILE_SECONDS", get("CAMERA_REFRESH_SECONDS", "30"))),
            snapshot_interval_seconds=float(get("SNAPSHOT_INTERVAL_SECONDS", "45")),
            event_heartbeat_seconds=float(get("EVENT_HEARTBEAT_SECONDS", "30")),
            lost_grace_seconds=float(get("LOST_GRACE_SECONDS", "8")),
            confirm_frames=int(get("TRACK_CONFIRM_FRAMES", "2")),
            max_cameras=int(get("MAX_CAMERAS", "16")),
            connect_timeout_seconds=float(get("CAMERA_CONNECT_TIMEOUT_SECONDS", "10")),
            request_timeout_seconds=float(get("REQUEST_TIMEOUT_SECONDS", "10")),
            reconnect_max_seconds=float(get("RECONNECT_MAX_SECONDS", "30")),
            outbox_max_events=int(get("OUTBOX_MAX_EVENTS", "10000")),
            outbox_path=Path(get("OUTBOX_PATH", str(root / "data" / "outbox.sqlite3"))),
            mapping_path=Path(get("YOLO_CLASS_MAPPING", str(root / "class_mapping.yolo26m-multiscale-v1.json"))),
            host=get("YOLO_HOST", "0.0.0.0"),
            port=int(get("YOLO_PORT", "8200")),
        )
