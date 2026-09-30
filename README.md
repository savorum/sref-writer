# SREF Writer

A Python library that writes SREF (Structured Recipe Exchange Format) recipe JSON,
`.sref` packages, and `.srefbundle` bundles. It refuses input that the published
schemas, the specification's identity and reference rules, and the field formats
listed in [Refusals](docs/usage.md#refusals) show to be invalid. It does not read
recipes or parse ingredient text; SREF Reader is the separate library for
reading.

## Install

Python 3.10 or later. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
```

The only runtime dependency is `jsonschema`. The SREF schemas and unit registry
ship inside the distribution, so no specification checkout is needed at
runtime.

## Example

```python
from pathlib import Path
import sref_writer

document = {
    "id": "tea",
    "title": "Tea",
    "instruction_sections": [
        {
            "id": "method",
            "steps": [{"id": "steep", "text": "Steep the tea, then strain."}],
        }
    ],
}
Path("tea.sref").write_bytes(sref_writer.write_package(document, {}))
```

## Documentation

- [Usage](docs/usage.md): writing, diagnostics, normalization, and what the
  writer refuses.
- [Development](docs/development.md): checks, conformance, and testing the
  installed distribution.

## Compatibility

`__version__` and `pyproject.toml` version this library.
`SUPPORTED_FORMAT` (`0.4.0`) and `SUPPORTED_REGISTRY` (`0.2.0`) name the SREF
and unit-registry versions it writes. The three numbers are independent.

The vendored SREF snapshot is loaded through `importlib.resources` and checked
against its manifest digests; altered bytes fail to load. Output is checked
against the schema and against the semantic and archive rules the schema cannot
express. A document declaring a compatible newer SREF version keeps its
unrecognized members and extensions unchanged.

## Contributing

Every commit carries a Developer Certificate of Origin sign-off, created with
`git commit -s`:

```text
Signed-off-by: Your Name <your-email@example.com>
```

The sign-off certifies the statement at <https://developercertificate.org/>, and
CI rejects a commit whose sign-off does not name its author.

## Licence

MIT, in [LICENSE.txt](LICENSE.txt). The vendored schemas and registry data keep
SREF's CC0 1.0 Universal dedication.
