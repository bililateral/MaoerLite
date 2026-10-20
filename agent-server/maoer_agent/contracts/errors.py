"""Shared invocation failure contract, independent of model transport."""
class ModelFailure(Exception):
    def __init__(self, code, retryable=False, delay=0, details=None):
        self.code, self.retryable, self.delay = code, retryable, delay
        self.details = details or {}
        super().__init__(code)
