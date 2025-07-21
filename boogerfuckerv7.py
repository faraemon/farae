import random
from flask import Flask, request, jsonify, render_template, abort, redirect, url_for, make_response, flash
from shapely.geometry import shape, Point
from datetime import datetime, timedelta
import json
import math
import time
import os

print("Env PORT =", os.environ.get("PORT"))

app = Flask(__name__)
app.secret_key = "some-super-secret-key-that-no-one-else-knows"

# Load simplified GeoJSON coastline map (water polygons)
with open("10m-world-map-rounded-to-3.json", "r") as f:
    geojson_data = json.load(f)
    water_shapes = [shape(geom) for geom in geojson_data["geometries"]]

# Load whitelist IPs
with open("whitelist.json") as f:
    WHITELISTED_IPS = set(json.load(f))

# Load admin passwords
with open("passwords.json") as f:
    ADMIN_PASSWORDS = json.load(f)

# Load or initialize bannage data
BANNAGE_FILE = "bannage.json"
if os.path.exists(BANNAGE_FILE):
    with open(BANNAGE_FILE, "r") as f:
        ip_strikes = json.load(f)
else:
    ip_strikes = {}

DECAY_RATE_PER_HOUR = 4
MAX_STRIKES = 10245760

APPEALS_FILE = "appeals.json"
if os.path.exists(APPEALS_FILE):
    with open(APPEALS_FILE, "r") as f:
        appeals_data = json.load(f)
else:
    appeals_data = {}

password_challenges = {}

class Forced404(Exception):
    pass

#######################################################################################################################################################
#######################################################################################################################################################

def get_client_ip():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    return ip

def is_whitelisted(ip):
    return ip in WHITELISTED_IPS

def save_bannage():
    with open(BANNAGE_FILE, "w") as f:
        json.dump(ip_strikes, f)

def save_appeals():
    with open(APPEALS_FILE, "w") as f:
        json.dump(appeals_data, f)
        
def format_timestamp(timestamp):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(timestamp)))
    except Exception:
        return "???"

def decay_strikes(ip):
    now = time.time()
    user = ip_strikes.get(ip)
    if not user:
        ip_strikes[ip] = {"strikes": 0, "last_update": now, "cooldown_until": 0}
        return

    time_passed = (now - user.get("last_update", now)) / 3600
    decay = int(time_passed * DECAY_RATE_PER_HOUR)
    user["strikes"] = max(0, user.get("strikes", 0) - decay)
    user["last_update"] = now
    
def migrate_appeals():
    pass  # Implement migration logic if needed

def add_strike(ip, points):
    if is_whitelisted(ip):
        return  # Skip strike logic
    now = time.time()
    decay_strikes(ip)
    user = ip_strikes[ip]
    user["strikes"] += points
    cooldown_quadrants = user["strikes"] - 64
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    cooldown_until = user.get("cooldown_until", 0)
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))
    if cooldown_quadrants > 0.01:
        user["cooldown_until"] = now + cooldown_quadrants * 900  # 15 mins per quadrant
    else:
        user["cooldown_until"] = now + cooldown_quadrants * 900  # 15 mins per quadrant

    save_bannage()

def format_ban_time(minutes):
    result = []
    total_seconds = int(minutes * 60)
    now = datetime.now()
    unban_time = now + timedelta(seconds=total_seconds)

    seconds = total_seconds
    units = [
        ("year", 60 * 60 * 24 * 365),
        ("month", 60 * 60 * 24 * 30),
        ("week", 60 * 60 * 24 * 7),
        ("day", 60 * 60 * 24),
        ("hour", 60 * 60),
        ("minute", 60),
    ]

    tt = []
    for name, count in units:
        value = seconds // count
        if value:
            result.append(f"{value} {name}{'s' if value != 1 else ''}")
            seconds %= count
        if len(result) == 2:
            break

    if not result:
        result.append("less than a minute")

    date_str = unban_time.strftime("%B %d, %Y — %H:%M:%S")
    return f"{' and '.join(result)}<br><small>Unban time: {date_str}</small>"

def is_throttled(ip):
    if is_whitelisted(ip):
        return False, 0
    user = ip_strikes.get(ip)
    now = time.time()
    if not user:
        return False, 0
    if user["strikes"] >= 768 and user.get("cooldown_until", 0) > now:
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.15) + 5, MAX_STRIKES)
        save_bannage()
        raise Forced404
    if user["strikes"] >= 64 and user.get("cooldown_until", 0) > now:
        remaining = int((user["cooldown_until"] - now) / 60) + 1
        return True, remaining
    return False, 0

def check_password(ip, password):
    challenge_index = password_challenges.get(ip, None)
    if challenge_index is None or challenge_index >= len(ADMIN_PASSWORDS):
        return False
    expected_password = ADMIN_PASSWORDS[challenge_index]
    return password == expected_password

def is_point_in_water(lat, lon):
    point = Point(lon, lat)
    return any(water_shape.intersects(point) for water_shape in water_shapes)

def encode_runs(bits):
    if not bits:
        return ""

    result = []
    current_bit = bits[0]
    count = 1

    for b in bits[1:]:
        if b == current_bit and count < 0xFFFF:
            count += 1
        else:
            result.append(f"{int(current_bit)}x{count:04X}")
            current_bit = b
            count = 1

    result.append(f"{int(current_bit)}x{count:04X}")
    return '.'.join(result)

def is_admin():
    ip = get_client_ip()
    return ip in WHITELISTED_IPS

def validate_radius(radius_miles, ip):
    if radius_miles < 1:
        add_strike(ip, 10)
        return {
            "error": "PXF3",
            "message": "You are asking for too little, and this counts as trying to DDoS. If you saw this in the app directly, that's a BIG error and needs to be addressed ASAP."
        }, 429
    if radius_miles > 40:
        add_strike(ip, 4)
        return {
            "error": "P907",
            "message": "You're asking for too much. Please lower the radius to 40 miles or less. Note you can always ping more times if needed."
        }, 429
    return None, None

#######################################################################################################################################################
#######################################################################################################################################################

@app.route("/appeal", methods=["GET", "POST"])
def appeal():
    ip = get_client_ip()
    decay_strikes(ip)
    add_strike(ip, 2.25)
    user = ip_strikes.get(ip, {})
    now = time.time()
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    throttled, minutes = is_throttled(ip)

    if user.get("strikes", 0) >= 76800 and user.get("cooldown_until", 0) > now:
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.15) + 5, MAX_STRIKES)
        save_bannage()
        raise Forced404    
    if throttled:
        decay_strikes(ip)
        return redirect(url_for("banned"))

    if request.method == "GET":
        return render_template("appeal.html", ip=ip)

    # POST logic
    last_appeal = appeals_data.get(ip, {}).get("time", 0) if isinstance(appeals_data.get(ip), dict) else 0
    if isinstance(last_appeal, str):
        try:
            last_appeal = time.mktime(time.strptime(last_appeal, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            last_appeal = 0

    cooldown_seconds = 7 * 24 * 3600  # 1 week
    if now - last_appeal < cooldown_seconds:
        remaining = int((cooldown_seconds - (now - last_appeal)) / 3600)
        return jsonify({
            "error": "P429",
            "message": f"You're allowed one appeal per week. Try again in {remaining} hours."
        }), 429

    appeal_text = request.form.get("text") or (request.json and request.json.get("text")) or ""
    if not appeal_text.strip():
        return jsonify({
            "error": "P400",
            "message": "Appeal text can't be empty."
        }), 400

    appeals_data[ip] = {
        "ip": ip,
        "text": appeal_text.strip(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    }
    save_appeals()

    return jsonify({"message": "Appeal received! We'll get back to you soon. :)"}), 200

@app.route("/banned")
def banned():
    ip = get_client_ip()
    decay_strikes(ip)
    add_strike(ip, 2)
    user = ip_strikes.get(ip, {"cooldown_until": 0, "strikes": 0})
    now = time.time()
    prev = user.get("strikes", 0)
    user["strikes"] = min(int(prev * 1.15) + 2, MAX_STRIKES)
    save_bannage()
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    cooldown_until = user.get("cooldown_until", 0)
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))

    if user["strikes"] >= 2048 and cooldown_until > now: # because sometimes you're hitting 256 with only one command.
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.4) + 5, MAX_STRIKES)
        save_bannage()
        raise Forced404

    if cooldown_remaining_seconds <= 0:
        return render_template("404.html"), 404

    cooldown_until = user.get("cooldown_until", 0)
    now = time.time()
    bantime_remaining = max(0, int(cooldown_until - now))  # in seconds

    return render_template("banned.html", bantime_remaining=bantime_remaining)


@app.route("/dashboard")
def dashboard():
    ip = get_client_ip()
    is_admin_user = ip in WHITELISTED_IPS

    if not is_admin_user:
        add_strike(ip, 0.7)
        decay_strikes(ip)
        throttled, minutes = is_throttled(ip)

        if throttled:
            user = ip_strikes[ip]
            prev = user["strikes"]
            user["strikes"] = min(int(prev * 1.15) + 2, MAX_STRIKES)
            save_bannage()
            return redirect(url_for("banned"))

    user = ip_strikes.get(ip, {})
    strikes = user.get("strikes", 0)
    tokens_left = max(0, 64 - strikes)
    cooldown_time = datetime.fromtimestamp(user.get("cooldown_until", 0)).strftime("%Y-%m-%d %H:%M:%S")
    cooldown_until = user.get("cooldown_until", 0)
    now = time.time()
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))
    cooldown_remaining_minutes = cooldown_remaining_seconds // 60

    password_index = None
    if is_admin_user:
        password_index = random.randint(0, len(ADMIN_PASSWORDS)-1)
        password_challenges[ip] = password_index

    banlist = []
    if is_admin_user:
        for banned_ip, data in ip_strikes.items():
            if data.get("cooldown_until", 0) > time.time():
                banlist.append({
                    "ip": banned_ip,
                    "strikes": data.get("strikes", 0),
                    "cooldown": datetime.fromtimestamp(data["cooldown_until"]).strftime("%Y-%m-%d %H:%M:%S")
                })

    appeals_log = []
    if is_admin_user:
        appeals_log_raw = list(appeals_data.items())[-15:]
        for appeal_ip, data in appeals_log_raw:
            if isinstance(data, dict):
                time_str = data.get("time", "???")
                text = data.get("text", "")
            else:
                time_str = format_timestamp(data)
                text = ""
            appeals_log.append({
                "ip": appeal_ip,
                "time": time_str,
                "text": text
            })
    return render_template("dashboard.html",
        ip=ip,
        tokens_left=tokens_left,
        cooldown_time=cooldown_time,
        is_admin=is_admin_user,
        banlist=banlist,
        total_users=len(ip_strikes),
        banned_count=len(banlist),
        total_appeals=len(appeals_data),
        appeals_log=appeals_log,
        password_index=password_index,
        ip_strikes=ip_strikes
        )
@app.route("/unban", methods=["GET", "POST"])
def unban():
    ip = get_client_ip()
    is_admin = ip in WHITELISTED_IPS
    if not is_admin:
        user = ip_strikes.get(ip, {"strikes": 0})
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 4) + 10, MAX_STRIKES)
        save_bannage()
        raise Forced404

    index = password_challenges.get(ip, None)
    if index is None:
        return render_template("403.html"), 403

    if request.method == "POST":
        target_ip = request.form.get("ip")
        password = request.form.get("password")

        expected_password = ADMIN_PASSWORDS[index]
        if password != expected_password:
            return render_template("403.html"), 403

        if target_ip in ip_strikes:
            del ip_strikes[target_ip]
            if target_ip in appeals_data:
                del appeals_data[target_ip]
                save_appeals()
            save_bannage()
            return redirect(url_for("dashboard"))
        else:
            return render_template("404.html"), 404

    # GET request: show the unban form
    suffix = ["st", "nd", "rd"] + ["th"] * 10
    suffix_str = suffix[index] if index < len(suffix) else "th"

    banlist = []
    for banned_ip, data in ip_strikes.items():
        if data.get("cooldown_until", 0) > time.time():
            banlist.append({
                "ip": banned_ip,
                "strikes": data.get("strikes", 0),
                "cooldown": datetime.fromtimestamp(data["cooldown_until"]).strftime("%Y-%m-%d %H:%M:%S")
            })

    return render_template("unban_form.html", banlist=banlist, password_index=index, suffix=suffix_str)


@app.route("/delete_appeal_by_index", methods=["GET", "POST"])
def delete_appeal_by_index():
    ip = get_client_ip()
    is_admin = ip in WHITELISTED_IPS
    if not is_admin:
        raise Forced404

    password = request.form.get("password", "")
    index = request.form.get("index", None)

    challenge_index = password_challenges.get(ip, None)
    if challenge_index is None or challenge_index >= len(ADMIN_PASSWORDS):
        return render_template("403.html"), 403

    expected_password = ADMIN_PASSWORDS[challenge_index]
    if password != expected_password:
        return render_template("403.html"), 403

    try:
        idx = int(index)
        if idx < 0:
            return render_template("404.html"), 404
    except:
        return render_template("404.html"), 404

    # appeals_data is dict keyed by IP, so we cannot pop by index directly.
    # We convert keys to list and pop by index accordingly:
    appeal_keys = list(appeals_data.keys())
    if idx >= len(appeal_keys):
        return render_template("404.html"), 404

    key_to_remove = appeal_keys[idx]
    del appeals_data[key_to_remove]
    save_appeals()
    return redirect(url_for("dashboard"))


@app.route("/ban", methods=["POST"])
def ban_ip():
    ip = get_client_ip()
    is_admin = ip in WHITELISTED_IPS
    if not is_admin:
        user = ip_strikes.get(ip, {"strikes": 0})
        prev = user.get("strikes", 0)
        user["strikes"] = min(int(prev * 4) + 10, MAX_STRIKES)
        save_bannage()
        raise Forced404

    target_ip = request.form.get("ip")
    raw_password = request.form.get("password", "").strip()

    if " " in raw_password:
        password, extra_strikes_part = raw_password.split(" ", 1)
    else:
        password = raw_password
        extra_strikes_part = ""

    extra_strikes = 250 * extra_strikes_part.count("!")
    base_strikes = 500
    total_strikes = base_strikes + extra_strikes

    if not check_password(ip, password):
        raise Forced404

    now = time.time()
    cooldown_hours = 24
    cooldown_until = now + cooldown_hours * 3600

    ip_strikes[target_ip] = {
        "strikes": total_strikes,
        "cooldown_until": cooldown_until
    }
    save_bannage()

    flash(f"Banned {target_ip} with {total_strikes} strikes for {cooldown_hours} hours!")
    return redirect(url_for("dashboard"))

@app.route("/check", methods=["GET"])
def check():
    admin_bonus = 0
    ip = get_client_ip()
    decay_strikes(ip)
    add_strike(ip, 0.01)
    user = ip_strikes.get(ip, {})
    now = time.time()
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    cooldown_until = user.get("cooldown_until", 0)
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))
    cooldown_remaining_minutes = cooldown_remaining_seconds // 60
    throttled, minutes = is_throttled(ip)
    is_admin = ip in WHITELISTED_IPS
    if is_admin:
        admin_bonus = 2000
    if throttled:
        decay_strikes(ip)
        user = ip_strikes[ip]
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.25) + 2, MAX_STRIKES)
        save_bannage()
        timetime = format_ban_time(cooldown_remaining)
        return redirect(url_for("banned"))

    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        radius_miles = float(request.args.get("radius_miles", 10))
        focus_mode_raw = request.args.get("focusmode", "0")

        # Try to interpret the value safely
        try:
            focus_level = int(focus_mode_raw)
        except (ValueError, TypeError):
            focus_level = 0  # fallback to default

        # Clamp between 0 and 4
        focus_level = max(0, min(4, focus_level))

        # Map focus level to step size
        focus_step_map = {
            0: 0.025,
            1: 0.016,
            2: 0.010,
            3: 0.007,
            4: 0.0055
        }

        step = focus_step_map[focus_level]

        # Optional: increase token cost for higher focus levels
        token_multiplier = 1.0 + focus_level * 0.15  # e.g. 1.0, 1.15, 1.3, etc.

        tiles_per_token = 425
        max_tokens = 64 + 16 + admin_bonus
        tokens_available = max_tokens - user.get("strikes", 0)

        def estimate_tile_count(radius_miles, step):
            radius_deg = radius_miles / 69.0
            lat_range = int(radius_deg / step)
            lon_range = int(radius_deg / step)
            return (2 * lat_range + 1) * (2 * lon_range + 1)

        while radius_miles > 0.1:
            tile_est = estimate_tile_count(radius_miles, step)
            token_est = round((tile_est / tiles_per_token) * token_multiplier, 2)
            if token_est <= tokens_available:
                break
            radius_miles = round(radius_miles - 0.1, 1)
        else:
            return jsonify({
                "error": "NOT_ENOUGH_TOKENS",
                "message": "You don't have enough tokens."
            }), 403

        tile_count = tile_est
        token_cost = token_est

        # Deduct tokens after adjusting radius
        add_strike(ip, token_cost)

        radius_deg = radius_miles / 69.0
        lat_range = int(radius_deg / step)
        lon_range = int(radius_deg / step)

        result_bits = []
        checked_tiles = 0

        for dy in range(-lat_range, lat_range + 1):
            for dx in range(-lon_range, lon_range + 1):
                new_lat = lat + dy * step
                new_lon = lon + dx * step
                result_bits.append(int(is_point_in_water(new_lat, new_lon)))
                checked_tiles += 1

        encoded = encode_runs(result_bits)
        user = ip_strikes.get(ip, {})
        tokens_left = round(max(0, 64 - user.get("strikes", 0)), 2)

        accept = request.headers.get("Accept", "").lower()
        ua = request.headers.get("User-Agent", "").lower()
        wants_html = "text/html" in accept or "mozilla" in ua
        wants_plain = "turbowarp" in ua or "scratch" in ua or "text/plain" in accept

        if wants_plain or wants_html:
            return (
                f"{encoded}\n\n"
                f"Tiles checked: {checked_tiles}\n"
                f"Radius used: {radius_miles} miles\n"
                f"Tokens used: {token_cost}\n"
                f"Tokens left: {tokens_left}/64\n"
                f"(1 token regenerates every ~15 minutes.)"
            ), 200, {'Content-Type': 'text/plain; charset=utf-8'}

        return jsonify({
            "encoded": encoded,
            "tiles_checked": checked_tiles,
            "radius_used": radius_miles,
            "tokens_used": token_cost,
            "tokens_left": tokens_left,
            "note": f"You have {tokens_left} tokens left."
        })

    except Exception as e:
        add_strike(ip, 24)
        decay_strikes(ip)
        return jsonify({
            "error": "P500",
            "message": f"Something went wrong: {str(e)}"
        }), 500

    
#######################################################################################################################################################
#######################################################################################################################################################
    

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@app.errorhandler(429)
def too_many_requests(e):
    return render_template("429.html"), 429


@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500


@app.errorhandler(Forced404)
def handle_forced_404(e):
    return "", 403


migrate_appeals()

if __name__ == "__main__":
    print("Starting Flask on 0.0.0.0:21095")
    app.run(host="0.0.0.0", port=21095, debug=False, use_reloader=False)