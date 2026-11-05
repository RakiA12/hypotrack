"""Exception types shared across hypotrack."""


class HypxError(Exception):
    """User-facing error. The CLI prints the message and exits with exit_code."""

    exit_code = 1


class RemoteError(HypxError):
    """ssh / rsync / sacct failure. Local state must not have been changed."""

    exit_code = 2


class NotImplementedPhase2(HypxError):
    """Command registered but deferred to phase 2."""

    exit_code = 3

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} is planned for phase 2 and is not implemented yet")
