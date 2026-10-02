"""What a collector returns."""

import dataclasses


@dataclasses.dataclass(frozen=True)
class Collection:
    # Appended to the job's prompt; None when there is nothing to do.
    text: str | None
    # Problems worth telling me about (eg. a repo that could not be read).
    errors: tuple[str, ...] = ()
