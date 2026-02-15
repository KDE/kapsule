# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup pipeline: steps run to configure a host user in a container.

Importing this package registers all steps with the pipeline.
"""

from ...pipeline import Pipeline
from ..contexts import UserSetupContext

user_setup_pipeline = Pipeline[UserSetupContext]("user_setup")

# Import step modules so their decorators register with the pipeline.
from . import mount_home as _  # noqa: F401, E402
from . import mount_custom as _  # noqa: F401, E402
from . import mount_host_dirs as _  # noqa: F401, E402
from . import create_account as _  # noqa: F401, E402
from . import configure_sudo as _  # noqa: F401, E402
from . import enable_linger as _  # noqa: F401, E402
from . import mark_mapped as _  # noqa: F401, E402
