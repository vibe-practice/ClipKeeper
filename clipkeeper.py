"""
ClipKeeper - 윈도우용 클립보드 기록 프로그램

복사한 텍스트, 이미지, 파일을 자동으로 기록합니다.
- Alt+V : 어디서든 기록 창을 오버레이로 열고 닫기
  창에서 검색 → Enter(또는 더블클릭)하면 원래 쓰던 창에 바로 붙여넣기
- 숨기기: 항목 선택 후 Ctrl+H(또는 '숨기기' 버튼) → 평소 목록에서 사라짐
  Alt+Shift+V(창 안에서는 Ctrl+Shift+H)를 누르면 숨긴 항목만 보임
- 종류/날짜 필터, 실시간 검색, 선택 삭제, 필터 결과 전체 삭제
- 창을 닫아도 작업표시줄 오른쪽 아래(트레이)에서 계속 기록
- 윈도우 시작 시 자동 실행 옵션
- 자동 업데이트: GitHub Releases에 새 버전이 올라오면 알아서 받아서 교체 (UPDATE_REPO 설정 필요)

기록은 %APPDATA%\\ClipKeeper 폴더에 저장됩니다.
"""

import base64
import datetime as dt
import hashlib
import json
import urllib.request
import io
import os
import queue
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:  # 아이콘만 만들 때는 tkinter가 없어도 됨
    tk = ttk = messagebox = None

try:
    from PIL import Image, ImageDraw, ImageGrab
except ImportError:
    Image = ImageDraw = ImageGrab = None
try:
    from PIL import ImageTk
except ImportError:
    ImageTk = None

try:
    import pystray
except Exception:
    pystray = None


APP_NAME = "ClipKeeper"
APP_VERSION = "1.2.0"

# ── 자동 업데이트 ──
# GitHub 저장소 "아이디/저장소이름". 비워두면 자동 업데이트를 하지 않음.
UPDATE_REPO = "vibe-practice/clipkeeper"
UPDATE_ASSET = "ClipKeeper.exe"
UPDATE_FIRST_CHECK_MS = 10_000          # 켜고 10초 뒤 첫 확인
UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000  # 이후 6시간마다 확인

POLL_MS = 500
LIST_LIMIT = 3000
INSTANCE_PORT = 47653
FONT = ("맑은 고딕", 10)
KIND_LABEL = {"text": "텍스트", "image": "이미지", "file": "파일"}
KIND_FROM_LABEL = {"전체": None, "텍스트": "text", "이미지": "image", "파일": "file"}

# ── 단축키 설정 (바꾸고 싶으면 여기만 수정) ──
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
# id: (조합키, 키, 이벤트 이름, 표시 이름)
HOTKEYS = {
    1: (MOD_ALT, ord("V"), "hotkey", "Alt+V"),                         # 기록 창 열기/닫기
    2: (MOD_ALT | MOD_SHIFT, ord("V"), "hotkey_hidden", "Alt+Shift+V"),  # 숨긴 항목 열기/닫기
}
HOTKEY_LABEL = HOTKEYS[1][3]
HIDDEN_HOTKEY_LABEL = HOTKEYS[2][3]


# ──────────────────────────────────────────────
# 아이콘 (코드로 직접 그림)
# ──────────────────────────────────────────────
def make_icon(size=256):
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((150, 130, 874, 970), radius=120, fill=(79, 70, 229))      # 보드
    d.rounded_rectangle((245, 260, 779, 880), radius=56, fill=(255, 255, 255))     # 종이
    for y, w in ((390, 380), (510, 300), (630, 380), (750, 230)):                  # 글줄
        d.rounded_rectangle((325, y, 325 + w, y + 50), radius=25, fill=(199, 210, 254))
    d.rounded_rectangle((352, 60, 672, 240), radius=64, fill=(49, 46, 129))        # 집게
    d.rounded_rectangle((452, 105, 572, 165), radius=30, fill=(129, 140, 248))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path="clipkeeper.ico"):
    make_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"icon saved: {os.path.abspath(path)}")


# ──────────────────────────────────────────────
# 윈도우 API (ctypes)
# ──────────────────────────────────────────────
CF_BITMAP, CF_DIB, CF_UNICODETEXT, CF_HDROP, CF_DIBV5 = 2, 8, 13, 15, 17
GMEM_MOVEABLE = 0x0002
WM_HOTKEY = 0x0312
ASFW_ANY = 0xFFFFFFFF
VK_CONTROL, KEYEVENTF_KEYUP = 0x11, 0x2

ctypes = wintypes = user32 = kernel32 = None


def init_win32():
    global ctypes, wintypes, user32, kernel32
    import ctypes as _ct
    from ctypes import wintypes as _wt

    ctypes, wintypes = _ct, _wt
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    sig = {
        "OpenClipboard": ([wintypes.HWND], wintypes.BOOL),
        "CloseClipboard": ([], wintypes.BOOL),
        "EmptyClipboard": ([], wintypes.BOOL),
        "GetClipboardData": ([wintypes.UINT], wintypes.HANDLE),
        "SetClipboardData": ([wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
        "IsClipboardFormatAvailable": ([wintypes.UINT], wintypes.BOOL),
        "GetClipboardSequenceNumber": ([], wintypes.DWORD),
        "RegisterHotKey": ([wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        "GetMessageW": ([ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        "GetForegroundWindow": ([], wintypes.HWND),
        "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
        "IsWindow": ([wintypes.HWND], wintypes.BOOL),
        "AllowSetForegroundWindow": ([wintypes.DWORD], wintypes.BOOL),
        "keybd_event": ([wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t], None),
    }
    for name, (args, res) in sig.items():
        fn = getattr(user32, name)
        fn.argtypes, fn.restype = args, res

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE

    try:  # 고해상도 모니터에서 글자가 흐리지 않게
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def clip_seq():
    return user32.GetClipboardSequenceNumber()


def _available(fmt):
    return bool(user32.IsClipboardFormatAvailable(fmt))


def _open_clipboard(retries=10):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.03)
    return False


def _read_text():
    if not _available(CF_UNICODETEXT) or not _open_clipboard():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def _grab():
    try:
        return ImageGrab.grabclipboard()
    except Exception:
        return None


def read_clipboard():
    """현재 클립보드 내용을 (종류, 데이터)로. 우선순위: 파일 > 텍스트 > 이미지"""
    if _available(CF_HDROP):
        data = _grab()
        if isinstance(data, list) and data:
            return "file", [str(p) for p in data]
    text = _read_text()
    if text and text.strip():
        return "text", text
    if any(_available(f) for f in (CF_DIB, CF_DIBV5, CF_BITMAP)):
        data = _grab()
        if isinstance(data, Image.Image):
            return "image", data
    return None, None


def _set_clipboard(fmt, data: bytes):
    if not _open_clipboard():
        raise RuntimeError("클립보드를 열 수 없어요. 잠시 후 다시 시도해 주세요.")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise MemoryError("메모리 할당 실패")
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(fmt, h):
            kernel32.GlobalFree(h)
            raise RuntimeError("클립보드 쓰기 실패")
    finally:
        user32.CloseClipboard()


def set_text(text):
    _set_clipboard(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))


def set_image(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    _set_clipboard(CF_DIB, buf.getvalue()[14:])  # BMP 파일 헤더 14바이트를 떼면 DIB


def set_files(paths):
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # DROPFILES
    body = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    _set_clipboard(CF_HDROP, header + body)


def send_ctrl_v():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(ord("V"), 0, 0, 0)
    user32.keybd_event(ord("V"), 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def hotkey_worker(events):
    """전역 단축키를 등록하고 눌릴 때마다 이벤트를 보낸다 (별도 스레드)."""
    failed = []
    for hid, (mods, vk, _ev, label) in HOTKEYS.items():
        if not user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            failed.append(label)
    if failed:
        events.put(("hotkey_fail", failed))
    if len(failed) == len(HOTKEYS):
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam in HOTKEYS:
            events.put(HOTKEYS[msg.wParam][2])


# ──────────────────────────────────────────────
# 한 번만 실행 (이미 켜져 있으면 그 창을 띄움)
# ──────────────────────────────────────────────
HELLO, REPLY = b"ClipKeeper:show", b"ClipKeeper:ok"


def single_instance(events):
    """이미 실행 중이면 'existing', 새로 서버를 열면 소켓을 돌려준다."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", INSTANCE_PORT))
        srv.listen(4)
    except OSError:
        srv.close()
        try:
            user32.AllowSetForegroundWindow(ASFW_ANY)
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as c:
                c.sendall(HELLO)
                if c.recv(32) == REPLY:
                    return "existing"
        except OSError:
            pass
        return None

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    if conn.recv(32) == HELLO:
                        conn.sendall(REPLY)
                        events.put("show")
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()
    return srv


# ──────────────────────────────────────────────
# 윈도우 시작 시 자동 실행 (레지스트리 Run 키)
# ──────────────────────────────────────────────
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_cmd():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --hidden'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{w if os.path.exists(w) else exe}" "{os.path.abspath(__file__)}" --hidden'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


# ──────────────────────────────────────────────
# 자동 업데이트 (GitHub Releases)
# ──────────────────────────────────────────────
_update_lock = threading.Lock()


def parse_version(v):
    nums = []
    for part in str(v).lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    return tuple(nums + [0] * (3 - len(nums)))


def is_frozen():
    return getattr(sys, "frozen", False)


def update_dir(base):
    d = os.path.join(base, "update")
    os.makedirs(d, exist_ok=True)
    return d


def fetch_latest_release():
    url = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    asset = next((a for a in data.get("assets", []) if a.get("name") == UPDATE_ASSET), None)
    return {
        "version": data.get("tag_name", "").lstrip("vV"),
        "notes": (data.get("body") or "").strip(),
        "url": asset and asset["browser_download_url"],
        "size": asset and asset.get("size"),
    }


def download_update(info, base):
    dest = os.path.join(update_dir(base), "ClipKeeper.new.exe")
    tmp = dest + ".part"
    req = urllib.request.Request(info["url"], headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    size = os.path.getsize(tmp)
    with open(tmp, "rb") as f:
        head = f.read(2)
    if head != b"MZ" or (info["size"] and size != info["size"]):
        os.remove(tmp)
        raise RuntimeError("받은 파일이 올바르지 않아요")
    os.replace(tmp, dest)
    return dest


def update_worker(events, base, manual=False):
    """새 버전이 있으면 내려받고 ('update_ready', 버전, 경로)를 보낸다 (별도 스레드)."""
    if not _update_lock.acquire(blocking=False):
        return
    try:
        if not UPDATE_REPO:
            if manual:
                events.put(("update_msg", "업데이트 저장소가 아직 설정되지 않았어요"))
            return
        info = fetch_latest_release()
        if not info["version"] or parse_version(info["version"]) <= parse_version(APP_VERSION):
            if manual:
                events.put(("update_msg", f"최신 버전이에요 (v{APP_VERSION})"))
            return
        if not is_frozen() or not info["url"]:
            events.put(("update_msg", f"새 버전 v{info['version']}이 있어요"))
            return
        path = download_update(info, base)
        events.put(("update_ready", info["version"], path))
    except Exception as e:
        if manual:
            events.put(("update_msg", f"업데이트 확인 실패: {e}"))
    finally:
        _update_lock.release()


def launch_swap_and_restart(new_exe, start_hidden, new_version):
    """이 프로그램이 꺼지길 기다렸다가 exe를 새 것으로 바꾸고 다시 실행하는 PowerShell을 띄운다."""
    q = lambda s: "'" + s.replace("'", "''") + "'"
    target = sys.executable
    args = ["--updated", new_version] + (["--hidden"] if start_hidden else [])
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
Wait-Process -Id {os.getpid()} -Timeout 30
Start-Sleep -Milliseconds 500
for ($i = 0; $i -lt 30; $i++) {{
  try {{ Move-Item -LiteralPath {q(new_exe)} -Destination {q(target)} -Force -ErrorAction Stop; break }}
  catch {{ Start-Sleep -Milliseconds 500 }}
}}
Start-Process -FilePath {q(target)} -ArgumentList {", ".join(q(a) for a in args)}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-EncodedCommand", encoded],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )


# ──────────────────────────────────────────────
# 저장소 (SQLite + 이미지 폴더)
# ──────────────────────────────────────────────
class Store:
    def __init__(self, base_dir):
        self.base = base_dir
        self.img_dir = os.path.join(base_dir, "images")
        os.makedirs(self.img_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(base_dir, "clipboard.db"))
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                hash TEXT NOT NULL UNIQUE,
                meta TEXT,
                size INTEGER)"""
        )
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(clips)")]
        if "hidden" not in cols:  # 예전 버전 기록에 숨김 칸 추가
            self.db.execute("ALTER TABLE clips ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_created ON clips(created)")
        self.db.commit()

    def set_hidden(self, ids, hidden):
        ids = list(ids)
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self.db.execute(f"UPDATE clips SET hidden=? WHERE id IN ({marks})", [1 if hidden else 0, *ids])
        self.db.commit()

    @staticmethod
    def _now():
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def image_path(self, name):
        return os.path.join(self.img_dir, name)

    def add(self, kind, payload):
        """기록 추가. 이미 있던 내용이면 시간만 갱신해 맨 위로 올린다."""
        if kind == "text":
            raw = payload.encode("utf-8")
            content, meta, size = payload, None, len(payload)
        elif kind == "file":
            content = "\n".join(payload)
            raw = content.encode("utf-8")
            meta, size = str(len(payload)), len(payload)
        elif kind == "image":
            buf = io.BytesIO()
            payload.save(buf, "PNG")
            raw = buf.getvalue()
            meta, size = f"{payload.width}x{payload.height}", len(raw)
            content = None
        else:
            return False

        h = hashlib.sha1(kind.encode() + b"|" + raw).hexdigest()
        row = self.db.execute("SELECT id FROM clips WHERE hash=?", (h,)).fetchone()
        if row:
            self.db.execute("UPDATE clips SET created=? WHERE id=?", (self._now(), row[0]))
        else:
            if kind == "image":
                content = f"{h}.png"
                with open(self.image_path(content), "wb") as f:
                    f.write(raw)
            self.db.execute(
                "INSERT INTO clips(created, kind, content, hash, meta, size) VALUES (?,?,?,?,?,?)",
                (self._now(), kind, content, h, meta, size),
            )
        self.db.commit()
        return True

    def query(self, kind=None, start=None, end=None, search=None, hidden=False, limit=LIST_LIMIT):
        sql = "SELECT id, created, kind, content, meta, size FROM clips WHERE hidden=?"
        args = [1 if hidden else 0]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if start:
            sql += " AND created >= ?"
            args.append(f"{start} 00:00:00")
        if end:
            sql += " AND created <= ?"
            args.append(f"{end} 23:59:59")
        if search:
            sql += " AND kind != 'image' AND content LIKE ?"
            args.append(f"%{search}%")
        sql += " ORDER BY created DESC, id DESC LIMIT ?"
        args.append(limit)
        return self.db.execute(sql, args).fetchall()

    def get(self, clip_id):
        return self.db.execute(
            "SELECT id, created, kind, content, meta, size FROM clips WHERE id=?", (clip_id,)
        ).fetchone()

    def delete(self, ids):
        ids = list(ids)
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        for (name,) in self.db.execute(
            f"SELECT content FROM clips WHERE kind='image' AND id IN ({marks})", ids
        ).fetchall():
            try:
                os.remove(self.image_path(name))
            except OSError:
                pass
        self.db.execute(f"DELETE FROM clips WHERE id IN ({marks})", ids)
        self.db.commit()

    def close(self):
        self.db.close()


# ──────────────────────────────────────────────
# 화면
# ──────────────────────────────────────────────
def preview_text(kind, content, meta):
    if kind == "text":
        s = " ".join(content.split())
        return s[:120] + ("…" if len(s) > 120 else "")
    if kind == "image":
        return f"이미지 {meta.replace('x', ' × ') if meta else ''}"
    paths = content.split("\n")
    names = ", ".join(os.path.basename(p.rstrip("\\/")) or p for p in paths[:3])
    more = f" 외 {len(paths) - 3}개" if len(paths) > 3 else ""
    return f"파일 {len(paths)}개: {names}{more}"


class App:
    def __init__(self, root, store, events, start_hidden=False, updated_to=None):
        self.root = root
        self.store = store
        self.events = events
        self.pending_update = None  # (버전, 내려받은 파일 경로)
        self.paused = False
        self.last_seq = 0
        self.photo = None
        self.prev_hwnd = None
        self.count = 0
        self.tray = None
        self._search_job = None
        self.hidden_mode = False  # True면 숨긴 항목만 보여줌

        root.title(f"ClipKeeper v{APP_VERSION}  ·  {HOTKEY_LABEL}로 언제든 열기")
        root.geometry("960x600")
        root.minsize(720, 420)
        self._icon_img = ImageTk.PhotoImage(make_icon(64))
        root.iconphoto(True, self._icon_img)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("Treeview", rowheight=28, font=FONT)
        style.configure("Treeview.Heading", font=(FONT[0], 10, "bold"))

        self._build_toolbar()
        self._build_body()
        self._build_bottom()

        root.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        root.bind("<Escape>", lambda e: self.hide())
        root.bind("<Control-H>", lambda e: self.set_hidden_mode(not self.hidden_mode))  # Ctrl+Shift+H
        root.protocol("WM_DELETE_WINDOW", self.on_close_button)

        self._start_tray()
        threading.Thread(target=hotkey_worker, args=(events,), daemon=True).start()

        self.refresh()
        if start_hidden:
            root.withdraw()
        else:
            self.show()
        self.poll()
        self.process_events()

        # 자동 업데이트: 남은 임시 파일 정리 후 주기적으로 확인
        for name in os.listdir(update_dir(store.base)):
            try:
                os.remove(os.path.join(update_dir(store.base), name))
            except OSError:
                pass
        root.after(UPDATE_FIRST_CHECK_MS, self._scheduled_update_check)
        if updated_to:
            self.update_status(f"v{updated_to}(으)로 업데이트했어요")
            self.notify(f"v{updated_to}(으)로 업데이트했어요")

    # ── 자동 업데이트 ──
    def check_update(self, manual=False):
        threading.Thread(
            target=update_worker, args=(self.events, self.store.base, manual), daemon=True
        ).start()

    def _scheduled_update_check(self):
        if not self.pending_update:
            self.check_update()
        self.root.after(UPDATE_INTERVAL_MS, self._scheduled_update_check)

    def on_update_ready(self, version, path):
        self.pending_update = (version, path)
        if not self.is_visible():
            self.apply_update(start_hidden=True)  # 안 쓰고 있을 때는 조용히 바로 적용
        else:
            self.update_btn.configure(text=f"v{version} 업데이트")
            self.update_btn.pack(side="right", padx=(8, 0))
            self.update_status(f"새 버전 v{version} 준비됨 · 창을 닫으면 자동으로 적용돼요")

    def apply_update(self, start_hidden=False):
        if not self.pending_update or not is_frozen():
            return
        version, path = self.pending_update
        try:
            launch_swap_and_restart(path, start_hidden, version)
        except Exception as e:
            self.pending_update = None
            self.update_status(f"업데이트 실패: {e}")
            return
        self.quit()

    def notify(self, text):
        if self.tray:
            try:
                self.tray.notify(text, APP_NAME)
            except Exception:
                pass

    # ── 레이아웃 ──
    def _build_toolbar(self):
        self.mode_bar = tk.Label(
            self.root, text=f"🔒 숨긴 항목을 보는 중이에요  ·  {HIDDEN_HOTKEY_LABEL} 또는 Esc로 닫기",
            bg="#4f46e5", fg="white", font=(FONT[0], 10, "bold"), pady=5,
        )

        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        self.toolbar = bar

        ttk.Label(bar, text="검색").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=26)
        self.search_entry.pack(side="left", padx=(4, 14))
        self.search_entry.bind("<KeyRelease>", self._on_search_key)
        self.search_entry.bind("<Return>", self._search_enter)
        self.search_entry.bind("<Down>", self._focus_list)

        ttk.Label(bar, text="종류").pack(side="left")
        self.kind_var = tk.StringVar(value="전체")
        kind_box = ttk.Combobox(
            bar, textvariable=self.kind_var, values=list(KIND_FROM_LABEL), width=7, state="readonly"
        )
        kind_box.pack(side="left", padx=(4, 14))
        kind_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        ttk.Label(bar, text="기간").pack(side="left")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        start = ttk.Entry(bar, textvariable=self.start_var, width=11)
        start.pack(side="left", padx=(4, 2))
        ttk.Label(bar, text="~").pack(side="left")
        end = ttk.Entry(bar, textvariable=self.end_var, width=11)
        end.pack(side="left", padx=(2, 6))
        for w in (start, end):
            w.bind("<Return>", lambda e: self.refresh())

        for label, days in (("오늘", 0), ("어제", -1), ("7일", 7), ("30일", 30), ("전체", None)):
            ttk.Button(bar, text=label, width=5, command=lambda d=days: self.quick_range(d)).pack(
                side="left", padx=1
            )

    def _build_body(self):
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10)

        left = ttk.Frame(pane)
        self.tree = ttk.Treeview(
            left, columns=("time", "kind", "preview"), show="headings", selectmode="extended"
        )
        self.tree.heading("time", text="시간")
        self.tree.heading("kind", text="종류")
        self.tree.heading("preview", text="내용")
        self.tree.column("time", width=150, stretch=False)
        self.tree.column("kind", width=70, stretch=False, anchor="center")
        self.tree.column("preview", width=380)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_preview())
        self.tree.bind("<Double-1>", lambda e: self.use_selected())
        self.tree.bind("<Return>", lambda e: self.use_selected())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Control-h>", lambda e: self.toggle_hide_selected())
        pane.add(left, weight=3)

        right = ttk.Frame(pane, padding=(10, 0, 0, 0))
        self.info_var = tk.StringVar(value="항목을 선택하면 여기에 미리보기가 나와요.")
        ttk.Label(right, textvariable=self.info_var, wraplength=360).pack(anchor="w", pady=(0, 6))
        self.preview_holder = ttk.Frame(right)
        self.preview_holder.pack(fill="both", expand=True)
        self.text_view = tk.Text(self.preview_holder, wrap="word", font=FONT, relief="solid", borderwidth=1)
        self.image_view = ttk.Label(self.preview_holder, anchor="center")
        pane.add(right, weight=2)

    def _build_bottom(self):
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="복사", command=self.copy_back).pack(side="left")
        self.hide_btn = ttk.Button(bar, text="숨기기", command=self.toggle_hide_selected)
        self.hide_btn.pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="선택 삭제", command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="목록 전체 삭제", command=self.delete_filtered).pack(side="left")
        ttk.Button(bar, text="파일 위치 열기", command=self.open_location).pack(side="left", padx=4)
        self.pause_btn = ttk.Button(bar, text="일시정지", command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=(16, 0))
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(
            bar, text="윈도우 시작 시 자동 실행", variable=self.auto_var, command=self.apply_autostart
        ).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="종료", command=self.quit).pack(side="right", padx=(8, 0))
        self.update_btn = ttk.Button(bar, text="업데이트", command=lambda: self.apply_update(start_hidden=False))
        self.status_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.status_var).pack(side="right")

    # ── 트레이 아이콘 ──
    def _start_tray(self):
        if pystray is None:
            return
        put = self.events.put
        menu = pystray.Menu(
            pystray.MenuItem("열기", lambda: put("show"), default=True),
            pystray.MenuItem("숨긴 항목 보기", lambda: put("show_hidden")),
            pystray.MenuItem("일시정지", lambda: put("pause"), checked=lambda i: self.paused),
            pystray.MenuItem("윈도우 시작 시 자동 실행", lambda: put("autostart"),
                             checked=lambda i: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"업데이트 확인 (현재 v{APP_VERSION})", lambda: put("check_update")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", lambda: put("quit")),
        )
        try:
            self.tray = pystray.Icon(APP_NAME, make_icon(64), f"ClipKeeper ({HOTKEY_LABEL})", menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def _update_tray(self):
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:
                pass

    # ── 창 보이기/숨기기 ──
    def _own_hwnd(self):
        try:
            return int(self.root.wm_frame(), 16)
        except Exception:
            return None

    def is_visible(self):
        return self.root.state() != "withdrawn"

    def show(self, from_hotkey=False, hidden=False):
        fg = user32.GetForegroundWindow()
        if fg != self._own_hwnd():
            self.prev_hwnd = fg if (from_hotkey and fg) else None
        self.set_hidden_mode(hidden, refresh=False)
        self.root.deiconify()
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{max((sw - w) // 2, 0)}+{max((sh - h) // 3, 0)}")
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        self.refresh()

    def hide(self):
        self.root.withdraw()
        self.set_hidden_mode(False, refresh=False)  # 다음에 열 때는 항상 일반 목록부터
        if self.pending_update:
            self.root.after(1500, lambda: (not self.is_visible()) and self.apply_update(start_hidden=True))

    def toggle_overlay(self, hidden=False):
        focused = self.is_visible() and user32.GetForegroundWindow() == self._own_hwnd()
        if focused and self.hidden_mode == hidden:
            self.hide()
        else:
            self.show(from_hotkey=True, hidden=hidden)

    # ── 숨기기 ──
    def set_hidden_mode(self, on, refresh=True):
        self.hidden_mode = on
        if on:
            self.mode_bar.pack(fill="x", before=self.toolbar)
        else:
            self.mode_bar.pack_forget()
        self.hide_btn.configure(text="숨김 해제" if on else "숨기기")
        if refresh:
            self.tree.selection_remove(self.tree.selection())
            self.refresh()
            self.show_preview()

    def toggle_hide_selected(self):
        ids = [int(i) for i in self.tree.selection()]
        if not ids:
            return "break"
        self.store.set_hidden(ids, not self.hidden_mode)
        msg = f"{len(ids)}개를 숨겼어요 ({HIDDEN_HOTKEY_LABEL}로 보기)" if not self.hidden_mode \
            else f"{len(ids)}개를 다시 보이게 했어요"
        self.refresh()
        self.show_preview()
        self.update_status(msg)
        return "break"

    def on_close_button(self):
        if self.tray:
            self.hide()
            self.update_status("창을 닫아도 계속 기록해요")
        else:
            self.quit()

    # ── 필터 ──
    def quick_range(self, days):
        today = dt.date.today()
        if days is None:
            s = e = ""
        elif days == 0:
            s = e = today.isoformat()
        elif days == -1:
            s = e = (today - dt.timedelta(days=1)).isoformat()
        else:
            s, e = (today - dt.timedelta(days=days - 1)).isoformat(), today.isoformat()
        self.start_var.set(s)
        self.end_var.set(e)
        self.refresh()

    def _filters(self):
        dates = []
        for var in (self.start_var, self.end_var):
            v = var.get().strip()
            if v:
                try:
                    dt.datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    messagebox.showwarning(APP_NAME, f"날짜는 2026-11-23 형식으로 입력해 주세요.\n입력값: {v}", parent=self.root)
                    return None
            dates.append(v or None)
        return {
            "kind": KIND_FROM_LABEL.get(self.kind_var.get()),
            "start": dates[0],
            "end": dates[1],
            "search": self.search_var.get().strip() or None,
            "hidden": self.hidden_mode,
        }

    def refresh(self):
        f = self._filters()
        if f is None:
            return
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        rows = self.store.query(**f)
        for cid, created, kind, content, meta, _size in rows:
            self.tree.insert("", "end", iid=str(cid),
                             values=(created, KIND_LABEL[kind], preview_text(kind, content, meta)))
        still = [i for i in keep if self.tree.exists(i)]
        if still:
            self.tree.selection_set(still)
        self.count = len(rows)
        self.update_status()

    def _on_search_key(self, e):
        if e.keysym in ("Return", "Down", "Up", "Escape", "Tab"):
            return
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(250, self.refresh)

    def _first_item(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])
            self.tree.see(kids[0])
        return bool(kids)

    def _search_enter(self, e):
        self.refresh()
        if self._first_item():
            self.use_selected()
        return "break"

    def _focus_list(self, e):
        if self._first_item():
            self.tree.focus_set()
        return "break"

    def update_status(self, msg=None):
        state = "⏸ 일시정지" if self.paused else "● 기록 중"
        text = f"{state}  ·  {'숨긴 항목 ' if self.hidden_mode else ''}{self.count}개"
        if self.count >= LIST_LIMIT:
            text += f" (최근 {LIST_LIMIT}개만 표시)"
        if msg:
            text = f"{msg}  ·  {text}"
        self.status_var.set(text)

    # ── 미리보기 ──
    def _selected_rows(self):
        return [r for r in (self.store.get(int(i)) for i in self.tree.selection()) if r]

    def show_preview(self):
        self.text_view.pack_forget()
        self.image_view.pack_forget()
        rows = self._selected_rows()
        if not rows:
            self.info_var.set("항목을 선택하면 여기에 미리보기가 나와요.")
            return
        if len(rows) > 1:
            self.info_var.set(f"{len(rows)}개 선택됨")
            return
        _cid, created, kind, content, meta, size = rows[0]
        if kind == "image":
            self.info_var.set(f"{created}  ·  이미지 {meta}  ·  {size / 1024:,.0f} KB")
            try:
                img = Image.open(self.store.image_path(content))
                w = max(self.preview_holder.winfo_width() - 10, 200)
                h = max(self.preview_holder.winfo_height() - 10, 200)
                img.thumbnail((w, h))
                self.photo = ImageTk.PhotoImage(img)
                self.image_view.configure(image=self.photo, text="")
            except Exception:
                self.image_view.configure(image="", text="이미지 파일을 찾을 수 없어요.")
            self.image_view.pack(fill="both", expand=True)
        else:
            if kind == "text":
                self.info_var.set(f"{created}  ·  텍스트 {size:,}자")
                body = content
            else:
                paths = content.split("\n")
                self.info_var.set(f"{created}  ·  파일 {len(paths)}개")
                body = "\n".join(("" if os.path.exists(p) else "[없음] ") + p for p in paths)
            self.text_view.configure(state="normal")
            self.text_view.delete("1.0", "end")
            self.text_view.insert("1.0", body)
            self.text_view.configure(state="disabled")
            self.text_view.pack(fill="both", expand=True)

    # ── 동작 ──
    def copy_back(self):
        rows = self._selected_rows()
        if not rows:
            return False
        _cid, _created, kind, content, _meta, _size = rows[0]
        try:
            if kind == "text":
                set_text(content)
            elif kind == "image":
                set_image(Image.open(self.store.image_path(content)))
            else:
                paths = [p for p in content.split("\n") if os.path.exists(p)]
                if not paths:
                    messagebox.showwarning(APP_NAME, "원본 파일이 모두 옮겨졌거나 삭제됐어요.", parent=self.root)
                    return False
                set_files(paths)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"복사하지 못했어요.\n{e}", parent=self.root)
            return False
        self.last_seq = clip_seq()  # 내가 넣은 건 새 기록으로 잡지 않음
        self.update_status("클립보드에 복사했어요")
        return True

    def use_selected(self):
        """Enter/더블클릭: 복사하고, 단축키로 열었으면 원래 창에 바로 붙여넣기"""
        if not self.copy_back():
            return
        target = self.prev_hwnd
        if target and user32.IsWindow(target):
            self.prev_hwnd = None
            self.hide()
            user32.SetForegroundWindow(target)
            self.root.after(120, send_ctrl_v)

    def delete_selected(self):
        ids = [int(i) for i in self.tree.selection()]
        if not ids:
            return
        if not messagebox.askyesno(APP_NAME, f"선택한 {len(ids)}개 기록을 삭제할까요?\n삭제하면 되돌릴 수 없어요.", parent=self.root):
            return
        self.store.delete(ids)
        self.refresh()
        self.show_preview()

    def delete_filtered(self):
        ids = [int(i) for i in self.tree.get_children()]
        if not ids:
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"지금 목록에 보이는 {len(ids)}개 기록을 모두 삭제할까요?\n(필터에 걸린 항목만 지워져요. 되돌릴 수 없어요.)",
            parent=self.root,
        ):
            return
        self.store.delete(ids)
        self.refresh()
        self.show_preview()

    def open_location(self):
        rows = self._selected_rows()
        if not rows:
            return
        _cid, _c, kind, content, _m, _s = rows[0]
        if kind == "image":
            path = self.store.image_path(content)
        elif kind == "file":
            path = next((p for p in content.split("\n") if os.path.exists(p)), None)
            if not path:
                messagebox.showwarning(APP_NAME, "원본 파일을 찾을 수 없어요.", parent=self.root)
                return
        else:
            return
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.configure(text="다시 기록" if self.paused else "일시정지")
        self.update_status()
        self._update_tray()

    def apply_autostart(self):
        try:
            set_autostart(self.auto_var.get())
        except OSError as e:
            messagebox.showerror(APP_NAME, f"자동 실행 설정을 바꾸지 못했어요.\n{e}", parent=self.root)
        self.auto_var.set(autostart_enabled())
        self._update_tray()

    def quit(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.store.close()
        self.root.destroy()
        os._exit(0)

    # ── 이벤트 처리 (단축키·트레이·두 번째 실행) ──
    def process_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev == "hotkey":
                    self.toggle_overlay()
                elif ev == "hotkey_hidden":
                    self.toggle_overlay(hidden=True)
                elif ev == "show":
                    self.show()
                elif ev == "show_hidden":
                    self.show(hidden=True)
                elif ev == "check_update":
                    self.update_status("업데이트 확인 중…")
                    self.check_update(manual=True)
                elif isinstance(ev, tuple) and ev[0] == "update_ready":
                    self.on_update_ready(ev[1], ev[2])
                elif isinstance(ev, tuple) and ev[0] == "update_msg":
                    self.update_status(ev[1])
                    if not self.is_visible():
                        self.notify(ev[1])
                elif ev == "pause":
                    self.toggle_pause()
                elif ev == "autostart":
                    self.auto_var.set(not autostart_enabled())
                    self.apply_autostart()
                elif ev == "quit":
                    self.quit()
                    return
                elif isinstance(ev, tuple) and ev[0] == "hotkey_fail":
                    messagebox.showwarning(
                        APP_NAME,
                        f"{', '.join(ev[1])} 단축키를 등록하지 못했어요.\n다른 프로그램이 이미 쓰고 있는 것 같아요.\n"
                        "트레이 아이콘이나 바탕화면 아이콘으로 열 수 있어요.",
                        parent=self.root,
                    )
        except queue.Empty:
            pass
        self.root.after(60, self.process_events)

    # ── 클립보드 감시 ──
    def poll(self):
        try:
            seq = clip_seq()
            if seq != self.last_seq:
                self.last_seq = seq
                if not self.paused:
                    kind, payload = read_clipboard()
                    if kind and self.store.add(kind, payload) and self.is_visible():
                        self.refresh()
        except Exception as e:
            self.update_status(f"오류: {e}")
        self.root.after(POLL_MS, self.poll)


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print(APP_VERSION)
        return
    if "--make-icon" in args:
        save_ico()
        return
    if sys.platform != "win32":
        print("ClipKeeper는 윈도우 전용이에요.")
        sys.exit(1)

    init_win32()
    events = queue.Queue()
    server = single_instance(events)
    if server == "existing":
        return  # 이미 켜져 있던 창이 앞으로 나옴

    root = tk.Tk()
    if Image is None:
        root.withdraw()
        messagebox.showerror(APP_NAME, "Pillow가 필요해요.\n명령 프롬프트에서 다음을 실행해 주세요:\n\npip install pillow pystray")
        sys.exit(1)
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(base, exist_ok=True)
    updated_to = args[args.index("--updated") + 1] if "--updated" in args[:-1] else None
    App(root, Store(base), events, start_hidden="--hidden" in args, updated_to=updated_to)
    root.mainloop()


if __name__ == "__main__":
    main()
