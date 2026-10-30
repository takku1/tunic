from core import target


def shadow(target):
    return target(1)


def actual():
    return target(2)
