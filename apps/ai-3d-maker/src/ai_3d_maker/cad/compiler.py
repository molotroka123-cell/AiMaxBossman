"""DesignSpec -> triangle mesh, deterministically.

Two artifacts come out of a spec:
  * `compile_scad`  — OpenSCAD source, human-readable and reproducible, usable
                      by an external OpenSCAD install or by the user directly;
  * `compile_mesh`  — the actual geometry, built in-process from watertight
                      primitives combined by a real CSG kernel.

`compile_mesh` is the path this app trusts. It never returns a mesh it has not
combined properly: if booleans are needed and no CSG backend exists, it raises
CapabilityUnavailableError rather than concatenating triangle soup.

It also checks that every boolean did what the spec said it would. A hole
placed off the part removes nothing, and a solid with no hole in it is a
perfectly valid mesh — watertight, manifold, printable, and wrong. Nothing
downstream can catch that, because downstream only ever sees the geometry, not
the intent. So the compiler measures the volume before and after each feature
and refuses a `cut` or `intersect` that changed nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import CapabilityUnavailableError, InvalidSpecError
from ..mesh import Mesh
from ..spec import DesignSpec, Feature
from . import csg, primitives

DEFAULT_SEGMENTS = primitives.DEFAULT_SEGMENTS


# A boolean that moves less than this fraction of the running volume did not
# really do anything; the difference is kernel noise, not material.
NO_EFFECT_RELATIVE_TOLERANCE = 1e-9


@dataclass(slots=True)
class CompileResult:
    mesh: Mesh
    engine: str
    backend: str
    features_applied: int
    notes: list[str] = field(default_factory=list)
    # One entry per boolean: what it was asked to do and what it actually did
    # to the volume. This is the audit trail for "the hole is really there".
    feature_effects: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "engine": self.engine,
            "csg_backend": self.backend,
            "features_applied": self.features_applied,
            "triangles": len(self.mesh.faces),
            "notes": self.notes,
            "feature_effects": self.feature_effects,
        }


def _primitive_mesh(feature: Feature, segments: int) -> Mesh:
    p = feature.primitive
    seg = p.segments or segments
    if p.kind == "box":
        m = primitives.box(tuple(p.size_mm), center=p.center)
    elif p.kind == "cylinder":
        d, h = p.size_mm
        m = primitives.cylinder(d, h, segments=seg, center=p.center)
    elif p.kind == "sphere":
        m = primitives.sphere(p.size_mm[0], segments=seg)
    else:  # pragma: no cover - schema restricts kinds
        raise InvalidSpecError(f"unsupported primitive kind {p.kind!r}")
    m = primitives.apply_rotation(m, tuple(feature.transform.rotate_deg))
    return m.translated(tuple(feature.transform.translate_mm))


def compile_mesh(spec: DesignSpec, *, segments: int = DEFAULT_SEGMENTS) -> CompileResult:
    backend = csg.available_backend()
    needs_boolean = len(spec.features) > 1
    if needs_boolean and not backend.available:
        raise CapabilityUnavailableError(
            "this DesignSpec needs boolean operations but no CSG backend is installed",
            detail={"backend": backend.as_dict(), "features": len(spec.features)},
        )

    notes: list[str] = []
    effects: list[dict] = []
    result: Mesh | None = None
    for feature in spec.features:
        solid = _primitive_mesh(feature, segments)
        if result is None:
            if feature.operation != "add":
                raise InvalidSpecError("first feature must be an 'add'")
            result = solid
            continue
        before = result.volume()
        try:
            if feature.operation == "add":
                result = csg.union(result, solid)
            elif feature.operation == "cut":
                result = csg.difference(result, solid)
            else:
                result = csg.intersection(result, solid)
        except ValueError as exc:
            raise InvalidSpecError(
                f"feature {feature.primitive.id!r} ({feature.operation}) produced no solid: {exc}"
            ) from exc
        after = result.volume()
        effects.append({
            "feature": feature.primitive.id,
            "operation": feature.operation,
            "volume_before_mm3": before,
            "volume_after_mm3": after,
        })
        _check_effect(feature, before, after, notes)

    assert result is not None  # guaranteed by DesignSpec min_length=1
    return CompileResult(
        mesh=result,
        engine="native",
        backend=backend.name if needs_boolean else "not-needed",
        features_applied=len(spec.features),
        notes=notes,
        feature_effects=effects,
    )


def _check_effect(feature: Feature, before: float, after: float, notes: list[str]) -> None:
    """Did this feature do what the spec said it would?"""
    changed = abs(after - before) > max(abs(before), 1.0) * NO_EFFECT_RELATIVE_TOLERANCE
    if changed:
        return
    name = feature.primitive.id
    if feature.operation in {"cut", "intersect"}:
        raise InvalidSpecError(
            f"feature {name!r} ({feature.operation}) removed no material: the body is "
            f"{before:g} mm^3 before and after. The primitive does not reach the solid it "
            "is meant to modify, so the finished part would have no such feature.",
            detail={
                "feature": name,
                "operation": feature.operation,
                "volume_before_mm3": before,
                "volume_after_mm3": after,
            },
        )
    notes.append(
        f"feature {name!r} (add) changed nothing: it lies entirely inside the existing body"
    )


# --------------------------------------------------------------- SCAD source
def _vec(values) -> str:
    return "[" + ", ".join(f"{float(v):.6g}" for v in values) + "]"


def _scad_primitive(feature: Feature, segments: int) -> str:
    p = feature.primitive
    seg = p.segments or segments
    if p.kind == "box":
        body = f"cube({_vec(p.size_mm)}, center={'true' if p.center else 'false'});"
    elif p.kind == "cylinder":
        d, h = p.size_mm
        body = f"cylinder(d={d:.6g}, h={h:.6g}, center={'true' if p.center else 'false'}, $fn={seg});"
    else:
        body = f"sphere(d={p.size_mm[0]:.6g}, $fn={seg});"
    if any(abs(float(v)) > 1e-12 for v in feature.transform.rotate_deg):
        body = f"rotate({_vec(feature.transform.rotate_deg)}) {{ {body} }}"
    if any(abs(float(v)) > 1e-12 for v in feature.transform.translate_mm):
        body = f"translate({_vec(feature.transform.translate_mm)}) {{ {body} }}"
    return body


def compile_scad(spec: DesignSpec, *, segments: int = DEFAULT_SEGMENTS) -> str:
    """Deterministic OpenSCAD source. Same spec in, byte-identical text out."""
    lines = [
        "// AI 3D Maker deterministic DesignSpec compiler",
        f"// design: {spec.name}",
        "// units: mm",
        "",
    ]
    body: list[str] = []
    current: list[str] = []
    # Fold the feature list into nested difference()/intersection() blocks so the
    # SCAD text matches the same left-to-right semantics as compile_mesh.
    for feature in spec.features:
        piece = _scad_primitive(feature, segments)
        if not current:
            current = [f"  {piece}"]
            continue
        if feature.operation == "add":
            current = ["union() {", "\n".join(current), f"  {piece}", "}"]
        elif feature.operation == "cut":
            current = ["difference() {", "\n".join(current), f"  {piece}", "}"]
        else:
            current = ["intersection() {", "\n".join(current), f"  {piece}", "}"]
    body.extend(current)
    return "\n".join(lines + body) + "\n"
