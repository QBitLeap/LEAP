"""Admission guard for the existing schema-4 index layout.

Legacy column names and empty database slots remain for storage compatibility.
No legacy protocol implementation or activation profile is included.
"""

from quantums.networks import NETWORKS


def resolve_index_profile(network: str | None, requested: str | None = None) -> str:
    if network is not None and network not in NETWORKS:
        raise ValueError(f"unsupported Qbit network: {network}")
    if requested not in (None, "auto", "off"):
        raise ValueError("only the disabled legacy index profile is supported")
    return "off"
