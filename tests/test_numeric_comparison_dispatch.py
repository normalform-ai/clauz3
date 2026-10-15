"""Unknown numeric comparison dispatch must refuse without a raw traceback."""

import operator
from collections.abc import Callable

import pytest
import z3

from clauz3._vendor.deal_solver._context import Context
from clauz3._vendor.deal_solver._exceptions import UnsupportedError
from clauz3._vendor.deal_solver._proxies import types
from clauz3._vendor.deal_solver._proxies._float import _compare_fp_to_real


@pytest.mark.parametrize("reverse", [False, True], ids=["float-real", "int-float"])
def test_noncomparison_handler_is_a_clean_refusal(
    ctx_factory: Callable[[], Context], reverse: bool
) -> None:
    ctx = ctx_factory()
    with pytest.raises(
        UnsupportedError, match="unsupported floating-point comparison handler"
    ):
        if reverse:
            types.int.val(1, ctx=ctx)._comp_op(
                types.float.val(1.0, ctx=ctx), operator.add, ctx
            )
        else:
            _compare_fp_to_real(
                z3.FPVal(1.0, z3.Float64(ctx=ctx.z3_ctx)),
                z3.RealVal(1, ctx=ctx.z3_ctx),
                operator.xor,
            )
