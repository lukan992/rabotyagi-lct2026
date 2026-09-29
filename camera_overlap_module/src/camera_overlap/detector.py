from dataclasses import asdict
from pathlib import Path
from threading import Lock
from time import perf_counter

import numpy as np
import torch

from ._vendor.lightglue import LightGlue
from ._vendor.superpoint import SuperPoint
from ._vendor.utils import rbd
from .config import OverlapConfig
from .errors import InferenceError, ModelWeightsError
from .geometry import estimate_regions
from .images import ImageInput, read_image
from .result import OverlapResult
from .weights import verify_weights


class OverlapDetector:
    """Load once, analyze many pairs. Calls on one instance are serialized."""

    def __init__(self, *, weights_dir: str | Path | None = None, device: str = "auto",
                 config: OverlapConfig | None = None):
        self.config = config if config is not None else OverlapConfig()
        if not isinstance(self.config, OverlapConfig):
            raise TypeError("config must be an OverlapConfig")
        paths = verify_weights(weights_dir)
        try:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else torch.device(device)
            if self.device.type not in {"cpu", "cuda"}:
                raise ValueError("Supported devices are auto, cpu, cuda or cuda:N")
            if self.device.type == "cuda" and not torch.cuda.is_available():
                raise ValueError("CUDA requested but unavailable; select device='cpu'")
        except (ValueError, RuntimeError) as error:
            raise InferenceError(str(error)) from error
        try:
            self.extractor = SuperPoint(weights_path=paths["superpoint_v1.pth"], max_num_keypoints=self.config.max_keypoints)
            # None prevents the upstream constructor from loading URL weights.
            self.matcher = LightGlue(features=None)
            state = torch.load(paths["superpoint_lightglue_v0-1_arxiv.pth"], map_location="cpu", weights_only=True)
            for i in range(self.matcher.conf.n_layers):
                state = {key.replace(f"self_attn.{i}", f"transformers.{i}.self_attn"): value for key, value in state.items()}
                state = {key.replace(f"cross_attn.{i}", f"transformers.{i}.cross_attn"): value for key, value in state.items()}
            # Release v0.1 has nine scalar .r entries that the pinned upstream
            # MatchAssignment no longer uses (upstream ignores them as well).
            legacy = {f"log_assignment.{i}.r" for i in range(self.matcher.conf.n_layers)}
            if any(key not in state or tuple(state[key].shape) != (1,) for key in legacy):
                raise ModelWeightsError("Unexpected legacy LightGlue release entries")
            state = {key: value for key, value in state.items() if key not in legacy}
            incompatible = self.matcher.load_state_dict(state, strict=False)
            if set(incompatible.missing_keys) != {"confidence_thresholds"} or incompatible.unexpected_keys:
                raise ModelWeightsError(f"Unexpected LightGlue checkpoint keys: {incompatible}")
        except (OSError, RuntimeError, ValueError) as error:
            raise ModelWeightsError(f"Cannot load local model checkpoints: {error}") from error
        try:
            self.extractor = self.extractor.eval().to(self.device)
            self.matcher = self.matcher.eval().to(self.device)
        except (RuntimeError, ValueError) as error:
            raise InferenceError(f"Cannot initialize device {self.device}: {error}") from error
        self._lock = Lock()

    def analyze(self, image0: ImageInput, image1: ImageInput) -> OverlapResult:
        """Return proposed regions or insufficient_evidence, never proven no-overlap."""
        started = perf_counter()
        # Decode both inputs before beginning inference on either input.
        decoded = [read_image(value, self.config) for value in [image0, image1]]
        images = tuple(value[0] for value in decoded)
        metadata = tuple(value[1] for value in decoded)
        with self._lock, torch.inference_mode():
            try:
                features = []
                for image in images:
                    tensor = torch.from_numpy(np.asarray(image).copy()).permute(2, 0, 1).float().div(255).to(self.device)
                    features.append(self.extractor.extract(tensor, resize=self.config.resize))
                matched = rbd(self.matcher({"image0": features[0], "image1": features[1]}))
                indices = matched["matches"].cpu().numpy()
                points0 = rbd(features[0])["keypoints"].cpu().numpy()[indices[:, 0]]
                points1 = rbd(features[1])["keypoints"].cpu().numpy()[indices[:, 1]]
                scores = matched["scores"].cpu().numpy()
            except (RuntimeError, ValueError) as error:
                raise InferenceError(f"Feature matching failed on {self.device}: {error}") from error
            sizes = [image.size for image in images]
            valid = (np.isfinite(points0).all(axis=1) & np.isfinite(points1).all(axis=1) & np.isfinite(scores)
                     & (points0 >= 0).all(axis=1) & (points1 >= 0).all(axis=1)
                     & (points0 < sizes[0]).all(axis=1) & (points1 < sizes[1]).all(axis=1))
            removed = int((~valid).sum())
            points0, points1, scores = points0[valid], points1[valid], scores[valid]
            metrics, masks, selected, fm, hm = estimate_regions(points0, points1, sizes, self.config)
        metrics["invalid_coordinate_matches_removed"] = removed
        return OverlapResult(images=images, image_metadata=metadata, masks=masks,
                             points0=points0, points1=points1, scores=scores,
                             selected_inliers=selected, f_inliers=fm, h_inliers=hm,
                             geometry=metrics, config=asdict(self.config), device=str(self.device),
                             elapsed_seconds=perf_counter() - started)
