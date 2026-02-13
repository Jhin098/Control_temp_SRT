import serial
import time
import threading
import sys
import serial.tools.list_ports

def find_serial_port():
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if "Arduino" in p.description or "CH340" in p.description:
            return p.device
    
    # If no specific name found, return the last one (often the newly plugged one)
    if len(ports) > 0:
        return ports[-1].device
    return None

def read_from_serial(ser):
    """Continuously read from serial port and print to console."""
    while ser.is_open:
        try:
            if ser.in_waiting:
                line = ser.readline().decode(errors='ignore').strip()
                if line:
                    # Filter out repetitive temperature data to see relay messages clearly
                    if "T=" not in line and "RAW=" not in line:
                        print(f"[Arduino] {line}")
            time.sleep(0.01)
        except:
            break

def test_relay_interactive():
    print("--- INTERACTIVE RELAY DIAGNOSTIC ---")
    port = find_serial_port()
    
    if not port:
        print("No serial port found!")
        input("Press Enter to exit...")
        return

    print(f"Connecting to {port}...")
    try:
        ser = serial.Serial(port, 115200, timeout=1)
        
        # Start reader thread to see Arduino responses
        t = threading.Thread(target=read_from_serial, args=(ser,), daemon=True)
        t.start()
        
        print("Waiting for Arduino reset (2s)...")
        time.sleep(2)  # Wait for Arduino auto-reset upon connection
        
        print("\nCOMMANDS:")
        print("  1 = Send 'r' (Set Pins LOW)  -> Turn ON (if relay is Active LOW)")
        print("  2 = Send 'h' (Set Pins HIGH) -> Turn OFF (if Active LOW) / ON (if Active HIGH)")
        print("  q = Quit")
        
        while True:
            cmd = input("\nEnter command (1/2/q): ").strip().lower()
            if cmd == '1':
                print(">> Sending 'r' (LOW)...")
                ser.write(b'r')
                ser.flush()
            elif cmd == '2':
                print(">> Sending 'h' (HIGH)...")
                ser.write(b'h')
                ser.flush()
            elif cmd == 'q':
                break
        
        ser.close()
        print("\nTest Complete.")
        
    except Exception as e:
        print(f"\nError: {e}")

    print("\n-------------------------")
    input("Press Enter to exit...")

if __name__ == "__main__":
    test_relay_interactive()
