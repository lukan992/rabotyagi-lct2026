#!/usr/bin/env bash
# Start the real analytics receiver before the site and exactly one YOLO tracker.
# All required credentials stay in ignored env files or the caller environment.
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE_DIR="$ROOT_DIR/service-release_1/service-release"
RELEASE_ENV="$RELEASE_DIR/.env"
RELEASE_COMPOSE="$RELEASE_DIR/compose.yaml"
VLM_OVERLAY_DIR="$ROOT_DIR/analytics_service_05"
VLM_OVERLAY_COMPOSE="$VLM_OVERLAY_DIR/compose.override.yaml"
VLM_DERIVATIVE_IMAGE=lct-analytics-vlm-llm:0.5.0-spider
RELEASE_CHECKSUMS="$RELEASE_DIR/SHA256SUMS"
RELEASE_MANIFEST="$RELEASE_DIR/manifest.json"
RELEASE_IMAGE_TAR="$RELEASE_DIR/images/analytics-images.tar"
RELEASE_PROJECT=lct-analytics-release
SITE_PROJECT=hakaton-main
SITE_COMPOSE="$ROOT_DIR/Hakaton-main/docker-compose.yml"
SITE_ANALYTICS_NETWORK_COMPOSE="$ROOT_DIR/Hakaton-main/analytics-network.compose.yaml"
SITE_ENV="$ROOT_DIR/Hakaton-main/.env"
YOLO_ENV="$ROOT_DIR/yolo_service/.env"
YOLO_COMPOSE="$ROOT_DIR/yolo_service/compose.yml"

CHECK_ONLY=false
TRACKER_PROFILE=gpu

usage() {
  cat <<'USAGE'
Usage: ./start-all.sh [--gpu | --cpu] [--check]

Starts the analytics release first, then the site and one external YOLO tracker.
GPU is the default. --check validates prerequisites and Compose configuration only;
it never loads images or creates/starts containers.
USAGE
}

while (($#)); do
  case "$1" in
    --gpu) TRACKER_PROFILE=gpu ;;
    --cpu) TRACKER_PROFILE=cpu ;;
    --check) CHECK_ONLY=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

die() {
  printf 'start-all: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path=$1 label=$2
  [[ -f "$path" ]] || die "missing $label: ${path#$ROOT_DIR/}"
  [[ -r "$path" ]] || die "cannot read $label: ${path#$ROOT_DIR/}"
}

verify_release_checksums() {
  (
    cd "$RELEASE_DIR"
    sha256sum --check --status --strict SHA256SUMS
  ) || die 'supplied analytics release SHA-256 verification failed'
}

# The checksum set authenticates the manifest and archive as one supplied release.
# Derive the accepted ID chain from both checked artifacts: Docker may report the
# OCI index, platform manifest, or config digest depending on its image store.
read_release_image_chains() {
  local output tag index_id manifest_id config_id platform
  if ! output=$(python3 -c '
import hashlib
import json
import sys
import tarfile

release_path, archive_path = sys.argv[1:]
release = json.load(open(release_path, encoding="utf-8"))
if release.get("release_version") != "0.5.0" or release.get("platform") != "linux/amd64":
    raise SystemExit("expected analytics release 0.5.0 for linux/amd64")
expected_tags = {
    "lct-analytics-deterministic:0.5.0",
    "lct-analytics-vlm-llm:0.5.0",
}
release_images = {image.get("tag"): image for image in release.get("images", [])}
if not expected_tags.issubset(release_images):
    raise SystemExit("manifest does not contain both analytics runtime images")

def fail(message):
    raise SystemExit(message)

def read_blob(archive, digest):
    algorithm, separator, hex_digest = digest.partition(":")
    if algorithm != "sha256" or not separator or len(hex_digest) != 64:
        fail("invalid OCI digest")
    member = "blobs/sha256/" + hex_digest
    try:
        handle = archive.extractfile(member)
    except KeyError:
        handle = None
    if handle is None:
        fail("OCI blob is missing")
    content = handle.read()
    if hashlib.sha256(content).hexdigest() != hex_digest:
        fail("OCI blob digest mismatch")
    return content

with tarfile.open(archive_path, "r") as archive:
    root = json.loads(archive.extractfile("index.json").read())
    docker_manifest = json.loads(archive.extractfile("manifest.json").read())
    roots = {}
    for descriptor in root.get("manifests", []):
        name = descriptor.get("annotations", {}).get("io.containerd.image.name", "")
        for tag in expected_tags:
            if name.endswith("/" + tag):
                if tag in roots:
                    fail("archive repeats runtime tag")
                roots[tag] = descriptor

    for tag in sorted(expected_tags):
        supplied = release_images[tag]
        index_id = supplied.get("image_id")
        if supplied.get("platform") != "linux/amd64" or supplied.get("included_in_archive") is not True:
            fail("manifest runtime image is not archived linux/amd64")
        if not isinstance(index_id, str) or not index_id.startswith("sha256:"):
            fail("manifest runtime image ID is invalid")
        root_descriptor = roots.get(tag)
        if root_descriptor is None or root_descriptor.get("digest") != index_id:
            fail("archive index does not match supplied manifest")

        index = json.loads(read_blob(archive, index_id))
        platform_manifests = [
            descriptor for descriptor in index.get("manifests", [])
            if descriptor.get("platform", {}).get("os") == "linux"
            and descriptor.get("platform", {}).get("architecture") == "amd64"
        ]
        if len(platform_manifests) != 1:
            fail("archive index does not contain exactly one linux/amd64 manifest")
        manifest_id = platform_manifests[0].get("digest")
        image_manifest = json.loads(read_blob(archive, manifest_id))
        config_id = image_manifest.get("config", {}).get("digest")
        config = json.loads(read_blob(archive, config_id))
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            fail("archive config is not linux/amd64")

        docker_entries = [
            entry for entry in docker_manifest
            if tag in entry.get("RepoTags", [])
        ]
        if len(docker_entries) != 1 or docker_entries[0].get("Config") != "blobs/sha256/" + config_id.split(":", 1)[1]:
            fail("archive Docker manifest does not match OCI config")
        print("{}\t{}\t{}\t{}\tlinux/amd64".format(tag, index_id, manifest_id, config_id))
' "$RELEASE_MANIFEST" "$RELEASE_IMAGE_TAR"); then
    die 'supplied analytics manifest and archive do not form a valid release 0.5.0 image chain'
  fi

  while IFS=$'\t' read -r tag index_id manifest_id config_id platform; do
    [[ -n "$tag" && -n "$index_id" && -n "$manifest_id" && -n "$config_id" && -n "$platform" ]] || die 'supplied analytics release has an invalid image chain'
    [[ -z ${release_image_index_ids[$tag]+present} ]] || die "supplied analytics release repeats image $tag"
    release_image_index_ids["$tag"]=$index_id
    release_image_manifest_ids["$tag"]=$manifest_id
    release_image_config_ids["$tag"]=$config_id
    release_image_platforms["$tag"]=$platform
  done <<< "$output"
}

verify_loaded_release_image() {
  local tag=$1 expected_platform=${release_image_platforms[$1]}
  local index_id=${release_image_index_ids[$1]} manifest_id=${release_image_manifest_ids[$1]} config_id=${release_image_config_ids[$1]}
  local inspected image_id image_os image_arch
  inspected=$(docker image inspect "$tag" --format '{{.Id}}|{{.Os}}|{{.Architecture}}') || die "loaded analytics image cannot be inspected: $tag"
  IFS='|' read -r image_id image_os image_arch <<< "$inspected"
  case "$image_id" in
    "$index_id"|"$manifest_id"|"$config_id") ;;
    *) die "loaded analytics image ID does not match the verified supplied chain: $tag" ;;
  esac
  [[ "$image_os/$image_arch" == "$expected_platform" ]] || die "loaded analytics image is not $expected_platform: $tag"
}

verify_derived_vlm_image() {
  local inspected image_os image_arch
  inspected=$(docker image inspect "$VLM_DERIVATIVE_IMAGE" --format '{{.Os}}|{{.Architecture}}') || die "derived VLM image cannot be inspected: $VLM_DERIVATIVE_IMAGE"
  IFS='|' read -r image_os image_arch <<< "$inspected"
  [[ "$image_os/$image_arch" == linux/amd64 ]] || die "derived VLM image is not linux/amd64: $VLM_DERIVATIVE_IMAGE"
  docker run --rm --entrypoint python "$VLM_DERIVATIVE_IMAGE" -c '
import construction_analytics
import construction_analytics.pipeline
import construction_analytics.vlm

expected = "/opt/analytics/construction_analytics"
if construction_analytics.__file__ != expected + "/__init__.py":
    raise SystemExit("construction_analytics import does not resolve to /opt/analytics")
if construction_analytics.__version__ != "0.5.0":
    raise SystemExit("derived image does not retain vendor 0.5.0 package")
for module in (construction_analytics.vlm, construction_analytics.pipeline):
    if not module.__file__.startswith(expected + "/"):
        raise SystemExit("derived override module is not imported from /opt/analytics")
' || die "derived VLM image does not import the expected 0.5 overlay"
}

# Both releases intentionally use this project name and therefore the same named
# pg_data volume. Pin it even if the caller exported COMPOSE_PROJECT_NAME.
check_release_project_volume() {
  local rendered
  rendered=$("${release_clean_env[@]}" docker compose --project-name "$RELEASE_PROJECT" --env-file "$RELEASE_ENV" -f "$RELEASE_COMPOSE" -f "$VLM_OVERLAY_COMPOSE" config --format json) || die 'analytics Compose configuration is invalid'
  if ! printf '%s' "$rendered" | python3 -c '
import json
import sys

config = json.load(sys.stdin)
project = "lct-analytics-release"
volume = config.get("volumes", {}).get("pg_data", {})
vlm = config.get("services", {}).get("vlm_llm", {})
if (
    config.get("name") != project
    or volume.get("name") != f"{project}_pg_data"
    or vlm.get("image") != "lct-analytics-vlm-llm:0.5.0-spider"
):
    raise SystemExit(1)
'; then
    die 'analytics release does not resolve to the existing lct-analytics-release pg_data volume'
  fi
}

# Pin the site project and explicitly selected volumes: a clean database may
# use new names, but an inherited Compose project must never choose them for us.
check_site_project_volume() {
  local rendered db_volume media_volume
  db_volume=${site_values[SK_DB_VOLUME]:-${SITE_PROJECT}_pgdata}
  media_volume=${site_values[SK_MEDIA_VOLUME]:-${SITE_PROJECT}_media}
  rendered=$("${site_clean_env[@]}" "${site_vars[@]}" docker compose --project-name "$SITE_PROJECT" "${site_env_args[@]}" -f "$SITE_COMPOSE" -f "$SITE_ANALYTICS_NETWORK_COMPOSE" -f "$YOLO_COMPOSE" --profile "$TRACKER_PROFILE" config --format json) || die 'site, analytics-network, and YOLO Compose configuration is invalid'
  if ! printf '%s' "$rendered" | python3 -c '
import json
import sys

config = json.load(sys.stdin)
database, media = sys.argv[1:]
volumes = config.get("volumes", {})
if (
    config.get("name") != "hakaton-main"
    or volumes.get("pgdata", {}).get("name") != database
    or volumes.get("media", {}).get("name") != media
):
    raise SystemExit(1)
' "$db_volume" "$media_volume"; then
    die 'site volumes do not match the selected database and media volumes'
  fi
  if ! printf '%s' "$rendered" | python3 -c '
import json
import sys

config = json.load(sys.stdin)
network = config.get("networks", {}).get("analytics_release", {})
backend = config.get("services", {}).get("backend", {})
expected_urls = {
    "DETERMINISTIC_SERVICE_URL": "http://deterministic:8000",
    "VLM_LLM_SERVICE_URL": "http://vlm_llm:8000",
}
if (
    network.get("name") != "lct-analytics-release_default"
    or network.get("external") is not True
    or set(backend.get("networks", {})) != {"default", "analytics_release"}
    or any(backend.get("environment", {}).get(key) != value for key, value in expected_urls.items())
):
    raise SystemExit(1)
'; then
    die 'site backend must join lct-analytics-release_default and use analytics service DNS'
  fi
}

# This intentionally reads only simple KEY=value records. Docker Compose performs
# the final dotenv parsing below; this pass only lets preflight name every missing
# required value without evaluating an env file as shell code.
read_dotenv_values() {
  local file=$1 target=$2
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    [[ -z "$line" || $line == \#* ]] && continue
    [[ $line =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    # Strip one matched pair of quotes for an empty-value check. Values used for
    # Compose are obtained from Compose itself, not from this lightweight pass.
    if [[ ${#value} -ge 2 && ( ${value:0:1} == '"' && ${value: -1} == '"' || ${value:0:1} == "'" && ${value: -1} == "'" ) ]]; then
      value=${value:1:${#value}-2}
    fi
    printf -v "$target[$key]" '%s' "$value"
  done < "$file"
}

is_blank() {
  [[ -z ${1//[[:space:]]/} ]]
}

require_dotenv_value() {
  local map=$1 key=$2 label=$3 ref
  ref="$map[$key]"
  if [[ -z ${!ref+x} ]] || is_blank "${!ref}"; then
    printf 'start-all: missing %s in service-release_1/service-release/.env\n' "$label" >&2
    return 1
  fi
}

reject_tracker_placeholder() {
  local value=$1
  case "$value" in
    11111111|dev-tracker-key|change-me|change-me-before-real-use|example|placeholder)
      die 'SK_TRACKER_API_KEY in yolo_service/.env is a placeholder; provide the real shared tracker key'
      ;;
  esac
}

# Compose has the authoritative dotenv parser. Capture its output instead of
# printing it because it includes secrets. The passed command must end in the
# Compose files/options, without a subcommand.
read_compose_environment() {
  local target=$1
  shift
  local output line key value
  if ! output=$("$@" config --environment); then
    return 1
  fi
  while IFS= read -r line; do
    [[ $line == *=* ]] || continue
    key=${line%%=*}
    value=${line#*=}
    printf -v "$target[$key]" '%s' "$value"
  done <<< "$output"
}

require_compose_value() {
  local map=$1 key=$2 label=$3 ref
  ref="$map[$key]"
  [[ -n ${!ref+x} ]] && ! is_blank "${!ref}" || die "missing $label"
}

require_file "$RELEASE_ENV" 'analytics environment file'
require_file "$YOLO_ENV" 'YOLO environment file'
require_file "$RELEASE_COMPOSE" 'analytics Compose file'
require_file "$VLM_OVERLAY_COMPOSE" 'VLM Spider Compose override'
require_file "$RELEASE_CHECKSUMS" 'analytics release checksum file'
require_file "$SITE_COMPOSE" 'site Compose file'
require_file "$SITE_ANALYTICS_NETWORK_COMPOSE" 'site analytics network Compose overlay'
require_file "$YOLO_COMPOSE" 'YOLO Compose file'
require_file "$RELEASE_IMAGE_TAR" 'analytics image archive'

command -v docker >/dev/null 2>&1 || die 'docker is not installed or not on PATH'
command -v python3 >/dev/null 2>&1 || die 'python3 is not installed or not on PATH'
command -v sha256sum >/dev/null 2>&1 || die 'sha256sum is not installed or not on PATH'
docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is not available'

verify_release_checksums
declare -A release_image_index_ids=()
declare -A release_image_manifest_ids=()
declare -A release_image_config_ids=()
declare -A release_image_platforms=()
read_release_image_chains

declare -A release_raw=()
read_dotenv_values "$RELEASE_ENV" release_raw
release_complete=true
require_dotenv_value release_raw POSTGRES_PASSWORD 'POSTGRES_PASSWORD' || release_complete=false
require_dotenv_value release_raw ANALYTICS_DETERMINISTIC_DB_PASSWORD 'ANALYTICS_DETERMINISTIC_DB_PASSWORD' || release_complete=false
require_dotenv_value release_raw ANALYTICS_VLM_DB_PASSWORD 'ANALYTICS_VLM_DB_PASSWORD' || release_complete=false
require_dotenv_value release_raw ANALYTICS_SERVICE_TOKEN 'ANALYTICS_SERVICE_TOKEN' || release_complete=false
require_dotenv_value release_raw LITELLM_API_KEY 'LITELLM_API_KEY' || release_complete=false
$release_complete || die 'analytics credentials are incomplete; no containers were changed'

# Do not allow a caller's exported value to override the reviewed credentials
# file, and pin the release project that owns the existing pg_data volume.
release_clean_env=(
  env
  -u COMPOSE_PROJECT_NAME
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u ANALYTICS_DETERMINISTIC_DB_PASSWORD -u ANALYTICS_VLM_DB_PASSWORD
  -u ANALYTICS_SERVICE_TOKEN -u POSTGRES_PORT -u DETERMINISTIC_PORT -u VLM_LLM_PORT
  -u LITELLM_API_KEY -u LITELLM_GATEWAY_URL -u ANALYTICS_MODEL
  -u ANALYTICS_MAX_CONCURRENCY -u ANALYTICS_MODEL_TIMEOUT_SECONDS
  -u ANALYTICS_EXECUTION_TIMEOUT_SECONDS
)
site_clean_env=(
  env
  -u COMPOSE_PROJECT_NAME
  -u COMPOSE_PROFILES
)

declare -A release_values=()
read_compose_environment release_values "${release_clean_env[@]}" docker compose --project-name "$RELEASE_PROJECT" --env-file "$RELEASE_ENV" -f "$RELEASE_COMPOSE" -f "$VLM_OVERLAY_COMPOSE" || die 'analytics Compose environment is invalid'
require_compose_value release_values ANALYTICS_SERVICE_TOKEN 'ANALYTICS_SERVICE_TOKEN after Compose parsing'
require_compose_value release_values LITELLM_API_KEY 'LITELLM_API_KEY after Compose parsing'

declare -A yolo_values=()
yolo_clean_env=(env -u SK_TRACKER_API_KEY -u YOLO_MODEL_HOST_PATH)
read_compose_environment yolo_values "${yolo_clean_env[@]}" docker compose --env-file "$YOLO_ENV" -f "$YOLO_COMPOSE" || die 'YOLO Compose environment is invalid'
require_compose_value yolo_values SK_TRACKER_API_KEY 'SK_TRACKER_API_KEY in yolo_service/.env'
require_compose_value yolo_values YOLO_MODEL_HOST_PATH 'YOLO_MODEL_HOST_PATH in yolo_service/.env'
reject_tracker_placeholder "${yolo_values[SK_TRACKER_API_KEY]}"
YOLO_MODEL_HOST_PATH=${yolo_values[YOLO_MODEL_HOST_PATH]}
[[ "$YOLO_MODEL_HOST_PATH" == /* ]] || die 'YOLO_MODEL_HOST_PATH must be an absolute host path'
[[ -f "$YOLO_MODEL_HOST_PATH" && -r "$YOLO_MODEL_HOST_PATH" ]] || die 'YOLO model file is missing or unreadable'

# Hakaton-main/.env is optional. Ask Compose to parse it so quoted dotenv values
# are decoded exactly as they will be for the final invocation; ordinary exported
# variables retain Compose's normal precedence over the file.
declare -A site_values=()
site_env_args=()
if [[ -f "$SITE_ENV" ]]; then
  require_file "$SITE_ENV" 'site environment file'
  site_env_args+=(--env-file "$SITE_ENV")
  read_compose_environment site_values "${site_clean_env[@]}" docker compose --project-name "$SITE_PROJECT" --env-file "$SITE_ENV" -f "$SITE_COMPOSE" -f "$SITE_ANALYTICS_NETWORK_COMPOSE" || die 'site analytics-network Compose configuration is invalid'
else
  read_compose_environment site_values "${site_clean_env[@]}" docker compose --project-name "$SITE_PROJECT" -f "$SITE_COMPOSE" -f "$SITE_ANALYTICS_NETWORK_COMPOSE" || die 'site analytics-network Compose configuration is invalid'
fi
site_env_args+=(--env-file "$YOLO_ENV")
require_compose_value site_values CAMERA_STAGE_MONITOR_URL 'CAMERA_STAGE_MONITOR_URL (set it in Hakaton-main/.env or the launcher environment)'
CAMERA_URL=${site_values[CAMERA_STAGE_MONITOR_URL]}
case "$CAMERA_URL" in
  http://*|https://*) ;;
  *) die 'CAMERA_STAGE_MONITOR_URL must use http:// or https://' ;;
esac

site_vars=(
  "SK_TRACKER_URL=ws://yolo-tracker:8200/stream"
  "SK_TRACKER_API_KEY=${yolo_values[SK_TRACKER_API_KEY]}"
  'SK_ANALYSIS_PROVIDER=push'
  'DETERMINISTIC_SERVICE_URL=http://deterministic:8000'
  'VLM_LLM_SERVICE_URL=http://vlm_llm:8000'
  "ANALYTICS_SERVICE_TOKEN=${release_values[ANALYTICS_SERVICE_TOKEN]}"
  "CAMERA_STAGE_MONITOR_URL=$CAMERA_URL"
)
if [[ -n ${site_values[CAMERA_STAGE_MONITOR_TOKEN]+present} ]]; then
  site_vars+=("CAMERA_STAGE_MONITOR_TOKEN=${site_values[CAMERA_STAGE_MONITOR_TOKEN]}")
fi

# Validate both final configurations before any image load or container action.
# Pin the site project as well as the analytics project so neither inherited
# Compose project nor profile settings can select another site's volumes.
"${release_clean_env[@]}" docker compose --project-name "$RELEASE_PROJECT" --env-file "$RELEASE_ENV" -f "$RELEASE_COMPOSE" -f "$VLM_OVERLAY_COMPOSE" config --quiet || die 'analytics Compose configuration is invalid'
check_release_project_volume
"${site_clean_env[@]}" "${site_vars[@]}" docker compose --project-name "$SITE_PROJECT" "${site_env_args[@]}" -f "$SITE_COMPOSE" -f "$SITE_ANALYTICS_NETWORK_COMPOSE" -f "$YOLO_COMPOSE" --profile "$TRACKER_PROFILE" config --quiet || die 'site, analytics-network, and YOLO Compose configuration is invalid'
check_site_project_volume

# These values are passed from one source of truth to both rendered stacks; the
# explicit comparison makes a future launcher edit fail rather than split tokens.
[[ "${site_vars[5]#ANALYTICS_SERVICE_TOKEN=}" == "${release_values[ANALYTICS_SERVICE_TOKEN]}" ]] || die 'analytics service token mismatch'

if "$CHECK_ONLY"; then
  printf 'Preflight passed: release 0.5.0 checksums and manifest are valid; analytics resolves the Spider VLM overlay on lct-analytics-release_pg_data and lct-analytics-release_default; site uses project hakaton-main with database volume %s; analytics, site, and %s YOLO Compose configurations are valid. No images or containers were changed.\n' "${site_values[SK_DB_VOLUME]:-${SITE_PROJECT}_pgdata}" "$TRACKER_PROFILE"
  exit 0
fi

# The checksums, manifest, and final Compose configurations are verified above.
# Load and verify the supplied base chain before building the local VLM overlay;
# do not run down, prune, delete, or replace the existing analytics data volume.
docker load -i "$RELEASE_IMAGE_TAR"
verify_loaded_release_image lct-analytics-deterministic:0.5.0
verify_loaded_release_image lct-analytics-vlm-llm:0.5.0
printf 'Verified supplied 0.5.0 deterministic and VLM base images for linux/amd64.\n'
docker build -t "$VLM_DERIVATIVE_IMAGE" "$VLM_OVERLAY_DIR"
verify_derived_vlm_image
printf 'Built and verified %s from the local Spider prompt overlay.\n' "$VLM_DERIVATIVE_IMAGE"
"${release_clean_env[@]}" docker compose --project-name "$RELEASE_PROJECT" --env-file "$RELEASE_ENV" -f "$RELEASE_COMPOSE" -f "$VLM_OVERLAY_COMPOSE" up -d --build
"${site_clean_env[@]}" "${site_vars[@]}" docker compose --project-name "$SITE_PROJECT" "${site_env_args[@]}" -f "$SITE_COMPOSE" -f "$SITE_ANALYTICS_NETWORK_COMPOSE" -f "$YOLO_COMPOSE" --profile "$TRACKER_PROFILE" up -d --build
printf 'Started analytics 0.5.0 with the Spider VLM overlay, site, and %s YOLO tracker.\n' "$TRACKER_PROFILE"
