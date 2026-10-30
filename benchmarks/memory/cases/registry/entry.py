from registry import REGISTRY


def entry(kind):
    return REGISTRY[kind](1)
