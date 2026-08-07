class K3Error(Exception):
    pass


class K3BuildError(K3Error):
    def __init__(self, exit_code, stdout, stderr):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"Build failed with exit code {exit_code}: {stderr[:200]}")


class K3CleanupError(K3Error):
    def __init__(self, message, partial=False):
        self.partial = partial
        super().__init__(message)
