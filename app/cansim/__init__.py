"""星载 CAN 总线逐位回放仿真器。"""

from .crc import CRC15_POLY, crc15
from .engine import BusEngine
from .frame import STANDARD_FRAME_FIELDS, CanFrame, FrameRequest
from .validate import (
    MAX_NODES,
    MAX_REQUESTS,
    ValidationError,
    validate_scenario,
)

__all__ = [
    "CRC15_POLY",
    "crc15",
    "STANDARD_FRAME_FIELDS",
    "CanFrame",
    "FrameRequest",
    "BusEngine",
    "MAX_NODES",
    "MAX_REQUESTS",
    "ValidationError",
    "validate_scenario",
]
