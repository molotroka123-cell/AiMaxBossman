# Precision CAD pipeline

A phrase such as "two M4 holes" is not necessarily complete. It may mean:
clearance, tapped, heat-set insert, counterbore or countersink.

The Requirement Normalizer should identify material ambiguity before CAD.

Keep nominal CAD dimensions separate from process compensation.

Validation must cover geometry, printer envelope, minimum features, orientation,
supports, tolerance capability, mesh health, slicing and final G-code.

Never promise CAD nominal size equals printed measured size.
