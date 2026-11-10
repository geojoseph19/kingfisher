# Changelog

## 1.0.1 - 2026-11-10

First release on the Snap Store.

### Snap Store version

- GNOME asks for screen access once, not on every capture: through the screenshot
  permission on Ubuntu 24.04 and later, and a remembered screen-sharing selection on
  Ubuntu 22.04. Xorg sessions need no permission
- **Preferences › Screen Access** shows the permission and can forget a remembered selection
- English and 20 other recognition languages are included
- Start at login works through snapd
- The top-bar icon menu works inside the sandbox
- QR codes and barcodes are read inside the sandbox
- High-accuracy models live in `~/snap/kingfisher/common/tessdata` and survive updates
- The keyboard shortcut is added by hand in GNOME Settings, with the command
  `kingfisher --capture`; Preferences explains how
- Images on USB drives and mounted disks can be opened after
  `sudo snap connect kingfisher:removable-media`

### Fixed

- Opening an image from the file manager or the command line failed with some
  PyGObject versions
- File paths in the interface no longer show the full home folder inside the snap
- A screenshot taken through the desktop portal on Ubuntu 22.04 was left behind in
  Pictures inside the snap; it is now removed

## 1.0.0 - 2026-11-07

First public release.

### Capture

- Select any area of the screen, with handles, keyboard adjustment and an optional magnifier
- Read the whole screen, immediately or after a three-second countdown
- Read image files, images on the clipboard and images dropped onto the window
- Works across multiple monitors and on HiDPI displays

### Text recognition

- Tuned for screen text, including small text and light text on dark backgrounds
- QR codes and barcodes are read along with the text
- Fast and high-accuracy recognition models
- Languages window for adding and removing more than 120 languages

### Results

- Text is copied as soon as it is read, and can be saved as a text file
- **As on Screen** and **Paragraphs** layouts
- Links, email addresses, phone numbers and QR codes open or copy with one click
- Confidence indicator and word highlighting
- Optional copy-only mode for the shortcut, with a notification instead of the window
- History of the last 20 results, saved as text only, with removal and undo

### Desktop integration

- Keyboard shortcut, top-bar icon and optional start at login
- Light, dark and system styles, with an animated background
- Missing components can be installed from within the app
- Ubuntu package and an install script
