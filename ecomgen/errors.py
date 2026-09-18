"""Warning and error types shared by generation and the CLI."""


class GenerationWarning(UserWarning):
    """A generated dataset is valid but deviates from the requested behaviour."""


class StockoutError(ValueError):
    """Stock-outs suppressed so much demand that the dataset would be misleading."""
