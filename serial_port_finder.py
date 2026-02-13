import serial.tools.list_ports

# VID ที่พบบ่อยของ Arduino / USB-Serial
PREFERRED_VIDS = {
    0x2341,  # Arduino
    0x2A03,  # Arduino
    0x1A86,  # CH340
    0x10C4,  # CP210x
    0x0403,  # FTDI
}

def list_serial_ports():
    return list(serial.tools.list_ports.comports())

def find_serial_port():
    ports = list_serial_ports()
    if not ports:
        return None

    # ตัด Bluetooth / Virtual port ออก
    ports = [
        p for p in ports
        if "bluetooth" not in (p.description or "").lower()
        and "bluetooth" not in (p.manufacturer or "").lower()
    ]

    if not ports:
        return None

    # 1) เลือกจาก VID ก่อน (แม่นสุด)
    for p in ports:
        if getattr(p, "vid", None) in PREFERRED_VIDS:
            return p.device

    # 2) fallback: พอร์ตแรกที่ไม่ใช่ Bluetooth
    return ports[0].device


if __name__ == "__main__":
    print("---- Available serial ports ----")
    for p in list_serial_ports():
        print(p.device, p.description, p.manufacturer, p.vid)
    print("Selected:", find_serial_port())
