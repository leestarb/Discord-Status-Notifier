"""Special exceptions for logging"""


class StatusNotifierException(Exception):
    pass


class InvalidHTTPStatusError(StatusNotifierException):
    pass
