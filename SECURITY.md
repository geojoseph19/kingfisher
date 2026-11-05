# Security policy

## Supported versions

Security fixes are made for the latest release.

## Reporting a vulnerability

Please report security issues privately with the **Report a vulnerability** button on the repository's **Security** tab, not in a public issue. You will receive a reply as soon as possible, and a fix will be released before any details are published.

## Design notes

- Kingfisher makes no network connections.
- Captures are read into memory. The temporary capture file is kept in a folder only you can read and is deleted immediately.
- Captured text never appears in notifications or logs.
- Software is installed only when you click **Install**, through PackageKit, with trusted packages only and the system password prompt.
- Images are passed to Tesseract through a pipe and are never written to disk.
- The history, when enabled, stores text only, in a file readable only by you.
- Links, email addresses and phone numbers open only when you click them.
