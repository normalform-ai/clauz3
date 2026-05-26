from collections.abc import Callable

import pytest

from clauz3._vendor.deal_solver._context import Context


@pytest.fixture
def ctx_factory() -> Callable[[], Context]:
    def make() -> Context:
        return Context.make_empty(get_contracts=lambda _: iter(()))

    return make
