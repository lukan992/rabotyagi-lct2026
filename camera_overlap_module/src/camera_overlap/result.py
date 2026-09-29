from dataclasses import dataclass, field
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import OverlapConfig
from .geometry import mask_shape, polygons
from .weights import ASSETS


@dataclass
class OverlapResult:
    images: tuple[Image.Image, Image.Image] = field(repr=False)
    image_metadata: tuple[dict, dict]
    masks: tuple[np.ndarray, np.ndarray] = field(repr=False)
    points0: np.ndarray = field(repr=False)
    points1: np.ndarray = field(repr=False)
    scores: np.ndarray = field(repr=False)
    selected_inliers: np.ndarray = field(repr=False)
    f_inliers: np.ndarray = field(repr=False)
    h_inliers: np.ndarray = field(repr=False)
    geometry: dict
    config: dict
    device: str
    elapsed_seconds: float

    @property
    def status(self):
        return "insufficient_evidence" if self.geometry["mode"] == "insufficient_support" else "candidate_overlap"

    def mask_original(self, index: int):
        """Binary uint8 mask (0/255) at the EXIF-oriented input image size."""
        if index not in (0, 1):
            raise IndexError("Image index must be 0 or 1")
        return cv2.resize(self.masks[index], self.images[index].size, interpolation=cv2.INTER_NEAREST)

    def to_dict(self):
        config = OverlapConfig(**self.config)
        images = []
        for image, metadata, mask in zip(self.images, self.image_metadata, self.masks):
            _, factor = mask_shape(image.size, config)
            images.append({**metadata, "mask_size": [mask.shape[1], mask.shape[0]],
                           "mask_to_image_scale": 1 / factor,
                           "regions": polygons(mask, image.size, config)})
        return {"schema_version": "camera-overlap-v1", "module_version": "0.1.0",
                "model": "superpoint_lightglue", "weights_sha256": ASSETS.copy(),
                "status": self.status, "images": images, "geometry": self.geometry,
                "config": self.config, "device": self.device, "elapsed_seconds": self.elapsed_seconds,
                "interpretation": "Approximate shared visual landmarks; not a proven full field-of-view or common ground area",
                "limitations": ["Repeated structures can produce false regions despite fitted geometry",
                                "Insufficient evidence does not prove absence of overlap"]}

    def preview(self):
        thumbnails = []
        for image, mask in zip(self.images, self.masks):
            small = image.copy()
            small.thumbnail((640, 640), Image.Resampling.LANCZOS)
            rgb = np.asarray(small).copy()
            scaled_mask = cv2.resize(mask, small.size, interpolation=cv2.INTER_NEAREST)
            inside = scaled_mask > 0
            rgb[inside] = (rgb[inside] * 0.62 + np.array([23, 225, 151]) * 0.38).astype(np.uint8)
            contours, _ = cv2.findContours(scaled_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(rgb, contours, -1, (23, 225, 151), 2)
            thumbnails.append(Image.fromarray(rgb))
        canvas = Image.new("RGB", (1280, 64 + max(image.height for image in thumbnails)), "#142431")
        for index, image in enumerate(thumbnails):
            canvas.paste(image, (index * 640, 64))
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default(size=18)
        draw.text((12, 8), f"SuperPoint + LightGlue | {self.status} | {self.geometry['mode']}", font=font, fill="white")
        draw.text((12, 34), f"matches={len(self.scores)}  selected_inliers={int(self.selected_inliers.sum())}  green=proposed region", font=font, fill="white")
        return canvas

    def save(self, directory: str | Path, *, overwrite: bool = False):
        """Save JSON, compact masks, coordinates and a preview. No silent overwrite."""
        directory = Path(directory)
        if directory.exists() and any(directory.iterdir()) and not overwrite:
            raise FileExistsError(f"Output directory is not empty: {directory}; use overwrite=True explicitly")
        directory.mkdir(parents=True, exist_ok=True)
        for index, mask in enumerate(self.masks):
            Image.fromarray(mask).save(directory / f"mask{index}.png")
        np.savez_compressed(directory / "matches.npz", points0=self.points0, points1=self.points1,
                            scores=self.scores, selected_inliers=self.selected_inliers,
                            F_inliers=self.f_inliers, H_inliers=self.h_inliers)
        self.preview().save(directory / "overlap.jpg", quality=92)
        payload = self.to_dict()
        payload["artifacts"] = {"masks": ["mask0.png", "mask1.png"], "matches": "matches.npz", "preview": "overlap.jpg"}
        (directory / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        return directory
