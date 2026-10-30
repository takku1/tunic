from dispatch import HANDLERS


def entry(which):
    return HANDLERS[which](0)
