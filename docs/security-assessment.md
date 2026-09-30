# Security assessment

This assessment covers the `sref_writer` library. It turns documents and asset
bytes supplied by a program into SREF artifacts and returns bytes. Those inputs
can come from users, so its exposure is what a crafted input can make it emit or
raise. [`SECURITY.md`](../SECURITY.md) defines what counts as a vulnerability and
how to report one.

## Method

Each way a crafted input could yield a wrong artifact or an unexpected exception
was matched to a control in the code and to a test that fails without it.

## Findings

| Risk                                                        | Control                                                                                                 | Evidence                                                                                                                                 |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| An exception other than `WriteRefusedError` escapes         | Input of the wrong type is refused before it reaches code that assumes its type.                        | [`tests/test_fuzz.py`](../tests/test_fuzz.py), [`fuzz/fuzz_writer.py`](../fuzz/fuzz_writer.py), and the `MalformedInput` and `MistypedInput` cases |
| An artifact the specification forbids or its reader rejects | The document is normalized, checked for identities and references, then checked against the schemas.   | The `FormatsTheReaderChecks` cases and the SREF corpus, run by `tools/conformance.py` with the reader                                    |
| Archive names that traverse or collide                      | Asset paths are checked after NFC normalization and case folding.                                       | [`validate.py`](../sref_writer/validate.py) and the `AssetPaths` cases                                                                    |
| A manifest that disagrees with the bytes written            | Digests and sizes are computed from the bytes written and replace any the caller declared.              | [`package.py`](../sref_writer/package.py) and the `Packages` cases                                                                        |
| Code execution, network access, or writing files            | The library returns bytes. It opens no sockets, files, or subprocesses and evaluates no code.           | A search of `sref_writer` for those calls finds none                                                                                     |
| A malicious dependency                                      | One runtime dependency, `jsonschema`. CI installs by hash, and Dependabot proposes updates.             | `requirements-ci.txt`, `.github/dependabot.yml`                                                                                          |

## Continuous checks

CodeQL analyzes the code, Hypothesis property tests run with the unit tests, and
a coverage-guided fuzz target runs on pull requests and weekly.

## Residual risk

The library does not scan asset bytes for malware or check that they are the
media type they claim, and it does not decide what an application should trust.
