# Langflow custom components exposing Salt (saltapp.ai) agent actions.
#
# This folder is also the Langflow "category" folder when loaded via
# LANGFLOW_COMPONENTS_PATH -- see README.md for how to point a real
# Langflow install at it. Keeping this __init__.py present and this
# folder flat (no subfolders) matches the loader's documented depth-2
# convention: <LANGFLOW_COMPONENTS_PATH>/<this folder>/<component file>.py.
"""Salt (saltapp.ai) custom components for Langflow."""

__all__ = [
    "SaltSendMessageComponent",
    "SaltAskHumanComponent",
    "SaltRequestPaymentComponent",
    "SaltReadUpdatesComponent",
    "SaltReadRoomComponent",
    "SaltInterestsComponent",
]

from .ask_human import SaltAskHumanComponent
from .interests import SaltInterestsComponent
from .read_room import SaltReadRoomComponent
from .read_updates import SaltReadUpdatesComponent
from .request_payment import SaltRequestPaymentComponent
from .send_message import SaltSendMessageComponent
