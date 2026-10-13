from __future__ import annotations

import asyncio
import base64
import email
import email.header
import email.utils
import html
import imaplib
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from cardinal import Cardinal
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
    from FunPayAPI.updater.events import NewMessageEvent

_REQUIRED_PACKAGES = [
    ("requests", "requests"),
    ("colorama", "colorama"),
]
_missing_pkgs = []
for _mod, _pkg in _REQUIRED_PACKAGES:
    try:
        __import__(_mod)
    except ImportError:
        _missing_pkgs.append(_pkg)

if _missing_pkgs:
    print(f"[Mail Code Plugin] Установка библиотек: {', '.join(_missing_pkgs)}...")
    for _extra_args in [["--break-system-packages"], []]:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *_missing_pkgs, "--quiet", "--no-warn-script-location", *_extra_args],
                timeout=180,
            )
            break
        except Exception:
            continue

import requests

try:
    import socks
except ImportError:
    socks = None

try:
    from colorama import Fore, Style
except ImportError:
    class _DummyColor:
        def __getattr__(self, name):
            return ""
    Fore = _DummyColor()
    Style = _DummyColor()

try:
    from telebot import types as tg_types
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
except ImportError:
    class InlineKeyboardMarkup:
        pass
    class InlineKeyboardButton:
        pass
    class Message:
        pass
    tg_types = None

try:
    from FunPayAPI.updater.events import NewMessageEvent
except Exception:
    class NewMessageEvent:
        pass

try:
    import tg_bot.CBT as CBT
except Exception:
    class CBT:
        EDIT_PLUGIN = "45"
        PLUGIN_SETTINGS = "47"
        PLUGINS_LIST = "44"
        BACK = None

NAME = "Mail-Code"
VERSION = "1.0.0"
DESCRIPTION = "Автовыдача кодов с почты (IMAP & Outlook Graph API / OWA)"
CREDITS = "@tinechelovec"
UUID = "f7b19a82-41e9-4e5a-8b83-a7c862d39b01"
SETTINGS_PAGE = True

CREATOR_URL = "https://t.me/tinechelovec"
GROUP_URL = "https://t.me/dev_thc_chat"
CHANNEL_URL = "https://t.me/by_thc"
INSTRUCTION_URL = "https://teletype.in/@tinechelovec/Mail-Code"
ALT_INSTRUCTION_URL = "https://github.com/tinechelovec/FPC-Mail-Code/blob/main/instructions.md"
GITHUB_URL = "https://github.com/tinechelovec/FPC-Mail-Code"
GITHUB_UPDATE_URL = os.getenv("MAIL_CODE_UPDATE_URL", "https://raw.githubusercontent.com/tinechelovec/FPC-Mail-Code/main/Mail-Code.py").strip()
CB_PLUGINS_LIST_OPEN = f"{getattr(CBT, 'PLUGINS_LIST', '44')}:0" if CBT else "44:0"

logger = logging.getLogger("FPC.MailCode")
LP = "[Mail-Code]"

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(_PLUGIN_DIR).lower() == "plugins":
    CARDINAL_ROOT = os.path.abspath(os.path.join(_PLUGIN_DIR, ".."))
else:
    CARDINAL_ROOT = os.getcwd()

STORAGE_DIR = os.path.join(CARDINAL_ROOT, "storage", "plugins", "mail_code_plugin")
CONFIG_FILE = os.path.join(STORAGE_DIR, "config.json")
LOG_FILE = os.path.join(STORAGE_DIR, "mail_code_plugin.log")

_unwanted_plugins_storage = os.path.join(_PLUGIN_DIR, "storage")
if os.path.exists(_unwanted_plugins_storage):
    try:
        shutil.rmtree(_unwanted_plugins_storage, ignore_errors=True)
    except Exception:
        pass

os.makedirs(STORAGE_DIR, exist_ok=True)

_ANSI_STRIP_RE = re.compile(r"(\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]))")

class _CleanFileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return _ANSI_STRIP_RE.sub("", s)

class _FlushFileHandler(logging.FileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
        except Exception:
            pass

_fh: Optional[_FlushFileHandler] = None
try:
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10 * 1024 * 1024:
        with open(LOG_FILE, "a", encoding="utf-8") as _f_trunc:
            _f_trunc.truncate(2 * 1024 * 1024)
    _fh = _FlushFileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(_CleanFileFormatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)
except Exception:
    pass

def _flush_logs():
    if _fh:
        try:
            _fh.flush()
        except Exception:
            pass

def _log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    tag = f"{Fore.CYAN}{LP}{Style.RESET_ALL}"
    ev_str = f"{Fore.YELLOW}event={event}{Style.RESET_ALL}"
    parts = [tag, ev_str]
    for k, v in fields.items():
        if v is None:
            continue
        v_str = str(v).replace("\r", " ").replace("\n", " ")
        if any(token in k.lower() for token in ("password", "secret", "token", "refresh")):
            v_str = "***"
        if k in ("status", "result"):
            if str(v).lower() in ("ok", "success", "true", "200"):
                val_colored = f"{Fore.GREEN}{v_str}{Style.RESET_ALL}"
            else:
                val_colored = f"{Fore.RED}{v_str}{Style.RESET_ALL}"
        elif any(w in k.lower() for w in ("err", "fail", "reason", "error")):
            val_colored = f"{Fore.RED}{v_str}{Style.RESET_ALL}"
        else:
            val_colored = f"{Fore.WHITE}{v_str}{Style.RESET_ALL}"
        parts.append(f"{Fore.LIGHTBLUE_EX}{k}={Style.RESET_ALL}{val_colored}")
    line = " ".join(parts)
    logger.log(level, line)
    _flush_logs()

cardinal_instance: Optional[Cardinal] = None
bot_instance: Any = None
admin_chat_id: Optional[int] = None

_config_lock = threading.RLock()
_waiting: Dict[int, Dict[str, Any]] = {}
_recent_buyer_requests: Dict[str, float] = {}
_suppress_own_notification_until = 0.0
_suppress_lock = threading.RLock()

PROVIDERS = {
    "outlook": {
        "name": "Outlook / Hotmail",
        "icon": "🔵",
        "desc": "Microsoft Graph API & OWA (OAuth2 / Refresh Token / Web Auth, без IMAP)",
        "default_host": "graph.microsoft.com",
        "default_port": 443,
        "is_oauth": True,
    },
    "gmail": {
        "name": "Gmail",
        "icon": "🔴",
        "desc": "IMAP (требуется Пароль приложений Google)",
        "default_host": "imap.gmail.com",
        "default_port": 993,
        "is_oauth": False,
    },
    "yandex": {
        "name": "Yandex",
        "icon": "🟡",
        "desc": "IMAP (требуется Пароль приложения Яндекс)",
        "default_host": "imap.yandex.ru",
        "default_port": 993,
        "is_oauth": False,
    },
    "mailru": {
        "name": "Mail.ru",
        "icon": "🔷",
        "desc": "IMAP (требуется Пароль для внешних приложений)",
        "default_host": "imap.mail.ru",
        "default_port": 993,
        "is_oauth": False,
    },
    "firstmail": {
        "name": "Firstmail",
        "icon": "🟣",
        "desc": "IMAP (поддерживается логин и пароль)",
        "default_host": "imap.firstmail.ltd",
        "default_port": 993,
        "is_oauth": False,
    },
    "notletters": {
        "name": "Notletters",
        "icon": "🟢",
        "desc": "IMAP (поддерживается логин и пароль)",
        "default_host": "imap.notletters.com",
        "default_port": 993,
        "is_oauth": False,
    },
    "smakmail": {
        "name": "SmakMail",
        "icon": "🟠",
        "desc": "IMAP (поддерживается логин и пароль)",
        "default_host": "imap.smakmail.com",
        "default_port": 993,
        "is_oauth": False,
    },
}

SERVICES = {
    "steam": {
        "name": "Steam",
        "icon": "🎮",
        "sender_hint": "steampowered.com",
        "code_desc": "5-значный код Steam Guard (буквы и цифры)",
    },
    "epic": {
        "name": "Epic Games",
        "icon": "🎯",
        "sender_hint": "epicgames.com",
        "code_desc": "6-значный цифровой код",
    },
    "discord": {
        "name": "Discord",
        "icon": "💬",
        "sender_hint": "discord.com",
        "code_desc": "6-значный цифровой код",
    },
    "battlenet": {
        "name": "Battle.net / Blizzard",
        "icon": "⚔️",
        "sender_hint": "blizzard.com",
        "code_desc": "6-8 значный цифровой код",
    },
    "riot": {
        "name": "Riot Games (Valorant/LoL)",
        "icon": "🔫",
        "sender_hint": "riotgames.com",
        "code_desc": "6-значный цифровой код",
    },
    "ubisoft": {
        "name": "Ubisoft",
        "icon": "🛡️",
        "sender_hint": "ubi.com",
        "code_desc": "6-значный цифровой код",
    },
    "supercell": {
        "name": "Supercell (Brawl/Clash)",
        "icon": "⚡",
        "sender_hint": "supercell.com",
        "code_desc": "6-значный цифровой код",
    },
    "telegram": {
        "name": "Telegram",
        "icon": "✈️",
        "sender_hint": "telegram.org",
        "code_desc": "5-значный цифровой код",
    },
    "roblox": {
        "name": "Roblox",
        "icon": "🎲",
        "sender_hint": "roblox.com",
        "code_desc": "6-значный цифровой код",
    },
    "microsoft": {
        "name": "Microsoft / Xbox",
        "icon": "🪟",
        "sender_hint": "microsoft.com",
        "code_desc": "6-7 значный цифровой код безопасности",
    },
    "google": {
        "name": "Google / Gmail",
        "icon": "🌐",
        "sender_hint": "google.com",
        "code_desc": "6-значный цифровой код (G-XXXXXX)",
    },
    "vk": {
        "name": "ВКонтакте (VK)",
        "icon": "🔵",
        "sender_hint": "vk.com",
        "code_desc": "6-значный цифровой код",
    },
    "all": {
        "name": "Авто-определение / Любой сервис",
        "icon": "🌐",
        "sender_hint": "любой",
        "code_desc": "Умное распознавание сервиса (Steam, Epic, TG и др.) и поиск кода",
    },
    "custom": {
        "name": "Свой фильтр",
        "icon": "✏️",
        "sender_hint": "пользовательский",
        "code_desc": "По ключевому слову или регулярному выражению",
    },
}

def _clean_template_html(tpl: str) -> str:
    if not tpl:
        return ""
    s = re.sub(r"</?(?:b|i|code|span|em|strong)[^>]*>", "", str(tpl))
    return s.strip()

class _SafeDict(dict):
    def __missing__(self, key):
        return f"{{{key}}}"

def _safe_format(template_str: str, **kwargs) -> str:
    if not template_str:
        return ""
    try:
        return template_str.format_map(_SafeDict(**kwargs))
    except Exception:
        res = template_str
        for k, v in kwargs.items():
            res = res.replace(f"{{{k}}}", str(v))
        return res

def _format_datetime(dt_input: Any) -> str:
    if not dt_input:
        return "только что"
    try:
        dt = None
        if isinstance(dt_input, datetime):
            dt = dt_input
        elif isinstance(dt_input, str):
            s = dt_input.strip()
            if "T" in s:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = email.utils.parsedate_to_datetime(s)
        if dt:
            local_dt = dt.astimezone()
            return local_dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass
    return str(dt_input)[:19] if dt_input else "только что"

def _default_templates() -> dict:
    return {
        "code_issued": (
            "✅ Ваш проверочный код: {code}\n\n"
            "📌 Сервис: {service}\n"
            "👤 Отправитель: {sender}\n"
            "🕒 Получено: {time}\n"
            "⏳ Код действителен ограниченное время."
        ),
        "cmd_received": (
            "🔍 Запрос принят! Ищу свежее письмо с кодом подтверждения... Пожалуйста, подождите."
        ),
        "code_not_found": (
            "❌ Свежий код подтверждения пока не найден на почте.\n\n"
            "Пожалуйста, запросите отправку кода заново на сайте/в приложении и напишите команду {command} на фанпее повторно через несколько секунд."
        ),
        "cooldown_warning": (
            "⏳ Пожалуйста, подождите {left} сек. перед повторным запросом кода."
        ),
        "blacklist_blocked": (
            "⛔ Вы находитесь в чёрном списке. Выдача кода недоступна."
        ),
    }

def _default_config() -> dict:
    return {
        "global": {
            "plugin_enabled": True,
            "command_notifications_enabled": False,
            "search_window_min": 15,
            "cooldown_sec": 10,
            "send_searching_msg": False,
            "template": _default_templates()["code_issued"],
            "template_mode": "global",
            "blacklist_enabled": False,
            "blacklist_nicks": [],
            "blacklist_users": {},
        },
        "notifications": {
            "success": True,
            "code_not_found": True,
            "mail_error": True,
            "blacklist_hit": False,
            "cooldown_hit": False,
        },
        "templates": _default_templates(),
        "accounts": [],
    }

def _load_config() -> dict:
    with _config_lock:
        if not os.path.exists(CONFIG_FILE):
            cfg = _default_config()
            _save_config(cfg)
            return cfg
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = _default_config()
            changed = False
            if not isinstance(data.get("global"), dict):
                data["global"] = base["global"]
                changed = True
            else:
                for k, v in base["global"].items():
                    if k not in data["global"]:
                        data["global"][k] = v
                        changed = True
            if not isinstance(data.get("notifications"), dict):
                data["notifications"] = base["notifications"]
                changed = True
            else:
                for k, v in base["notifications"].items():
                    if k not in data["notifications"]:
                        data["notifications"][k] = v
                        changed = True
            if not isinstance(data.get("templates"), dict):
                data["templates"] = base["templates"]
                changed = True
            else:
                for k, v in base["templates"].items():
                    if k not in data["templates"]:
                        data["templates"][k] = v
                        changed = True
                    elif isinstance(data["templates"][k], str) and ("<b>" in data["templates"][k] or "<code>" in data["templates"][k]):
                        data["templates"][k] = _clean_template_html(data["templates"][k])
                        changed = True
            if isinstance(data.get("global", {}).get("template"), str) and "<b>" in data["global"]["template"]:
                data["global"]["template"] = _clean_template_html(data["global"]["template"])
                changed = True

            cur_tpl = data.get("templates", {}).get("code_issued", "")
            if cur_tpl and "{sender}" not in cur_tpl and "{time}" not in cur_tpl:
                data["templates"]["code_issued"] = base["templates"]["code_issued"]
                changed = True
            if data.get("global", {}).get("template") and "{sender}" not in data["global"]["template"] and "{time}" not in data["global"]["template"]:
                data["global"]["template"] = base["templates"]["code_issued"]
                changed = True

            for t_key in ("code_issued",):
                val = data.get("templates", {}).get(t_key, "")
                if "📧 Почта: {email}\n" in val:
                    data["templates"][t_key] = val.replace("📧 Почта: {email}\n", "")
                    changed = True
                elif "📧 Почта: {email}" in val:
                    data["templates"][t_key] = val.replace("📧 Почта: {email}", "").strip()
                    changed = True

            glob_tpl = data.get("global", {}).get("template", "")
            if "📧 Почта: {email}\n" in glob_tpl:
                data["global"]["template"] = glob_tpl.replace("📧 Почта: {email}\n", "")
                changed = True
            elif "📧 Почта: {email}" in glob_tpl:
                data["global"]["template"] = glob_tpl.replace("📧 Почта: {email}", "").strip()
                changed = True

            for acc in data.get("accounts", []):
                acc_tpl = acc.get("template", "")
                if "📧 Почта: {email}" in acc_tpl:
                    acc["template"] = acc_tpl.replace("📧 Почта: {email}\n", "").replace("📧 Почта: {email}", "")
                    changed = True

            cnf_tpl = data.get("templates", {}).get("code_not_found", "")
            if cnf_tpl and "повторно через несколько секунд" in cnf_tpl and "на фанпее" not in cnf_tpl:
                data["templates"]["code_not_found"] = cnf_tpl.replace(
                    "повторно через несколько секунд", "на фанпее повторно через несколько секунд"
                )
                changed = True

            bl_users = data["global"].setdefault("blacklist_users", {})
            for n in data["global"].get("blacklist_nicks", []):
                if str(n) not in bl_users:
                    bl_users[str(n)] = {"mode": "all", "blocked_accounts": []}
                    changed = True
            if not isinstance(data.get("accounts"), list):
                data["accounts"] = []
            if changed:
                _save_config(data)
            return data
        except Exception as e:
            logger.error(f"{LP} Ошибка чтения config.json: {e}")
            return _default_config()

def _save_config(cfg: dict) -> bool:
    with _config_lock:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"{LP} Ошибка сохранения config.json: {e}")
            return False

def _normalize_cmd(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("\xa0", " ")
    s = re.sub(r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFE0E\uFE0F\u00AD]", "", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("C"))
    s = "".join(ch for ch in s if not ch.isspace())
    return s.strip().lower()

def _normalize_nick(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).strip()
    if s.startswith("@"):
        s = s[1:].strip()
    return s.casefold()

def _clean_html(raw: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>", "\n", raw or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s).strip()

def _mask_secret(s: str) -> str:
    t = str(s or "").strip()
    if not t:
        return "—"
    if len(t) <= 8:
        return "****"
    return t[:3] + "..." + t[-3:]

STOP_WORDS = {
    "your", "code", "here", "this", "from", "with", "dear", "user", "enter", "copy",
    "into", "that", "valid", "email", "steam", "login", "guard", "security", "access",
    "please", "account", "verification", "service", "support", "button", "click", "reset",
    "valve", "bellevue", "box", "po", "help", "store", "about", "check", "hello", "hi",
    "password", "device", "address", "microsoft", "google", "apple", "discord", "telegram"
}

FALSE_POSITIVES = {
    "98009", "1688", "10001", "90210", "12345", "00000", "11111", "99999", "123456", "000000", "98004", "98052",
    "2024", "2025", "2026", "2027", "1234", "0000", "1111", "9999"
}

NON_CODE_CONTEXT = (
    "box", "po box", "bellevue", "wa", "zip", "suite", "street", "avenue", "address",
    "order", "заказ", "чек", "квитанция", "receipt", "invoice", "сумма", "итог", "total",
    "тел", "phone", "tel", "rub", "usd", "eur", "руб", "id:", "id ", "номер заказа"
)

CODE_INDICATORS = (
    "код", "code", "pin", "пароль", "passcode", "security", "verification", "подтверждения",
    "guard", "безопасности", "одноразовый", "2fa", "двухфакторн", "входа", "login", "auth"
)

def _is_suspicious_context(full_text: str, start_idx: int, end_idx: int) -> bool:
    start = max(0, start_idx - 50)
    end = min(len(full_text), end_idx + 50)
    surrounding = full_text[start:end].lower()
    return any(w in surrounding for w in NON_CODE_CONTEXT)

SERVICES_DETECT = [
    ("steam", "Steam", ["steampowered.com", "valvesoftware.com", "steam guard", " steam"]),
    ("epic", "Epic Games", ["epicgames.com", "epic games"]),
    ("telegram", "Telegram", ["telegram.org", " telegram"]),
    ("discord", "Discord", ["discord.com", " discord"]),
    ("battlenet", "Battle.net / Blizzard", ["blizzard.com", "battle.net"]),
    ("riot", "Riot Games", ["riotgames.com", "riot games", "valorant", "league of legends"]),
    ("ubisoft", "Ubisoft", ["ubi.com", "ubisoft.com", "ubisoft"]),
    ("supercell", "Supercell", ["supercell.com", "supercell"]),
    ("roblox", "Roblox", ["roblox.com", "roblox"]),
    ("microsoft", "Microsoft / Xbox", ["microsoft.com", "accountprotection", "майкрософт", "xbox"]),
    ("google", "Google", ["google.com", "accounts.google"]),
    ("vk", "ВКонтакте", ["vk.com", "вконтакте"]),
]

class CodeExtractor:
    @staticmethod
    def detect_service(subject: str, sender: str, full_text: str = "") -> Tuple[str, str]:
        probe = f"{sender} {subject} {full_text[:300]}".lower()
        for s_key, s_name, patterns in SERVICES_DETECT:
            if any(p in probe for p in patterns):
                return s_key, s_name
        return "all", "Почта"

    @classmethod
    def extract_steam(cls, full_text: str, html_content: str) -> Optional[str]:
        if html_content:
            m = re.search(r'class=[\'"][^\'"]*title-\d+[^\'"]*[\'"][^>]*>\s*([2-9A-Z]{5})\s*<', html_content, re.I)
            if m and m.group(1).upper() not in FALSE_POSITIVES:
                return m.group(1).upper()

            m = re.search(r'font-size:\s*(?:3[0-9]|4[0-9]|5[0-9])px[^>]*>\s*([2-9A-Z]{5})\s*<', html_content, re.I)
            if m and m.group(1).upper() not in FALSE_POSITIVES:
                return m.group(1).upper()

            m = re.search(r'color:\s*#(?:3a9aed|1a9fff)[^>]*>\s*([2-9A-Z]{5})\s*<', html_content, re.I)
            if m and m.group(1).upper() not in FALSE_POSITIVES:
                return m.group(1).upper()

            m = re.search(r'<[^>]+class=[\'"][^\'"]*(?:code|steam-code)[^\'"]*[\'"][^>]*>\s*([2-9A-Z]{5})\s*<', html_content, re.I)
            if m and m.group(1).upper() not in FALSE_POSITIVES:
                return m.group(1).upper()

        m_ctx = re.search(r'(?i)(?:Steam\s+Guard|код(?:[\s\w]*?доступа)?|code(?:\s+is)?|guard|вот\s+ваш\s+код|here\s+is\s+your\s+code)[\s\S]{0,120}?\b([2-9A-Z]{5})\b', full_text)
        if m_ctx:
            val = m_ctx.group(1).upper()
            if val not in FALSE_POSITIVES and val.lower() not in STOP_WORDS:
                return val

        if any(w in full_text.lower() for w in ("steam guard", "код", "guard", "security")):
            for candidate in re.finditer(r'\b([2-9A-Z]{5})\b', full_text):
                val = candidate.group(1).upper()
                if val not in FALSE_POSITIVES and val.lower() not in STOP_WORDS:
                    if any(ch.isalpha() for ch in val) and not _is_suspicious_context(full_text, candidate.start(), candidate.end()):
                        return val
        return None

    @classmethod
    def extract_epic(cls, full_text: str, html_content: str) -> Optional[str]:
        if html_content:
            m = re.search(r'<[^>]+style=[\'"][^\'"]*font-size:\s*(?:2[0-9]|3[0-9]|4[0-9])px[^\'"]*[\'"][^>]*>\s*(\d{6})\s*<', html_content, re.I)
            if m and m.group(1) not in FALSE_POSITIVES:
                return m.group(1)

        m = re.search(r'(?i)(?:код(?:[\s\w]*?безопасности|[\s\w]*?подтверждения)?|security\s+code|verification\s+code|passcode)[\s\S]{0,80}?\b(\d{6})\b', full_text)
        if m and m.group(1) not in FALSE_POSITIVES:
            return m.group(1)

        if any(w in full_text.lower() for w in CODE_INDICATORS):
            for m_solo in re.finditer(r'\b(\d{6})\b', full_text):
                val = m_solo.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m_solo.start(), m_solo.end()):
                    return val
        return None

    @classmethod
    def extract_telegram(cls, full_text: str, html_content: str) -> Optional[str]:
        m = re.search(r'(?i)(?:код(?:[\s\w]*?входа|[\s\w]*?подтверждения)?|login\s+code|code)[\s\S]{0,60}?\b(\d{5})\b', full_text)
        if m and m.group(1) not in FALSE_POSITIVES:
            return m.group(1)

        if any(w in full_text.lower() for w in ("telegram", "код", "login", "вход")):
            for m_solo in re.finditer(r'\b(\d{5})\b', full_text):
                val = m_solo.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m_solo.start(), m_solo.end()):
                    return val
        return None

    @classmethod
    def extract_supercell(cls, full_text: str, html_content: str) -> Optional[str]:
        if html_content:
            m = re.search(r'font-size:\s*(?:2[0-9]|3[0-9]|4[0-9])px[^>]*>\s*(\d{6})\s*<', html_content, re.I)
            if m and m.group(1) not in FALSE_POSITIVES:
                return m.group(1)
        m = re.search(r'(?i)(?:Supercell\s+ID|код|code)[\s\S]{0,80}?\b(\d{6})\b', full_text)
        if m and m.group(1) not in FALSE_POSITIVES:
            return m.group(1)
        if any(w in full_text.lower() for w in CODE_INDICATORS):
            for m_solo in re.finditer(r'\b(\d{6})\b', full_text):
                val = m_solo.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m_solo.start(), m_solo.end()):
                    return val
        return None

    @classmethod
    def extract(cls, service: str, subject: str, sender: str, text: str, html_content: str, custom_filter: str = "") -> Tuple[Optional[str], str, str]:
        full_text = f"{subject}\n{text}\n{_clean_html(html_content)}".strip()
        s_low = str(service or "all").lower()

        detected_key, detected_name = cls.detect_service(subject, sender, full_text)
        effective_service = detected_key if s_low == "all" else s_low
        display_name = detected_name if s_low == "all" else SERVICES.get(s_low, {}).get("name", detected_name)

        if effective_service == "steam":
            code = cls.extract_steam(full_text, html_content)
            if code:
                return code, "steam", "Steam"

        elif effective_service in ("epic", "epicgames"):
            code = cls.extract_epic(full_text, html_content)
            if code:
                return code, "epic", "Epic Games"

        elif effective_service in ("telegram", "tg"):
            code = cls.extract_telegram(full_text, html_content)
            if code:
                return code, "telegram", "Telegram"

        elif effective_service == "supercell":
            code = cls.extract_supercell(full_text, html_content)
            if code:
                return code, "supercell", "Supercell"

        elif effective_service in ("discord", "battlenet", "blizzard", "riot", "ubisoft", "roblox", "vk"):
            m = re.search(r'(?i)(?:код(?:[\s\w]*?входа|[\s\w]*?подтверждения|[\s\w]*?безопасности)?|verification\s+code|security\s+code|login\s+code|passcode|pin|code)[\s\S]{0,80}?\b([A-Za-z0-9]{6,8})\b', full_text)
            if m and m.group(1) not in FALSE_POSITIVES and m.group(1).lower() not in STOP_WORDS:
                return m.group(1), effective_service, display_name

            if html_content:
                m_h = re.search(r'font-size:\s*(?:2[0-9]|3[0-9]|4[0-9])px[^>]*>\s*([A-Za-z0-9]{6,8})\s*<', html_content, re.I)
                if m_h and m_h.group(1) not in FALSE_POSITIVES and m_h.group(1).lower() not in STOP_WORDS:
                    return m_h.group(1), effective_service, display_name

            if any(w in full_text.lower() for w in CODE_INDICATORS):
                for m_solo in re.finditer(r'\b(\d{6})\b', full_text):
                    val = m_solo.group(1)
                    if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m_solo.start(), m_solo.end()):
                        return val, effective_service, display_name

        elif effective_service == "microsoft":
            m = re.search(r'(?i)(?:код\s+безопасности|security\s+code|код|code)[\s\S]{0,80}?\b(\d{6,7})\b', full_text)
            if m and m.group(1) not in FALSE_POSITIVES:
                return m.group(1), "microsoft", "Microsoft / Xbox"
            if any(w in full_text.lower() for w in ("код безопасности", "security code", "код проверки")):
                for m_solo in re.finditer(r'\b(\d{6,7})\b', full_text):
                    val = m_solo.group(1)
                    if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m_solo.start(), m_solo.end()):
                        return val, "microsoft", "Microsoft / Xbox"

        elif effective_service == "google":
            m = re.search(r'(?i)G-(\d{6})\b', full_text)
            if m:
                return m.group(1), "google", "Google / Gmail"
            m2 = re.search(r'(?i)(?:код|code|verification)[\s\S]{0,80}?\b(\d{6})\b', full_text)
            if m2 and m2.group(1) not in FALSE_POSITIVES:
                return m2.group(1), "google", "Google / Gmail"

        elif effective_service == "custom" and custom_filter:
            cf = custom_filter.strip()
            try:
                m_custom = re.search(cf, full_text, re.I)
                if m_custom:
                    val = m_custom.group(1) if m_custom.groups() else m_custom.group(0)
                    return val, "custom", "Свой фильтр"
            except Exception:
                pass

        m_ctx = re.search(r'(?i)(?:код(?:[\s\w]*?подтверждения|[\s\w]*?безопасности|[\s\w]*?доступа)?|verification\s+code|security\s+code|login\s+code|passcode|pin|code)(?:[\s\w]*?[:=—–-]|[\s]+(?:is|это))[\s]*([A-Za-z0-9]{4,8})\b', full_text)
        if m_ctx:
            val = m_ctx.group(1)
            if val not in FALSE_POSITIVES and val.lower() not in STOP_WORDS and not (val.isalpha() and val.islower()):
                return val, detected_key, detected_name

        if html_content:
            m_b = re.search(r'<(?:h[1-3]|span class=[\'"][^\'"]*code[^\'"]*[\'"])[^>]*>\s*([A-Za-z0-9]{4,8})\s*</', html_content, re.I)
            if m_b:
                val = m_b.group(1)
                if val not in FALSE_POSITIVES and val.lower() not in STOP_WORDS and not (val.isalpha() and val.islower()):
                    return val, detected_key, detected_name

        if any(w in full_text.lower() for w in CODE_INDICATORS):
            for m6 in re.finditer(r'\b(\d{6})\b', full_text):
                val = m6.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m6.start(), m6.end()):
                    return val, detected_key, detected_name

            for m5 in re.finditer(r'\b(\d{5})\b', full_text):
                val = m5.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m5.start(), m5.end()):
                    return val, detected_key, detected_name

            for m4 in re.finditer(r'\b(\d{4})\b', full_text):
                val = m4.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m4.start(), m4.end()):
                    try:
                        num = int(val)
                        if 1990 <= num <= 2035:
                            continue
                    except Exception:
                        pass
                    return val, detected_key, detected_name

            for m78 in re.finditer(r'\b(\d{7,8})\b', full_text):
                val = m78.group(1)
                if val not in FALSE_POSITIVES and not _is_suspicious_context(full_text, m78.start(), m78.end()):
                    return val, detected_key, detected_name

        return None, detected_key, detected_name

def _format_proxy_for_requests(proxy_str: str) -> Optional[dict]:
    p = str(proxy_str or "").strip()
    if not p:
        return None
    if "://" not in p:
        parts = p.split(":")
        if len(parts) == 4:
            p = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
        elif len(parts) == 2:
            p = f"http://{parts[0]}:{parts[1]}"
        else:
            p = f"http://{p}"
    return {"http": p, "https": p}

AUTH_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
LIVE_CLIENT_ID = "0000000040170455"

_oauth_server: Optional[HTTPServer] = None
_oauth_server_thread: Optional[threading.Thread] = None
_oauth_server_port: int = 8543

def get_outlook_auto_auth_url(chat_id: Optional[int] = None, action: str = "wizard", acc_id: Optional[str] = None) -> str:
    _start_oauth_server()
    port = _oauth_server_port or 8543
    redirect_uri = f"http://localhost:{port}"
    state_payload = f"{chat_id or 0}:{action}:{acc_id or 'none'}"
    params = {
        "client_id": AUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
        "prompt": "select_account",
        "state": state_payload,
    }
    return "https://login.live.com/oauth20_authorize.srf?" + urllib.parse.urlencode(params)

LIVE_AUTH_URL = (
    "https://login.live.com/oauth20_authorize.srf"
    f"?client_id={LIVE_CLIENT_ID}"
    "&response_type=code"
    "&redirect_uri=https://login.microsoftonline.com/common/oauth2/nativeclient"
    "&scope=https://graph.microsoft.com/Mail.Read%20offline_access"
)

class OutlookClient:
    PUBLIC_CLIENT_IDS = [
        "14d82eec-204b-4c2f-b7e8-296a70dab67e",
        "0000000040170455",
        "08162f7c-0242-4814-a5ed-eb70b59f7435",
        "d3590ed6-52b3-4102-aeff-aad2292ab01c",
        "e8abda48-c7f7-418c-851b-417dac19566a",
    ]

    def __init__(self, acc: dict):
        self.acc = acc
        self.email = str(acc.get("email") or "").strip()
        self.refresh_token = str(acc.get("refresh_token") or "").strip()
        self.client_id = str(acc.get("client_id") or self.PUBLIC_CLIENT_IDS[0]).strip()
        self.client_secret = str(acc.get("client_secret") or "").strip()
        self.password = str(acc.get("password") or "").strip()
        self.proxy = str(acc.get("proxy") or "").strip()

    def get_access_token(self) -> Tuple[bool, str]:
        if not self.refresh_token:
            if self.password:
                logger.info(f"{LP} [Outlook] Нет Refresh Token для {self.email}, запуск веб-авторизации...")
                ok, token_or_err = self.try_web_auth_exchange()
                if ok:
                    return True, token_or_err
                return False, f"Требуется Refresh Token: {token_or_err}"
            return False, "Отсутствует Refresh Token для Outlook/Hotmail."

        proxies = _format_proxy_for_requests(self.proxy)
        token_urls = [
            "https://login.live.com/oauth20_token.srf",
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        ]
        client_ids_to_try = [self.client_id] + [cid for cid in self.PUBLIC_CLIENT_IDS if cid != self.client_id]

        last_err = ""
        for cid in client_ids_to_try:
            for url in token_urls:
                data = {
                    "client_id": cid,
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "scope": "https://graph.microsoft.com/Mail.Read offline_access",
                }
                if self.client_secret:
                    data["client_secret"] = self.client_secret
                try:
                    resp = requests.post(url, data=data, proxies=proxies, timeout=15)
                    if resp.status_code == 200:
                        payload = resp.json()
                        access_token = payload.get("access_token")
                        new_refresh = payload.get("refresh_token")
                        if new_refresh and new_refresh != self.refresh_token:
                            self.refresh_token = new_refresh
                            self.acc["refresh_token"] = new_refresh
                            self.acc["client_id"] = cid
                            cfg = _load_config()
                            for a in cfg.get("accounts", []):
                                if a.get("id") == self.acc.get("id"):
                                    a["refresh_token"] = new_refresh
                                    a["client_id"] = cid
                                    break
                            _save_config(cfg)
                            logger.info(f"{LP} [Outlook] Refresh Token обновлен и сохранен для {self.email} (cid={cid[:8]})")
                        if access_token:
                            return True, access_token
                    else:
                        err_payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        desc = err_payload.get("error_description", resp.text[:150])
                        last_err = f"HTTP {resp.status_code}: {desc}"
                except Exception as e:
                    last_err = str(e)

        logger.warning(f"{LP} [Outlook] Не удалось обновить токен для {self.email}: {last_err}")
        return False, f"Ошибка обновления токена: {last_err}"

    def try_web_auth_exchange(self) -> Tuple[bool, str]:
        if not self.email or not self.password:
            return False, "Не указан email или пароль."
        proxies = _format_proxy_for_requests(self.proxy)
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })

        try:
            auth_url = LIVE_AUTH_URL
            logger.info(f"{LP} [Outlook] Запрос сессии live.com для {self.email}...")
            r1 = sess.get(auth_url, proxies=proxies, timeout=15)

            m_ppft = re.search(r'value=\\?["\']([^"\\&]+)\\?["\'][^>]*name=\\?["\']PPFT\\?["\']', r1.text)
            if not m_ppft:
                m_ppft = re.search(r'name=\\?["\']PPFT\\?["\'][^>]*value=\\?["\']([^"\\&]+)\\?["\']', r1.text)
            if not m_ppft:
                m_ppft = re.search(r'["\']?sFTTag["\']?\s*:\s*["\'].*?value=\\?["\']([^"\\&]+)\\?["\']', r1.text)
            if not m_ppft:
                m_ppft = re.search(r'name=["\']PPFT["\']\s+value=["\']([^"\']+)["\']', r1.text)

            m_url = re.search(r'["\']?urlPost["\']?\s*:\s*["\']([^"\']+)["\']', r1.text)

            if not m_ppft or not m_url:
                logger.warning(f"{LP} [Outlook] Токен PPFT не найден на live.com (len={len(r1.text)})")
                return False, "Не удалось получить сессионный токен PPFT от Microsoft. Рекомендуется авторизоваться через браузер."

            ppft = m_ppft.group(1)
            url_post = html.unescape(m_url.group(1))
            logger.info(f"{LP} [Outlook] PPFT получен успешно (len={len(ppft)}). Отправка логина и пароля...")

            post_data = {
                "login": self.email,
                "loginfmt": self.email,
                "passwd": self.password,
                "PPFT": ppft,
                "PPSX": "Passport",
                "NewUser": "1",
                "type": "11",
                "LoginOptions": "3",
                "i13": "0",
            }
            sess.headers.update({
                "Referer": auth_url,
                "Content-Type": "application/x-www-form-urlencoded",
            })
            r2 = sess.post(url_post, data=post_data, proxies=proxies, allow_redirects=True, timeout=20)
            logger.info(f"{LP} [Outlook] Ответ после логина: status={r2.status_code}, url={r2.url[:80]}")

            code = None
            candidate_urls = [r2.url] + [getattr(h, "url", "") for h in r2.history] + [str(getattr(h, "headers", {}).get("Location", "")) for h in r2.history]
            for u in candidate_urls:
                if "code=" in u:
                    m_c = re.search(r"[?&#]code=([A-Za-z0-9_\.\-]+)", u)
                    if m_c:
                        code = m_c.group(1)
                        break
            if not code and "code=" in r2.text:
                m_c = re.search(r"[?&#]code=([A-Za-z0-9_\.\-]+)", r2.text)
                if m_c:
                    code = m_c.group(1)

            if not code and ("i0118" in r2.text or ("password" in r2.text.lower() and "incorrect" in r2.text.lower())):
                logger.warning(f"{LP} [Outlook] Неверный пароль от {self.email}")
                return False, "Неверный пароль от учетной записи Microsoft."

            if not code and ("identity/confirm" in r2.text or "proof" in r2.text or "TwoFactor" in r2.text or "Consent" in r2.text):
                logger.warning(f"{LP} [Outlook] Microsoft требует подтверждение безопасности (2FA/Consent) для {self.email}")
                return False, "Microsoft требует подтверждения безопасности или согласие на доступ. Нажмите «🔑 Авторизация / Refresh Token» -> «Войти в браузере»."

            if not code:
                logger.warning(f"{LP} [Outlook] Microsoft не вернул OAuth-код для {self.email}")
                return False, "Microsoft не выдал OAuth-код. Рекомендуется использовать вход через браузер."

            logger.info(f"{LP} [Outlook] OAuth-код получен, обмен на Refresh Token...")
            token_data = {
                "client_id": LIVE_CLIENT_ID,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://login.microsoftonline.com/common/oauth2/nativeclient",
                "scope": "https://graph.microsoft.com/Mail.Read offline_access",
            }

            token_urls = [
                "https://login.live.com/oauth20_token.srf",
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            ]
            for t_url in token_urls:
                try:
                    r3 = sess.post(t_url, data=token_data, proxies=proxies, timeout=15)
                    if r3.status_code == 200:
                        payload = r3.json()
                        acc_token = payload.get("access_token")
                        ref_token = payload.get("refresh_token")
                        if ref_token:
                            self.refresh_token = ref_token
                            self.acc["refresh_token"] = ref_token
                            self.acc["client_id"] = LIVE_CLIENT_ID
                            cfg = _load_config()
                            for a in cfg.get("accounts", []):
                                if a.get("id") == self.acc.get("id"):
                                    a["refresh_token"] = ref_token
                                    a["client_id"] = LIVE_CLIENT_ID
                                    break
                            _save_config(cfg)
                            logger.info(f"{LP} [Outlook] Авторизация успешна! Refresh Token сохранен для {self.email}")
                        if acc_token:
                            return True, acc_token
                except Exception as ex:
                    logger.debug(f"{LP} [Outlook] Ошибка POST {t_url}: {ex}")

            return False, f"Ошибка обмена кода на токен: {r3.text[:200]}"
        except Exception as e:
            logger.warning(f"{LP} [Outlook] Ошибка веб-авторизации: {e}")
            return False, f"Ошибка веб-авторизации Outlook: {e}"

    @staticmethod
    def exchange_code_or_url(raw_input: str, proxy: Optional[str] = None, redirect_uri: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
        s = str(raw_input or "").strip()
        if not s:
            return False, "Получена пустая строка.", None

        proxies = _format_proxy_for_requests(proxy or "")
        code = None
        if "code=" in s:
            m = re.search(r"[?&#]code=([A-Za-z0-9_\.\-]+)", s)
            if m:
                code = m.group(1)
        elif s.startswith("M.C") or s.startswith("M.R") or (len(s) < 120 and " " not in s and ":" not in s and "." in s):
            code = s

        if code:
            logger.info(f"{LP} [OAuth Exchange] Обмен кода авторизации: {code[:15]}...")

            red_uri = redirect_uri
            if not red_uri:
                if "localhost:" in s or "127.0.0.1:" in s:
                    m_port = re.search(r"http://(?:localhost|127\.0\.0\.1):(\d+)", s)
                    if m_port:
                        red_uri = f"http://localhost:{m_port.group(1)}"
                elif "nativeclient" in s:
                    red_uri = "https://login.microsoftonline.com/common/oauth2/nativeclient"
                else:
                    port = _oauth_server_port or 8543
                    red_uri = f"http://localhost:{port}"

            candidate_uris = [red_uri]
            for alt in [f"http://localhost:{_oauth_server_port or 8543}", "https://login.microsoftonline.com/common/oauth2/nativeclient"]:
                if alt and alt not in candidate_uris:
                    candidate_uris.append(alt)

            endpoints = [
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "https://login.live.com/oauth20_token.srf",
                "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            ]
            last_err = ""
            candidate_cids = [
                AUTH_CLIENT_ID,
                LIVE_CLIENT_ID,
                "08162f7c-0242-4814-a5ed-eb70b59f7435",
                "d3590ed6-52b3-4102-aeff-aad2292ab01c",
            ]
            last_err = ""
            for cur_cid in candidate_cids:
                for cur_uri in candidate_uris:
                    token_data = {
                        "client_id": cur_cid,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": cur_uri,
                        "scope": "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
                    }
                    for ep in endpoints:
                        try:
                            r = requests.post(ep, data=token_data, proxies=proxies, timeout=15)
                            if r.status_code == 200:
                                payload = r.json()
                                ref_token = payload.get("refresh_token")
                                acc_token = payload.get("access_token")
                                if ref_token:
                                    email = None
                                    if acc_token:
                                        try:
                                            r_me = requests.get("https://graph.microsoft.com/v1.0/me", headers={"Authorization": f"Bearer {acc_token}"}, proxies=proxies, timeout=10)
                                            if r_me.status_code == 200:
                                                email = r_me.json().get("mail") or r_me.json().get("userPrincipalName")
                                        except Exception:
                                            pass
                                        if not email:
                                            try:
                                                r_msg = requests.get("https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=toRecipients", headers={"Authorization": f"Bearer {acc_token}"}, proxies=proxies, timeout=10)
                                                if r_msg.status_code == 200:
                                                    m_ctx = re.search(r"users\('([^']+)'\)", r_msg.text)
                                                    if m_ctx:
                                                        email = urllib.parse.unquote(m_ctx.group(1))
                                            except Exception:
                                                pass
                                    return True, ref_token, email
                            else:
                                last_err = r.text[:200]
                        except Exception as ex:
                            last_err = str(ex)

            return False, f"Не удалось обменять код на токен: {last_err}", None

        candidate_token = s.split(":")[-1].strip() if ":" in s else s
        if len(candidate_token) > 40:
            logger.info(f"{LP} [OAuth Exchange] Проверка переданного Refresh Token...")
            for cid in OutlookClient.PUBLIC_CLIENT_IDS:
                for ep in ["https://login.live.com/oauth20_token.srf", "https://login.microsoftonline.com/common/oauth2/v2.0/token"]:
                    try:
                        r = requests.post(ep, data={
                            "client_id": cid,
                            "grant_type": "refresh_token",
                            "refresh_token": candidate_token,
                            "scope": "https://graph.microsoft.com/Mail.Read offline_access",
                        }, proxies=proxies, timeout=15)
                        if r.status_code == 200:
                            payload = r.json()
                            acc_token = payload.get("access_token")
                            new_ref = payload.get("refresh_token") or candidate_token
                            email = None
                            if acc_token:
                                try:
                                    r_me = requests.get("https://graph.microsoft.com/v1.0/me", headers={"Authorization": f"Bearer {acc_token}"}, proxies=proxies, timeout=10)
                                    if r_me.status_code == 200:
                                        email = r_me.json().get("mail") or r_me.json().get("userPrincipalName")
                                except Exception:
                                    pass
                                if not email:
                                    try:
                                        r_msg = requests.get("https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=toRecipients", headers={"Authorization": f"Bearer {acc_token}"}, proxies=proxies, timeout=10)
                                        if r_msg.status_code == 200:
                                            m_ctx = re.search(r"users\('([^']+)'\)", r_msg.text)
                                            if m_ctx:
                                                email = urllib.parse.unquote(m_ctx.group(1))
                                    except Exception:
                                        pass
                            return True, new_ref, email
                    except Exception:
                        pass

            return True, candidate_token, None

        return False, "Некорректная ссылка или токен. Вставьте ссылку с кодом из браузера или refresh_token.", None

    def test_connection(self) -> Tuple[bool, str, float]:
        t0 = time.time()
        logger.info(f"{LP} [Outlook] Проверка соединения с {self.email}...")
        ok, token_or_err = self.get_access_token()
        if not ok:
            return False, token_or_err, round((time.time() - t0) * 1000, 1)

        proxies = _format_proxy_for_requests(self.proxy)
        headers = {"Authorization": f"Bearer {token_or_err}", "Accept": "application/json"}
        url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox?$select=displayName,totalItemCount"
        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=15)
            latency = round((time.time() - t0) * 1000, 1)
            if resp.status_code == 200:
                info = resp.json()
                total = info.get("totalItemCount", 0)
                return True, f"OK (папка Входящие: {total} писем)", latency
            return False, f"HTTP {resp.status_code}: {resp.text[:150]}", latency
        except Exception as e:
            return False, str(e), round((time.time() - t0) * 1000, 1)

    def fetch_latest_code(self, max_age_min: int = 15) -> Tuple[bool, str, dict]:
        ok, token_or_err = self.get_access_token()
        if not ok:
            return False, f"Ошибка авторизации: {token_or_err}", {}

        proxies = _format_proxy_for_requests(self.proxy)
        headers = {"Authorization": f"Bearer {token_or_err}", "Accept": "application/json"}
        url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=10&$orderby=receivedDateTime%20desc"

        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=15)
            if resp.status_code != 200:
                return False, f"Ошибка Graph API: HTTP {resp.status_code}", {}
            messages = resp.json().get("value", [])
            if not messages:
                return False, "Папка Входящие пуста.", {}

            service = self.acc.get("service", "all")
            custom_filter = self.acc.get("custom_filter", "")
            now = datetime.now(timezone.utc)
            max_delta_sec = max_age_min * 60

            for msg in messages:
                dt_str = msg.get("receivedDateTime")
                if dt_str:
                    try:
                        msg_time = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        if (now - msg_time).total_seconds() > max_delta_sec:
                            continue
                    except Exception:
                        pass

                subject = str(msg.get("subject") or "")
                sender_addr = str(msg.get("from", {}).get("emailAddress", {}).get("address") or "")
                sender_name = str(msg.get("from", {}).get("emailAddress", {}).get("name") or "")
                sender_display = f"{sender_name} <{sender_addr}>" if sender_name and sender_name != sender_addr else sender_addr
                sender = sender_display or sender_addr

                body_dict = msg.get("body", {})
                body_type = body_dict.get("contentType", "")
                body_content = str(body_dict.get("content") or "")
                body_preview = str(msg.get("bodyPreview") or "")

                text = body_preview if body_type == "html" else body_content
                html_c = body_content if body_type == "html" else ""

                if service != "all" and service != "custom":
                    hint = SERVICES.get(service, {}).get("sender_hint", "").lower()
                    if hint and (hint not in sender.lower() and hint not in subject.lower()):
                        continue

                code, s_key, s_name = CodeExtractor.extract(service, subject, sender, text, html_c, custom_filter)
                if code:
                    formatted_time = _format_datetime(dt_str)
                    return True, code, {
                        "subject": subject,
                        "sender": sender,
                        "time": formatted_time,
                        "service": s_name,
                        "service_key": s_key,
                        "preview": body_preview[:120],
                    }

            return False, f"Свежее письмо с кодом ({service}) не найдено за последние {max_age_min} мин.", {}
        except Exception as e:
            return False, f"Ошибка запроса Graph API: {e}", {}

class IMAPMailClient:
    def __init__(self, acc: dict):
        self.acc = acc
        self.email = str(acc.get("email") or "").strip()
        self.password = str(acc.get("password") or "").strip()
        self.host = str(acc.get("imap_host") or "").strip()
        self.port = int(acc.get("imap_port") or 993)
        self.proxy = str(acc.get("proxy") or "").strip()

    def _create_imap_connection(self) -> imaplib.IMAP4_SSL:

        if self.proxy and socks is not None:
            p = self.proxy.strip()
            proxy_type = socks.SOCKS5 if p.startswith("socks5") else socks.HTTP
            p_clean = re.sub(r"^[a-zA-Z0-9]+://", "", p)
            auth_part, _, host_part = p_clean.rpartition("@") if "@" in p_clean else ("", "", p_clean)
            host, port_s = host_part.split(":")
            username, password = auth_part.split(":") if ":" in auth_part else (None, None)

            s = socks.socksocket()
            s.set_proxy(proxy_type, host, int(port_s), username=username, password=password)
            s.settimeout(20)
            s.connect((self.host, self.port))

            import ssl
            ctx = ssl.create_default_context()
            sock_ssl = ctx.wrap_socket(s, server_hostname=self.host)
            client = imaplib.IMAP4_SSL(self.host, self.port)
            client.sock = sock_ssl
            return client

        return imaplib.IMAP4_SSL(self.host, self.port, timeout=20)

    def test_connection(self) -> Tuple[bool, str, float]:
        t0 = time.time()
        client = None
        logger.info(f"{LP} [IMAP] Проверка подключения к {self.host}:{self.port} ({self.email})...")
        try:
            client = self._create_imap_connection()
            typ, data = client.login(self.email, self.password)
            latency = round((time.time() - t0) * 1000, 1)
            if typ == "OK":
                client.select("INBOX", readonly=True)
                client.logout()
                logger.info(f"{LP} [IMAP] Авторизация успешна для {self.email} ({latency} ms)")
                return True, "OK (Авторизация успешна)", latency
            logger.warning(f"{LP} [IMAP] Ошибка авторизации {self.email}: {typ} {data}")
            return False, f"IMAP ответ: {typ} {data}", latency
        except Exception as e:
            latency = round((time.time() - t0) * 1000, 1)
            err_msg = str(e)
            if "Basic authentication is disabled" in err_msg:
                err_msg = "Microsoft отключил базовую аутентификацию по паролю! Для Outlook используйте режим Graph API."
            elif "Application-specific password required" in err_msg or "Invalid credentials" in err_msg:
                err_msg = "Неверный пароль. Для Gmail/Yandex/Mail.ru создайте 'Пароль приложений'."
            logger.warning(f"{LP} [IMAP] Исключение при подключении к {self.email}: {err_msg}")
            return False, err_msg, latency
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def fetch_latest_code(self, max_age_min: int = 15) -> Tuple[bool, str, dict]:
        client = None
        try:
            client = self._create_imap_connection()
            typ, _ = client.login(self.email, self.password)
            if typ != "OK":
                return False, "Ошибка входа в IMAP.", {}

            client.select("INBOX", readonly=True)
            res, data = client.search(None, "ALL")
            if res != "OK" or not data or not data[0]:
                return False, "Папка Входящие пуста.", {}

            msg_ids = data[0].split()

            recent_ids = msg_ids[-10:][::-1]

            service = self.acc.get("service", "all")
            custom_filter = self.acc.get("custom_filter", "")
            now = datetime.now(timezone.utc)
            max_delta_sec = max_age_min * 60

            for mid in recent_ids:
                res, fetch_data = client.fetch(mid, "(RFC822)")
                if res != "OK" or not fetch_data:
                    continue

                raw_email = None
                for part in fetch_data:
                    if isinstance(part, tuple) and len(part) > 1:
                        raw_email = part[1]
                        break

                if not raw_email:
                    continue

                msg = email.message_from_bytes(raw_email)

                date_hdr = msg.get("Date")
                msg_time = None
                if date_hdr:
                    try:
                        msg_time = email.utils.parsedate_to_datetime(date_hdr)
                        if msg_time.tzinfo is None:
                            msg_time = msg_time.replace(tzinfo=timezone.utc)
                        if (now - msg_time).total_seconds() > max_delta_sec:
                            continue
                    except Exception:
                        pass

                subject = self._decode_header(msg.get("Subject", ""))
                sender = self._decode_header(msg.get("From", ""))

                if service != "all" and service != "custom":
                    hint = SERVICES.get(service, {}).get("sender_hint", "").lower()
                    if hint and (hint not in sender.lower() and hint not in subject.lower()):
                        continue

                body_text, body_html = self._extract_body(msg)

                code, s_key, s_name = CodeExtractor.extract(service, subject, sender, body_text, body_html, custom_filter)
                if code:
                    formatted_time = _format_datetime(msg_time or date_hdr)
                    return True, code, {
                        "subject": subject,
                        "sender": sender,
                        "time": formatted_time,
                        "service": s_name,
                        "service_key": s_key,
                        "preview": (body_text or _clean_html(body_html))[:120],
                    }

            return False, f"Свежее письмо с кодом ({service}) не найдено за последние {max_age_min} мин.", {}
        except Exception as e:
            return False, f"Ошибка IMAP: {e}", {}
        finally:
            if client:
                try:
                    client.close()
                    client.logout()
                except Exception:
                    pass

    @staticmethod
    def _decode_header(header_val: str) -> str:
        if not header_val:
            return ""
        decoded_parts = []
        for part, enc in email.header.decode_header(header_val):
            if isinstance(part, bytes):
                try:
                    decoded_parts.append(part.decode(enc or "utf-8", errors="ignore"))
                except Exception:
                    decoded_parts.append(part.decode("latin1", errors="ignore"))
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts)

    @staticmethod
    def _extract_body(msg: email.message.Message) -> Tuple[str, str]:
        text_parts = []
        html_parts = []
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="ignore")
                except Exception:
                    decoded = payload.decode("latin1", errors="ignore")
                if ctype == "text/plain":
                    text_parts.append(decoded)
                elif ctype == "text/html":
                    html_parts.append(decoded)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="ignore")
                except Exception:
                    decoded = payload.decode("latin1", errors="ignore")
                if msg.get_content_type() == "text/html":
                    html_parts.append(decoded)
                else:
                    text_parts.append(decoded)
        return "\n".join(text_parts), "\n".join(html_parts)

class MailManager:
    @staticmethod
    def get_client(acc: dict):
        provider = acc.get("provider", "outlook")
        if provider == "outlook":
            return OutlookClient(acc)
        return IMAPMailClient(acc)

    @staticmethod
    def test_connection(acc: dict) -> Tuple[bool, str, float]:
        email_str = acc.get("email", "unknown")
        prov_str = acc.get("provider", "outlook")
        logger.info(f"{LP} [TEST] Запуск проверки подключения к {email_str} ({prov_str})...")
        client = MailManager.get_client(acc)
        ok, msg, lat = client.test_connection()
        if ok:
            logger.info(f"{LP} [TEST] Результат проверки {email_str}: OK ({msg}) за {lat} ms")
        else:
            logger.warning(f"{LP} [TEST] Результат проверки {email_str}: ОШИБКА ({msg}) за {lat} ms")
        return ok, msg, lat

    @staticmethod
    def fetch_code(acc: dict, max_age_min: int = 15) -> Tuple[bool, str, dict]:
        email_str = acc.get("email", "unknown")
        serv_str = acc.get("service", "all")
        logger.info(f"{LP} [FETCH] Поиск письма с кодом для {email_str} (сервис: {serv_str}, окно: {max_age_min} мин)...")
        client = MailManager.get_client(acc)
        ok, code_or_err, meta = client.fetch_latest_code(max_age_min=max_age_min)
        if ok:
            logger.info(f"{LP} [FETCH] УСПЕХ для {email_str}: код={_mask_secret(code_or_err)}, тема='{meta.get('subject')}'")
        else:
            logger.warning(f"{LP} [FETCH] Письмо не найдено для {email_str}: {code_or_err}")
        return ok, code_or_err, meta

def _is_exact_command_message(raw_text: str, cfg: Optional[dict] = None) -> bool:
    text = _normalize_cmd(raw_text)
    if not text:
        return False
    if cfg is None:
        cfg = _load_config()
    if not cfg.get("global", {}).get("plugin_enabled", True):
        return False
    global_notify = cfg.get("global", {}).get("command_notifications_enabled", False)
    for acc in cfg.get("accounts", []):
        if not acc.get("enabled", True):
            continue
        acc_cmd = _normalize_cmd(acc.get("command", ""))
        if acc_cmd and text == acc_cmd:
            acc_notify = acc.get("command_notifications_enabled", global_notify)

            if not acc_notify:
                return True
    return False

def _mark_recent_suppression():
    global _suppress_own_notification_until
    with _suppress_lock:
        _suppress_own_notification_until = time.time() + 4.0

def _is_recently_suppressed() -> bool:
    with _suppress_lock:
        return time.time() <= _suppress_own_notification_until

def _filter_notification_text(text: str, cfg: Optional[dict] = None) -> Tuple[bool, str]:
    if not text:
        return True, text
    if cfg is None:
        cfg = _load_config()
    if not cfg.get("global", {}).get("plugin_enabled", True):
        return True, text

    if _is_exact_command_message(text, cfg):
        return False, ""

    blocks = [b.strip() for b in re.split(r"(?:\r?\n){2,}", text) if b.strip()]
    kept = []
    has_suppressed = False
    for block in blocks:
        codes = re.findall(r"<code>(.*?)</code>", block, flags=re.I | re.S)
        if codes and any(_is_exact_command_message(_clean_html(c), cfg) for c in codes):
            has_suppressed = True
            continue
        if _is_exact_command_message(_clean_html(block), cfg):
            has_suppressed = True
            continue
        kept.append(block)

    if has_suppressed and not kept:
        return False, ""
    return True, "\n\n".join(kept)

def _patch_telegram_notifications(cardinal: Cardinal):
    tg = getattr(cardinal, "telegram", None)
    if not tg:
        return
    orig_send = getattr(tg, "send_notification", None)
    if not callable(orig_send) or getattr(orig_send, "_mcp_wrapped", False):
        return

    def wrapped_send_notification(*args, **kwargs):
        try:
            if args:
                raw_text = str(args[0] or "")
                allowed, filtered_text = _filter_notification_text(raw_text)
                if not allowed:
                    _log_event("telegram_notification_suppressed", logging.DEBUG, text=raw_text[:80])
                    return None
                if filtered_text != raw_text:
                    args = (filtered_text,) + args[1:]
            return orig_send(*args, **kwargs)
        except Exception as e:
            logger.error(f"{LP} Ошибка фильтра уведомлений: {e}")
            return orig_send(*args, **kwargs)

    wrapped_send_notification._mcp_wrapped = True
    wrapped_send_notification._mcp_orig = orig_send
    setattr(tg, "send_notification", wrapped_send_notification)
    _log_event("notification_patch_installed", logging.DEBUG)

def _send_tg_notification(cardinal: Optional[Cardinal], text: str):
    try:
        c = cardinal or cardinal_instance
        tg = getattr(c, "telegram", None) if c else None
        bot = getattr(tg, "bot", None) or bot_instance
        if not bot:
            return
        auth = getattr(tg, "authorized_users", None) if tg else None
        target_ids = list(auth.keys()) if isinstance(auth, dict) and auth else ([admin_chat_id] if admin_chat_id else [])
        for cid in target_ids:
            try:
                bot.send_message(int(cid), text, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"{LP} Ошибка отправки TG уведомления: {e}")

def _is_authorized(user_id: Optional[int]) -> bool:
    if not user_id:
        return True
    auth = getattr(getattr(cardinal_instance, "telegram", None), "authorized_users", None)
    if isinstance(auth, dict) and auth:
        return int(user_id) in auth or str(user_id) in auth
    return True

def _make_kb(rows: List[List[Tuple[str, str]]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    for row in rows:
        btns = []
        for t, cd in row:
            if cd.startswith("http://") or cd.startswith("https://") or cd.startswith("tg://"):
                btns.append(InlineKeyboardButton(text=t, url=cd))
            else:
                btns.append(InlineKeyboardButton(text=t, callback_data=cd))
        kb.row(*btns)
    return kb

def _tg_send(chat_id: int, text: str, reply_markup=None):
    if not bot_instance or not chat_id:
        return None
    try:
        return bot_instance.send_message(
            chat_id, text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True
        )
    except Exception as e:
        logger.warning(f"{LP} Ошибка send_message: {e}")
        return None

def _tg_edit(chat_id: int, message_id: int, text: str, reply_markup=None):
    if not bot_instance or not chat_id or not message_id:
        return None
    try:
        return bot_instance.edit_message_text(
            text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True
        )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return None
        return _tg_send(chat_id, text, reply_markup=reply_markup)

def _answer_cb(call, text: Optional[str] = None, alert: bool = False):
    try:
        if bot_instance:
            bot_instance.answer_callback_query(call.id, text=text, show_alert=alert)
    except Exception:
        pass

def _about_text() -> str:
    return (
        f"🧩 <b>Плагин:</b> {NAME}\n"
        f"📦 <b>Версия:</b> {VERSION}\n"
        f"👤 <b>Автор:</b> {CREDITS}\n\n"
        "Выберите раздел ниже."
    )

def _home_kb() -> InlineKeyboardMarkup:
    return _make_kb([
        [("⚙️ Настройки", "mcp_settings"), ("ℹ️ Информация", "mcp_info")],
        [("⬆️ Обновить плагин", "mcp_update_menu"), ("🗑 Удалить", "mcp_delete_ask")],
        [("🔙 К списку плагинов", CB_PLUGINS_LIST_OPEN)],
    ])

def _open_home(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _about_text()
    kb = _home_kb()
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _settings_text(chat_id: int) -> str:
    cfg = _load_config()
    glob = cfg.get("global", {})
    plugin_state = "🟢 Включено" if glob.get("plugin_enabled", True) else "🔴 Выключено"
    accs = cfg.get("accounts", [])
    total_accs = len(accs)
    active_accs = sum(1 for a in accs if a.get("enabled", True))
    cmd_notif = glob.get("command_notifications_enabled", False)
    win_min = glob.get("search_window_min", 15)
    cd_sec = glob.get("cooldown_sec", 10)
    bl_en = glob.get("blacklist_enabled", False)
    bl_cnt = len(glob.get("blacklist_nicks", []))
    return (
        "⚙️ <b>Настройки плагина</b>\n\n"
        f"• Состояние: <b>{plugin_state}</b>\n"
        f"• Почты: <b>🟢 Активных: {active_accs} / Всего: {total_accs} шт.</b>\n"
        f"• Уведомления Telegram: <b>{'🔔 ВКЛ (приходят)' if cmd_notif else '🔕 Заглушены (без спама)'}</b>\n"
        f"• Окно поиска писем: <b>{win_min} мин</b>\n"
        f"• Задержка спама: <b>{cd_sec} сек</b>\n"
        f"• Чёрный список: <b>{'🟢 ВКЛ' if bl_en else '⚪ ВЫКЛ'} ({bl_cnt} ников)</b>\n\n"
        "Выберите раздел:"
    )

def _settings_kb(chat_id: int) -> InlineKeyboardMarkup:
    cfg = _load_config()
    glob = cfg.get("global", {})
    enabled = glob.get("plugin_enabled", True)
    accs = cfg.get("accounts", [])
    rows = [
        [(f"🧩 Состояние плагина: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}", "mcp_tgl_plugin")],
        [(f"📬 Почтовые ящики · {len(accs)}", "mcp_acc_page:0")],
        [("🔔 Уведомления", "mcp_notif_menu")],
        [("⏱ Окно поиска писем", "mcp_win_menu"), ("⏳ Кулдаун спама", "mcp_cd_menu")],
        [("💬 Шаблоны сообщений", "mcp_templates_menu")],
        [("⛔ Чёрный список", "mcp_bl_menu")],
        [("🧰 Обслуживание", "mcp_maint")],
        [("◀️ Назад", "mcp_home")],
    ]
    return _make_kb(rows)

def _open_settings(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _settings_text(chat_id)
    kb = _settings_kb(chat_id)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

_open_settings_panel = _open_settings
_open_mini_settings = _open_settings

def _notif_menu_text() -> str:
    cfg = _load_config()
    n = cfg.get("notifications", {})
    glob = cfg.get("global", {})
    cmd_suppress = not glob.get("command_notifications_enabled", False)
    suc = n.get("success", True)
    fail = n.get("code_not_found", True)
    err = n.get("mail_error", True)
    bl = n.get("blacklist_hit", False)
    cd = n.get("cooldown_hit", False)
    return (
        "🔔 <b>Настройки уведомлений в Telegram</b>\n\n"
        "Настройте события, о которых плагин присылает оповещения вам в Telegram:\n\n"
        f"• Спам командами FunPay: <b>{'🔕 ЗАГЛУШЕН (без спама)' if cmd_suppress else '🔔 ВКЛ (приходят)'}</b>\n"
        "  <i>(Не присылать оповещение '[Чат] Покупатель написал: !code')</i>\n\n"
        f"• Успешная выдача кода: <b>{'🟢 ВКЛ' if suc else '🔴 ВЫКЛ'}</b>\n"
        "  <i>(Оповещение в TG, когда покупатель получил код подтверждения)</i>\n\n"
        f"• Код не найден на почте: <b>{'🟢 ВКЛ' if fail else '🔴 ВЫКЛ'}</b>\n"
        "  <i>(Оповещение в TG, если покупатель запросил код, но письма нет)</i>\n\n"
        f"• Ошибки почты и прокси: <b>{'🟢 ВКЛ' if err else '🔴 ВЫКЛ'}</b>\n"
        "  <i>(Оповещение о сбое IMAP, разрыве прокси или ошибке токена)</i>\n\n"
        f"• Запросы из Чёрного списка: <b>{'🟢 ВКЛ' if bl else '🔴 ВЫКЛ'}</b>\n"
        "  <i>(Оповещение, когда заблокированный покупатель пишет команду)</i>\n\n"
        f"• Спам и повторы (кулдаун): <b>{'🟢 ВКЛ' if cd else '🔴 ВЫКЛ'}</b>\n"
        "  <i>(Оповещение при частых повторных запросах)</i>\n\n"
        "Нажмите на кнопку для переключения:"
    )

def _notif_menu_kb() -> InlineKeyboardMarkup:
    cfg = _load_config()
    n = cfg.get("notifications", {})
    glob = cfg.get("global", {})
    cmd_notif = glob.get("command_notifications_enabled", False)
    suc = n.get("success", True)
    fail = n.get("code_not_found", True)
    err = n.get("mail_error", True)
    bl = n.get("blacklist_hit", False)
    cd = n.get("cooldown_hit", False)
    rows = [
        [(f"🔕 Глушение команд FunPay: {'🔕 ЗАГЛУШЕН' if not cmd_notif else '🔔 ПРИХОДИТ'}", "mcp_tgl_notif_cmd")],
        [(f"✅ Успешная выдача: {'🟢 ВКЛ' if suc else '🔴 ВЫКЛ'}", "mcp_tgl_notif:success")],
        [(f"❌ Код не найден: {'🟢 ВКЛ' if fail else '🔴 ВЫКЛ'}", "mcp_tgl_notif:code_not_found")],
        [(f"⚠️ Ошибки почты/прокси: {'🟢 ВКЛ' if err else '🔴 ВЫКЛ'}", "mcp_tgl_notif:mail_error")],
        [(f"⛔ Запросы из ЧС: {'🟢 ВКЛ' if bl else '🔴 ВЫКЛ'}", "mcp_tgl_notif:blacklist_hit")],
        [(f"⏳ Превышение кулдауна: {'🟢 ВКЛ' if cd else '🔴 ВЫКЛ'}", "mcp_tgl_notif:cooldown_hit")],
        [("◀️ Назад", "mcp_settings")],
    ]
    return _make_kb(rows)

def _open_notif_menu(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _notif_menu_text()
    kb = _notif_menu_kb()
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

ACCOUNTS_PER_PAGE = 5

def _open_accounts_list(chat_id: int, page: int = 0, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    accs = cfg.get("accounts", [])
    total = len(accs)
    active = sum(1 for a in accs if a.get("enabled", True))

    text = (
        f"📬 <b>Настройка почтовых ящиков</b>\n\n"
        f"• Всего почт в плагине: <b>{total} шт.</b>\n"
        f"• Активных: <b>🟢 {active}</b> | Выключенных: <b>🔴 {total - active}</b>"
    )
    rows: List[List[Tuple[str, str]]] = [
        [("➕ Добавить почту", "mcp_add_wizard")],
    ]

    total_pages = max(1, (total + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE) if total > 0 else 1
    page = max(0, min(page, total_pages - 1))
    start = page * ACCOUNTS_PER_PAGE
    page_accs = accs[start : start + ACCOUNTS_PER_PAGE]

    for a in page_accs:
        aid = a.get("id")
        st_ico = "🟢" if a.get("enabled", True) else "🔴"
        prov = PROVIDERS.get(a.get("provider", "outlook"), {})
        p_ico = prov.get("icon", "📧")
        serv = SERVICES.get(a.get("service", "all"), {})
        s_name = serv.get("name", a.get("service", ""))
        email_str = a.get("email", aid)
        cmd_str = a.get("command", "!code")
        lbl = f"{st_ico} {p_ico} {email_str} · [{s_name}] ({cmd_str})"
        rows.append([(lbl, f"mcp_acc_card:{aid}")])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️ Пред.", f"mcp_acc_page:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "mcp_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"mcp_acc_page:{page + 1}"))
        rows.append(nav)

    rows.append([("◀️ Назад", "mcp_settings")])
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _open_account_card(chat_id: int, acc_id: str, message_id: Optional[int] = None, notice: Optional[str] = None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    acc = next((a for a in cfg.get("accounts", []) if a.get("id") == acc_id), None)
    if not acc:
        _tg_send(chat_id, "⚠️ Почта не найдена.")
        _open_accounts_list(chat_id, 0, message_id)
        return

    prov = PROVIDERS.get(acc.get("provider", "outlook"), {})
    serv = SERVICES.get(acc.get("service", "all"), {})
    enabled = acc.get("enabled", True)
    global_notif = cfg.get("global", {}).get("command_notifications_enabled", False)
    acc_notif = acc.get("command_notifications_enabled", global_notif)
    is_outlook = (acc.get("provider") == "outlook")
    has_token = bool(acc.get("refresh_token"))

    notice_part = f"{notice}\n\n" if notice else ""
    oauth_line = f"• <b>OAuth токен:</b> {'🟢 Привязан' if has_token else '⚠️ Не привязан (нужен вход)'}\n" if is_outlook else ""
    text = (
        f"{notice_part}📧 <b>Карточка почты:</b> <code>{acc.get('email')}</code>\n\n"
        f"• <b>Провайдер:</b> {prov.get('icon', '')} {prov.get('name', acc.get('provider'))}\n"
        f"• <b>Сервис:</b> {serv.get('icon', '')} {serv.get('name', acc.get('service'))}\n"
        f"• <b>Команда вызова:</b> <code>{acc.get('command', '!code')}</code>\n"
        f"• <b>Статус выдачи:</b> {'🟢 Включён' if enabled else '🔴 Выключен'}\n"
        f"• <b>Уведомления о команде:</b> {'🔔 Приходят' if acc_notif else '🔕 Заглушены'}\n"
        f"{oauth_line}"
        f"• <b>Шаблон ответа:</b> <i>{'Кастомный' if acc.get('template') else 'Общий'}</i>\n"
        f"• <b>Прокси:</b> <code>{acc.get('proxy') or 'Прямое соединение'}</code>"
    )

    rows = [
        [(f"⚡ Статус: {'🟢 ВКЛ' if enabled else '🔴 ВЫКЛ'}", f"mcp_acc_tgl:{acc_id}"),
         (f"Уведомления: {'🔔 ВКЛ' if acc_notif else '🔕 ВЫКЛ'}", f"mcp_acc_notif_tgl:{acc_id}")],
        [("🔄 Проверить подключение сейчас", f"mcp_acc_check:{acc_id}")],
    ]
    if is_outlook:
        rows.append([("🔑 Авторизация / Refresh Token", f"mcp_acc_oauth_menu:{acc_id}")])
    rows.extend([
        [("✏️ Изменить команду", f"mcp_acc_cmd_edit:{acc_id}"), ("🎯 Изменить сервис", f"mcp_acc_serv_menu:{acc_id}")],
        [("📝 Шаблон ответа", f"mcp_acc_tpl_menu:{acc_id}"), ("🌐 Настроить прокси", f"mcp_acc_proxy_edit:{acc_id}")],
        [("🗑 Удалить почту", f"mcp_acc_del_ask:{acc_id}")],
        [("◀️ К списку почт", "mcp_acc_page:0")],
    ])
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _open_acc_oauth_menu(chat_id: int, acc_id: str, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    acc = next((a for a in cfg.get("accounts", []) if a.get("id") == acc_id), None)
    if not acc:
        _open_accounts_list(chat_id, 0, message_id)
        return
    has_token = bool(acc.get("refresh_token"))
    auto_url = get_outlook_auto_auth_url(chat_id=chat_id, action="reauth", acc_id=acc_id)
    text = (
        f"🔑 <b>Авторизация Outlook / Hotmail</b>\n\n"
        f"📧 <b>Почта:</b> <code>{acc.get('email')}</code>\n"
        f"• <b>Статус токена:</b> <b>{'🟢 Привязан (работает)' if has_token else '⚠️ Не привязан'}</b>\n\n"
        "🚀 <b>Автоматическая привязка в 1 клик (Рекомендуется!):</b>\n"
        "1️⃣ Нажмите <b>«🌐 Войти в браузере (Авто)»</b> ниже.\n"
        "2️⃣ Войдите в аккаунт Microsoft и подтвердите вход.\n"
        "3️⃣ Плагин <b>сам перехватит код</b> и обновит токен без ручного копирования!\n\n"
        "✍️ <i>Или нажмите «Ввести токен вручную», если настраиваете бота удаленно.</i>"
    )
    rows = [
        [("🌐 Войти в браузере (Авто)", auto_url)],
        [("✍️ Ввести ссылку / токен вручную", f"mcp_acc_oauth_input:{acc_id}")],
        [("◀️ Назад в карточку", f"mcp_acc_card:{acc_id}")],
    ]
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _start_add_wizard(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = (
        "➕ <b>Мастер добавления почты (Шаг 1 из 4)</b>\n\n"
        "Выберите провайдера электронной почты:"
    )
    rows = [
        [("🔵 Outlook / Hotmail", "mcp_add_prov:outlook"), ("🔴 Gmail", "mcp_add_prov:gmail")],
        [("🟡 Yandex", "mcp_add_prov:yandex"), ("🔷 Mail.ru", "mcp_add_prov:mailru")],
        [("🟣 Firstmail", "mcp_add_prov:firstmail"), ("🟢 Notletters", "mcp_add_prov:notletters")],
        [("🟠 SmakMail", "mcp_add_prov:smakmail")],
        [("◀️ Отмена", "mcp_acc_page:0")],
    ]
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _add_wizard_credentials_prompt(chat_id: int, provider: str, message_id: Optional[int] = None):
    prov = PROVIDERS.get(provider, {})
    _waiting[chat_id] = {"state": "waiting_credentials", "provider": provider}

    if provider == "outlook":
        auto_url = get_outlook_auto_auth_url(chat_id=chat_id, action="wizard")
        text = (
            f"➕ <b>Добавление почты: {prov.get('name')} (Шаг 2 из 4)</b>\n\n"
            "Плагин поддерживает <b>автоматическую привязку через браузер в 1 клик</b>:\n\n"
            "🚀 <b>Авто-привязка (Рекомендуется! Без копирования):</b>\n"
            "1️⃣ Нажмите <b>«🌐 Войти в браузере (Авто-привязка)»</b> ниже.\n"
            "2️⃣ Войдите в почту Microsoft и разрешите доступ.\n"
            "3️⃣ Плагин <b>автоматически перехватит код</b>, определит email и перейдет к следующему шагу!\n\n"
            "✍️ <b>Или отправьте данные вручную в этот чат:</b>\n"
            "• <code>email:password:refresh_token</code> (покупные почты)\n"
            "• <code>email:password</code> (авто-вход)\n"
            "• С прокси: <code>email:password:token|http://ip:port</code>"
        )
        rows = [
            [("🌐 Войти в браузере (Авто-привязка)", auto_url)],
            [("✍️ Ввести токен / данные вручную", "mcp_wiz_manual_outlook")],
            [("◀️ Отмена", "mcp_acc_page:0")],
        ]
    else:
        hint = prov.get("desc", "")
        text = (
            f"➕ <b>Добавление почты: {prov.get('name')} (Шаг 2 из 4)</b>\n\n"
            f"<b>Особенности:</b> {hint}\n\n"
            "Отправьте данные в формате:\n"
            "<code>email:password</code>\n"
            "<i>(или email:пароль_приложений)</i>\n\n"
            "С прокси (опционально):\n"
            "<code>email:password|http://user:pass@ip:port</code>\n\n"
            f"{'Для кастомного IMAP: host:port:email:password' if provider == 'custom_imap' else ''}"
        )
        rows = [
            [("◀️ Отмена", "mcp_acc_page:0")],
        ]
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _add_wizard_service_prompt(chat_id: int, message_id: Optional[int] = None):
    text = (
        "➕ <b>Выбор сервиса (Шаг 3 из 4)</b>\n\n"
        "От какого сервиса эта почта будет выдавать код?\n\n"
        "Выберите сервис из списка ниже или укажите «Универсальный» (любой код):"
    )
    rows = [
        [("🎮 Steam", "mcp_add_serv:steam"), ("🎯 Epic Games", "mcp_add_serv:epic")],
        [("💬 Discord", "mcp_add_serv:discord"), ("⚔️ Battle.net", "mcp_add_serv:battlenet")],
        [("🔫 Riot Games", "mcp_add_serv:riot"), ("🛡️ Ubisoft", "mcp_add_serv:ubisoft")],
        [("⚡ Supercell", "mcp_add_serv:supercell"), ("✈️ Telegram", "mcp_add_serv:telegram")],
        [("🎲 Roblox", "mcp_add_serv:roblox"), ("🌐 Универсальный", "mcp_add_serv:all")],
        [("✏️ Свой фильтр", "mcp_add_serv:custom")],
        [("◀️ Отмена", "mcp_acc_page:0")],
    ]
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _add_wizard_command_prompt(chat_id: int, service: str, message_id: Optional[int] = None):
    _waiting[chat_id]["state"] = "waiting_command"
    _waiting[chat_id]["service"] = service

    cfg = _load_config()
    existing_cmds = {a.get("command", "").lower() for a in cfg.get("accounts", [])}
    def_cmd = "!code" if "!code" not in existing_cmds else f"!code_{service}"
    counter = 2
    while def_cmd.lower() in existing_cmds:
        def_cmd = f"!code_{service}_{counter}"
        counter += 1

    _waiting[chat_id]["suggested_command"] = def_cmd

    text = (
        f"➕ <b>Настройка команды (Шаг 4 из 4)</b>\n\n"
        f"Какая команда в чате FunPay будет выдавать код?\n\n"
        f"Предлагаемая команда: <code>{def_cmd}</code>\n\n"
        "Вы можете использовать предложенную команду или написать свою в чат (например: <code>!steam</code>, <code>!код</code>, <code>/code</code>):"
    )
    rows = [
        [(f"✅ Использовать {def_cmd}", "mcp_add_cmd_default")],
        [("◀️ Отмена", "mcp_acc_page:0")],
    ]
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _finish_add_account(chat_id: int, command: str):
    data = _waiting.pop(chat_id, None)
    if not data:
        _tg_send(chat_id, "⚠️ Сессия добавления устарела.")
        _open_accounts_list(chat_id, 0)
        return

    acc_id = os.urandom(4).hex()
    provider = data.get("provider", "outlook")
    prov_meta = PROVIDERS.get(provider, {})

    new_acc = {
        "id": acc_id,
        "email": data.get("email", ""),
        "provider": provider,
        "password": data.get("password", ""),
        "refresh_token": data.get("refresh_token", ""),
        "client_id": data.get("client_id", OutlookClient.PUBLIC_CLIENT_IDS[0]),
        "client_secret": data.get("client_secret", ""),
        "imap_host": data.get("imap_host", prov_meta.get("default_host", "")),
        "imap_port": data.get("imap_port", prov_meta.get("default_port", 993)),
        "proxy": data.get("proxy", ""),
        "service": data.get("service", "all"),
        "custom_filter": data.get("custom_filter", ""),
        "command": command.strip(),
        "enabled": True,
        "command_notifications_enabled": False,
        "template": "",
    }

    cfg = _load_config()
    cfg.setdefault("accounts", []).append(new_acc)
    _save_config(cfg)

    _log_event("account_added", logging.INFO, email=new_acc["email"], provider=provider, service=new_acc["service"], cmd=command)

    text = (
        f"🎉 <b>Почта успешно добавлена!</b>\n\n"
        f"• <b>Email:</b> <code>{new_acc['email']}</code>\n"
        f"• <b>Провайдер:</b> {prov_meta.get('icon', '')} {prov_meta.get('name')}\n"
        f"• <b>Сервис:</b> {SERVICES.get(new_acc['service'], {}).get('name')}\n"
        f"• <b>Команда в чате:</b> <code>{new_acc['command']}</code>\n"
        f"• <b>Уведомления покупателей:</b> 🔕 Заглушены (без спама в TG)\n\n"
        "Теперь когда покупатель напишет эту команду в чате FunPay, бот автоматически достанет свежий проверочный код из почты!"
    )
    rows = [
        [("📬 Перейти к карточке", f"mcp_acc_card:{acc_id}")],
        [("➕ Добавить ещё", "mcp_add_wizard"), ("◀️ К списку почт", "mcp_acc_page:0")],
    ]
    _tg_send(chat_id, text, _make_kb(rows))

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.debug(f"{LP} [OAuth HTTP] " + format % args)

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/favicon.ico"):
                self.send_response(204)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query)
            code = params.get("code", [""])[0]
            error = params.get("error", [""])[0]
            error_desc = params.get("error_description", [""])[0]
            state_str = params.get("state", [""])[0]

            cid: Optional[int] = None
            action = "wizard"
            acc_id: Optional[str] = None

            if state_str:
                parts = state_str.split(":", 2)
                try:
                    cid = int(parts[0]) if parts[0] not in ("0", "") else None
                except Exception:
                    cid = None
                if len(parts) > 1:
                    action = parts[1]
                if len(parts) > 2 and parts[2] != "none":
                    acc_id = parts[2]

            if not cid:
                if _waiting:
                    cid = next(iter(_waiting.keys()))
                else:
                    cid = admin_chat_id

            if error:
                logger.warning(f"{LP} [OAuth HTTP] Ошибка от Microsoft: {error} - {error_desc}")
                self._send_html_response(
                    title="Ошибка авторизации Microsoft",
                    icon="❌",
                    accent_color="#ef4444",
                    header="Авторизация отменена",
                    badge=f"Код: {error}",
                    message=f"Microsoft сообщил об ошибке: {error_desc or 'Действие отменено пользователем'}. Вы можете закрыть эту вкладку и попробовать снова в боте."
                )
                if cid:
                    _tg_send(cid, f"❌ <b>Ошибка авторизации Microsoft:</b>\n<i>{error_desc or error}</i>")
                return

            if not code:
                self._send_html_response(
                    title="Ожидание авторизации",
                    icon="ℹ️",
                    accent_color="#3b82f6",
                    header="Ожидание кода авторизации",
                    badge=NAME,
                    message="Страница ожидает перенаправления от Microsoft с кодом подтверждения."
                )
                return

            port = getattr(self.server, "server_port", _oauth_server_port or 8543)
            redirect_uri = f"http://localhost:{port}"
            logger.info(f"{LP} [OAuth HTTP] Получен код авторизации Microsoft, обмен на токены (порт {port})...")

            ok, tok_or_err, found_email = OutlookClient.exchange_code_or_url(code, redirect_uri=redirect_uri)
            if not ok:
                logger.warning(f"{LP} [OAuth HTTP] Ошибка обмена кода: {tok_or_err}")
                self._send_html_response(
                    title="Ошибка обмена кода",
                    icon="⚠️",
                    accent_color="#f59e0b",
                    header="Не удалось обменять код на токен",
                    badge="OAuth Exchange Error",
                    message=f"Ошибка: {tok_or_err}. Попробуйте авторизоваться снова."
                )
                if cid:
                    _tg_send(cid, f"❌ <b>Ошибка обмена кода авторизации:</b>\n<i>{tok_or_err}</i>")
                return

            email_display = found_email or "Outlook / Hotmail"
            logger.info(f"{LP} [OAuth HTTP] Успешная авторизация почты: {email_display}!")

            self._send_html_response(
                title="Успешная авторизация Outlook",
                icon="✅",
                accent_color="#22c55e",
                header="Почта успешно подключена!",
                badge=email_display,
                message="Код авторизации успешно передан в плагин. Вы можете закрыть эту вкладку и вернуться в Telegram бота."
            )

            try:
                _handle_oauth_success(cid, action, acc_id, tok_or_err, found_email)
            except Exception as e:
                logger.error(f"{LP} [OAuth HTTP] Ошибка обработки успеха в плагине: {e}")

        except Exception as ex:
            logger.error(f"{LP} [OAuth HTTP] Исключение в обработчике запроса: {ex}")

    def _send_html_response(self, title: str, icon: str, accent_color: str, header: str, badge: str, message: str):
        badge_html = f'<div class="badge">{html.escape(badge)}</div>' if badge else ""
        content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: #0f1117;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 20px;
      box-sizing: border-box;
    }}
    .card {{
      background: #1a1d27;
      border: 1px solid #2d3348;
      border-radius: 16px;
      padding: 36px 32px;
      text-align: center;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 12px 36px rgba(0, 0, 0, 0.45);
    }}
    .icon {{
      font-size: 52px;
      line-height: 1;
      margin-bottom: 18px;
    }}
    h1 {{
      font-size: 22px;
      margin: 0 0 12px;
      font-weight: 700;
      color: {accent_color};
    }}
    .badge {{
      display: inline-block;
      background: #242938;
      color: #93c5fd;
      padding: 8px 16px;
      border-radius: 8px;
      font-family: monospace;
      font-size: 15px;
      margin: 10px 0 18px;
      word-break: break-all;
    }}
    p {{
      color: #94a3b8;
      font-size: 15px;
      line-height: 1.5;
      margin: 0 0 20px;
    }}
    .footer {{
      font-size: 13px;
      color: #64748b;
      border-top: 1px solid #262b3d;
      padding-top: 14px;
      margin-top: 10px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{html.escape(header)}</h1>
    {badge_html}
    <p>{html.escape(message)}</p>
    <div class="footer">{html.escape(NAME)} • FunPay Cardinal</div>
  </div>
</body>
</html>"""
        data = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

def _handle_oauth_success(chat_id: Optional[int], action: str, acc_id: Optional[str], refresh_token: str, email: Optional[str]):
    email_clean = (email or "").strip() or "outlook_account"
    cfg = _load_config()

    if action == "reauth" and acc_id:
        acc = next((a for a in cfg.get("accounts", []) if a.get("id") == acc_id), None)
        if acc:
            acc["refresh_token"] = refresh_token
            if email and "@" in email and (acc.get("email") in ("outlook_account", "", None) or "@" not in acc.get("email", "")):
                acc["email"] = email
            _save_config(cfg)
            ok, msg, lat = MailManager.test_connection(acc)
            res_str = f"🟢 Связь установлена: {msg} ({lat} ms)" if ok else f"🔴 Ошибка соединения: {msg} ({lat} ms)"
            if chat_id:
                _waiting.pop(chat_id, None)
                _open_account_card(chat_id, acc_id, notice=f"✅ <b>Refresh Token успешно обновлен через браузер!</b>\n{res_str}")
            return

    if chat_id:
        existing = next((a for a in cfg.get("accounts", []) if a.get("email", "").lower() == email_clean.lower()), None)
        if existing and email_clean != "outlook_account":
            existing["refresh_token"] = refresh_token
            _save_config(cfg)
            _waiting.pop(chat_id, None)
            ok, msg, lat = MailManager.test_connection(existing)
            res_str = f"🟢 Связь установлена: {msg} ({lat} ms)" if ok else f"🔴 Ошибка соединения: {msg} ({lat} ms)"
            _tg_send(chat_id, f"ℹ️ Почта <code>{email_clean}</code> уже была добавлена ранее. Токен успешно обновлен!\n{res_str}")
            _open_account_card(chat_id, existing.get("id"))
            return

        _waiting[chat_id] = {
            "state": "waiting_service",
            "provider": "outlook",
            "email": email_clean,
            "password": "",
            "refresh_token": refresh_token,
            "imap_host": "",
            "imap_port": 993,
            "proxy": "",
        }
        _tg_send(chat_id, (
            f"🎉 <b>Почта успешно подключена через браузер!</b>\n\n"
            f"📧 <b>Email:</b> <code>{email_clean}</code>\n"
            f"🔑 <b>Refresh Token:</b> 🟢 Получен и сохранен\n\n"
            f"Переходим к выбору сервиса (Шаг 3 из 4) 👇"
        ))
        _add_wizard_service_prompt(chat_id)
        return

    _send_tg_notification(
        None,
        f"🎉 <b>[Mail Plugin] Почта Outlook успешно авторизована через браузер:</b>\n<code>{email_clean}</code>"
    )

def _start_oauth_server():
    global _oauth_server, _oauth_server_thread, _oauth_server_port
    if _oauth_server:
        return
    for port in range(8543, 8560):
        try:
            srv = HTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
            _oauth_server = srv
            _oauth_server_port = port
            srv.server_port = port
            _oauth_server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
            _oauth_server_thread.start()
            logger.info(f"{LP} Фоновый OAuth HTTP-сервер запущен на 127.0.0.1:{port}")
            return
        except OSError as e:
            logger.debug(f"{LP} Порт {port} занят: {e}, пробуем следующий...")
            continue
    logger.warning(f"{LP} Не удалось запустить фоновый OAuth HTTP-сервер на портах 8543-8559")

def _stop_oauth_server():
    global _oauth_server, _oauth_server_thread
    if _oauth_server:
        try:
            _oauth_server.shutdown()
            _oauth_server.server_close()
        except Exception:
            pass
        _oauth_server = None
        _oauth_server_thread = None
        logger.info(f"{LP} Фоновый OAuth HTTP-сервер остановлен.")

def _maint_menu_text() -> str:
    return (
        "🧰 <b>Обслуживание плагина</b>\n\n"
        "Здесь вы можете управлять конфигурацией плагина или скачать файл логов:\n\n"
        "• <b>Конфиг</b> — экспорт и импорт настроек (config.json).\n"
        "• <b>Скачать логи</b> — выгрузка лог-файла плагина."
    )

def _maint_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [("⚙️ Конфиг", "mcp_maint_cfg_menu")],
        [("📄 Скачать логи", "mcp_maint_log_download")],
        [("◀️ Назад", "mcp_settings")],
    ]
    return _make_kb(rows)

def _open_maint_menu(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _maint_menu_text()
    kb = _maint_menu_kb()
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _maint_cfg_menu_text() -> str:
    cfg = _load_config()
    accs = cfg.get("accounts", [])
    active = sum(1 for a in accs if a.get("enabled", True))
    return (
        "⚙️ <b>Управление конфигурацией</b>\n\n"
        "Экспорт и импорт настроек плагина, базы почт и шаблонов сообщений.\n\n"
        f"• Сохранено почт в конфиге: <b>{len(accs)} шт.</b> (активных: {active})\n"
        "• Формат: <code>JSON</code> (с авто-бэкапом перед заменой)\n\n"
        "Выберите действие:"
    )

def _maint_cfg_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [("💾 Скачать конфиг (.json)", "mcp_maint_cfg_export")],
        [("📥 Импортировать конфиг (.json)", "mcp_maint_cfg_import")],
        [("◀️ Назад", "mcp_maint")],
    ]
    return _make_kb(rows)

def _open_maint_cfg_menu(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _maint_cfg_menu_text()
    kb = _maint_cfg_menu_kb()
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _run_health_check_all(chat_id: int, message_id: Optional[int] = None):
    cfg = _load_config()
    accs = cfg.get("accounts", [])
    if not accs:
        _tg_send(chat_id, "⚠️ Нет добавленных почт для проверки.")
        _open_maint_menu(chat_id, message_id)
        return

    _tg_edit(chat_id, message_id, "⏳ <i>Проверка подключения ко всем почтам... Пожалуйста, подождите.</i>") if message_id else _tg_send(chat_id, "⏳ <i>Проверка подключения ко всем почтам...</i>")

    results = []
    for acc in accs:
        prov = PROVIDERS.get(acc.get("provider", "outlook"), {})
        ok, msg, lat = MailManager.test_connection(acc)
        ico = "🟢" if ok else "🔴"
        results.append(f"{ico} <b>{acc.get('email')}</b> ({prov.get('name')})\n    └ <i>{msg}</i> [{lat} ms]")

    text = "🩺 <b>Результаты диагностики всех почт:</b>\n\n" + "\n\n".join(results)
    rows = [
        [("◀️ В меню обслуживания", "mcp_maint")],
    ]
    _tg_edit(chat_id, message_id, text, _make_kb(rows)) if message_id else _tg_send(chat_id, text, _make_kb(rows))

BL_USERS_PER_PAGE = 5

def _get_blacklist_users(cfg: dict) -> dict:
    glob = cfg.setdefault("global", {})
    users = glob.get("blacklist_users")
    if not isinstance(users, dict):
        users = {}
        for n in glob.get("blacklist_nicks", []):
            users[str(n)] = {"mode": "all", "blocked_accounts": []}
        glob["blacklist_users"] = users
    return users

def _blacklist_menu_text() -> str:
    cfg = _load_config()
    glob = cfg.get("global", {})
    bl_en = glob.get("blacklist_enabled", False)
    users = _get_blacklist_users(cfg)
    return (
        "⛔ <b>Чёрный список покупателей</b>\n\n"
        f"• Состояние: <b>{'🟢 Включён' if bl_en else '🔴 Выключен'}</b>\n"
        f"• Заблокировано: <b>{len(users)} покупателей</b>\n\n"
        "Нажмите на покупателя для настройки ограничений (полный запрет или для конкретных почт):"
    )

def _blacklist_menu_kb(page: int = 0) -> InlineKeyboardMarkup:
    cfg = _load_config()
    glob = cfg.get("global", {})
    bl_en = glob.get("blacklist_enabled", False)
    users = _get_blacklist_users(cfg)
    user_keys = sorted(users.keys())
    total = len(user_keys)
    total_pages = max(1, (total + BL_USERS_PER_PAGE - 1) // BL_USERS_PER_PAGE) if total > 0 else 1
    page = max(0, min(page, total_pages - 1))
    start = page * BL_USERS_PER_PAGE
    page_keys = user_keys[start : start + BL_USERS_PER_PAGE]

    rows = [
        [(f"ЧС: {'🟢 ВКЛЮЧЁН' if bl_en else '🔴 ВЫКЛЮЧЕН'}", "mcp_bl_tgl")],
    ]
    for nick in page_keys:
        u_info = users[nick]
        mode = u_info.get("mode", "all")
        b_cnt = len(u_info.get("blocked_accounts", []))
        mode_str = "⛔ Все почты" if mode == "all" else f"⚠️ {b_cnt} почт"
        rows.append([(f"👤 {nick} · {mode_str}", f"mcp_bl_u:{nick}")])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️ Пред.", f"mcp_bl_page:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "mcp_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"mcp_bl_page:{page + 1}"))
        rows.append(nav)

    rows.append([("➕ Добавить покупателя", "mcp_bl_add"), ("🗑 Очистить весь ЧС", "mcp_bl_clear")])
    rows.append([("◀️ Назад", "mcp_settings")])
    return _make_kb(rows)

def _open_blacklist_menu(chat_id: int, page: int = 0, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _blacklist_menu_text()
    kb = _blacklist_menu_kb(page)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _open_bl_user_card(chat_id: int, nick: str, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    users = _get_blacklist_users(cfg)
    u_data = users.get(nick)
    if not u_data:
        _tg_send(chat_id, "⚠️ Покупатель не найден в ЧС.")
        _open_blacklist_menu(chat_id, 0, message_id)
        return

    mode = u_data.get("mode", "all")
    blocked_accs = u_data.get("blocked_accounts", [])
    accs = cfg.get("accounts", [])

    text = (
        f"👤 <b>Настройка ограничений:</b> <code>{html.escape(nick)}</code>\n\n"
        f"• Режим: <b>{'⛔ Полный (запрещены все почты)' if mode == 'all' else '⚠️ Выборочный (запрещены отдельные почты)'}</b>\n"
    )
    if mode == "selective":
        if blocked_accs:
            b_list = []
            for aid in blocked_accs:
                acc = next((a for a in accs if a.get("id") == aid), None)
                if acc:
                    prov = PROVIDERS.get(acc.get("provider", "outlook"), {})
                    b_list.append(f"  • {prov.get('icon', '')} <code>{acc.get('email')}</code> ({acc.get('command')})")
            text += f"• Заблокировано почт (<b>{len(b_list)} шт.</b>):\n" + "\n".join(b_list)
        else:
            text += "• <i>Пока нет заблокированных почт — покупателю доступны все почты. Нажмите «Выбрать запрещённые почты».</i>\n"

    text += "\nВыберите действие:"
    rows = [
        [(f"🔄 Режим: {'⛔ Полный' if mode == 'all' else '⚠️ Выборочный'}", f"mcp_bl_u_mode:{nick}")],
    ]
    if mode == "selective":
        rows.append([("📬 Выбрать запрещённые почты", f"mcp_bl_u_accs:{nick}:0")])
    rows.append([("🗑 Удалить из ЧС", f"mcp_bl_u_del:{nick}")])
    rows.append([("◀️ К чёрному списку", "mcp_bl_menu")])
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _open_bl_user_accs(chat_id: int, nick: str, page: int = 0, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    cfg = _load_config()
    users = _get_blacklist_users(cfg)
    u_data = users.get(nick, {})
    blocked_set = set(u_data.get("blocked_accounts", []))
    accs = cfg.get("accounts", [])
    total = len(accs)

    text = (
        f"📬 <b>Выбор запрещённых почт для:</b> <code>{html.escape(nick)}</code>\n\n"
        "Нажмите на почту, чтобы переключить доступ для этого покупателя:\n"
        "• ⛔ — доступ ЗАПРЕЩЁН (бот не выдаст код по её команде)\n"
        "• 🟢 — доступ РАЗРЕШЁН (покупатель может получать коды)\n\n"
        f"Заблокировано почт: <b>{len(blocked_set)} из {total} шт.</b>"
    )

    ACCS_PER_PAGE = 5
    total_pages = max(1, (total + ACCS_PER_PAGE - 1) // ACCS_PER_PAGE) if total > 0 else 1
    page = max(0, min(page, total_pages - 1))
    start = page * ACCS_PER_PAGE
    page_accs = accs[start : start + ACCS_PER_PAGE]

    rows = []
    for acc in page_accs:
        aid = acc.get("id")
        is_b = aid in blocked_set
        st_ico = "⛔ Запрещён" if is_b else "🟢 Разрешён"
        prov = PROVIDERS.get(acc.get("provider", "outlook"), {})
        email_str = acc.get("email", aid)
        cmd_str = acc.get("command", "!code")
        lbl = f"{st_ico} · {prov.get('icon', '')} {email_str} ({cmd_str})"
        rows.append([(lbl, f"mcp_bl_u_tgl_acc:{nick}:{aid}:{page}")])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀️ Пред.", f"mcp_bl_u_accs:{nick}:{page - 1}"))
        nav.append((f"Стр {page + 1}/{total_pages}", "mcp_noop"))
        if page < total_pages - 1:
            nav.append(("След. ▶️", f"mcp_bl_u_accs:{nick}:{page + 1}"))
        rows.append(nav)

    rows.append([("◀️ Назад к покупателю", f"mcp_bl_u:{nick}")])
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

TEMPLATE_LABELS = {
    "code_issued": "Выдача кода (успех)",
    "cmd_received": "Поиск письма / статус",
    "code_not_found": "Код не найден на почте",
    "cooldown_warning": "Превышение кулдауна спама",
    "blacklist_blocked": "Ответ заблокированному (ЧС)",
}

TEMPLATE_DESCS = {
    "code_issued": "Сообщение покупателю с выданным проверочным кодом.",
    "cmd_received": "Статусное сообщение сразу после получения команды (если включено).",
    "code_not_found": "Сообщение, если свежее письмо с кодом не найдено на почте.",
    "cooldown_warning": "Предупреждение, если покупатель пишет команду слишком часто.",
    "blacklist_blocked": "Ответ покупателю, находящемуся в чёрном списке.",
}

TEMPLATE_VARS = {
    "code_issued": "• <code>{code}</code> — проверочный код\n• <code>{service}</code> — сервис\n• <code>{email}</code> — адрес почты\n• <code>{sender}</code> — отправитель письма\n• <code>{time}</code> — время получения письма\n• <code>{subject}</code> — тема письма\n• <code>{command}</code> — вызванная команда",
    "cmd_received": "• <code>{command}</code> — вызванная команда\n• <code>{service}</code> — сервис\n• <code>{email}</code> — почта",
    "code_not_found": "• <code>{command}</code> — вызванная команда\n• <code>{service}</code> — сервис\n• <code>{email}</code> — почта\n• <code>{reason}</code> — причина",
    "cooldown_warning": "• <code>{left}</code> — осталось секунд кулдауна\n• <code>{command}</code> — вызванная команда",
    "blacklist_blocked": "• <code>{command}</code> — вызванная команда",
}

def _templates_menu_text() -> str:
    cfg = _load_config()
    send_search = cfg.get("global", {}).get("send_searching_msg", False)
    return (
        "💬 <b>Шаблоны сообщений покупателям</b>\n\n"
        "Здесь вы можете изменить тексты любых сообщений, отправляемых ботом в чат FunPay:\n\n"
        "• <b>Выдача кода (успех)</b> — итоговое сообщение с кодом.\n"
        f"• <b>Статус поиска</b> ({'🟢 ВКЛ' if send_search else '⚪ ВЫКЛ'}) — промежуточное сообщение о начале поиска.\n"
        "• <b>Код не найден</b> — если письмо ещё не пришло.\n"
        "• <b>Кулдаун анти-спама</b> — предупреждение при частых повторах.\n"
        "• <b>Чёрный список</b> — ответ заблокированным покупателям.\n\n"
        "Выберите шаблон для настройки:"
    )

def _templates_menu_kb() -> InlineKeyboardMarkup:
    cfg = _load_config()
    send_search = cfg.get("global", {}).get("send_searching_msg", False)
    rows = [
        [("✅ Выдача кода (успех)", "mcp_tpl_edit:code_issued")],
        [("🔍 Поиск письма / статус", "mcp_tpl_edit:cmd_received")],
        [("❌ Код не найден", "mcp_tpl_edit:code_not_found")],
        [("⏳ Кулдаун анти-спама", "mcp_tpl_edit:cooldown_warning")],
        [("⛔ Чёрный список", "mcp_tpl_edit:blacklist_blocked")],
        [(f"📨 Отправка статуса поиска: {'🟢 ВКЛ' if send_search else '⚪ ВЫКЛ'}", "mcp_tgl_searching_msg")],
        [("🔄 Сбросить все на стандартные", "mcp_tpl_reset_all")],
        [("◀️ Назад", "mcp_settings")],
    ]
    return _make_kb(rows)

def _open_templates_menu(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    text = _templates_menu_text()
    kb = _templates_menu_kb()
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _open_template_editor(chat_id: int, key: str, message_id: Optional[int] = None):
    _waiting[chat_id] = {"state": "edit_template", "tpl_key": key}
    cfg = _load_config()
    cur = cfg.get("templates", {}).get(key, _default_templates().get(key, ""))
    label = TEMPLATE_LABELS.get(key, key)
    desc = TEMPLATE_DESCS.get(key, "")
    vars_str = TEMPLATE_VARS.get(key, "")
    cur_clean = _clean_template_html(cur)

    text = (
        f"📝 <b>Редактирование шаблона:</b> {label}\n\n"
        f"<i>{desc}</i>\n\n"
        f"<b>Текущий текст:</b>\n<blockquote>{html.escape(cur_clean)}</blockquote>\n\n"
        f"<b>Доступные переменные:</b>\n{vars_str}\n\n"
        "✍️ <b>Отправьте новый текст шаблона в этот чат</b> или нажмите сброс:"
    )
    rows = [
        [("🔄 Сбросить этот шаблон", f"mcp_tpl_reset_one:{key}")],
        [("◀️ Назад к шаблонам", "mcp_templates_menu")],
    ]
    kb = _make_kb(rows)
    if message_id:
        _tg_edit(chat_id, message_id, text, kb)
    else:
        _tg_send(chat_id, text, kb)

def _info_text() -> str:
    return (
        f"ℹ️ <b>Информация о {NAME}</b>\n\n"
        "Здесь находятся официальные ссылки и справочные материалы:\n\n"
        "• <b>Чат</b> — помощь и общение.\n"
        "• <b>Канал</b> — новости и обновления.\n"
        "• <b>Инструкция</b> — подробная статья в Teletype.\n"
        "• <b>Альтернативная инструкция</b> — документация на GitHub.\n"
        "• <b>Мой Telegram</b> — связь с автором."
    )

def _info_kb() -> InlineKeyboardMarkup:
    return _make_kb([
        [("💬 Чат", GROUP_URL), ("📢 Канал", CHANNEL_URL)],
        [("📖 Инструкция", INSTRUCTION_URL)],
        [("📚 Альтернативная инструкция", ALT_INSTRUCTION_URL)],
        [("👤 Мой Telegram", CREATOR_URL)],
        [("◀️ Назад", "mcp_home")],
    ])

def _open_info(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _info_text(), _info_kb()) if message_id else _tg_send(chat_id, _info_text(), _info_kb())

def _plugin_version_key(value: str) -> tuple:
    nums = [int(x) for x in re.findall(r'\d+', str(value or ''))[:4]]
    nums.extend([0] * (4 - len(nums)))
    return tuple(nums[:4])

def _online_update_worker(chat_id: int, message_id: Optional[int] = None):
    _tg_edit(chat_id, message_id, "⏳ <b>Проверка обновлений на GitHub...</b>", _make_kb([[("◀️ Назад", "mcp_update_menu")]]))
    try:
        target_urls = [
            GITHUB_UPDATE_URL,
            "https://raw.githubusercontent.com/tinechelovec/FPC-Mail-Code/main/Mail-Code.py",
            "https://raw.githubusercontent.com/tinechelovec/FPC-Mail-Code/main/plugins/Mail-Code.py",
            "https://raw.githubusercontent.com/tinechelovec/FPC-Mail-Code/master/Mail-Code.py",
            "https://raw.githubusercontent.com/tinechelovec/FPC-Mail-Code/master/plugins/Mail-Code.py",
        ]
        seen = set()
        uniq_urls = []
        for u in target_urls:
            if u and u not in seen:
                seen.add(u)
                uniq_urls.append(u)

        resp = None
        for u in uniq_urls:
            try:
                r = requests.get(u, timeout=15)
                if r.status_code == 200 and r.content:
                    resp = r
                    break
            except Exception:
                continue

        if not resp or resp.status_code != 200 or not resp.content:
            _tg_edit(
                chat_id,
                message_id,
                f"⚠️ Не удалось загрузить обновление с GitHub (код {getattr(resp, 'status_code', 'нет ответа')}).\n"
                f"Проверьте репозиторий: {GITHUB_URL}",
                _make_kb([[("◀️ Назад", "mcp_update_menu")]])
            )
            return

        source = resp.text
        m = re.search(r'(?m)^\s*VERSION\s*=\s*["\']([^"\']+)["\']', source)
        if not m:
            _tg_edit(
                chat_id,
                message_id,
                "❌ В файле на GitHub не найдено поле VERSION.",
                _make_kb([[("◀️ Назад", "mcp_update_menu")]])
            )
            return

        remote_version = m.group(1).strip()
        if _plugin_version_key(remote_version) <= _plugin_version_key(VERSION):
            _tg_edit(
                chat_id,
                message_id,
                f"✅ <b>У вас уже установлена актуальная версия {VERSION}.</b>",
                _make_kb([[("◀️ Назад", "mcp_update_menu")]])
            )
            return

        plugin_file = os.path.abspath(__file__)
        compile(source, plugin_file, "exec")
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup_file = plugin_file + f'.pre-online-update.{stamp}.bak'
        shutil.copy2(plugin_file, backup_file)
        with open(plugin_file, "w", encoding="utf-8") as f:
            f.write(source)
        text = (
            f"✅ <b>Онлайн-обновление успешно установлено: v{remote_version}!</b>\n\n"
            f"Резервная копия сохранена в <code>{os.path.basename(backup_file)}</code>.\n"
            f"Выполните команду <code>/restart</code> для применения."
        )
        _tg_edit(chat_id, message_id, text, _make_kb([[("◀️ В меню", "mcp_home")]]))
    except Exception as e:
        _tg_edit(chat_id, message_id, f"❌ Ошибка обновления: {e}", _make_kb([[("◀️ Назад", "mcp_update_menu")]]))

def _update_menu_text() -> str:
    return (
        f"⬆️ <b>Обновление {NAME}</b>\n\n"
        f"Текущая версия: {VERSION}\n\n"
        "• <b>Обновить локально</b> — пришлите новый файл плагина <code>.py</code> в этот чат. Он будет проверен и установлен вместо текущего файла.\n"
        "• <b>Обновить онлайн</b> — проверить новую версию и скачать её с GitHub.\n\n"
        "Перед заменой автоматически создаётся резервная копия текущего плагина."
    )

def _update_menu_kb() -> InlineKeyboardMarkup:
    return _make_kb([
        [("📥 Обновить локально", "mcp_update_local")],
        [("🌐 Обновить онлайн", "mcp_update_online")],
        [("◀️ Назад", "mcp_home")],
    ])

def _open_update_menu(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _update_menu_text(), _update_menu_kb()) if message_id else _tg_send(chat_id, _update_menu_text(), _update_menu_kb())

def _delete_confirm_text() -> str:
    return (
        "⚠️ <b>Удаление плагина</b>\n\n"
        f"Вы точно хотите удалить <b>{NAME}</b>?\n\n"
        "Будут удалены:\n"
        "• файлы плагина\n"
        "• настройки и логи\n\n"
        "<b>Действие необратимо.</b>\n"
        "После удаления выполните перезапуск: напишите команду <code>/restart</code>."
    )

def _delete_confirm_kb() -> InlineKeyboardMarkup:
    return _make_kb([
        [("✅ Да, удалить", "mcp_delete_yes"), ("❌ Нет", "mcp_home")],
    ])

def _open_delete_confirm(chat_id: int, message_id: Optional[int] = None):
    _waiting.pop(chat_id, None)
    _tg_edit(chat_id, message_id, _delete_confirm_text(), _delete_confirm_kb()) if message_id else _tg_send(chat_id, _delete_confirm_text(), _delete_confirm_kb())

def _self_delete(chat_id: int, message_id: Optional[int] = None):
    errors = []
    try:
        shutil.rmtree(STORAGE_DIR, ignore_errors=True)
    except Exception as e:
        errors.append(f"данные: {e}")
    plugin_file = os.path.abspath(__file__)
    try:
        if os.path.exists(plugin_file):
            os.remove(plugin_file)
    except Exception as e:
        errors.append(f"файл: {e}")
    if not errors:
        text = f"✅ <b>Плагин {NAME} успешно удалён.</b>\n\nВыполните команду <code>/restart</code>."
    else:
        text = f"⚠️ <b>Плагин удалён с замечаниями:</b>\n" + "\n".join(errors)
    _tg_edit(chat_id, message_id, text, _make_kb([[("🔙 К списку плагинов", CB_PLUGINS_LIST_OPEN)]]))

def _cb_router(call):
    data = str(getattr(call, "data", "") or "")
    chat_id = getattr(getattr(getattr(call, "message", None), "chat", None), "id", None)
    msg_id = getattr(getattr(call, "message", None), "message_id", None)
    from_user = getattr(call, "from_user", None)
    user_id = getattr(from_user, "id", None)
    if not chat_id or not _is_authorized(user_id):
        return

    _answer_cb(call)

    if data in ("mcp_home", f"{UUID}:back"):
        _open_home(chat_id, msg_id)

    elif data in ("mcp_settings", "mcp_settings_panel", "mcp_mini_settings"):
        _open_settings(chat_id, msg_id)

    elif data == "mcp_notif_menu":
        _open_notif_menu(chat_id, msg_id)

    elif data == "mcp_info":
        _open_info(chat_id, msg_id)

    elif data == "mcp_update_menu":
        _open_update_menu(chat_id, msg_id)

    elif data == "mcp_update_local":
        _waiting[chat_id] = {"action": "update_local", "prompt_id": msg_id}
        cur_file_name = os.path.basename(__file__)
        text = (
            "📥 <b>Локальное обновление</b>\n\n"
            f"Пришлите новый файл <code>{cur_file_name}</code> с расширением <code>.py</code> в этот чат.\n"
            "После проверки файл заменит текущий плагин. Старый файл будет сохранён в резервной копии.\n\n"
            "Для отмены нажмите кнопку ниже."
        )
        _tg_edit(chat_id, msg_id, text, _make_kb([[("❌ Отмена", "mcp_update_menu")]]))

    elif data == "mcp_update_online":
        threading.Thread(target=_online_update_worker, args=(chat_id, msg_id), daemon=True).start()

    elif data == "mcp_delete_ask":
        _open_delete_confirm(chat_id, msg_id)

    elif data == "mcp_delete_yes":
        _self_delete(chat_id, msg_id)

    elif data.startswith("mcp_acc_page:"):
        p = int(data.split(":")[1])
        _open_accounts_list(chat_id, p, msg_id)

    elif data.startswith("mcp_acc_card:"):
        aid = data.split(":")[1]
        _open_account_card(chat_id, aid, msg_id)

    elif data.startswith("mcp_acc_tgl:"):
        aid = data.split(":")[1]
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                a["enabled"] = not a.get("enabled", True)
                break
        _save_config(cfg)
        _open_account_card(chat_id, aid, msg_id)

    elif data.startswith("mcp_acc_notif_tgl:"):
        aid = data.split(":")[1]
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                g_notif = cfg.get("global", {}).get("command_notifications_enabled", False)
                cur = a.get("command_notifications_enabled", g_notif)
                a["command_notifications_enabled"] = not cur
                break
        _save_config(cfg)
        _open_account_card(chat_id, aid, msg_id)

    elif data.startswith("mcp_acc_check:"):
        aid = data.split(":")[1]
        cfg = _load_config()
        acc = next((a for a in cfg.get("accounts", []) if a.get("id") == aid), None)
        if acc:
            _tg_edit(chat_id, msg_id, "⏳ <i>Проверка подключения к почте...</i>")
            ok, msg, lat = MailManager.test_connection(acc)
            notice = f"{'🟢 Связь установлена' if ok else '🔴 Ошибка соединения'}: {msg} ({lat} ms)"
            _open_account_card(chat_id, aid, msg_id, notice=notice)

    elif data.startswith("mcp_acc_oauth_menu:"):
        aid = data.split(":")[1]
        _open_acc_oauth_menu(chat_id, aid, msg_id)

    elif data.startswith("mcp_acc_oauth_input:"):
        aid = data.split(":")[1]
        _waiting[chat_id] = {"state": "waiting_oauth_token", "acc_id": aid}
        text = (
            "✍️ <b>Отправьте в чат ссылку или токен:</b>\n\n"
            "• Ссылку из браузера (<code>https://login.microsoftonline.com/...code=...</code>)\n"
            "• Либо сам код (<code>code=...</code>)\n"
            "• Либо готовый купленный <code>refresh_token</code>\n\n"
            "<i>Плагин автоматически сохранит токен и проверит подключение.</i>"
        )
        rows = [[("◀️ Отмена", f"mcp_acc_oauth_menu:{aid}")]]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_acc_cmd_edit:"):
        aid = data.split(":")[1]
        _waiting[chat_id] = {"state": "edit_acc_command", "acc_id": aid}
        text = "✏️ <b>Введите новую команду</b> для вызова кода (например: <code>!code</code>, <code>!steam</code>, <code>/code</code>):"
        rows = [[("◀️ Отмена", f"mcp_acc_card:{aid}")]]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_acc_serv_menu:"):
        aid = data.split(":")[1]
        text = "🎯 <b>Выберите сервис для этого аккаунта:</b>"
        rows = []
        serv_items = list(SERVICES.items())
        for i in range(0, len(serv_items), 2):
            pair = serv_items[i:i+2]
            r = [(f"{s_meta.get('icon', '')} {s_meta.get('name')}", f"mcp_acc_serv_set:{aid}:{s_key}") for s_key, s_meta in pair]
            rows.append(r)
        rows.append([("◀️ Назад", f"mcp_acc_card:{aid}")])
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_acc_serv_set:"):
        _, aid, serv_key = data.split(":")
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                a["service"] = serv_key
                break
        _save_config(cfg)
        _open_account_card(chat_id, aid, msg_id, notice=f"✅ Сервис изменен на {SERVICES.get(serv_key, {}).get('name')}")

    elif data.startswith("mcp_acc_del_ask:"):
        aid = data.split(":")[1]
        text = "⚠️ <b>Вы уверены, что хотите удалить эту почту?</b>"
        rows = [
            [("🗑 Да, удалить", f"mcp_acc_del_yes:{aid}"), ("◀️ Отмена", f"mcp_acc_card:{aid}")],
        ]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_acc_del_yes:"):
        aid = data.split(":")[1]
        cfg = _load_config()
        cfg["accounts"] = [a for a in cfg.get("accounts", []) if a.get("id") != aid]
        _save_config(cfg)
        _open_accounts_list(chat_id, 0, msg_id)

    elif data.startswith("mcp_acc_proxy_edit:"):
        aid = data.split(":")[1]
        _waiting[chat_id] = {"state": "edit_acc_proxy", "acc_id": aid}
        text = (
            "🌐 <b>Настройка прокси</b>\n\n"
            "Отправьте строку прокси в формате:\n"
            "<code>http://user:pass@ip:port</code>\n"
            "или <code>socks5://user:pass@ip:port</code>\n"
            "или <code>ip:port:user:pass</code>\n\n"
            "<i>Чтобы отключить прокси, отправьте 0 или слово 'нет'.</i>"
        )
        rows = [[("◀️ Отмена", f"mcp_acc_card:{aid}")]]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_acc_tpl_menu:"):
        aid = data.split(":")[1]
        cfg = _load_config()
        acc = next((a for a in cfg.get("accounts", []) if a.get("id") == aid), None)
        cur_tpl = acc.get("template") or cfg.get("global", {}).get("template", "")
        cur_clean = _clean_template_html(cur_tpl)
        text = (
            "📝 <b>Настройка шаблона ответа покупателю</b>\n\n"
            f"<b>Текущий шаблон:</b>\n<blockquote>{html.escape(cur_clean)}</blockquote>\n\n"
            "Доступные переменные:\n"
            "• <code>{code}</code> — проверочный код\n"
            "• <code>{service}</code> — название сервиса\n"
            "• <code>{email}</code> — адрес почты\n"
            "• <code>{time}</code> — время получения"
        )
        rows = [
            [("✏️ Задать свой шаблон", f"mcp_acc_tpl_edit:{aid}"), ("🔄 Сбросить на общий", f"mcp_acc_tpl_reset:{aid}")],
            [("◀️ Назад", f"mcp_acc_card:{aid}")],
        ]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_acc_tpl_reset:"):
        aid = data.split(":")[1]
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                a["template"] = ""
                break
        _save_config(cfg)
        _open_account_card(chat_id, aid, msg_id, notice="✅ Шаблон сброшен на общий.")

    elif data.startswith("mcp_acc_tpl_edit:"):
        aid = data.split(":")[1]
        _waiting[chat_id] = {"state": "edit_acc_tpl", "acc_id": aid}
        text = "📝 <b>Отправьте новый текст шаблона в чат.</b>\nОбязательно используйте <code>{code}</code> для вставки кода."
        rows = [[("◀️ Отмена", f"mcp_acc_tpl_menu:{aid}")]]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data == "mcp_add_wizard":
        _start_add_wizard(chat_id, msg_id)

    elif data.startswith("mcp_add_prov:"):
        prov_key = data.split(":")[1]
        _add_wizard_credentials_prompt(chat_id, prov_key, msg_id)

    elif data == "mcp_wiz_manual_outlook":
        _waiting[chat_id] = {"state": "waiting_credentials", "provider": "outlook"}
        text = (
            "✍️ <b>Ручной ввод данных Outlook (Шаг 2 из 4)</b>\n\n"
            "Отправьте данные в одном из удобных форматов:\n\n"
            "1️⃣ <b>Email:Пароль:Refresh_Token</b> (покупные почты):\n"
            "<code>email:password:refresh_token</code>\n\n"
            "2️⃣ <b>Только Refresh Token или ссылку из браузера:</b>\n"
            "<code>refresh_token</code> или <code>https://login.microsoftonline.com/...code=...</code>\n\n"
            "3️⃣ <b>Email:Пароль (авто-вход):</b>\n"
            "<code>email:password</code>\n\n"
            "4️⃣ <b>С прокси (опционально):</b>\n"
            "<code>email:password:token|http://ip:port</code>"
        )
        rows = [
            [("🌐 Вернуться к Авто-привязке", "mcp_add_prov:outlook")],
            [("◀️ Отмена", "mcp_acc_page:0")],
        ]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_add_serv:"):
        serv_key = data.split(":")[1]
        if serv_key == "custom":
            _waiting[chat_id]["state"] = "waiting_custom_filter"
            text = "✏️ <b>Введите ключевое слово или регулярное выражение</b> для фильтрации писем (например: <code>EA</code>, <code>Faceit</code>, <code>Instagram</code>):"
            rows = [[("◀️ Отмена", "mcp_acc_page:0")]]
            _tg_edit(chat_id, msg_id, text, _make_kb(rows))
        else:
            _add_wizard_command_prompt(chat_id, serv_key, msg_id)

    elif data == "mcp_add_cmd_default":
        sug_cmd = _waiting.get(chat_id, {}).get("suggested_command", "!code")
        _finish_add_account(chat_id, sug_cmd)

    elif data == "mcp_tgl_plugin":
        cfg = _load_config()
        cur = cfg.get("global", {}).get("plugin_enabled", True)
        cfg["global"]["plugin_enabled"] = not cur
        _save_config(cfg)
        _open_settings(chat_id, msg_id)

    elif data == "mcp_tgl_notif_cmd":
        cfg = _load_config()
        cur = cfg.get("global", {}).get("command_notifications_enabled", False)
        cfg["global"]["command_notifications_enabled"] = not cur
        _save_config(cfg)
        _open_notif_menu(chat_id, msg_id)

    elif data.startswith("mcp_tgl_notif:"):
        key = data.split(":")[1]
        cfg = _load_config()
        n = cfg.setdefault("notifications", {})
        n[key] = not n.get(key, True)
        _save_config(cfg)
        _open_notif_menu(chat_id, msg_id)

    elif data == "mcp_win_menu":
        _waiting[chat_id] = {"state": "waiting_input_win", "prompt_id": msg_id}
        cfg = _load_config()
        cur_w = cfg.get("global", {}).get("search_window_min", 15)
        text = (
            "⏱ <b>Настройка окна поиска писем</b>\n\n"
            f"Текущее значение: <b>{cur_w} мин</b>\n\n"
            "Плагин ищет проверочный код только в письмах не старше этого времени.\n\n"
            "✍️ <b>Напишите число (минут) в этот чат</b> (например: <code>10</code>, <code>15</code>, <code>30</code>, <code>60</code>)\n"
            "или выберите быстрый вариант:"
        )
        rows = [
            [("5 минут", "mcp_win_set:5"), ("15 минут", "mcp_win_set:15")],
            [("30 минут", "mcp_win_set:30"), ("60 минут", "mcp_win_set:60")],
            [("◀️ Назад", "mcp_settings")],
        ]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_win_set:"):
        m = int(data.split(":")[1])
        cfg = _load_config()
        cfg["global"]["search_window_min"] = m
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _open_settings(chat_id, msg_id)

    elif data == "mcp_cd_menu":
        _waiting[chat_id] = {"state": "waiting_input_cd", "prompt_id": msg_id}
        cfg = _load_config()
        cur_cd = cfg.get("global", {}).get("cooldown_sec", 10)
        text = (
            "⏳ <b>Настройка кулдауна анти-спама</b>\n\n"
            f"Текущее значение: <b>{cur_cd} сек</b>\n\n"
            "Минимальный интервал между повторными вызовами команды одним покупателем.\n\n"
            "✍️ <b>Напишите число (секунд) в этот чат</b> (например: <code>5</code>, <code>10</code>, <code>20</code>, <code>30</code>)\n"
            "или выберите быстрый вариант:"
        )
        rows = [
            [("5 секунд", "mcp_cd_set:5"), ("10 секунд", "mcp_cd_set:10")],
            [("20 секунд", "mcp_cd_set:20"), ("30 секунд", "mcp_cd_set:30")],
            [("◀️ Назад", "mcp_settings")],
        ]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data.startswith("mcp_cd_set:"):
        s = int(data.split(":")[1])
        cfg = _load_config()
        cfg["global"]["cooldown_sec"] = s
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _open_settings(chat_id, msg_id)

    elif data == "mcp_templates_menu":
        _open_templates_menu(chat_id, msg_id)

    elif data.startswith("mcp_tpl_edit:"):
        key = data.split(":")[1]
        _open_template_editor(chat_id, key, msg_id)

    elif data == "mcp_tgl_searching_msg":
        cfg = _load_config()
        cur = cfg.get("global", {}).get("send_searching_msg", False)
        cfg["global"]["send_searching_msg"] = not cur
        _save_config(cfg)
        _open_templates_menu(chat_id, msg_id)

    elif data == "mcp_tpl_reset_all":
        cfg = _load_config()
        cfg["templates"] = _default_templates()
        _save_config(cfg)
        _open_templates_menu(chat_id, msg_id)

    elif data.startswith("mcp_tpl_reset_one:"):
        key = data.split(":")[1]
        cfg = _load_config()
        cfg.setdefault("templates", {})[key] = _default_templates().get(key, "")
        _save_config(cfg)
        _open_template_editor(chat_id, key, msg_id)

    elif data == "mcp_maint":
        _open_maint_menu(chat_id, msg_id)

    elif data == "mcp_maint_cfg_menu":
        _open_maint_cfg_menu(chat_id, msg_id)

    elif data == "mcp_maint_cfg_export":
        if os.path.exists(CONFIG_FILE) and bot_instance:
            with open(CONFIG_FILE, "rb") as f:
                bot_instance.send_document(chat_id, f, caption="💾 Резервная копия config.json")
            _open_maint_cfg_menu(chat_id)

    elif data == "mcp_maint_cfg_import":
        _waiting[chat_id] = {"state": "waiting_import_cfg"}
        text = "📥 <b>Импорт конфигурации</b>\n\nОтправьте файл <code>config.json</code> документом в чат."
        rows = [[("◀️ Отмена", "mcp_maint_cfg_menu")]]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data == "mcp_maint_log_download":
        _flush_logs()
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) < 50:
            logger.info(f"{LP} === Лог плагина {NAME} v{VERSION} ===")
            logger.info(f"{LP} Время сервера: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            cfg = _load_config()
            accs = cfg.get("accounts", [])
            logger.info(f"{LP} Сохранено почт в конфиге: {len(accs)} шт.")
            for a in accs:
                logger.info(f"{LP} • Почта {a.get('email')} ({a.get('provider')}) -> команда '{a.get('command')}', сервис '{a.get('service')}'")
            _flush_logs()
        if os.path.exists(LOG_FILE) and bot_instance:
            with open(LOG_FILE, "rb") as f:
                bot_instance.send_document(chat_id, f, caption=f"📄 Лог плагина {NAME}")
            _open_maint_menu(chat_id)

    elif data == "mcp_bl_menu" or data.startswith("mcp_bl_page:"):
        p = int(data.split(":")[1]) if ":" in data else 0
        _open_blacklist_menu(chat_id, p, msg_id)

    elif data == "mcp_bl_tgl":
        cfg = _load_config()
        cur = cfg.get("global", {}).get("blacklist_enabled", False)
        cfg["global"]["blacklist_enabled"] = not cur
        _save_config(cfg)
        _open_blacklist_menu(chat_id, 0, msg_id)

    elif data == "mcp_bl_add":
        _waiting[chat_id] = {"state": "waiting_bl_nick"}
        text = (
            "⛔ <b>Добавление покупателя в чёрный список</b>\n\n"
            "Отправьте ник покупателя в этот чат (можно несколько через запятую):\n"
            "<i>(После добавления вы сможете выбрать: заблокировать полностью или только отдельные почты)</i>"
        )
        rows = [[("◀️ Отмена", "mcp_bl_menu")]]
        _tg_edit(chat_id, msg_id, text, _make_kb(rows))

    elif data == "mcp_bl_clear":
        cfg = _load_config()
        cfg["global"]["blacklist_nicks"] = []
        cfg["global"]["blacklist_users"] = {}
        _save_config(cfg)
        _open_blacklist_menu(chat_id, 0, msg_id)

    elif data.startswith("mcp_bl_u:"):
        nick = data.split(":", 1)[1]
        _open_bl_user_card(chat_id, nick, msg_id)

    elif data.startswith("mcp_bl_u_mode:"):
        nick = data.split(":", 1)[1]
        cfg = _load_config()
        users = _get_blacklist_users(cfg)
        if nick in users:
            cur_m = users[nick].get("mode", "all")
            users[nick]["mode"] = "selective" if cur_m == "all" else "all"
            _save_config(cfg)
        _open_bl_user_card(chat_id, nick, msg_id)

    elif data.startswith("mcp_bl_u_del:"):
        nick = data.split(":", 1)[1]
        cfg = _load_config()
        users = _get_blacklist_users(cfg)
        users.pop(nick, None)
        cfg["global"]["blacklist_nicks"] = [n for n in cfg["global"].get("blacklist_nicks", []) if n != nick]
        _save_config(cfg)
        _open_blacklist_menu(chat_id, 0, msg_id)

    elif data.startswith("mcp_bl_u_accs:"):
        _, nick, p_str = data.split(":")
        _open_bl_user_accs(chat_id, nick, int(p_str), msg_id)

    elif data.startswith("mcp_bl_u_tgl_acc:"):
        _, nick, aid, p_str = data.split(":")
        cfg = _load_config()
        users = _get_blacklist_users(cfg)
        if nick in users:
            b_accs = users[nick].setdefault("blocked_accounts", [])
            if aid in b_accs:
                b_accs.remove(aid)
            else:
                b_accs.append(aid)
            _save_config(cfg)
        _open_bl_user_accs(chat_id, nick, int(p_str), msg_id)

    elif data == "mcp_help_outlook":
        _open_info(chat_id, msg_id)

def _handle_waiting_message(message: Message):
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if not chat_id or chat_id not in _waiting:
        return

    st_info = _waiting[chat_id]
    state = st_info.get("state") or st_info.get("action")
    text = (message.text or "").strip()
    doc = getattr(message, "document", None)
    file_name = str(getattr(doc, "file_name", "") or "").strip() if doc else ""

    if state == "update_local" or file_name.endswith(".py"):
        if doc and file_name.endswith(".py"):
            try:
                f_info = bot_instance.get_file(doc.file_id)
                payload = bot_instance.download_file(f_info.file_path)
                source = payload.decode("utf-8-sig")
                plugin_file = os.path.abspath(__file__)
                compile(source, plugin_file, "exec")
                m = re.search(r'(?m)^\s*VERSION\s*=\s*["\']([^"\']+)["\']', source)
                new_ver = m.group(1).strip() if m else "неизвестно"
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup_file = plugin_file + f".pre-local-update.{stamp}.bak"
                shutil.copy2(plugin_file, backup_file)
                with open(plugin_file, "w", encoding="utf-8") as f:
                    f.write(source)
                _waiting.pop(chat_id, None)
                _tg_send(chat_id, f"✅ <b>Локальное обновление установлено: v{new_ver}!</b>\n\nРезервная копия: <code>{os.path.basename(backup_file)}</code>\nВыполните <code>/restart</code> для применения.", _make_kb([[("◀️ В меню", "mcp_home")]]))
                return
            except Exception as e:
                _tg_send(chat_id, f"❌ Ошибка проверки/установки файла: {e}")
                return
        elif not doc:
            _tg_send(chat_id, "⚠️ Отправьте файл плагина с расширением <code>.py</code> документом.")
            return

    if state in ("waiting_import_cfg", "import_config") or file_name.endswith(".json"):
        if not doc or not bot_instance:
            _tg_send(chat_id, "⚠️ Отправьте файл .json документом.")
            return
        try:
            f_info = bot_instance.get_file(doc.file_id)
            raw = bot_instance.download_file(f_info.file_path)
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data.get("accounts"), list):
                raise ValueError("Некорректная структура файла config.json.")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            if os.path.exists(CONFIG_FILE):
                shutil.copy2(CONFIG_FILE, CONFIG_FILE + f".{stamp}.bak")
            _save_config(data)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"✅ <b>Конфигурация успешно импортирована!</b> Загружено почт: <b>{len(data['accounts'])} шт.</b>", _make_kb([[("⚙️ В настройки", "mcp_settings")]]))
            _open_maint_menu(chat_id)
        except Exception as e:
            _tg_send(chat_id, f"❌ Ошибка импорта: {e}")
        return

    if state == "waiting_credentials":
        provider = st_info.get("provider", "outlook")
        proxy = ""

        if "|" in text:
            creds_part, proxy = text.split("|", 1)
            creds_part = creds_part.strip()
            proxy = proxy.strip()
        else:
            creds_part = text

        email_val = ""
        password_val = ""
        refresh_val = ""
        host_val = PROVIDERS.get(provider, {}).get("default_host", "")
        port_val = PROVIDERS.get(provider, {}).get("default_port", 993)

        if provider == "outlook":

            if "code=" in creds_part or (creds_part.startswith("M.C") and len(creds_part) > 20):
                ok_ex, tok_ex, em_ex = OutlookClient.exchange_code_or_url(creds_part, proxy=proxy)
                if ok_ex:
                    refresh_val = tok_ex
                    email_val = em_ex or "outlook_account"
                else:
                    _tg_send(chat_id, f"❌ Ошибка обмена кода из ссылки: {tok_ex}")
                    return
            else:
                parts = creds_part.split(":")
                if len(parts) >= 3:
                    email_val = parts[0].strip()
                    password_val = parts[1].strip()
                    refresh_val = ":".join(parts[2:]).strip()
                elif len(parts) == 2:
                    email_val = parts[0].strip()
                    password_val = parts[1].strip()
                elif len(parts) == 1 and ("." in creds_part or len(creds_part) > 40):
                    ok_ex, tok_ex, em_ex = OutlookClient.exchange_code_or_url(creds_part, proxy=proxy)
                    refresh_val = tok_ex if ok_ex else creds_part.strip()
                    email_val = em_ex or "outlook_account"
                else:
                    _tg_send(chat_id, "⚠️ Неверный формат. Используйте <code>email:password</code>, <code>email:password:refresh_token</code> или отправьте ссылку с кодом из браузера.")
                    return

        elif provider == "custom_imap":
            parts = creds_part.split(":")
            if len(parts) >= 4:
                host_val = parts[0].strip()
                port_val = int(parts[1].strip())
                email_val = parts[2].strip()
                password_val = ":".join(parts[3:]).strip()
            elif len(parts) == 2:
                email_val = parts[0].strip()
                password_val = parts[1].strip()
            else:
                _tg_send(chat_id, "⚠️ Неверный формат. Используйте <code>host:port:email:password</code>.")
                return
        else:
            parts = creds_part.split(":")
            if len(parts) >= 2:
                email_val = parts[0].strip()
                password_val = ":".join(parts[1:]).strip()
            else:
                _tg_send(chat_id, "⚠️ Неверный формат. Используйте <code>email:password</code>.")
                return

        _waiting[chat_id].update({
            "email": email_val,
            "password": password_val,
            "refresh_token": refresh_val,
            "imap_host": host_val,
            "imap_port": port_val,
            "proxy": proxy,
        })

        _tg_send(chat_id, "⏳ <i>Проверка подключения к почте...</i>")
        temp_acc = {
            "email": email_val,
            "password": password_val,
            "refresh_token": refresh_val,
            "provider": provider,
            "imap_host": host_val,
            "imap_port": port_val,
            "proxy": proxy,
        }
        ok, msg, lat = MailManager.test_connection(temp_acc)
        if ok:
            _tg_send(chat_id, f"🟢 <b>Подключение успешно!</b> ({lat} ms)\n{msg}")
            _add_wizard_service_prompt(chat_id)
        else:
            _tg_send(chat_id, (
                f"⚠️ <b>Предупреждение при проверке подключения:</b>\n<i>{msg}</i>\n\n"
                "Вы можете продолжить настройку (например, если почта заработает позже или нужен прокси) или отменить."
            ))
            _add_wizard_service_prompt(chat_id)
        return

    if state == "waiting_oauth_token":
        aid = st_info.get("acc_id")
        cfg = _load_config()
        acc = next((a for a in cfg.get("accounts", []) if a.get("id") == aid), None)
        if not acc:
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, "⚠️ Аккаунт не найден.")
            return

        _tg_send(chat_id, "⏳ <i>Проверка и привязка токена...</i>")
        ok, token_or_err, found_email = OutlookClient.exchange_code_or_url(text, proxy=acc.get("proxy"))
        if ok:
            acc["refresh_token"] = token_or_err
            if found_email and "@" in found_email and (acc.get("email") in ("outlook_account", "", None) or "@" not in acc.get("email", "")):
                acc["email"] = found_email
            _save_config(cfg)
            _waiting.pop(chat_id, None)
            logger.info(f"{LP} Refresh Token успешно привязан для {acc.get('email')}")

            ok_c, msg_c, lat_c = MailManager.test_connection(acc)
            res_str = f"🟢 Связь установлена: {msg_c} ({lat_c} ms)" if ok_c else f"🔴 Ошибка соединения: {msg_c} ({lat_c} ms)"
            _open_account_card(chat_id, aid, notice=f"✅ <b>Refresh Token успешно сохранен!</b>\n{res_str}")
        else:
            _tg_send(chat_id, f"❌ <b>Ошибка:</b> {token_or_err}\n\nПопробуйте ещё раз или нажмите отмена в меню.")
        return

    if state == "waiting_custom_filter":
        _waiting[chat_id]["custom_filter"] = text
        _add_wizard_command_prompt(chat_id, "custom")
        return

    if state == "waiting_command":
        cmd_val = text if text.startswith(("!", "/")) else f"!{text}"
        _finish_add_account(chat_id, cmd_val)
        return

    if state == "edit_acc_command":
        aid = st_info.get("acc_id")
        cmd_val = text if text.startswith(("!", "/")) else f"!{text}"
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                a["command"] = cmd_val
                break
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _open_account_card(chat_id, aid, notice=f"✅ Команда изменена на <code>{cmd_val}</code>")
        return

    if state == "edit_acc_proxy":
        aid = st_info.get("acc_id")
        px_val = "" if text in ("0", "нет", "-", "none") else text
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                a["proxy"] = px_val
                break
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _open_account_card(chat_id, aid, notice="✅ Прокси обновлен.")
        return

    if state == "edit_acc_tpl":
        aid = st_info.get("acc_id")
        cfg = _load_config()
        for a in cfg.get("accounts", []):
            if a.get("id") == aid:
                a["template"] = text
                break
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _open_account_card(chat_id, aid, notice="✅ Кастомный шаблон сохранён.")
        return

    if state == "waiting_input_win":
        try:
            val = int(text)
            if val < 1 or val > 1440:
                raise ValueError("Число должно быть от 1 до 1440 минут.")
            cfg = _load_config()
            cfg["global"]["search_window_min"] = val
            _save_config(cfg)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"✅ Окно поиска писем установлено: <b>{val} мин</b>")
            _open_settings(chat_id)
        except ValueError as e:
            _tg_send(chat_id, f"⚠️ Пожалуйста, введите корректное число минут от 1 до 1440: {e}")
        return

    if state == "waiting_input_cd":
        try:
            val = int(text)
            if val < 0 or val > 3600:
                raise ValueError("Число должно быть от 0 до 3600 секунд.")
            cfg = _load_config()
            cfg["global"]["cooldown_sec"] = val
            _save_config(cfg)
            _waiting.pop(chat_id, None)
            _tg_send(chat_id, f"✅ Кулдаун анти-спама установлен: <b>{val} сек</b>")
            _open_settings(chat_id)
        except ValueError as e:
            _tg_send(chat_id, f"⚠️ Пожалуйста, введите корректное число секунд от 0 до 3600: {e}")
        return

    if state == "edit_template":
        tpl_key = st_info.get("tpl_key", "code_issued")
        cfg = _load_config()
        cfg.setdefault("templates", {})[tpl_key] = text
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        lbl = TEMPLATE_LABELS.get(tpl_key, tpl_key)
        _tg_send(chat_id, f"✅ Шаблон <b>«{lbl}»</b> успешно обновлен!")
        _open_templates_menu(chat_id)
        return

    if state == "edit_global_tpl":
        cfg = _load_config()
        cfg.setdefault("templates", {})["code_issued"] = text
        cfg["global"]["template"] = text
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _tg_send(chat_id, "✅ Общий шаблон выдачи кода обновлен.")
        _open_settings(chat_id)
        return

    if state == "waiting_bl_nick":
        parts = [p.strip() for p in re.split(r"[\n,;]+", text) if p.strip()]
        cfg = _load_config()
        users = _get_blacklist_users(cfg)
        added = 0
        for p in parts:
            clean = p[1:] if p.startswith("@") else p
            if clean and clean not in users:
                users[clean] = {"mode": "all", "blocked_accounts": []}
                if clean not in cfg["global"].setdefault("blacklist_nicks", []):
                    cfg["global"]["blacklist_nicks"].append(clean)
                added += 1
        _save_config(cfg)
        _waiting.pop(chat_id, None)
        _tg_send(chat_id, f"✅ Добавлено покупателей в ЧС: <b>{added} чел.</b>")
        _open_blacklist_menu(chat_id, 0)
        return

def handle_new_message(cardinal: Cardinal, event: NewMessageEvent):
    try:
        msg = getattr(event, "message", None)
        if not msg:
            return

        if getattr(msg, "by_bot", False):
            return
        seller_id = getattr(getattr(cardinal, "account", None), "id", -2)
        if int(getattr(msg, "author_id", -1)) == int(seller_id):
            return

        raw_text = (getattr(msg, "text", "") or "").strip()
        cmd = _normalize_cmd(raw_text)
        if not cmd:
            return

        cfg = _load_config()
        glob = cfg.get("global", {})
        if not glob.get("plugin_enabled", True):
            return

        matched_acc = None
        for acc in cfg.get("accounts", []):
            if not acc.get("enabled", True):
                continue
            acc_cmd = _normalize_cmd(acc.get("command", ""))
            if acc_cmd and cmd == acc_cmd:
                matched_acc = acc
                break

        if not matched_acc:
            return

        chat_id = getattr(msg, "chat_id", None)
        buyer_id = str(getattr(msg, "author_id", "") or chat_id)
        buyer_nick = str(getattr(msg, "author", "") or "")

        _log_event("command_received", logging.INFO, cmd=cmd, chat_id=chat_id, buyer=buyer_nick, acc=matched_acc.get("email"))

        acc_notify = matched_acc.get("command_notifications_enabled", glob.get("command_notifications_enabled", False))
        if not acc_notify:
            _mark_recent_suppression()

        if glob.get("blacklist_enabled", False):
            users = _get_blacklist_users(cfg)
            norm_nick = _normalize_nick(buyer_nick)
            bl_entry = None
            for u_k, u_v in users.items():
                if _normalize_nick(u_k) == norm_nick:
                    bl_entry = u_v
                    break
            if bl_entry:
                is_blocked = False
                mode = bl_entry.get("mode", "all")
                if mode == "all":
                    is_blocked = True
                elif mode == "selective":
                    blocked_list = bl_entry.get("blocked_accounts", [])
                    if matched_acc.get("id") in blocked_list:
                        is_blocked = True
                if is_blocked:
                    bl_tpl = cfg.get("templates", {}).get("blacklist_blocked", "⛔ Вы находитесь в чёрном списке. Выдача кода недоступна.")
                    clean_bl_msg = _clean_html(bl_tpl.format(command=cmd))
                    cardinal.account.send_message(chat_id, clean_bl_msg)
                    _log_event("command_rejected_blacklist", logging.WARNING, buyer=buyer_nick, cmd=cmd, acc=matched_acc.get("email"))
                    if cfg.get("notifications", {}).get("blacklist_hit", False):
                        _send_tg_notification(cardinal, f"⛔ <b>[Mail Plugin] Запрос от пользователя из ЧС:</b>\n\n• <b>Покупатель:</b> <code>{buyer_nick}</code>\n• <b>Команда:</b> <code>{cmd}</code>\n• <b>Почта:</b> <code>{matched_acc.get('email')}</code>")
                    return

        cooldown_sec = int(glob.get("cooldown_sec", 10))
        now_ts = time.time()
        cache_key = f"{buyer_id}:{cmd}"
        last_req = _recent_buyer_requests.get(cache_key, 0)
        if (now_ts - last_req) < cooldown_sec:
            left_s = int(cooldown_sec - (now_ts - last_req))
            cd_tpl = cfg.get("templates", {}).get("cooldown_warning", "⏳ Пожалуйста, подождите {left} сек. перед повторным запросом кода.")
            clean_cd_msg = _clean_html(cd_tpl.format(left=left_s, command=cmd))
            cardinal.account.send_message(chat_id, clean_cd_msg)
            if cfg.get("notifications", {}).get("cooldown_hit", False):
                _send_tg_notification(cardinal, f"⏳ <b>[Mail Plugin] Спам командой (кулдаун):</b>\n\n• <b>Покупатель:</b> <code>{buyer_nick}</code>\n• <b>Команда:</b> <code>{cmd}</code>\n• <b>Осталось:</b> {left_s} сек.")
            return
        _recent_buyer_requests[cache_key] = now_ts

        serv_name = SERVICES.get(matched_acc.get("service", "all"), {}).get("name", matched_acc.get("service"))

        if glob.get("send_searching_msg", False):
            search_tpl = cfg.get("templates", {}).get("cmd_received", "🔍 Запрос принят! Ищу свежее письмо с кодом подтверждения... Пожалуйста, подождите.")
            clean_search_msg = _clean_html(_safe_format(search_tpl, command=cmd, service=serv_name, email=matched_acc.get("email", "")))
            cardinal.account.send_message(chat_id, clean_search_msg)

        search_win = int(glob.get("search_window_min", 15))
        ok, code_or_err, details = MailManager.fetch_code(matched_acc, max_age_min=search_win)

        if ok:
            detected_srv = details.get("service") or serv_name
            tpl = matched_acc.get("template") or cfg.get("templates", {}).get("code_issued", glob.get("template", ""))
            reply_text = _safe_format(
                tpl,
                code=code_or_err,
                service=detected_srv,
                email=matched_acc.get("email", ""),
                time=details.get("time", "только что"),
                command=cmd,
                subject=details.get("subject", ""),
                sender=details.get("sender", "—"),
            )
            clean_reply = _clean_html(reply_text)
            cardinal.account.send_message(chat_id, clean_reply)
            _log_event("code_issued_success", logging.INFO, buyer=buyer_nick, code="***", service=detected_srv, acc=matched_acc.get("email"))
            if cfg.get("notifications", {}).get("success", True):
                _send_tg_notification(
                    cardinal,
                    f"✅ <b>[Mail Plugin] Код выдан покупателю!</b>\n\n"
                    f"• <b>Покупатель:</b> <code>{buyer_nick}</code>\n"
                    f"• <b>Сервис:</b> {detected_srv}\n"
                    f"• <b>Почта:</b> <code>{matched_acc.get('email')}</code>\n"
                    f"• <b>Отправитель:</b> <code>{details.get('sender', '—')}</code>\n"
                    f"• <b>Время письма:</b> {details.get('time', 'только что')}\n"
                    f"• <b>Команда:</b> <code>{cmd}</code>\n"
                    f"• <b>Код:</b> <code>{code_or_err}</code>"
                )
        else:
            fail_tpl = cfg.get("templates", {}).get("code_not_found", (
                "❌ Свежий код подтверждения пока не найден на почте.\n\n"
                "Пожалуйста, запросите отправку кода заново на сайте/в приложении и напишите команду {command} на фанпее повторно через несколько секунд."
            ))
            clean_fail = _clean_html(_safe_format(fail_tpl, command=cmd, service=serv_name, email=matched_acc.get("email", ""), reason=code_or_err))
            cardinal.account.send_message(chat_id, clean_fail)
            _log_event("code_fetch_failed", logging.WARNING, buyer=buyer_nick, reason=code_or_err, acc=matched_acc.get("email"))

            err_lower = str(code_or_err).lower()
            if any(w in err_lower for w in ("auth", "login", "password", "proxy", "connect", "timeout", "token")):
                if cfg.get("notifications", {}).get("mail_error", True):
                    _send_tg_notification(
                        cardinal,
                        f"⚠️ <b>[Mail Plugin] Сбой подключения к почте:</b>\n\n"
                        f"• <b>Почта:</b> <code>{matched_acc.get('email')}</code>\n"
                        f"• <b>Провайдер:</b> {matched_acc.get('provider')}\n"
                        f"• <b>Ошибка:</b> <i>{code_or_err}</i>"
                    )
            elif cfg.get("notifications", {}).get("code_not_found", True):
                _send_tg_notification(
                    cardinal,
                    f"❌ <b>[Mail Plugin] Код не найден на почте:</b>\n\n"
                    f"• <b>Покупатель:</b> <code>{buyer_nick}</code>\n"
                    f"• <b>Сервис:</b> {serv_name}\n"
                    f"• <b>Почта:</b> <code>{matched_acc.get('email')}</code>\n"
                    f"• <b>Команда:</b> <code>{cmd}</code>\n"
                    f"• <b>Причина:</b> <i>{code_or_err}</i>"
                )

    except Exception as e:
        logger.error(f"{LP} Ошибка обработки сообщения чата: {e}")

def init_cardinal(cardinal: Cardinal, *args):
    global cardinal_instance, bot_instance, admin_chat_id
    cardinal_instance = cardinal
    bot_obj = getattr(getattr(cardinal, "telegram", None), "bot", None)
    globals()["bot_instance"] = bot_obj

    try:
        auth = getattr(getattr(cardinal, "telegram", None), "authorized_users", None)
        if isinstance(auth, dict) and auth:
            admin_chat_id = int(list(auth.keys())[0])
    except Exception:
        admin_chat_id = None

    try:
        _patch_telegram_notifications(cardinal)
    except Exception as e:
        logger.warning(f"{LP} Ошибка патча уведомлений: {e}")

    try:
        cardinal.add_telegram_commands(UUID, [
            ("mail", f"Открыть меню {NAME} (Почты и коды)", True),
            ("mail_menu", "Открыть главное меню почт", True),
        ])
    except Exception as e:
        logger.warning(f"{LP} Ошибка add_telegram_commands: {e}")

    if bot_obj:
        def _open_plugin_home(call):
            _answer_cb(call)
            cid = getattr(getattr(getattr(call, "message", None), "chat", None), "id", None)
            mid = getattr(getattr(call, "message", None), "message_id", None)
            if cid:
                _open_home(cid, mid)

        def _is_entry_call(data: Any) -> bool:
            s = str(data or "")
            edit = getattr(CBT, "EDIT_PLUGIN", "45")
            settings = getattr(CBT, "PLUGIN_SETTINGS", "47")
            return s.startswith(f"{edit}:{UUID}") or s.startswith(f"{settings}:{UUID}") or s == f"{UUID}:0"

        try:
            cardinal.telegram.cbq_handler(_open_plugin_home, func=lambda call: _is_entry_call(getattr(call, "data", None)))
        except Exception as e:
            logger.warning(f"{LP} Не удалось привязать кнопку настроек: {e}")

        try:
            bot_obj.register_callback_query_handler(
                _cb_router,
                func=lambda call: isinstance(getattr(call, "data", None), str) and call.data.startswith("mcp_")
            )
        except Exception as e:
            logger.warning(f"{LP} Не удалось привязать router: {e}")

        def _cmd_mail(message):
            cid = getattr(getattr(message, "chat", None), "id", None)
            if cid:
                _open_home(cid)

        try:
            bot_obj.register_message_handler(_cmd_mail, commands=["mail", "mail_menu", "mailcode"])
        except Exception as e:
            logger.warning(f"{LP} Не удалось привязать команду /mail: {e}")

        def _msg_predicate(message):
            cid = getattr(getattr(message, "chat", None), "id", None)
            return bool(cid and cid in _waiting)

        try:
            bot_obj.register_message_handler(_handle_waiting_message, func=_msg_predicate, content_types=["text", "document"])
            if hasattr(bot_obj, "message_handlers") and isinstance(bot_obj.message_handlers, list) and bot_obj.message_handlers:
                h = bot_obj.message_handlers.pop()
                bot_obj.message_handlers.insert(0, h)
        except Exception as e:
            logger.warning(f"{LP} Не удалось привязать обработчик ввода: {e}")

    try:
        _start_oauth_server()
    except Exception as e:
        logger.warning(f"{LP} Ошибка старта OAuth-сервера: {e}")

    logger.info(f"[{NAME}] Плагин инициализирован (v{VERSION})")
    _flush_logs()

def post_start_handler(cardinal: Cardinal, *args):
    try:
        _patch_telegram_notifications(cardinal)
    except Exception:
        pass

def on_delete(cardinal: Cardinal, *args):
    _waiting.clear()
    try:
        _stop_oauth_server()
    except Exception:
        pass
    _log_event("plugin_deleted", logging.INFO)

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_POST_START = [post_start_handler]
BIND_TO_NEW_MESSAGE = [handle_new_message]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [handle_new_message]
BIND_TO_DELETE = on_delete
