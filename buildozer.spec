[app]
title = LAN Chat
package.name = lanchat
package.domain = org.lanchat

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,txt
source.exclude_dirs = .github,bin,.buildozer

version = 0.1

# pynacl + zeroconf are the likely trouble spots on a first build
# python-bidi is pinned to 0.4.2 (pure Python); newer versions need Rust and won't build.
# p4a installs pip packages without dependencies, so list them (six, ifaddr...).
requirements = python3,kivy==2.3.0,pynacl,cffi,libsodium,zeroconf,ifaddr,arabic-reshaper,python-bidi==0.4.2,six,pyjnius,android

orientation = portrait
fullscreen = 0

# CHANGE_WIFI_MULTICAST_STATE is required for mDNS discovery
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_MULTICAST_STATE
android.api = 33
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = False

[buildozer]
log_level = 2
warn_on_root = 1
