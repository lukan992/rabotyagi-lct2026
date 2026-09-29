FROM python:3.13.12-slim-bookworm

ARG TORCH_FLAVOR=cpu
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=8 \
    MKL_NUM_THREADS=8

LABEL org.opencontainers.image.title="LCT camera overlap" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="Local SuperPoint + LightGlue approximate overlap CLI"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Choose the official CPU or CUDA 12.8 wheels. Installing Torch first keeps
# pip from pulling the default CUDA distribution for the smaller CPU image.
RUN case "$TORCH_FLAVOR" in cpu|cu128) ;; *) echo "TORCH_FLAVOR must be cpu or cu128" >&2; exit 2 ;; esac \
    && python -m pip install --no-cache-dir "torch==2.7.1" \
       --index-url "https://download.pytorch.org/whl/${TORCH_FLAVOR}"

WORKDIR /opt/camera-overlap
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir . \
    && python -m pip check \
    && python -c "from camera_overlap.weights import verify_weights; verify_weights()" \
    && useradd --uid 10001 --create-home overlap \
    && mkdir -p /work /output \
    && chown overlap:overlap /work /output

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python", "-m", "camera_overlap"]
CMD ["--help"]
