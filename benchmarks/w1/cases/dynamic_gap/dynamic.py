def target(value):
    return value + 1


def indirect():
    return globals()["target"](2)


def entry():
    return indirect()
