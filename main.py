"""Secret Santa. Python in the browser (PyScript) + Firebase Realtime Database (REST).

Rooms are shared: everyone with the link sees the same live roster and draw.
Layout in the database:
  rooms/<CODE>/state   -> {locked, created, people:{id:{name,t}}, matches:{id:id}, seen:{id:true}}
  rooms/<CODE>/photos/<id> -> data-URL string (kept out of `state` so polling stays light)
"""
import asyncio
import json
import random
from html import escape

from pyodide.ffi import create_proxy
from pyodide.http import pyfetch
from pyscript import document, window

DB = "https://secretsanta-32aa6-default-rtdb.firebaseio.com"
POLL_SECONDS = 3
CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
AVATARS = ["🎅", "🤶", "🧝", "🦌", "⛄", "🎄", "🎁", "🍪"]

room = None          # current room code
st = None            # latest state from the database (dict) or None
photos = {}          # id -> data URL, cached
ui = {"shown": None, "pending_photo": None, "busy": False, "error": ""}

app = document.getElementById("app")


# ---------- database ----------
async def db(method, path, body=None):
    kw = {"method": method}
    if body is not None:
        kw["body"] = json.dumps(body)
    r = await pyfetch(f"{DB}/{path}.json", **kw)
    if not r.ok:
        raise RuntimeError(f"db {method} {path}: {r.status}")
    return await r.json()


def ls_get(key):
    try:
        return window.localStorage.getItem(key)
    except Exception:
        return None


def ls_set(key, val):
    try:
        if val is None:
            window.localStorage.removeItem(key)
        else:
            window.localStorage.setItem(key, val)
    except Exception:
        pass


def me():
    return ls_get(f"santa_me_{room}") if room else None


# ---------- helpers ----------
def new_code():
    return "".join(random.choice(CODE_CHARS) for _ in range(5))


def draw_matches(ids):
    """Single random cycle: everyone gives one gift, receives one, nobody gets themselves."""
    order = ids[:]
    random.shuffle(order)
    return {order[i]: order[(i + 1) % len(order)] for i in range(len(order))}


def people_list():
    ppl = (st or {}).get("people") or {}
    return sorted(((pid, p) for pid, p in ppl.items()), key=lambda x: x[1].get("t", 0))


def avatar(pid, cls="avatar"):
    if pid and photos.get(pid):
        return f'<img class="{cls}" src="{photos[pid]}" alt="">'
    emoji = AVATARS[sum(map(ord, pid)) % len(AVATARS)] if pid else "🎁"
    return f'<div class="{cls}">{emoji}</div>'


def room_link():
    return f"{window.location.origin}{window.location.pathname}?room={room}"


def header(sub):
    return f'<h1>🎅 Secret Santa</h1><p class="sub">{sub}</p>'


def set_url(code):
    qs = f"?room={code}" if code else window.location.pathname
    window.history.replaceState(None, "", qs)


async def load_missing_photos():
    for pid, _ in people_list():
        if pid not in photos:
            try:
                photos[pid] = (await db("GET", f"rooms/{room}/photos/{pid}")) or ""
            except Exception:
                return
            roster_update()


# ---------- views ----------
def view_key():
    if room is None:
        return "home"
    if st is None:
        return "missing"
    if st.get("locked"):
        return "reveal" if me() else "pick"
    return "lobby" if me() else "join"


def show():
    key = view_key()
    if key != ui["shown"]:
        ui["shown"] = key
        {"home": v_home, "missing": v_missing, "join": v_join, "lobby": v_lobby,
         "pick": v_pick, "reveal": v_reveal}[key]()
    roster_update()


def v_home():
    app.innerHTML = header("Santa got fired. Let's make Christmas anyway.") + """
    <section class="card">
      <h2>Start a new group</h2>
      <p class="hint">You'll get a link to send to your friends. Everyone adds themselves on their own phone.</p>
      <button class="btn" style="width:100%" data-act="create">Create a Secret Santa 🎁</button>
    </section>
    <section class="card">
      <h2>Have a code?</h2>
      <div class="row">
        <input id="code" type="text" placeholder="ABCDE" maxlength="5" autocapitalize="characters" autocomplete="off">
        <button class="btn alt" data-act="joincode">Join</button>
      </div>
    </section>"""


def v_missing():
    app.innerHTML = header("Hmm.") + f"""
    <section class="card"><h2>Room {escape(room)} not found</h2>
    <p class="hint">Check the link or code.</p>
    <button class="btn" data-act="home">Back</button></section>"""


def v_join():
    app.innerHTML = header("You're invited! Add yourself to the list.") + f"""
    <section class="card">
      <h2>Who are you?</h2>
      <div class="row">
        <label for="photo" id="prevwrap" style="cursor:pointer">{preview_html()}</label>
        <input id="photo" type="file" accept="image/*">
        <input id="name" type="text" placeholder="Your name" maxlength="30" autocomplete="off">
        <button class="btn" data-act="join">Join</button>
      </div>
      <p class="hint">Tap the circle to add a photo (optional).</p>
      {err_html()}
    </section>
    <section class="card"><h2>Already in</h2><div id="roster"></div></section>"""


def v_lobby():
    app.innerHTML = header("You're in! Waiting for everyone.") + f"""
    <section class="card stage">
      <h2>Room code</h2>
      <div class="big-name" style="letter-spacing:.2em">{room}</div>
      <div class="row" style="justify-content:center">
        <button class="btn" data-act="copy">Copy link 🔗</button>
        <button class="btn alt" data-act="share">Share…</button>
      </div>
      <p class="hint" id="linktxt">{escape(room_link())}</p>
    </section>
    <section class="card"><h2>In the sleigh (<span id="count">0</span>)</h2><div id="roster"></div></section>
    <button class="btn alt" style="width:100%" id="lockbtn" data-act="lock" disabled>
      Everyone's in: lock &amp; draw 🎲 (need 3+)</button>
    <button class="muted-link" data-act="leave">Leave this room</button>"""


def v_pick():
    app.innerHTML = header("The draw is done. Which one are you?") + """
    <section class="card"><div id="roster"></div>
    <p class="hint">Tap your own face. Only pick yourself! 🤫</p></section>
    <button class="muted-link" data-act="home">Leave</button>"""


def v_reveal():
    pid = me()
    name = ((st.get("people") or {}).get(pid) or {}).get("name", "")
    again = bool((st.get("seen") or {}).get(pid))
    app.innerHTML = header(f"Hi {escape(name)}!") + f"""
    <section class="card stage">
      <div id="dice" class="dice-box"><span class="pips">🎲</span>{avatar(None)}</div>
      <div id="result" class="big-name">{"Your match is hidden. 🤫" if again else "Ready?"}</div>
      <div class="row" style="justify-content:center">
        <button id="rollbtn" class="btn" data-act="roll">{"Show me again" if again else "Roll the dice 🎲"}</button>
      </div>
      <p class="hint">Room {room}. Keep it secret!</p>
    </section>
    <button class="muted-link" data-act="notme">Not {escape(name)}? Switch</button>"""


def preview_html():
    p = ui["pending_photo"]
    return f'<img class="preview" src="{p}" alt="">' if p else '<div class="preview">📷</div>'


def err_html():
    return f'<p class="hint" style="color:#b00020">{escape(ui["error"])}</p>' if ui["error"] else ""


def roster_update():
    box = document.getElementById("roster")
    key = ui["shown"]
    if key in ("join", "lobby"):
        mine = me()
        items = "".join(
            f'<li>{avatar(pid)}<span class="name">{escape(p.get("name", "?"))}{" (you)" if pid == mine else ""}</span></li>'
            for pid, p in people_list()
        ) or '<li class="hint">Nobody yet.</li>'
        if box:
            box.innerHTML = f'<ul class="people">{items}</ul>'
        c = document.getElementById("count")
        if c:
            c.textContent = str(len(people_list()))
        b = document.getElementById("lockbtn")
        if b:
            b.disabled = len(people_list()) < 3
    elif key == "pick" and box:
        seen = st.get("seen") or {}
        tiles = "".join(
            f'<button class="tile" data-act="claim" data-id="{pid}">{avatar(pid)}<b>{escape(p.get("name", "?"))}</b>'
            f'<span class="done">{"✔ rolled" if seen.get(pid) else "tap to roll"}</span></button>'
            for pid, p in people_list()
        )
        box.innerHTML = f'<div class="grid">{tiles}</div>'


# ---------- sync ----------
async def refresh():
    global st
    if not room:
        return
    try:
        new = await db("GET", f"rooms/{room}/state")
    except Exception:
        return
    st = new
    # our entry got removed (e.g. we left on another tab)
    if st and me() and me() not in (st.get("people") or {}) and not st.get("locked"):
        ls_set(f"santa_me_{room}", None)
    show()
    await load_missing_photos()


async def poll_loop():
    while True:
        if room and not ui["busy"]:
            await refresh()
        await asyncio.sleep(POLL_SECONDS)


async def enter_room(code):
    global room, st
    room, st = code, None
    ui["shown"] = None
    set_url(code)
    await refresh()
    if st is None:
        ui["shown"] = None
        show()


# ---------- actions ----------
async def create_room():
    code = new_code()
    await db("PUT", f"rooms/{code}/state", {"locked": False, "created": int(window.Date.now())})
    await enter_room(code)


async def join():
    box = document.getElementById("name")
    name = box.value.strip()
    if not name:
        box.focus()
        return
    pid = f"p{int(window.Date.now())}{random.randint(0, 999)}"
    try:
        await db("PUT", f"rooms/{room}/state/people/{pid}", {"name": name, "t": int(window.Date.now())})
        if ui["pending_photo"]:
            await db("PUT", f"rooms/{room}/photos/{pid}", ui["pending_photo"])
            photos[pid] = ui["pending_photo"]
    except Exception:
        ui["error"] = "Couldn't reach the server. Try again."
        ui["shown"] = None
        show()
        return
    ui["pending_photo"], ui["error"] = None, ""
    ls_set(f"santa_me_{room}", pid)
    await refresh()


async def lock():
    await refresh()
    ids = [pid for pid, _ in people_list()]
    if len(ids) < 3 or st.get("locked"):
        return
    await db("PATCH", f"rooms/{room}/state", {"locked": True, "matches": draw_matches(ids)})
    await refresh()


async def leave():
    pid = me()
    if pid:
        await db("DELETE", f"rooms/{room}/state/people/{pid}")
        await db("DELETE", f"rooms/{room}/photos/{pid}")
        ls_set(f"santa_me_{room}", None)
    await refresh()


async def roll():
    if ui["busy"]:
        return
    ui["busy"] = True
    pid = me()
    target = (st.get("matches") or {}).get(pid)
    dice, result = document.getElementById("dice"), document.getElementById("result")
    btn = document.getElementById("rollbtn")
    btn.disabled = True
    if target not in photos:
        try:
            photos[target] = (await db("GET", f"rooms/{room}/photos/{target}")) or ""
        except Exception:
            pass
    others = [x for x, _ in people_list() if x != pid]
    dice.classList.add("rolling")
    result.textContent = "Rolling…"
    delay = 0.06
    for _ in range(16):
        dice.innerHTML = f'<span class="pips">🎲</span>{avatar(random.choice(others))}'
        await asyncio.sleep(delay)
        delay *= 1.12
    dice.classList.remove("rolling")
    dice.classList.add("win")
    tname = ((st.get("people") or {}).get(target) or {}).get("name", "?")
    dice.innerHTML = f'<span class="pips">🎁</span>{avatar(target)}'
    result.innerHTML = f"You're buying for<br>{escape(tname)}!"
    btn.textContent = "Show me again"
    btn.disabled = False
    ui["busy"] = False
    try:
        await db("PUT", f"rooms/{room}/state/seen/{pid}", True)
    except Exception:
        pass


def go(coro):
    asyncio.ensure_future(coro)


def on_click(ev):
    el = ev.target.closest("[data-act]")
    if not el:
        return
    act, pid = el.getAttribute("data-act"), el.getAttribute("data-id")
    if act == "create":
        go(create_room())
    elif act == "joincode":
        code = document.getElementById("code").value.strip().upper()
        if code:
            go(enter_room(code))
    elif act == "join":
        go(join())
    elif act == "lock":
        if window.confirm("Lock the list and draw names? Nobody can join after this."):
            go(lock())
    elif act == "leave":
        go(leave())
    elif act == "claim":
        ls_set(f"santa_me_{room}", pid)
        ui["shown"] = None
        show()
    elif act == "notme":
        ls_set(f"santa_me_{room}", None)
        ui["shown"] = None
        show()
    elif act == "roll":
        go(roll())
    elif act == "home":
        go(goto_home())
    elif act == "copy":
        try:
            window.navigator.clipboard.writeText(room_link())
            el.textContent = "Copied ✔"
        except Exception:
            window.prompt("Copy this link:", room_link())
    elif act == "share":
        if hasattr(window.navigator, "share"):
            window.navigator.share(to_js_obj({"title": "Secret Santa", "text": "Join our Secret Santa 🎅", "url": room_link()}))
        else:
            window.prompt("Copy this link:", room_link())


def to_js_obj(d):
    from pyodide.ffi import to_js
    return to_js(d, dict_converter=window.Object.fromEntries)


async def goto_home():
    global room, st
    room, st = None, None
    ui["shown"] = None
    set_url(None)
    show()


def on_key(ev):
    if ev.key != "Enter":
        return
    tid = ev.target.id
    if tid == "name":
        go(join())
    elif tid == "code":
        code = ev.target.value.strip().upper()
        if code:
            go(enter_room(code))


def on_change(ev):
    if ev.target.id != "photo":
        return
    f = ev.target.files.item(0)
    if not f:
        return
    reader = window.FileReader.new()

    def loaded(_):
        img = window.Image.new()

        def ready(_):
            size = 128
            side = min(img.naturalWidth, img.naturalHeight)
            canvas = document.createElement("canvas")
            canvas.width = canvas.height = size
            canvas.getContext("2d").drawImage(
                img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, size, size)
            ui["pending_photo"] = canvas.toDataURL("image/jpeg", 0.7)
            w = document.getElementById("prevwrap")
            if w:
                w.innerHTML = preview_html()

        img.onload = create_proxy(ready)
        img.src = reader.result

    reader.onload = create_proxy(loaded)
    reader.readAsDataURL(f)


def make_snow():
    box = document.getElementById("snowfall")
    for _ in range(36):
        f = document.createElement("div")
        f.className = "flake"
        f.textContent = random.choice(["❄", "❅", "❆"])
        f.style.left = f"{random.uniform(0, 100):.1f}%"
        f.style.fontSize = f"{random.uniform(10, 24):.0f}px"
        f.style.animationDuration = f"{random.uniform(7, 16):.1f}s"
        f.style.animationDelay = f"-{random.uniform(0, 16):.1f}s"
        box.appendChild(f)


make_snow()
document.addEventListener("click", create_proxy(on_click))
document.addEventListener("keydown", create_proxy(on_key))
document.addEventListener("change", create_proxy(on_change))

code = window.URLSearchParams.new(window.location.search).get("room")
if code:
    go(enter_room(code.upper()))
else:
    show()
asyncio.ensure_future(poll_loop())
