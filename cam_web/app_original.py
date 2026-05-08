from flask import Flask, send_from_directory, request, jsonify, abort
import os, subprocess, pathlib, time, threading, math
from smbus2 import SMBus
import struct

APP = Flask(__name__, static_folder=".", static_url_path="")
TOKEN = "super-secret-token"
REC_DIR = "/home/robot/recordings"

# I2C Configuration
I2C_BUS = 1
MUX_ADDR = 0x70
IMU_ADDR = 0x69
PCA9685_ADDR = 0x40
ADS1115_ADDR = 0x48
IMU_MUX_PORT = 0

# Servo configuration (from Arduino code)
SERVO_CHANNEL = 0
SERVOMIN = 170  #fine-tuning this still
SERVOMAX = 600
SERVO_FREQ = 50  # Hz

# Servo state
servo_running = False
servo_angle = 0
servo_direction = 1  # 1 = forward, -1 = backward

# Complementary filter state
comp_angle_x = 0.0  # Roll angle
comp_angle_y = 0.0  # Pitch angle
last_time = time.time()
ALPHA = 0.98  # Complementary filter coefficient (0.96-0.98 typical)

# Sensor data globals
sensor_data = {
    "i2c_devices": {},
    "imu": {
        "accel": [0, 0, 0], 
        "gyro": [0, 0, 0],
        "comp_angle_x": 0.0,  # Roll from complementary filter
        "comp_angle_y": 0.0,  # Pitch from complementary filter
        "accel_angle_x": 0.0,  # Roll from accel only
        "accel_angle_y": 0.0   # Pitch from accel only
    },
    "bend_sensor": {
        "angle": 0, 
        "voltage": 0,
        "voltage_a0": 0,
        "voltage_a1": 0
    }
}

# IMU initialization flag
imu_initialized = False
pca_initialized = False

def check_auth():
    if request.headers.get("X-API-Token") != TOKEN:
        abort(401)

def init_pca9685():
    """Initialize PCA9685 servo driver"""
    global pca_initialized
    try:
        bus = SMBus(I2C_BUS)
        
        # Reset
        bus.write_byte_data(PCA9685_ADDR, 0x00, 0x00)
        time.sleep(0.01)
        
        # Set PWM frequency to 50Hz
        prescale = int(25000000.0 / (4096 * SERVO_FREQ) - 1)
        
        oldmode = bus.read_byte_data(PCA9685_ADDR, 0x00)
        newmode = (oldmode & 0x7F) | 0x10  # Sleep
        bus.write_byte_data(PCA9685_ADDR, 0x00, newmode)
        bus.write_byte_data(PCA9685_ADDR, 0xFE, prescale)
        bus.write_byte_data(PCA9685_ADDR, 0x00, oldmode)
        time.sleep(0.02)
        bus.write_byte_data(PCA9685_ADDR, 0x00, oldmode | 0x80)
        
        bus.close()
        pca_initialized = True
        print("PCA9685 initialized successfully!")
        return True
    except Exception as e:
        print(f"PCA9685 init error: {e}")
        return False

def set_servo_pwm(angle):
    """Set servo to specific angle (0-180)"""
    try:
        bus = SMBus(I2C_BUS)
        
        # Map angle to PWM value
        pwm = int(SERVOMIN + (angle / 180.0) * (SERVOMAX - SERVOMIN))
        
        # Set PWM on channel
        bus.write_byte_data(PCA9685_ADDR, 0x06 + 4 * SERVO_CHANNEL, 0)
        bus.write_byte_data(PCA9685_ADDR, 0x07 + 4 * SERVO_CHANNEL, 0)
        bus.write_byte_data(PCA9685_ADDR, 0x08 + 4 * SERVO_CHANNEL, pwm & 0xFF)
        bus.write_byte_data(PCA9685_ADDR, 0x09 + 4 * SERVO_CHANNEL, pwm >> 8)
        
        bus.close()
    except Exception as e:
        print(f"Servo PWM error: {e}")

def servo_sweep_loop():
    """Background thread for servo sweep"""
    global servo_angle, servo_direction, servo_running
    
    while True:
        if servo_running:
            # Move servo
            set_servo_pwm(servo_angle)
            
            # Update angle
            servo_angle += servo_direction
            
            # Reverse at endpoints
            if servo_angle >= 180:
                servo_angle = 180
                servo_direction = -1
            elif servo_angle <= 0:
                servo_angle = 0
                servo_direction = 1
            
            time.sleep(0.005)  # 5ms delay between steps
        else:
            time.sleep(0.1)

def init_imu():
    """Initialize ICM-20948 IMU"""
    global imu_initialized, comp_angle_x, comp_angle_y, last_time
    try:
        bus = SMBus(I2C_BUS)
        bus.write_byte(MUX_ADDR, 0x01)  # Enable MUX port 0
        time.sleep(0.02)
        
        # Check WHO_AM_I
        who_am_i = bus.read_byte_data(IMU_ADDR, 0x00)
        if who_am_i != 0xEA:
            print(f"ICM-20948 WHO_AM_I failed: 0x{who_am_i:02X}")
            bus.write_byte(MUX_ADDR, 0x00)
            bus.close()
            return False
        
        # Don't reset - just wake up
        bus.write_byte_data(IMU_ADDR, 0x06, 0x01)  # PWR_MGMT_1, auto select clock
        time.sleep(0.01)
        
        # Enable accel and gyro
        bus.write_byte_data(IMU_ADDR, 0x07, 0x00)  # PWR_MGMT_2, enable all sensors
        time.sleep(0.01)
        
        bus.write_byte(MUX_ADDR, 0x00)  # Disable MUX
        bus.close()
        
        # Initialize complementary filter angles with first reading
        comp_angle_x = 0.0
        comp_angle_y = 0.0
        last_time = time.time()
        
        imu_initialized = True
        print("ICM-20948 initialized successfully!")
        return True
        
    except Exception as e:
        print(f"IMU init error: {e}")
        imu_initialized = False
        return False

def scan_i2c_devices():
    """Scan for I2C devices and update status"""
    devices = {}
    try:
        bus = SMBus(I2C_BUS)
        
        # Check main bus devices
        for addr in [PCA9685_ADDR, ADS1115_ADDR, MUX_ADDR]:
            try:
                bus.read_byte(addr)
                name = {
                    PCA9685_ADDR: "PCA9685 Servo Driver",
                    ADS1115_ADDR: "ADS1115 ADC",
                    MUX_ADDR: "TCA9548A MUX"
                }.get(addr, f"Device 0x{addr:02X}")
                devices[f"0x{addr:02X}"] = {"name": name, "status": "online"}
            except:
                name = {
                    PCA9685_ADDR: "PCA9685 Servo Driver",
                    ADS1115_ADDR: "ADS1115 ADC",
                    MUX_ADDR: "TCA9548A MUX"
                }.get(addr, f"Device 0x{addr:02X}")
                devices[f"0x{addr:02X}"] = {"name": name, "status": "offline"}
        
        # Check IMU behind MUX
        try:
            bus.write_byte(MUX_ADDR, 0x01)  # Enable MUX port 0
            time.sleep(0.01)
            bus.read_byte(IMU_ADDR)
            devices["0x69"] = {"name": "ICM-20948 IMU (via MUX)", "status": "online"}
        except:
            devices["0x69"] = {"name": "ICM-20948 IMU (via MUX)", "status": "offline"}
        finally:
            try:
                bus.write_byte(MUX_ADDR, 0x00)  # Disable MUX
            except:
                pass
        
        bus.close()
    except Exception as e:
        print(f"I2C scan error: {e}")
    
    sensor_data["i2c_devices"] = devices

def read_imu():
    """Read IMU data from ICM-20948 and calculate complementary filter angle"""
    global imu_initialized, comp_angle_x, comp_angle_y, last_time
    
    # Initialize on first read
    if not imu_initialized:
        init_imu()
        if not imu_initialized:
            return
    
    try:
        bus = SMBus(I2C_BUS)
        bus.write_byte(MUX_ADDR, 0x01)  # Enable MUX port 0
        time.sleep(0.005)
        
        # ICM-20948 register addresses (Bank 0)
        ACCEL_XOUT_H = 0x2D
        GYRO_XOUT_H = 0x33
        
        # Read accelerometer (6 bytes)
        accel_data = bus.read_i2c_block_data(IMU_ADDR, ACCEL_XOUT_H, 6)
        ax_raw = struct.unpack('>h', bytes(accel_data[0:2]))[0]
        ay_raw = struct.unpack('>h', bytes(accel_data[2:4]))[0]
        az_raw = struct.unpack('>h', bytes(accel_data[4:6]))[0]
        
        # Convert to g (±2g range, 16384 LSB/g)
        ax = ax_raw / 16384.0
        ay = ay_raw / 16384.0
        az = az_raw / 16384.0
        
        # Read gyroscope (6 bytes)
        gyro_data = bus.read_i2c_block_data(IMU_ADDR, GYRO_XOUT_H, 6)
        gx_raw = struct.unpack('>h', bytes(gyro_data[0:2]))[0]
        gy_raw = struct.unpack('>h', bytes(gyro_data[2:4]))[0]
        gz_raw = struct.unpack('>h', bytes(gyro_data[4:6]))[0]
        
        # Convert to °/s (±250 dps range, 131 LSB/°/s)
        gx = gx_raw / 131.0
        gy = gy_raw / 131.0
        gz = gz_raw / 131.0
        
        bus.write_byte(MUX_ADDR, 0x00)  # Disable MUX
        bus.close()
        
        # Calculate time delta
        current_time = time.time()
        dt = current_time - last_time
        last_time = current_time
        
        # Calculate angles from accelerometer (in degrees)
        # Roll (rotation around X-axis)
        accel_angle_x = math.atan2(ay, math.sqrt(ax*ax + az*az)) * 180.0 / math.pi
        # Pitch (rotation around Y-axis)  
        accel_angle_y = math.atan2(-ax, math.sqrt(ay*ay + az*az)) * 180.0 / math.pi
        
        # Complementary filter
        # Combine gyro (good short-term) with accel (good long-term)
        # angle = ALPHA * (angle + gyro * dt) + (1 - ALPHA) * accel_angle
        comp_angle_x = ALPHA * (comp_angle_x + gx * dt) + (1 - ALPHA) * accel_angle_x
        comp_angle_y = ALPHA * (comp_angle_y + gy * dt) + (1 - ALPHA) * accel_angle_y
        
        # Update sensor data
        sensor_data["imu"]["accel"] = [round(ax, 3), round(ay, 3), round(az, 3)]
        sensor_data["imu"]["gyro"] = [round(gx, 2), round(gy, 2), round(gz, 2)]
        sensor_data["imu"]["comp_angle_x"] = round(comp_angle_x, 2)
        sensor_data["imu"]["comp_angle_y"] = round(comp_angle_y, 2)
        sensor_data["imu"]["accel_angle_x"] = round(accel_angle_x, 2)
        sensor_data["imu"]["accel_angle_y"] = round(accel_angle_y, 2)
        
    except Exception as e:
        print(f"IMU read error: {e}")
        # Try to reinitialize on next read
        imu_initialized = False

def read_bend_sensor():
    """Read bidirectional bend sensor via ADS1115 using A0 and A1 differential"""
    try:
        bus = SMBus(I2C_BUS)
        
        # ADS1115 configuration
        CONFIG_REG = 0x01
        CONVERSION_REG = 0x00
        
        # Read A0 (single-ended)
        config_a0 = 0xC383  # MUX=100 (A0 vs GND), ±4.096V, 860 SPS
        bus.write_i2c_block_data(ADS1115_ADDR, CONFIG_REG, [(config_a0 >> 8) & 0xFF, config_a0 & 0xFF])
        time.sleep(0.01)
        data_a0 = bus.read_i2c_block_data(ADS1115_ADDR, CONVERSION_REG, 2)
        adc_a0 = struct.unpack('>h', bytes(data_a0))[0]
        voltage_a0 = (adc_a0 / 32768.0) * 4.096
        
        # Read A1 (single-ended)
        config_a1 = 0xD383  # MUX=101 (A1 vs GND), ±4.096V, 860 SPS
        bus.write_i2c_block_data(ADS1115_ADDR, CONFIG_REG, [(config_a1 >> 8) & 0xFF, config_a1 & 0xFF])
        time.sleep(0.01)
        data_a1 = bus.read_i2c_block_data(ADS1115_ADDR, CONVERSION_REG, 2)
        adc_a1 = struct.unpack('>h', bytes(data_a1))[0]
        voltage_a1 = (adc_a1 / 32768.0) * 4.096
        
        # Calculate differential voltage
        voltage_diff = voltage_a0 - voltage_a1
        
        # Map bidirectional bend:
        # Positive voltage_diff = bend in one direction (0° to 90°)
        # Negative voltage_diff = bend in opposite direction (0° to -90°)
        # Assuming ±1.65V range maps to ±90°
        
        max_voltage = 1.65  # Maximum expected differential voltage
        angle = (voltage_diff / max_voltage) * 90.0
        
        # Clamp to ±90°
        angle = max(-90.0, min(90.0, angle))
        
        sensor_data["bend_sensor"]["voltage"] = round(voltage_diff, 3)
        sensor_data["bend_sensor"]["voltage_a0"] = round(voltage_a0, 3)
        sensor_data["bend_sensor"]["voltage_a1"] = round(voltage_a1, 3)
        sensor_data["bend_sensor"]["angle"] = round(angle, 1)
        
        bus.close()
    except Exception as e:
        print(f"Bend sensor read error: {e}")

def sensor_loop():
    """Background thread to continuously read sensors - OPTIMIZED"""
    loop_count = 0
    while True:
        # Read IMU at 50 Hz (needed for complementary filter)
        read_imu()
        
        # Read other sensors at lower rate (10 Hz) - every 5th loop
        if loop_count % 5 == 0:
            scan_i2c_devices()
            read_bend_sensor()
        
        loop_count += 1
        time.sleep(0.02)  # 50 Hz base rate

@APP.get("/")
def root():
    return APP.send_static_file("index.html")

# Sensor data endpoint
@APP.get("/api/sensors")
def get_sensors():
    check_auth()
    return jsonify(sensor_data)

# Servo control endpoints
@APP.get("/api/servo/status")
def servo_status():
    check_auth()
    return jsonify({"running": servo_running, "angle": servo_angle})

@APP.post("/api/servo/start")
def servo_start():
    check_auth()
    global servo_running, pca_initialized
    
    if not pca_initialized:
        init_pca9685()
    
    servo_running = True
    return jsonify({"ok": True, "running": True})

@APP.post("/api/servo/stop")
def servo_stop():
    check_auth()
    global servo_running
    servo_running = False
    return jsonify({"ok": True, "running": False})

# Camera recording endpoints
@APP.get("/api/record/status")
def status():
    check_auth()
    p = subprocess.run(["systemctl","is-active","--quiet","cam-record"])
    return jsonify({"active": p.returncode==0})

@APP.post("/api/record/start")
def rec_start():
    check_auth()
    subprocess.run(["sudo","systemctl","start","cam-record"])
    return jsonify({"ok": True})

@APP.post("/api/record/stop")
def rec_stop():
    check_auth()
    subprocess.run(["sudo","systemctl","stop","cam-record"])
    return jsonify({"ok": True})

# File management endpoints
@APP.get("/api/files")
def list_files():
    check_auth()
    pathlib.Path(REC_DIR).mkdir(parents=True, exist_ok=True)
    items=[]
    for name in sorted(os.listdir(REC_DIR), reverse=True):
        p = os.path.join(REC_DIR,name)
        if os.isfile(p):
            st=os.stat(p)
            items.append({
                "name": name,
                "size_mb": round(st.st_size/1024/1024,2),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            })
    return jsonify({"files": items})

@APP.get("/api/files/download/<path:name>")
def dl(name):
    check_auth()
    return send_from_directory(REC_DIR, name, as_attachment=True)

@APP.post("/api/files/delete")
def delete():
    check_auth()
    name = request.json.get("name","")
    p = os.path.join(REC_DIR, name)
    if not p.startswith(REC_DIR): abort(400)
    if os.path.exists(p): os.remove(p)
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Initialize PCA9685
    init_pca9685()
    
    # Start sensor reading thread
    threading.Thread(target=sensor_loop, daemon=True).start()
    
    # Start servo sweep thread
    threading.Thread(target=servo_sweep_loop, daemon=True).start()
    
    APP.run(host="0.0.0.0", port=5050)
