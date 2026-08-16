"""Domain errors, mapped to status codes at the API boundary and nowhere else."""


class MeridianError(Exception):
    """Base for every error this system raises deliberately."""


class NotFoundError(MeridianError):
    """A key that should resolve does not. Becomes a 404."""


class IncompleteError(MeridianError):
    """An operation needs configuration that is missing. Becomes a 422 with findings."""


class ConflictingStateError(MeridianError):
    """The object is not in a state where this is legal. Becomes a 409."""
