from flask import Flask, render_template_string, request, jsonify, send_from_directory, abort
import threading
import time
import math
import os
import subprocess
import pathlib

# --- HARDWARE IMPORTS (STRIPPED DOWN) ---
import board
import busio
import adafruit_pca9685
from adafruit_motor import servo
from adafruit_ina219 import INA219

app = Flask(__name__)

REC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
TOKEN = "super-secret-token"

def check_auth():
    if request.headers.get("X-API-Token") != TOKEN:
        abort(401)

# --- GLOBAL DATA STRUCTURES ---
# Static values kept so the frontend JS doesn't crash
telemetry_data = {
    "imu1": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "accX": 0.0, "accY": 0.0, "accZ": 0.0, "gyrX": 0.0, "gyrY": 0.0, "gyrZ": 0.0, "magX": 0.0, "magY": 0.0, "magZ": 0.0, "temp": 0.0},
    "imu2": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "accX": 0.0, "accY": 0.0, "accZ": 0.0, "gyrX": 0.0, "gyrY": 0.0, "gyrZ": 0.0, "magX": 0.0, "magY": 0.0, "magZ": 0.0, "temp": 0.0},
    "bend": {"sensor1": 512, "sensor2": 512},
    "battery": {"voltage": "N/A", "percent": "N/A"},
    "servos": {"servo1_pos": 90, "servo2_pos": 90}
}

# --- ANGUILLIFORM KINEMATICS ---
gait_params = {
    'frequency': 1.0,     # f (or 1/T) in Hz
    'wavelength': 1.0,    # lambda (normalized to Body Length L)
    'amplitude': 45.0,    # Max tail angle in degrees
    'gain': 2.0           # Hardware amplification multiplier
}

servo_params = {
    'S1': {'enabled': True, 'channel': 0, 'center': 90, 'x_L': 0.3, 'gain': 1.0},
    'S2': {'enabled': False, 'channel': 1, 'center': 90, 'x_L': 0.7, 'gain': 1.0}
}

# --- DIRECT HARDWARE INITIALIZATION ---
print("Initializing Hardware (Servos & Battery Only)...")
i2c = busio.I2C(board.SCL, board.SDA)

# 1. Servos on Driver Channel 0 and 1
try:
    pca = adafruit_pca9685.PCA9685(i2c)
    pca.frequency = 50
    servo_0 = servo.Servo(pca.channels[0])
    servo_1 = servo.Servo(pca.channels[1])
    print("Servo driver online.")
except Exception as e:
    print(f"Servo driver offline: {e}")
    servo_0 = None
    servo_1 = None
    
# 2. Waveshare UPS Battery Monitor (INA219)
try:
    ina219 = INA219(i2c, addr=0x42)
    print("Battery monitor online.")
except Exception:
    try:
        ina219 = INA219(i2c, addr=0x43) # Auto-fallback for Waveshare boards
        print("Battery monitor online.")
    except Exception as e:
        print(f"UPS Battery Monitor offline: {e}")
        ina219 = None

print("Hardware Ready. Starting server...")


# --- REAL-TIME CONTROL & SENSOR LOOP ---
def hardware_loop():
    start_time = time.time()
    
    while True:
        elapsed = time.time() - start_time

        # --- 1. Read Battery Voltage ---
        if ina219 is not None:
            try:
                voltage = ina219.bus_voltage
                percent = ((voltage - 3.2) / (4.2 - 3.2)) * 100
                percent = max(0, min(100, percent))
                
                telemetry_data["battery"]["voltage"] = f"{voltage:.2f}"
                telemetry_data["battery"]["percent"] = f"{int(percent)}"
            except Exception:
                telemetry_data["battery"]["voltage"] = "N/A"
                telemetry_data["battery"]["percent"] = "N/A"

        # --- 2. Drive Servos via Anguilliform Equation ---
        f = gait_params['frequency']
        lam = gait_params['wavelength']
        A_max = gait_params['amplitude']
        gain = gait_params['gain']
        
        for target, srv in [('S1', servo_0), ('S2', servo_1)]:
            if srv is not None:
                params = servo_params[target]
                if params['enabled']:
                    x_L = params['x_L']
                    
                    # Amplitude envelope a(x)
                    env_val = 0.351 * math.sin(x_L - 1.796) + 0.359
                    amplitude_multiplier = env_val / 0.10972 
                    s_gain = params.get('gain', 1.0)
                    amp = A_max * amplitude_multiplier * gain * s_gain
                    
                    # Traveling wave phase
                    phase = 2 * math.pi * ((x_L / lam) - (elapsed * f))
                    
                    # Output angle mapping
                    angle = params['center'] + (amp * math.sin(phase))
                    angle = max(0, min(180, angle)) # Clamp bounds securely
                    
                    telemetry_data["servos"][f"{target.lower()}_pos"] = angle
                    
                    try:
                        srv.angle = angle
                    except Exception:
                        pass
                    
        time.sleep(0.02) # 50Hz loop

# Start hardware thread
thread = threading.Thread(target=hardware_loop, daemon=True)
thread.start()

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, rec_dir=REC_DIR)

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    return jsonify(telemetry_data)

@app.route('/api/command', methods=['POST'])
def send_command():
    data = request.json
    command = data.get('command')
    
    if command == 'UPDATE_GAIT':
        gait_params['amplitude'] = float(data['amplitude'])
        gait_params['frequency'] = float(data['frequency'])
        gait_params['wavelength'] = float(data['wavelength'])
        if 'gain' in data:
            gait_params['gain'] = float(data['gain'])
        return jsonify({"status": "success"})
        
    elif command == 'UPDATE_SERVO_POS':
        target = data.get('target')
        if target in servo_params:
            servo_params[target]['x_L'] = float(data['x_L'])
            return jsonify({"status": "success"})
            
    elif command == 'UPDATE_SERVO_GAIN':
        target = data.get('target')
        if target in servo_params:
            servo_params[target]['gain'] = float(data['gain'])
            return jsonify({"status": "success"})

    elif command == 'CENTER_ALL':
        # Disable the wave logic and force servos to their center position
        for target in servo_params:
            servo_params[target]['enabled'] = False
            
        try:
            if servo_0 is not None: servo_0.angle = servo_params['S1']['center']
            if servo_1 is not None: servo_1.angle = servo_params['S2']['center']
        except Exception:
            pass
        return jsonify({"status": "success"})
            
    elif command == 'TOGGLE_POWER':
        target = data.get('target')
        if target in servo_params:
            is_on = (data['state'] == 'ON')
            servo_params[target]['enabled'] = is_on
            
            if not is_on:
                # Return to center position when powered off
                try:
                    if target == 'S1' and servo_0 is not None:
                        servo_0.angle = servo_params[target]['center']
                    if target == 'S2' and servo_1 is not None:
                        servo_1.angle = servo_params[target]['center']
                except Exception:
                    pass
            return jsonify({"status": "success"})
            
    return jsonify({"status": "error", "message": "Unknown command"}), 400

@app.route('/api/record/status')
def record_status():
    check_auth()
    p = subprocess.run(["systemctl", "is-active", "--quiet", "cam-record"])
    return jsonify({"active": p.returncode == 0})

@app.route('/api/record/start', methods=['POST'])
def record_start():
    check_auth()
    subprocess.run(["sudo", "systemctl", "start", "cam-record"])
    return jsonify({"ok": True})

@app.route('/api/record/stop', methods=['POST'])
def record_stop():
    check_auth()
    subprocess.run(["sudo", "systemctl", "stop", "cam-record"])
    return jsonify({"ok": True})

@app.route('/api/files')
def list_files():
    check_auth()
    pathlib.Path(REC_DIR).mkdir(parents=True, exist_ok=True)
    items = []
    for name in sorted(os.listdir(REC_DIR), reverse=True):
        p = os.path.join(REC_DIR, name)
        if os.path.isfile(p) and not name.startswith('.'):
            st = os.stat(p)
            items.append({
                "name": name,
                "size_mb": round(st.st_size / 1024 / 1024, 2),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            })
    return jsonify({"files": items})

@app.route('/api/files/download/<path:name>')
def download_file(name):
    check_auth()
    return send_from_directory(REC_DIR, name, as_attachment=True)

@app.route('/api/files/delete', methods=['POST'])
def delete_file():
    check_auth()
    name = request.json.get("name", "")
    p = os.path.join(REC_DIR, name)
    if not os.path.abspath(p).startswith(os.path.abspath(REC_DIR)):
        abort(400)
    if os.path.exists(p):
        os.remove(p)
    return jsonify({"ok": True})

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Telemetry</title>
    <style>
        /* MATLAB Light Mode Aesthetic */
        body { font-family: Arial, sans-serif; background-color: #FFFFFF; color: #000000; margin: 0; padding: 20px; }
        h1 { font-size: 24px; font-weight: bold; border-bottom: 2px solid #D0D0D0; padding-bottom: 10px; margin-bottom: 20px; }
        h2 { font-size: 16px; font-weight: bold; background-color: #F0F0F0; padding: 5px 10px; border: 1px solid #D0D0D0; margin-top: 0; }
        
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { border: 1px solid #D0D0D0; padding: 15px; background-color: #FAFAFA; }
        
        /* Data Tables */
        table { width: 100%; border-collapse: collapse; margin-bottom: 15px; font-family: 'Courier New', Courier, monospace; font-size: 14px; }
        th, td { border: 1px solid #D0D0D0; padding: 4px 8px; text-align: right; }
        th { background-color: #E8E8E8; text-align: center; }
        .row-label { font-family: Arial, sans-serif; font-weight: bold; text-align: left; background-color: #F8F8F8;}
        
        /* 3D Cubes */
        .scene-container { display: flex; justify-content: space-around; margin-bottom: 20px; }
        .scene { width: 100px; height: 100px; perspective: 400px; }
        .cube { width: 100%; height: 100%; position: relative; transform-style: preserve-3d; transform: translateZ(-50px); }
        .cube__face { position: absolute; width: 100px; height: 100px; border: 1px solid #0000FF; background: rgba(0, 0, 255, 0.1); font-size: 12px; display: flex; align-items: center; justify-content: center; }
        .cube__face--front  { transform: rotateY(  0deg) translateZ(50px); }
        .cube__face--right  { transform: rotateY( 90deg) translateZ(50px); }
        .cube__face--back   { transform: rotateY(180deg) translateZ(50px); }
        .cube__face--left   { transform: rotateY(-90deg) translateZ(50px); }
        .cube__face--top    { transform: rotateX( 90deg) translateZ(50px); }
        .cube__face--bottom { transform: rotateX(-90deg) translateZ(50px); }
        
        .imu-label { text-align: center; font-weight: bold; margin-top: 5px; font-size: 12px; }

        /* Compass */
        .compass-wrapper { display: flex; align-items: center; justify-content: center; margin-bottom: 20px; }
        .compass-circle { width: 80px; height: 80px; border: 2px solid #000; border-radius: 50%; position: relative; background: #FFF; }
        .compass-circle::before { content: 'N'; position: absolute; top: 2px; left: 50%; transform: translateX(-50%); font-size: 10px; font-weight: bold;}
        .compass-needle { width: 2px; height: 90%; background: linear-gradient(to bottom, #FF0000 50%, #000000 50%); position: absolute; top: 5%; left: calc(50% - 1px); transform-origin: center; transition: transform 0.1s linear;}

        /* Camera Box */
        .camera-placeholder { background-color: #1a1a1a; width: 100%; height: 240px; display: flex; align-items: center; justify-content: center; border: 1px solid #A0A0A0; margin-bottom: 25px; box-shadow: inset 0 0 10px #000; }
        .camera-text { color: #ff4444; font-family: 'Courier New', Courier, monospace; font-size: 16px; font-weight: bold; letter-spacing: 2px; }

        /* Controls */
        .control-group { margin-bottom: 20px; border: 1px solid #E0E0E0; padding: 10px; background: #FFF; }
        button { border: 1px solid #A0A0A0; padding: 5px 15px; cursor: pointer; background-color: #E8E8E8; font-size: 14px; font-family: Arial; }
        button:hover { background-color: #D8D8D8; }
        .btn-on { background-color: #D0FFD0; border-color: #00A000; }
        .btn-off { background-color: #FFD0D0; border-color: #A00000; }
        
        .slider-row { display: flex; align-items: center; margin: 10px 0; font-size: 14px; }
        .slider-row label { width: 130px; }
        input[type=range] { flex-grow: 1; margin: 0 10px; }
        .val-display { width: 35px; text-align: right; font-family: monospace; }
        
        canvas { background-color: #FFF; border: 1px solid #D0D0D0; display: block; margin: 10px 0; width: 100%; height: 100px; }
    </style>
</head>
<body>
    <div style="position: absolute; top: 20px; right: 20px; text-align: right; font-family: monospace; font-size: 16px; background: #F8F8F8; padding: 5px 10px; border: 1px solid #D0D0D0;">
        <strong>UPS BATTERY:</strong> <span id="batt_display" style="font-weight: bold;">Checking...</span>
    </div>

    <h1>Telemetry (Servos Only)</h1>
    
    <div class="grid">
        <div class="card">
            <h2>IMU Telemetry & Orientation</h2>
            
            <div class="scene-container">
                <div>
                    <div class="scene">
                        <div class="cube" id="cube1">
                            <div class="cube__face cube__face--front">IMU 1</div><div class="cube__face cube__face--back"></div>
                            <div class="cube__face cube__face--right"></div><div class="cube__face cube__face--left"></div>
                            <div class="cube__face cube__face--top"></div><div class="cube__face cube__face--bottom"></div>
                        </div>
                    </div>
                    <div class="imu-label">Node 1 (Offline)</div>
                </div>
                <div>
                    <div class="scene">
                        <div class="cube" id="cube2">
                            <div class="cube__face cube__face--front">IMU 2</div><div class="cube__face cube__face--back"></div>
                            <div class="cube__face cube__face--right"></div><div class="cube__face cube__face--left"></div>
                            <div class="cube__face cube__face--top"></div><div class="cube__face cube__face--bottom"></div>
                        </div>
                    </div>
                    <div class="imu-label">Node 2 (Offline)</div>
                </div>
                <div>
                    <div class="compass-wrapper" style="margin:0; height:100px;">
                        <div class="compass-circle"><div class="compass-needle" id="mag_needle"></div></div>
                    </div>
                    <div class="imu-label">Heading: <span id="heading_val">0.0</span>&deg;</div>
                </div>
            </div>

            <h2>Real-time Acceleration Trace</h2>
            <div style="font-family:monospace; font-size:12px; margin-bottom:5px;">
                <strong>IMU 1 (Solid):</strong> <span style="color:#FF0000; font-weight:bold;">X</span> | <span style="color:#00A000; font-weight:bold;">Y</span> | <span style="color:#0000FF; font-weight:bold;">Z</span><br>
                <strong>IMU 2 (Dashed):</strong> <span style="color:#FF8888; font-weight:bold;">X</span> | <span style="color:#33CC33; font-weight:bold;">Y</span> | <span style="color:#8888FF; font-weight:bold;">Z</span>
            </div>
            <canvas id="accelCanvas" style="height: 120px;"></canvas>

            <table>
                <tr><th colspan="4">IMU 1 Data Stream (Offline) | Temp: <span id="t1_temp">0.0</span>&deg;C</th></tr>
                <tr><th>Vector</th><th>X</th><th>Y</th><th>Z</th></tr>
                <tr><td class="row-label">Accel (mg)</td><td id="t1_ax">0</td><td id="t1_ay">0</td><td id="t1_az">0</td></tr>
                <tr><td class="row-label">Gyro (dps)</td><td id="t1_gx">0</td><td id="t1_gy">0</td><td id="t1_gz">0</td></tr>
                <tr><td class="row-label">Mag (&mu;T)</td><td id="t1_mx">0</td><td id="t1_my">0</td><td id="t1_mz">0</td></tr>
            </table>

            <table>
                <tr><th colspan="4">IMU 2 Data Stream (Offline) | Temp: <span id="t2_temp">0.0</span>&deg;C</th></tr>
                <tr><th>Vector</th><th>X</th><th>Y</th><th>Z</th></tr>
                <tr><td class="row-label">Accel (mg)</td><td id="t2_ax">0</td><td id="t2_ay">0</td><td id="t2_az">0</td></tr>
                <tr><td class="row-label">Gyro (dps)</td><td id="t2_gx">0</td><td id="t2_gy">0</td><td id="t2_gz">0</td></tr>
                <tr><td class="row-label">Mag (&mu;T)</td><td id="t2_mx">0</td><td id="t2_my">0</td><td id="t2_mz">0</td></tr>
            </table>

            <h2>Bend Sensor Deflection</h2>
            <div style="font-family:monospace; font-size:14px; margin-bottom:5px; display:flex; justify-content:space-between; align-items:center;">
                <div>S1 ADC: <span id="b1_val">0</span> | S2 ADC: <span id="b2_val">0</span></div>
                <button onclick="tareBend()" style="padding: 2px 8px; font-size:12px;">Set Center (Tare)</button>
            </div>
            <div class="slider-row" style="font-size:12px; margin: 5px 0;">
                <label style="width: 100px;">Pos Sensitivity</label>
                <input type="range" id="sens_pos" min="1" max="1000" value="80" oninput="document.getElementById('sens_pos_val').innerText=this.value">
                <span class="val-display" id="sens_pos_val">80</span>
            </div>
            <div class="slider-row" style="font-size:12px; margin: 5px 0;">
                <label style="width: 100px;">Neg Sensitivity</label>
                <input type="range" id="sens_neg" min="1" max="1000" value="160" oninput="document.getElementById('sens_neg_val').innerText=this.value">
                <span class="val-display" id="sens_neg_val">160</span>
            </div>
            <canvas id="bendCanvas" style="height: 220px;"></canvas>
        </div>

        <div class="card">
            <h2>Forward Camera Feed</h2>
            <iframe
                id="camStream"
                style="width:100%;height:300px;border:none;border-radius:4px;background:#000;"
                allowfullscreen>
            </iframe>
            <div style="font-size:.85rem;color:#666;margin-bottom:8px;">Stream: WebRTC via MediaMTX | <span id="streamStatus" style="color:#137a1d">Connected</span></div>

            <div style="display: flex; gap: 10px; margin-bottom: 25px;">
                <button id="record_btn" style="flex: 1; border-color: #A00000; color: #A00000; font-weight: bold;" onclick="startRecording()">&#9679; Start Recording</button>
                <button id="stop_btn" style="flex: 1; border-color: #A00000; color: #A00000; font-weight: bold;" onclick="stopRecording()">&#9632; Stop Recording</button>
                <span id="statusText" style="margin-left:auto; font-family:monospace; align-self:center; color:#c21f1f;">Status: IDLE</span>
            </div>
            <h2 style="margin-top:20px;">Recordings <button onclick="refreshFiles()" style="font-size:12px; padding:3px 8px; margin-left:10px;">Refresh List</button></h2>
            <span style="font-size:.85rem; font-family:monospace;">Folder: <code>{{ rec_dir }}</code></span>
            <table style="margin-top:8px;">
                <thead><tr><th style="text-align:left;">File</th><th>Size (MB)</th><th>Modified</th><th>Actions</th></tr></thead>
                <tbody id="filesBody"><tr><td colspan="4">Loading...</td></tr></tbody>
            </table>

            <h2>Anguilliform Kinematics (Eq. 2)</h2>
            <div class="control-group">
                <div class="slider-row">
                    <label>Tail Amp. (&deg;)</label>
                    <input type="range" id="gait_amp" min="10" max="90" value="45" oninput="updateGait()">
                    <span class="val-display" id="gait_amp_val">45</span>
                </div>
                <div class="slider-row">
                    <label>Frequency (Hz)</label>
                    <input type="range" id="gait_freq" min="0.1" max="5.0" step="0.1" value="1.0" oninput="updateGait()">
                    <span class="val-display" id="gait_freq_val">1.0</span>
                </div>
                <div class="slider-row">
                    <label>Wavelength (&lambda;/L)</label>
                    <input type="range" id="gait_lam" min="0.5" max="3.0" step="0.1" value="1.0" oninput="updateGait()">
                    <span class="val-display" id="gait_lam_val">1.0</span>
                </div>
                <div class="slider-row">
                    <label>Hardware Gain</label>
                    <input type="range" id="gait_gain" min="0.1" max="5.0" step="0.1" value="2.0" oninput="updateGait()">
                    <span class="val-display" id="gait_gain_val">2.0</span>
                </div>
                <canvas id="bodyWaveCanvas" style="height: 180px;"></canvas>
            </div>

            <h3 style="font-size:14px; margin-bottom:5px; border-bottom: 1px solid #CCC; padding-bottom:3px;">Actuator Configurations</h3>
            
            <button onclick="centerAllServos()" style="width: 100%; border-color: #0000FF; color: #0000FF; font-weight: bold; margin-bottom: 15px; padding: 10px; background-color: #E8E8FF;">
                Lock All Servos to Center (For Hardware Alignment)
            </button>

            <div class="control-group">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>Servo 1 (Channel 0)</strong>
                    <button id="s1_power_btn" class="btn-on" onclick="togglePower('S1')">Power: ON</button>
                </div>
                <div class="slider-row">
                    <label>Body Position (x/L)</label>
                    <input type="range" id="s1_pos" min="0" max="1" step="0.05" value="0.3" oninput="updateServoPos('S1', this.value)">
                    <span class="val-display" id="s1_pos_val">0.30</span>
                </div>
                <div class="slider-row">
                    <label>Servo Gain</label>
                    <input type="range" id="s1_gain" min="0.1" max="5.0" step="0.1" value="1.0" oninput="updateServoGain('S1', this.value)">
                    <span class="val-display" id="s1_gain_val">1.0</span>
                </div>
            </div>

            <div class="control-group">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>Servo 2 (Channel 1)</strong>
                    <button id="s2_power_btn" class="btn-off" onclick="togglePower('S2')">Power: OFF</button>
                </div>
                <div class="slider-row">
                    <label>Body Position (x/L)</label>
                    <input type="range" id="s2_pos" min="0" max="1" step="0.05" value="0.7" oninput="updateServoPos('S2', this.value)">
                    <span class="val-display" id="s2_pos_val">0.70</span>
                </div>
                <div class="slider-row">
                    <label>Servo Gain</label>
                    <input type="range" id="s2_gain" min="0.1" max="5.0" step="0.1" value="1.0" oninput="updateServoGain('S2', this.value)">
                    <span class="val-display" id="s2_gain_val">1.0</span>
                </div>
            </div>

            <h3 style="font-size:14px; margin-bottom:5px; border-bottom: 1px solid #CCC; padding-bottom:3px;">PWM Signal (50Hz / 20ms Period)</h3>
            <canvas id="pwmCanvas" style="height: 100px;"></canvas>
        </div>
    </div>

    <script>
        let servosEnabled = { 'S1': true, 'S2': false };
        let latestData = null;
        let accelHistory = [];
        
        // Variables for bend sensor calibration
        let b1_center = 512;
        let b2_center = 512;

        // Fetch data at 10Hz
        setInterval(() => {
            fetch('/api/telemetry')
                .then(response => response.json())
                .then(data => { 
                    latestData = data; 
                    accelHistory.push({
                        x1: data.imu1.accX, y1: data.imu1.accY, z1: data.imu1.accZ,
                        x2: data.imu2.accX, y2: data.imu2.accY, z2: data.imu2.accZ
                    });
                    if (accelHistory.length > 100) accelHistory.shift();
                });
        }, 100);

        // Update readable text tables at 2Hz
        setInterval(() => {
            if(!latestData) return;
            document.getElementById('t1_ax').innerText = latestData.imu1.accX.toFixed(1);
            document.getElementById('t1_ay').innerText = latestData.imu1.accY.toFixed(1);
            document.getElementById('t1_az').innerText = latestData.imu1.accZ.toFixed(1);
            document.getElementById('t1_gx').innerText = latestData.imu1.gyrX.toFixed(1);
            document.getElementById('t1_gy').innerText = latestData.imu1.gyrY.toFixed(1);
            document.getElementById('t1_gz').innerText = latestData.imu1.gyrZ.toFixed(1);
            document.getElementById('t1_mx').innerText = latestData.imu1.magX.toFixed(1);
            document.getElementById('t1_my').innerText = latestData.imu1.magY.toFixed(1);
            document.getElementById('t1_mz').innerText = latestData.imu1.magZ.toFixed(1);
            document.getElementById('t1_temp').innerText = latestData.imu1.temp.toFixed(1);
            document.getElementById('b1_val').innerText = latestData.bend.sensor1;
            document.getElementById('b2_val').innerText = latestData.bend.sensor2;
            
            // Update Battery
            if (latestData.battery.voltage === "N/A") {
                document.getElementById('batt_display').innerText = "Data Not Available";
                document.getElementById('batt_display').style.color = "#A00000";
            } else {
                document.getElementById('batt_display').innerHTML = `<span style="color: #00A000;">${latestData.battery.voltage}V</span> | <span style="color: #0000FF;">${latestData.battery.percent}%</span>`;
                document.getElementById('batt_display').style.color = "";
            }
        }, 500);

        // 60FPS Animation Loop for visual elements
        function animate() {
            let timeVar = performance.now() / 1000; // Accurate seconds for the wave eq

            if (latestData) {
                // Orient Cubes
                document.getElementById('cube1').style.transform = `translateZ(-50px) rotateX(${-latestData.imu1.pitch}deg) rotateY(${latestData.imu1.yaw}deg) rotateZ(${latestData.imu1.roll}deg)`;
                
                let magHeading = Math.atan2(latestData.imu1.magY, latestData.imu1.magX) * (180 / Math.PI);
                let displayHeading = (magHeading + 360) % 360; // Convert to standard 0-360 range
                
                document.getElementById('mag_needle').style.transform = `rotate(${magHeading + 90}deg)`;
                document.getElementById('heading_val').innerText = displayHeading.toFixed(1);
                
                drawBendAnimation(latestData.bend.sensor1, latestData.bend.sensor2);
                drawAccelTrace();
            }

            // Draw Mathematical Body Wave
            drawBodyWave(timeVar);
            
            // Draw PWM Signals
            drawPWM();

            requestAnimationFrame(animate);
        }
        
        function tareBend() {
            if(latestData) {
                b1_center = latestData.bend.sensor1;
                b2_center = latestData.bend.sensor2;
            }
        }
        
        function drawBendAnimation(b1, b2) {
            const cvs = document.getElementById('bendCanvas');
            cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
            const ctx = cvs.getContext('2d');
            
            ctx.strokeStyle = '#E0E0E0'; ctx.lineWidth = 1;
            for(let i=0; i<cvs.width; i+=20) { ctx.beginPath(); ctx.moveTo(i,0); ctx.lineTo(i,cvs.height); ctx.stroke(); }
            for(let i=0; i<cvs.height; i+=20) { ctx.beginPath(); ctx.moveTo(0,i); ctx.lineTo(cvs.width,i); ctx.stroke(); }

            let diff1 = b1 - b1_center;
            let diff2 = b2 - b2_center;
            let sensPos = parseFloat(document.getElementById('sens_pos').value);
            let sensNeg = parseFloat(document.getElementById('sens_neg').value);

            let sens1 = (diff1 > 0) ? sensPos : sensNeg;
            let sens2 = (diff2 > 0) ? sensPos : sensNeg;

            let a1 = (diff1 / sens1) * (Math.PI / 3); 
            let a2 = (diff2 / sens2) * (Math.PI / 3); 
            a1 = Math.max(-Math.PI/3, Math.min(Math.PI/3, a1));
            a2 = Math.max(-Math.PI/3, Math.min(Math.PI/3, a2));

            let sX = cvs.width * 0.1; let sY = cvs.height / 2; let seg = cvs.width * 0.35;
            let x1 = sX + seg * Math.cos(a1); let y1 = sY + seg * Math.sin(a1);
            let x2 = x1 + seg * Math.cos(a1+a2); let y2 = y1 + seg * Math.sin(a1+a2);

            ctx.strokeStyle = '#0000FF'; ctx.lineWidth = 6; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
            ctx.beginPath(); ctx.moveTo(sX, sY); 
            ctx.bezierCurveTo(sX + seg * 0.5, sY, x1 - seg * 0.5 * Math.cos(a1), y1 - seg * 0.5 * Math.sin(a1), x1, y1);
            ctx.bezierCurveTo(x1 + seg * 0.5 * Math.cos(a1), y1 + seg * 0.5 * Math.sin(a1), x2 - seg * 0.5 * Math.cos(a1+a2), y2 - seg * 0.5 * Math.sin(a1+a2), x2, y2);
            ctx.stroke();
            
            ctx.fillStyle = '#FF0000';
            ctx.beginPath(); ctx.arc(sX, sY, 5, 0, Math.PI*2); ctx.fill();
            ctx.beginPath(); ctx.arc(x1, y1, 5, 0, Math.PI*2); ctx.fill();
            ctx.beginPath(); ctx.arc(x2, y2, 5, 0, Math.PI*2); ctx.fill();
        }

        function drawAccelTrace() {
            const cvs = document.getElementById('accelCanvas');
            cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
            const ctx = cvs.getContext('2d');

            ctx.strokeStyle = '#E0E0E0'; ctx.lineWidth = 1;
            for(let i=0; i<cvs.width; i+=20) { ctx.beginPath(); ctx.moveTo(i,0); ctx.lineTo(i,cvs.height); ctx.stroke(); }
            for(let i=0; i<cvs.height; i+=20) { ctx.beginPath(); ctx.moveTo(0,i); ctx.lineTo(cvs.width,i); ctx.stroke(); }
            
            let midY = cvs.height / 2;
            ctx.strokeStyle = '#A0A0A0'; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(0, midY); ctx.lineTo(cvs.width, midY); ctx.stroke();

            if (accelHistory.length < 2) return;

            const maxG = 10.0; 
            const stepX = cvs.width / 100;

            const drawLine = (axis, color, isDashed) => {
                ctx.strokeStyle = color; ctx.lineWidth = 2;
                if (isDashed) ctx.setLineDash([5, 5]); else ctx.setLineDash([]);
                ctx.beginPath();
                for(let i=0; i<accelHistory.length; i++) {
                    let y = midY - ((accelHistory[i][axis] / maxG) * midY);
                    if (i === 0) ctx.moveTo(i * stepX, y); else ctx.lineTo(i * stepX, y);
                }
                ctx.stroke();
                ctx.setLineDash([]);
            };

            drawLine('x1', '#FF0000', false); drawLine('y1', '#00A000', false); drawLine('z1', '#0000FF', false); 
            drawLine('x2', '#FF8888', true); drawLine('y2', '#33CC33', true); drawLine('z2', '#8888FF', true); 
        }

        function drawBodyWave(timeVar) {
            const cvs = document.getElementById('bodyWaveCanvas');
            cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
            const ctx = cvs.getContext('2d');

            // Draw Background Grid
            ctx.strokeStyle = '#E0E0E0'; ctx.lineWidth = 1;
            for(let i=0; i<cvs.width; i+=20) { ctx.beginPath(); ctx.moveTo(i,0); ctx.lineTo(i,cvs.height); ctx.stroke(); }
            for(let i=0; i<cvs.height; i+=20) { ctx.beginPath(); ctx.moveTo(0,i); ctx.lineTo(cvs.width,i); ctx.stroke(); }
            
            let midY = cvs.height / 2;
            ctx.strokeStyle = '#A0A0A0'; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(0, midY); ctx.lineTo(cvs.width, midY); ctx.stroke();

            let f = parseFloat(document.getElementById('gait_freq').value);
            let lam = parseFloat(document.getElementById('gait_lam').value);
            let maxVisScale = cvs.height / 2.5;

            // Amplitude Envelope Function a(x)
            const getEnvMultiplier = (x_L) => {
                let val = 0.351 * Math.sin(x_L - 1.796) + 0.359;
                return (val / 0.10972); 
            };

            // Draw Envelope Guides
            ctx.strokeStyle = '#FFBBBB'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
            ctx.beginPath();
            for(let x = 0; x <= cvs.width; x+=5) {
                let envY = getEnvMultiplier(x / cvs.width) * maxVisScale;
                if(x===0) ctx.moveTo(x, midY - envY); else ctx.lineTo(x, midY - envY);
            }
            ctx.stroke();
            ctx.beginPath();
            for(let x = 0; x <= cvs.width; x+=5) {
                let envY = getEnvMultiplier(x / cvs.width) * maxVisScale;
                if(x===0) ctx.moveTo(x, midY + envY); else ctx.lineTo(x, midY + envY);
            }
            ctx.stroke();
            ctx.setLineDash([]);

            // Draw Active Traveling Wave
            ctx.strokeStyle = '#0000FF'; ctx.lineWidth = 3;
            ctx.beginPath();
            for(let x = 0; x <= cvs.width; x+=2) {
                let x_L = x / cvs.width;
                let M = getEnvMultiplier(x_L);
                let phase = 2 * Math.PI * ((x_L / lam) - (timeVar * f));
                let y = midY - (M * Math.sin(phase) * maxVisScale); 
                if(x===0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.stroke();

            // Draw Servo Trackers
            let s1_xL = parseFloat(document.getElementById('s1_pos').value);
            let s2_xL = parseFloat(document.getElementById('s2_pos').value);

            const drawTracker = (x_L, color, isEnabled) => {
                if(!isEnabled) return;
                let x = x_L * cvs.width;
                let phase = 2 * Math.PI * ((x_L / lam) - (timeVar * f));
                let y = midY - (getEnvMultiplier(x_L) * Math.sin(phase) * maxVisScale);

                ctx.fillStyle = color;
                ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI*2); ctx.fill();
                ctx.strokeStyle = '#000'; ctx.lineWidth = 1; ctx.stroke();
            };

            drawTracker(s1_xL, '#00FF00', servosEnabled['S1']); // Green dot
            drawTracker(s2_xL, '#FF8800', servosEnabled['S2']); // Orange dot
        }

        function drawPWM() {
            const cvs = document.getElementById('pwmCanvas');
            cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
            const ctx = cvs.getContext('2d');

            // Draw 20ms timeline grid
            ctx.strokeStyle = '#E0E0E0'; ctx.lineWidth = 1;
            let msStep = cvs.width / 20; 
            for(let i=0; i<=20; i++) { 
                ctx.beginPath(); ctx.moveTo(i*msStep, 0); ctx.lineTo(i*msStep, cvs.height); ctx.stroke(); 
            }

            if(!latestData) return;

            let a1 = latestData.servos.servo1_pos;
            let a2 = latestData.servos.servo2_pos;

            // Calculate pulse width in ms (Default adafruit servo limits: 0.75ms to 2.25ms)
            let pw1 = servosEnabled['S1'] ? 0.75 + (a1 / 180) * 1.5 : 0; 
            let pw2 = servosEnabled['S2'] ? 0.75 + (a2 / 180) * 1.5 : 0; 

            const drawWave = (pw, color, yOffset, label) => {
                let dutyX = (pw / 20.0) * cvs.width;
                let yHigh = yOffset;
                let yLow = yOffset + 25;

                ctx.strokeStyle = color; ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(0, yLow);
                
                if (pw > 0) {
                    ctx.lineTo(0, yHigh);
                    ctx.lineTo(dutyX, yHigh);
                    ctx.lineTo(dutyX, yLow);
                }
                
                ctx.lineTo(cvs.width, yLow);
                ctx.stroke();

                ctx.fillStyle = color; ctx.font = '11px monospace';
                ctx.fillText(`${label}: ${pw > 0 ? pw.toFixed(2) + 'ms' : 'OFF'}`, 5, yHigh - 5);
            };

            drawWave(pw1, '#00A000', 20, 'S1');
            drawWave(pw2, '#FF8800', 65, 'S2');
        }

        // --- COMMANDS ---
        function centerAllServos() {
            // Update the UI switches to visually show the power is cut to the wave generator
            if (servosEnabled['S1']) { 
                servosEnabled['S1'] = false; 
                document.getElementById('s1_power_btn').innerText = 'Power: OFF'; 
                document.getElementById('s1_power_btn').className = 'btn-off'; 
            }
            if (servosEnabled['S2']) { 
                servosEnabled['S2'] = false; 
                document.getElementById('s2_power_btn').innerText = 'Power: OFF'; 
                document.getElementById('s2_power_btn').className = 'btn-off'; 
            }
            
            // Send the center command to the backend
            fetch('/api/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'CENTER_ALL' })
            });
        }

        function togglePower(target) {
            servosEnabled[target] = !servosEnabled[target];
            let state = servosEnabled[target] ? 'ON' : 'OFF';
            let prefix = target.toLowerCase();
            let btn = document.getElementById(`${prefix}_power_btn`);
            
            btn.innerText = `Power: ${state}`;
            btn.className = servosEnabled[target] ? 'btn-on' : 'btn-off';
            
            fetch('/api/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'TOGGLE_POWER', target: target, state: state })
            });
        }

        function updateGait() {
            let amp = document.getElementById('gait_amp').value;
            let freq = document.getElementById('gait_freq').value;
            let lam = document.getElementById('gait_lam').value;
            let gain = document.getElementById('gait_gain').value;

            document.getElementById('gait_amp_val').innerText = amp;
            document.getElementById('gait_freq_val').innerText = parseFloat(freq).toFixed(1);
            document.getElementById('gait_lam_val').innerText = parseFloat(lam).toFixed(1);
            document.getElementById('gait_gain_val').innerText = parseFloat(gain).toFixed(1);

            fetch('/api/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'UPDATE_GAIT', amplitude: amp, frequency: freq, wavelength: lam, gain: gain })
            });
        }

        function updateServoPos(target, val) {
            let prefix = target.toLowerCase();
            document.getElementById(`${prefix}_pos_val`).innerText = parseFloat(val).toFixed(2);

            fetch('/api/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'UPDATE_SERVO_POS', target: target, x_L: val })
            });
        }

        function updateServoGain(target, val) {
            let prefix = target.toLowerCase();
            document.getElementById(`${prefix}_gain_val`).innerText = parseFloat(val).toFixed(1);

            fetch('/api/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'UPDATE_SERVO_GAIN', target: target, gain: val })
            });
        }

        // --- CAMERA FUNCTIONS ---
        let isRecording = false;
        const API_BASE = window.location.origin;
        const TOKEN = "super-secret-token";

        async function api(path, method="GET", body=null){
            let url = API_BASE + path;
            if (method==="GET") url += (path.includes("?")?"&":"?") + "_ts=" + Date.now();
            const opts = { method, headers:{"X-API-Token":TOKEN}, cache:"no-store" };
            if (body){ opts.headers["Content-Type"]="application/json"; opts.body = JSON.stringify(body); }
            const r = await fetch(url, opts);
            if (!r.ok) throw new Error(await r.text());
            return r.json();
        }

        function renderFiles(files){
            const tb = document.getElementById("filesBody");
            if (!files.length){ tb.innerHTML = `<tr><td colspan="4" style="font-size:.85rem;">No recordings found.</td></tr>`; return; }
            tb.innerHTML = "";
            for (const f of files){
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td style="text-align:left;">${f.name}</td><td>${f.size_mb}</td><td>${f.mtime}</td>
                    <td>
                        <button onclick="downloadFile('${encodeURIComponent(f.name)}')">Download</button>
                        <button style="background:#ffeaea;" onclick="deleteFile('${encodeURIComponent(f.name)}')">Delete</button>
                    </td>`;
                tb.appendChild(tr);
            }
        }

        async function refreshFiles(){ 
            try{ renderFiles((await api("/api/files")).files); }
            catch{ document.getElementById("filesBody").innerHTML = `<tr><td colspan="4">Error listing files.</td></tr>`; } 
        }

        async function downloadFile(nameEnc){
            const res = await fetch(`${API_BASE}/api/files/download/${nameEnc}?_ts=${Date.now()}`, {headers:{"X-API-Token":TOKEN}, cache:"no-store"});
            if (!res.ok) return alert("Download failed.");
            const blob = await res.blob(), a = document.createElement("a");
            a.href = URL.createObjectURL(blob); a.download = decodeURIComponent(nameEnc);
            document.body.appendChild(a); a.click(); a.remove();
        }

        async function deleteFile(nameEnc){
            const name = decodeURIComponent(nameEnc);
            if (!confirm(`Delete ${name}?`)) return;
            try{ await api("/api/files/delete","POST",{name}); refreshFiles(); } catch{ alert("Delete failed."); }
        }

        async function refreshStatus(){
            try{
                const j = await api("/api/record/status");
                const s = document.getElementById("statusText");
                s.textContent = "Status: " + (j.active ? "RECORDING" : "IDLE");
                s.style.color = j.active ? "#137a1d" : "#c21f1f";
            }catch{ document.getElementById("statusText").textContent = "Status: error"; }
        }

        function startRecording() {
            api("/api/record/start","POST")
                .then(() => { setTimeout(refreshStatus, 500); })
                .catch(()=>alert("Start failed"));
        }

        function stopRecording() {
            api("/api/record/stop","POST")
                .then(() => { setTimeout(refreshFiles, 1000); setTimeout(refreshStatus, 500); })
                .catch(()=>alert("Stop failed"));
        }

        document.getElementById('camStream').src = `http://${window.location.hostname}:8889/cam`;
        refreshFiles();
        refreshStatus();
        setInterval(refreshStatus, 5000);

                // Start animation loop
                requestAnimationFrame(animate);
            </script>
        </body>
        </html>
        """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5051, debug=False)