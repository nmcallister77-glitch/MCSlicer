"""Fusion-style modular slicer engine.

This module intentionally keeps a stable API for the GUI and script callers:
- mesh loading + scaling helpers
- slicing at explicit planes
- optional interlocking notch generation
- 2D nesting to sheet space
- DXF export (individual + nested)
- STEP export when CadQuery exists, STL fallback otherwise
"""
from __future__ import annotations

import math
import re
import shutil
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import shapely.affinity as sa
import shapely.geometry as sg
import shapely.ops as so
import trimesh

try:
    import ezdxf
except Exception:  # pragma: no cover - optional dependency
    ezdxf = None

try:
    import cadquery as cq
    from cadquery import exporters as cq_exporters
except Exception:  # pragma: no cover - optional dependency
    cq = None
    cq_exporters = None


PathLike = Union[str, Path]


class SlicerEngine:
    def __init__(self, file_path: PathLike):
        self.file_path = str(Path(file_path))
        self.mesh = self._load_mesh(self.file_path)

    # -----------------------------
    # Mesh + transforms
    # -----------------------------
    @staticmethod
    def _load_mesh(path: PathLike) -> trimesh.Trimesh:
        loaded = trimesh.load(str(path), force="scene", process=True)

        if isinstance(loaded, trimesh.Scene):
            meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
            if not meshes:
                raise ValueError("The loaded 3D scene contains no mesh geometry.")
            mesh = trimesh.util.concatenate(meshes)
        elif isinstance(loaded, trimesh.Trimesh):
            mesh = loaded
        else:
            raise ValueError(f"Unsupported geometry type: {type(loaded).__name__}")

        if mesh.vertices.size == 0 or mesh.faces.size == 0:
            raise ValueError("The loaded model has no vertices or triangular faces.")

        # Trimesh API differs by version; use guarded cleanup calls.
        try:
            if hasattr(mesh, "remove_duplicate_faces"):
                mesh.remove_duplicate_faces()
            elif hasattr(mesh, "unique_faces") and hasattr(mesh, "update_faces"):
                mesh.update_faces(mesh.unique_faces())
        except Exception:
            pass

        try:
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass

        try:
            mesh.process(validate=True)
        except TypeError:
            mesh.process()
        return mesh

    def get_bounds_size(self) -> Tuple[float, float, float]:
        return tuple(float(v) for v in self.mesh.extents)

    def get_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        bounds = np.asarray(self.mesh.bounds, dtype=float)
        return bounds[0], bounds[1]

    def get_center(self) -> np.ndarray:
        return np.asarray(self.mesh.bounding_box.centroid, dtype=float)

    def set_scale_by_dimension(self, val: float, axis: str = "x", uniform: bool = True) -> None:
        target = float(val)
        if target <= 0:
            raise ValueError("Target dimension must be greater than zero.")

        axis_idx = {"x": 0, "y": 1, "z": 2}[axis.lower()]
        current = float(self.mesh.extents[axis_idx])
        if current <= 1e-9:
            raise ValueError(f"The model has zero size on the {axis.upper()} axis.")

        factor = target / current
        factors = np.ones(3, dtype=float)
        if uniform:
            factors[:] = factor
        else:
            factors[axis_idx] = factor

        matrix = np.eye(4)
        matrix[0, 0], matrix[1, 1], matrix[2, 2] = factors
        self.mesh.apply_transform(matrix)

    # -----------------------------
    # Geometry helpers
    # -----------------------------
    @staticmethod
    def _safe_filename(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name).strip())
        cleaned = cleaned.strip("._")
        return cleaned or "unnamed_part"

    @staticmethod
    def _normalize(vector: Sequence[float]) -> np.ndarray:
        v = np.asarray(vector, dtype=float)
        n = float(np.linalg.norm(v))
        if n <= 1e-12:
            raise ValueError("Normal vector has zero length.")
        return v / n

    @staticmethod
    def _plane_basis(normal: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
        n = SlicerEngine._normalize(normal)
        up = np.array([0.0, 0.0, 1.0])
        u = np.cross(up, n)
        if float(np.linalg.norm(u)) <= 1e-9:
            u = np.array([1.0, 0.0, 0.0])
        else:
            u = u / float(np.linalg.norm(u))
        v = np.cross(n, u)
        v = v / float(np.linalg.norm(v))
        return u, v

    @staticmethod
    def _polygonal_only(geom):
        if geom is None or geom.is_empty:
            return None
        if isinstance(geom, (sg.Polygon, sg.MultiPolygon)):
            return geom
        if hasattr(geom, "geoms"):
            polygons = [g for g in geom.geoms if isinstance(g, sg.Polygon)]
            if polygons:
                return so.unary_union(polygons)
        return None

    @staticmethod
    def _iter_polygons(geometry) -> Iterable[sg.Polygon]:
        if geometry is None or geometry.is_empty:
            return []
        if isinstance(geometry, sg.Polygon):
            return [geometry]
        if isinstance(geometry, sg.MultiPolygon):
            return [poly for poly in geometry.geoms if not poly.is_empty]
        return []

    @staticmethod
    def _normal_from_angle(angle_deg: float) -> np.ndarray:
        angle = math.radians(float(angle_deg))
        return SlicerEngine._normalize([math.cos(angle), math.sin(angle), 0.0])

    def _build_piece_mesh(self, poly, transform: np.ndarray, thickness: float) -> Optional[trimesh.Trimesh]:
        try:
            mesh_parts = []
            for polygon in self._iter_polygons(poly):
                poly_mesh = trimesh.creation.extrude_polygon(polygon, height=float(thickness))
                poly_mesh.apply_translation([0.0, 0.0, -float(thickness) / 2.0])
                mesh_parts.append(poly_mesh)

            if not mesh_parts:
                return None

            piece_mesh = trimesh.util.concatenate(mesh_parts) if len(mesh_parts) > 1 else mesh_parts[0]
            piece_mesh.apply_transform(np.asarray(transform, dtype=float))
            return piece_mesh
        except Exception:
            return None

    # -----------------------------
    # Slicing
    # -----------------------------
    def _slice_one_plane(
        self,
        normal: Sequence[float],
        origin: Sequence[float],
        label: str,
        axis: str,
        material_thickness: float,
        kerf: float,
    ) -> Optional[dict]:
        normal = self._normalize(normal)
        origin = np.asarray(origin, dtype=float)
        u, v = self._plane_basis(normal)

        section = self.mesh.section(plane_origin=origin, plane_normal=normal)
        if section is None:
            return None

        loops = []
        try:
            discrete = section.discrete
        except Exception:
            discrete = []

        for loop3d in discrete:
            points = np.asarray(loop3d, dtype=float)
            if len(points) < 3:
                continue

            local = np.column_stack(((points - origin) @ u, (points - origin) @ v))
            if not np.allclose(local[0], local[-1]):
                local = np.vstack((local, local[0]))

            poly = sg.Polygon(local)
            if not poly.is_valid:
                poly = poly.buffer(0)

            poly = self._polygonal_only(poly)
            if poly is not None and not poly.is_empty and float(poly.area) > 1e-8:
                loops.append(poly)

        if not loops:
            return None

        profile = self._polygonal_only(so.unary_union(loops))
        if profile is None or profile.is_empty:
            return None

        if kerf > 0:
            # Outward offset preserves target dimensions after material removal.
            profile = self._polygonal_only(profile.buffer(float(kerf) / 2.0, join_style=2))

        if profile is None or profile.is_empty:
            return None

        transform = np.eye(4)
        transform[:3, 0] = u
        transform[:3, 1] = v
        transform[:3, 2] = normal
        transform[:3, 3] = origin

        piece_mesh = self._build_piece_mesh(profile, transform, float(material_thickness))

        return {
            "poly": profile,
            "axis": str(axis),
            "family": str(axis),
            "tf": transform,
            "mesh": piece_mesh,
            "normal": normal.copy(),
            "origin": origin.copy(),
            "label": str(label),
            "material_thickness": float(material_thickness),
            "slotted": False,
            "notch_count": 0,
        }

    def slice_at_planes(self, planes: List[dict], kerf: float = 0.15, material_thickness: float = 3.0) -> List[dict]:
        pieces = []

        def family_rank(family_name: str) -> int:
            family_name = str(family_name).lower()
            if family_name == "primary":
                return 0
            if family_name == "secondary":
                return 1
            return 2

        for index, plane in enumerate(planes, start=1):
            piece = self._slice_one_plane(
                plane["normal"],
                plane["origin"],
                plane.get("label") or f"P{index:02d}",
                plane.get("axis", "custom"),
                float(material_thickness),
                float(kerf),
            )
            if not piece:
                continue

            piece["id"] = plane.get("id", f"part-{index:04d}")
            piece["label"] = str(plane.get("label") or f"P{index:03d}")
            piece["family"] = plane.get("family", plane.get("axis", "custom"))
            piece["axis"] = piece["family"]
            piece["display_axis"] = plane.get("display_axis", "")
            piece["order"] = int(plane.get("order", index))

            # Keep a deterministic signed position along the plane normal so
            # part order is stable regardless of insertion sequence.
            normal = self._normalize(piece["normal"])
            origin = np.asarray(piece["origin"], dtype=float)
            piece["_plane_pos"] = float(normal @ origin)
            piece["_family_rank"] = family_rank(piece["family"])
            pieces.append(piece)

        if not pieces:
            raise ValueError(
                "No closed slice profiles were produced at the specified planes. "
                "Check that each plane intersects the model."
            )

        # Fusion-like ordering: primary -> secondary -> custom, then by plane
        # location in each family. Explicit plane order is retained as a tiebreaker.
        pieces.sort(
            key=lambda p: (
                int(p.get("_family_rank", 2)),
                float(p.get("_plane_pos", 0.0)),
                int(p.get("order", 0)),
                str(p.get("label", "")),
            )
        )

        for final_index, piece in enumerate(pieces, start=1):
            piece["order"] = final_index
            piece.pop("_plane_pos", None)
            piece.pop("_family_rank", None)

        return pieces

    def _slice_family(
        self,
        count: int,
        angle_deg: float,
        axis_name: str,
        material_thickness: float,
        kerf: float,
    ) -> List[dict]:
        count = max(1, int(count))
        normal = self._normal_from_angle(angle_deg)
        projected = np.asarray(self.mesh.vertices, dtype=float) @ normal
        low, high = float(projected.min()), float(projected.max())
        if high - low <= 1e-9:
            return []

        offsets = np.linspace(low, high, count + 2)[1:-1]
        center = self.get_center()
        prefix = axis_name[0].upper()
        pieces = []

        for index, offset in enumerate(offsets, start=1):
            origin = center + (offset - float(center @ normal)) * normal
            piece = self._slice_one_plane(
                normal,
                origin,
                f"{prefix}{index:02d}",
                axis_name,
                material_thickness,
                kerf,
            )
            if piece:
                piece["id"] = f"{axis_name}-{index:04d}"
                piece["order"] = index
                piece["display_axis"] = axis_name.upper()
                pieces.append(piece)

        return pieces

    def generate_interlocking_grid(
        self,
        count_primary: int = 6,
        count_secondary: int = 6,
        angle_primary_deg: float = 0,
        angle_secondary_deg: float = 90,
        material_thickness: float = 3.0,
        kerf: float = 0.15,
    ) -> List[dict]:
        thickness = float(material_thickness)
        kerf = float(kerf)
        if thickness <= 0:
            raise ValueError("Material thickness must be greater than zero.")
        if kerf < 0:
            raise ValueError("Kerf cannot be negative.")

        primary = self._slice_family(count_primary, angle_primary_deg, "primary", thickness, kerf)
        secondary = self._slice_family(count_secondary, angle_secondary_deg, "secondary", thickness, kerf)
        pieces = primary + secondary

        if not pieces:
            raise ValueError(
                "No closed slice profiles were produced. "
                "The mesh may be open or the requested planes may not intersect it."
            )

        for i, piece in enumerate(pieces, start=1):
            piece["order"] = i

        return pieces

    def auto_generate_planes(self, direction, count, label_prefix="P", axis="custom") -> List[dict]:
        normal = self._normalize(direction)
        vertices = np.asarray(self.mesh.vertices, dtype=float)
        projected = vertices @ normal
        low, high = float(projected.min()), float(projected.max())
        if high - low <= 1e-9:
            return []

        count = max(1, int(count))
        offsets = np.linspace(low, high, count + 2)[1:-1]
        center = self.get_center()

        planes = []
        for i, offset in enumerate(offsets, start=1):
            origin = center + (offset - float(center @ normal)) * normal
            planes.append(
                {
                    "normal": normal.tolist(),
                    "origin": origin.tolist(),
                    "label": f"{label_prefix}{i:02d}",
                    "axis": axis,
                    "family": axis,
                    "order": i,
                }
            )
        return planes

    def preview_plane_rectangles(self, planes: List[dict]) -> List[dict]:
        bounds_lo, bounds_hi = self.get_bounds()
        corners_3d = np.array(
            [
                [bounds_lo[0], bounds_lo[1], bounds_lo[2]],
                [bounds_hi[0], bounds_lo[1], bounds_lo[2]],
                [bounds_hi[0], bounds_hi[1], bounds_lo[2]],
                [bounds_lo[0], bounds_hi[1], bounds_lo[2]],
                [bounds_lo[0], bounds_lo[1], bounds_hi[2]],
                [bounds_hi[0], bounds_lo[1], bounds_hi[2]],
                [bounds_hi[0], bounds_hi[1], bounds_hi[2]],
                [bounds_lo[0], bounds_hi[1], bounds_hi[2]],
            ]
        )

        rects = []
        for plane in planes:
            normal = self._normalize(plane["normal"])
            origin = np.asarray(plane["origin"], dtype=float)
            u, v = self._plane_basis(normal)

            rel = corners_3d - origin
            us = rel @ u
            vs = rel @ v
            umin, umax = float(us.min()), float(us.max())
            vmin, vmax = float(vs.min()), float(vs.max())

            local = np.array(
                [
                    [umin, vmin, 0],
                    [umax, vmin, 0],
                    [umax, vmax, 0],
                    [umin, vmax, 0],
                ]
            )
            world = origin[np.newaxis, :] + local[:, 0:1] * u[np.newaxis, :] + local[:, 1:2] * v[np.newaxis, :]

            rects.append(
                {
                    "corners": world,
                    "label": plane.get("label", ""),
                    "axis": plane.get("axis", "custom"),
                    "normal": normal.tolist(),
                    "origin": origin.tolist(),
                }
            )

        return rects

    # -----------------------------
    # Interlocking notches
    # -----------------------------
    @staticmethod
    def _notch_polygon(width, depth, shape, dovetail_ratio=0.6, tslot_head=1.6, corner_radius=0.4):
        hw = width / 2.0

        if shape == "dovetail":
            base_hw = hw
            mouth_hw = hw * max(0.2, min(0.95, dovetail_ratio))
            return sg.Polygon([(-mouth_hw, 0.0), (mouth_hw, 0.0), (base_hw, depth), (-base_hw, depth)])

        if shape == "tslot":
            neck = hw
            head = hw * max(1.05, tslot_head)
            neck_depth = depth * 0.55
            return sg.Polygon(
                [
                    (-neck, 0.0),
                    (neck, 0.0),
                    (neck, neck_depth),
                    (head, neck_depth),
                    (head, depth),
                    (-head, depth),
                    (-head, neck_depth),
                    (-neck, neck_depth),
                ]
            )

        if shape == "rounded":
            rect = sg.box(-hw, 0.0, hw, depth)
            r = min(corner_radius, hw * 0.9, depth * 0.45)
            if r > 1e-4:
                inner = sg.box(-hw + r, 0.0, hw - r, depth - r).buffer(r, join_style=1)
                rect = rect.union(inner)
            return rect

        return sg.box(-hw, 0.0, hw, depth)

    def _crossing_segment(self, piece_a: dict, piece_b: dict):
        na = self._normalize(piece_a["normal"])
        nb = self._normalize(piece_b["normal"])

        line_dir = np.cross(na, nb)
        norm = float(np.linalg.norm(line_dir))
        if norm <= 1e-9:
            return None
        line_dir /= norm

        oa = np.asarray(piece_a["origin"], dtype=float)
        ob = np.asarray(piece_b["origin"], dtype=float)
        da = float(na @ oa)
        db = float(nb @ ob)

        A = np.array([na, nb, line_dir])
        d = np.array([da, db, float(line_dir @ oa)])

        try:
            point = np.linalg.solve(A, d)
        except np.linalg.LinAlgError:
            return None

        bbox = np.asarray(self.mesh.bounds, dtype=float)
        diag = float(np.linalg.norm(bbox[1] - bbox[0]))
        return point - line_dir * diag, point + line_dir * diag, line_dir

    @staticmethod
    def _extract_line_segments(geometry):
        if geometry.is_empty:
            return []
        if geometry.geom_type == "LineString":
            return [geometry]
        if geometry.geom_type == "MultiLineString":
            return list(geometry.geoms)
        if geometry.geom_type == "GeometryCollection":
            segments = []
            for child in geometry.geoms:
                segments.extend(SlicerEngine._extract_line_segments(child))
            return segments
        return []

    def apply_slots(self, pieces: List[dict], slot_config: dict) -> List[dict]:
        if not slot_config or not slot_config.get("enabled", False):
            return pieces

        shape = str(slot_config.get("shape", "rectangular")).lower()
        count = max(1, int(slot_config.get("count", 1)))
        clearance = float(slot_config.get("clearance", 0.10))
        material_thickness = float(slot_config.get("material_thickness", 3.0))
        configured_width = float(slot_config.get("width", 0.0))
        configured_depth = float(slot_config.get("depth", 0.0))
        target = str(slot_config.get("target", "both")).lower()
        dovetail_ratio = float(slot_config.get("dovetail_ratio", 0.60))
        tslot_head = float(slot_config.get("tslot_head", 1.60))
        corner_radius = float(slot_config.get("corner_radius", 0.40))

        if material_thickness <= 0:
            raise ValueError("Material thickness must be greater than zero.")

        nominal_width = configured_width if configured_width > 0 else material_thickness
        cut_width = nominal_width + clearance
        if cut_width <= 0:
            raise ValueError("The resulting notch width must be greater than zero.")

        primaries = [p for p in pieces if p.get("family", p.get("axis")) == "primary"]
        secondaries = [p for p in pieces if p.get("family", p.get("axis")) == "secondary"]
        if not primaries or not secondaries:
            return pieces

        cuts = {id(piece): [] for piece in pieces}

        def build_notches_for(target_piece: dict, other_piece: dict) -> None:
            crossing = self._crossing_segment(target_piece, other_piece)
            if crossing is None:
                return

            p0_world, p1_world, _ = crossing
            transform = np.asarray(target_piece["tf"], dtype=float)
            u_axis = transform[:3, 0]
            v_axis = transform[:3, 1]
            origin = np.asarray(target_piece["origin"], dtype=float)

            def to_local(point):
                relative = np.asarray(point, dtype=float) - origin
                return np.array([float(relative @ u_axis), float(relative @ v_axis)])

            p0_local = to_local(p0_world)
            p1_local = to_local(p1_world)

            profile = self._polygonal_only(target_piece["poly"])
            if profile is None or profile.is_empty:
                return

            crossing_line = sg.LineString([tuple(p0_local), tuple(p1_local)])
            segments = self._extract_line_segments(crossing_line.intersection(profile))
            if not segments:
                return

            longest_segment = max(segments, key=lambda segment: float(segment.length))
            coordinates = np.asarray(longest_segment.coords, dtype=float)
            if len(coordinates) < 2:
                return

            endpoint_a = coordinates[0]
            endpoint_b = coordinates[-1]
            segment_vector = endpoint_b - endpoint_a
            segment_length = float(np.linalg.norm(segment_vector))
            if segment_length <= 1e-8:
                return

            segment_unit = segment_vector / segment_length
            family = target_piece.get("family", target_piece.get("axis", "custom"))
            if family == "primary":
                mouth = endpoint_a
                inward_direction = segment_unit
            else:
                mouth = endpoint_b
                inward_direction = -segment_unit

            notch_depth = min(configured_depth, segment_length) if configured_depth > 0 else segment_length * 0.5
            if notch_depth <= 1e-8:
                return

            base_notch = self._notch_polygon(
                cut_width,
                notch_depth,
                shape,
                dovetail_ratio=dovetail_ratio,
                tslot_head=tslot_head,
                corner_radius=corner_radius,
            )

            rotation_angle = math.degrees(math.atan2(inward_direction[1], inward_direction[0])) - 90.0
            notch = sa.rotate(base_notch, rotation_angle, origin=(0.0, 0.0), use_radians=False)

            lateral_direction = np.array([-inward_direction[1], inward_direction[0]])
            offsets = [0.0] if count == 1 else np.linspace(-cut_width * (count - 1), cut_width * (count - 1), count)

            for lateral_offset in offsets:
                location = mouth + lateral_direction * float(lateral_offset)
                placed_notch = sa.translate(notch, xoff=float(location[0]), yoff=float(location[1]))
                if placed_notch.intersects(profile):
                    cuts[id(target_piece)].append(placed_notch)

        for primary in primaries:
            for secondary in secondaries:
                if target in ("primary", "both"):
                    build_notches_for(primary, secondary)
                if target in ("secondary", "both"):
                    build_notches_for(secondary, primary)

        result = []
        for piece in pieces:
            new_piece = dict(piece)
            piece_cuts = cuts.get(id(piece), [])
            if piece_cuts:
                cutter = so.unary_union(piece_cuts)
                cut_profile = self._polygonal_only(piece["poly"].difference(cutter))
                if cut_profile is None or cut_profile.is_empty:
                    raise ValueError(f"Notches removed the entire part {piece.get('label', '')}.")
                if not cut_profile.is_valid:
                    cut_profile = self._polygonal_only(cut_profile.buffer(0))
                if cut_profile is not None and not cut_profile.is_empty:
                    new_piece["poly"] = cut_profile
                    new_piece["slotted"] = True
                    new_piece["notch_count"] = len(piece_cuts)

            # Keep preview/export geometry synchronized with polygon edits.
            new_piece["mesh"] = self._build_piece_mesh(
                new_piece.get("poly"),
                np.asarray(new_piece.get("tf", np.eye(4)), dtype=float),
                float(new_piece.get("material_thickness", material_thickness)),
            )
            result.append(new_piece)

        return result

    def validate_interlocking_assembly(
        self,
        pieces: List[dict],
        material_thickness: float,
        clearance: float = 0.0,
    ) -> List[dict]:
        """Return physical-fit problems that make an interlocking build unreliable."""
        thickness = float(material_thickness)
        fit_clearance = max(0.0, float(clearance))
        issues = []

        if thickness <= 0:
            return [{"severity": "error", "code": "invalid_thickness", "message": "Material thickness must be greater than zero."}]

        families = defaultdict(list)
        for piece in pieces:
            family = str(piece.get("family", piece.get("axis", "custom"))).lower()
            families[family].append(piece)

            profile = self._polygonal_only(piece.get("poly"))
            label = str(piece.get("label", piece.get("id", "part")))
            if profile is None or profile.is_empty or float(profile.area) <= 1e-8:
                issues.append({"severity": "error", "code": "empty_profile", "label": label, "message": f"{label} has no usable cut profile."})
                continue

            if bool(piece.get("slotted", False)) and int(piece.get("notch_count", 0)) <= 0:
                issues.append({"severity": "error", "code": "missing_notch", "label": label, "message": f"{label} is marked slotted but has no usable notch."})

        for family, family_pieces in families.items():
            ordered = sorted(
                family_pieces,
                key=lambda piece: float(
                    self._normalize(piece.get("normal", [0.0, 0.0, 1.0]))
                    @ np.asarray(piece.get("origin", [0.0, 0.0, 0.0]), dtype=float)
                ),
            )
            for previous, current in zip(ordered, ordered[1:]):
                normal = self._normalize(previous.get("normal", [0.0, 0.0, 1.0]))
                previous_origin = np.asarray(previous.get("origin", [0.0, 0.0, 0.0]), dtype=float)
                current_origin = np.asarray(current.get("origin", [0.0, 0.0, 0.0]), dtype=float)
                spacing = abs(float(np.dot(current_origin - previous_origin, normal)))
                required = thickness + fit_clearance
                if spacing < required - 1e-6:
                    first_label = previous.get("label", previous.get("id", "part"))
                    second_label = current.get("label", current.get("id", "part"))
                    issues.append({
                        "severity": "error",
                        "code": "plate_collision",
                        "label": f"{first_label}, {second_label}",
                        "labels": [str(first_label), str(second_label)],
                        "message": f"{first_label} and {second_label} are only {spacing:.2f} mm apart; they need at least {required:.2f} mm.",
                    })

        primary = families.get("primary", [])
        secondary = families.get("secondary", [])
        if primary and secondary:
            for piece in primary + secondary:
                label = str(piece.get("label", piece.get("id", "part")))
                if int(piece.get("notch_count", 0)) <= 0:
                    issues.append({
                        "severity": "error",
                        "code": "no_crossing_notch",
                        "label": label,
                        "message": f"{label} has no notch at a crossing, so it cannot lock to the opposite family.",
                    })
        elif len(pieces) > 1:
            issues.append({
                "severity": "warning",
                "code": "single_family",
                "message": "Only one slice family is present; the parts cannot form an interlocking grid.",
            })

        return issues

    # -----------------------------
    # Nesting
    # -----------------------------
    @staticmethod
    def _bounds_size(poly) -> Tuple[float, float]:
        minx, miny, maxx, maxy = poly.bounds
        return float(maxx - minx), float(maxy - miny)

    def nest_pieces(
        self,
        pieces: List[dict],
        sheet_width: float = 300,
        sheet_height: float = 200,
        margin: float = 5.0,
        gap: float = 4.0,
    ):
        width, height = float(sheet_width), float(sheet_height)
        if width <= 0 or height <= 0:
            raise ValueError("Sheet dimensions must be greater than zero.")

        prepared = []
        for piece in pieces:
            geom = self._polygonal_only(piece.get("poly"))
            if geom is None or geom.is_empty:
                continue

            minx, miny, _, _ = geom.bounds
            geom = sa.translate(geom, xoff=-minx, yoff=-miny)
            w, h = self._bounds_size(geom)
            rotated = False

            if (w > width - 2 * margin or h > height - 2 * margin) and h <= width - 2 * margin and w <= height - 2 * margin:
                geom = sa.rotate(geom, 90, origin=(0, 0))
                minx, miny, _, _ = geom.bounds
                geom = sa.translate(geom, xoff=-minx, yoff=-miny)
                w, h = self._bounds_size(geom)
                rotated = True

            if w > width - 2 * margin or h > height - 2 * margin:
                label = piece.get("label", piece.get("axis", ""))
                raise ValueError(
                    f"Slice {label} ({w:.1f} x {h:.1f} mm) does not fit a {width:.1f} x {height:.1f} mm sheet."
                )

            prepared.append((piece, geom, w, h, rotated))

        prepared.sort(key=lambda item: item[3], reverse=True)

        sheets = []
        current = []
        x = margin
        y = margin
        row_height = 0.0

        for piece, geom, w, h, rotated in prepared:
            if x + w > width - margin:
                x = margin
                y += row_height + gap
                row_height = 0.0

            if y + h > height - margin:
                if current:
                    sheets.append(current)
                current = []
                x, y, row_height = margin, margin, 0.0

            placed = dict(piece)
            placed["poly"] = sa.translate(geom, xoff=x, yoff=y)
            placed["nested_rotated"] = rotated
            placed["nested_x"] = float(x)
            placed["nested_y"] = float(y)
            current.append(placed)

            x += w + gap
            row_height = max(row_height, h)

        if current:
            sheets.append(current)

        return sheets, width, height

    # -----------------------------
    # DXF export
    # -----------------------------
    @staticmethod
    def _write_ring(msp, ring, layer: str) -> None:
        coords = [(float(x), float(y)) for x, y in ring.coords]
        if len(coords) >= 3:
            msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": layer})

    def export_part_to_dxf(self, piece: dict, output_filename: PathLike) -> Path:
        output_path = Path(output_filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        geometry = self._polygonal_only(piece.get("poly"))
        if geometry is None or geometry.is_empty:
            raise ValueError(f"Part {piece.get('label', '')} has no exportable profile.")

        if ezdxf is None:
            output_path = output_path.with_suffix(".txt")
            output_path.write_text(geometry.wkt, encoding="utf-8")
            return output_path

        document = ezdxf.new(dxfversion="R2010")
        document.units = ezdxf.units.MM
        document.layers.add("CUT", color=1)
        document.layers.add("LABEL", color=7)
        modelspace = document.modelspace()

        for polygon in self._iter_polygons(geometry):
            self._write_ring(modelspace, polygon.exterior, "CUT")
            for interior in polygon.interiors:
                self._write_ring(modelspace, interior, "CUT")

        label = str(piece.get("label", "PART"))
        label_point = geometry.representative_point()
        modelspace.add_text(label, dxfattribs={"layer": "LABEL", "height": 4.0}).set_placement(
            (float(label_point.x), float(label_point.y))
        )

        document.saveas(str(output_path))
        return output_path

    def export_individual_dxfs(
        self,
        pieces: List[dict],
        output_directory: PathLike,
        filename_template: Optional[str] = None,
        filename_map: Optional[List[str]] = None,
    ) -> List[str]:
        output_directory = Path(output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)

        exported_paths: List[str] = []
        used_names = set()

        ordered_pieces = sorted(pieces, key=lambda item: int(item.get("order", 0)))

        project_name = self._safe_filename(Path(self.file_path).stem)

        for index, piece in enumerate(ordered_pieces, start=1):
            if filename_map and index - 1 < len(filename_map) and filename_map[index - 1]:
                base_name = self._safe_filename(filename_map[index - 1])
            elif filename_template:
                try:
                    formatted = filename_template.format(
                        axis=piece.get("display_axis", ""),
                        slice=index,
                        order=piece.get("order", index),
                        label=piece.get("label", ""),
                        family=piece.get("family", ""),
                        id=piece.get("id", ""),
                        project=project_name,
                    )
                    base_name = self._safe_filename(formatted)
                except Exception:
                    base_name = self._safe_filename(piece.get("label", f"PART-{index:03d}"))
            else:
                label = self._safe_filename(piece.get("label", f"PART-{index:03d}"))
                base_name = f"{index:03d}_{label}"

            unique_name = base_name
            duplicate_index = 2
            while unique_name.lower() in used_names:
                unique_name = f"{base_name}_{duplicate_index}"
                duplicate_index += 1
            used_names.add(unique_name.lower())

            output_path = output_directory / f"{unique_name}.dxf"
            real_path = self.export_part_to_dxf(piece, output_path)
            exported_paths.append(str(real_path))

        return exported_paths

    def export_nested_to_dxf(
        self,
        nested_sheets,
        final_w,
        final_h,
        output_filename: PathLike = "output.dxf",
    ) -> Path:
        output_path = Path(output_filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if ezdxf is None:
            output_path = output_path.with_suffix(".txt")
            output_path.write_text("Nested DXF unavailable (ezdxf not installed).", encoding="utf-8")
            return output_path

        doc = ezdxf.new(dxfversion="R2010")
        doc.units = ezdxf.units.MM
        msp = doc.modelspace()

        doc.layers.add("SHEET", color=8)
        doc.layers.add("PRIMARY", color=4)
        doc.layers.add("SECONDARY", color=6)
        doc.layers.add("CUSTOM", color=2)
        doc.layers.add("LABELS", color=7)

        spacing = float(final_w) + 20.0

        for sheet_index, sheet in enumerate(nested_sheets):
            xoff = sheet_index * spacing
            frame = [(xoff, 0), (xoff + final_w, 0), (xoff + final_w, final_h), (xoff, final_h)]
            msp.add_lwpolyline(frame, close=True, dxfattribs={"layer": "SHEET"})
            msp.add_text(f"SHEET {sheet_index + 1}", dxfattribs={"layer": "LABELS", "height": 8}).set_placement(
                (xoff + 5, float(final_h) - 12)
            )

            for piece in sheet:
                family = str(piece.get("family", piece.get("axis", "custom")))
                layer = "PRIMARY" if family == "primary" else "SECONDARY" if family == "secondary" else "CUSTOM"
                geom = sa.translate(piece["poly"], xoff=xoff)

                for poly in self._iter_polygons(geom):
                    self._write_ring(msp, poly.exterior, layer)
                    for interior in poly.interiors:
                        self._write_ring(msp, interior, layer)

                    label = piece.get("label", "")
                    if label:
                        minx, miny, _, _ = poly.bounds
                        msp.add_text(str(label), dxfattribs={"layer": "LABELS", "height": 6}).set_placement(
                            (float(minx) + 2, float(miny) + 2)
                        )

        doc.saveas(str(output_path))
        return output_path

    # -----------------------------
    # STEP / STL export
    # -----------------------------
    @staticmethod
    def _polygon_to_cadquery_face(polygon: sg.Polygon):
        if cq is None:
            raise RuntimeError("CadQuery is not installed.")

        exterior_points = [cq.Vector(float(x), float(y), 0.0) for x, y in list(polygon.exterior.coords)[:-1]]
        if len(exterior_points) < 3:
            raise ValueError("A profile has fewer than three exterior points.")

        outer_wire = cq.Wire.makePolygon(exterior_points, close=True)
        inner_wires = []

        for interior in polygon.interiors:
            interior_points = [cq.Vector(float(x), float(y), 0.0) for x, y in list(interior.coords)[:-1]]
            if len(interior_points) >= 3:
                inner_wires.append(cq.Wire.makePolygon(interior_points, close=True))

        return cq.Face.makeFromWires(outer_wire, inner_wires)

    @staticmethod
    def _extrude_cadquery_face(face, thickness: float):
        if cq is None:
            raise RuntimeError("CadQuery is not installed.")

        solid = cq.Workplane(face).extrude(until=float(thickness), combine=True).val()
        return solid.translate(cq.Vector(0.0, 0.0, -float(thickness) / 2.0))

    def _piece_to_local_cadquery_solid(self, piece: dict, thickness: float):
        if cq is None:
            raise RuntimeError("CadQuery is not installed.")

        geometry = self._polygonal_only(piece.get("poly"))
        if geometry is None or geometry.is_empty:
            raise ValueError(f"Part {piece.get('label', '')} has no valid profile.")

        if thickness <= 0:
            raise ValueError("Material thickness must be greater than zero.")

        solids = []
        for polygon in self._iter_polygons(geometry):
            face = self._polygon_to_cadquery_face(polygon)
            solids.append(self._extrude_cadquery_face(face, thickness))

        if not solids:
            raise ValueError(f"Part {piece.get('label', '')} did not produce a 3D solid.")

        combined = solids[0]
        for solid in solids[1:]:
            combined = combined.fuse(solid)
        return combined

    @staticmethod
    def _apply_piece_transform_to_cadquery(solid, transform: np.ndarray):
        if cq is None:
            raise RuntimeError("CadQuery is not installed.")

        matrix_values = np.asarray(transform, dtype=float)
        if matrix_values.shape != (4, 4):
            raise ValueError("Part transform must be a 4 x 4 matrix.")

        matrix = cq.Matrix(
            [
                [float(matrix_values[0, 0]), float(matrix_values[0, 1]), float(matrix_values[0, 2]), float(matrix_values[0, 3])],
                [float(matrix_values[1, 0]), float(matrix_values[1, 1]), float(matrix_values[1, 2]), float(matrix_values[1, 3])],
                [float(matrix_values[2, 0]), float(matrix_values[2, 1]), float(matrix_values[2, 2]), float(matrix_values[2, 3])],
            ]
        )
        return solid.transformGeometry(matrix)

    def _piece_to_trimesh(self, piece: dict, thickness: float) -> trimesh.Trimesh:
        geometry = self._polygonal_only(piece.get("poly"))
        if geometry is None or geometry.is_empty:
            raise ValueError(f"Part {piece.get('label', '')} has no valid profile.")

        meshes = []
        for polygon in self._iter_polygons(geometry):
            extruded = trimesh.creation.extrude_polygon(polygon, height=float(thickness))
            extruded.apply_translation([0.0, 0.0, -float(thickness) / 2.0])
            meshes.append(extruded)

        if not meshes:
            raise ValueError(f"Part {piece.get('label', '')} did not produce a 3D mesh.")

        mesh = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]

        transform = np.asarray(piece.get("tf", np.eye(4)), dtype=float)
        if transform.shape == (4, 4):
            mesh.apply_transform(transform)

        return mesh

    @staticmethod
    def _is_file_target(path: Path) -> bool:
        return path.suffix.lower() in {".step", ".stp", ".stl"}

    def export_assembly_to_step(
        self,
        pieces: List[dict],
        output_target: PathLike,
        material_thickness: float,
    ) -> List[str]:
        thickness = float(material_thickness)
        if thickness <= 0:
            raise ValueError("Material thickness must be greater than zero.")

        ordered_pieces = sorted(pieces, key=lambda item: int(item.get("order", 0)))
        if not ordered_pieces:
            raise ValueError("There are no parts to export.")

        target = Path(output_target)
        target_is_file = self._is_file_target(target)

        if target_is_file:
            output_dir = target.parent
            stem = self._safe_filename(target.stem)
        else:
            output_dir = target
            stem = ""

        output_dir.mkdir(parents=True, exist_ok=True)
        exported_paths: List[str] = []

        if cq is not None and cq_exporters is not None:
            assembly = cq.Assembly(name=stem or self._safe_filename(Path(self.file_path).stem))

            for index, piece in enumerate(ordered_pieces, start=1):
                local_solid = self._piece_to_local_cadquery_solid(piece, thickness)
                transform = np.asarray(piece.get("tf", np.eye(4)), dtype=float)
                world_solid = self._apply_piece_transform_to_cadquery(local_solid, transform)

                part_name = self._safe_filename(piece.get("label", f"PART-{index:03d}"))

                if target_is_file and len(ordered_pieces) == 1:
                    output_file = target.with_suffix(".step")
                elif target_is_file:
                    output_file = output_dir / f"{stem}_{index:03d}_{part_name}.step"
                else:
                    output_file = output_dir / f"{index:03d}_{part_name}.step"

                cq_exporters.export(world_solid, str(output_file), exportType="STEP")
                exported_paths.append(str(output_file))

                # Build a true multi-component assembly STEP so Inventor can open
                # the assembled result directly without manual reassembly.
                assembly.add(world_solid, name=f"{index:03d}_{part_name}")

            if target_is_file:
                assembly_file = target.with_suffix(".step")
            else:
                assembly_file = output_dir / f"{self._safe_filename(Path(self.file_path).stem)}_assembly.step"

            assembly.save(str(assembly_file), exportType="STEP")
            exported_paths.append(str(assembly_file))

            return exported_paths

        # CadQuery not available: provide STL fallback instead of failing hard.
        for index, piece in enumerate(ordered_pieces, start=1):
            part_name = self._safe_filename(piece.get("label", f"PART-{index:03d}"))
            if target_is_file and len(ordered_pieces) == 1:
                output_file = target.with_suffix(".stl")
            elif target_is_file:
                output_file = output_dir / f"{stem}_{index:03d}_{part_name}.stl"
            else:
                output_file = output_dir / f"{index:03d}_{part_name}.stl"

            mesh = self._piece_to_trimesh(piece, thickness)
            mesh.export(str(output_file))
            exported_paths.append(str(output_file))

        return exported_paths

    def export_nested_parts_to_step(
        self,
        nested_sheets,
        output_directory: PathLike,
        material_thickness: float,
        sheet_spacing: float = 20.0,
    ) -> List[str]:
        thickness = float(material_thickness)
        if thickness <= 0:
            raise ValueError("Material thickness must be greater than zero.")

        output_path = Path(output_directory)
        output_path.mkdir(parents=True, exist_ok=True)

        if not nested_sheets:
            raise ValueError("There are no nested sheets to export.")

        maximum_sheet_width = 0.0
        for sheet in nested_sheets:
            for piece in sheet:
                _, _, maxx, _ = piece["poly"].bounds
                maximum_sheet_width = max(maximum_sheet_width, float(maxx))

        sheet_offset = maximum_sheet_width + float(sheet_spacing)

        part_number = 0
        exported_paths: List[str] = []

        for sheet_index, sheet in enumerate(nested_sheets, start=1):
            x_offset = (sheet_index - 1) * sheet_offset

            for piece_index, piece in enumerate(sheet, start=1):
                part_number += 1

                nested_piece = dict(piece)
                nested_piece["poly"] = sa.translate(piece["poly"], xoff=x_offset)
                nested_piece["tf"] = np.eye(4)

                part_name = self._safe_filename(piece.get("label", f"PART-{part_number:03d}"))

                if cq is not None and cq_exporters is not None:
                    solid = self._piece_to_local_cadquery_solid(nested_piece, thickness)
                    output_file = output_path / f"S{sheet_index:03d}_{piece_index:03d}_{part_name}.step"
                    cq_exporters.export(solid, str(output_file), exportType="STEP")
                else:
                    mesh = self._piece_to_trimesh(nested_piece, thickness)
                    output_file = output_path / f"S{sheet_index:03d}_{piece_index:03d}_{part_name}.stl"
                    mesh.export(str(output_file))

                exported_paths.append(str(output_file))

        return exported_paths

    # -----------------------------
    # Assembly step planners
    # -----------------------------
    def generate_interlocking_assembly_steps(self, pieces: List[dict]) -> List[dict]:
        """Generate deterministic assembly steps for interlocking slice parts.

        Returns a list of step dictionaries that can be used by UI timelines,
        exporters, or downstream robotic/weld planning tools.
        """
        ordered = sorted(
            pieces,
            key=lambda piece: (
                int(piece.get("order", 0)),
                str(piece.get("family", piece.get("axis", "custom"))),
                str(piece.get("label", "")),
            ),
        )

        steps = []
        for index, piece in enumerate(ordered, start=1):
            steps.append(
                {
                    "step": index,
                    "type": "insert_interlock",
                    "piece_id": piece.get("id", f"piece-{index:03d}"),
                    "label": piece.get("label", ""),
                    "family": piece.get("family", piece.get("axis", "custom")),
                    "slotted": bool(piece.get("slotted", False)),
                    "notch_count": int(piece.get("notch_count", 0)),
                    "transform": np.asarray(piece.get("tf", np.eye(4)), dtype=float).tolist(),
                }
            )

        return steps

    def generate_welded_polygon_assembly_steps(self, polygon_parts: List[dict]) -> List[dict]:
        """Create fit-up and weld sequencing steps for welded polygon builds.

        Expected item fields (best effort):
        - id/label
        - weld_edges: iterable of edge identifiers or edge metadata
        - order (optional)
        """
        ordered = sorted(
            polygon_parts,
            key=lambda part: (
                int(part.get("order", 0)),
                str(part.get("label", part.get("id", ""))),
            ),
        )

        steps = []
        step_no = 1
        for part in ordered:
            part_id = part.get("id", part.get("label", f"part-{step_no:03d}"))

            steps.append(
                {
                    "step": step_no,
                    "type": "fit_up",
                    "part_id": part_id,
                    "label": part.get("label", str(part_id)),
                    "notes": "Position and clamp part for welding.",
                }
            )
            step_no += 1

            weld_edges = list(part.get("weld_edges", []))
            if not weld_edges:
                continue

            for edge_idx, edge in enumerate(weld_edges, start=1):
                steps.append(
                    {
                        "step": step_no,
                        "type": "weld_edge",
                        "part_id": part_id,
                        "edge": edge,
                        "edge_index": edge_idx,
                        "notes": "Perform weld pass along edge.",
                    }
                )
                step_no += 1

        return steps

    # -----------------------------
    # Polygonize + flat pattern
    # -----------------------------
    def _mesh_to_polygon_panels(self, min_area: float = 1e-6):
        """Convert mesh to coplanar polygon panels.

        Returns:
          panels: list of dicts with local 2D polygon and basis
          face_to_panel: map face-index -> panel-index
        """
        mesh = self.mesh
        panels = []
        face_to_panel = {}

        facets = getattr(mesh, "facets", None)
        if facets is None or len(facets) == 0:
            facets = [np.array([idx], dtype=int) for idx in range(len(mesh.faces))]

        for panel_index, face_ids in enumerate(facets):
            face_ids = np.asarray(face_ids, dtype=int)
            if face_ids.size == 0:
                continue

            face_normals = np.asarray(mesh.face_normals[face_ids], dtype=float)
            normal = np.mean(face_normals, axis=0)
            if float(np.linalg.norm(normal)) <= 1e-12:
                continue
            normal = self._normalize(normal)

            tri_vertices = np.asarray(mesh.triangles[face_ids], dtype=float).reshape(-1, 3)
            origin = np.mean(tri_vertices, axis=0)
            u, v = self._plane_basis(normal)

            polys = []
            for tri in np.asarray(mesh.triangles[face_ids], dtype=float):
                local = np.column_stack(((tri - origin) @ u, (tri - origin) @ v))
                poly = sg.Polygon(local)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty and float(poly.area) > min_area:
                    polys.append(poly)

            if not polys:
                continue

            poly2d = self._polygonal_only(so.unary_union(polys))
            if poly2d is None or poly2d.is_empty or float(poly2d.area) <= min_area:
                continue

            saved_idx = len(panels)
            for face_id in face_ids:
                face_to_panel[int(face_id)] = saved_idx

            panels.append(
                {
                    "panel_index": saved_idx,
                    "faces": [int(fid) for fid in face_ids.tolist()],
                    "poly2d": poly2d,
                    "origin": origin,
                    "normal": normal,
                    "u": u,
                    "v": v,
                }
            )

        if not panels:
            raise ValueError("Could not polygonize mesh into panels.")

        return panels, face_to_panel

    @staticmethod
    def _apply_2d_transform(point_xy: Sequence[float], rot_2x2: np.ndarray, trans_xy: np.ndarray) -> np.ndarray:
        p = np.asarray(point_xy, dtype=float)
        return rot_2x2 @ p + trans_xy

    @staticmethod
    def _line_side(a: np.ndarray, b: np.ndarray, p: np.ndarray) -> float:
        ab = b - a
        ap = p - a
        return float(ab[0] * ap[1] - ab[1] * ap[0])

    @staticmethod
    def _rot_from_to(vec_from: np.ndarray, vec_to: np.ndarray) -> np.ndarray:
        af = math.atan2(float(vec_from[1]), float(vec_from[0]))
        at = math.atan2(float(vec_to[1]), float(vec_to[0]))
        ang = at - af
        c = math.cos(ang)
        s = math.sin(ang)
        return np.array([[c, -s], [s, c]], dtype=float)

    def _world_to_panel_local(self, point_3d: np.ndarray, panel: dict) -> np.ndarray:
        rel = np.asarray(point_3d, dtype=float) - np.asarray(panel["origin"], dtype=float)
        return np.array([
            float(rel @ np.asarray(panel["u"], dtype=float)),
            float(rel @ np.asarray(panel["v"], dtype=float)),
        ])

    def _unwrap_panels_with_bends(
        self,
        panels: List[dict],
        face_to_panel: dict,
        bend_min_angle_deg: float = 1.0,
        component_gap: float = 25.0,
    ):
        mesh = self.mesh
        adj_faces = np.asarray(getattr(mesh, "face_adjacency", []), dtype=int)
        adj_edges = np.asarray(getattr(mesh, "face_adjacency_edges", []), dtype=int)
        adj_angles = np.asarray(getattr(mesh, "face_adjacency_angles", []), dtype=float)

        panel_adj = defaultdict(list)
        bend_links = []

        for idx in range(len(adj_faces)):
            f0, f1 = int(adj_faces[idx][0]), int(adj_faces[idx][1])
            p0 = face_to_panel.get(f0)
            p1 = face_to_panel.get(f1)
            if p0 is None or p1 is None or p0 == p1:
                continue

            angle_deg = abs(math.degrees(float(adj_angles[idx]))) if idx < len(adj_angles) else 90.0
            if angle_deg < bend_min_angle_deg:
                continue

            edge = adj_edges[idx] if idx < len(adj_edges) else None
            if edge is None or len(edge) < 2:
                continue

            v0 = np.asarray(mesh.vertices[int(edge[0])], dtype=float)
            v1 = np.asarray(mesh.vertices[int(edge[1])], dtype=float)

            panel_adj[p0].append((p1, v0, v1, angle_deg))
            panel_adj[p1].append((p0, v0, v1, angle_deg))
            bend_links.append((p0, p1, v0, v1, angle_deg))

        placed = {}
        cursor_x = 0.0

        def place_component(root_idx: int):
            nonlocal cursor_x
            queue = deque([root_idx])
            local_placed = {
                root_idx: (np.eye(2), np.zeros(2, dtype=float)),
            }

            while queue:
                parent_idx = queue.popleft()
                parent_panel = panels[parent_idx]
                parent_R, parent_t = local_placed[parent_idx]

                parent_poly = parent_panel["poly2d"]
                parent_ctr = np.array(parent_poly.representative_point().coords[0], dtype=float)
                parent_ctr_g = self._apply_2d_transform(parent_ctr, parent_R, parent_t)

                for child_idx, w0, w1, _angle in panel_adj.get(parent_idx, []):
                    if child_idx in local_placed or child_idx in placed:
                        continue

                    child_panel = panels[child_idx]
                    a0 = self._world_to_panel_local(w0, parent_panel)
                    a1 = self._world_to_panel_local(w1, parent_panel)
                    b0 = self._world_to_panel_local(w0, child_panel)
                    b1 = self._world_to_panel_local(w1, child_panel)

                    g0 = self._apply_2d_transform(a0, parent_R, parent_t)
                    g1 = self._apply_2d_transform(a1, parent_R, parent_t)
                    edge_vec = g1 - g0
                    if float(np.linalg.norm(edge_vec)) <= 1e-9:
                        continue

                    child_ctr = np.array(child_panel["poly2d"].representative_point().coords[0], dtype=float)

                    candidates = []
                    for src0, src1 in ((b0, b1), (b1, b0)):
                        src_vec = src1 - src0
                        if float(np.linalg.norm(src_vec)) <= 1e-9:
                            continue
                        R = self._rot_from_to(src_vec, edge_vec)
                        t = g0 - (R @ src0)
                        ctr_g = self._apply_2d_transform(child_ctr, R, t)
                        side_parent = self._line_side(g0, g1, parent_ctr_g)
                        side_child = self._line_side(g0, g1, ctr_g)
                        opposite = (side_parent == 0.0) or (side_child == 0.0) or (side_parent * side_child < 0.0)
                        candidates.append((opposite, R, t))

                    if not candidates:
                        continue

                    candidates.sort(key=lambda item: 0 if item[0] else 1)
                    _ok, child_R, child_t = candidates[0]
                    local_placed[child_idx] = (child_R, child_t)
                    queue.append(child_idx)

            # Shift this connected component to the right of the previous one.
            component_polys = []
            for panel_idx, (R, t) in local_placed.items():
                poly = panels[panel_idx]["poly2d"]
                transformed = sa.affine_transform(poly, [R[0, 0], R[0, 1], R[1, 0], R[1, 1], t[0], t[1]])
                component_polys.append(transformed)

            minx = min(float(poly.bounds[0]) for poly in component_polys)
            miny = min(float(poly.bounds[1]) for poly in component_polys)
            maxx = max(float(poly.bounds[2]) for poly in component_polys)
            width = maxx - minx

            shift = np.array([cursor_x - minx, -miny], dtype=float)
            for panel_idx, (R, t) in local_placed.items():
                placed[panel_idx] = (R, t + shift)

            cursor_x += width + max(5.0, float(component_gap))

        for panel_idx in range(len(panels)):
            if panel_idx not in placed:
                place_component(panel_idx)

        placed_polys = []
        for panel in panels:
            idx = panel["panel_index"]
            R, t = placed[idx]
            poly = sa.affine_transform(panel["poly2d"], [R[0, 0], R[0, 1], R[1, 0], R[1, 1], t[0], t[1]])
            placed_polys.append({
                "panel_index": idx,
                "poly": poly,
                "transform": (R, t),
            })

        bend_lines = []
        seen = set()
        for p0, p1, w0, w1, angle_deg in bend_links:
            if p0 not in placed or p1 not in placed:
                continue
            key = tuple(sorted((int(p0), int(p1))))
            if key in seen:
                continue
            seen.add(key)

            panel0 = panels[p0]
            R0, t0 = placed[p0]
            l0 = self._world_to_panel_local(w0, panel0)
            l1 = self._world_to_panel_local(w1, panel0)
            g0 = self._apply_2d_transform(l0, R0, t0)
            g1 = self._apply_2d_transform(l1, R0, t0)
            bend_lines.append({
                "from": g0,
                "to": g1,
                "angle_deg": float(angle_deg),
                "panels": (int(p0), int(p1)),
            })

        return placed_polys, bend_lines

    def _compute_flat_pattern_layout(
        self,
        bend_min_angle_deg: float = 1.0,
        component_gap: float = 25.0,
        margin: float = 10.0,
    ):
        panels, face_to_panel = self._mesh_to_polygon_panels()
        placed_panels, bend_lines = self._unwrap_panels_with_bends(
            panels,
            face_to_panel,
            bend_min_angle_deg=bend_min_angle_deg,
            component_gap=component_gap,
        )

        if not placed_panels:
            raise ValueError("Could not create flat pattern panels from mesh.")

        all_minx = min(float(item["poly"].bounds[0]) for item in placed_panels)
        all_miny = min(float(item["poly"].bounds[1]) for item in placed_panels)
        x_shift = float(margin) - all_minx
        y_shift = float(margin) - all_miny

        shifted_panels = []
        for item in placed_panels:
            shifted_panels.append(
                {
                    "panel_index": item["panel_index"],
                    "poly": sa.translate(item["poly"], xoff=x_shift, yoff=y_shift),
                }
            )

        shifted_bends = []
        for bend in bend_lines:
            shifted_bends.append(
                {
                    "from": np.asarray(bend["from"], dtype=float) + np.array([x_shift, y_shift], dtype=float),
                    "to": np.asarray(bend["to"], dtype=float) + np.array([x_shift, y_shift], dtype=float),
                    "angle_deg": bend["angle_deg"],
                    "panels": bend["panels"],
                }
            )

        return shifted_panels, shifted_bends

    def export_flat_pattern_with_bend_lines(
        self,
        output_filename: PathLike,
        bend_min_angle_deg: float = 1.0,
        component_gap: float = 25.0,
        margin: float = 10.0,
    ) -> Path:
        output_path = Path(output_filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        shifted_panels, shifted_bends = self._compute_flat_pattern_layout(
            bend_min_angle_deg=bend_min_angle_deg,
            component_gap=component_gap,
            margin=margin,
        )

        if ezdxf is None:
            output_path = output_path.with_suffix(".txt")
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write("Flat pattern export summary\n")
                fh.write(f"Panels: {len(shifted_panels)}\n")
                fh.write(f"Bend lines: {len(shifted_bends)}\n")
            return output_path

        doc = ezdxf.new(dxfversion="R2010")
        doc.units = ezdxf.units.MM
        msp = doc.modelspace()

        doc.layers.add("FLAT_CUT", color=1)
        doc.layers.add("BEND_LINES", color=5)
        doc.layers.add("PANEL_LABELS", color=3)

        for item in shifted_panels:
            poly = item["poly"]
            for panel_poly in self._iter_polygons(poly):
                self._write_ring(msp, panel_poly.exterior, "FLAT_CUT")
                for hole in panel_poly.interiors:
                    self._write_ring(msp, hole, "FLAT_CUT")

            rep = poly.representative_point()
            msp.add_text(
                f"P{int(item['panel_index']) + 1:03d}",
                dxfattribs={"layer": "PANEL_LABELS", "height": 4.0},
            ).set_placement((float(rep.x), float(rep.y)))

        for bend in shifted_bends:
            p0 = bend["from"]
            p1 = bend["to"]
            msp.add_line(
                (float(p0[0]), float(p0[1])),
                (float(p1[0]), float(p1[1])),
                dxfattribs={"layer": "BEND_LINES"},
            )

        doc.saveas(str(output_path))
        return output_path

    def export_bend_line_sketch_dxf(
        self,
        output_filename: PathLike,
        bend_min_angle_deg: float = 1.0,
        component_gap: float = 25.0,
        margin: float = 10.0,
    ) -> Path:
        """Export only bend lines as a sketch-friendly DXF layer."""
        output_path = Path(output_filename)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _panels, shifted_bends = self._compute_flat_pattern_layout(
            bend_min_angle_deg=bend_min_angle_deg,
            component_gap=component_gap,
            margin=margin,
        )

        if ezdxf is None:
            output_path = output_path.with_suffix(".txt")
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write("Bend sketch export summary\n")
                fh.write(f"Bend lines: {len(shifted_bends)}\n")
            return output_path

        doc = ezdxf.new(dxfversion="R2010")
        doc.units = ezdxf.units.MM
        msp = doc.modelspace()

        doc.layers.add("BEND_SKETCH", color=5)

        for bend in shifted_bends:
            p0 = bend["from"]
            p1 = bend["to"]
            msp.add_line(
                (float(p0[0]), float(p0[1])),
                (float(p1[0]), float(p1[1])),
                dxfattribs={"layer": "BEND_SKETCH"},
            )

        doc.saveas(str(output_path))
        return output_path

    def _export_solid_reference_file(self, output_directory: Path, base_name: str) -> Path:
        """Write a solid file for downstream CAD import.

        Preference order:
        1) copy original mesh/CAD file if recognized
        2) export STL mesh fallback
        """
        source = Path(self.file_path)
        allowed_copy = {".stl", ".step", ".stp", ".obj", ".3mf", ".ply"}

        if source.exists() and source.suffix.lower() in allowed_copy:
            target = output_directory / f"{base_name}_solid{source.suffix.lower()}"
            shutil.copy2(str(source), str(target))
            return target

        target = output_directory / f"{base_name}_solid.stl"
        self.mesh.export(str(target))
        return target

    @staticmethod
    def _try_export_inventor_ipt_from_solid(solid_path: Path, output_ipt_path: Path):
        """Best-effort Inventor automation: open solid and Save As IPT.

        Returns (success: bool, message: str).
        """
        try:
            import win32com.client  # type: ignore
        except Exception as exc:
            return False, f"pywin32 unavailable: {exc}"

        inventor = None
        doc = None
        try:
            inventor = win32com.client.Dispatch("Inventor.Application")
            doc = inventor.Documents.Open(str(solid_path), False)
            doc.SaveAs(str(output_ipt_path), False)
            return True, "Inventor IPT exported via COM automation."
        except Exception as exc:
            return False, str(exc)
        finally:
            try:
                if doc is not None:
                    doc.Close(True)
            except Exception:
                pass

    def export_solid_with_bend_sketch_package(
        self,
        output_directory: PathLike,
        base_name: str,
        bend_min_angle_deg: float = 1.0,
        component_gap: float = 25.0,
        margin: float = 10.0,
        try_export_ipt: bool = True,
    ) -> dict:
        """Export a bend-ready package for Inventor-style workflows.

        Outputs:
        - solid model file (copied source or STL fallback)
        - flat pattern DXF with cut + bend layers
        - bend sketch-only DXF layer
        - optional IPT (best effort via Inventor COM)
        """
        target_dir = Path(output_directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_base = self._safe_filename(base_name)

        solid_path = self._export_solid_reference_file(target_dir, safe_base)

        flat_dxf = self.export_flat_pattern_with_bend_lines(
            target_dir / f"{safe_base}_flat_pattern.dxf",
            bend_min_angle_deg=bend_min_angle_deg,
            component_gap=component_gap,
            margin=margin,
        )

        bend_sketch_dxf = self.export_bend_line_sketch_dxf(
            target_dir / f"{safe_base}_bend_sketch.dxf",
            bend_min_angle_deg=bend_min_angle_deg,
            component_gap=component_gap,
            margin=margin,
        )

        result = {
            "solid": str(solid_path),
            "flat_pattern_dxf": str(flat_dxf),
            "bend_sketch_dxf": str(bend_sketch_dxf),
            "inventor_ipt": None,
            "inventor_status": "IPT export not requested.",
        }

        if try_export_ipt:
            ipt_path = target_dir / f"{safe_base}_bend_model.ipt"
            ok, message = self._try_export_inventor_ipt_from_solid(solid_path, ipt_path)
            if ok and ipt_path.exists():
                result["inventor_ipt"] = str(ipt_path)
                result["inventor_status"] = message
            else:
                result["inventor_status"] = f"IPT export failed: {message}"

        return result


class ProceduralModelEngine:
    """Procedural polygon/primitive model generator for rapid fixture concepts.

    This engine creates simple meshes from 2D polygons and common primitives,
    which can then be passed to SlicerEngine workflows.
    """

    @staticmethod
    def create_from_polygon_points(points_2d: Sequence[Sequence[float]], height: float = 10.0) -> trimesh.Trimesh:
        polygon = sg.Polygon(points_2d)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            raise ValueError("Polygon points did not create a valid area.")
        return trimesh.creation.extrude_polygon(polygon, height=float(height))

    @staticmethod
    def create_primitive(shape: str, size: Sequence[float]) -> trimesh.Trimesh:
        shape_key = str(shape).lower()
        if shape_key in ("box", "square", "rectangle"):
            sx, sy, sz = [float(v) for v in size[:3]]
            return trimesh.creation.box(extents=[sx, sy, sz])
        if shape_key in ("cylinder", "round"):
            radius = float(size[0])
            height = float(size[1]) if len(size) > 1 else float(size[0])
            return trimesh.creation.cylinder(radius=radius, height=height)
        if shape_key in ("triangle", "tri"):
            base = float(size[0])
            height = float(size[1]) if len(size) > 1 else float(size[0])
            depth = float(size[2]) if len(size) > 2 else base * 0.25
            tri = sg.Polygon([(0.0, 0.0), (base, 0.0), (base * 0.5, height)])
            return trimesh.creation.extrude_polygon(tri, depth)
        raise ValueError(f"Unsupported primitive shape: {shape}")

    @staticmethod
    def combine_meshes(meshes: Sequence[trimesh.Trimesh]) -> trimesh.Trimesh:
        valid = [mesh for mesh in meshes if isinstance(mesh, trimesh.Trimesh)]
        if not valid:
            raise ValueError("No valid meshes provided for combination.")
        return trimesh.util.concatenate(valid)
