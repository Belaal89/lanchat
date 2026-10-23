# LAN Chat / شات الشبكة

Encrypted chat between phones/PCs on the same Wi-Fi. No server, no accounts.

## Run on a computer
    pip install kivy pynacl zeroconf arabic-reshaper python-bidi
    python main.py          # or: python lanchat_gui.py <name>

## Build the Android APK (free, on GitHub)
1. Upload this folder (including `.github/`) to a new GitHub repository.
2. Open **Actions** -> **Build APK** -> **Run workflow**.
3. When it finishes, download the `lanchat-apk` artifact and install the APK.

Arabic: the DejaVu Sans font is bundled in `fonts/` (license: `fonts/DEJAVU-LICENSE.txt`).

## macOS
Easiest: double-click `run_mac.command` (needs Python 3 from python.org; it installs
the libraries into a local `venv` the first time).

Standalone app (free, built on GitHub): **Actions -> Build macOS app -> Run workflow**,
then download `lanchat-macos`, unzip, and drag `LAN Chat.app` to Applications.
The app is not notarized, so the first time: right-click -> Open -> Open
(or run `xattr -cr "/Applications/LAN Chat.app"`). Allow the "Local Network"
prompt, otherwise other users won't appear. Keys/settings are kept in
`~/Library/Application Support/LANChat`.
