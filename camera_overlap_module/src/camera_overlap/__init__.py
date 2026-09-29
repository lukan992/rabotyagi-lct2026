"""Approximate shared image regions; no camera calibration is required."""

from .config import OverlapConfig
from .detector import OverlapDetector
from .errors import CameraOverlapError, InvalidImageError, ModelWeightsError, InferenceError
from .result import OverlapResult

__version__ = "0.1.0"
__all__ = ["OverlapDetector", "OverlapConfig", "OverlapResult", "CameraOverlapError",
           "InvalidImageError", "ModelWeightsError", "InferenceError"]
