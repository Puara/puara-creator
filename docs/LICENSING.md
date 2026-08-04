# Licensing

## The tool

`puara-creator` is licensed under the **GNU Affero General Public License, version 3.0 or later**
(AGPL-3.0-or-later). The full text is in [`LICENSE`](../LICENSE).

The AGPL was chosen deliberately over the MIT licence used by `puara-gestures`. The relevant
difference is §13 of the AGPL: a party who modifies `puara-creator` and offers it to others over a
network — as a hosted corpus service, an annotation platform, or a descriptor-design service — must
make their modified source available to those users. For a research tool whose value grows with
shared methodology, that obligation is the point.

## What the tool generates is not covered

**Descriptor code, corpora, metric reports, and any other output produced by running
`puara-creator` are not covered by the AGPL.** Their copyright belongs to whoever produced them, and
they may be licensed on any terms, including proprietary ones.

This is the same position taken by GNU Bison and by GCC's runtime library exception, and for the
same reason: a tool that infected the licence of its output would be unusable for its intended
purpose. The intended purpose here is generating descriptors for `puara-gestures`, which is MIT
licensed, and for instruments built by people who have no obligation to open their firmware.

The following exception is therefore attached to the licence, confirmed by the copyright holders on
4 August 2026:

> **Output exception.** As a special exception, the copyright holders of `puara-creator` give
> permission to use the output of running this program — including generated descriptor source
> code, recorded corpora, and evaluation reports — without restriction, and to license that output
> on any terms, notwithstanding the terms of the GNU Affero General Public License that govern this
> program itself. This exception does not apply to code from `puara-creator` that is copied into
> the output beyond what is necessary to make the output function.

The trailing sentence is the standard qualification: templates and boilerplate emitted by the code
generator carry the exception; wholesale copying of the tool's own source into a project does not.

**Status:** confirmed. The exception is in force and is reproduced at the head of
[`LICENSE`](../LICENSE).

## Copyright

```
Copyright (C) 2026 Société des Arts Technologiques (SAT)
Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
Copyright (C) 2026 Eduardo Meneses
```

The attribution follows the convention already used across `puara-gestures`.

## Third-party dependencies

All runtime dependencies are permissively licensed and compatible with distribution under the AGPL:

| Dependency | Licence |
| --- | --- |
| `python-osc` | Unlicense |
| `typer` | MIT |
| `rich` | MIT |
| `numpy` | BSD-3-Clause |
| `orjson` | Apache-2.0 / MIT |
| `fastapi` | MIT |
| `uvicorn` | BSD-3-Clause |
| `pyarrow` (optional) | Apache-2.0 |
| `matplotlib` (optional) | PSF-based, BSD-compatible |

No dependency is added under a copyleft licence without a note in this file explaining why.

## Corpora

Recorded corpora are not code and are not licensed by this file. They contain movement data from
identifiable people and are governed by the consent under which they were collected; see
[`PROTOCOL.md`](PROTOCOL.md) §6. `corpus/` is excluded from version control by default, and
publishing a corpus is a deliberate act requiring that the consent covers it.

Where a corpus is published, the project's recommendation is CC BY 4.0 for the data with a
documented consent basis, but the decision belongs to the subjects and to the ethics approval, not
to this repository.
