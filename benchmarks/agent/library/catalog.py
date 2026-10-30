def find_book(catalog, title):
    for entry in catalog:
        if entry["title"] == title:
            return entry
    return None


def add_book(catalog, entry):
    catalog.append(entry)
