import board
import busio
import adafruit_tca9548a

print("--- I2C HARDWARE SCANNER ---")
try:
    i2c = busio.I2C(board.SCL, board.SDA)
except Exception as e:
    print(f"Failed to initialize I2C bus: {e}")
    exit()

# 1. Scan the main I2C bus
while not i2c.try_lock():
    pass
main_addresses = i2c.scan()
i2c.unlock()

print(f"\nMain Bus Devices: {[hex(addr) for addr in main_addresses]}")
if 0x70 in main_addresses:
    print("  -> TCA9548A Multiplexer found!")
if 0x48 in main_addresses:
    print("  -> ADS1115 ADC found!")
if 0x40 in main_addresses:
    print("  -> PCA9685 Servo Driver found!")

# 2. Scan behind the multiplexer
if 0x70 in main_addresses:
    print("\nScanning Multiplexer Channels...")
    try:
        tca = adafruit_tca9548a.TCA9548A(i2c)
        for channel in range(8):
            if tca[channel].try_lock():
                chan_addresses = tca[channel].scan()
                tca[channel].unlock()
                
                if chan_addresses:
                    print(f"  Channel {channel}: {[hex(addr) for addr in chan_addresses]}")
                    if 0x69 in chan_addresses or 0x68 in chan_addresses:
                        print(f"    -> IMU found on Channel {channel}!")
                else:
                    print(f"  Channel {channel}: No devices")
    except Exception as e:
        print(f"Error scanning multiplexer: {e}")
else:
    print("\nMultiplexer not found on main bus. Cannot scan channels.")
    
print("\nScan Complete.")
