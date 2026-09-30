"""Coverage-guided fuzz target for the writers.

The first byte chooses a recipe, a package, or a bundle. The rest is parsed as a
JSON document. The writer promises to raise nothing but `WriteRefusedError`, and
to write only well-formed archives, so any other exception is a finding.
"""

import contextlib
import io
import json
import sys
import zipfile

import atheris

with atheris.instrument_imports():
    import sref_writer

ASSETS = {"photo": b"asset bytes"}


def test_one_input(data: bytes) -> None:
    if not data:
        return
    try:
        document = json.loads(data[1:])
    except ValueError:
        return

    kind = data[0] % 3
    with contextlib.suppress(sref_writer.WriteRefusedError):
        if kind == 0:
            sref_writer.write_recipe(document)
            return
        archive = (
            sref_writer.write_package(document, ASSETS)
            if kind == 1
            else sref_writer.write_bundle([(document, ASSETS)])
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            if opened.testzip() is not None:
                message = "the writer produced a corrupt archive"
                raise AssertionError(message)


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
