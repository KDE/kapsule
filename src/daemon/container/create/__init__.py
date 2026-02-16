# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Container creation pipeline: pre-creation config, API call, and post-creation setup.

Importing this package registers all steps with the pipeline.
"""

from ...pipeline import Pipeline
from ..contexts import CreateContext

create_pipeline = Pipeline[CreateContext]("create")

# Import step modules so their decorators register with the pipeline.
# Pre-creation steps (build config, parse image, create instance)
from . import build_config as _  # noqa: F401, E402
from . import create_instance as _  # noqa: F401, E402

# Post-creation steps (configure the running container)
from . import file_capabilities as _  # noqa: F401, E402
from . import host_network as _  # noqa: F401, E402
from . import session_mode as _  # noqa: F401, E402
