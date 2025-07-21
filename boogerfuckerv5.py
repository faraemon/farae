from flask import Flask, request, jsonify, render_template, abort, redirect, url_for, make_response
from shapely.geometry import shape, Point
from datetime import datetime, timedelta
import json
import math
import time
import os
import random

print("Env PORT =", os.environ.get("PORT"))

app = Flask(__name__)

# Load simplified GeoJSON coastline map (water polygons)
with open("10m-world-map-rounded-to-3.json", "r") as f:
    geojson_data = json.load(f)
    water_shapes = [shape(geom) for geom in geojson_data["geometries"]]
    
with open("white-chocolate-gounach.laus") as f:
    WHITELISTED_IPS = {line.strip() for line in f if line.strip()}

class Forced404(Exception):
    pass
    
# Load or initialize bannage data
BANNAGE_FILE = "bannage.json"
if os.path.exists(BANNAGE_FILE):
    with open(BANNAGE_FILE, "r") as f:
        ip_strikes = json.load(f)
else:
    ip_strikes = {}

DECAY_RATE_PER_HOUR = 4
MAX_STRIKES = 10245760

APPEALS_FILE = "appeallog.json"

password_challenges = {}

def format_ban_time(minutes):
    total_seconds = int(minutes * 60)  # only if input is actually in minutes
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

    result = []
    for name, count in units:
        value = seconds // count
        if value:
            result.append(f"{value} {name}{'s' if value != 1 else ''}")
            seconds %= count
        if len(result) == 2:
            break  # only show top 2 units

    if not result:
        result.append("less than a minute")

    date_str = unban_time.strftime("%B %d, %Y — %H:%M:%S")
    return f"{' and '.join(result)}<br><small>Unban time: {date_str}</small>"

@app.template_filter("datetimeformat")
def datetimeformat(value):
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
    return "—"

@app.route("/delete_appeal", methods=["POST"])
def delete_appeal():
    ip = get_client_ip()
    if ip not in WHITELISTED_IPS:
        return render_template("403.html"), 403

    target_ip = request.form.get("ip")
    if target_ip in appeals_data:
        del appeals_data[target_ip]
        save_appeals()

    return redirect(url_for("dashboard"))

# Save strikes periodically or after each request
def save_bannage():
    with open(BANNAGE_FILE, "w") as f:
        json.dump(ip_strikes, f)

def get_client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr)

def is_whitelisted(ip):
    return ip in WHITELISTED_IPS

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
    if os.path.exists("migration_done.flag"):
        return  # migration already done

    if os.path.exists("appeals.log"):
        appeals_data = {}
        with open("appeals.log", "r") as f:
            for line in f:
                # Parse each line into your JSON format
                # Example assuming line like: "[Wed Jul 15 13:01:00 2025] IP: 1.2.3.4 — Appeal: Some text"
                try:
                    parts = line.strip().split(" — Appeal: ")
                    if len(parts) != 2:
                        continue
                    timestamp_ip, appeal_text = parts
                    timestamp = timestamp_ip[1:timestamp_ip.index("]")]
                    ip_part = timestamp_ip[timestamp_ip.index("IP: ") + 4:]
                    ip = ip_part.strip()
                    # Store the appeal with timestamp as epoch seconds
                    appeals_data[ip] = time.time()  # or parse timestamp if you want real times
                except Exception as e:
                    print("Skipping line:", line, e)

        with open("appeals.json", "w") as f:
            import json
            json.dump(appeals_data, f)

    with open("migration_done.flag", "w") as f:
        f.write("done")
    print("Appeals migration completed")
    
def add_strike(ip, points):
    if is_whitelisted(ip):
        return  # Skip strike logic
    now = time.time()
    decay_strikes(ip)  # move this up!
    user = ip_strikes[ip]
    user["strikes"] += points

    cooldown_quadrants = user["strikes"] - 40
    user["cooldown_until"] = now + cooldown_quadrants * 900
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    cooldown_until = user.get("cooldown_until", 0)
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))
    cooldown_remaining_minutes = cooldown_remaining_seconds // 60

    save_bannage()


    save_bannage()

def is_throttled(ip):
    if is_whitelisted(ip):
        return False, 0 # Skip strike logic
    user = ip_strikes.get(ip)
    now = time.time()
    if not user:
        return False, 0
    if user["strikes"] >= 256 and user.get("cooldown_until", 0) > now:
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.35) + 5, MAX_STRIKES)
        save_bannage()
        raise Forced404
    if user["strikes"] >= 41 and user.get("cooldown_until", 0) > now:
        remaining = int((user["cooldown_until"] - now) / 60) + 1
        return True, remaining
    return False, 0

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
        if b == current_bit and count < 0xFFF:
            count += 1
        else:
            result.append(f"{int(current_bit)}x{count:03X}")
            current_bit = b
            count = 1

    result.append(f"{int(current_bit)}x{count:03X}")
    return '.'.join(result)

@app.route("/check", methods=["GET"])
def check():
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

        if radius_miles < 1:
            add_strike(ip, 10)
            decay_strikes(ip)
            return jsonify({
                "error": "PXF3",
                "message": "You are asking for too little, and this counts as trying to DDoS. If you saw this in the app directly, that's a BIG error and needs to be addressed ASAP."
            }), 429
        
        
        if radius_miles > 40:
            add_strike(ip, 4)
            decay_strikes(ip)
            return jsonify({
                "error": "P907",
                "message": "You're asking for too much. Please lower the radius to 40 miles or less. Note you can always ping more times if needed."
            }), 429

        add_strike(ip, int(radius_miles) * 3)

        radius_deg = radius_miles / 69.0
        step = 0.01

        result_bits = []
        lat_range = int(radius_deg / step)
        lon_range = int(radius_deg / step)

        for dy in range(-lat_range, lat_range + 1):
            for dx in range(-lon_range, lon_range + 1):
                new_lat = lat + dy * step
                new_lon = lon + dx * step
                result_bits.append(int(is_point_in_water(new_lat, new_lon)))

        encoded = encode_runs(result_bits)

        # Calculate tokens left (max 64 - current strikes)
        user = ip_strikes.get(ip, {})
        tokens_left = max(0, 64 - user.get("strikes", 0))

        accept = request.headers.get("Accept", "").lower()
        ua = request.headers.get("User-Agent", "").lower()

        wants_html = "text/html" in accept or "mozilla" in ua
        wants_plain = "turbowarp" in ua or "scratch" in ua or "text/plain" in accept

        if wants_plain or wants_html:
            return (
                f"{encoded}\n\n"
                f"Tokens left: {tokens_left}/64\n"
                f"(1 token regenerates every ~15 minutes.)"
            ), 200, {'Content-Type': 'text/plain; charset=utf-8'}

        # Default fallback for API-style users
        return jsonify({
            "encoded": encoded,
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

@app.route("/check-my-ip")
def check_my_ip():
    ip = get_client_ip()
    decay_strikes(ip)
    add_strike(ip, 2.25)
    user = ip_strikes.get(ip, {})
    now = time.time()
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    cooldown_until = user.get("cooldown_until", 0)
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))
    cooldown_remaining_minutes = cooldown_remaining_seconds // 60
    throttled, minutes = is_throttled(ip)
    if throttled:
        decay_strikes(ip)
        user = ip_strikes[ip]
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.17) + 2, MAX_STRIKES)
        save_bannage()
        timetime = format_ban_time(cooldown_remaining)
        return redirect(url_for("banned"))
    return jsonify({
        "ip": ip,
        "strikes": user.get("strikes", 0),
        "cooldown_until": user.get("cooldown_until", 0),
        "cooldown_remaining_minutes": cooldown_remaining
    })


if os.path.exists(APPEALS_FILE):
    with open(APPEALS_FILE, "r") as f:
        appeals_data = json.load(f)
else:
    appeals_data = {}

def save_appeals():
    with open(APPEALS_FILE, "w") as f:
        json.dump(appeals_data, f)
        


from flask import render_template

@app.route("/appeal", methods=["GET", "POST"])
def appeal():
    if request.method == "GET":
        ip = get_client_ip()
        return render_template("appeal.html", ip=ip)  # serve your HTML form

    # POST logic below:
    ip = get_client_ip()
    now = time.time()
    
    last_appeal = appeals_data.get(ip, 0)
    cooldown_seconds = 7 * 24 * 3600  # 1 week
    
    if now - last_appeal < cooldown_seconds:
        remaining = int((cooldown_seconds - (now - last_appeal)) / 3600)
        return jsonify({
            "error": "P429",
            "message": f"You're allowed one appeal per week. Try again in {remaining} hours."
        }), 429

    # get appeal text from POST JSON or form
    appeal_text = request.form.get("text") or (request.json and request.json.get("text")) or ""
    if not appeal_text.strip():
        return jsonify({
            "error": "P400",
            "message": "Appeal text can't be empty."
        }), 400


    appeal_entry = {
        "ip": ip,
        "text": appeal_text,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    }
    appeals_data.append(appeal_entry)
    save_appeals()

    return jsonify({"message": "Appeal received! We'll get back to you soon. :)"}), 200

@app.route("/banned")
def banned():
    ip = get_client_ip()
    decay_strikes(ip)
    add_strike(ip, 2.25)
    user = ip_strikes.get(ip, {})
    user = ip_strikes.get(ip, {"cooldown_until": 0})
    now = time.time()
    prev = user["strikes"]
    user["strikes"] = min(int(prev * 1.17) + 2, MAX_STRIKES)
    save_bannage()
    cooldown_remaining = max(0, int((user.get("cooldown_until", 0) - now) / 60))
    cooldown_until = user.get("cooldown_until", 0)
    timetime = format_ban_time(cooldown_remaining)
    cooldown_remaining_seconds = max(0, int(user.get("cooldown_until", 0) - now))
    cooldown_remaining_minutes = cooldown_remaining_seconds // 60
    
    if user["strikes"] >= 256 and user.get("cooldown_until", 0) > now:
        prev = user["strikes"]
        user["strikes"] = min(int(prev * 1.35) + 5, MAX_STRIKES)
        save_bannage()
        raise Forced404


    if cooldown_remaining_seconds <= 0:
        # User isn't banned, redirect somewhere (maybe home or check)
        return render_template("404.html"), 404


    timetime = format_ban_time(cooldown_remaining_seconds / 60)

    return render_template("banned.html", timetime=timetime, ip=ip)

@app.route("/dashboard")
def dashboard():
    ip = get_client_ip()
    is_admin = ip in WHITELISTED_IPS

    if not is_admin:
        add_strike(ip, 1.5)
        decay_strikes(ip)
        throttled, minutes = is_throttled(ip)

        if throttled:
            user = ip_strikes[ip]
            prev = user["strikes"]
            user["strikes"] = min(int(prev * 1.2) + 2, MAX_STRIKES)
            save_bannage()
            return redirect(url_for("banned"))

    # now we do admin logic safely
    user = ip_strikes.get(ip, {})
    strikes = user.get("strikes", 0)
    tokens_left = max(0, 64 - strikes)
    cooldown_time = datetime.fromtimestamp(user.get("cooldown_until", 0)).strftime("%Y-%m-%d %H:%M:%S")
    now = time.time()
    cooldown_until = user.get("cooldown_until", 0)
    cooldown_remaining_seconds = max(0, int(cooldown_until - now))
    cooldown_remaining_minutes = cooldown_remaining_seconds // 60

    password_index = None
    if is_admin:
        password_index = random.randint(0, 3)
        password_challenges[ip] = password_index

    banlist = []
    if is_admin:
        for banned_ip, data in ip_strikes.items():
            if data.get("cooldown_until", 0) > time.time():
                banlist.append({
                    "ip": banned_ip,
                    "strikes": data.get("strikes", 0),
                    "cooldown": datetime.fromtimestamp(data["cooldown_until"]).strftime("%Y-%m-%d %H:%M:%S")
                })

    appeals_log = []
    if is_admin:
        appeals_log_raw = list(appeals_data.items())[-15:]
        appeals_log = [{
            "ip": ip,
            "time": data.get("time", "???"),
            "text": data.get("text", "")
        } for ip, data in appeals_log_raw]

    return render_template("dashboard.html",
        ip=ip,
        tokens_left=tokens_left,
        cooldown_time=cooldown_time,
        is_admin=is_admin,
        banlist=banlist,
        total_users=len(ip_strikes),
        banned_count=len(banlist),
        total_appeals=len(appeals_data),
        appeals_log=appeals_log,
        password_index=password_index,
        ip_strikes=ip_strikes
    )

PASSWORDS_FILE = "pass.words"
with open(PASSWORDS_FILE) as f:
    ADMIN_PASSWORDS = [line.strip() for line in f if line.strip()]

@app.route("/unban", methods=["GET", "POST"])
def unban():
    ip = get_client_ip()
    is_admin = ip in WHITELISTED_IPS
    if not is_admin:
        return render_template("403.html"), 403

    index = password_challenges.get(ip, 0)

    if request.method == "POST":
        target_ip = request.form.get("ip")
        password = request.form.get("password")

        if index is None:
            return render_template("403.html"), 403

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
    suffix = ["st", "nd", "rd", "th"][index] if index < 3 else "th"
    banlist = []
    for banned_ip, data in ip_strikes.items():
        if data.get("cooldown_until", 0) > time.time():
            banlist.append({
                "ip": banned_ip,
                "strikes": data.get("strikes", 0),
                "cooldown": datetime.fromtimestamp(data["cooldown_until"]).strftime("%Y-%m-%d %H:%M:%S")
            })
    
    return render_template("unban_form.html", banlist=banlist, password_index=index, suffix=suffix)


@app.route("/delete_appeal_by_index", methods=["POST"])
def delete_appeal_by_index():
    ip = get_client_ip()
    is_admin = ip in WHITELISTED_IPS
    if not is_admin:
        return render_template("403.html"), 403

    password = request.form.get("password", "")
    index = request.form.get("index", None)

    # Validate password challenge index for admin
    challenge_index = password_challenges.get(ip, None)
    if challenge_index is None or challenge_index >= len(ADMIN_PASSWORDS):
        return render_template("403.html"), 403

    expected_password = ADMIN_PASSWORDS[challenge_index]
    if password != expected_password:
        return render_template("403.html"), 403

    try:
        idx = int(index)
        # Defensive check
        if idx < 0 or idx >= len(appeals_data):
            return render_template("404.html"), 404
    except:
        return render_template("404.html"), 404

    # Delete the appeal
    appeals_data.pop(idx)
    save_appeals()
    return redirect(url_for("dashboard"))


# 404 Not Found
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

# 403 Forbidden
@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

# 429 Too Many Requests
@app.errorhandler(429)
def too_many_requests(e):
    return render_template("429.html"), 429

# 500 Internal Server Error
@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500

@app.errorhandler(Forced404)
def handle_forced_404(e):
    return "", 403

migrate_appeals()

if __name__ == "__main__":
    print("Starting Flask on 0.0.0.0:21095")  # for debugging
    app.run(host="0.0.0.0", port=21095, debug=False, use_reloader=False)