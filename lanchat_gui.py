#!/usr/bin/env python3
"""
LAN encrypted chat - Kivy GUI (phone-friendly, Arabic + English).

Screens (one at a time, like any chat app):
  1. Users     - who is on the Wi-Fi; tap a name to connect / open the chat
  2. Chat      - message bubbles, back button, send box
  3. Settings  - your fingerprint, contacts' fingerprints, language

Needs lanchat.py next to this file (it reuses its crypto/identity helpers).

Install:  pip install kivy pynacl zeroconf arabic-reshaper python-bidi
Run:      python lanchat_gui.py [your-name]
"""
import asyncio
import json
import os
import re
import socket
import struct
import sys
import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.text import Label as CoreLabel
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import (NoTransition, Screen, ScreenManager,
                                    SlideTransition)
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from nacl.exceptions import BadSignatureError, CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.signing import VerifyKey
from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

import lanchat
from lanchat import (HELLO_LEN, MAX_MSG, SERVICE, fingerprint, load_identity,
                     load_known, local_ip, save_known)

# --------------------------------------------------------------------------
# Arabic support: bundled font + letter shaping + right-to-left ordering
# --------------------------------------------------------------------------
try:
    import arabic_reshaper
    try:
        from bidi.algorithm import get_display
    except ImportError:  # newer python-bidi layout
        from bidi import get_display

    def ar(text: str) -> str:
        return get_display(arabic_reshaper.reshape(text))
except ImportError:
    def ar(text: str) -> str:
        return text

# Inside a PyInstaller bundle the bundled files live under sys._MEIPASS.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(HERE, "fonts")
_regular = os.path.join(FONT_DIR, "DejaVuSans.ttf")
_bold = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
if os.path.exists(_regular):
    # Replace Kivy's default font with one that has Arabic glyphs, so every
    # widget (buttons, popups, text boxes) can show Arabic without per-widget setup.
    LabelBase.register(
        name="Roboto",
        fn_regular=_regular,
        fn_bold=_bold if os.path.exists(_bold) else None,
    )

ARABIC_RE = re.compile(r"[֐-ࣿﭐ-﻿]")


def has_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text))


def layout_text(text: str, max_w: float, font_size: float):
    """Wrap `text` to max_w pixels, then shape + reorder each line separately.

    Kivy's own wrapping runs on already-reordered text, which scrambles the line
    order of long Arabic paragraphs. Wrapping first (in logical order) and
    reshaping line by line keeps long Arabic messages readable.
    """
    meter = CoreLabel(font_size=font_size)
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}" if cur else word
            if cur and meter.get_extents(trial)[0] > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return "\n".join(ar(line) for line in lines)


# --------------------------------------------------------------------------
# Translations
# --------------------------------------------------------------------------
STR = {
    "en": {
        "app_title": "LAN Chat",
        "users": "Nearby users",
        "no_users": "Nobody found yet.\nMake sure everyone is on the same Wi-Fi "
                    "and has the app open.",
        "connected": "Connected",
        "tap_connect": "Tap to connect",
        "new": "new",
        "settings": "Settings",
        "type_msg": "Type a message...",
        "send": "Send",
        "your_name": "Your name",
        "your_fp": "Your fingerprint",
        "fp_help": "Compare this fingerprint with your contact in person (or over "
                   "a channel you trust). It should match what they see for you in "
                   "their Contacts list. If it does, nobody is in the middle.",
        "copy": "Copy",
        "contacts": "Contacts (fingerprints)",
        "no_contacts": "No contacts yet.",
        "language": "Language",
        "welcome": "Welcome",
        "choose_name": "Choose your name",
        "ok": "OK",
        "started": "You are '{name}' on {ip}:{port}",
        "start_failed": "Could not start networking: {err}",
        "connecting": "Connecting to {peer}...",
        "unknown_peer": "Unknown user.",
        "connect_failed": "Connection failed: {err}",
        "handshake_failed": "Handshake failed: {err}",
        "secure": "Secure session established",
        "disconnected": "Disconnected",
        "new_contact": "New contact '{peer}'.\nFingerprint:\n{fp}\nCompare it with "
                       "them in person to rule out a man-in-the-middle.",
        "not_connected": "Not connected to this user.",
        "send_failed": "Send failed: {err}",
    },
    "ar": {
        "app_title": "شات الشبكة",
        "users": "المستخدمون القريبون",
        "no_users": "لا يوجد أحد حاليًا.\nتأكد أن الجميع على نفس شبكة الواي فاي "
                    "وأن التطبيق مفتوح عندهم.",
        "connected": "متصل",
        "tap_connect": "اضغط للاتصال",
        "new": "جديد",
        "settings": "الإعدادات",
        "type_msg": "اكتب رسالة...",
        "send": "إرسال",
        "your_name": "اسمك",
        "your_fp": "بصمتك",
        "fp_help": "قارن هذه البصمة مع صديقك وجهًا لوجه (أو عبر وسيلة تثق بها). "
                   "يجب أن تطابق ما يظهر له عنك في قائمة جهات الاتصال عنده. "
                   "إذا تطابقت فلا أحد يتنصّت بينكما.",
        "copy": "نسخ",
        "contacts": "جهات الاتصال (البصمات)",
        "no_contacts": "لا توجد جهات اتصال بعد.",
        "language": "اللغة",
        "welcome": "أهلًا بك",
        "choose_name": "اختر اسمك",
        "ok": "موافق",
        "started": "أنت '{name}' على {ip}:{port}",
        "start_failed": "تعذّر تشغيل الشبكة: {err}",
        "connecting": "جارٍ الاتصال بـ {peer}...",
        "unknown_peer": "مستخدم غير معروف.",
        "connect_failed": "فشل الاتصال: {err}",
        "handshake_failed": "فشلت المصافحة الآمنة: {err}",
        "secure": "تم إنشاء جلسة مشفّرة",
        "disconnected": "انقطع الاتصال",
        "new_contact": "جهة اتصال جديدة '{peer}'.\nالبصمة:\n{fp}\nقارنها معه وجهًا "
                       "لوجه للتأكد من عدم وجود وسيط يتنصّت.",
        "not_connected": "أنت غير متصل بهذا المستخدم.",
        "send_failed": "فشل الإرسال: {err}",
    },
}

# Colors
BG = (0.08, 0.09, 0.11, 1)
BAR = (0.12, 0.14, 0.18, 1)
ROW = (0.16, 0.18, 0.23, 1)
MINE = (0.13, 0.45, 0.85, 1)
THEIRS = (0.22, 0.24, 0.29, 1)
DIM = (0.65, 0.68, 0.74, 1)
WHITE = (1, 1, 1, 1)
GREEN = (0.35, 0.85, 0.5, 1)


# --------------------------------------------------------------------------
# Networking + crypto (runs on the asyncio loop Kivy shares)
# --------------------------------------------------------------------------
class Core:
    def __init__(self, name, ui):
        self.name = name[:32]
        self.ui = ui
        self.sk = load_identity()
        self.ident = bytes(self.sk.verify_key)
        self.known = load_known()
        self.peers = {}     # name -> (ip, port)
        self.sessions = {}  # name -> (writer, box)
        self.active = None
        self.server = None
        self.zc = None
        self.info = None

    # ----- lifecycle -----
    async def start(self):
        try:
            loop = asyncio.get_running_loop()
            self.server = await asyncio.start_server(self.setup_session, "0.0.0.0", 0)
            port = self.server.sockets[0].getsockname()[1]
            ip = local_ip()
            # Create Zeroconf off the event loop so it keeps its own thread/loop.
            self.zc = await loop.run_in_executor(None, Zeroconf)
            self.info = ServiceInfo(
                SERVICE,
                f"{self.name}.{SERVICE}",
                addresses=[socket.inet_aton(ip)],
                port=port,
            )
            await loop.run_in_executor(None, self.zc.register_service, self.info)
            ServiceBrowser(self.zc, SERVICE, handlers=[self.on_service])
            self.ui.event("started", "", name=self.name, ip=ip, port=port)
        except Exception as e:  # noqa: BLE001 - show any startup problem to the user
            self.ui.event("start_failed", "", err=str(e))

    def shutdown(self):
        def _close():
            try:
                self.zc.unregister_service(self.info)
                self.zc.close()
            except Exception:
                pass

        if self.zc:
            t = threading.Thread(target=_close, daemon=True)
            t.start()
            t.join(2)
        if self.server:
            self.server.close()

    # ----- discovery (called from zeroconf's thread) -----
    def on_service(self, zeroconf, service_type, name, state_change):
        peer = name.split(".")[0]
        if peer == self.name:
            return
        if state_change is ServiceStateChange.Removed:
            self.peers.pop(peer, None)
        else:
            info = zeroconf.get_service_info(service_type, name)
            if info and info.parsed_addresses():
                self.peers[peer] = (info.parsed_addresses()[0], info.port)
        Clock.schedule_once(lambda dt: self.ui.refresh_peers(), 0)

    # ----- handshake -----
    async def handshake(self, reader, writer):
        eph = PrivateKey.generate()
        eph_pub = bytes(eph.public_key)
        sig = self.sk.sign(eph_pub).signature
        hello = self.ident + eph_pub + sig + self.name.encode()[:32].ljust(32, b"\0")
        writer.write(hello)
        await writer.drain()

        data = await reader.readexactly(HELLO_LEN)
        ident, peer_eph, peer_sig = data[:32], data[32:64], data[64:128]
        peer_name = data[128:].rstrip(b"\0").decode(errors="replace")
        peer_name = "".join(c for c in peer_name if c.isprintable()) or "unknown"

        try:
            VerifyKey(ident).verify(peer_eph, peer_sig)
        except BadSignatureError:
            raise ConnectionError("bad signature")

        ident_hex = ident.hex()
        pinned = self.known.get(peer_name)
        if pinned is None:
            self.known[peer_name] = ident_hex
            save_known(self.known)
            self.ui.event("new_contact", peer_name, peer=peer_name,
                          fp=fingerprint(ident))
        elif pinned != ident_hex:
            raise ConnectionError(
                f"KEY CHANGED for '{peer_name}' - possible impersonation. Refusing."
            )
        return peer_name, Box(eph, PublicKey(peer_eph))

    # ----- sessions -----
    async def setup_session(self, reader, writer):
        try:
            peer, box = await self.handshake(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            self.ui.event("handshake_failed", "", err=str(e))
            writer.close()
            return
        if peer in self.sessions:
            writer.close()
            return
        self.sessions[peer] = (writer, box)
        self.active = self.active or peer
        self.ui.session_opened(peer)
        await self.read_loop(peer, reader, box)

    async def read_loop(self, peer, reader, box):
        try:
            while True:
                (n,) = struct.unpack(">I", await reader.readexactly(4))
                if n > MAX_MSG:
                    break
                text = box.decrypt(await reader.readexactly(n)).decode(errors="replace")
                self.ui.on_message(peer, text)
        except (asyncio.IncompleteReadError, ConnectionError, CryptoError):
            pass
        finally:
            self.sessions.pop(peer, None)
            if self.active == peer:
                self.active = next(iter(self.sessions), None)
            self.ui.session_closed(peer)

    async def send(self, peer, text):
        writer, box = self.sessions[peer]
        ct = bytes(box.encrypt(text.encode()))
        writer.write(struct.pack(">I", len(ct)) + ct)
        await writer.drain()

    async def connect(self, peer):
        if peer not in self.peers:
            self.ui.event("unknown_peer", "")
            return
        ip, port = self.peers[peer]
        try:
            reader, writer = await asyncio.open_connection(ip, port)
        except OSError as e:
            self.ui.event("connect_failed", "", err=str(e))
            return
        asyncio.ensure_future(self.setup_session(reader, writer))


# --------------------------------------------------------------------------
# Settings file (language, name) - lives next to the identity key
# --------------------------------------------------------------------------
def settings_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(lanchat.IDENTITY_FILE)),
                        "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(d: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def clean_name(raw: str) -> str:
    # '.' would break mDNS service names (they are split on dots)
    return raw.strip().replace(".", "_")[:32]


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------
class LanChatApp(App):
    def __init__(self, username="", **kw):
        super().__init__(**kw)
        self.settings = load_settings()
        self.lang = self.settings.get("lang", "ar")
        self.username = clean_name(username) or self.settings.get("name", "")
        self.core = None
        self.logs = {}          # peer -> [item]; item = {"who": me|them|sys, ...}
        self.view = ""          # peer whose chat is open ("" = none)
        self.unread = set()
        self.status = None      # (key, fmt) shown at the bottom of the users screen
        self.pending = None     # peer we are connecting to (open chat when ready)
        self.input_draft = ""
        self._relayout = Clock.create_trigger(lambda dt: self.rebuild(), 0.3)

    # ----- helpers -----
    @property
    def rtl(self) -> bool:
        return self.lang == "ar"

    @property
    def align(self) -> str:
        return "right" if self.rtl else "left"

    def T(self, key, **fmt):
        text = STR[self.lang][key]
        return text.format(**fmt) if fmt else text

    def ordered(self, widgets):
        """Reverse a row of widgets for right-to-left layout."""
        return list(reversed(widgets)) if self.rtl else widgets

    def text_label(self, text, fs=14, color=WHITE, bold=False, max_w=None,
                   halign=None):
        """A wrapped, shaped, aligned label that sizes itself to its text."""
        max_w = max_w or (Window.width - dp(32))
        fs_px = sp(fs)
        shaped = layout_text(text, max_w * 0.93, fs_px)
        lab = Label(text=shaped, font_size=fs_px, color=color, bold=bold,
                    size_hint=(None, None), width=max_w,
                    text_size=(max_w, None), halign=halign or self.align,
                    valign="middle")
        lab.texture_update()
        lab.height = lab.texture_size[1] + dp(4)
        return lab

    def button(self, text, on_release, color=ROW, width=None, height=dp(48),
               bold=False):
        b = Button(text=ar(text), background_normal="", background_down="",
                   background_color=color, color=WHITE, bold=bold,
                   size_hint=(None, None) if width else (1, None),
                   height=height)
        if width:
            b.width = width
        b.bind(on_release=lambda *_: on_release())
        return b

    def bar(self, title, left=None, right=None):
        spacer = lambda: Widget(size_hint=(None, 1), width=dp(52))  # noqa: E731
        left = left or spacer()
        right = right or spacer()
        title_lbl = Label(text=ar(title), bold=True, font_size=sp(18), color=WHITE,
                          shorten=True, shorten_from="right")
        title_lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        bar = BoxLayout(size_hint_y=None, height=dp(56), padding=(dp(4), dp(4)),
                        spacing=dp(4))
        with bar.canvas.before:
            Color(*BAR)
            rect = RoundedRectangle(pos=bar.pos, size=bar.size, radius=[0])
        bar.bind(pos=lambda i, v: setattr(rect, "pos", v),
                 size=lambda i, v: setattr(rect, "size", v))
        for w in self.ordered([left, title_lbl, right]):
            bar.add_widget(w)
        return bar

    def icon_button(self, text, on_release):
        return self.button(text, on_release, color=BAR, width=dp(52),
                           height=dp(48), bold=True)

    # ----- Kivy hooks -----
    def build(self):
        self.title = ar(self.T("app_title"))
        Window.clearcolor = BG
        self.sm = ScreenManager(transition=SlideTransition(duration=0.18))
        self.build_screens("peers")
        Window.bind(width=lambda *_: self._relayout(), on_keyboard=self._on_key)
        return self.sm

    def on_start(self):
        if self.username:
            self.start_core(self.username)
        else:
            self.ask_name()

    def on_stop(self):
        if self.core:
            self.core.shutdown()

    def _on_key(self, window, key, *args):
        # Android "back" button (and Esc on desktop): go back instead of quitting.
        if key == 27 and self.sm.current != "peers":
            self.go_back()
            return True
        return False

    # ----- startup -----
    def start_core(self, name):
        self.username = name
        self.settings["name"] = name
        save_settings(self.settings)
        self.core = Core(name, self)
        asyncio.ensure_future(self.core.start())
        self.refresh_peers()

    def ask_name(self):
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        box.add_widget(self.text_label(self.T("choose_name"), 16,
                                       max_w=Window.width * 0.8 - dp(24)))
        field = TextInput(multiline=False, size_hint_y=None, height=dp(48),
                          write_tab=False)
        ok = self.button(self.T("ok"), lambda: None, color=MINE)
        box.add_widget(field)
        box.add_widget(ok)
        popup = Popup(title=ar(self.T("welcome")), content=box,
                      size_hint=(0.9, None), height=dp(260), auto_dismiss=False)

        def done(*_):
            name = clean_name(field.text)
            if not name:
                return
            popup.dismiss()
            self.start_core(name)

        ok.bind(on_release=done)
        field.bind(on_text_validate=done)
        popup.open()
        Clock.schedule_once(lambda dt: setattr(field, "focus", True), 0.3)

    # ----- building screens -----
    def build_screens(self, current):
        if getattr(self, "input", None) is not None:
            self.input_draft = self.input.text
        old = self.sm.transition
        self.sm.transition = NoTransition()
        self.sm.clear_widgets()
        for name, builder in (("peers", self.build_peers),
                              ("chat", self.build_chat),
                              ("settings", self.build_settings)):
            scr = Screen(name=name)
            scr.add_widget(builder())
            self.sm.add_widget(scr)
        self.sm.current = current
        self.sm.transition = old
        self.refresh_peers()
        self.update_status()
        if current == "chat":
            self.render_chat()
        if current == "settings":
            self.fill_settings()

    def rebuild(self):
        self.build_screens(self.sm.current)

    # --- users screen
    def build_peers(self):
        root = BoxLayout(orientation="vertical")
        gear = self.icon_button("⚙", lambda: self.show_settings())
        root.add_widget(self.bar(self.T("app_title"), right=gear))
        sv = ScrollView()
        self.peers_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                   padding=dp(10), spacing=dp(8))
        self.peers_box.bind(minimum_height=self.peers_box.setter("height"))
        sv.add_widget(self.peers_box)
        root.add_widget(sv)
        self.status_box = BoxLayout(size_hint_y=None, height=dp(44),
                                    padding=(dp(10), 0))
        root.add_widget(self.status_box)
        return root

    def refresh_peers(self):
        box = getattr(self, "peers_box", None)
        if box is None:
            return
        box.clear_widgets()
        names = []
        if self.core:
            names = sorted(set(self.core.peers) | set(self.core.sessions))
        if not names:
            box.add_widget(self.text_label(self.T("no_users"), 15, color=DIM,
                                           max_w=Window.width - dp(40),
                                           halign="center"))
            return
        for n in names:
            connected = n in self.core.sessions
            dot = "●" if connected else "○"
            status = self.T("connected") if connected else self.T("tap_connect")
            if n in self.unread:
                status += f"  •  {self.T('new')}"
            b = Button(text=f"{ar(f'{dot} {n}')}\n{ar(status)}",
                       background_normal="", background_down="",
                       background_color=ROW, color=WHITE, size_hint_y=None,
                       height=dp(68), halign=self.align, valign="middle",
                       font_size=sp(16))
            b.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(28), v[1])))
            b.bind(on_release=lambda _b, n=n: self.select_peer(n))
            box.add_widget(b)

    def update_status(self):
        box = getattr(self, "status_box", None)
        if box is None:
            return
        box.clear_widgets()
        if self.status:
            key, fmt = self.status
            lab = Label(text=ar(self.T(key, **fmt)), color=DIM, font_size=sp(12),
                        shorten=True, shorten_from="right", halign=self.align,
                        valign="middle")
            lab.bind(size=lambda i, v: setattr(i, "text_size", v))
            box.add_widget(lab)

    # --- chat screen
    def build_chat(self):
        root = BoxLayout(orientation="vertical")
        back = self.icon_button("→" if self.rtl else "←", lambda: self.go_back())
        self.chat_bar_holder = BoxLayout(size_hint_y=None, height=dp(56))
        self.chat_bar_holder.add_widget(self.bar(self.view or " ", left=back))
        root.add_widget(self.chat_bar_holder)

        self.msgs_sv = ScrollView()
        self.msgs_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                  padding=dp(8), spacing=dp(6))
        self.msgs_box.bind(minimum_height=self.msgs_box.setter("height"))
        self.msgs_sv.add_widget(self.msgs_box)
        root.add_widget(self.msgs_sv)

        self.input = TextInput(multiline=False, write_tab=False,
                               hint_text=ar(self.T("type_msg")),
                               size_hint_y=None, height=dp(48),
                               font_size=sp(16), text=self.input_draft)
        self.input.bind(on_text_validate=lambda *_: self.send_current())
        send = self.button(self.T("send"), self.send_current, color=MINE,
                           width=dp(86), bold=True)
        row = BoxLayout(size_hint_y=None, height=dp(56), padding=dp(4),
                        spacing=dp(4))
        for w in self.ordered([self.input, send]):
            row.add_widget(w)
        root.add_widget(row)
        return root

    def bubble(self, item):
        """One message row (a rounded bubble, or a centered system note)."""
        who = item["who"]
        if who == "sys":
            text = self.T(item["key"], **item.get("fmt", {}))
        else:
            text = item["text"]
        max_w = Window.width * (0.9 if who == "sys" else 0.76)
        fs = sp(12 if who == "sys" else 16)
        shaped = layout_text(text, max_w - dp(24), fs)
        lab = Label(text=shaped, font_size=fs, color=DIM if who == "sys" else WHITE,
                    size_hint=(None, None),
                    halign="right" if has_arabic(text) else "left")
        lab.texture_update()
        padx, pady = (dp(4), dp(2)) if who == "sys" else (dp(12), dp(8))
        lab.size = (lab.texture_size[0] + 2 * padx, lab.texture_size[1] + 2 * pady)
        if who != "sys":
            with lab.canvas.before:
                Color(*(MINE if who == "me" else THEIRS))
                rect = RoundedRectangle(pos=lab.pos, size=lab.size,
                                        radius=[dp(14)])
            lab.bind(pos=lambda i, v: setattr(rect, "pos", v),
                     size=lambda i, v: setattr(rect, "size", v))
        row = BoxLayout(size_hint_y=None, height=lab.height)
        if who == "me":
            row.add_widget(Widget())
            row.add_widget(lab)
        elif who == "them":
            row.add_widget(lab)
            row.add_widget(Widget())
        else:
            row.add_widget(Widget())
            row.add_widget(lab)
            row.add_widget(Widget())
        return row

    def render_chat(self):
        if not getattr(self, "msgs_box", None):
            return
        self.msgs_box.clear_widgets()
        for item in self.logs.get(self.view, [])[-300:]:
            self.msgs_box.add_widget(self.bubble(item))
        self.scroll_down()

    def scroll_down(self):
        Clock.schedule_once(lambda dt: setattr(self.msgs_sv, "scroll_y", 0), 0.05)

    # --- settings screen
    def build_settings(self):
        root = BoxLayout(orientation="vertical")
        back = self.icon_button("→" if self.rtl else "←", lambda: self.go_back())
        root.add_widget(self.bar(self.T("settings"), left=back))
        sv = ScrollView()
        self.settings_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                      padding=dp(16), spacing=dp(10))
        self.settings_box.bind(minimum_height=self.settings_box.setter("height"))
        sv.add_widget(self.settings_box)
        root.add_widget(sv)
        return root

    def fill_settings(self):
        box = self.settings_box
        box.clear_widgets()
        w = Window.width - dp(32)
        add = box.add_widget
        add(self.text_label(self.T("your_name"), 13, DIM, max_w=w))
        add(self.text_label(self.username or "-", 20, bold=True, max_w=w))
        if self.core:
            fp = fingerprint(self.core.ident).split()
            fp_text = " ".join(fp[:4]) + "\n" + " ".join(fp[4:])
            add(self.text_label(self.T("your_fp"), 13, DIM, max_w=w))
            lab = self.text_label(fp_text, 20, GREEN, bold=True, max_w=w,
                                  halign="left")
            add(lab)
            add(self.button(self.T("copy"),
                            lambda: Clipboard.copy(" ".join(fp))))
            add(self.text_label(self.T("fp_help"), 13, DIM, max_w=w))

            add(self.text_label(self.T("contacts"), 13, DIM, max_w=w))
            if not self.core.known:
                add(self.text_label(self.T("no_contacts"), 14, max_w=w))
            for name, ident_hex in sorted(self.core.known.items()):
                f = fingerprint(bytes.fromhex(ident_hex)).split()
                add(self.text_label(name, 16, bold=True, max_w=w))
                add(self.text_label(" ".join(f[:4]) + "\n" + " ".join(f[4:]), 15,
                                    GREEN, max_w=w, halign="left"))

        add(self.text_label(self.T("language"), 13, DIM, max_w=w))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        for code, label in (("ar", "العربية"), ("en", "English")):
            row.add_widget(self.button(
                label, lambda c=code: self.set_lang(c),
                color=MINE if self.lang == code else ROW,
                bold=self.lang == code))
        add(row)

    # ----- navigation -----
    def slide(self, direction):
        # In right-to-left mode the slide directions mirror.
        if self.rtl:
            direction = "right" if direction == "left" else "left"
        self.sm.transition = SlideTransition(direction=direction, duration=0.18)

    def go_to(self, screen, direction):
        self.slide(direction)
        self.sm.current = screen

    def show_settings(self):
        self.fill_settings()
        self.go_to("settings", "left")

    def go_back(self):
        self.view = ""
        self.go_to("peers", "right")
        self.refresh_peers()

    def open_chat(self, peer):
        self.view = peer
        self.unread.discard(peer)
        if self.core:
            self.core.active = peer
        self.chat_bar_holder.clear_widgets()
        back = self.icon_button("→" if self.rtl else "←", lambda: self.go_back())
        self.chat_bar_holder.add_widget(self.bar(peer, left=back))
        self.render_chat()
        self.go_to("chat", "left")
        self.refresh_peers()

    def set_lang(self, code):
        self.lang = code
        self.settings["lang"] = code
        save_settings(self.settings)
        self.title = ar(self.T("app_title"))
        self.build_screens("settings")

    # ----- peers -----
    def select_peer(self, name):
        if self.core and name in self.core.sessions:
            self.open_chat(name)
        elif self.core:
            self.pending = name
            self.event("connecting", "", peer=name)
            asyncio.ensure_future(self.core.connect(name))

    # ----- events from Core -----
    def event(self, key, target, **fmt):
        """A translated notice. target "" = status line; otherwise that chat."""
        if key in ("connect_failed", "handshake_failed", "unknown_peer"):
            self.pending = None
        if target:
            self.add_item(target, {"who": "sys", "key": key, "fmt": fmt})
        else:
            self.status = (key, fmt)
            self.update_status()

    def add_item(self, key, item):
        self.logs.setdefault(key, []).append(item)
        viewing = self.view == key and self.sm.current == "chat"
        if viewing:
            self.msgs_box.add_widget(self.bubble(item))
            self.scroll_down()
        elif item["who"] == "them":
            self.unread.add(key)
            self.refresh_peers()

    def session_opened(self, peer):
        self.add_item(peer, {"who": "sys", "key": "secure"})
        self.refresh_peers()
        if self.pending == peer:
            self.pending = None
            self.open_chat(peer)

    def session_closed(self, peer):
        self.add_item(peer, {"who": "sys", "key": "disconnected"})
        self.refresh_peers()

    def on_message(self, peer, text):
        self.add_item(peer, {"who": "them", "text": text})

    # ----- sending -----
    def send_current(self):
        text = self.input.text.strip()
        if not text or not self.view:
            return
        peer = self.view
        if self.core and peer in self.core.sessions:
            asyncio.ensure_future(self._send(peer, text))
            self.add_item(peer, {"who": "me", "text": text})
            self.input.text = ""
        else:
            self.add_item(peer, {"who": "sys", "key": "not_connected"})
        Clock.schedule_once(lambda dt: setattr(self.input, "focus", True), 0.1)

    async def _send(self, peer, text):
        try:
            await self.core.send(peer, text)
        except (ConnectionError, OSError) as e:
            self.event("send_failed", peer, err=str(e))


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(LanChatApp(name).async_run(async_lib="asyncio"))
