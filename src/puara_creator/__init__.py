# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""puara-creator — a corpus-driven workbench for designing gesture descriptors.

Design-time counterpart to puara-gestures. Records OSC sensor streams, replays them
deterministically, and scores candidate descriptors against the recording.

See docs/ARCHITECTURE.md for the system architecture and docs/SPEC_V1.md for the
normative specification of this release.
"""

__version__ = "0.1.0.dev0"

#: Corpus format version written into meta.json. See docs/FORMAT.md.
SCHEMA_VERSION = 1

__all__ = ["SCHEMA_VERSION", "__version__"]
