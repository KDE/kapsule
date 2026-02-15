# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Post-creation pipeline: steps run after a container is created.

Importing this package registers all steps with the pipeline.
"""

from ...pipeline import Pipeline
from ..contexts import PostCreateContext

post_create_pipeline = Pipeline[PostCreateContext]("post_create")

# Import step modules so their decorators register with the pipeline.
from . import host_network as _  # noqa: F401, E402
from . import file_capabilities as _  # noqa: F401, E402
from . import session_mode as _  # noqa: F401, E402
