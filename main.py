"""Secret Santa app. Runs entirely in the browser via PyScript (Pyodide).

State lives in localStorage on this device only (no backend).
"""
import asyncio
import json
import random
from html import escape

from pyodide.ffi import create_proxy
from pyscript import document, window

KEY = "secret_santa_v1"
AVATARS = ["🎅", "🤶", "🧝", "🦌", "⛄", "🎄", "🎁", "🍪"]

state = {"people": [], "matches": {}, "locked": False}
ui = {"view": "setup", "who": None, "pending_photo": None, "busy": False}

app = document.getElementById("app")


# ---------- storage ----------
def load():
    try:
        raw = window.localStorage.getItem(KEY)
        if raw:
            state.update(json.loads(raw))
    except Exception:
        pass


def save():
    try:
        window.localStorage.setItem(KEY, json.dumps(state))
    except Exception:
        window.alert("Couldn't save (storage full?). Try smaller photos or fewer people.")


# ---------- logic ----------
def draw_matches(ids):
    """Sattolo's algorithm: one big cycle, so nobody draws themselves."""
    order = ids[:]
    random.shuffle(order)
    return {order[i]: order[(i + 1) % len(order)] for i in range(len(order))}


def person(pid):
    return next((p for p in state["people"] if p["id"] == pid), None)


def avatar_html(p, cls="avatar"):
    if p and p.get("photo"):
        return f'<img class="{cls}" src="{p["photo"]}" alt="">'
    emoji = AVATARS[sum(map(ord, p["id"])) % len(AVATARS)] if p else "🎁"
    return f'<div class="{cls}">{emoji}</div>'


# ---------- views ----------
def render():
    {"setup": render_setup, "pick": render_pick, "reveal": render_reveal}[ui["view"]]()


def header(sub):
    return f'<h1>🎅 Secret Santa</h1><p class="sub">{sub}</p>'


def render_setup():
    pending = ui["pending_photo"]
    preview = (f'<img class="preview" src="{pending}" alt="">' if pending
               else '<div class="preview">📷</div>')
    items = "".join(
        f'<li>{avatar_html(p)}<span class="name">{escape(p["name"])}</span>'
        f'<button class="x" data-act="remove" data-id="{p["id"]}" aria-label="Remove">✖</button></li>'
        for p in state["people"]
    ) or '<li class="hint">Nobody yet. Add at least 3 people.</li>'
    n = len(state["people"])
    app.innerHTML = header("Santa got fired. Let's make Christmas anyway.") + f"""
    <section class="card">
      <h2>1. Add everyone</h2>
      <div class="row">
        <label for="photo" style="cursor:pointer">{preview}</label>
        <input id="photo" type="file" accept="image/*">
        <input id="name" type="text" placeholder="Name" maxlength="30" autocomplete="off">
        <button class="btn" data-act="add">Add</button>
      </div>
      <p class="hint">Tap the circle to pick a photo (optional).</p>
    </section>
    <section class="card">
      <h2>The list ({n})</h2>
      <ul class="people">{items}</ul>
    </section>
    <button class="btn alt" style="width:100%" data-act="lock" {"disabled" if n < 3 else ""}>
      2. Lock the list &amp; start rolling 🎲</button>
    {"<button class='muted-link' data-act='reset'>Start over</button>" if n else ""}
    """
    document.getElementById("photo").addEventListener("change", on_photo)
    document.getElementById("name").addEventListener("keydown", on_name_key)


def render_pick():
    tiles = "".join(
        f'<button class="tile" data-act="pick" data-id="{p["id"]}">{avatar_html(p)}'
        f'<b>{escape(p["name"])}</b>'
        f'<span class="done">{"✔ rolled" if p["id"] in state.get("seen", []) else "tap to roll"}</span></button>'
        for p in state["people"]
    )
    app.innerHTML = header("Who are you? Tap your face, then roll the dice.") + f"""
    <section class="card"><div class="grid">{tiles}</div>
    <p class="hint">Pass the device around. Keep your match secret! 🤫</p></section>
    <button class="muted-link" data-act="unlock">Edit the list (re-draws everything)</button>
    <button class="muted-link" data-act="reset">Start over</button>
    """


def render_reveal():
    p = person(ui["who"])
    again = ui["who"] in state.get("seen", [])
    app.innerHTML = header(f"Hi {escape(p['name'])}!") + f"""
    <section class="card stage">
      <div id="dice" class="dice-box"><span class="pips">🎲</span>{avatar_html(None)}</div>
      <div id="result" class="big-name">{"Your match is hidden. 🤫" if again else "Ready?"}</div>
      <div class="row" style="justify-content:center">
        <button id="rollbtn" class="btn" data-act="roll">{"Show me again" if again else "Roll the dice 🎲"}</button>
        <button class="btn ghost" data-act="back">Hide &amp; back</button>
      </div>
    </section>"""


async def roll():
    if ui["busy"]:
        return
    ui["busy"] = True
    me = ui["who"]
    target = person(state["matches"][me])
    dice = document.getElementById("dice")
    result = document.getElementById("result")
    document.getElementById("rollbtn").disabled = True
    others = [p for p in state["people"] if p["id"] != me]
    dice.classList.add("rolling")
    result.textContent = "Rolling…"
    delay = 0.06
    for _ in range(16):
        face = random.choice(others)
        dice.innerHTML = f'<span class="pips">🎲</span>{avatar_html(face)}'
        await asyncio.sleep(delay)
        delay *= 1.12
    dice.classList.remove("rolling")
    dice.classList.add("win")
    dice.innerHTML = f'<span class="pips">🎁</span>{avatar_html(target)}'
    result.innerHTML = f'You\'re buying for<br>{escape(target["name"])}!'
    seen = state.setdefault("seen", [])
    if me not in seen:
        seen.append(me)
        save()
    ui["busy"] = False


# ---------- events ----------
def on_photo(ev):
    f = ev.target.files.item(0)
    if not f:
        return
    reader = window.FileReader.new()

    def loaded(_):
        img = window.Image.new()

        def img_ready(_):
            size = 200
            side = min(img.width, img.height)
            canvas = document.createElement("canvas")
            canvas.width = canvas.height = size
            canvas.getContext("2d").drawImage(
                img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size)
            ui["pending_photo"] = canvas.toDataURL("image/jpeg", 0.8)
            keep = document.getElementById("name").value
            render_setup()
            document.getElementById("name").value = keep

        img.onload = create_proxy(img_ready)
        img.src = reader.result

    reader.onload = create_proxy(loaded)
    reader.readAsDataURL(f)


def on_name_key(ev):
    if ev.key == "Enter":
        add_person()


def add_person():
    box = document.getElementById("name")
    name = box.value.strip()
    if not name:
        box.focus()
        return
    state["people"].append({
        "id": f"p{window.Date.now()}{random.randint(0, 999)}",
        "name": name,
        "photo": ui["pending_photo"],
    })
    ui["pending_photo"] = None
    save()
    render()
    document.getElementById("name").focus()


def on_click(ev):
    el = ev.target.closest("[data-act]")
    if not el:
        return
    act = el.dataset.act
    pid = el.dataset.id
    if act == "add":
        add_person()
    elif act == "remove":
        state["people"] = [p for p in state["people"] if p["id"] != pid]
        save()
        render()
    elif act == "lock":
        state["matches"] = draw_matches([p["id"] for p in state["people"]])
        state["locked"] = True
        state["seen"] = []
        save()
        ui["view"] = "pick"
        render()
    elif act == "unlock":
        if window.confirm("This wipes the current draw. Continue?"):
            state["locked"], state["matches"], state["seen"] = False, {}, []
            save()
            ui["view"] = "setup"
            render()
    elif act == "pick":
        ui["who"] = pid
        ui["view"] = "reveal"
        render()
    elif act == "roll":
        asyncio.ensure_future(roll())
    elif act == "back":
        ui["view"] = "pick"
        render()
    elif act == "reset":
        if window.confirm("Delete everyone and all matches?"):
            state.update({"people": [], "matches": {}, "locked": False, "seen": []})
            save()
            ui["view"] = "setup"
            render()


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


load()
ui["view"] = "pick" if state.get("locked") else "setup"
make_snow()
document.addEventListener("click", create_proxy(on_click))
render()
