def calculate_total(items):
    return sum(items)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def update(record):
    record["updated"] = True
    return record
