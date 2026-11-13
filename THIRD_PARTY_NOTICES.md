# Third-party notices

Resource Planner is licensed under the MIT License ([`LICENSE`](LICENSE)). It uses the components
below, each under its own license. Versions are those of `uv.lock` and
`src/ui/package-lock.json` at the time of writing; the lock files are authoritative.

## Python packages in the images

Installed in the `api` and `ui` images (direct dependencies and what they pull in). The license
is the one the package declares.

| Package | License |
|---|---|
| alembic, Mako | MIT |
| annotated-doc, annotated-types, anyio, APScheduler, fastapi, h11, httptools, pydantic, pydantic-core, pydantic-settings, PyJWT, PyYAML, SQLAlchemy, tzlocal, typing-inspection, watchfiles | MIT |
| argon2-cffi, argon2-cffi-bindings | MIT |
| cffi | MIT-0 |
| greenlet | MIT and PSF-2.0 |
| Babel, click, httpcore, httpx, idna, Jinja2, MarkupSafe, pycparser, python-dotenv, starlette, uvicorn, websockets | BSD-3-Clause |
| cryptography | Apache-2.0 or BSD-3-Clause |
| opentelemetry-api, python-multipart, tzdata | Apache-2.0 |
| typing-extensions | PSF-2.0 |
| uvloop (Linux images) | MIT or Apache-2.0 |
| certifi | MPL-2.0 (the source of certifi is at https://github.com/certifi/python-certifi) |
| psycopg, psycopg-binary | LGPL-3.0-only |

**psycopg** is used as an unmodified library and is imported, not copied, into the planner. Under
the LGPL you may replace it with another version of psycopg: the image installs it from PyPI, so
rebuilding the image with another version of the package is enough. Its source is at
https://github.com/psycopg/psycopg and its license text at
https://www.gnu.org/licenses/lgpl-3.0.html.

## Files served by the web interface

These are copied into `src/ui/planner_ui/static` by `npm run build` (script `src/ui/scripts/vendor.mjs`)
and are not stored in the repository; the license file of each is copied next to it.

| Component | Version | License | Copyright |
|---|---|---|---|
| [htmx](https://htmx.org) | 2.0.4 | Zero-Clause BSD | – |
| [Alpine.js](https://alpinejs.dev) (CSP build) | 3.14.9 | MIT | Caleb Porzio and contributors |
| [Sortable](https://github.com/SortableJS/Sortable) | 1.15.6 | MIT | 2019 All contributors to Sortable |
| [IBM Plex Sans](https://github.com/IBM/plex) (via Fontsource) | 5.2.5 | SIL OFL 1.1 | 2017 IBM Corp. with Reserved Font Name "Plex" |
| [Manrope](https://github.com/sharanda/manrope) (via Fontsource) | 5.2.5 | SIL OFL 1.1 | 2018 The Manrope Project Authors |
| [Tabler Icons](https://tabler.io/icons) (a subset, in `src/ui/planner_ui/icons`) | 3.31.0 | MIT | 2020-2024 Paweł Kuna |

The style sheet `app.css` is generated at build time by
[Tailwind CSS](https://tailwindcss.com) 3.4.17 (MIT, Copyright Tailwind Labs, Inc.) with the
[daisyUI](https://daisyui.com) 4.12.23 plugin (MIT, Copyright Pouya Saadeghi). Parts of both end up
in the generated file. Neither is a runtime dependency.

### MIT License (Alpine.js, Sortable, Tabler Icons, Tailwind CSS, daisyUI)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### Zero-Clause BSD (htmx)

Permission to use, copy, modify, and/or distribute this software for any purpose with or without
fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

### SIL Open Font License 1.1 (IBM Plex Sans, Manrope)

The fonts are included unmodified as WOFF2 files, with this license and their copyright notices:

- IBM Plex Sans: Copyright © 2017 IBM Corp. with Reserved Font Name "Plex".
- Manrope: Copyright 2018 The Manrope Project Authors (https://github.com/sharanda/manrope).

```
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

## Separate programs the Compose files start

These run in their own containers and are not part of this repository's distribution; their
licenses are those of their projects: PostgreSQL (PostgreSQL License), nginx (BSD-2-Clause) and
Keycloak (Apache-2.0), the last two only in `docker/docker-compose.dev.yml`, and the Python base image used by the Dockerfiles
(Python Software Foundation License; Debian packages under their own licenses).

## Tools used to build and test

Not shipped in the images: uv, Ruff, mypy, pytest, Playwright, Testcontainers, import-linter,
datamodel-code-generator, Node.js and npm. They have their own licenses (mostly MIT and
Apache-2.0).
