from pkg.consumer import use


def test_use():
    assert use(2) == 1
