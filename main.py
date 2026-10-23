 #!/usr/bin/env python3
"""
Android entry point for LAN Chat (Buildozer / python-for-android needs main.py).

The app itself lives in lanchat_gui.py. This file only adds what a phone needs:
  - key/contact/settings files stored in the app's private writable storage
  - Wi-Fi multicast lock, otherwise Android drops mDNS (discovery) packets
  - on_pause returns True so the app is not killed when you switch apps
It also runs on a desktop:  python main.py
"""
import asyncio
import os
import sys

ANDROID = "ANDROID_ARGUMENT" in os.environ


def data_dir() -> str:
    if ANDROID:
        from android.storage import app_storage_path
        return app_storage_path()
    if sys.platform == "darwin":
        # An installed .app can't write inside itself, so keep keys/settings here.
        path = os.path.expanduser("~/Library/Application Support/LANChat")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


DATA = data_dir()

# Point lanchat.py's files at writable storage BEFORE the app uses them.
import lanchat  # noqa: E402

lanchat.IDENTITY_FILE = os.path.join(DATA, "identity.key")
lanchat.KNOWN_FILE = os.path.join(DATA, "known_peers.json")

from kivy.core.window import Window  # noqa: E402

from lanchat_gui import LanChatApp  # noqa: E402

# Keep the on-screen keyboard from covering the message box.
Window.softinput_mode = "below_target"

_multicast_lock = None


def acquire_multicast_lock():
    """Android filters multicast (mDNS) packets unless an app holds this lock."""
    global _multicast_lock
    if not ANDROID:
        return
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        context = autoclass("android.content.Context")
        wifi = activity.getSystemService(context.WIFI_SERVICE)
        _multicast_lock = wifi.createMulticastLock("lanchat")
        _multicast_lock.setReferenceCounted(False)
        _multicast_lock.acquire()
    except Exception as e:  # keep the app usable even if this fails
        print("multicast lock failed:", e)


class AndroidApp(LanChatApp):
    def on_start(self):
        acquire_multicast_lock()
        super().on_start()

    def on_pause(self):
        return True  # stay alive in the background

    def on_resume(self):
        pass


if __name__ == "__main__":
    asyncio.run(AndroidApp("").async_run(async_lib="asyncio"))
