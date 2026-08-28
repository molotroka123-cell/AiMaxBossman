"""AI WebCam Vision — operational analytics for a fixed clinic camera.

Independent workload service. It never imports the BOSSMAN control plane, and
the control plane never imports it: the only coupling is the HTTP contract in
:mod:`ai_webcam_vision.api`.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
