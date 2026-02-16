# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generic ordered pipeline for async step functions.

Create a :class:`Pipeline` instance at module level and use its
:meth:`~Pipeline.step` method as a decorator.  When the steps are
split across files, each file just imports the pipeline instance and
decorates its functions — no central list to maintain.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar, overload

_Ctx = TypeVar("_Ctx")

_StepFn = Callable[[_Ctx], Awaitable[None]]

# Default order for steps that don't specify one.
_DEFAULT_ORDER = 500


class Pipeline(Generic[_Ctx]):
    """A registry of async step functions executed by ``order``.

    Create an instance at module level and use its :meth:`step` method
    as a decorator.  When the steps are split across files, each file
    just imports the pipeline instance and decorates its functions — no
    central list to maintain.

    Ordering
    --------
    Every step has a numeric *order* (default 500).  Steps run in
    ascending order; steps with equal order run in registration
    (decoration) order.  This keeps sequencing explicit even when
    steps live in different modules with unpredictable import order.

    Convention: use multiples of 100 so there's room to insert
    steps between existing ones.

    Example::

        create = Pipeline[CreateContext]("create")

        @create.step
        async def fix_caps(ctx: CreateContext) -> None: ...

        @create.step(order=900)
        async def finalize(ctx: CreateContext) -> None: ...
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._entries: list[tuple[int, int, _StepFn[_Ctx]]] = []
        self._seq = 0  # registration counter for stable sort

    @overload
    def step(self, fn: _StepFn[_Ctx]) -> _StepFn[_Ctx]: ...
    @overload
    def step(self, *, order: int) -> Callable[[_StepFn[_Ctx]], _StepFn[_Ctx]]: ...

    def step(
        self,
        fn: _StepFn[_Ctx] | None = None,
        *,
        order: int = _DEFAULT_ORDER,
    ) -> _StepFn[_Ctx] | Callable[[_StepFn[_Ctx]], _StepFn[_Ctx]]:
        """Register *fn* as a step in this pipeline.

        Can be used bare (``@pipeline.step``) or with arguments
        (``@pipeline.step(order=200)``).
        """
        def _register(f: _StepFn[_Ctx]) -> _StepFn[_Ctx]:
            self._entries.append((order, self._seq, f))
            self._seq += 1
            return f

        if fn is not None:
            # Called as @pipeline.step (no parentheses)
            return _register(fn)
        # Called as @pipeline.step(order=...)
        return _register

    async def run(self, ctx: _Ctx) -> None:
        """Execute every registered step in order."""
        for _ord, _seq, s in sorted(self._entries):
            await s(ctx)

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        ordered = sorted(self._entries)
        names = ", ".join(f"{f.__name__}({o})" for o, _s, f in ordered)
        return f"Pipeline({self.name!r}, [{names}])"
