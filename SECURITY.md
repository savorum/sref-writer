# Security policy

## Supported versions

The default branch is supported. Other versions do not receive security
maintenance.

## Scope

This library writes SREF recipe JSON, packages, and bundles from documents and
asset bytes a program gives it. Those inputs can come from users, so a report is
in scope when a crafted input makes the writer do any of the following:

- emit an archive that names a path outside the archive, collides with another
  entry after Unicode normalization or case folding, or uses a name the
  specification prohibits;
- emit an artifact that its own reader or the specification's schemas reject,
  where the writer promises to refuse the input;
- raise anything other than `WriteRefusedError` for input it should refuse;
- record a digest, size, or media type that disagrees with the bytes it wrote; or
- write files, open connections, or run code beyond returning the bytes.

The writer checks identities, references, asset paths, formats, and the published
schemas before it writes. It does not scan asset bytes for malware or check that
they are the media type they claim, and it does not decide what an application
should trust.

## Reporting

Use the hosting platform's private vulnerability-reporting feature when it is
available. Otherwise contact a maintainer privately through the hosting platform
and ask for a secure reporting channel. Do not include exploit details in a
public issue.

A report should include the input or a way to build it, the version, and the
effect. A fix adds a regression test that reproduces the issue without
unnecessary risk.
