"""Workarounds for this PC's Windows setup.

Smart App Control blocks some unsigned DLLs. openWakeWord's package __init__
imports its *training* helper, which pulls in scikit-learn, whose
`_dist_metrics` DLL is blocked. Jarvis never trains models, so we register a
stub for that one submodule before openwakeword is imported.
"""
from __future__ import annotations

import sys
import types


def import_openwakeword():
    name = "openwakeword.custom_verifier_model"
    if name not in sys.modules:
        stub = types.ModuleType(name)

        def train_custom_verifier(*_a, **_k):  # pragma: no cover - never used
            raise RuntimeError("Custom verifier training is disabled in Jarvis")

        stub.train_custom_verifier = train_custom_verifier
        sys.modules[name] = stub
    import openwakeword  # noqa: E402
    from openwakeword.model import Model  # noqa: E402

    return openwakeword, Model
