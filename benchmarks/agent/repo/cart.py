def add_item(cart, item):
    cart.append(item)


def total(cart):
    return sum(line["price"] for line in cart)
