from core import target


class Worker:
    def run(self):
        return target(2)


def entry():
    return Worker().run()
