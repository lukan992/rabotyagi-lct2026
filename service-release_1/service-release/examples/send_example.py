"""Send unchanged example bytes, optionally repeat/lookup/conflict. Standard library only."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


def send(base, metadata, image, token):
    case = json.loads(metadata)
    version = 2 if case["schema_version"] == "frame-analysis-input-v2" else 1
    boundary = "lct-" + uuid4().hex
    chunks = []
    for name, filename, mime, data in [("metadata", "metadata.json", "application/json", metadata),
                                      ("image", "frame.png", case["frame"]["media_type"], image)]:
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode() + data + b"\r\n")
    payload = b"".join(chunks) + ("--" + boundary + "--\r\n").encode()
    request = urllib.request.Request(base.rstrip("/") + f"/v{version}/analyze/frame", data=payload,
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": case["request_id"], "Content-Type": "multipart/form-data; boundary=" + boundary})
    return fetch(request)


def fetch(request):
    try:
        response = urllib.request.urlopen(request, timeout=1050)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return {"http_status": response.status, "body": json.loads(response.read())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--case", default="only-no-class")
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--lookup", action="store_true")
    parser.add_argument("--conflict", action="store_true")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    metadata, image = (directory / (args.case + ".json")).read_bytes(), (directory / "frame.png").read_bytes()
    token = os.environ["ANALYTICS_SERVICE_TOKEN"]
    result = {"initial": send(args.url, metadata, image, token)}
    if args.repeat:
        result["repeat"] = send(args.url, metadata, image, token)
    if args.lookup:
        case = json.loads(metadata)
        version = 2 if case["schema_version"] == "frame-analysis-input-v2" else 1
        path = f"/v{version}/analyses/by-request/" + urllib.parse.quote(case["request_id"]) + "?" + urllib.parse.urlencode({"site_id": case["site_id"]})
        result["lookup"] = fetch(urllib.request.Request(args.url.rstrip("/") + path, headers={"Authorization": "Bearer " + token}))
    if args.conflict:
        result["conflict"] = send(args.url, metadata + b" ", image, token)
    print(json.dumps(result, ensure_ascii=False, indent=2))
