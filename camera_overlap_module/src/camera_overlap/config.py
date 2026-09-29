from dataclasses import dataclass
import math


@dataclass(frozen=True)
class OverlapConfig:
    """Exploratory thresholds from the conservative dataset experiment."""

    max_keypoints: int = 2048
    resize: int = 1024
    geometry_long_edge: int = 1024
    mask_long_edge: int = 512
    f_threshold_px: float = 2.0
    h_threshold_px: float = 3.0
    min_inliers: int = 50
    min_inlier_ratio: float = 0.45
    min_hull_fraction: float = 0.01
    planar_min_inliers: int = 50
    planar_min_ratio: float = 0.60
    planar_min_hull_fraction: float = 0.10
    support_triangle_max_edge_fraction: float = 0.18
    support_point_radius_fraction: float = 0.015
    assume_planar: bool = False
    max_image_bytes: int = 50 * 1024 * 1024
    max_image_pixels: int = 40_000_000

    def __post_init__(self):
        for field in ["max_keypoints", "resize", "geometry_long_edge", "mask_long_edge",
                      "min_inliers", "planar_min_inliers", "max_image_bytes", "max_image_pixels"]:
            value = getattr(self, field)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field} must be a positive integer")
        if self.resize < 128 or self.mask_long_edge < 64:
            raise ValueError("resize must be >=128 and mask_long_edge >=64")
        if type(self.assume_planar) is not bool:
            raise ValueError("assume_planar must be a bool")
        for field in ["f_threshold_px", "h_threshold_px", "min_inlier_ratio", "min_hull_fraction",
                      "planar_min_ratio", "planar_min_hull_fraction", "support_triangle_max_edge_fraction",
                      "support_point_radius_fraction"]:
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be finite and positive")
            if field not in ["f_threshold_px", "h_threshold_px"] and value > 1:
                raise ValueError(f"{field} must be <=1")
