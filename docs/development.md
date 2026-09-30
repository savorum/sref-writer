# Development

Run commands from the repository root with Python 3.10 or later.

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install '.[dev]' build
python3 -m ruff check .
python3 -m ruff format --check .
python3 -m unittest discover -s tests -v
```

## Conformance

The library claims these capabilities for SREF 0.4.0 with unit registry 0.2.0:

| Capability | Status |
| --- | --- |
| JSON writer | claimed |
| Package writer | claimed |
| Bundle writer | claimed |
| JSON, package, and bundle reader | not implemented (see SREF Reader) |
| Round trip | claimed only together with SREF Reader |

The conformance run needs the SREF repository for the fixture corpus and an
SREF Reader checkout to read back what this library wrote:

```sh
python3 tools/conformance.py --sref ../sref --reader ../sref-reader
```

Report both commits with the result. CI pins them in its `SREF_COMMIT` and
`SREF_READER_COMMIT` variables; a run against other checkouts is evidence for
those revisions only. `SREF_COMMIT=<pinned commit> tools/drift.sh ../sref`
reports the SREF commits made since the pin.

### Independent validation

A reader and a writer can share the same mistake and still agree with each
other. [`sref_writer/independent.py`](../sref_writer/independent.py) therefore
checks each written artifact from its bytes, against the published schemas and
the specification's archive rules, without using either library's model. A
conformance case that the reader accepts but these checks reject fails as
`schema-shortfall`.

The independence is in the code, not in the authorship. The reader, the writer,
and this oracle have one author and one reading of the specification, so a point
the specification leaves open is settled the same way in each. The oracle checks
structure, digests, and archive rules; it does not interpret quantities or
units. A second implementation written by someone else is what finds an
ambiguity in the specification.

It can also be run directly:

```sh
python3 -m sref_writer.independent shortbread.sref
```

It ships with the library because the conformance harness uses it. The write
path never calls it.

## Installed distribution

`tools/installed.py` checks an installed copy of the library; it does not build
or install it. Build a wheel and install it into a clean environment:

```sh
python3 -m build --outdir dist .
python3 -m venv .installed
.installed/bin/python -m pip install dist/*.whl
```

Then run the check from an empty directory, so the checkout is not on the import
path:

```sh
mkdir -p /tmp/sref-installed && cd /tmp/sref-installed
/path/to/sref-writer/.installed/bin/python /path/to/sref-writer/tools/installed.py \
  --sref /path/to/sref \
  --reader /path/to/sref-reader
```

It loads every vendored snapshot file, writes one valid case per claimed
capability, and reads the output back with the reader. It is a packaging smoke
test, not a substitute for the conformance run.
