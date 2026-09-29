import argparse
import json
import sys

from .config import OverlapConfig
from .detector import OverlapDetector
from .errors import CameraOverlapError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Propose approximate shared regions on two photos using local SuperPoint + LightGlue")
    parser.add_argument("image0")
    parser.add_argument("image1")
    parser.add_argument("--output", required=True, help="Empty output directory for result.json, masks, matches and preview")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda or cuda:N")
    parser.add_argument("--weights-dir", help="Directory with the two verified checkpoints; default: bundled weights")
    parser.add_argument("--assume-planar", action="store_true", help="Allow whole-plane H overlap only when the caller knows the scene is planar")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly overwrite result files in an existing output directory")
    args = parser.parse_args(argv)
    try:
        detector = OverlapDetector(weights_dir=args.weights_dir, device=args.device,
                                   config=OverlapConfig(assume_planar=args.assume_planar))
        result = detector.analyze(args.image0, args.image1)
        directory = result.save(args.output, overwrite=args.overwrite)
        print(json.dumps({"status": result.status, "output": str(directory.resolve()),
                          "matches": result.geometry["matches"], "inliers": result.geometry["selected_inliers"],
                          "rejection_reasons": result.geometry["rejection_reasons"]}, ensure_ascii=False))
        # A measured insufficient_evidence is a successful analysis, not a process error.
        return 0
    except (CameraOverlapError, OSError, ValueError) as error:
        print(json.dumps({"error": {"type": type(error).__name__, "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2
