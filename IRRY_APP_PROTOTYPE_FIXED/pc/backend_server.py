
import time
import threading
import sqlite3
import queue
import json
import os
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

try:
    from hardware_bridge import HardwareDriver
    hardware = HardwareDriver()
except Exception:
    class HardwareDriver:
        def set_pump(self, state): print(f"[SIM] Pump {'ON' if state else 'OFF'}")
        def set_valve(self, idx, state): print(f"[SIM] Valve {idx+1} {'ON' if state else 'OFF'}")
        def cleanup(self): print("[SIM] Cleanup")
    hardware = HardwareDriver()

NUM_BEDS = 3  # matches the 3 GPIO valve pins wired in config.py
WATER_ADVANCE_TIME_SECONDS = 8.0
BED_TIMEOUT_SECONDS = 120.0
MAX_BED_DURATION_SECONDS = 10800.0  # 180 minutes safety cap per bed
CHANGEOVER_DELAY_SECONDS = 0.5
TICK_SECONDS = 0.1

MOISTURE_THRESHOLD = 60.0
MOISTURE_DRY_RATE = 0.5
MOISTURE_WET_RATE = 2.0
INITIAL_MOISTURE = 6.1

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(APP_DIR, "irrigation_history.db")

def db_conn():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def db_init():
    conn = db_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT, ended_at TEXT,
        beds_watered TEXT, beds_skipped TEXT, bed_durations TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, bed INTEGER, message TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bed_number INTEGER, start_time TEXT, duration INTEGER,
        status TEXT DEFAULT 'PENDING'
    )""")
    conn.commit()
    conn.close()

def db_start_cycle(started_at):
    conn = db_conn()
    cur = conn.execute(
        "INSERT INTO cycles (started_at, ended_at, beds_watered, beds_skipped, bed_durations) VALUES (?,?,?,?,?)",
        (started_at, "", "", "", "")
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid

def db_finish_cycle(cycle_id, ended_at, watered, skipped, durations):
    if not cycle_id:
        return
    conn = db_conn()
    conn.execute(
        "UPDATE cycles SET ended_at=?, beds_watered=?, beds_skipped=?, bed_durations=? WHERE id=?",
        (ended_at, ",".join(map(str, watered)), ",".join(map(str, skipped)),
         json.dumps(durations), cycle_id)
    )
    conn.commit()
    conn.close()

def db_save_alert(bed, message):
    conn = db_conn()
    conn.execute(
        "INSERT INTO alerts(created_at,bed,message) VALUES(?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), bed, message)
    )
    conn.commit()
    conn.close()

def db_fetch_history(limit=30):
    conn = db_conn()
    rows = conn.execute(
        "SELECT id,started_at,ended_at,beds_watered,beds_skipped,bed_durations "
        "FROM cycles ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [{
        "id": r[0], "started_at": r[1], "ended_at": r[2],
        "beds_watered": r[3], "beds_skipped": r[4], "bed_durations": r[5]
    } for r in rows]

def db_fetch_alerts(limit=20):
    conn = db_conn()
    rows = conn.execute(
        "SELECT id,created_at,bed,message FROM alerts ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [{"id":r[0],"created_at":r[1],"bed":r[2],"message":r[3]} for r in rows]

def db_clear_history():
    conn = db_conn()
    conn.execute("DELETE FROM cycles")
    conn.commit()
    conn.close()

def db_create_schedule(bed_number, start_time, duration):
    conn = db_conn()
    cur = conn.execute(
        "INSERT INTO schedules(bed_number,start_time,duration) VALUES(?,?,?)",
        (bed_number,start_time,duration)
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid

def db_get_pending_schedules():
    conn = db_conn()
    rows = conn.execute(
        "SELECT id,bed_number,start_time,duration FROM schedules "
        "WHERE status='PENDING' ORDER BY start_time"
    ).fetchall()
    conn.close()
    return [{"id":r[0],"bed_number":r[1],"start_time":r[2],"duration":r[3]} for r in rows]

def db_cancel_all_schedules():
    conn = db_conn()
    conn.execute("DELETE FROM schedules WHERE status='PENDING'")
    conn.commit()
    conn.close()

def db_cancel_schedules(ids):
    if not ids: return
    conn = db_conn()
    q = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM schedules WHERE id IN ({q})", ids)
    conn.commit()
    conn.close()

class IrrigationController:
    def __init__(self, num_beds=NUM_BEDS):
        self.num_beds = num_beds
        self.lock = threading.RLock()
        self.durations = {i: BED_TIMEOUT_SECONDS for i in range(num_beds)}
        self.selected_beds = []
        self.active_durations = {}
        self._reset_state()
        self.current_cycle_id = None
        for i in range(num_beds):
            hardware.set_valve(i, False)
        hardware.set_pump(False)

    def _reset_state(self):
        self.valve_state = [False] * self.num_beds
        self.sensor_state = [False] * self.num_beds
        self.water_progress = [0.0] * self.num_beds
        self.moisture = [INITIAL_MOISTURE] * self.num_beds
        self.skipped = [False] * self.num_beds
        self.current_bed = -1
        self.running = False
        self.bed_elapsed_time = 0.0
        self.cycle_status = "Idle"
        self.started_at = None

    def start(self, bed_list=None, durations=None):
        with self.lock:
            if self.running:
                return False, "already running"
            beds = list(range(1, self.num_beds + 1)) if not bed_list else [
                int(b) for b in bed_list if 1 <= int(b) <= self.num_beds
            ]
            if not beds:
                return False, "no valid beds selected"

            self.selected_beds = beds
            self.active_durations = {}
            if durations:
                for k, v in durations.items():
                    try:
                        bed = int(k); sec = float(v)
                    except (TypeError, ValueError):
                        return False, f"Invalid duration value for bed {k}"
                    if not (1 <= bed <= self.num_beds):
                        continue  # duration for a bed that isn't part of this cycle; ignore safely
                    if not (0 < sec <= MAX_BED_DURATION_SECONDS):
                        return False, (
                            f"Duration for bed {bed} must be between 0 and "
                            f"{int(MAX_BED_DURATION_SECONDS // 60)} minutes"
                        )
                    self.durations[bed-1] = sec
                    self.active_durations[str(bed)] = sec

            self._reset_state()
            self.selected_beds = beds
            self.active_durations = self.active_durations
            self.running = True
            self.cycle_status = "Running"
            self.started_at = datetime.now().isoformat(timespec="seconds")
            self.current_cycle_id = db_start_cycle(self.started_at)

        threading.Thread(target=self._run_cycle, daemon=True).start()
        return True, f"started watering beds {', '.join(map(str,beds))}"

    def stop(self, reason="Stopped"):
        with self.lock:
            was_running = self.running
            self.running = False
            self.cycle_status = reason
            self.valve_state = [False] * self.num_beds
            self.current_bed = -1
            for i in range(self.num_beds):
                hardware.set_valve(i, False)
            hardware.set_pump(False)
        return was_running

    def manual_open(self, idx):
        with self.lock:
            if self.running:
                return False, "cannot manually open while a cycle is running"
            if not 0 <= idx < self.num_beds:
                return False, "invalid valve"
            for i in range(self.num_beds):
                self.valve_state[i] = i == idx
                hardware.set_valve(i, i == idx)
            hardware.set_pump(True)
        return True, f"valve {idx+1} open"

    def manual_close(self, idx):
        with self.lock:
            if not 0 <= idx < self.num_beds:
                return False, "invalid valve"
            self.valve_state[idx] = False
            hardware.set_valve(idx, False)
            if not any(self.valve_state):
                hardware.set_pump(False)
        return True, f"valve {idx+1} closed"

    def set_moisture_threshold(self, value):
        global MOISTURE_THRESHOLD
        value = float(value)
        if not 0 <= value <= 100:
            raise ValueError("threshold must be 0..100")
        MOISTURE_THRESHOLD = value

    def status(self):
        with self.lock:
            bed = self.current_bed + 1 if self.current_bed >= 0 else 0
            mins, secs = divmod(int(self.bed_elapsed_time), 60)
            valves = {f"valve{i+1}": ("Open" if self.valve_state[i] else "Closed")
                      for i in range(self.num_beds)}
            sensors = {f"sensor{i+1}": ("Detected" if self.sensor_state[i] else "Not Detected")
                       for i in range(self.num_beds)}
            return {
                "current_bed": bed, "bed": bed,
                "time": f"{mins}:{secs:02d}",
                "elapsed_seconds": round(self.bed_elapsed_time, 1),
                "status": self.cycle_status, "message": self.cycle_status,
                "running": self.running,
                "pump": "ON" if any(self.valve_state) else "OFF",
                "valves": valves, "sensors": sensors,
                "moisture": {f"bed{i+1}": round(self.moisture[i],1) for i in range(self.num_beds)},
                "skipped": {f"bed{i+1}": bool(self.skipped[i]) for i in range(self.num_beds)},
                "water_progress": {f"bed{i+1}": round(self.water_progress[i],2) for i in range(self.num_beds)},
                "moisture_threshold": MOISTURE_THRESHOLD,
                "selected_beds": self.selected_beds,
                "bed_durations": self.active_durations,
                "started_at": self.started_at
            }

    def sensor_data(self):
        with self.lock:
            values = list(self.moisture)
            avg = sum(values) / len(values)
            active = self.current_bed >= 0
            return {
                "soil_moisture": round(avg, 1),
                "temperature": None,
                "humidity": None,
                "weather": "Local sensor data only",
                "rain_expected": None,
                "source": "backend telemetry (simulation unless hardware is enabled)",
                "active_bed": self.current_bed + 1 if active else 0,
                "moisture_threshold": MOISTURE_THRESHOLD,
                "bed_moisture": {f"bed{i+1}": round(self.moisture[i],1) for i in range(self.num_beds)}
            }

    def _run_cycle(self):
        time.sleep(CHANGEOVER_DELAY_SECONDS)
        watered, skipped = [], []
        for bed_number in list(self.selected_beds):
            bed = bed_number - 1
            with self.lock:
                if not self.running: break
                if self.moisture[bed] >= MOISTURE_THRESHOLD:
                    self.skipped[bed] = True
                    skipped.append(bed_number)
                    continue
                self.skipped[bed] = False
                self.current_bed = bed
                self.valve_state[bed] = True
                self.water_progress[bed] = 0.0
                self.sensor_state[bed] = False
                self.bed_elapsed_time = 0.0
                hardware.set_valve(bed, True)
                hardware.set_pump(True)
                duration = self.durations.get(bed, BED_TIMEOUT_SECONDS)

            sensor_triggered = False
            while True:
                with self.lock:
                    if not self.running or self.bed_elapsed_time >= duration:
                        break
                    self.bed_elapsed_time += TICK_SECONDS
                    self.water_progress[bed] = min(1.0, self.bed_elapsed_time / WATER_ADVANCE_TIME_SECONDS)
                    self.moisture[bed] = min(100, self.moisture[bed] + MOISTURE_WET_RATE*TICK_SECONDS)
                    if self.water_progress[bed] >= 0.95 and not sensor_triggered:
                        sensor_triggered = True
                        self.sensor_state[bed] = True
                time.sleep(TICK_SECONDS)

            with self.lock:
                if self.running and not sensor_triggered:
                    db_save_alert(bed_number, f"Bed {bed_number}: end sensor not reached before timeout")
                if self.running:
                    watered.append(bed_number)
                self.valve_state[bed] = False
                self.sensor_state[bed] = sensor_triggered
                hardware.set_valve(bed, False)
                if not any(self.valve_state):
                    hardware.set_pump(False)
                self.current_bed = -1
            time.sleep(CHANGEOVER_DELAY_SECONDS)

        with self.lock:
            finished = self.running
            self.running = False
            self.cycle_status = "Complete" if finished else self.cycle_status
            ended = datetime.now().isoformat(timespec="seconds")
            cid = self.current_cycle_id
            self.current_bed = -1
            self.valve_state = [False] * self.num_beds
            hardware.set_pump(False)
        db_finish_cycle(cid, ended, watered, skipped, self.active_durations)
        if finished:
            db_save_alert(0, "Irrigation Complete")

    def dry_soil_tick(self):
        with self.lock:
            for i in range(self.num_beds):
                if not self.valve_state[i] and self.moisture[i] > 0:
                    self.moisture[i] = max(0, self.moisture[i] - MOISTURE_DRY_RATE*TICK_SECONDS)

controller = IrrigationController()

def drying_loop():
    while True:
        controller.dry_soil_tick()
        time.sleep(TICK_SECONDS)

def scheduler_loop():
    while True:
        now = datetime.now().isoformat(timespec="seconds")
        due = db_get_pending_schedules()
        due_now = [s for s in due if s["start_time"] <= now]
        if due_now and not controller.running:
            beds = [s["bed_number"] for s in due_now]
            durations = {str(s["bed_number"]): s["duration"] for s in due_now}
            controller.start(bed_list=beds, durations=durations)
            db_cancel_schedules([s["id"] for s in due_now])
        time.sleep(2)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    static_folder=APP_DIR,
    static_url_path=""
)

@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/")
def index():
    index_path = os.path.join(APP_DIR, "index.html")
    if not os.path.isfile(index_path):
        return jsonify({
            "ok": False,
            "error": "index.html not found",
            "expected": index_path
        }), 500
    return send_from_directory(APP_DIR, "index.html")

@app.route("/api/diagnostics")
def api_diagnostics():
    return jsonify({
        "ok": True,
        "app_dir": APP_DIR,
        "index_exists": os.path.isfile(os.path.join(APP_DIR, "index.html")),
        "frontend_exists": os.path.isfile(os.path.join(APP_DIR, "frontend.js")),
        "db_file": DB_FILE,
        "db_exists": os.path.isfile(DB_FILE),
    })

@app.route("/mobile/")
def mobile_index():
    return send_from_directory(os.path.join(APP_DIR, "mobile"), "index.html")

@app.route("/mobile/<path:filename>")
def mobile_asset(filename):
    return send_from_directory(os.path.join(APP_DIR, "mobile"), filename)

@app.route("/prototype/")
def prototype_index():
    return send_from_directory(os.path.join(APP_DIR, "prototype"), "index.html")

@app.route("/prototype/<path:filename>")
def prototype_asset(filename):
    return send_from_directory(os.path.join(APP_DIR, "prototype"), filename)

@app.route("/api/status")
def api_status():
    return jsonify(controller.status())

@app.route("/api/sensor-data")
def api_sensor_data():
    return jsonify(controller.sensor_data())

@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "service":"IRRY backend", "beds":NUM_BEDS,
                    "time":datetime.now().isoformat(timespec="seconds")})

@app.route("/api/start", methods=["POST"])
@app.route("/api/irrigation/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    beds = data.get("beds", [])
    raw = data.get("durations") or {}
    durations = {}
    for k,v in raw.items():
        try: durations[str(int(k))] = float(v)
        except: pass
    ok,msg = controller.start(bed_list=beds, durations=durations)
    return jsonify({"ok":ok,"message":msg}), 200 if ok else 409

@app.route("/api/stop", methods=["POST"])
@app.route("/api/irrigation/stop", methods=["POST"])
def api_stop():
    data = request.get_json(silent=True) or {}
    emergency = bool(data.get("emergency"))
    was = controller.stop("Emergency Stop" if emergency else "Stopped")
    if emergency: db_save_alert(0, "EMERGENCY STOP triggered")
    return jsonify({"ok":True,"was_running":was,"emergency":emergency})

@app.route("/api/estop", methods=["POST"])
def api_estop():
    controller.stop("Emergency Stop")
    db_save_alert(0, "EMERGENCY STOP triggered")
    return jsonify({"ok":True})

@app.route("/api/valve/<int:valve_id>/open", methods=["POST"])
def valve_open(valve_id):
    ok,msg=controller.manual_open(valve_id-1)
    return jsonify({"ok":ok,"message":msg}), 200 if ok else 409

@app.route("/api/valve/<int:valve_id>/close", methods=["POST"])
def valve_close(valve_id):
    ok,msg=controller.manual_close(valve_id-1)
    return jsonify({"ok":ok,"message":msg}), 200 if ok else 409

@app.route("/api/config", methods=["GET","POST"])
def api_config():
    global MOISTURE_THRESHOLD
    if request.method=="POST":
        data=request.get_json(silent=True) or {}
        if "moisture_threshold" in data:
            try: controller.set_moisture_threshold(data["moisture_threshold"])
            except ValueError as e: return jsonify({"ok":False,"message":str(e)}),400
    return jsonify({"ok":True,"moisture_threshold":MOISTURE_THRESHOLD,
                    "beds":NUM_BEDS,"timeout_seconds":BED_TIMEOUT_SECONDS})

@app.route("/api/history")
def api_history():
    try: limit=max(1,min(100,int(request.args.get("limit",30))))
    except: limit=30
    return jsonify(db_fetch_history(limit))

@app.route("/api/history/clear", methods=["POST","DELETE"])
def api_history_clear():
    db_clear_history()
    return jsonify({"ok":True,"message":"History cleared"})

@app.route("/api/alerts")
def api_alerts():
    try: limit=max(1,min(100,int(request.args.get("limit",20))))
    except: limit=20
    return jsonify(db_fetch_alerts(limit))

@app.route("/api/schedule", methods=["GET","POST"])
def api_schedule():
    if request.method=="GET":
        return jsonify(db_get_pending_schedules())
    data=request.get_json(silent=True) or {}
    beds=data.get("beds",[])
    date_str=data.get("date"); time_str=data.get("time"); duration=data.get("duration")
    if not beds or not date_str or not time_str or not duration:
        return jsonify({"ok":False,"message":"Missing fields"}),400
    try:
        duration_sec=float(duration)*60.0
        start=f"{date_str}T{time_str}:00"
        datetime.strptime(start,"%Y-%m-%dT%H:%M:%S")
        ids=[]
        for b in beds:
            b=int(b)
            if 1<=b<=NUM_BEDS: ids.append(db_create_schedule(b,start,duration_sec))
        return jsonify({"ok":True,"message":"Schedule created","ids":ids}),200
    except Exception as e:
        return jsonify({"ok":False,"message":str(e)}),400

@app.route("/api/schedule/cancel", methods=["POST","DELETE"])
def api_schedule_cancel():
    db_cancel_all_schedules()
    return jsonify({"ok":True,"message":"Schedule cancelled"})

@app.route("/api/schedule/<int:schedule_id>", methods=["DELETE"])
def api_schedule_delete(schedule_id):
    db_cancel_schedules([schedule_id])
    return jsonify({"ok":True})

@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "",204

if __name__=="__main__":
    db_init()
    threading.Thread(target=drying_loop,daemon=True).start()
    threading.Thread(target=scheduler_loop,daemon=True).start()
    print(f"IRRY BACKEND: http://127.0.0.1:5000 | files: {APP_DIR}")
    app.run(host="0.0.0.0",port=5000,debug=False,use_reloader=False)
