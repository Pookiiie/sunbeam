"""Which games are installed on this PC, and how to start (or install) them. Windows only.

Read from each launcher's own records, never by searching your drives:
  Steam      libraryfolders.vdf → each library's appmanifest_<appid>.acf (fully installed ones)
  Epic       the launcher's install manifests (ProgramData\\Epic\\EpicGamesLauncher\\Data\\Manifests)
  Ubisoft    the launcher's registry list of installs, named by its "Uplay Install <id>" uninstall entry
  GOG        GOG's registry list of installed games (written by GOG Galaxy and GOG's offline installers)
  Battle.net Blizzard's uninstall entries (Windows' Installed apps list)
  Xbox app   installed packages (Get-AppxPackage) that carry a MicrosoftGame.config (PC Game Pass
             and other Xbox app games): its TitleId and StoreId match the Xbox history and XGP list

Starting a game goes through its launcher (steam://rungameid/…, the Epic launcher's link,
GOG Galaxy (or the game itself when Galaxy isn't installed), uplay://, battlenet://, or the Xbox app package), so sign-in, updates and cloud saves work
as usual. The scan runs in the background at start-up, after every sync, and when the window
comes back into focus (at most every couple of minutes); the result is the `installs` table.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import db, desktop

SCAN_EVERY = 120  # seconds between focus-triggered scans
_lock = threading.Lock()
_last = {"at": 0.0}
errors: dict[str, str] = {}
last_found: list[dict] = []  # what the last scan saw, matched or not (GET /api/installed?details=1)


# ------------------------------------------------------------------ small parsers (testable anywhere)

def parse_vdf(text: str) -> dict:
    """Valve's KeyValues text (libraryfolders.vdf, appmanifest .acf) → nested dicts."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root: dict = {}
    stack = [root]
    key = None
    for s, brace in tokens:
        if brace == "{":
            new: dict = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = s.replace("\\\\", "\\")
        else:
            stack[-1][key] = s.replace("\\\\", "\\")
            key = None
    return root


def steam_libraries(vdf_text: str) -> list[str]:
    lf = parse_vdf(vdf_text)
    lf = lf.get("libraryfolders") or lf.get("LibraryFolders") or {}
    out = []
    for k, v in lf.items():
        if isinstance(v, dict) and v.get("path"):
            out.append(v["path"])
        elif isinstance(v, str) and k.isdigit():
            out.append(v)  # the old format: "1" "D:\\SteamLibrary"
    return out


def steam_manifest(acf_text: str) -> dict | None:
    st = parse_vdf(acf_text).get("AppState") or {}
    try:
        flags = int(st.get("StateFlags") or 0)
    except ValueError:
        flags = 0
    if not st.get("appid") or not flags & 4:  # 4 = fully installed
        return None
    return {"appid": st["appid"], "name": st.get("name") or "", "dir": st.get("installdir") or ""}


def epic_manifest(text: str) -> dict | None:
    try:
        m = json.loads(text)
    except ValueError:
        return None
    if m.get("bIsIncompleteInstall") or not m.get("CatalogItemId") or not m.get("AppName"):
        return None
    if m.get("MainGameAppName") and m.get("MainGameAppName") != m.get("AppName"):
        return None  # an add-on of another game
    return {"id": m["CatalogItemId"], "ns": m.get("CatalogNamespace") or "", "app": m["AppName"],
            "name": m.get("DisplayName") or "", "dir": m.get("InstallLocation") or ""}


def xbox_config(xml_text: str) -> dict | None:
    """MicrosoftGame.config → {title_id (decimal, like the Xbox history), store_id, name, app_id}."""
    try:
        root = ET.fromstring(xml_text.lstrip("\ufeff"))
    except ET.ParseError:
        return None
    def find(tag):
        for el in root.iter():
            if el.tag.split("}")[-1] == tag:
                return el
        return None
    tid = (find("TitleId").text or "").strip() if find("TitleId") is not None else ""
    sid = (find("StoreId").text or "").strip() if find("StoreId") is not None else ""
    vis = find("ShellVisuals")
    name = vis.get("DefaultDisplayName", "") if vis is not None else ""
    exe = find("Executable")
    app_id = exe.get("Id", "") if exe is not None else ""
    try:
        title_id = str(int(tid, 16)) if tid else ""
    except ValueError:
        title_id = ""
    if not (title_id or sid):
        return None
    return {"title_id": title_id, "store_id": sid, "name": name, "app_id": app_id or "Game"}


def epic_launch(ns: str, item: str, app: str) -> str:
    return f"com.epicgames.launcher://apps/{ns}%3A{item}%3A{app}?action=launch&silent=true"


# ------------------------------------------------------------------ Windows readers

def _reg_values(root, path: str) -> dict:
    import winreg
    out = {}
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except OSError:
        pass
    return out


def _reg_subkeys(root, path: str) -> list[str]:
    import winreg
    out = []
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return out


UNINSTALL = [r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"]


def _uninstall_entries():
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in UNINSTALL:
            for sub in _reg_subkeys(root, base):
                yield sub, _reg_values(root, base + "\\" + sub)


def scan_steam() -> list[dict]:
    import winreg
    path = _reg_values(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam").get("SteamPath") \
        or _reg_values(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam").get("InstallPath")
    if not path:
        return []
    steam = Path(path)
    libs = {str(steam)}
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        libs.update(steam_libraries(vdf.read_text("utf-8", "replace")))
    out = []
    for lib in libs:
        for acf in (Path(lib) / "steamapps").glob("appmanifest_*.acf"):
            try:
                m = steam_manifest(acf.read_text("utf-8", "replace"))
            except OSError:
                continue
            if m:
                out.append({"platform": "steam", "ref": m["appid"], "name": m["name"],
                            "launch": f"steam://rungameid/{m['appid']}",
                            "dir": str(Path(lib) / "steamapps" / "common" / m["dir"])})
    return out


def scan_epic() -> list[dict]:
    folder = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    out = []
    for f in folder.glob("*.item"):
        try:
            m = epic_manifest(f.read_text("utf-8", "replace"))
        except OSError:
            continue
        if m:
            out.append({"platform": "epic", "ref": m["id"], "name": m["name"],
                        "launch": epic_launch(m["ns"], m["id"], m["app"]), "dir": m["dir"]})
    return out


def scan_ubisoft() -> list[dict]:
    import winreg
    base = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
    names = {}
    for sub, vals in _uninstall_entries():
        m = re.fullmatch(r"Uplay Install (\d+)", sub)
        if m and vals.get("DisplayName"):
            names[m.group(1)] = vals["DisplayName"]
    out = []
    for pid in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, base):
        d = _reg_values(winreg.HKEY_LOCAL_MACHINE, base + "\\" + pid).get("InstallDir") or ""
        if not d or not Path(d).exists():
            continue
        name = names.get(pid) or Path(d.rstrip("/\\")).name
        out.append({"platform": "ubisoft", "ref": pid, "name": name, "launch": f"uplay://launch/{pid}/0", "dir": d})
    return out


# Battle.net's install id (the uninstaller's --uid=) → the product code Sunbeam's Battle.net
# import uses (account.battle.net's list). Windows' name for a game is often shorter than the
# store's ("Warcraft III" for Warcraft III: Reforged), so the code is what matches.
BNET_UIDS = {
    "w3": "W3", "prometheus": "PRO", "fenris": "FEN", "diablo3": "D3", "osi": "OSI", "s1": "S1", "s2": "S2",
    "hs_beta": "WTCG", "heroes": "HERO", "wow": "WOW", "wow_classic": "WOW", "wow_classic_era": "WOW",
    "anbs": "ANBS", "gryphon": "GRY", "odin": "ODIN", "viper": "VIPR", "zeus": "ZEUS", "fore": "FORE",
    "auks": "AUKS", "lazarus": "LAZR", "rtro": "RTRO", "wlby": "WLBY", "destiny2": "DST2", "aqua": "AQUA",
}


def scan_battlenet() -> list[dict]:
    out = []
    for sub, vals in _uninstall_entries():
        pub, name = vals.get("Publisher") or "", vals.get("DisplayName") or ""
        uninstall = (vals.get("UninstallString") or "").lower()
        # Blizzard's games, and Activision's installed through Battle.net (its uninstaller)
        from_bnet = "blizzard" in pub.lower() or "battle.net\\agent" in uninstall or "blizzard uninstaller" in uninstall
        if not from_bnet or not name or name.lower().startswith("battle.net"):
            continue
        uid = re.search(r"--uid=([\w-]+)", vals.get("UninstallString") or "")
        ref = uid.group(1) if uid else sub
        out.append({"platform": "battlenet", "ref": ref, "name": name, "code": BNET_UIDS.get(ref.lower(), ""),
                    "launch": None, "dir": vals.get("InstallLocation") or ""})
    return out


GOG_GAMES = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def gog_client() -> str | None:
    """GOG Galaxy's GalaxyClient.exe, when Galaxy is installed."""
    import winreg
    for path in (r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient\paths", r"SOFTWARE\GOG.com\GalaxyClient\paths"):
        d = _reg_values(winreg.HKEY_LOCAL_MACHINE, path).get("client")
        if d and (Path(d) / "GalaxyClient.exe").exists():
            return str(Path(d) / "GalaxyClient.exe")
    default = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "GOG Galaxy" / "GalaxyClient.exe"
    return str(default) if default.exists() else None


def gog_entry(game_key: str, vals: dict) -> dict | None:
    """One game from GOG's registry list → what the scan keeps (None for DLC and broken entries).
    The value names' case varies between installers (gameID, GAMENAME, PATH, EXE, LAUNCHCOMMAND…)."""
    v = {str(k).lower(): val for k, val in vals.items()}
    gid = str(v.get("gameid") or v.get("productid") or game_key or "").strip()
    path = str(v.get("path") or v.get("workingdir") or "")
    if not gid or not path or str(v.get("dependson") or "").strip():
        return None  # dependsOn: a DLC of another game
    return {"id": gid, "name": str(v.get("gamename") or ""), "dir": path,
            "exe": str(v.get("exe") or v.get("launchcommand") or ""), "params": str(v.get("launchparam") or "")}


def gog_launch(entry: dict, client: str | None) -> str | None:
    """How to start it: through GOG Galaxy when it's installed (so playtime and cloud saves count), else the game itself."""
    if client:
        return "cmd:" + json.dumps([client, "/command=runGame", f"/gameId={entry['id']}", f"/path={entry['dir']}"])
    exe = (entry.get("exe") or "").strip().strip('"')
    if exe and Path(exe).exists():
        return "cmd:" + json.dumps([exe] + ([entry["params"]] if entry.get("params") else []))
    return None


def scan_gog() -> list[dict]:
    import winreg
    client = gog_client()
    out = []
    for key in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, GOG_GAMES):
        e = gog_entry(key, _reg_values(winreg.HKEY_LOCAL_MACHINE, GOG_GAMES + "\\" + key))
        if not e or not Path(e["dir"]).exists():
            continue
        out.append({"platform": "gog", "ref": e["id"], "name": e["name"], "launch": gog_launch(e, client), "dir": e["dir"]})
    return out


PS_PACKAGES = ("Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.InstallLocation } | "
               "ForEach-Object { $_.PackageFamilyName + '|' + $_.InstallLocation }")


def scan_xbox() -> list[dict]:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_PACKAGES],
                       capture_output=True, text=True, timeout=60, creationflags=desktop.NO_WINDOW)
    out = []
    for line in r.stdout.splitlines():
        pfn, _, loc = line.strip().partition("|")
        if not pfn or not loc:
            continue
        cfg = Path(loc) / "MicrosoftGame.config"
        try:
            if not cfg.exists():
                continue
            m = xbox_config(cfg.read_text("utf-8", "replace"))
        except OSError:
            continue
        if m:
            out.append({"platform": "xbox", "ref": m["title_id"] or m["store_id"], "name": m["name"],
                        "store_id": m["store_id"], "title_id": m["title_id"],
                        "launch": f"shell:AppsFolder\\{pfn}!{m['app_id']}", "dir": loc})
    return out


SCANNERS = {"steam": scan_steam, "epic": scan_epic, "gog": scan_gog, "ubisoft": scan_ubisoft,
            "battlenet": scan_battlenet, "xbox": scan_xbox}


# ------------------------------------------------------------------ matching to your games

def match(found: list[dict]) -> list[tuple[int, dict]]:
    """Pair what's installed with games in the library: by store id first, then by name."""
    entries = db.query("SELECT game_id, platform, external_id FROM entries")
    by_ext = {(e["platform"], str(e["external_id"])): e["game_id"] for e in entries}
    by_appid = {str(r["steam_appid"]): r["id"] for r in db.query("SELECT id, steam_appid FROM games WHERE steam_appid IS NOT NULL")}
    titles: dict[str, list[tuple[int, set]]] = {}
    plats: dict[int, set] = {}
    for e in entries:
        plats.setdefault(e["game_id"], set()).add(e["platform"])
    for r in db.query("SELECT id, norm_title FROM games"):
        titles.setdefault(r["norm_title"], []).append((r["id"], plats.get(r["id"], set())))

    def by_name(name: str, platform: str) -> int | None:
        key = db.normalize_title(db.strip_pc_tags(name or ""))
        cands = titles.get(key) or []
        own = [gid for gid, p in cands if platform in p]
        if own or cands:
            return (own or [gid for gid, _ in cands])[0]
        # Windows' shorter name for a game on that store: "Warcraft III" → "Warcraft III: Reforged"
        if len(key) >= 4:
            pre = [gid for t, lst in titles.items() if t.startswith(key + " ") for gid, p in lst if platform in p]
            if len(pre) == 1:
                return pre[0]
        return None

    by_code = {str(e["external_id"]).upper(): e["game_id"] for e in entries if e["platform"] == "battlenet"}

    out = []
    for f in found:
        p, ref = f["platform"], str(f["ref"])
        gid = by_ext.get((p, ref))
        if gid is None and p == "steam":
            gid = by_appid.get(ref)
        if gid is None and p == "battlenet" and f.get("code"):
            gid = by_code.get(f["code"]) or next((g for c, g in by_code.items() if c.split("_")[0] == f["code"]), None)
        if gid is None and p == "xbox":
            gid = by_ext.get(("xbox", f.get("title_id") or "")) or by_ext.get(("xbox", "xgp:" + (f.get("store_id") or "")))
        if gid is None and p in ("epic", "gog", "ubisoft", "battlenet", "xbox"):
            gid = by_name(f["name"], p)
        if gid is not None:
            out.append((gid, f))
    return out


def scan() -> dict:
    """Look again and store the result. Returns {"at", "count", "errors"}."""
    if not desktop.is_windows():
        return {"at": None, "count": 0, "errors": {}}
    with _lock:
        found, errs = [], {}
        for name, fn in SCANNERS.items():
            try:
                found += fn()
            except Exception as e:  # one launcher's oddity never stops the others
                errs[name] = str(e)[:200]
        pairs = match(found)
        matched = {id(f): gid for gid, f in pairs}
        last_found[:] = [{"platform": f["platform"], "ref": str(f["ref"]), "name": f["name"], "game_id": matched.get(id(f)),
                          "can_play": bool(f.get("launch")) or f["platform"] == "battlenet"} for f in found]
        codes = {r["game_id"]: r["external_id"] for r in db.query("SELECT game_id, external_id FROM entries WHERE platform='battlenet'")}
        with db.tx() as c:
            c.execute("DELETE FROM installs")
            for gid, f in pairs:
                launch = f["launch"]
                if f["platform"] == "battlenet":
                    code = codes.get(gid) or f.get("code")
                    launch = f"battlenet://{code}" if code else None
                c.execute("INSERT OR REPLACE INTO installs(game_id, platform, ref, name, launch, dir, seen_at) VALUES (?,?,?,?,?,?,?)",
                          (gid, f["platform"], str(f["ref"]), f["name"], launch, f.get("dir") or "", db.now()))
        _last["at"] = time.time()
        errors.clear()
        errors.update(errs)
        db.set_meta("installs_at", str(db.now()))
        return {"at": db.now(), "count": len(pairs), "errors": errs}


def scan_soon(delay: float = 3) -> None:
    def run():
        try:
            scan()
        except Exception as e:
            print(f"  Couldn't check installed games: {e}")
    t = threading.Timer(delay, run)
    t.daemon = True
    t.start()


def scan_if_stale() -> bool:
    """When the window comes back into focus: look again unless we just did."""
    if time.time() - _last["at"] < SCAN_EVERY or _lock.locked():
        return False
    scan_soon(0)
    return True


def by_game() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in db.query("SELECT game_id, platform, MAX(launch IS NOT NULL) AS can FROM installs GROUP BY game_id, platform"):
        out.setdefault(r["game_id"], []).append({"platform": r["platform"], "can_play": bool(r["can"])})
    return out


# ------------------------------------------------------------------ starting and installing

def _open(uri: str) -> None:
    if uri.startswith("cmd:"):  # a program to run (GOG Galaxy, or a GOG game without Galaxy)
        args = json.loads(uri[4:])
        exe = Path(args[0])
        subprocess.Popen(args, cwd=str(exe.parent), close_fds=True)
    elif uri.startswith("shell:AppsFolder"):
        subprocess.Popen(["explorer.exe", uri], creationflags=desktop.NO_WINDOW, close_fds=True)
    else:
        desktop.open_external(uri)


def play(game_id: int, platform: str | None = None) -> str:
    """Start an installed copy (the one you picked, else the one you played last). Returns its platform."""
    rows = db.query("SELECT platform, launch FROM installs WHERE game_id=? AND launch IS NOT NULL", (game_id,))
    if platform:
        rows = [r for r in rows if r["platform"] == platform]
    if not rows:
        raise LookupError("This game isn't installed on this PC (as far as its launcher says).")
    last = {r["platform"]: (r["last_played"] or 0, r["playtime_min"] or 0)
            for r in db.query("SELECT platform, MAX(last_played) last_played, MAX(playtime_min) playtime_min "
                              "FROM entries WHERE game_id=? GROUP BY platform", (game_id,))}
    best = max(rows, key=lambda r: last.get(r["platform"], (0, 0)))
    _open(best["launch"])
    return best["platform"]


def install_link(game_id: int, platform: str) -> str | None:
    """Where to install a copy you have but haven't installed: the store's own install page."""
    ents = db.query("SELECT external_id, ownership FROM entries WHERE game_id=? AND platform=?", (game_id, platform))
    if platform == "steam":
        appid = next((e["external_id"] for e in ents if str(e["external_id"]).isdigit()), None)
        if not appid:
            row = db.query("SELECT steam_appid FROM games WHERE id=?", (game_id,))
            appid = row[0]["steam_appid"] if row else None
        return f"steam://install/{appid}" if appid else None
    if platform == "xbox":
        sid = next((str(e["external_id"])[4:] for e in ents if str(e["external_id"]).startswith("xgp:")), None)
        return f"msxbox://game/?productId={sid}" if sid else None
    if platform == "battlenet":
        code = next((e["external_id"] for e in ents), None)
        return f"battlenet://{code}" if code else None
    if platform == "epic":
        return "com.epicgames.launcher://store"
    if platform == "ubisoft":
        return "uplay://"
    if platform == "gog":
        gid = next((e["external_id"] for e in ents if str(e["external_id"]).isdigit()), None)
        if desktop.is_windows() and gog_client() and gid:
            return f"goggalaxy://openGameView/gog_{gid}"
        return "https://www.gog.com/en/account"
    return None


def install(game_id: int, platform: str) -> None:
    link = install_link(game_id, platform)
    if not link:
        raise LookupError("There's no way to install this copy from here.")
    _open(link)
