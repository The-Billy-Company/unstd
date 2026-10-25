"""Five Hypothesis profiles, applied once at collection."""

import os

from hypothesis import Phase, settings


def load_active_profile() -> str:
    """Register shared profiles and select the environment's generation depth."""
    common = {"deadline": None, "print_blob": True}
    settings.register_profile("dev", max_examples=25, **common)
    settings.register_profile("default", max_examples=100, **common)
    settings.register_profile("ci", max_examples=300, derandomize=True, **common)
    settings.register_profile("campaign", max_examples=2500, **common)
    settings.register_profile(
        "replay", max_examples=1, phases=(Phase.explicit, Phase.reuse), **common
    )
    default = "ci" if os.environ.get("CI") else "dev"
    name = os.environ.get("HYPOTHESIS_PROFILE", default)
    settings.load_profile(name)
    return name
