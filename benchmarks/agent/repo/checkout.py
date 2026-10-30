from cart import add_item, total
from pricing import discount


def place_order(cart):
    return discount(total(cart))
