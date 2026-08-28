# Architecture

BOSSMAN / standalone UI
→ Design Intent
→ Requirement Normalizer
→ strict DesignSpec
→ Requirement Gate
→ deterministic CAD (CadQuery/OpenSCAD)
→ STEP/STL
→ Mesh Validator
→ Printer Fit Check
→ real slicer
→ G-code Safety Scan
→ Human Approval

Normal mode uses a constrained feature DSL instead of executing arbitrary
AI-written Python on the host. An advanced sandbox may be added later.

The app is more reliable than ordinary chat-based STL generation because failed
validation feeds another design iteration rather than being hidden.
