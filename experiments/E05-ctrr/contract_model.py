"""Bounded contract model for the original AVP CTRR register block.

This module is a host-side, self-authored contract oracle.  It does not map
guest memory, alter QEMU permissions, execute Apple code, or claim that a
protection request succeeded on a real VM.  The recorded protection calls are
only the normal inputs that a later QEMU device implementation must apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BASE = 0x30260000
WINDOW_BYTES = 0x10000
RANGE_OFFSET = 0x40
RANGE_STRIDE = 0x20
RANGE_COUNT = 16
LOCK = 1 << 0
ENABLE = 1 << 1
READ_ONLY = 1 << 2
SUPPORTED_CONTROL = LOCK | ENABLE | READ_ONLY


class CtrrError(ValueError):
    """A normal rejected register operation."""


@dataclass
class ProtectionCall:
    index: int
    start: int
    length: int
    flags: int
    reason: str


@dataclass
class Range:
    lower: int = 0
    upper: int = 0
    control: int = 0

    @property
    def enabled(self) -> bool:
        return bool(self.control & ENABLE)

    @property
    def locked(self) -> bool:
        return bool(self.control & LOCK)


@dataclass
class CtrrModel:
    """The statically established unconfigured CTRR state and transitions."""

    ram_ranges: tuple[tuple[int, int], ...]
    page_size: int
    ranges: list[Range] = field(default_factory=lambda: [Range() for _ in range(RANGE_COUNT)])
    global_state: int = 0
    calls: list[ProtectionCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.ram_ranges:
            raise CtrrError("CTRR requires at least one RAM interval")
        if self.page_size <= 0 or self.page_size & (self.page_size - 1):
            raise CtrrError("page size must be a positive power of two")
        if len(self.ranges) != RANGE_COUNT:
            raise CtrrError("CTRR has exactly sixteen ranges")

    @property
    def page_shift(self) -> int:
        return self.page_size.bit_length() - 1

    @property
    def ram_lower(self) -> int:
        return self.ram_ranges[0][0]

    @property
    def ram_upper_last_page(self) -> int:
        return self.ram_ranges[-1][1] - self.page_size

    def read_global(self, offset: int) -> int:
        if offset == 0x00:
            return 0x63747272  # 'ctrr'
        if offset == 0x04:
            return 1
        if offset == 0x08:
            return self.global_state
        if offset == 0x0C:
            return RANGE_COUNT
        if offset == 0x10:
            return self.page_shift
        if offset == 0x20:
            return self.ram_lower
        if offset == 0x28:
            return self.ram_upper_last_page
        return 0

    def read_range(self, index: int, field: str) -> int:
        r = self._range(index)
        if field == "lower":
            return r.lower
        if field == "upper":
            return r.upper
        if field == "control":
            return r.control
        raise CtrrError(f"unknown range field: {field}")

    def write_range(self, index: int, field: str, value: int) -> None:
        r = self._range(index)
        if type(value) is not int or value < 0 or value >= 1 << 64:
            raise CtrrError("range value must be a u64")
        if field in ("lower", "upper"):
            if r.locked or r.enabled:
                return
            setattr(r, field, value)
            return
        if field == "control":
            self._write_control(index, value)
            return
        raise CtrrError(f"unknown range field: {field}")

    def _write_control(self, index: int, value: int) -> None:
        r = self._range(index)
        if value & ~SUPPORTED_CONTROL:
            return
        if r.locked:
            return
        if r.enabled and (value & ENABLE):
            # The original path keeps the active protection and only allows
            # the lock bit to be added.  READ_ONLY cannot be changed while
            # the range remains enabled.
            r.control = (r.control & READ_ONLY) | (value & LOCK) | ENABLE
            return
        if not (value & ENABLE):
            if r.enabled:
                self._protect(index, r.lower, r.upper, 7, "disable")
            r.control = value & LOCK
            return
        self._validate_enabled_range(index, r.lower, r.upper)
        flags = 5 if (value & READ_ONLY) else 7
        self._protect(index, r.lower, r.upper, flags, "enable")
        r.control = value

    def teardown(self) -> None:
        for index, r in enumerate(self.ranges):
            if r.enabled:
                self._protect(index, r.lower, r.upper, 7, "teardown")
                r.control &= ~ENABLE
        self.ranges = [Range() for _ in range(RANGE_COUNT)]

    def _range(self, index: int) -> Range:
        if type(index) is not int or not 0 <= index < RANGE_COUNT:
            raise CtrrError("range index must be in 0..15")
        return self.ranges[index]

    def _validate_enabled_range(self, index: int, lower: int, upper: int) -> None:
        if lower % self.page_size or upper % self.page_size:
            raise CtrrError("enabled range is not page aligned")
        if upper < lower:
            raise CtrrError("enabled range upper is below lower")
        if not any(start <= lower and upper < end for start, end in self.ram_ranges):
            raise CtrrError("enabled range is outside a complete RAM interval")
        for other_index, other in enumerate(self.ranges):
            if other_index == index or not other.enabled:
                continue
            if not (upper < other.lower or lower > other.upper):
                raise CtrrError("enabled range overlaps another enabled range")

    def _protect(self, index: int, lower: int, upper: int, flags: int, reason: str) -> None:
        self.calls.append(ProtectionCall(index, lower,
                                         upper - lower + self.page_size,
                                         flags, reason))

