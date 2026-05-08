from flask import Flask, render_template_string, request, jsonify
import serial
import threading
import json
import time

app = Flask(__name__)

# --- CONFIGURATION ---
SERIAL_PORT = '/dev/ttyACM0' 
BAUD_RATE = 115200

# Updated telemetry structure for dual IMUs
telemetry_data = {
    "imu1": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "accX": 0.0, "accY": 0.0, "accZ": 0.0, "gyrX": 0.0, "gyrY": 0.0, "gyrZ": 0.0, "magX": 0.0, "magY": 0.0, "magZ": 0.0, "temp": 0.0},
    "imu2": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "accX": 0.0, "accY": 0.0, "accZ": 0.0, "gyrX": 0.0, "gyrY": 0.0, "gyrZ": 0.0, "magX": 0.0, "magY": 0.0, "magZ": 0.0, "temp": 0.0},
    "bend": {"sensor1": 512, "sensor2": 512},
    "battery": {"voltage": "0.0", "percent": "0"},
    "servos": {"servo1_pos": 90, "servo2_pos": 90}
}

try:
    arduino = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected to Arduino on {SERIAL_PORT}")
except Exception as e:
    arduino = None
    print(f"WARNING: Could not connect to Arduino. {e}")

def read_from_arduino():
    global telemetry_data
    while True:
        if arduino and arduino.in_waiting > 0:
            try:
                line = arduino.readline().decode('utf-8').strip()
                incoming_data = json.loads(line)
                telemetry_data.update(incoming_data)
            except Exception as e:
                pass 
        time.sleep(0.01)

thread = threading.Thread(target=read_from_arduino, daemon=True)
thread.start()

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    return jsonify(telemetry_data)

@app.route('/api/command', methods=['POST'])
def send_command():
    if not arduino:
        return jsonify({"status": "error", "message": "Arduino not connected"}), 500
    
    data = request.json
    if data['command'] == 'UPDATE_SWEEP':
        command_str = f"<{data['command']},{data['target']},{data['range']},{data['speed']}>\n"
    elif data['command'] == 'TOGGLE_POWER':
        command_str = f"<{data['command']},{data['target']},{data['state']}>\n"
    else:
        return jsonify({"status": "error", "message": "Unknown command"}), 400
        
    try:
        arduino.write(command_str.encode('utf-8'))
        return jsonify({"status": "success", "command_sent": command_str})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Research Telemetry Dashboard</title>
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

        /* Controls */
        .control-group { margin-bottom: 20px; border: 1px solid #E0E0E0; padding: 10px; background: #FFF; }
        button { border: 1px solid #A0A0A0; padding: 5px 15px; cursor: pointer; background-color: #E8E8E8; font-size: 14px; font-family: Arial; }
        button:hover { background-color: #D8D8D8; }
        .btn-on { background-color: #D0FFD0; border-color: #00A000; }
        .btn-off { background-color: #FFD0D0; border-color: #A00000; }
        
        .slider-row { display: flex; align-items: center; margin: 10px 0; font-size: 14px; }
        .slider-row label { width: 120px; }
        input[type=range] { flex-grow: 1; margin: 0 10px; }
        .val-display { width: 30px; text-align: right; font-family: monospace; }
        
        canvas { background-color: #FFF; border: 1px solid #D0D0D0; display: block; margin: 10px 0; width: 100%; height: 100px; }
    </style>
</head>
<body>
    <h1>Control Dashboard</h1>
    
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
                    <div class="imu-label">Node 1 (Ch 0)</div>
                </div>
                <div>
                    <div class="scene">
                        <div class="cube" id="cube2">
                            <div class="cube__face cube__face--front">IMU 2</div><div class="cube__face cube__face--back"></div>
                            <div class="cube__face cube__face--right"></div><div class="cube__face cube__face--left"></div>
                            <div class="cube__face cube__face--top"></div><div class="cube__face cube__face--bottom"></div>
                        </div>
                    </div>
                    <div class="imu-label">Node 2 (Ch 1)</div>
                </div>
                <div>
                    <div class="compass-wrapper" style="margin:0; height:100px;">
                        <div class="compass-circle"><div class="compass-needle" id="mag_needle"></div></div>
                    </div>
                    <div class="imu-label">Avg Heading</div>
                </div>
            </div>

            <table>
                <tr><th colspan="4">IMU 1 Data Stream (Mux 0) | Temp: <span id="t1_temp">0.0</span>&deg;C</th></tr>
                <tr><th>Vector</th><th>X</th><th>Y</th><th>Z</th></tr>
                <tr><td class="row-label">Accel (mg)</td><td id="t1_ax">0</td><td id="t1_ay">0</td><td id="t1_az">0</td></tr>
                <tr><td class="row-label">Gyro (dps)</td><td id="t1_gx">0</td><td id="t1_gy">0</td><td id="t1_gz">0</td></tr>
                <tr><td class="row-label">Mag (&mu;T)</td><td id="t1_mx">0</td><td id="t1_my">0</td><td id="t1_mz">0</td></tr>
            </table>

            <table>
                <tr><th colspan="4">IMU 2 Data Stream (Mux 1) | Temp: <span id="t2_temp">0.0</span>&deg;C</th></tr>
                <tr><th>Vector</th><th>X</th><th>Y</th><th>Z</th></tr>
                <tr><td class="row-label">Accel (mg)</td><td id="t2_ax">0</td><td id="t2_ay">0</td><td id="t2_az">0</td></tr>
                <tr><td class="row-label">Gyro (dps)</td><td id="t2_gx">0</td><td id="t2_gy">0</td><td id="t2_gz">0</td></tr>
                <tr><td class="row-label">Mag (&mu;T)</td><td id="t2_mx">0</td><td id="t2_my">0</td><td id="t2_mz">0</td></tr>
            </table>

            <h2>Bend Sensor Deflection</h2>
            <div style="font-family:monospace; font-size:14px; margin-bottom:5px;">S1 ADC: <span id="b1_val">0</span> | S2 ADC: <span id="b2_val">0</span></div>
            <canvas id="bendCanvas"></canvas>
        </div>

        <div class="card">
            <h2>Propulsion Actuators</h2>
            
            <div class="control-group">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>Servo 1 (Channel 0)</strong>
                    <button id="s1_power_btn" class="btn-on" onclick="togglePower('S1')">Power: ON</button>
                </div>
                <div class="slider-row">
                    <label>Amplitude Range</label>
                    <input type="range" id="s1_range" min="10" max="90" value="45" oninput="document.getElementById('s1_range_val').innerText=this.value">
                    <span class="val-display" id="s1_range_val">45</span>
                </div>
                <div class="slider-row">
                    <label>Frequency Speed</label>
                    <input type="range" id="s1_speed" min="1" max="100" value="50" oninput="document.getElementById('s1_speed_val').innerText=this.value">
                    <span class="val-display" id="s1_speed_val">50</span>
                </div>
                <button onclick="sendCommand('S1')">Update Servo 1 Parameters</button>
                <canvas id="s1Canvas"></canvas>
            </div>

            <div class="control-group">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>Servo 2 (Channel 1)</strong>
                    <button id="s2_power_btn" class="btn-on" onclick="togglePower('S2')">Power: ON</button>
                </div>
                <div class="slider-row">
                    <label>Amplitude Range</label>
                    <input type="range" id="s2_range" min="10" max="90" value="45" oninput="document.getElementById('s2_range_val').innerText=this.value">
                    <span class="val-display" id="s2_range_val">45</span>
                </div>
                <div class="slider-row">
                    <label>Frequency Speed</label>
                    <input type="range" id="s2_speed" min="1" max="100" value="50" oninput="document.getElementById('s2_speed_val').innerText=this.value">
                    <span class="val-display" id="s2_speed_val">50</span>
                </div>
                <button onclick="sendCommand('S2')">Update Servo 2 Parameters</button>
                <canvas id="s2Canvas"></canvas>
            </div>
        </div>
    </div>

    <script>
        let servosEnabled = { 'S1': true, 'S2': true };
        let latestData = null;
        let animationTime = 0;

        // Fetch data at 10Hz
        setInterval(() => {
            fetch('/api/telemetry')
                .then(response => response.json())
                .then(data => { latestData = data; });
        }, 100);

        // Update readable text tables at 2Hz (so human eyes can read them)
        setInterval(() => {
            if(!latestData) return;
            
            // IMU 1 Table
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

            // IMU 2 Table
            document.getElementById('t2_ax').innerText = latestData.imu2.accX.toFixed(1);
            document.getElementById('t2_ay').innerText = latestData.imu2.accY.toFixed(1);
            document.getElementById('t2_az').innerText = latestData.imu2.accZ.toFixed(1);
            document.getElementById('t2_gx').innerText = latestData.imu2.gyrX.toFixed(1);
            document.getElementById('t2_gy').innerText = latestData.imu2.gyrY.toFixed(1);
            document.getElementById('t2_gz').innerText = latestData.imu2.gyrZ.toFixed(1);
            document.getElementById('t2_mx').innerText = latestData.imu2.magX.toFixed(1);
            document.getElementById('t2_my').innerText = latestData.imu2.magY.toFixed(1);
            document.getElementById('t2_mz').innerText = latestData.imu2.magZ.toFixed(1);
            document.getElementById('t2_temp').innerText = latestData.imu2.temp.toFixed(1);

            // Bend Text
            document.getElementById('b1_val').innerText = latestData.bend.sensor1;
            document.getElementById('b2_val').innerText = latestData.bend.sensor2;
        }, 500);

        // 60FPS Animation Loop for visual elements
        function animate() {
            animationTime += 0.05;

            if (latestData) {
                // Orient Cubes
                document.getElementById('cube1').style.transform = `translateZ(-50px) rotateX(${-latestData.imu1.pitch}deg) rotateY(${latestData.imu1.yaw}deg) rotateZ(${latestData.imu1.roll}deg)`;
                document.getElementById('cube2').style.transform = `translateZ(-50px) rotateX(${-latestData.imu2.pitch}deg) rotateY(${latestData.imu2.yaw}deg) rotateZ(${latestData.imu2.roll}deg)`;

                // Average Compass
                let avgMagX = (latestData.imu1.magX + latestData.imu2.magX) / 2;
                let avgMagY = (latestData.imu1.magY + latestData.imu2.magY) / 2;
                let magHeading = Math.atan2(avgMagY, avgMagX) * (180 / Math.PI);
                document.getElementById('mag_needle').style.transform = `rotate(${magHeading + 90}deg)`;

                // Bend Render
                drawBendAnimation(latestData.bend.sensor1, latestData.bend.sensor2);
            }

            // Sine Wave Renders
            let r1 = document.getElementById('s1_range').value;
            let sp1 = document.getElementById('s1_speed').value;
            drawSineWave('s1Canvas', r1, sp1, animationTime, servosEnabled['S1']);

            let r2 = document.getElementById('s2_range').value;
            let sp2 = document.getElementById('s2_speed').value;
            drawSineWave('s2Canvas', r2, sp2, animationTime, servosEnabled['S2']);

            requestAnimationFrame(animate);
        }
        
        function drawBendAnimation(b1, b2) {
            const cvs = document.getElementById('bendCanvas');
            // Standardize canvas resolution
            cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
            const ctx = cvs.getContext('2d');
            
            // Draw MATLAB-style grid
            ctx.strokeStyle = '#E0E0E0'; ctx.lineWidth = 1;
            for(let i=0; i<cvs.width; i+=20) { ctx.beginPath(); ctx.moveTo(i,0); ctx.lineTo(i,cvs.height); ctx.stroke(); }
            for(let i=0; i<cvs.height; i+=20) { ctx.beginPath(); ctx.moveTo(0,i); ctx.lineTo(cvs.width,i); ctx.stroke(); }

            let a1 = ((b1 - 512) / 512) * (Math.PI / 3); 
            let a2 = ((b2 - 512) / 512) * (Math.PI / 3); 

            let sX = cvs.width * 0.1; let sY = cvs.height / 2; let seg = cvs.width * 0.35;
            let x1 = sX + seg * Math.cos(a1); let y1 = sY + seg * Math.sin(a1);
            let x2 = x1 + seg * Math.cos(a1+a2); let y2 = y1 + seg * Math.sin(a1+a2);

            ctx.strokeStyle = '#0000FF'; ctx.lineWidth = 4; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
            ctx.beginPath(); ctx.moveTo(sX, sY); ctx.lineTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
            
            ctx.fillStyle = '#FF0000';
            ctx.beginPath(); ctx.arc(sX, sY, 5, 0, Math.PI*2); ctx.fill();
            ctx.beginPath(); ctx.arc(x1, y1, 5, 0, Math.PI*2); ctx.fill();
            ctx.beginPath(); ctx.arc(x2, y2, 5, 0, Math.PI*2); ctx.fill();
        }

        function drawSineWave(canvasId, range, speed, timeVar, isEnabled) {
            const cvs = document.getElementById(canvasId);
            cvs.width = cvs.clientWidth; cvs.height = cvs.clientHeight;
            const ctx = cvs.getContext('2d');

            // Draw grid
            ctx.strokeStyle = '#E0E0E0'; ctx.lineWidth = 1;
            for(let i=0; i<cvs.width; i+=20) { ctx.beginPath(); ctx.moveTo(i,0); ctx.lineTo(i,cvs.height); ctx.stroke(); }
            ctx.beginPath(); ctx.moveTo(0, cvs.height/2); ctx.lineTo(cvs.width, cvs.height/2); ctx.stroke();

            if (!isEnabled) return; // Draw flatline or nothing if off

            ctx.strokeStyle = '#0000FF'; ctx.lineWidth = 2;
            ctx.beginPath();
            
            // Map speed 1-100 to a reasonable frequency multiplier
            let freq = speed * 0.002; 
            // Map range 10-90 to pixels (max amplitude ~ 40px)
            let amp = (range / 90) * 40;

            for(let x = 0; x < cvs.width; x++) {
                let y = (cvs.height / 2) + amp * Math.sin((x * 0.05) - (timeVar * speed * 0.1));
                if (x === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
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

        function sendCommand(target) {
            let prefix = target.toLowerCase();
            let range = document.getElementById(`${prefix}_range`).value;
            let speed = document.getElementById(`${prefix}_speed`).value;
            fetch('/api/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'UPDATE_SWEEP', target: target, range: range, speed: speed })
            });
        }

        // Start animation loop
        requestAnimationFrame(animate);
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5051, debug=False)
