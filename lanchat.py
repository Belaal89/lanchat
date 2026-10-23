#!/usr/bin/env python3
"""
LAN encrypted chat (starter).

- Discovery : mDNS / DNS-SD (zeroconf)
- Transport : direct TCP between peers (no server)
- Crypto    : ephemeral X25519 per session (forward secrecy)
              + Ed25519 long-term identity signs the ephemeral key
              + XSalsa20-Poly1305 (NaCl Box) for every message
- Identity  : TOFU (trust on first use) + fingerprint to compare in person

Install:  pip install pynacl zeroconf
Run:      python lanchat.py <your-name>
Commands: /peers   list discovered users
          /connect <name>
          /to <name>   switch active chat
          /quit
Any other line is sent to the active chat.
"""
import asyncio
import hashlib
import json
import os
import socket
import struct
import sys

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.signing import SigningKey, VerifyKey
from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

SERVICE = "_lanchat._tcp.local."
IDENTITY_FILE = "identity.key"
KNOWN_FILE = "known_peers.json"
HELLO_LEN = 32 + 32 + 64 + 32  # identity + ephemeral + signature + name
MAX_MSG = 64 * 1024


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def fingerprint(ident: bytes) -> str:
    h = hashlib.sha256(ident).hexdigest()[:32]
    return " ".join(h[i:i + 4] for i in range(0, 32, 4))


def load_identity() -> SigningKey:
    if os.path.exists(IDENTITY_FILE):
        with open(IDENTITY_FILE, "rb") as f:
            return SigningKey(f.read())
    sk = SigningKey.generate()
    fd = os.open(IDENTITY_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(bytes(sk))
    return sk


def load_known() -> dict:
    try:
        with open(KNOWN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_known(d: dict) -> None:
    with open(KNOWN_FILE, "w") as f:
        json.dump(d, f, indent=2)


class Chat:
    def __init__(self, name: str):
        self.name = name[:32]
        self.sk = load_identity()
        self.ident = bytes(self.sk.verify_key)
        self.known = load_known()
        self.peers = {}     # name -> (ip, port)
        self.sessions = {}  # name -> (writer, box)
        self.active = None

    # ---------- discovery ----------
    def on_service(self, zeroconf, service_type, name, state_change):
        peer = name.split(".")[0]
        if peer == self.name:
            return
        if state_change is ServiceStateChange.Removed:
            self.peers.pop(peer, None)
            return
        info = zeroconf.get_service_info(service_type, name)
        if info and info.parsed_addresses():
            self.peers[peer] = (info.parsed_addresses()[0], info.port)

    # ---------- handshake ----------
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

        # TOFU: pin the identity key the first time we see this name
        ident_hex = ident.hex()
        pinned = self.known.get(peer_name)
        if pinned is None:
            self.known[peer_name] = ident_hex
            save_known(self.known)
            print(f"\n[!] New contact '{peer_name}'. Fingerprint: {fingerprint(ident)}")
            print("    Compare it with them in person to rule out a man-in-the-middle.")
        elif pinned != ident_hex:
            raise ConnectionError(
                f"KEY CHANGED for '{peer_name}' - possible impersonation. Refusing."
            )

        return peer_name, Box(eph, PublicKey(peer_eph))

    # ---------- messaging ----------
    async def read_loop(self, peer, reader, box):
        try:
            while True:
                (n,) = struct.unpack(">I", await reader.readexactly(4))
                if n > MAX_MSG:
                    break
                text = box.decrypt(await reader.readexactly(n)).decode(errors="replace")
                print(f"\n[{peer}] {text}\n> ", end="", flush=True)
        except (asyncio.IncompleteReadError, ConnectionError, CryptoError):
            pass
        finally:
            self.sessions.pop(peer, None)
            if self.active == peer:
                self.active = None
            print(f"\n[-] {peer} disconnected")

    async def setup_session(self, reader, writer):
        try:
            peer, box = await self.handshake(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            print(f"\n[x] Handshake failed: {e}")
            writer.close()
            return
        if peer in self.sessions:
            writer.close()
            return
        self.sessions[peer] = (writer, box)
        self.active = self.active or peer
        print(f"\n[+] Secure session with {peer}")
        await self.read_loop(peer, reader, box)

    async def send(self, peer, text):
        writer, box = self.sessions[peer]
        ct = bytes(box.encrypt(text.encode()))  # random 24-byte nonce prepended
        writer.write(struct.pack(">I", len(ct)) + ct)
        await writer.drain()

    async def connect(self, peer):
        if peer not in self.peers:
            print("Unknown peer. Use /peers")
            return
        ip, port = self.peers[peer]
        try:
            reader, writer = await asyncio.open_connection(ip, port)
        except OSError as e:
            print(f"Connection failed: {e}")
            return
        asyncio.create_task(self.setup_session(reader, writer))

    # ---------- CLI ----------
    async def cli(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = (await loop.run_in_executor(None, input, "> ")).strip()
            except EOFError:
                return
            if not line:
                continue
            if line == "/quit":
                return
            if line == "/peers":
                for n, (ip, port) in self.peers.items():
                    mark = "*" if n in self.sessions else " "
                    print(f" {mark} {n}  {ip}:{port}")
                if not self.peers:
                    print(" (nobody found yet)")
            elif line.startswith("/connect "):
                await self.connect(line[9:].strip())
            elif line.startswith("/to "):
                p = line[4:].strip()
                if p in self.sessions:
                    self.active = p
                else:
                    print("No session with that user.")
            elif self.active in self.sessions:
                await self.send(self.active, line)
            else:
                print("No active chat. /peers then /connect <name>")


async def main(zc: Zeroconf, chat: Chat):
    server = await asyncio.start_server(chat.setup_session, "0.0.0.0", 0)
    port = server.sockets[0].getsockname()[1]
    ip = local_ip()

    info = ServiceInfo(
        SERVICE,
        f"{chat.name}.{SERVICE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, zc.register_service, info)
    ServiceBrowser(zc, SERVICE, handlers=[chat.on_service])

    print(f"You are '{chat.name}' on {ip}:{port}")
    print(f"Your fingerprint: {fingerprint(chat.ident)}")
    print("Commands: /peers  /connect <name>  /to <name>  /quit\n")
    try:
        await chat.cli()
    finally:
        await loop.run_in_executor(None, zc.unregister_service, info)
        server.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python lanchat.py <your-name>")
    zc = Zeroconf()
    try:
        asyncio.run(main(zc, Chat(sys.argv[1])))
    finally:
        zc.close()
