# Contributing to Kingfisher

Bug reports, feature ideas and pull requests are welcome. For larger changes, please open an issue first so the approach can be discussed.

## Development setup

On Ubuntu 22.04 or newer:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil \
  tesseract-ocr tesseract-ocr-eng gnome-screenshot python3-pyzbar gir1.2-dbusmenu-glib-0.4
./bin/kingfisher            # run from the source tree
```

## Checks

```bash
python3 -s -m unittest discover -s tests -t .
pipx run ruff==0.16.6 check kingfisher bin/kingfisher tests
shellcheck install.sh uninstall.sh packaging/build-deb.sh
```

The `-s` flag ignores packages in `~/.local`, so the tests use the distribution's libraries, as the app does. The tests need no display and no network. Please add or update tests when you change behavior.

## Guidelines

- Keep Kingfisher offline: no network calls, no telemetry.
- Don't write captured images to disk, apart from the short-lived capture file, which must be deleted.
- Follow the style of the surrounding code. GTK 4.6 and libadwaita 1.1 are the oldest supported versions, so newer APIs need a fallback.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages, e.g. `fix(overlay): keep selection inside the monitor`.

## Packaging

```bash
packaging/build-deb.sh        # writes dist/kingfisher_<version>_all.deb
```

## Releasing

1. Bump `VERSION` in `kingfisher/__init__.py` (the single source of truth).
2. Add a `## <version> - YYYY-MM-DD` section to `CHANGELOG.md`. The package and the AppStream release take their date from that heading.
3. Commit, tag `v<version>` and push the tag. CI tests, builds and validates the package.
4. Publish a GitHub release for the tag; CI attaches the `.deb` to it.

The application ID in `kingfisher/__init__.py` must never change after a release: settings paths, the autostart entry, the desktop file and the AppStream ID all derive from it.

## License

Kingfisher is licensed under the GNU General Public License, version 3 or later. By contributing, you agree that your contributions are licensed under the same terms.
