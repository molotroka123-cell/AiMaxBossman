# Generative 3D

P1 adapter for figurines/statues/decorative/organic shapes.

Any local/cloud text/image-to-3D engine may implement:
`generate(prompt/images) -> mesh + metadata`

All output still passes mesh repair, scale, manifold/component checks,
printability, slicing and G-code validation.

A beautiful render is not evidence of a printable mesh.
