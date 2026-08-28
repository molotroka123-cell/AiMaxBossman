# G-code safety

Generated G-code is executable machine control.

V1 never starts a physical print automatically.

Seed hard caps:
- nozzle <= 260 C
- bed <= 100 C
- extrusion moves inside 320 × 320 × 400 mm

The scanner additionally flags persistent/reset/firmware-style commands.

A scanner is not a firmware emulator. Test it against the user's real ELEGOO
Cura start/end G-code before production use.
