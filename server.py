#!/usr/bin/env python3
# Lottery Record System + Workspace Key + Discord Bot (single file)
# - แต่ละคีย์ = 1 Workspace (ชุดข้อมูลแยก) | คีย์เดียวกัน = ร่วมงานกันได้
# - คีย์สุ่ม 4 ตัว / ตั้งเองได้ 1-100 ตัว
# - Discord: /getkey (ขอคีย์ส่วนตัว 24 ชม.) และ /key list/add/delete/me
# Run:  python server.py
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import shutil
import subprocess
import platform
import threading
import time
import re
import sys
import os
import secrets
import string
import math
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

# ========== CONFIGURATION ==========
PORT = 8000
BIND_IP = "0.0.0.0"
ROOT = Path(__file__).parent
DATA = ROOT / "data.json"              # (เก็บไว้สำหรับข้อมูลรูปแบบเก่า)
WS_DIR = ROOT / "workspaces"            # ข้อมูลแยกตามคีย์: workspaces/<KEY>.json
WS_DIR.mkdir(exist_ok=True)
KEYS_FILE = ROOT / "keys.json"
CONFIG_FILE = ROOT / "config.json"
BACKUP = ROOT / "data-backups"
BACKUP.mkdir(exist_ok=True)
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "server.log"
MAX_LOG_LINES = 8000          # เก็บ log สูงสุดกี่บรรทัด (เกินตัดเก่าทิ้ง)
DEFAULT_LOG_TAIL = 60         # เมนูดู log แสดงล่าสุดกี่บรรทัด
MAX_JSON_BODY = 32 * 1024 * 1024
STATIC_FILES = {"index.html", "admin.html", "app.js", "admin.js", "style.css"}

DISCORD_KEY_HOURS = 24          # คีย์จากบอท /getkey ใช้ได้ 24 ชม.
PERMANENT_THRESHOLD = 10000     # สร้างระบุชั่วโมง >= 10000 = ถาวร
KEY_LENGTH = 4                  # คีย์สุ่ม 4 ตัวอักษร

_lock = threading.RLock()   # RLock = ซ้อน lock ได้ (กัน deadlock ตอน log/save ซ้อนกัน)
_log_since_menu = {"n": 0}  # นับจำนวน log ที่เกิดขึ้นตั้งแต่ครั้งล่าสุดที่ดู (แจ้งเตือนที่เมนูหลัก)

# ========== COLORS ==========
class C:
    G = "\033[92m"; B = "\033[94m"; Y = "\033[93m"; R = "\033[91m"
    M = "\033[95m"; C = "\033[96m"; BOLD = "\033[1m"; END = "\033[0m"

# ========== UTILS ==========
def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")

def wait_enter(text="\nPress Enter to return to Main Menu"):
    input(f"{C.Y}{text}{C.END}")

def get_local_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ========== LOG + ATOMIC WRITE (เสถียรภาพ) ==========
def log(msg):
    """เก็บ log ลงไฟล์ + ตัดเก่าเมื่อไฟล์ใหญ่เกินไป (thread-safe)"""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with _lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if LOG_FILE.stat().st_size > 512 * 1024:  # > 512KB → เก็บแค่ท้าย MAX_LOG_LINES
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > MAX_LOG_LINES:
                    with open(LOG_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines[-MAX_LOG_LINES:])
    except Exception:
        pass
    # ไม่ print ลงจอหลัก เพราะจะซ้อนทับเมนู (เห็นในรูปที่ log HTTP พิมพ์กระจาย)
    # log ทุกบรรทัดถูกเก็บลงไฟล์ logs/server.log ดูได้ที่เมนู 08 เท่านั้น
    _log_since_menu["n"] += 1

def write_json_atomic(path, obj):
    """เขียนไฟล์แบบ Atomic: เขียนไฟล์ชั่วคราวแล้วค่อย replace → กันไฟล์เสียตอนดับ/ค้างกลางคัน"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

# ========== CONFIG (Discord token + admin) ==========
def default_config():
    return {"discord_token": "", "guild_id": "", "admin_ids": [], "tunnel_protocol": "auto", "tunnel_extra_args": ""}

def load_config():
    default = default_config()
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return default
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in default.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return default

def save_config(cfg):
    write_json_atomic(CONFIG_FILE, cfg)

def is_admin(discord_id):
    """admin_ids ว่าง = อนุญาตทุกคน (ระบบส่วนตัว); มีค่า = เฉพาะ ID เหล่านั้น"""
    ids = [str(x) for x in load_config().get("admin_ids", []) if x]
    if not ids:
        return True
    return str(discord_id) in ids

# ========== KEYS ==========
def normalize_key(k):
    return re.sub(r"[^A-Za-z0-9]", "", str(k or "")).upper()

def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    existing = {normalize_key(e.get("key")) for e in load_keys()}
    while True:
        raw = "".join(secrets.choice(alphabet) for _ in range(KEY_LENGTH))
        if raw not in existing:
            if KEY_LENGTH <= 4:
                return raw  # สั้นๆ ไม่ใส่ขีด
            return f"{raw[:3]}-{raw[3:]}"
def format_custom_key(raw):
    """จัดรูปแบบคีย์ที่ผู้ใช้พิมพ์เอง → คืน (display, normalized) หรือ (None, None) ถ้าไม่ถูกต้อง/ซ้ำ"""
    n = normalize_key(raw)
    if len(n) < 1 or len(n) > 100:
        return None, None  # ยาว 1-100 ตัวอักษร
    existing = {normalize_key(e.get("key")) for e in load_keys()}
    if n in existing:
        return None, "duplicate"
    # จัดรูปแบบแสดง: สั้นๆ (<=4 ตัว) ไม่ใส่ขีด, ยาวกว่าใส่ขีดทุก 3 ตัว
    if len(n) <= 4:
        display = n
    else:
        chunks = [n[i:i+3] for i in range(0, len(n), 3)]
        display = "-".join(chunks)
    return display, n

def load_keys():
    if not KEYS_FILE.exists():
        return []
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_keys(keys):
    with _lock:
        if KEYS_FILE.exists():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(KEYS_FILE, BACKUP / f"keys-{ts}.json")
            all_bak = sorted(BACKUP.glob("keys-*.json"))
            if len(all_bak) > 5:
                all_bak[0].unlink(missing_ok=True)
        write_json_atomic(KEYS_FILE, keys)

def is_expired(entry, now=None):
    now = now or datetime.now()
    if entry.get("permanent") or not entry.get("expires_at"):
        return False
    try:
        return datetime.fromisoformat(entry["expires_at"]) <= now
    except Exception:
        return True

def find_key(raw):
    n = normalize_key(raw)
    if not n:
        return None
    now = datetime.now()
    for e in load_keys():
        if normalize_key(e.get("key")) == n and not is_expired(e, now):
            return e
    return None

def clean_expired_keys():
    now = datetime.now()
    keys = load_keys()
    kept = [e for e in keys if not is_expired(e, now)]
    removed = len(keys) - len(kept)
    save_keys(kept)
    return removed

def create_key(hours, created_by="termux", discord_id=None, discord_name=None, note="", custom_key=None):
    if custom_key:
        display, n = format_custom_key(custom_key)
        if not display:
            return None  # คีย์ไม่ถูกต้องหรือซ้ำ
        normalized = n
    else:
        display = generate_key()
        normalized = normalize_key(display)
    now = datetime.now()
    permanent = hours >= PERMANENT_THRESHOLD
    entry = {
        "key": normalized,
        "display": display,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": None if permanent else (now + timedelta(hours=hours)).isoformat(timespec="seconds"),
        "permanent": permanent,
        "hours": None if permanent else hours,
        "created_by": created_by,
        "discord_id": discord_id,
        "discord_name": discord_name,
        "note": note,
    }
    with _lock:
        keys = load_keys()
        keys.append(entry)
        save_keys(keys)
    log(f"สร้างคีย์ {entry['display']} | {hours} ชม.{' (ถาวร)' if permanent else ''} | โดย {created_by} {discord_name or note or ''}")
    return entry

def delete_key(raw):
    n = normalize_key(raw)
    with _lock:
        keys = load_keys()
        before = len(keys)
        keys = [e for e in keys if normalize_key(e.get("key")) != n]
        save_keys(keys)
    removed = before - len(keys)
    log(f"ลบคีย์ {n} → {removed} รายการ")
    return removed

# ========== WORKSPACE DATA (แยกตามคีย์) ==========
# รูปแบบ: {currentDate, rounds: {date: {billNo, customer, items}}}
def ws_path(ws_id):
    return WS_DIR / f"{normalize_key(ws_id)}.json"

def default_data():
    return {"currentDate": datetime.now().strftime("%Y-%m-%d"), "rounds": {}}

def load_data(ws_id):
    p = ws_path(ws_id)
    if not p.exists():
        return default_data()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"workspace data is not an object: {p.name}")
        if "rounds" not in data:
            data["rounds"] = {}
        if not isinstance(data["rounds"], dict):
            raise ValueError(f"workspace rounds are invalid: {p.name}")
        if "currentDate" not in data:
            data["currentDate"] = datetime.now().strftime("%Y-%m-%d")
        return data
    except json.JSONDecodeError:
        backups = sorted(BACKUP.glob(f"ws-{normalize_key(ws_id)}-*.json"), reverse=True)
        if backups:
            try:
                with open(backups[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("rounds", {}), dict):
                    raise ValueError(f"workspace backup has invalid format: {backups[0].name}")
                return data
            except (OSError, json.JSONDecodeError) as e:
                raise ValueError(f"workspace and backup are unreadable: {p.name}") from e
        raise ValueError(f"workspace data is corrupt and has no backup: {p.name}")

def save_data(ws_id, data):
    p = ws_path(ws_id)
    with _lock:
        if p.exists():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(p, BACKUP / f"ws-{normalize_key(ws_id)}-{ts}.json")
            all_bak = sorted(BACKUP.glob("ws-*.json"))
            if len(all_bak) > 15:
                for b in all_bak[:len(all_bak)-15]:
                    b.unlink(missing_ok=True)
        write_json_atomic(p, data)

# ========== SERVER STATE ==========
server_thread = None
server_instance = None
tunnel_proc = None
tunnel_url = None       # เก็บลิงค์สาธารณะของ cloudflared เพื่อแสดงที่เมนูหลัก
server_running = False
bot_running = False


def folder_size_mb(path):
    """คำนวณขนาดโฟลเดอร์ทั้งหมด (MB)"""
    total = 0
    try:
        for p in Path(path).rglob("*"):
            if p.is_file():
                try: total += p.stat().st_size
                except Exception: pass
    except Exception:
        pass
    return total / (1024 * 1024)

def find_cloudflared():
    """หาไฟล์ cloudflared ในหลายตำแหน่ง คืน path หรือ None"""
    import shutil
    candidates = [
        ROOT / "cloudflared",
        Path("/data/data/com.termux/files/usr/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
        Path("/usr/bin/cloudflared"),
        Path.home() / "cloudflared",
    ]
    for c in candidates:
        try:
            if c.exists() and os.access(str(c), os.X_OK):
                return str(c)
        except Exception:
            pass
    w = shutil.which("cloudflared")
    if w:
        return w
    return None

# ========== WEB SERVER ==========
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_content_length"})
            return None
        if length < 1:
            self._send_json(400, {"error": "empty_request_body"})
            return None
        if length > MAX_JSON_BODY:
            self._send_json(413, {"error": "request_body_too_large"})
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, {"error": "invalid_json"})
            return None
        if not isinstance(data, dict):
            self._send_json(400, {"error": "json_object_required"})
            return None
        if "rounds" in data and not isinstance(data["rounds"], dict):
            self._send_json(400, {"error": "rounds_must_be_an_object"})
            return None
        if "items" in data and not isinstance(data["items"], list):
            self._send_json(400, {"error": "items_must_be_an_array"})
            return None
        for round_data in data.get("rounds", {}).values():
            if not isinstance(round_data, dict) or not isinstance(round_data.get("items", []), list):
                self._send_json(400, {"error": "invalid_round_data"})
                return None
            if any(not self._valid_item(item) for item in round_data.get("items", [])):
                self._send_json(400, {"error": "invalid_item_data"})
                return None
        if any(not self._valid_item(item) for item in data.get("items", [])):
            self._send_json(400, {"error": "invalid_item_data"})
            return None
        return data

    @staticmethod
    def _valid_item(item):
        if not isinstance(item, dict) or not isinstance(item.get("number"), str):
            return False
        amounts = item.get("amounts", [])
        return (
            isinstance(amounts, list)
            and all(
                (isinstance(amount, int) or isinstance(amount, float) and math.isfinite(amount))
                and not isinstance(amount, bool)
                for amount in amounts
            )
        )

    def _serve_static(self, head=False):
        requested = unquote(urlsplit(self.path).path)
        name = "index.html" if requested == "/" else requested.lstrip("/")
        if name not in STATIC_FILES or "/" in name or "\\" in name:
            self.send_error(404)
            return
        self.path = "/" + name
        if head:
            super().do_HEAD()
        else:
            super().do_GET()

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ws(self):
        """คืน (entry, ws_id) หรือ (None, None) ถ้าคีย์ไม่ถูกต้อง"""
        key = self.headers.get("X-Access-Key", "") or ""
        entry = find_key(key)
        if not entry:
            log(f"403 คีย์ไม่ถูกต้อง/หมดอายุ path={self.path} ip={self.client_address[0]}")
            return None, None
        return entry, normalize_key(key)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/verify":
            key = parse_qs(urlsplit(self.path).query).get("key", [""])[0]
            entry = find_key(key)
            if entry:
                self._send_json(200, {
                    "ok": True,
                    "permanent": bool(entry.get("permanent")),
                    "expires_at": entry.get("expires_at"),
                    "created_by": entry.get("created_by", ""),
                })
            else:
                self._send_json(200, {"ok": False, "message": "คีย์ไม่ถูกต้องหรือหมดอายุ"})
            return
        if path == "/api/status":
            entry, ws_id = self._auth_ws()
            if not entry:
                self._send_json(403, {"error": "invalid_or_expired_key"}); return
            with _lock:
                try:
                    data = load_data(ws_id)
                    keys = load_keys()
                except (OSError, ValueError) as e:
                    log(f"Failed to load workspace {ws_id}: {e}")
                    self._send_json(500, {"error": "data_unavailable"})
                    return
            now = datetime.now()
            active = [k for k in keys if not is_expired(k, now)]
            self._send_json(200, {
                "server_time": now.isoformat(timespec="seconds"),
                "total_keys": len(keys),
                "active_keys": len(active),
                "dates_stored": len(data.get("rounds", {})),
                "workspace": ws_id,
            })
            return
        if path == "/api/data":
            entry, ws_id = self._auth_ws()
            if not entry:
                self._send_json(403, {"error": "invalid_or_expired_key"}); return
            with _lock:
                try:
                    data = load_data(ws_id)
                except (OSError, ValueError) as e:
                    log(f"Failed to load workspace {ws_id}: {e}")
                    self._send_json(500, {"error": "data_unavailable"})
                    return
            self._send_json(200, data)
            return
        self._serve_static()

    def do_HEAD(self):
        if urlsplit(self.path).path.startswith("/api/"):
            self.send_error(404)
            return
        self._serve_static(head=True)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/data":
            entry, ws_id = self._auth_ws()
            if not entry:
                self._send_json(403, {"error": "invalid_or_expired_key"}); return
            data = self._read_json_body()
            if data is None:
                return
            try:
                save_data(ws_id, data)
                self._send_json(200, {"ok": True})
            except (OSError, TypeError, ValueError) as e:
                log(f"Failed to save workspace {ws_id}: {e}")
                self._send_json(500, {"error": "save_failed"})
            return
        if path == "/api/delete_date":
            entry, ws_id = self._auth_ws()
            if not entry:
                self._send_json(403, {"error": "invalid_or_expired_key"}); return
            body = self._read_json_body()
            if body is None:
                return
            date = body.get("date")
            if not isinstance(date, str):
                self._send_json(400, {"error": "invalid_date"})
                return
            date = date.strip()
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                self._send_json(400, {"error": "invalid_date"})
                return
            try:
                deleted = False
                with _lock:
                    data = load_data(ws_id)
                    if date in data.get("rounds", {}):
                        del data["rounds"][date]
                        save_data(ws_id, data)
                        deleted = True
                if deleted:
                    log(f"ลบงวด {date} ใน workspace {ws_id}")
                self._send_json(200, {"ok": True, "deleted": deleted})
            except (OSError, TypeError, ValueError) as e:
                log(f"Failed to delete date {date} in workspace {ws_id}: {e}")
                self._send_json(500, {"error": "delete_failed"})
            return
        self.send_error(404)

    def log_message(self, fmt, *args):
        try:
            ip = self.client_address[0]
            req = str(args[0]) if args else ""
            code = str(args[1]) if len(args) > 1 else "?"
            parts = req.split()
            method = parts[0] if parts else "?"
            path = urlsplit(parts[1]).path if len(parts) > 1 else "?"
            friendly = {
                "/index.html": "เปิดหน้าบันทึกเลข",
                "/admin.html": "เปิดหน้าแดชบอร์ด (admin)",
                "/app.js": "โหลดไฟล์หน้าบันทึก",
                "/admin.js": "โหลดไฟล์แดชบอร์ด",
                "/style.css": "โหลดสไตล์เว็บ",
                "/api/data": "บันทึก/ดึงข้อมูลสลาก",
                "/api/verify": "ตรวจสอบคีย์",
            }.get(path, f"เรียก {path}")
            log(f"[{ip}] {method} {friendly} → สถานะ {code}")
        except Exception:
            log(f"HTTP request: {args}")

def _run_server_background():
    global server_instance, server_running
    try:
        server_instance.serve_forever()
    except Exception as e:
        log(f"Web server stopped unexpectedly: {e}")
    server_running = False

def start_server(with_tunnel=False, auto=False):
    global server_thread, server_instance, server_running, tunnel_proc, tunnel_url
    if server_running:
        if not auto:
            print(f"{C.Y}⚠️  Server is already running!{C.END}")
            wait_enter()
        return
    if not auto:
        clear_screen()
        print(f"\n{'='*60}")
        print(f"{C.G}{C.BOLD}✅ SERVER STARTED — Running in background{C.END}")
        print("="*60)
        print(f"{C.C}📍 Local:      http://127.0.0.1:{PORT}{C.END}")
        print(f"{C.C}🏠 LAN:        http://{get_local_ip()}:{PORT}{C.END}")
        print(f"{C.C}💾 Workspaces: {WS_DIR}/ ({len(list(WS_DIR.glob('*.json')))} ชุด){C.END}")
        print(f"{C.C}🔑 Keys:       {len(load_keys())} รายการ{C.END}")
    else:
        print(f"{C.G}✅ เปิดเว็บเซิร์ฟเวอร์อัตโนมัติ:  http://{get_local_ip()}:{PORT}{C.END}")

    tunnel_proc = None
    if with_tunnel:
        cf_path = find_cloudflared()
        if not cf_path:
            print(f"{C.R}❌ ไม่พบ cloudflared — ไม่สามารถเปิด Public URL ได้{C.END}")
            print(f"{C.Y}วิธีติดตั้ง (Termux):{C.END}")
            print("  pkg install wget -y")
            print("  wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm -O cloudflared")
            print("  chmod +x cloudflared")
            print("  (วางไฟล์ cloudflared ในโฟลเดอร์เดียวกับ server.py แล้วรันใหม่)")
            print(f"{C.Y}หรือถ้าเป็น arm64 ให้ดาวน์โหลด cloudflared-linux-arm64 แทน{C.END}")
            print("-"*60)
        else:
            print(f"{C.C}🔌 กำลังเปิด Public Tunnel ผ่าน cloudflared...{C.END}")
            print(f"{C.Y}   รอสักครู่ จะแสดงลิงค์ https://...trycloudflare.com ให้{C.END}")
            url_re = re.compile(r"https://[a-zA-Z0-9.-]+\.(trycloudflare\.com|argotunnel\.com|cfargotunnel\.com)")
            def parse_tunnel_line(line):
                try:
                    log_obj = json.loads(line)
                    if isinstance(log_obj, dict) and "message" in log_obj:
                        line = str(log_obj["message"])
                except Exception:
                    pass
                m = url_re.search(line)
                if m and "api." not in m.group(0):
                    return f"{C.G}{C.BOLD}🌐 PUBLIC URL: {m.group(0)}{C.END}"
                return None
            cfg = load_config()
            proto = (cfg.get("tunnel_protocol") or "auto").strip().lower()
            extra = (cfg.get("tunnel_extra_args") or "").strip()
            print(f"{C.C}⚙️  การตั้งค่า cloudflared: protocol={proto} | extra={extra or '(ไม่มี)'}{C.END}")
            if input("เปลี่ยนการตั้งค่า cloudflared? (y/N): ").strip().lower() == "y":
                print("  protocol แนะนำบนมือถือ: tcp (เสถียรกว่า) | auto=http2 | quic")
                p = input("  protocol (auto/tcp/http2/quic, กด Enter = auto): ").strip().lower()
                proto = p if p in ("tcp", "http2", "quic") else "auto"
                e = input("  พารามิเตอร์เพิ่มเติม (กด Enter = ไม่มี): ").strip()
                extra = e
                cfg["tunnel_protocol"] = proto; cfg["tunnel_extra_args"] = extra; save_config(cfg)
                print(f"{C.G}✅ บันทึกการตั้งค่าแล้ว{C.END}")
            import shlex
            cmd = [cf_path, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--loglevel", "info", "--output", "json"]
            if proto != "auto":
                cmd += ["--protocol", proto]
            if extra:
                cmd += shlex.split(extra)
            tunnel_url = None  # รีเซ็ต รอจับลิงค์ใหม่
            tunnel_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            def tunnel_reader():
                global tunnel_url
                for line in iter(tunnel_proc.stdout.readline, ""):
                    line = line.strip()
                    if not line:
                        continue
                    msg = parse_tunnel_line(line)
                    if msg:
                        print(f"   {msg}")
                        m2 = url_re.search(line)
                        if m2 and "api." not in m2.group(0):
                            tunnel_url = m2.group(0)
                            log(f"Tunnel public URL: {tunnel_url}")
            threading.Thread(target=tunnel_reader, daemon=True).start()

    if not auto:
        print(f"{C.Y}💡 Server runs in background — you can use other menus now{C.END}")
        print(f"{C.R}⏹ Select 'Stop Server' from menu to shut down{C.END}")
        print("="*60)
    try:
        server_instance = ThreadingHTTPServer((BIND_IP, PORT), Handler)
    except OSError as e:
        if tunnel_proc:
            tunnel_proc.terminate()
            tunnel_proc = None
        server_running = False
        print(f"{C.R}❌ เปิดเว็บเซิร์ฟเวอร์ไม่ได้: {e}{C.END}")
        log(f"Web server failed to bind {BIND_IP}:{PORT}: {e}")
        if not auto:
            wait_enter()
        return
    server_running = True
    server_thread = threading.Thread(target=_run_server_background, daemon=True)
    server_thread.start()
    log(f"Web server started on port {PORT} (tunnel={'on' if with_tunnel else 'off'})")
    if not auto:
        wait_enter()

def stop_server():
    global server_instance, server_running, tunnel_proc, tunnel_url
    if not server_running:
        print(f"{C.Y}⚠️  Server is not running{C.END}")
        wait_enter(); return
    clear_screen()
    print(f"{C.Y}⏹ Stopping server...{C.END}")
    if server_instance:
        server_instance.shutdown()
        server_instance.server_close()
    if tunnel_proc:
        tunnel_proc.terminate()
        try:
            tunnel_proc.wait(timeout=5)
        except Exception:
            tunnel_proc.kill()
    server_running = False
    tunnel_proc = None
    tunnel_url = None
    log("Web server stopped")
    print(f"{C.G}✅ Server stopped successfully{C.END}")
    wait_enter()

# ========== DISCORD BOT ==========
def bot_worker(token, guild_id):
    global bot_running
    try:
        import asyncio
        import discord
        from discord import app_commands
    except ImportError:
        print(f"{C.R}❌ discord.py not installed. Run:  pip install discord.py{C.END}")
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    def fmt_exp(entry):
        if entry.get("permanent") or not entry.get("expires_at"):
            return "ถาวร"
        try:
            return datetime.fromisoformat(entry["expires_at"]).strftime("%Y-%m-%d %H:%M") + " น."
        except Exception:
            return "?"

    @tree.command(name="getkey", description="ขอคีย์เข้าระบบ (ใช้ได้ 24 ชั่วโมง)")
    async def getkey(interaction):
        uid = str(interaction.user.id); uname = str(interaction.user)
        now = datetime.now()
        with _lock:
            keys = load_keys()
            found = None
            for e in keys:
                if e.get("discord_id") == uid and not is_expired(e, now):
                    found = e; break
            if not found:
                display = generate_key()
                exp = now + timedelta(hours=DISCORD_KEY_HOURS)
                found = {
                    "key": normalize_key(display), "display": display,
                    "created_at": now.isoformat(timespec="seconds"),
                    "expires_at": exp.isoformat(timespec="seconds"),
                    "permanent": False, "hours": DISCORD_KEY_HOURS,
                    "created_by": "discord", "discord_id": uid,
                    "discord_name": uname, "note": "",
                }
                keys.append(found); save_keys(keys)
        log(f"/getkey จาก {uname} (id {uid}) → คีย์ {found['display']}")
        await interaction.response.send_message(
            f"🔑 **คีย์ของคุณ:** `{found['display']}`\n"
            f"⏰ หมดอายุ: **{fmt_exp(found)}** (ใช้ได้ {DISCORD_KEY_HOURS} ชั่วโมง)\n"
            f"💡 คีย์นี้เป็น Workspace ส่วนตัวของคุณ ใส่ที่หน้าเว็บเพื่อเข้าระบบ",
            ephemeral=True,
        )

    key_group = app_commands.Group(name="key", description="จัดการคีย์ (ดู/เพิ่ม/ลบ)")

    @key_group.command(name="me", description="ดูคีย์ปัจจุบันของตัวเอง")
    async def key_me(interaction):
        uid = str(interaction.user.id); now = datetime.now()
        mine = [e for e in load_keys() if e.get("discord_id") == uid and not is_expired(e, now)]
        if not mine:
            await interaction.response.send_message("คุณยังไม่มีคีย์ที่ใช้งานได้ พิมพ์ `/getkey` เพื่อขอคีย์", ephemeral=True); return
        lines = [f"`{e['display']}`  หมด: {fmt_exp(e)}" for e in mine]
        await interaction.response.send_message("🔑 **คีย์ของคุณ:**\n" + "\n".join(lines), ephemeral=True)

    @key_group.command(name="list", description="ดูคีย์ทั้งหมดในระบบ")
    async def key_list(interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("⛔ เฉพาะแอดมินเท่านั้น", ephemeral=True); return
        keys = load_keys(); now = datetime.now()
        if not keys:
            await interaction.response.send_message("(ยังไม่มีคีย์ในระบบ)", ephemeral=True); return
        lines = []
        for e in keys:
            status = "✅" if not is_expired(e, now) else "⏰หมด"
            who = e.get("discord_name") or e.get("note") or e.get("created_by") or "-"
            lines.append(f"`{e['display']}` {status} หมด: {fmt_exp(e)} | {who}")
        msg = "📋 **รายการคีย์ทั้งหมด:**\n" + "\n".join(lines[:30])
        if len(lines) > 30:
            msg += f"\n... (อีก {len(lines)-30} รายการ ดูใน Termux เมนู 06)"
        await interaction.response.send_message(msg, ephemeral=True)

    @key_group.command(name="add", description="สร้างคีย์ใหม่ (ระบุชั่วโมง, >=10000 = ถาวร)")
    @app_commands.describe(hours="จำนวนชั่วโมงที่ใช้ได้ (>=10000 = ถาวร)", note="หมายเหตุ/ชื่อเจ้าของ")
    async def key_add(interaction, hours: int, note: str = ""):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("⛔ เฉพาะแอดมินเท่านั้น", ephemeral=True); return
        if hours < 1:
            await interaction.response.send_message("ชั่วโมงต้อง >= 1", ephemeral=True); return
        entry = create_key(hours, created_by=f"discord:{interaction.user}", note=note)
        log(f"/key add จาก {interaction.user} → {entry['display']} ({hours} ชม.) note={note}")
        await interaction.response.send_message(
            f"✅ สร้างคีย์สำเร็จ: `{entry['display']}`\nหมดอายุ: **{fmt_exp(entry)}**\n(แชร์คีย์นี้ให้คนอื่น = เข้าร่วม Workspace เดียวกัน)",
            ephemeral=True,
        )

    @key_group.command(name="delete", description="ลบคีย์")
    @app_commands.describe(key="คีย์ที่จะลบ (เช่น ABC-123)")
    async def key_delete(interaction, key: str):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("⛔ เฉพาะแอดมินเท่านั้น", ephemeral=True); return
        removed = delete_key(key)
        log(f"/key delete จาก {interaction.user} → {key} removed={removed}")
        if removed:
            await interaction.response.send_message(f"🗑 ลบคีย์ `{normalize_key(key)}` แล้ว", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ ไม่พบคีย์ `{key}`", ephemeral=True)

    tree.add_command(key_group)

    @client.event
    async def on_ready():
        global bot_running
        try:
            if guild_id:
                tree.copy_global_to(guild=discord.Object(id=int(guild_id)))
                await tree.sync(guild=discord.Object(id=int(guild_id)))
            else:
                await tree.sync()
        except Exception as e:
            print(f"{C.R}⚠️  slash sync failed: {e}{C.END}")
        bot_running = True
        log(f"Discord bot online: @{client.user} | คำสั่ง: /getkey, /key list/add/delete/me")

    try:
        loop.run_until_complete(client.start(token))
    except Exception as e:
        log(f"Discord bot error: {e}")
    finally:
        bot_running = False

def start_bot_if_configured():
    global bot_running
    if bot_running:
        return
    cfg = load_config()
    token = (cfg.get("discord_token") or "").strip()
    if not token:
        print(f"{C.Y}🤖 Discord bot disabled (no token in config.json){C.END}")
        return
    guild_id = (cfg.get("guild_id") or "").strip() or None
    threading.Thread(target=bot_worker, args=(token, guild_id), daemon=True).start()
    print(f"{C.C}🤖 Discord bot starting... (slash: /getkey, /key ...){C.END}")

# ========== MENU PAGES ==========
def fmt_expiry(entry):
    if entry.get("permanent") or not entry.get("expires_at"):
        return f"{C.M}ถาวร{C.END}"
    try:
        d = datetime.fromisoformat(entry["expires_at"])
        hrs = (d - datetime.now()).total_seconds() / 3600
        rem = f"{hrs:.1f} ชม." if hrs > 0 else "หมดอายุ"
        return f"{d.strftime('%Y-%m-%d %H:%M')} น. (เหลือ {rem})"
    except Exception:
        return "?"

def page_system_config():
    clear_screen()
    keys = load_keys()
    active = [k for k in keys if not is_expired(k)]
    ws_count = len(list(WS_DIR.glob("*.json")))
    print(f"\n{C.BOLD}{C.G}📋 SYSTEM CONFIGURATION{C.END}")
    print("="*60)
    print(f"Workspaces:      {ws_count} ชุดข้อมูล (แยกตามคีย์)")
    print(f"Keys (total):    {len(keys)}  |  ใช้งานได้: {len(active)}")
    print(f"Backup Folder:   {BACKUP}/")
    print(f"Server Port:     {PORT}")
    if tunnel_url:
        print(f"🌐 Public URL:    {C.G}{tunnel_url}{C.END}")
    elif tunnel_proc:
        print(f"🌐 Public URL:    {C.Y}กำลังเชื่อมต่อ cloudflared...{C.END}")
    else:
        print(f"🌐 Public URL:    {C.R}ยังไม่เปิด (ใช้เมนู 04){C.END}")
    print(f"Discord bot:     {C.G}● Online{C.END}" if bot_running else f"Discord bot:     {C.R}○ Offline{C.END}")
    print(f"Web Server:      {C.G}● Running{C.END}" if server_running else f"Web Server:      {C.R}○ Stopped{C.END}")
    print("="*60)
    wait_enter()

def format_amount(n):
    return f"{float(n):,.2f}"

def page_view_data():
    clear_screen()
    print(f"\n{C.BOLD}{C.C}📊 VIEW RECORDS BY WORKSPACE + DATE{C.END}")
    print("="*60)
    keys = load_keys()
    if not keys:
        print(f"{C.Y}ยังไม่มีคีย์ในระบบ สร้างก่อนที่เมนู 06{C.END}"); wait_enter(); return
    print("เลือก Workspace (คีย์):")
    for i, e in enumerate(keys, 1):
        who = e.get("discord_name") or e.get("note") or e.get("created_by") or "-"
        print(f"  [{i:>2}] {e.get('display')}  ({who})")
    sel = input("ใส่เลขลำดับ: ").strip()
    try:
        idx = int(sel) - 1
        if not (0 <= idx < len(keys)):
            raise ValueError
    except Exception:
        print(f"{C.R}❌ เลือกไม่ถูกต้อง{C.END}"); wait_enter(); return
    ws_id = normalize_key(keys[idx].get("key"))
    date_input = input(f"วันที่ (YYYY-MM-DD) หรือกด Enter = วันนี้: ").strip()
    if not date_input:
        date_input = datetime.now().strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_input):
        print(f"{C.R}❌ Invalid format! Use YYYY-MM-DD{C.END}"); wait_enter(); return
    data = load_data(ws_id)
    round_data = data.get("rounds", {}).get(date_input, {})
    items = round_data.get("items", [])
    print(f"\n{C.BOLD}📅 Workspace {keys[idx].get('display')} | {date_input} | บิล: {round_data.get('billNo','-')} | ลูกค้า: {round_data.get('customer','-')}{C.END}")
    print("-"*60)
    if not items:
        print(f"{C.Y}No records found.{C.END}")
    else:
        grouped = {}
        for it in items:
            num = it.get("number", "")
            if num not in grouped:
                grouped[num] = {"amounts": [0]*5, "total": 0}
            g = grouped[num]; amts = it.get("amounts", [0]*5)
            for i in range(5):
                g["amounts"][i] += amts[i] if i < len(amts) else 0
            g["total"] += sum(amts)
        grand = 0
        print(f"{'Number':<8} {'2Top':>10} {'2Bot':>10} {'3Straight':>10} {'3Combo':>10} {'3Bot':>10} {'Total':>12}")
        print("-"*60)
        for num in sorted(grouped.keys()):
            a = grouped[num]["amounts"]; t = grouped[num]["total"]; grand += t
            print(f"{num:<8} {format_amount(a[0]):>10} {format_amount(a[1]):>10} {format_amount(a[2]):>10} {format_amount(a[3]):>10} {format_amount(a[4]):>10} {C.BOLD}{format_amount(t):>12}{C.END}")
        print("-"*60)
        print(f"{'GRAND TOTAL':<58} {C.BOLD}{format_amount(grand):>12}{C.END}")
    wait_enter()

def page_key_manager():
    while True:
        clear_screen()
        keys = load_keys()
        active = [k for k in keys if not is_expired(k)]
        print(f"\n{C.BOLD}{C.Y}🔑 KEY MANAGER{C.END}   (ทั้งหมด {len(keys)} | ใช้งานได้ {len(active)})")
        print("="*60)
        print(f"{C.C} 01.{C.END} ดูรายการคีย์ทั้งหมด (List)")
        print(f"{C.C} 02.{C.END} สร้างคีย์ใหม่ (Generate) — ตั้งชื่อคีย์เอง หรือสุ่ม | ระบุชั่วโมง 1-inf (>=10000 = ถาวร)")
        print(f"{C.C} 03.{C.END} ลบคีย์ (Delete)")
        print(f"{C.C} 04.{C.END} ล้างคีย์ที่หมดอายุ (Clean expired)")
        print(f"{C.C} 00.{C.END} กลับเมนูหลัก")
        print("="*60)
        choice = input(f"\n{C.B}Enter option: {C.END}").strip()
        if choice in ("1", "01"):
            clear_screen()
            print(f"\n{C.BOLD}📃 KEY LIST{C.END}")
            print("-"*60)
            if not keys:
                print("(ยังไม่มีคีย์)")
            for i, e in enumerate(keys, 1):
                who = e.get("discord_name") or e.get("note") or e.get("created_by") or "-"
                status = f"{C.G}ใช้งานได้{C.END}" if not is_expired(e) else f"{C.R}หมดอายุ{C.END}"
                print(f"[{i:>2}] {e.get('display')}  {status}  หมด: {fmt_expiry(e)}  โดย: {who}")
            print("-"*60)
            wait_enter()
        elif choice in ("2", "02"):
            clear_screen()
            print(f"\n{C.BOLD}➕ GENERATE NEW KEY{C.END}")
            print(f"  ตั้งคีย์เองได้ 1-100 ตัว (A-Z 0-9) หรือกด Enter = สุ่ม 4 ตัว")
            print(f"  ระบุจำนวนชั่วโมง 1-inf | >= {PERMANENT_THRESHOLD} ชม. = ถาวร")
            print("-"*60)
            custom_raw = input("ตั้งคีย์เอง (พิมพ์ได้ เช่น FAM01) หรือกด Enter = สุ่ม: ").strip()
            custom_display = None
            if custom_raw:
                custom_display, n = format_custom_key(custom_raw)
                while not custom_display:
                    if n == "duplicate":
                        print(f"{C.R}❌ คีย์นี้มีอยู่แล้วในระบบ กรุณาใช้ชื่ออื่น{C.END}")
                    else:
                        print(f"{C.R}❌ คีย์ต้องยาว 1-100 ตัวอักษร (A-Z 0-9){C.END}")
                    custom_raw = input("ตั้งคีย์เองอีกครั้ง หรือกด Enter = สุ่ม: ").strip()
                    if not custom_raw:
                        break
                    custom_display, n = format_custom_key(custom_raw)
                if custom_display:
                    print(f"{C.G}✓ ใช้คีย์: {custom_display}{C.END}")
                else:
                    print(f"{C.C}→ จะสุ่มคีย์ให้อัตโนมัติ{C.END}")
            raw = input("จำนวนชั่วโมง: ").strip()
            try:
                hours = int(float(raw))
                if hours < 1: raise ValueError
            except Exception:
                print(f"{C.R}❌ กรุณาใส่ตัวเลข >= 1{C.END}"); wait_enter(); continue
            note = input("ชื่อเจ้าของ/หมายเหตุ (เว้นว่างได้): ").strip()
            entry = create_key(hours, created_by="termux", note=note, custom_key=(custom_display if custom_display else None))
            if entry is None:
                print(f"{C.R}❌ สร้างคีย์ไม่สำเร็จ (คีย์ซ้ำ){C.END}"); wait_enter(); continue
            print("\n" + "="*60)
            print(f"{C.G}{C.BOLD}🔑 สร้างคีย์สำเร็จ{C.END}")
            print(f"   คีย์:     {C.BOLD}{entry['display']}{C.END}")
            print(f"   หมดอายุ:  {fmt_expiry(entry)}")
            if note:
                print(f"   ชื่อ:     {note}")
            print(f"   💡 แชร์คีย์นี้ให้คนอื่น = เข้าร่วม Workspace เดียวกัน (ร่วมงานกันได้)")
            print("="*60)
            wait_enter()
        elif choice in ("3", "03"):
            clear_screen()
            keys = load_keys()
            if not keys:
                print(f"{C.Y}ไม่มีคีย์ในระบบ{C.END}"); wait_enter(); continue
            print(f"\n{C.BOLD}🗑 DELETE KEY{C.END}")
            print("-"*60)
            for i, e in enumerate(keys, 1):
                who = e.get("discord_name") or e.get("note") or e.get("created_by") or "-"
                print(f"[{i:>2}] {e.get('display')}  หมด: {fmt_expiry(e)}  โดย: {who}")
            print("-"*60)
            raw = input("ใส่เลขลำดับ หรือพิมพ์คีย์ตรงๆ: ").strip()
            if not raw:
                continue
            with _lock:
                keys = load_keys()
                if raw.isdigit() and 1 <= int(raw) <= len(keys):
                    del keys[int(raw)-1]; save_keys(keys); removed = 1
                else:
                    n = normalize_key(raw); before = len(keys)
                    keys = [e for e in keys if normalize_key(e.get("key")) != n]; save_keys(keys)
                    removed = before - len(keys)
            print(f"{C.G}✅ ลบแล้ว ({removed} รายการ){C.END}")
            wait_enter()
        elif choice in ("4", "04"):
            removed = clean_expired_keys()
            print(f"{C.G}✅ ล้างคีย์หมดอายุ {removed} รายการ{C.END}")
            wait_enter()
        elif choice in ("0", "00"):
            return
        else:
            print(f"{C.R}Invalid option!{C.END}"); time.sleep(1)

def page_bot_config():
    clear_screen()
    cfg = load_config()
    token = cfg.get("discord_token", "")
    masked = (token[:8] + "..." + token[-4:]) if len(token) > 12 else ("(ยังไม่ตั้งค่า)" if not token else token)
    admins = cfg.get("admin_ids", [])
    print(f"\n{C.BOLD}{C.M}🤖 DISCORD BOT SETUP{C.END}")
    print("="*60)
    print(f"Token:      {masked}")
    print(f"Guild ID:   {cfg.get('guild_id') or '(global sync, อาจช้าสักครู่)'}")
    print(f"Admin IDs:  {admins if admins else '(ว่าง = ทุกคนใช้ /key add/delete/list ได้)'}")
    print(f"Status:     {C.G}● Online{C.END}" if bot_running else f"Status:     {C.R}○ Offline{C.END}")
    print("-"*60)
    print("คำสั่งบอท: /getkey (ขอคีย์ส่วนตัว 24 ชม.)")
    print("            /key me (ดูคีย์ตัวเอง) | /key list | /key add <ชม.> | /key delete <คีย์>")
    print("="*60)
    ans = input("ต้องการตั้ง/เปลี่ยน Token? (y/N): ").strip().lower()
    if ans == "y":
        new_token = input("วาง Bot Token: ").strip()
        cfg["discord_token"] = new_token
        gid = input("Guild ID (เว้นว่าง = global): ").strip()
        cfg["guild_id"] = gid
        aids = input("Admin Discord IDs (คั่นด้วย , เว้นว่าง = อนุญาตทุกคน): ").strip()
        cfg["admin_ids"] = [x.strip() for x in aids.split(",") if x.strip()] if aids else []
        save_config(cfg)
        print(f"{C.G}✅ บันทึก config.json แล้ว{C.END}")
        if not bot_running:
            print(f"{C.C}🤖 กำลังเปิดบอททันที...{C.END}")
            start_bot_if_configured()
            time.sleep(2)
    wait_enter()

# ========== LOG VIEWER (เมนู 08) ==========
def _read_log_lines():
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return f.readlines()


def _show_log_all():
    """ดู log ทั้งหมดทั้งไฟล์"""
    lines = _read_log_lines()
    clear_screen()
    print(f"\n{C.BOLD}📜 LOG ทั้งหมด {len(lines)} บรรทัด{C.END}")
    print("-"*60)
    if not lines:
        print("(ยังไม่มี log)")
    else:
        print("".join(lines))
    wait_enter()

def _show_log_tail(n):
    lines = _read_log_lines()
    tail = lines[-n:] if lines else ["(ยังไม่มี log)\n"]
    clear_screen()
    print(f"\n{C.BOLD}📜 LOG ล่าสุด {len(tail)} บรรทัด{C.END}")
    print("-"*60)
    print("".join(tail))
    wait_enter()

def _show_log_filter(kw):
    lines = [l for l in _read_log_lines() if kw.lower() in l.lower()]
    clear_screen()
    print(f"\n{C.BOLD}🔍 ผลค้นหา: {kw}  ({len(lines)} บรรทัด){C.END}")
    print("-"*60)
    if not lines:
        print("(ไม่พบ)")
    else:
        print("".join(lines[-100:]))
        if len(lines) > 100:
            print(f"... (แสดงแค่ 100 บรรทัดล่าสุด จาก {len(lines)})")
    wait_enter()

def _show_log_range(start, count):
    """ดู log ตั้งแต่บรรทัดที่ start เป็นต้นไป จำนวน count บรรทัด"""
    lines = _read_log_lines()
    total = len(lines)
    if total == 0:
        clear_screen(); print("(ยังไม่มี log)"); wait_enter(); return
    start = max(1, min(start, total))
    end = min(start + count - 1, total)
    chunk = lines[start-1:end]
    clear_screen()
    print(f"\n{C.BOLD}📜 บรรทัดที่ {start}-{end} / ทั้งหมด {total} บรรทัด{C.END}")
    print("-"*60)
    for i, ln in enumerate(chunk, start):
        print(f"{i:>5}| {ln}", end="")
    if end < total:
        print(f"\n... (เหลืออีก {total-end} บรรทัด ใช้เมนู 4 ดูต่อ)")
    wait_enter()


def _show_log_realtime():
    """ดู log แบบ realtime เหมือน tail -f — กด Ctrl+C เพื่อกลับเมนู"""
    clear_screen()
    print(f"{C.BOLD}{C.C}📜 LOG REALTIME (กด Ctrl+C เพื่อกลับเมนู){C.END}")
    print("-"*60)
    pos = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    try:
        while True:
            if LOG_FILE.exists():
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    new = f.read()
                    pos = f.tell()
                    if new:
                        print(new, end="", flush=True)
            time.sleep(1.5)
    except KeyboardInterrupt:
        print(f"\n{C.Y}⏹ หยุดดู realtime แล้ว{C.END}")
    wait_enter()

def page_logs():
    _log_since_menu["n"] = 0  # เข้ามาดูแล้ว → รีเซ็ตตัวนับที่เมนูหลัก
    while True:
        clear_screen()
        total = len(_read_log_lines())
        print(f"\n{C.BOLD}{C.C}📜 LOG VIEWER (ดูประวัติการใช้งาน){C.END}   มีทั้งหมด {total} บรรทัด")
        print("="*60)
        print(f"{C.C} 01.{C.END} ดู log ทั้งหมด (ทั้งไฟล์)")
        print(f"{C.C} 02.{C.END} ดูล่าสุด N บรรทัด (กำหนดเอง)")
        print(f"{C.C} 03.{C.END} ค้นหา (เช่น 403 / getkey / สร้างคีย์ / ลบคีย์)")
        print(f"{C.C} 04.{C.END} ดูจากบรรทัดที่ระบุ (เลือกดูเอง)")
        print(f"{C.C} 05.{C.END} ดูแบบ realtime (สดๆ ทุก 1.5 วินาที) — กด Ctrl+C เพื่อหยุด")
        print(f"{C.C} 06.{C.END} ล้าง log ทั้งหมด")
        print(f"{C.C} 00.{C.END} กลับเมนูหลัก")
        print("="*60)
        choice = input(f"\n{C.B}Enter option: {C.END}").strip()
        if choice in ("1", "01"):
            _show_log_all()
        elif choice in ("2", "02"):
            try:
                n = int(input("จำนวนบรรทัด: ").strip())
                _show_log_tail(max(1, n))
            except Exception:
                print(f"{C.R}ตัวเลขไม่ถูกต้อง{C.END}"); time.sleep(1)
        elif choice in ("3", "03"):
            kw = input("คำที่ต้องการค้นหา: ").strip()
            if kw:
                _show_log_filter(kw)
            else:
                print(f"{C.Y}ไม่ได้ใส่คำค้น{C.END}"); time.sleep(1)
        elif choice in ("4", "04"):
            try:
                s = input("ดูจากบรรทัดที่ (กด Enter = 1): ").strip()
                start = int(s) if s else 1
                c = input("จำนวนบรรทัด (กด Enter = 60): ").strip()
                count = int(c) if c else 60
                _show_log_range(start, max(1, count))
            except Exception:
                print(f"{C.R}ตัวเลขไม่ถูกต้อง{C.END}"); time.sleep(1)
        elif choice in ("5", "05"):
            _show_log_realtime()
        elif choice in ("6", "06"):
            if input("ล้าง log ทั้งหมด? (y/N): ").strip().lower() == "y":
                LOG_FILE.write_text("", encoding="utf-8")
                log("ล้าง log แล้ว")
                print(f"{C.G}✅ ล้าง log แล้ว{C.END}"); time.sleep(1)
        elif choice in ("0", "00"):
            return
        else:
            print(f"{C.R}Invalid option!{C.END}"); time.sleep(1)

# ========== MAIN MENU ==========
def main_menu():
    while True:
        clear_screen()
        ws_count = len(list(WS_DIR.glob("*.json")))
        status_indicator = f"{C.G}●{C.END}" if server_running else f"{C.R}○{C.END}"
        bot_indicator = f"{C.G}●{C.END}" if bot_running else f"{C.R}○{C.END}"
        new_logs = _log_since_menu["n"]
        log_note = f" | 📜 Log ใหม่: {new_logs} บรรทัด (ดูที่ 08)" if new_logs > 0 else ""
        print(f"\n{'='*60}")
        print(f"{C.G}{C.BOLD}    LOTTERY RECORD SYSTEM — MAIN MENU{C.END}")
        mb_used = folder_size_mb(ROOT)
        if tunnel_proc and tunnel_url:
            tunnel_note = f" | 🌐 Tunnel: {C.G}{tunnel_url}{C.END}"
        elif tunnel_proc:
            tunnel_note = f" | 🌐 Tunnel: {C.Y}กำลังเชื่อมต่อ...{C.END}"
        else:
            tunnel_note = ""
        print(f"   Web: {status_indicator} {'Running' if server_running else 'Stopped'} | Bot: {bot_indicator} {'Online' if bot_running else 'Offline'} | Workspaces: {ws_count} | 💾 {mb_used:.1f} MB{tunnel_note}{log_note}")
        print("="*60)
        print(f"{C.C} 01.{C.END} System Configuration")
        print(f"{C.C} 02.{C.END} View Records (เลือก Workspace + วันที่)")
        print(f"{C.C} 03.{C.END} Start Local Server")
        print(f"{C.C} 04.{C.END} Start Server + Public Tunnel")
        print(f"{C.C} 05.{C.END} Stop Server")
        print(f"{C.C} 06.{C.END} 🔑 Key Manager (สร้าง/ลบ/ดูคีย์)")
        print(f"{C.C} 07.{C.END} 🤖 Discord Bot Setup (Token + Admin)")
        print(f"{C.C} 08.{C.END} 📜 ดู Log (ประวัติการใช้งาน)")
        print(f"{C.C} 00.{C.END} Exit")
        print("="*60)
        choice = input(f"\n{C.B}Enter option & press Enter: {C.END}").strip()
        if choice in ["01", "1"]:
            page_system_config()
        elif choice in ["02", "2"]:
            page_view_data()
        elif choice in ["03", "3"]:
            start_server(with_tunnel=False)
        elif choice in ["04", "4"]:
            start_server(with_tunnel=True)
        elif choice in ["05", "5"]:
            stop_server()
        elif choice in ["06", "6"]:
            page_key_manager()
        elif choice in ["07", "7"]:
            page_bot_config()
        elif choice in ["08", "8"]:
            page_logs()
        elif choice in ["00", "0"]:
            if server_running:
                print(f"{C.Y}⚠️  Server still running — stopping first...{C.END}")
                stop_server()
            clear_screen()
            print(f"{C.G}Goodbye!{C.END}\n")
            sys.exit(0)
        else:
            print(f"{C.R}Invalid option! Try again.{C.END}")
            time.sleep(1.2)

if __name__ == "__main__":
    try:
        load_config()
        removed = clean_expired_keys()
        if removed:
            log(f"ล้างคีย์หมดอายุตอนเปิดโปรแกรม {removed} รายการ")
        log("=== โปรแกรมเริ่มทำงาน ===")
        start_bot_if_configured()
        time.sleep(0.5)
        start_server(with_tunnel=False, auto=True)  # เปิดเว็บเซิร์ฟเวอร์อัตโนมัติทันที
        time.sleep(0.5)
        mb = folder_size_mb(ROOT)
        nkeys = len(load_keys())
        nws = len(list(WS_DIR.glob("*.json")))
        nlog = len(_read_log_lines())
        print(f"{C.C}💾 โฟลเดอร์นี้ใช้พื้นที่ {mb:.2f} MB | Keys: {nkeys} | Workspaces: {nws} | Log: {nlog} บรรทัด{C.END}")
        print(f"{C.C}🖥  ระบบ: {platform.system()} {platform.machine()} | Python {platform.python_version()}{C.END}")
        main_menu()
    except KeyboardInterrupt:
        if server_running and server_instance:
            server_instance.shutdown()
        if tunnel_proc:
            tunnel_proc.terminate()
        clear_screen()
        print(f"{C.G}Exited. Goodbye!{C.END}\n")
        sys.exit(0)
