from catalog import find_book


def checkout_book(catalog, title, member):
    entry = find_book(catalog, title)
    if entry is None:
        return None
    entry["holder"] = member
    return entry


def return_book(catalog, title):
    entry = find_book(catalog, title)
    if entry is None:
        return False
    entry["holder"] = None
    return True


def overdue_fine(days_late):
    return days_late * 2
