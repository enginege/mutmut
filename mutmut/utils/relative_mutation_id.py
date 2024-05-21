from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class RelativeMutationID:
    line: str
    index: int
    line_number: int
    filename: Optional[str] = field(default=None, compare=False, hash=False)

ALL = RelativeMutationID(filename='%all%', line='%all%', index=-1, line_number=-1)