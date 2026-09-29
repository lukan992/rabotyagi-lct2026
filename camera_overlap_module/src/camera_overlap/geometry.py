"""Image-only geometric verification and approximate support regions.

Adapted from the verified conservative experiment. A fitted F/H can still
support incorrect matches on repeated structures; it is not ground truth.
"""

import cv2
import numpy as np

from .config import OverlapConfig


def scale_matrix(size, edge):
    return np.diag([edge / max(size), edge / max(size), 1.0])


def transform(points, matrix):
    homogeneous = np.c_[points, np.ones(len(points))] @ matrix.T
    denominator = homogeneous[:, 2:3]
    return np.divide(homogeneous[:, :2], denominator,
                     out=np.full((len(points), 2), np.nan), where=np.abs(denominator) > 1e-10)


def hull_fraction(points, size):
    if len(points) < 3:
        return 0.0
    return float(cv2.contourArea(cv2.convexHull(points.astype(np.float32))) / np.prod(size))


def mask_shape(size, config):
    factor = config.mask_long_edge / max(size)
    return (max(2, round(size[1] * factor)), max(2, round(size[0] * factor))), factor


def support_mask(points, size, config):
    shape, factor = mask_shape(size, config)
    height, width = shape
    mask = np.zeros(shape, np.uint8)
    scaled = np.asarray(points * factor, dtype=np.float32)
    scaled = np.clip(scaled, [0, 0], [width - 1.01, height - 1.01]).astype(np.float32)
    diagonal = np.hypot(width, height)
    if len(scaled) >= 3:
        triangulation = cv2.Subdiv2D((0, 0, width, height))
        for x, y in np.unique(scaled, axis=0):
            # Near-identical points can be rejected by OpenCV's triangulation.
            # Their measured locations are still used for the disks below.
            try:
                triangulation.insert((float(x), float(y)))
            except cv2.error:
                continue
        for triangle in triangulation.getTriangleList().reshape(-1, 3, 2):
            if np.any(triangle < 0) or np.any(triangle >= [width, height]):
                continue
            longest = max(np.linalg.norm(triangle[a] - triangle[b]) for a, b in [(0, 1), (1, 2), (2, 0)])
            if longest <= diagonal * config.support_triangle_max_edge_fraction:
                cv2.fillConvexPoly(mask, np.round(triangle).astype(np.int32), 255)
    radius = max(1, round(diagonal * config.support_point_radius_fraction))
    for x, y in scaled:
        cv2.circle(mask, (round(float(x)), round(float(y))), radius, 255, -1)
    return mask


def planar_masks(matrix, sizes, config):
    shape0, factor0 = mask_shape(sizes[0], config)
    shape1, factor1 = mask_shape(sizes[1], config)
    normalized = np.diag([factor1, factor1, 1.0]) @ matrix @ np.diag([1 / factor0, 1 / factor0, 1.0])
    mask0 = cv2.warpPerspective(np.full(shape1, 255, np.uint8), np.linalg.inv(normalized), shape0[::-1], flags=cv2.INTER_NEAREST)
    mask1 = cv2.warpPerspective(np.full(shape0, 255, np.uint8), normalized, shape1[::-1], flags=cv2.INTER_NEAREST)
    return [mask0, mask1]


def safe_homography(matrix, sizes):
    if matrix is None or matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
        return False
    for current, size in [(matrix, sizes[0]), (np.linalg.inv(matrix), sizes[1])]:
        corners = np.array([[0, 0], [size[0], 0], [size[0], size[1]], [0, size[1]]], dtype=float)
        denominators = np.c_[corners, np.ones(4)] @ current[2]
        if np.min(denominators) <= 0 <= np.max(denominators):
            return False
        warped = transform(corners, current)
        if not np.isfinite(warped).all() or np.max(np.abs(warped)) > 20 * max(*sizes[0], *sizes[1]):
            return False
        if not cv2.isContourConvex(warped.astype(np.float32)):
            return False
    return True


def estimate_regions(points0, points1, sizes, config: OverlapConfig):
    scales = [scale_matrix(size, config.geometry_long_edge) for size in sizes]
    p0, p1 = [transform(points, scale) for points, scale in zip([points0, points1], scales)]
    F, H = None, None
    fm, hm = np.zeros(len(p0), bool), np.zeros(len(p0), bool)
    errors = []
    cv2.setRNGSeed(0)
    try:
        if len(p0) >= 8:
            candidate, found = cv2.findFundamentalMat(p0, p1, cv2.USAC_MAGSAC, config.f_threshold_px, 0.999, 10000)
            if candidate is not None and candidate.shape == (3, 3) and np.isfinite(candidate).all() and found is not None:
                F, fm = candidate, found.ravel().astype(bool)
    except cv2.error as error:
        errors.append(f"fundamental_matrix: {error}")
    try:
        if len(p0) >= 4:
            Hn, found = cv2.findHomography(p0, p1, cv2.USAC_MAGSAC, config.h_threshold_px, maxIters=10000, confidence=0.999)
            if Hn is not None and Hn.shape == (3, 3) and np.isfinite(Hn).all() and found is not None:
                H = np.linalg.inv(scales[1]) @ Hn @ scales[0]
                hm = found.ravel().astype(bool)
    except cv2.error as error:
        errors.append(f"homography: {error}")
    hull_f = [hull_fraction(points[fm], size) for points, size in zip([points0, points1], sizes)]
    hull_h = [hull_fraction(points[hm], size) for points, size in zip([points0, points1], sizes)]
    h_ratio, f_ratio = float(hm.mean()) if len(hm) else 0.0, float(fm.mean()) if len(fm) else 0.0
    planar = (hm.sum() >= config.planar_min_inliers and h_ratio >= config.planar_min_ratio
              and min(hull_h) >= config.planar_min_hull_fraction and safe_homography(H, sizes))
    use_h = (planar and config.assume_planar) or hm.sum() > fm.sum()
    selected, selected_hull = (hm, hull_h) if use_h else (fm, hull_f)
    reasons = []
    if selected.sum() < config.min_inliers:
        reasons.append("few_geometric_inliers")
    if not len(selected) or selected.mean() < config.min_inlier_ratio:
        reasons.append("low_inlier_ratio")
    if min(selected_hull) < config.min_hull_fraction:
        reasons.append("localized_or_degenerate_support")
    mode = "insufficient_support" if reasons else "planar_extent" if planar and config.assume_planar else "matched_support"
    if mode == "planar_extent":
        masks = planar_masks(H, sizes, config)
    elif mode == "matched_support":
        masks = [support_mask(points[selected], size, config) for points, size in zip([points0, points1], sizes)]
    else:
        masks = [np.zeros(mask_shape(size, config)[0], np.uint8) for size in sizes]
    original_f = scales[1].T @ F @ scales[0] if F is not None else None
    metrics = {"matches": len(points0), "F_inliers": int(fm.sum()), "H_inliers": int(hm.sum()),
               "F_ratio": f_ratio, "H_ratio": h_ratio, "F_hull_fractions": hull_f, "H_hull_fractions": hull_h,
               "selected_inliers": int(selected.sum()), "selected_geometry": "H" if use_h else "F",
               "mode": mode, "rejection_reasons": reasons, "geometry_errors": errors,
               "mask_fractions": [float((mask > 0).mean()) for mask in masks],
               "H_image0_to_image1": H.tolist() if H is not None else None,
               "F_original_pixels": original_f.tolist() if original_f is not None else None,
               "F_normalized": F.tolist() if F is not None else None,
               "geometry_long_edge": config.geometry_long_edge}
    return metrics, tuple(masks), selected, fm, hm


def polygons(mask, size, config):
    """Original-pixel exterior contours and holes; masks remain authoritative."""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    _, factor = mask_shape(size, config)

    def contour_points(contour):
        points = cv2.approxPolyDP(contour, 2.0, True).reshape(-1, 2) / factor
        if len(points) < 3:
            points = contour.reshape(-1, 2) / factor
        points = np.clip(points, [0, 0], [size[0] - 1, size[1] - 1])
        return points.round(1).tolist()

    regions = []
    for index, contour in enumerate(contours):
        if hierarchy[0, index, 3] != -1 or cv2.contourArea(contour) < 4:
            continue
        holes = []
        child = hierarchy[0, index, 2]
        while child != -1:
            holes.append(contour_points(contours[child]))
            child = hierarchy[0, child, 0]
        regions.append({"exterior": contour_points(contour), "holes": holes})
    return regions
