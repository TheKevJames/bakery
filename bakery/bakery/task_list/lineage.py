"""Lineage: the identity of a task's source, by its link."""


def source(link: str) -> str:
    """`link` without its line anchor, which moves as the code does."""
    return link.split('#', 1)[0]


def of(link: str) -> str:
    """
    `link`'s lineage, equal for links to the same source. Case is ignored, as
    `task`'s link filters ignore it.
    """
    return source(link).casefold()
