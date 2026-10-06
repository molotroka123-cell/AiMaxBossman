"""The fixed-port test lock really excludes a second holder and releases for the next one."""
import pytest

from ._port_lock import exclusive_port_lock


def test_second_holder_is_refused_while_the_first_holds_and_gets_it_after_release(tmp_path):
    path = tmp_path / "port.lock"
    with exclusive_port_lock(path, timeout=5):
        with pytest.raises(TimeoutError):                  # bad case: a concurrent holder must NOT get in
            with exclusive_port_lock(path, timeout=0.5):
                pytest.fail("two holders at once")
    with exclusive_port_lock(path, timeout=5):             # legit case: free after release
        pass
