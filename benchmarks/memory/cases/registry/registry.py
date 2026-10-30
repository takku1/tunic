from core import target


def handler(value):
    return target(value)


REGISTRY = {"handle": handler}
