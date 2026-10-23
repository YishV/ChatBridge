class GatewayError(Exception):
    """A fixed-code gateway failure without credentials or chat content."""


class GatewayFatalError(GatewayError):
    """An identity or integrity failure requires immediate gateway shutdown."""
