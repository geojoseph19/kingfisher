# Changelog

## Unreleased

### Fixed

- Snap: pointing at a detected link, email address, phone number or code showed a grey
  box that was wider than the item and out of line with it
- Snap: the application icon in the launcher was a low-colour copy with a flat background
  and rough edges. It is now rendered from the original artwork, as are the README banners

## 1.0.5 - 2026-11-13

### Fixed

- Arabic, Urdu and other right-to-left text came out with the words of every line in
  reverse order in the **As on Screen** format. Lines now keep their reading order, also
  when they contain numbers or left-to-right phrases
- Chinese and Japanese text had a space between every word or character; it is now joined
  the way it is written
- Images with a transparent background (logos, exported diagrams, signatures) opened from
  a file or pasted from the clipboard were read as empty, because the transparent areas
  turned black. They are now filled with a contrasting background
- A damaged image file could fail without any message
- Copy-only mode: after selecting an area and switching to **Screen**, the reading
  animation ran over the earlier selection although the whole screen was being read. The
  animation now always covers the area that is read
- With two monitors, a selection left on the other screen is no longer shown while reading

## 1.0.4 - 2026-11-13

### Fixed

- Snap: a selection smaller than 800 pixels failed with "Unexpected error: … 'resize' …
  returned NULL" unless it contained a QR code or barcode. The search for small codes
  used an operation that the snap's image library cannot perform
- A fault while reading QR codes and barcodes no longer stops the text from being read

## 1.0.3 - 2026-11-13

### Fixed

- A capture started from the top-bar icon's menu could show the menu itself, half faded,
  in the picture

## 1.0.2 - 2026-11-13

### Fixed

- Snap on Ubuntu 24.04: capturing failed with "Kingfisher is not allowed to take
  screenshots" and no permission was ever asked for. GNOME cannot show its screenshot
  question for snaps and refuses instead; Kingfisher now asks through the screen-sharing
  dialog, once, as it does on Ubuntu 22.04
- Denying the screenshot permission is no longer a dead end: Kingfisher offers the
  screen-sharing dialog instead
- Messages and Preferences no longer send you to the README. **Preferences › Screen Access**
  shows whether permission has been given

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
