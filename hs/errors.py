"""Errors raised while loading, addressing, or viewing a document."""


class HyperscopeError(Exception):
    """A problem the user can be shown, often in the original '? …' form."""

    def __init__(self, message):
        super().__init__(message)
        self.message = str(message)

    def __str__(self):
        return self.message
