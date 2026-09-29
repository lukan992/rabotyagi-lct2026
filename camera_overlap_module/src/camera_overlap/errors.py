class CameraOverlapError(Exception):
    """Base class for actionable module errors."""


class InvalidImageError(CameraOverlapError):
    """An input cannot be decoded or exceeds supported image limits."""


class ModelWeightsError(CameraOverlapError):
    """Local weights are missing, changed or incompatible."""


class InferenceError(CameraOverlapError):
    """The selected device cannot complete inference."""
