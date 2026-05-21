#!/usr/bin/env python3
"""
PCA9685 Diagnostic & Servo Sweep Test
Run directly on the Pi: python3 test_pca9685.py
"""

import time
import sys

# ── 1. I2C BUS SCAN ──────────────────────────────────────────────────────────
print("=" * 50)
print("STEP 1: I2C Bus Scan")
print("=" * 50)

try:
    import board
    import busio

    i2c = busio.I2C(board.SCL, board.SDA)

    # Wait for bus lock
    while not i2c.try_lock():
        pass

    addresses = i2c.scan()
    i2c.unlock()

    if addresses:
        print(f"  Devices found at addresses: {[hex(a) for a in addresses]}")
        if 0x40 in addresses:
            print("  ✓ PCA9685 detected at 0x40 (default)")
        else:
            print("  ✗ 0x40 not found — check wiring or address jumpers")
            for alt in [0x41, 0x42, 0x43, 0x44, 0x70]:
                if alt in addresses:
                    print(f"  → Found device at {hex(alt)} — may be PCA9685 with soldered jumper")
        if 0x42 in addresses or 0x43 in addresses:
            print("  ✓ INA219 (UPS) also present")
    else:
        print("  ✗ No I2C devices found. Check:")
        print("     - SDA → GPIO2 (Pin 3)")
        print("     - SCL → GPIO3 (Pin 5)")
        print("     - PCA9685 VCC → 3.3V, GND → GND")
        print("     - sudo raspi-config → Interface Options → I2C → Enable")
        sys.exit(1)

except Exception as e:
    print(f"  ✗ I2C init failed: {e}")
    sys.exit(1)

# ── 2. PCA9685 INIT ──────────────────────────────────────────────────────────
print()
print("=" * 50)
print("STEP 2: PCA9685 Init")
print("=" * 50)

try:
    import adafruit_pca9685
    pca = adafruit_pca9685.PCA9685(i2c)
    pca.frequency = 50
    print(f"  ✓ PCA9685 online, PWM frequency set to 50 Hz")
except Exception as e:
    print(f"  ✗ PCA9685 init failed: {e}")
    sys.exit(1)

# ── 3. SERVO INIT ─────────────────────────────────────────────────────────────
print()
print("=" * 50)
print("STEP 3: Servo Init (Channels 0, 1 & 2)")
print("=" * 50)

from adafruit_motor import servo

servos = {}
for ch in [0, 1, 2]:
    try:
        servos[ch] = servo.Servo(pca.channels[ch], min_pulse=750, max_pulse=2250)
        print(f"  ✓ Servo on Channel {ch} ready")
    except Exception as e:
        print(f"  ✗ Channel {ch} servo failed: {e}")
        servos[ch] = None

if not any(servos.values()):
    print("  ✗ No servos available. Exiting.")
    sys.exit(1)

# ── 4. SERVO TESTS ────────────────────────────────────────────────────────────
def move(ch, angle):
    srv = servos[ch]
    if srv is None:
        return
    try:
        srv.angle = angle
        print(f"    Ch{ch} → {angle}°  ✓")
    except Exception as e:
        print(f"    Ch{ch} → {angle}°  ✗  ({e})")

def move_all(angle):
    for ch in [0, 1, 2]:
        move(ch, angle)

print()
print("=" * 50)
print("STEP 4: Center Test (all servos → 90°)")
print("=" * 50)
move_all(90)
time.sleep(1.5)

print()
print("=" * 50)
print("STEP 5: Endpoint Test  (0° → 90° → 180°)")
print("=" * 50)
for angle in [0, 90, 180]:
    move_all(angle)
    time.sleep(1.0)

print()
print("=" * 50)
print("STEP 6: Slow Sweep — 0° to 180° and back")
print("  Watch for binding or missed steps")
print("=" * 50)
for angle in list(range(0, 181, 5)) + list(range(180, -1, -5)):
    move_all(angle)
    time.sleep(0.04)

print()
print("=" * 50)
print("STEP 7: Return to center & release")
print("=" * 50)
move_all(90)
time.sleep(1.0)

# Disable PWM output (de-energize)
for ch in [0, 1, 2]:
    pca.channels[ch].duty_cycle = 0
print("  PWM disabled — servos de-energized")

print()
print("All tests complete.")