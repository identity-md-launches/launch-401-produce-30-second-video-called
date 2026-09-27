#!/usr/bin/env python3
"""Build One block decides from Ethereum contract SVGs and a pinned chain state.

Live mode refreshes data/chain_snapshot.json from Ethereum before rendering.
--offline rebuilds from that committed snapshot without network access.
Only the Python standard library and ffmpeg/ffprobe are required.
"""

import argparse
import base64
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET
import zlib


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "chain_snapshot.json"
ARTIFACTS = ROOT / "artifacts"
WORK = ROOT / "test" / "scratch" / "render"
SWARM = "0x999ce0ce8c5f7661e0c74a568ffe27ceb9177bdb"
PIXEL = "0x07Fd9841eEB6a359EfB30f861D59bFa1f6B03FcA"
DEPLOY_TX = "0x7c73ad94945b6cd5a53c374b7f0340fd25ec07c64962a399d5912f7a6ecf985f"
LOG_RPC = "https://rpc.flashbots.net"
LOG_INDEX = "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api"
CALL_RPC = "https://ethereum-rpc.publicnode.com"
MINT_TOPIC = "0x25b428dfde728ccfaddad7e29e4ac23c24ed7fd1a6e3e3f91894a9a073f5dfff"
REVEAL_TOPIC = "0x9a5a126d5a9736641cc82adfebab5f62e9429e7963b9810cffdc9184c5fda2a7"
FPS = 30
FRAMES = 30 * FPS


def rpc(url, method, params, tries=5):
    req_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, req_body,
                                         {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.load(response)
            if "error" in result:
                raise RuntimeError(f"{method}: {result['error']}")
            return result["result"]
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def rpc_batch(url, calls, tries=5):
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p}
               for i, (m, p) in enumerate(calls)]
    body = json.dumps(payload).encode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, body,
                                         {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as response:
                results = json.load(response)
            results = {x["id"]: x for x in results}
            if len(results) != len(calls):
                raise RuntimeError("Incomplete RPC batch")
            if any("error" in results[i] for i in range(len(calls))):
                raise RuntimeError("RPC batch error")
            return [results[i]["result"] for i in range(len(calls))]
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def uint_arg(n):
    return f"{n:064x}"


def call(to, selector, args="", block="latest"):
    return rpc(CALL_RPC, "eth_call", [{"to": to, "data": selector + args}, block])


def decode_string(hex_result):
    data = bytes.fromhex(hex_result[2:])
    offset = int.from_bytes(data[:32], "big")
    length = int.from_bytes(data[offset:offset + 32], "big")
    return data[offset + 32:offset + 32 + length].decode()


def rol(n, amount):
    mask = (1 << 64) - 1
    return ((n << amount) | (n >> (64 - amount))) & mask if amount else n


def keccak256(message):
    """Ethereum Keccak-256, used to audit the Revealed event seed."""
    rotation = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2],
                [62, 6, 43, 15, 61], [28, 55, 25, 21, 56],
                [27, 20, 39, 8, 14]]
    rounds = [
        0x0000000000000001, 0x0000000000008082, 0x800000000000808a,
        0x8000000080008000, 0x000000000000808b, 0x0000000080000001,
        0x8000000080008081, 0x8000000000008009, 0x000000000000008a,
        0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
        0x000000008000808b, 0x800000000000008b, 0x8000000000008089,
        0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
        0x000000000000800a, 0x800000008000000a, 0x8000000080008081,
        0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
    ]
    rate = 136
    padded = bytearray(message)
    padded.append(1)
    padded.extend(b"\0" * ((rate - len(padded) % rate) % rate))
    padded[-1] |= 0x80
    state = [0] * 25
    mask = (1 << 64) - 1
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:i * 8 + 8], "little")
        for rc in rounds:
            c = [state[x] ^ state[x+5] ^ state[x+10] ^ state[x+15] ^ state[x+20]
                 for x in range(5)]
            d = [c[(x-1) % 5] ^ rol(c[(x+1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    state[x+5*y] ^= d[x]
            b = [0] * 25
            for x in range(5):
                for y in range(5):
                    b[y + 5*((2*x + 3*y) % 5)] = rol(state[x+5*y], rotation[x][y])
            for x in range(5):
                for y in range(5):
                    state[x+5*y] = (b[x+5*y] ^ ((~b[(x+1) % 5+5*y]) & b[(x+2) % 5+5*y])) & mask
            state[0] ^= rc
    return b"".join(x.to_bytes(8, "little") for x in state)[:32]


def get_logs(topic, first_block, last_block):
    # The public RPC's historical eth_getLogs window is truncated. RouteScan's
    # Ethereum log index serves the event topics/data for the full span.
    params = urllib.parse.urlencode({"module": "logs", "action": "getLogs",
        "fromBlock": first_block, "toBlock": last_block,
        "address": SWARM, "topic0": topic, "page": 1, "offset": 1000})
    url = LOG_INDEX + "?" + params
    for attempt in range(7):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.load(response)
            if not isinstance(result.get("result"), list):
                if result.get("message") == "No logs found":
                    return []
                raise RuntimeError(f"log index: {result}")
            logs = result["result"]
            if len(logs) >= 1000:
                if first_block == last_block:
                    raise RuntimeError("Log page may be truncated within one block")
                middle = (first_block + last_block) // 2
                return get_logs(topic, first_block, middle) + get_logs(topic, middle + 1, last_block)
            return logs
        except (urllib.error.URLError, RuntimeError):
            if attempt == 6:
                raise
            time.sleep(3 * (attempt + 1))


def svg_rects(svg):
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.attrib["viewBox"] == "0 0 24 24"
    rects = []
    for element in root:
        assert element.tag.endswith("rect"), element.tag
        a = element.attrib
        assert not set(a) - {"x", "y", "width", "height", "fill"}, a
        x, y, w, h = (int(a[k]) for k in ("x", "y", "width", "height"))
        fill = a["fill"]
        assert fill.startswith("#") and len(fill) == 7
        assert 0 <= x < 24 and 0 <= y < 24 and w > 0 and h > 0
        assert x + w <= 24 and y + h <= 24
        rects.append((x, y, w, h, fill))
    return rects


def fetch_chain():
    # A small head margin ensures the two public RPCs agree on the same block.
    head = min(int(rpc(LOG_RPC, "eth_blockNumber", []), 16),
               int(rpc(CALL_RPC, "eth_blockNumber", []), 16))
    read_height = head - 2
    pinned = hex(read_height)
    creation = rpc(CALL_RPC, "eth_getTransactionReceipt", [DEPLOY_TX])
    first_block = int(creation["blockNumber"], 16)
    assert creation["contractAddress"].lower() == SWARM
    print(f"Reading Ethereum mainnet through block {read_height}", flush=True)

    minted_logs = get_logs(MINT_TOPIC, first_block, read_height)
    reveal_logs = get_logs(REVEAL_TOPIC, first_block, read_height)
    minted = {}
    for log in minted_logs:
        token_id = int(log["topics"][2], 16)
        assert token_id not in minted
        minted[token_id] = log
    revealed = {}
    for log in reveal_logs:
        token_id = int(log["topics"][1], 16)
        assert token_id not in revealed
        revealed[token_id] = log
    assert 343 in minted and 343 in revealed
    mint_log = minted[343]
    reveal_log = revealed[343]
    mint_block = int(mint_log["data"], 16)
    assert mint_block == int(mint_log["blockNumber"], 16)
    next_block = rpc(CALL_RPC, "eth_getBlockByNumber", [hex(mint_block + 1), False])
    next_hash = next_block["hash"]
    minter = bytes.fromhex(mint_log["topics"][1][-40:])
    seed = int(reveal_log["data"], 16)
    assert int(call(SWARM, "0x82829f74", uint_arg(343), pinned), 16) == seed
    abi_encoded = bytes.fromhex(next_hash[2:]) + (343).to_bytes(32, "big")
    abi_encoded += minter.rjust(32, b"\0") + bytes.fromhex(SWARM[2:]).rjust(32, b"\0")
    assert int.from_bytes(keccak256(abi_encoded), "big") == seed

    total = int(call(SWARM, "0xa2309ff8", block=pinned), 16)
    assert total == len(minted_logs), (total, len(minted_logs))
    assert sorted(minted) == list(range(1, total + 1))
    assert set(revealed).issubset(minted)

    # Ask the deployed PixelArt contract for the attributes of every revealed
    # seed. This counts Gold from live contract results, not from the rarity odds.
    ids = sorted(revealed)
    skins = {}
    for start in range(0, len(ids), 70):
        batch_ids = ids[start:start + 70]
        calls = [("eth_call", [{"to": PIXEL,
            "data": "0xd05dcc6a" + uint_arg(int(revealed[i]["data"], 16))}, pinned])
            for i in batch_ids]
        for token_id, encoded in zip(batch_ids, rpc_batch(CALL_RPC, calls)):
            attrs = json.loads(decode_string(encoded))
            skin = next(x["value"] for x in attrs if x["trait_type"] == "Skin")
            skins[token_id] = skin
    gold = sum(s == "Gold" for s in skins.values())
    assert skins[343] == "Gold"
    print(f"{gold} Gold among {len(revealed)} revealed; {total} minted", flush=True)

    uri = decode_string(call(SWARM, "0xc87b56dd", uint_arg(343), pinned))
    assert uri.startswith("data:application/json;base64,")
    metadata = json.loads(base64.b64decode(uri.split(",", 1)[1]))
    assert metadata["name"] == "Swarm Pepe #343"
    assert metadata["image"].startswith("data:image/svg+xml;base64,")
    token_svg = base64.b64decode(metadata["image"].split(",", 1)[1]).decode()
    rendered = decode_string(call(PIXEL, "0xd12a4c98", uint_arg(seed), pinned))
    assert token_svg == rendered
    assert next(a["value"] for a in metadata["attributes"] if a["trait_type"] == "Skin") == "Gold"
    placeholder = decode_string(call(PIXEL, "0x4b393033", block=pinned))
    assert svg_rects(placeholder) and svg_rects(token_svg)
    block = rpc(CALL_RPC, "eth_getBlockByNumber", [pinned, False])

    snapshot = {
        "chain_id": int(rpc(CALL_RPC, "eth_chainId", []), 16),
        "read_block": read_height,
        "read_block_hash": block["hash"],
        "contracts": {"SwarmPepe": SWARM, "PixelArt": PIXEL},
        "deployment_tx": DEPLOY_TX,
        "deployment_block": first_block,
        "token_id": 343,
        "mint_block": mint_block,
        "mint_event_tx": mint_log["transactionHash"],
        "next_block": mint_block + 1,
        "next_block_hash": next_hash,
        "reveal_block": int(reveal_log["blockNumber"], 16),
        "reveal_event_tx": reveal_log["transactionHash"],
        "seed": str(seed),
        "gold_revealed": gold,
        "revealed_count": len(revealed),
        "total_minted": total,
        "token_attributes": metadata["attributes"],
        "token_svg": token_svg,
        "placeholder_svg": placeholder,
        "revealed_seeds": [[i, str(int(revealed[i]["data"], 16))] for i in ids],
        "checks": ["Minted event", "Revealed event", "seedOf(343)",
                   "keccak256(abi.encode(blockhash, id, minter, contract))",
                   "totalMinted()", "PixelArt.attributes(seed) for every revealed id",
                   "tokenURI(343) SVG equals PixelArt.renderSVG(seed)",
                   "PixelArt.placeholderSVG()"],
    }
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def rgb(value):
    return bytes.fromhex(value.lstrip("#"))


def fill(canvas, width, height, x, y, w, h, color):
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    row = rgb(color) * (x1 - x0)
    for yy in range(y0, y1):
        start = (yy * width + x0) * 3
        canvas[start:start + len(row)] = row


def png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)


def write_png(path, width, height, canvas):
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(canvas[y * stride:(y + 1) * stride])
    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += png_chunk(b"IDAT", zlib.compress(raw, 5))
    png += png_chunk(b"IEND", b"")
    path.write_bytes(png)


def art_geometry(width, phase):
    if width == 1080:
        if phase == "intro":
            return (204, 172, 28)
        if phase == "hash":
            return (300, 150, 20)
        if phase == "formula":
            return (300, 150, 20)
        if phase == "finished":
            return (80, 215, 24)
        return (204, 170, 28)
    if phase == "intro":
        return (600, 150, 30)
    if phase == "hash" or phase == "formula":
        return (220, 180, 27)
    if phase == "finished":
        return (220, 175, 29)
    return (220, 175, 29)


def draw_art(canvas, width, height, rects, geometry, clip_right=None):
    ox, oy, scale = geometry
    for x, y, w, h, color in rects:
        left = ox + x * scale
        right = ox + (x + w) * scale
        if clip_right is not None:
            right = min(right, clip_right)
        fill(canvas, width, height, left, oy + y * scale,
             right - left, h * scale, color)


def base_canvas(width, height, phase):
    if phase == "black":
        return bytearray(rgb("#000000") * (width * height))
    canvas = bytearray(rgb("#101713") * (width * height))
    # Editorial frame around the on-chain pixels; all artwork itself is copied
    # exactly from SVG rect fills in SVG order.
    fill(canvas, width, height, 38, 38, width - 76, 2, "#5b694f")
    fill(canvas, width, height, 38, height - 40, width - 76, 2, "#5b694f")
    fill(canvas, width, height, 38, 38, 2, height - 76, "#5b694f")
    fill(canvas, width, height, width - 40, 38, 2, height - 76, "#5b694f")
    fill(canvas, width, height, 38, 38, 90, 5, "#d8b23c")
    if phase in ("intro", "hash", "formula", "reveal", "finished"):
        ox, oy, scale = art_geometry(width, phase)
        fill(canvas, width, height, ox - 7, oy - 7, 24 * scale + 14, 24 * scale + 14, "#29352b")
        fill(canvas, width, height, ox - 3, oy - 3, 24 * scale + 6, 24 * scale + 6, "#101713")
    if phase == "formula":
        if width == 1080:
            x, y, w, h = 80, 175, 920, 760
        else:
            x, y, w, h = 1045, 210, 790, 660
        fill(canvas, width, height, x, y, w, h, "#17211b")
        fill(canvas, width, height, x, y, w, 2, "#d8b23c")
        fill(canvas, width, height, x, y + h - 2, w, 2, "#d8b23c")
        fill(canvas, width, height, x, y, 2, h, "#d8b23c")
        fill(canvas, width, height, x + w - 2, y, 2, h, "#d8b23c")
    return canvas


def frame_spec(frame, rect_count):
    if frame >= 840:
        return "black", 0, 0
    if frame >= 750:
        return "finished", rect_count, 0
    if frame >= 735:
        return "reveal", rect_count, 0
    if frame >= 405:
        # First rect at 13.5 s; the final SVG rect arrives at 24.5 s.
        count = 1 + (frame - 405) * (rect_count - 1) // 330
        return "reveal", count, 0
    if frame >= 390:
        return "formula", 0, 405 - frame
    if frame >= 300:
        return "formula", 0, 15
    if frame >= 120:
        return "hash", 0, 15
    return "intro", 0, 15


def render_frames(snapshot, width, height):
    placeholder = svg_rects(snapshot["placeholder_svg"])
    art = svg_rects(snapshot["token_svg"])
    folder = WORK / f"frames_{width}x{height}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    cache = {}
    for f in range(FRAMES):
        phase, count, wipe = frame_spec(f, len(art))
        key = (phase, count, wipe)
        source = cache.get(key)
        if source is None:
            canvas = base_canvas(width, height, phase)
            if phase in ("intro", "hash", "formula") and (width != 1080 or phase != "formula" or f >= 390):
                geom = art_geometry(width, phase)
                # The moving cover at 13 s wipes the real placeholder pixels.
                clip = None
                if f >= 390:
                    clip = geom[0] + 24 * geom[2] * wipe // 15
                draw_art(canvas, width, height, placeholder, geom, clip)
            if phase in ("reveal", "finished"):
                draw_art(canvas, width, height, art[:count], art_geometry(width, phase))
            source = folder / f"unique_{len(cache):04d}.png"
            write_png(source, width, height, canvas)
            cache[key] = source
        os.link(source, folder / f"{f:05d}.png")
    print(f"{width}x{height}: {FRAMES} PNG frames, {len(cache)} unique pixel states", flush=True)
    return folder


def ass_time(frame):
    cs = round(frame * 100 / FPS)
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    seconds, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def ass_escape(s):
    return s.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def make_ass(snapshot, width, height):
    is_square = width == 1080
    font = "DejaVu Sans Mono"
    styles = [
        ("Title", 42, "&H00E9EFE6", 0),
        ("Caption", 31, "&H00E9EFE6", 0),
        ("Mono", 24, "&H00E6D7AD", 0),
        ("Small", 25, "&H00C2D0C0", 0),
        ("Seed", 28, "&H00E6C566", 0),
        ("Gold", 42, "&H004AC1E8", 0),
        ("Final", 34, "&H00FFFFFF", 0),
    ]
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}",
             f"PlayResY: {height}", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for name, size, color, bold in styles:
        lines.append(f"Style: {name},{font},{size},{color},{color},&H00000000,&H00000000,{bold},0,0,0,100,100,0,0,1,0,0,7,0,0,0,1")
    lines += ["", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def event(a, b, style, x, y, content, align=7, move=None):
        tag = f"\\an{align}\\pos({x},{y})"
        if move:
            tag = f"\\an{align}\\move({move[0]},{move[1]},{x},{y},0,450)"
        lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{{{tag}}}{ass_escape(content)}")

    event(0, 840, "Title", width // 2, 66, "ONE BLOCK DECIDES", align=8)
    event(0, 120, "Caption", width // 2, 900 if is_square else 940,
          f"Swarm Pepe #343 · minted in block {snapshot['mint_block']}", align=8)

    hash_value = snapshot["next_block_hash"][2:]
    hx = 140 if is_square else 1050
    hy = 805 if is_square else 510
    event(120, 300, "Caption", hx, 720 if is_square else 395,
          "its art came from the next block")
    for i in range(1, 65):
        shown = hash_value[:i]
        text = "0x" + shown[:32]
        if len(shown) > 32:
            text += "\n" + shown[32:]
        event(119 + i, 120 + i, "Mono", hx, hy, text)
    event(184, 300, "Mono", hx, hy,
          "0x" + hash_value[:32] + "\n" + hash_value[32:])

    if is_square:
        x, y, gap = 125, 230, 84
        seed_x, seed_y = 125, 785
    else:
        x, y, gap = 1080, 255, 72
        seed_x, seed_y = 1080, 705
    event(300, 390, "Caption", x, y, "keccak256(")
    hrows = ["0x" + hash_value[:32], hash_value[32:]]
    event(300, 390, "Mono", x + 25, y + gap, "\n".join(hrows), move=(x+25, y-100))
    event(309, 390, "Mono", x + 25, y + gap * 2 + 20, "343", move=(x+25, y-60))
    event(318, 390, "Mono", x + 25, y + gap * 3 + 20, "minter", move=(x+25, y-50))
    event(327, 390, "Mono", x + 25, y + gap * 4 + 20, SWARM, move=(x+25, y-40))
    event(340, 390, "Caption", x, y + gap * 5 + 24, ")")
    seed = snapshot["seed"]
    assert len(seed) == 78
    event(350, 390, "Small", seed_x, seed_y - 39, "SEED")
    event(350, 390, "Seed", seed_x, seed_y,
          seed[:26] + "\n" + seed[26:52] + "\n" + seed[52:])

    event(405, 750, "Small", width // 2, 944 if is_square else 930,
          "ONE RECT AT A TIME · IN CONTRACT ORDER", align=8)
    tx = 685 if is_square else 1100
    ty = 400 if is_square else 430
    gold_text = "Skin: Gold"
    for i in range(1, len(gold_text) + 1):
        event(749 + i, 750 + i, "Gold", tx, ty, gold_text[:i])
    event(750 + len(gold_text), 840, "Gold", tx, ty, gold_text)
    count_text = f"{snapshot['gold_revealed']} of {snapshot['total_minted']}"
    for i in range(1, len(count_text) + 1):
        event(767 + i, 768 + i, "Caption", tx, ty + 92, count_text[:i])
    event(768 + len(count_text), 840, "Caption", tx, ty + 92, count_text)

    event(840, 900, "Final", width // 2, 470,
          "reveal(uint256[]) — anyone can call it.", align=8)
    event(840, 900, "Mono", width // 2, 560, SWARM, align=8)
    path = WORK / f"captions_{width}x{height}.ass"
    path.write_text("\n".join(lines) + "\n")
    return path


def synth_audio(path):
    """Original quiet mechanical track: 120 BPM, final visual hit at 24.5 s."""
    sample_rate = 48000
    count = 30 * sample_rate
    samples = bytearray(count * 2)
    # Deterministic Xorshift noise for dry clicks; no sampled or licensed audio.
    noise = 0x3431
    notes = [55.0, 65.406, 73.416, 82.407]
    for i in range(count):
        t = i / sample_rate
        if t >= 28:
            value = 0.0
        else:
            beat = int(t * 2)
            phase = t - beat * 0.5
            strength = 0.35 + 0.65 * min(1.0, max(0.0, (t - 10) / 15))
            freq = notes[(beat // 4) % len(notes)]
            kick = math.sin(2 * math.pi * (50 + 80 * math.exp(-phase * 30)) * phase)
            kick *= math.exp(-phase * 22) * (0.11 if beat % 2 == 0 else 0.065)
            hum = math.sin(2 * math.pi * freq * t) * (0.018 + 0.014 * strength)
            pulse = math.sin(2 * math.pi * 220 * phase) * math.exp(-phase * 35) * 0.025
            noise ^= (noise << 13) & 0xffffffff
            noise ^= noise >> 17
            noise ^= (noise << 5) & 0xffffffff
            click = ((noise & 0xffff) / 32768 - 1) * math.exp(-phase * 190) * 0.027
            if t >= 13:
                half_phase = (t * 4) % 1 / 4
                click += (((noise >> 16) & 0xffff) / 32768 - 1) * math.exp(-half_phase * 280) * 0.018 * strength
            accent_phase = t - 24.5
            accent = 0.0
            if 0 <= accent_phase < 0.6:
                accent = math.sin(2 * math.pi * 164.814 * accent_phase) * math.exp(-accent_phase * 8) * 0.11
            fade = min(1.0, (28 - t) / 0.5)
            value = (kick + hum + pulse + click + accent) * max(0.0, fade)
        struct.pack_into("<h", samples, i * 2,
                         max(-32767, min(32767, int(value * 32767))))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples)
    return path


def encode(folder, ass_path, audio_path, output):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-framerate", str(FPS), "-i", str(folder / "%05d.png"),
               "-i", str(audio_path), "-vf", f"ass={ass_path}",
               "-c:v", "libx264", "-preset", "fast", "-crf", "18",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
               "-t", "30", "-movflags", "+faststart", str(output)]
    subprocess.run(command, check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_name,width,height,codec_type", "-of", "json",
        str(output)], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    assert abs(float(info["format"]["duration"]) - 30) < 0.04
    assert any(s["codec_type"] == "video" and s["codec_name"] == "h264"
               for s in info["streams"])
    assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac"
               for s in info["streams"])
    print(f"Encoded {output}: {info['format']['duration']} s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="rebuild from data/chain_snapshot.json")
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        parser.error("ffmpeg and ffprobe are required")
    snapshot = json.loads(SNAPSHOT.read_text()) if args.offline else fetch_chain()
    assert snapshot["chain_id"] == 1 and snapshot["token_id"] == 343
    assert snapshot["gold_revealed"] > 0 and snapshot["total_minted"] >= 343
    WORK.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    audio = synth_audio(WORK / "original_track.wav")
    for width, height, filename in [(1080, 1080, "video.mp4"),
                                    (1920, 1080, "video_1920x1080.mp4")]:
        folder = render_frames(snapshot, width, height)
        ass = make_ass(snapshot, width, height)
        encode(folder, ass, audio, ARTIFACTS / filename)


if __name__ == "__main__":
    main()
