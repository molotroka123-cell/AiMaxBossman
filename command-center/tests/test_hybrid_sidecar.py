
import pytest
import sys
import time
from bcc.hybrid.sidecar import SidecarProcessManager, SidecarConfig, SidecarStatus

def test_sidecar_lifecycle_normal():
    # Run a simple python echo server/subprocess as sidecar test
    config = SidecarConfig(
        name="test_sidecar",
        command=[sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(2)"],
        startup_timeout_s=5.0,
        heartbeat_interval_s=1.0,
    )
    mgr = SidecarProcessManager(config)
    started = mgr.start()
    assert started is True
    assert mgr.status == SidecarStatus.HEALTHY
    assert mgr.pid is not None

    mgr.stop()
    assert mgr.status == SidecarStatus.STOPPED

def test_sidecar_missing_binary():
    config = SidecarConfig(
        name="missing_binary",
        command=["/non/existent/binary/path/12345"],
        startup_timeout_s=1.0,
    )
    mgr = SidecarProcessManager(config)
    started = mgr.start()
    assert started is False
    assert mgr.status == SidecarStatus.CRASHED
