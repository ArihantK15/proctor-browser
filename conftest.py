"""Repo-root conftest — loads for every pytest invocation (both tests/ and
integration_tests/) ahead of their package-level conftests.

Sole job: make numpy's RNG seeding crash-proof under pytest-randomly.

pytest-randomly reseeds every plugin registered under the
'pytest_randomly.random_seeder' entry-point group once per test, passing
`base_seed + crc32(test_nodeid)` — a value that routinely exceeds 2**32.
`thinc` (pulled in transitively via the ML stack) registers
`thinc.api.fix_random_seed`, which forwards that seed straight to
`numpy.random.seed()`. numpy rejects seeds >= 2**32 with
"ValueError: Seed must be between 0 and 2**32 - 1", so the suite errors
*intermittently* — depending on the run's random base seed and which test
nodeids' crc32 happen to push the sum over the limit.

Note pytest-randomly already mods its *own* numpy seed into range (so the
bug is not in pytest-randomly); the offending caller is the unclamped thinc
reseeder. Clamping numpy.random.seed here makes any such caller safe while
leaving behaviour for in-range seeds unchanged.
"""
try:
    import numpy as _np

    _orig_numpy_seed = _np.random.seed

    def _clamped_numpy_seed(seed=None, *args, **kwargs):
        if isinstance(seed, int):
            seed %= 2 ** 32
        return _orig_numpy_seed(seed, *args, **kwargs)

    _np.random.seed = _clamped_numpy_seed
except Exception:
    # numpy not installed (or seed already wrapped) — nothing to guard.
    pass
