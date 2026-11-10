"""Goal-line geometry.

The line is a polyline drawn by hand on a video frame (usually 2 points on a
top-down / behind-goal camera, more if the line is slightly curved by lens
distortion).  "Inside the goal" is disambiguated by one extra point clicked
inside the goal mouth.
"""

from __future__ import annotations

import math

import numpy as np


class GoalLine:
    """Polyline goal line with a signed distance convention.

    ``signed_distance`` is positive when the point is on the goal side of the
    nearest line segment and negative on the field side.  Units are pixels of
    the input video.
    """

    def __init__(self, points: list[list[float]], inside_point: list[float]) -> None:
        pts = np.asarray(points, dtype=float)
        if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] != 2:
            raise ValueError("goal_line must be a list of at least two [x, y] points")
        self.points = pts
        self.inside_point = np.asarray(inside_point, dtype=float)
        self._starts = pts[:-1]
        self._vecs = pts[1:] - pts[:-1]
        self._lengths = np.linalg.norm(self._vecs, axis=1)
        if np.any(self._lengths < 1e-6):
            raise ValueError("goal_line has a zero-length segment")

        # Which side of each segment is the goal side?
        rel = self.inside_point - self._starts
        crosses = self._cross(self._vecs, rel) / self._lengths
        self._inside_sign = np.sign(crosses)
        if np.any(self._inside_sign == 0):
            raise ValueError("inside_point lies on the goal line; pick a point inside the goal")

    @staticmethod
    def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]

    def locate(self, point: np.ndarray | tuple[float, float]) -> tuple[float, float, float, int]:
        """Return ``(inside_distance, t, distance_to_line, segment_index)``.

        ``t`` is the unclamped normalised projection on the selected segment:
        values outside ``[0, 1]`` mean the projection falls beyond an endpoint, so
        the crossing happened outside the drawn goal mouth.  ``distance_to_line``
        is the distance to the polyline (endpoints included) and is used only to
        pick the nearest segment.
        """
        p = np.asarray(point, dtype=float)
        rel = p - self._starts
        proj = np.einsum("ij,ij->i", rel, self._vecs) / (self._lengths**2)
        t_clamped = np.clip(proj, 0.0, 1.0)
        closest = self._starts + t_clamped[:, None] * self._vecs
        seg_dist = np.linalg.norm(p - closest, axis=1)
        idx = int(np.argmin(seg_dist))

        perp = self._cross(self._vecs[idx], rel[idx]) / self._lengths[idx]
        signed = float(perp * self._inside_sign[idx])
        return signed, float(proj[idx]), float(seg_dist[idx]), idx

    def inside_distance(self, point: np.ndarray | tuple[float, float]) -> float:
        return self.locate(point)[0]

    @staticmethod
    def required_depth(radius_px: float, judge) -> float:
        """Centre depth required for the ball to be considered over the line."""
        if judge.require_whole_ball:
            return float(radius_px) + float(judge.line_half_width_px)
        return 0.0

    def distance_to_point(self, point) -> float:
        return self.locate(point)[2]

    def shaded_polygon(self, image_width: int, image_height: int, depth: float = 60.0):
        """Quadrilateral strip on the goal side, handy for drawing an overlay."""
        pts = self.points
        a, b = pts[0], pts[-1]
        direction = b - a
        norm = math.hypot(*direction)
        if norm < 1e-6:
            return None
        normal = np.array([-direction[1], direction[0]]) / norm
        # Normal points to the left of a->b; make it point to the goal side.
        rel = self.inside_point - a
        if np.dot(normal, rel) < 0:
            normal = -normal
        quad = np.stack([a, b, b + normal * depth, a + normal * depth])
        quad[:, 0] = np.clip(quad[:, 0], 0, image_width - 1)
        quad[:, 1] = np.clip(quad[:, 1], 0, image_height - 1)
        return quad.astype(np.int32)
