import subprocess
from pathlib import Path

import pytest
from core.k3.exceptions import K3Error, K3BuildError, K3CleanupError


class TestK3BuildError:
    def test_attributes(self):
        e = K3BuildError(exit_code=1, stdout="out", stderr="err")
        assert e.exit_code == 1
        assert e.stdout == "out"
        assert e.stderr == "err"
        assert "exit code 1" in str(e)
        assert isinstance(e, K3Error)


class TestK3CleanupError:
    def test_default_not_partial(self):
        e = K3CleanupError("bad")
        assert e.partial is False

    def test_partial_flag(self):
        e = K3CleanupError("bad", partial=True)
        assert e.partial is True
        assert isinstance(e, K3Error)
