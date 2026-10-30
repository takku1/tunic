def target(value):
    return relay(value - 1) if value > 0 else 0


def relay(value):
    return target(value)


def entry():
    return relay(2)
