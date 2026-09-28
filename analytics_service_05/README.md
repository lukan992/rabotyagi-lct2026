# VLM 0.5 Spider prompt overlay

This is a minimal local derivative of the supplied `lct-analytics-vlm-llm:0.5.0` image. It replaces only `construction_analytics/vlm.py` and `construction_analytics/pipeline.py`; the vendor HTTP service, storage, database migrations, schemas, gateway, retries, VLM image prompt, and result envelope remain in the base image.

For a v2 request with a non-null `resource_plan`, the overlay passes only `resource_plan`, `source_context`, and `analysis_mode` to the LLM model case. It does not pass `equipment_observation`, manual counts, or `resource_target`. The image/VLM request remains unchanged. The LLM system instruction treats the external resource plan as data rather than camera evidence, forbids implicit local-stage mapping, and forbids completion, lag, or factual-volume claims from plan values.

## Verify and build

Run these commands from the repository root before building. They bind the derivative to the supplied 0.5 archive and verify that the two copied modules match the unmodified base image.

```sh
sha256sum service-release_1/service-release/images/analytics-images.tar
# expected: 68aef89954fa77f1eb4d6b36a2d694110b778947da20143f7319f4356e9678c2

docker load -i service-release_1/service-release/images/analytics-images.tar
docker image inspect lct-analytics-vlm-llm:0.5.0 --format '{{.Id}} {{.Os}}/{{.Architecture}}'
# expected: sha256:2798634ae3e8a2fef58f5f75aa42dd3ff79147da2fb9c7eb58b451927b42f064 linux/amd64

python -c 'import hashlib, zipfile; p="service-release_1/service-release/wheels/lct_construction_analytics-0.5.0-py3-none-any.whl"; z=zipfile.ZipFile(p); print(*(hashlib.sha256(z.read(n)).hexdigest() for n in ("construction_analytics/vlm.py", "construction_analytics/pipeline.py")))'
docker run --rm --entrypoint sha256sum lct-analytics-vlm-llm:0.5.0 /opt/analytics/construction_analytics/vlm.py /opt/analytics/construction_analytics/pipeline.py
# both commands must respectively print:
# 201b61eebff81725e27e4ee369657c60bb805137721ea197f7cf20d39d7c8874
# 8f1ac846676a98ba87721f561d6c7211fbd672706365b16027e7ad075733a8f2

docker build -t lct-analytics-vlm-llm:0.5.0-spider analytics_service_05
```

The expected base ID is the SHA-256 of the VLM config object in the supplied archive (`images/analytics-images.tar:manifest.json`). It is intentionally checked as an OCI config/image ID, not against the separate release-manifest repository digest.

## Compose deployment

Use the supplied 0.5 Compose file as the first file so the override build context resolves from that file's directory:

```sh
docker compose \
  -f service-release_1/service-release/compose.yaml \
  -f analytics_service_05/compose.override.yaml \
  config
```

The override changes only `vlm_llm` to `lct-analytics-vlm-llm:0.5.0-spider`; every other service setting is inherited from the vendor Compose file. Do not point a production deployment at the override until its existing database has the vendor 0.5 migration state and the controlled smoke/deployment procedure has completed.

## Boundary test and import precedence

After the build, run the gateway-boundary test in the actual derivative image:

```sh
docker run --rm --user 0 \
  -v "$PWD/analytics_service_05/tests:/tests:ro" \
  --entrypoint python lct-analytics-vlm-llm:0.5.0-spider \
  -m unittest discover -s /tests -v

docker run --rm --entrypoint python lct-analytics-vlm-llm:0.5.0-spider \
  -c 'import construction_analytics; import construction_analytics.vlm, construction_analytics.pipeline; print(construction_analytics.__file__); print(construction_analytics.__version__); print(construction_analytics.vlm.__file__); print(construction_analytics.pipeline.__file__)'
```

The test validates a real v2 schema/semantic request and crosses the actual `vlm.analyze` → `pipeline.execute` gateway boundary with a controlled gateway. It proves P04, `3500`, `700`, and planned quantities are in the saved LLM prompt, absent from the image prompt, manual counts are absent from the model case, and a v1 request stays unchanged. It does not validate a live model's output quality or connect to a live Spider service.
