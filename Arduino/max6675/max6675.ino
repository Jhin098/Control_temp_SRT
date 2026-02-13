#include <Arduino.h>
#include <math.h>

// ----- MAX6675 PIN (UNO) -----
static const uint8_t PIN_SCK = 13;
static const uint8_t PIN_CS  = 10;
static const uint8_t PIN_SO  = 12;

// ----- CONFIG -----
static const uint16_t UPDATE_MS = 250;     // อย่าอ่านถี่กว่า ~220ms
static const float    MIN_VALID_C = 0.0f;
static const float    MAX_VALID_C = 520.0f;
static const float    CAL_OFFSET_C = 0.0f; // ปรับ offset เล็กน้อยถ้าต้องการเทียบเครื่องมืออ้างอิง

// กรองกันหลุด/EMI
static const float    MAX_JUMP_C = 10.0f;  // กระโดดเกินนี้ทิ้ง
static const uint8_t  NEED_STABLE_N = 3;   // ต้องนิ่งกี่รอบก่อนยอมรับค่าแรก
static const uint8_t  DEBUG_RAW_EVERY = 10; // พิมพ์ raw ทุกๆ กี่รอบ

static float last_good = NAN;
static uint8_t stable_count = 0;
static uint32_t loop_count = 0;

static uint16_t max6675_read16() {
  digitalWrite(PIN_CS, LOW);
  delayMicroseconds(2);

  uint16_t v = 0;
  for (int i = 15; i >= 0; --i) {
    digitalWrite(PIN_SCK, LOW);
    delayMicroseconds(1);
    if (digitalRead(PIN_SO)) v |= (1u << i);
    digitalWrite(PIN_SCK, HIGH);
    delayMicroseconds(1);
  }

  digitalWrite(PIN_CS, HIGH);
  return v;
}

static bool read_temp_c(float &outC, uint16_t &raw16) {
  raw16 = max6675_read16();

  // D2 = 1 => Thermocouple OPEN
  if (raw16 & 0x0004) return false;

  // bits [14:3] => temp * 0.25C
  uint16_t t12 = (raw16 >> 3) & 0x0FFF;
  float c = (float)t12 * 0.25f;

  // กันค่าศูนย์หลอก: ถ้าอ่านได้ 0.00 ตลอดในอุณหภูมิห้อง ให้ถือว่าน่าสงสัย
  // (ห้องจริงควร ~25-40C ไม่ควร 0)
  if (c == 0.0f) {
    return false;
  }

  if (c < MIN_VALID_C || c > MAX_VALID_C) return false;
  outC = c + CAL_OFFSET_C;
  return true;
}

void setup() {
  pinMode(PIN_CS, OUTPUT);
  pinMode(PIN_SCK, OUTPUT);
  pinMode(PIN_SO, INPUT);

  digitalWrite(PIN_CS, HIGH);
  digitalWrite(PIN_SCK, HIGH);

  Serial.begin(115200);
  delay(800);
  Serial.println("READY");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < UPDATE_MS) return;
  last = millis();

  float t;
  uint16_t raw16 = 0;
  bool ok = read_temp_c(t, raw16);

  loop_count++;

  // debug raw เป็นช่วง ๆ (ช่วยฟันธงว่าขา/สายผิด)
  if (loop_count % DEBUG_RAW_EVERY == 0) {
    Serial.print("RAW=0x");
    if (raw16 < 0x1000) Serial.print("0");
    if (raw16 < 0x0100) Serial.print("0");
    if (raw16 < 0x0010) Serial.print("0");
    Serial.println(raw16, HEX);
  }

  if (!ok) {
    Serial.println("T=OPEN");   // ตอนนี้ใช้ OPEN เป็นสถานะรวม (สาย/ขา/0หลอก)
    stable_count = 0;
    return;
  }

  if (!isnan(last_good) && fabs(t - last_good) > MAX_JUMP_C) {
    Serial.println("T=SPIKE");
    stable_count = 0;
    return;
  }

  if (isnan(last_good)) {
    stable_count++;
    if (stable_count < NEED_STABLE_N) {
      Serial.println("T=WARMUP");
      return;
    }
  }

  last_good = t;

  Serial.print("T=");
  Serial.println(t, 2);
}
