"""Application classification.

Text that reads well in Slack reads wrong in Outlook and is actively harmful in
a terminal.  Everything downstream keys off the category decided here.

Browsers are special: the executable says "chrome.exe" while the actual
application is Gmail, Google Docs or ChatGPT, so the window title (and the URL
when UI Automation can reach it) decides.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL = "email"
MESSAGING = "messaging"
DOCUMENT = "document"
CODE = "code"
TERMINAL = "terminal"
CHAT = "chat"
BROWSER = "browser"
GENERAL = "general"

CATEGORIES = (EMAIL, MESSAGING, DOCUMENT, CODE, TERMINAL, CHAT, BROWSER, GENERAL)

EXE_CATEGORIES: dict[str, tuple[str, str]] = {
    # exe -> (category, friendly name)
    "outlook.exe": (EMAIL, "Outlook"),
    "olk.exe": (EMAIL, "Outlook"),
    "thunderbird.exe": (EMAIL, "Thunderbird"),
    "mailspring.exe": (EMAIL, "Mailspring"),
    "hxoutlook.exe": (EMAIL, "Mail"),
    "slack.exe": (MESSAGING, "Slack"),
    "discord.exe": (MESSAGING, "Discord"),
    "whatsapp.exe": (MESSAGING, "WhatsApp"),
    "telegram.exe": (MESSAGING, "Telegram"),
    "signal.exe": (MESSAGING, "Signal"),
    "ms-teams.exe": (MESSAGING, "Microsoft Teams"),
    "teams.exe": (MESSAGING, "Microsoft Teams"),
    "skype.exe": (MESSAGING, "Skype"),
    "winword.exe": (DOCUMENT, "Word"),
    "onenote.exe": (DOCUMENT, "OneNote"),
    "notion.exe": (DOCUMENT, "Notion"),
    "obsidian.exe": (DOCUMENT, "Obsidian"),
    "soffice.bin": (DOCUMENT, "LibreOffice"),
    "wps.exe": (DOCUMENT, "WPS Writer"),
    "acrobat.exe": (DOCUMENT, "Acrobat"),
    "code.exe": (CODE, "VS Code"),
    "code - insiders.exe": (CODE, "VS Code Insiders"),
    "cursor.exe": (CODE, "Cursor"),
    "windsurf.exe": (CODE, "Windsurf"),
    "zed.exe": (CODE, "Zed"),
    "devenv.exe": (CODE, "Visual Studio"),
    "idea64.exe": (CODE, "IntelliJ IDEA"),
    "pycharm64.exe": (CODE, "PyCharm"),
    "webstorm64.exe": (CODE, "WebStorm"),
    "rider64.exe": (CODE, "Rider"),
    "clion64.exe": (CODE, "CLion"),
    "goland64.exe": (CODE, "GoLand"),
    "sublime_text.exe": (CODE, "Sublime Text"),
    "notepad++.exe": (CODE, "Notepad++"),
    "windowsterminal.exe": (TERMINAL, "Windows Terminal"),
    "wt.exe": (TERMINAL, "Windows Terminal"),
    "cmd.exe": (TERMINAL, "Command Prompt"),
    "powershell.exe": (TERMINAL, "PowerShell"),
    "pwsh.exe": (TERMINAL, "PowerShell"),
    "conhost.exe": (TERMINAL, "Console"),
    "alacritty.exe": (TERMINAL, "Alacritty"),
    "wezterm-gui.exe": (TERMINAL, "WezTerm"),
    "mintty.exe": (TERMINAL, "Git Bash"),
    "putty.exe": (TERMINAL, "PuTTY"),
    "chrome.exe": (BROWSER, "Chrome"),
    "msedge.exe": (BROWSER, "Edge"),
    "firefox.exe": (BROWSER, "Firefox"),
    "brave.exe": (BROWSER, "Brave"),
    "opera.exe": (BROWSER, "Opera"),
    "vivaldi.exe": (BROWSER, "Vivaldi"),
    "arc.exe": (BROWSER, "Arc"),
    "notepad.exe": (GENERAL, "Notepad"),
    "explorer.exe": (GENERAL, "Windows Explorer"),
}

# (regex over the window title or URL) -> (category, friendly name)
TITLE_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bgmail\b|\bmail\.google\b", re.I), EMAIL, "Gmail"),
    (re.compile(r"outlook\.(?:office|live|com)|\boutlook\b", re.I), EMAIL, "Outlook Web"),
    (re.compile(r"\bproton\s*mail\b", re.I), EMAIL, "Proton Mail"),
    (re.compile(r"\bzoho mail\b|\byahoo mail\b", re.I), EMAIL, "Webmail"),
    (re.compile(r"google docs|docs\.google", re.I), DOCUMENT, "Google Docs"),
    (re.compile(r"notion\.so|\bnotion\b", re.I), DOCUMENT, "Notion"),
    (re.compile(r"confluence|\bnotepad\b.*\bwiki\b", re.I), DOCUMENT, "Confluence"),
    (re.compile(r"overleaf", re.I), DOCUMENT, "Overleaf"),
    (re.compile(r"\bchatgpt\b|chat\.openai", re.I), CHAT, "ChatGPT"),
    (re.compile(r"\bclaude\b|claude\.ai", re.I), CHAT, "Claude"),
    (re.compile(r"\bgemini\b|\bbard\b", re.I), CHAT, "Gemini"),
    (re.compile(r"perplexity", re.I), CHAT, "Perplexity"),
    (re.compile(r"copilot\.microsoft|github copilot", re.I), CHAT, "Copilot"),
    (re.compile(r"\bslack\b", re.I), MESSAGING, "Slack"),
    (re.compile(r"web\.whatsapp|\bwhatsapp\b", re.I), MESSAGING, "WhatsApp"),
    (re.compile(r"\bdiscord\b", re.I), MESSAGING, "Discord"),
    (re.compile(r"\blinkedin\b", re.I), MESSAGING, "LinkedIn"),
    (re.compile(r"\bx\.com\b|\btwitter\b", re.I), MESSAGING, "X"),
    (re.compile(r"github\.com|gitlab\.com", re.I), CODE, "GitHub"),
    (re.compile(r"stackoverflow|stack overflow", re.I), CODE, "Stack Overflow"),
    (re.compile(r"codesandbox|replit|codepen", re.I), CODE, "Online IDE"),
    (re.compile(r"jira|linear\.app|asana|trello", re.I), DOCUMENT, "Project tracker"),
)

DEFAULT_STYLE: dict[str, str] = {
    EMAIL: "professional",
    MESSAGING: "casual",
    DOCUMENT: "neutral",
    CODE: "developer",
    TERMINAL: "developer",
    CHAT: "concise",
    BROWSER: "neutral",
    GENERAL: "neutral",
}

# Terminals get verbatim text: a "helpful" rewrite of a shell command is a bug,
# and potentially a destructive one.
LLM_BY_DEFAULT: dict[str, bool] = {
    EMAIL: True,
    MESSAGING: True,
    DOCUMENT: True,
    CODE: True,
    TERMINAL: False,
    CHAT: True,
    BROWSER: True,
    GENERAL: True,
}

# Electron and browser targets are far more reliable with a clipboard paste
# than with synthesised per-character key events.
INJECTION_BY_EXE: dict[str, str] = {
    "cmd.exe": "unicode",
    "powershell.exe": "unicode",
    "pwsh.exe": "unicode",
    "conhost.exe": "unicode",
    "mintty.exe": "clipboard",
    "slack.exe": "clipboard",
    "discord.exe": "clipboard",
    "whatsapp.exe": "clipboard",
    "ms-teams.exe": "clipboard",
    "teams.exe": "clipboard",
    "notion.exe": "clipboard",
    "obsidian.exe": "clipboard",
}


@dataclass
class AppIdentity:
    exe: str
    app_name: str
    category: str
    style: str
    llm_enabled: bool
    injection_method: str
    matched_on: str = "default"


def classify(
    exe: str = "",
    title: str = "",
    url: str = "",
    profiles: list[dict] | None = None,
) -> AppIdentity:
    """Resolve an executable/title/URL into an application identity."""
    exe_key = (exe or "").strip().lower()
    haystack = " ".join(x for x in (url, title) if x)

    # 1. User-defined profiles win over everything.
    for profile in sorted(profiles or [], key=lambda p: p.get("priority", 100)):
        if not profile.get("enabled", True):
            continue
        pattern = (profile.get("pattern") or "").lower()
        if not pattern:
            continue
        match_on = profile.get("match_on", "exe")
        subject = exe_key if match_on == "exe" else (url if match_on == "url" else title).lower()
        if not subject:
            continue
        hit = pattern == subject if match_on == "exe" else pattern in subject
        if not hit:
            continue
        category = profile.get("category") or GENERAL
        return AppIdentity(
            exe=exe_key,
            app_name=profile.get("app_name") or _fallback_name(exe_key),
            category=category,
            style=profile.get("style") or DEFAULT_STYLE.get(category, "neutral"),
            llm_enabled=bool(profile.get("llm_enabled", LLM_BY_DEFAULT.get(category, True))),
            injection_method=profile.get("injection_method", "auto"),
            matched_on="profile",
        )

    base = EXE_CATEGORIES.get(exe_key)
    base_category = base[0] if base else GENERAL
    app_name = base[1] if base else _fallback_name(exe_key)
    matched_on = "exe" if base else "default"

    # 2. For browsers and Electron shells the title is the real identity.
    if base_category in (BROWSER, GENERAL, MESSAGING, DOCUMENT) and haystack:
        for pattern, category, name in TITLE_RULES:
            if pattern.search(haystack):
                base_category = category
                app_name = name
                matched_on = "title"
                break

    return AppIdentity(
        exe=exe_key,
        app_name=app_name,
        category=base_category,
        style=DEFAULT_STYLE.get(base_category, "neutral"),
        llm_enabled=LLM_BY_DEFAULT.get(base_category, True),
        injection_method=INJECTION_BY_EXE.get(exe_key, "auto"),
        matched_on=matched_on,
    )


def _fallback_name(exe_key: str) -> str:
    if not exe_key:
        return "Unknown application"
    name = exe_key[:-4] if exe_key.endswith(".exe") else exe_key
    return name.replace("-", " ").replace("_", " ").title()


def builtin_profiles() -> list[dict]:
    """Seed rows for the application_profiles table, shown in Settings."""
    rows: list[dict] = []
    for exe, (category, name) in EXE_CATEGORIES.items():
        rows.append(
            {
                "pattern": exe,
                "match_on": "exe",
                "app_name": name,
                "category": category,
                "style": DEFAULT_STYLE.get(category, "neutral"),
                "llm_enabled": LLM_BY_DEFAULT.get(category, True),
                "injection_method": INJECTION_BY_EXE.get(exe, "auto"),
                "builtin": True,
                "enabled": True,
                "priority": 200,
            }
        )
    for pattern, category, name in TITLE_RULES:
        rows.append(
            {
                "pattern": name.lower(),
                "match_on": "title",
                "app_name": name,
                "category": category,
                "style": DEFAULT_STYLE.get(category, "neutral"),
                "llm_enabled": LLM_BY_DEFAULT.get(category, True),
                "injection_method": "auto",
                "builtin": True,
                "enabled": True,
                "priority": 150,
            }
        )
    return rows
