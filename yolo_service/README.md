# StroyKontrol YOLO tracking service

Independent FastAPI service for the supplied `yolo26m_multiscale_best.pt`. It discovers enabled cameras from the backend, continuously reads RTSP even with no browser, runs one shared Ultralytics model with isolated per-camera ByteTrack/BoT-SORT state, broadcasts processed-frame boxes, and pushes snapshots and visit events.

## Run with the StroyKontrol backend

From the repository root, create the ignored `yolo_service/.env` and set the
shared key and absolute host path to the supplied checkpoint:

```sh
SK_TRACKER_API_KEY='replace-with-a-private-shared-secret'
YOLO_MODEL_HOST_PATH=/absolute/path/to/yolo26m_multiscale_best.pt
```

Add those settings to `yolo_service/.env`; the Compose commands below load that
file. `SK_TRACKER_API_KEY` must match the backend setting.

The backend and service then share Compose's default network. The service is
reachable as `yolo-tracker:8200`, contacts `http://backend:8100`, and reads
camera RTSP streams from the backend's MediaMTX service (`video:8554`).
The backend must use the same key and `SK_ANALYSIS_PROVIDER=push`.

CPU:

```sh
SK_TRACKER_URL=ws://yolo-tracker:8200/stream SK_ANALYSIS_PROVIDER=push \
  docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  --profile cpu up --build
```

NVIDIA GPU (requires Docker NVIDIA GPU support). Use this instead of the CPU
command; do not enable both `cpu` and `gpu` profiles, since they publish the
same port and hostname:

```sh
SK_TRACKER_URL=ws://yolo-tracker:8200/stream SK_ANALYSIS_PROVIDER=push \
  docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  --profile gpu up --build
```

The GPU service uses Docker's registered `nvidia` runtime with
`compute,utility` capabilities; `gpus: all` failed CUDA initialization on the
tested Docker host. When switching an already-running CPU stack, stop just the
CPU tracker first, then start the GPU service without deleting volumes:

```sh
docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  --profile cpu stop yolo-tracker-cpu
SK_TRACKER_URL=ws://yolo-tracker:8200/stream SK_ANALYSIS_PROVIDER=push \
  docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  --profile gpu up -d --no-deps --build yolo-tracker-gpu
```

Compose resolves paths in both files relative to the first file's directory
(`Hakaton-main`): the YOLO build context, `.env`, and checkpoint paths above
are set up for this command. The RTSP stream URL is generated inside the
backend as `rtsp://video:8554/...`; do not use a host-only RTSP address for
this combined stack.

Set `YOLO_HOST_PORT` (default `8200`) to change the host-side port if needed;
the inter-container hostname and port remain `yolo-tracker:8200`.

`/health` is liveness; `/ready` returns 503 until the checkpoint loaded and at least one camera discovery has succeeded. `GET /status` requires `X-Api-Key`. `WS /stream` requires `Authorization: Bearer <SK_TRACKER_API_KEY>`; bad credentials close with 4401.

## Camera lifecycle control

The backend is the only desired-state source. It sends the camera ID only; this
service looks up the enabled camera and its connection data from
`GET /api/tracker/cameras` at `BACKEND_URL`, using `SK_TRACKER_API_KEY`. No
control request can supply an RTSP URL.

All control routes require the same `X-Api-Key` with a constant-time comparison:

```sh
curl -X POST http://localhost:8200/cameras/start \
  -H "X-Api-Key: $SK_TRACKER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"camera_id":"cam-123","request_id":"b2c5c45b-74f3-4c48-9e35-7eceb3a50647"}'
curl -X POST http://localhost:8200/cameras/stop \
  -H "X-Api-Key: $SK_TRACKER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"camera_id":"cam-123"}'
curl -X POST http://localhost:8200/cameras/restart \
  -H "X-Api-Key: $SK_TRACKER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"camera_id":"cam-123"}'
curl -H "X-Api-Key: $SK_TRACKER_API_KEY" \
  'http://localhost:8200/cameras/status?camera_id=cam-123'
```

`START` and `RESTART` reply with `camera_id`, `operation`, `accepted`,
`status`, and `already_running`. `accepted` means the request was serialized
and the worker was scheduled; only status `running` means frames have actually
been inferred. Repeating START for an unchanged worker sets
`already_running: true`. STOP is safe for a deleted or unknown local camera.
The periodic full-list reconcile is controlled by `CAMERA_RECONCILE_SECONDS`;
an unavailable or invalid backend response leaves current workers untouched.
If `MAX_CAMERAS` is exhausted, additional authorized cameras appear as
`waiting_capacity`, rather than being silently discarded.

`GET /cameras/status` reports worker session, last frame and detection times,
frame age, processed FPS, inference latency, confirmed-track count,
reconnect count, and safe last-error/interruption fields. The legacy protected
`GET /status` remains available.

## Transport

Each processed frame sends this JSON to all authenticated `/stream` clients (including `objects: []`):

```json
{"camera_id":"camera-001","ts":"2026-09-26T09:00:00.120Z","frame_w":1920,"frame_h":1080,"objects":[{"track_id":"s-550e8400-e29b-41d4-a716-446655440000:17","type":"excavator","confidence":0.94,"box":{"x":24.5,"y":41.5,"w":18.5,"h":27.0}}]}
```

Boxes are clamped percentages of the original camera image, with top-left `x/y` and width/height `w/h`; they are never letterbox coordinates, pixels, centers, or fractions. `ts` is the UTC receipt time of the RTSP frame, not send time.

Snapshots go to `POST /api/ingest/snapshots` as multipart `camera_id`, UTC `taken_at`, `model`, JSON `detections` (`type`, `confidence`, `box` only), and JPEG `image`. A snapshot is marked sent only for HTTP 202 and `{"accepted": true}`; it reuses the processed frame and will not send a stale timestamp. These snapshots feed the backend's retained analysis history and plan checks; they are not the source of the live-video overlay, which receives only `/stream` WebSocket tracks.

Visit events are first made durable in this service's SQLite outbox and delivered to `POST /api/ingest/equipment-events` with `X-Api-Key` (`SK_TRACKER_API_KEY` or backend `SK_INGEST_API_KEY`). Backend PostgreSQL stores only appeared/disappeared boundaries and camera/session-scoped observed visits; `PRESENT` updates the last detected time without a journal row. Missing legacy `confidence` remains null. A short absence inside the configured grace remains one visit; interrupted camera observation and backend timeout close it as **lost**, never as a confirmed departure. Backend timeout (`SK_EQUIPMENT_OBSERVATION_TIMEOUT_SECONDS`, default 90) must exceed the tracker heartbeat plus grace. The backend journal is independent of the browser's live overlay and the hourly `equipment_usage` aggregate; the outbox remains only a delivery queue.

## Taxonomy

`class_mapping.yolo26m-multiscale-v1.json` locks the exact taxonomy of
`yolo26m_multiscale_best.pt` (mapping version `yolo26m-multiscale-v1`):

| output ID | checkpoint name | emitted site type |
|---:|---|---|
| 0 | `asphalt_paver` | `null` |
| 1 | `backhoe_loader` | `null` |
| 2 | `bulldozer` | `bulldozer` |
| 3 | `concrete_mixer` | `mixer` |
| 4 | `concrete_mixer_truck` | `mixer` |
| 5 | `concrete_pump_truck` | `null` |
| 6 | `concrete_vibrator` | `null` |
| 7 | `crane` | `crane` |
| 8 | `dump_truck` | `dump_truck` |
| 9 | `excavator` | `excavator` |
| 10 | `forklift` | `null` |
| 11 | `hydraulic_cropper` | `null` |
| 12 | `jack_hammer` | `null` |
| 13 | `loader` | `null` |
| 14 | `mobile_crane` | `crane` |
| 15 | `motor_grader` | `null` |
| 16 | `pile_driver` | `null` |
| 17 | `road_roller` | `roller` |
| 18 | `skid_steer_loader` | `null` |
| 19 | `tanker_truck` | `truck` |
| 20 | `telehandler` | `null` |
| 21 | `tower_crane` | `crane` |
| 22 | `tractor` | `null` |
| 23 | `trailer` | `null` |
| 24 | `truck` | `truck` |
| 25 | `truck_mounted_crane` | `manipulator` |
| 26 | `wheel_loader` | `null` |

The only emitted values are the site-supported `excavator`, `dump_truck`,
`roller`, `manipulator`, `mixer`, `bulldozer`, `truck`, and `crane`. The
non-null entries are exact or subtype matches; `truck_mounted_crane` is the
site's `manipulator` type. Every unsupported class is explicitly `null` in the
mapping, rather than being coerced into a nearby type. Loading fails readiness
if checkpoint IDs or names differ.

## Limits and operational behavior

- `YOLO_TARGET_FPS=5`, `CAMERA_RECONCILE_SECONDS=30`, `SNAPSHOT_INTERVAL_SECONDS=45` are configurable targets, not throughput guarantees.
- A per-camera decoder thread keeps a queue of one latest frame and records its receipt timestamp; slow inference drops stale decoded frames rather than accumulating latency. It opens only through OpenCV FFmpeg with open/read timeout parameters; shutdown requests capture stop and waits at most 2.2 seconds for its daemon decoder thread.
- The model loads once per process. Inference is serialized because a single model instance is shared; camera decoder/reconnect and HTTP delivery do not block the event loop.
- Each camera has a full UUID session and independent tracker state. Confirmation needs `TRACK_CONFIRM_FRAMES` frames; `LOST_GRACE_SECONDS` absorbs brief occlusions. There is no cross-camera ReID and a track is a visual observation, not evidence of engine operation, entry/exit, or time outside view.
- RTSP and backend failures use bounded exponential reconnect; discovery failure retains current workers. The SQLite outbox is bounded by `OUTBOX_MAX_EVENTS`; overflow logs an explicit error rather than silently pretending delivery.

## Verification

After starting either Compose profile, check the tracker from the host:

```sh
# Export the same SK_TRACKER_API_KEY that is set in yolo_service/.env.
export YOLO_URL="http://localhost:${YOLO_HOST_PORT:-8200}"
curl -fsS "$YOLO_URL/ready"
curl -fsS -H "X-Api-Key: $SK_TRACKER_API_KEY" "$YOLO_URL/status"
```

`/ready` needs no key, but returns HTTP 503 until both the checkpoint and an
initial backend camera discovery are ready. `/status` requires the shared key;
its camera entries should show the enabled camera's worker, recent frame and
detection times, and confirmed tracks. A successful status response alone does
not prove a camera is producing detections.

To exercise discovery with a supplied clip, sign in to the web UI at
`http://localhost:${WEB_PORT:-8080}` as a user allowed to add cameras. In
**Add camera**, select a **Demo clip** preset, choose its zone, and save. The UI
must receive `201 Created` from `POST /api/cameras`; wait for the next discovery
interval, then run the commands above. Do not use the standalone mock tracker
or clip annotations as proof of integrated YOLO detections.

With an admin or manager bearer token, verify that the backend is relaying the
external tracker:

```sh
curl -fsS -H "Authorization: Bearer $SK_USER_TOKEN" \
  "http://localhost:${API_PORT:-8100}/api/tracker/status"
```

Look for `source: "service"`, `connected: true`, and increasing `messages`.
The camera workers and their frame timestamps are reported by YOLO's `/status`,
not by the backend's `liveCameras` field (that field describes its local model).

For the same hourly aggregates through the authenticated backend API, use a
site ID from the web UI (for example, `s1` in demo mode):

```sh
curl -fsS -H "Authorization: Bearer $SK_USER_TOKEN" \
  "http://localhost:${API_PORT:-8100}/api/sites/s1/equipment-usage?hours=24"
```

Check persisted hourly usage in the Compose PostgreSQL database (this example
uses the current demo database credentials):

```sh
docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  exec db psql -U stroykontrol -d stroykontrol -c \
  'SELECT camera_id, hour, equipment_type, max_count,
          round((present_s / 60)::numeric, 2) AS present_min,
          round((moving_s / 60)::numeric, 2) AS moving_min
   FROM equipment_usage ORDER BY hour DESC, camera_id LIMIT 20;'
```

`equipment_usage` is updated about once per minute. To distinguish real
multiscale-model snapshots from seeded/mock history, query by provider:

```sh
docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  exec db psql -U stroykontrol -d stroykontrol -c \
  "SELECT camera_id, count(*), max(taken_at) FROM snapshots
   WHERE provider = 'yolo26m-multiscale-v1' GROUP BY camera_id;"
```

For the latest model-only boxes and confidences, join detections to their
snapshot:

```sh
docker compose --env-file yolo_service/.env \
  -f Hakaton-main/docker-compose.yml -f yolo_service/compose.yml \
  exec db psql -U stroykontrol -d stroykontrol -c \
  "SELECT s.camera_id, s.taken_at, d.equipment_type,
          round(d.confidence::numeric, 3) AS confidence, d.x, d.y, d.w, d.h
   FROM detections d JOIN snapshots s ON s.id = d.snapshot_id
   WHERE s.provider = 'yolo26m-multiscale-v1'
   ORDER BY s.taken_at DESC LIMIT 20;"
```

Snapshot metadata and detections live in PostgreSQL (`snapshots`, `detections`);
JPEGs are on the backend `media` volume at `/app/data/media/frames/`. Existing
seed records with `provider = 'mock'` are not YOLO detections.

The usage rows are accumulated from live tracks, not from snapshot detections
or the unavailable event-ingestion route. Their presence is not evidence of
engine operation, entry/exit, or cross-camera identity; one vehicle visible to
two cameras must not have its minutes added together.

For service unit tests:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

The former CPU/GPU measurements and video smokes covered the retired
`MOCS_yolo26m.pt` checkpoint. They are historical only and are intentionally
not presented as verification of `yolo26m_multiscale_best.pt`; rerun the
verification command above after deploying this checkpoint.
