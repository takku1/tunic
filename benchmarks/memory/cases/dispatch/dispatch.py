from core import target


def fast(value):
    return target(value)


def slow(value):
    return target(value + 1)


HANDLERS = {"fast": fast, "slow": slow}
