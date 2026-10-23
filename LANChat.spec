# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds "LAN Chat.app" for macOS.
#   pyinstaller --noconfirm LANChat.spec

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[("fonts", "fonts")],
    hiddenimports=["lanchat", "lanchat_gui", "zeroconf", "ifaddr", "nacl",
                   "arabic_reshaper", "bidi", "bidi.algorithm"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LAN Chat",
    console=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="LAN Chat")

app = BUNDLE(
    coll,
    name="LAN Chat.app",
    bundle_identifier="org.lanchat.app",
    info_plist={
        "CFBundleDisplayName": "LAN Chat",
        "CFBundleShortVersionString": "0.1",
        "NSHighResolutionCapable": True,
        # macOS asks for "Local Network" permission; without these keys
        # discovery (Bonjour/mDNS) is silently blocked on recent macOS.
        "NSLocalNetworkUsageDescription":
            "LAN Chat uses your local network to find and chat with nearby users.",
        "NSBonjourServices": ["_lanchat._tcp"],
    },
)
