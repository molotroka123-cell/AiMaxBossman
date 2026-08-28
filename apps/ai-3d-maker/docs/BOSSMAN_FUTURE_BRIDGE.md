# Future BOSSMAN bridge

After V2.2 foundation closes:

Commands:
- `/3d make`
- `/3d validate`
- `/3d slice`
- `/3d calibrate`

Allowed autonomous loop:
spec → CAD → validate → revise → validate

Disallowed:
generate → automatically start physical printer

BOSSMAN may remember versioned printer/calibration profiles and successful
design lessons, but must not turn one bad print into a universal compensation.
