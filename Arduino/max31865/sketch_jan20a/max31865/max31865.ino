#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_MAX31865.h>

// -------- Hardware pins (UNO hardware SPI)
// SCK = D13, MISO = D12, MOSI = D11, CS below
static const uint8_t PIN_CS = 10;

// -------- Relay control pins
static const uint8_t RELAY_PIN_1 = 4;  // IN1
static const uint8_t RELAY_PIN_2 = 5;  // IN2

// -------- Buzzer pin
static const uint8_t BUZZER_PIN = 9;

// -------- Buzzer timing
static const uint32_t BUZZER_DURATION_MS = 30000; // 30 seconds

// ==============================
// RELAY: JUMPER H (Active HIGH)
// HIGH = ON  (Relay Energized)
// LOW  = OFF (Relay De-energized)
// Wiring: NC terminal -> Machine RUN when OFF
// ==============================

// -------- RTD config
static const float RNOMINAL = 100.0f;
static const float RREF     = 430.0f;
#define WIRE_MODE MAX31865_2WIRE
static const float LEAD_OHMS = 0.0f;

// -------- Timing / limits
static const uint16_t READ_PERIOD_MS = 200;
static const float MIN_C = -200.0f;
static const float MAX_C = 850.0f;
static const float MAX_JUMP_C = 50.0f;
static const uint8_t DEBUG_RAW_EVERY = 10;

Adafruit_MAX31865 rtd(PIN_CS);

// Helper functions
static float rawToOhms(uint16_t raw15, float rref) {
  return (static_cast<float>(raw15) * rref) / 32768.0f;
}

static bool calcTempCFromOhms(float ohms, float r0, float &outT) {
  const float A = 3.9083e-3f;
  const float B = -5.775e-7f;
  float ratio = ohms / r0;
  float c = ratio - 1.0f;
  float disc = A * A - 4.0f * B * c;
  if (disc < 0.0f) return false;
  outT = (-A + sqrt(disc)) / (2.0f * B);
  return true;
}

static void printFault(uint8_t f) {
  if (f & MAX31865_FAULT_HIGHTHRESH) Serial.println("FAULT: RTD High Threshold");
  if (f & MAX31865_FAULT_LOWTHRESH)  Serial.println("FAULT: RTD Low Threshold");
  if (f & MAX31865_FAULT_REFINLOW)   Serial.println("FAULT: REFIN- < 0.85*Bias");
  if (f & MAX31865_FAULT_REFINHIGH)  Serial.println("FAULT: REFIN- > 0.85*Bias");
  if (f & MAX31865_FAULT_RTDINLOW)   Serial.println("FAULT: RTDIN- < 0.85*Bias");
  if (f & MAX31865_FAULT_OVUV)       Serial.println("FAULT: Over/Under voltage");
}

// Relay control (Jumper H: HIGH=ON, LOW=OFF)
void relayON() {
  digitalWrite(RELAY_PIN_1, HIGH);
  digitalWrite(RELAY_PIN_2, HIGH);
}

void relayOFF() {
  digitalWrite(RELAY_PIN_1, LOW);
  digitalWrite(RELAY_PIN_2, LOW);
}

void setup() {
  // Relay OFF first (Machine RUN via NC)
  pinMode(RELAY_PIN_1, OUTPUT);
  pinMode(RELAY_PIN_2, OUTPUT);
  relayOFF();

  // Buzzer OFF
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);

  delay(100);
  Serial.begin(115200);
  delay(300);

  Serial.println("READY");
  Serial.println("FORMAT: T=xx.xx");
  Serial.println("RELAY PIN 4/5 (Jumper H): HIGH=ON, LOW=OFF");

  if (!rtd.begin(WIRE_MODE)) {
    Serial.println("ERR: MAX31865 begin() failed");
    while (1) delay(10);
  }
}

void loop() {
  static uint32_t lastRead = 0;
  static uint32_t count = 0;
  static float last_good = NAN;
  static uint32_t buzzer_start_time = 0;
  static bool buzzer_active = false;
  static bool emg_active = false;
  static uint32_t emg_start = 0;
  static bool latched = false;

  // ---- Emergency sequence: buzzer 10s → relay ON (stop machine) ----
  if (emg_active) {
    if (millis() - emg_start >= 10000) {
      relayON();          // Stop Machine (HIGH = ON)
      delay(200);
      digitalWrite(BUZZER_PIN, HIGH); // Buzzer OFF
      emg_active = false;
      latched = true;
      Serial.println("EMG_SEQ: MACHINE STOPPED (Relay ON/HIGH)");
    }
  }

  // Reinforce latch
  if (latched) {
    relayON();
  }

  // Handle buzzer auto-off (standalone 'b' command)
  if (buzzer_active && !emg_active && buzzer_start_time > 0 &&
      (millis() - buzzer_start_time >= BUZZER_DURATION_MS)) {
    digitalWrite(BUZZER_PIN, HIGH);
    buzzer_active = false;
    Serial.println("BUZZER: AUTO-OFF (30s)");
  }

  // ---- COMMANDS ----
  if (Serial.available()) {
    char cmd = Serial.read();

    // 'R' = Emergency Sequence (Python sends this)
    if (cmd == 'R') {
      if (!emg_active && !latched) {
        emg_active = true;
        emg_start = millis();
        buzzer_active = true;
        buzzer_start_time = millis();
        digitalWrite(BUZZER_PIN, LOW); // Buzzer ON
        relayOFF(); // Keep machine running during 10s countdown
        Serial.println("EMG_SEQ: STARTED (Buzzer 10s -> Relay ON)");
      }
    }
    // 'H' or 'h' = FULL RESET (Python sends uppercase 'H')
    else if (cmd == 'H' || cmd == 'h') {
      emg_active = false;
      latched = false;
      buzzer_active = false;
      relayOFF();                       // Machine RUN (LOW = OFF)
      digitalWrite(BUZZER_PIN, HIGH);   // Buzzer OFF
      Serial.println("RESET: Relay OFF (LOW), Buzzer OFF");
    }
    // 'r' = Force relay ON (test)
    else if (cmd == 'r') {
      relayON();
      Serial.println("TEST: Relay ON (HIGH)");
    }
    // Individual relay controls
    else if (cmd == 'c') {
      digitalWrite(RELAY_PIN_1, HIGH);
      Serial.println("R1: ON (HIGH)");
    }
    else if (cmd == 'd') {
      digitalWrite(RELAY_PIN_1, LOW);
      Serial.println("R1: OFF (LOW)");
    }
    else if (cmd == 'e') {
      digitalWrite(RELAY_PIN_2, HIGH);
      Serial.println("R2: ON (HIGH)");
    }
    else if (cmd == 'f') {
      digitalWrite(RELAY_PIN_2, LOW);
      Serial.println("R2: OFF (LOW)");
    }
    // Buzzer commands
    else if (cmd == 'b') {
      digitalWrite(BUZZER_PIN, LOW);
      buzzer_start_time = millis();
      buzzer_active = true;
      Serial.println("BUZZER: ON (30s)");
    }
    else if (cmd == 's') {
      digitalWrite(BUZZER_PIN, HIGH);
      buzzer_active = false;
      Serial.println("BUZZER: STOP");
    }
  }

  // ---- READ TEMPERATURE ----
  const uint32_t now = millis();
  if (now - lastRead < READ_PERIOD_MS) return;
  lastRead = now;
  count++;

  const uint8_t fault = rtd.readFault();
  if (fault) {
    Serial.print("T=FAULT:0x");
    Serial.println(fault, HEX);
    printFault(fault);
    rtd.clearFault();
    return;
  }

  const uint16_t raw = rtd.readRTD();
  const uint16_t raw15 = raw >> 1;
  float ohms = rawToOhms(raw15, RREF);
  if (LEAD_OHMS > 0.0f) ohms -= LEAD_OHMS;

  float t = rtd.temperature(RNOMINAL, RREF);
  float t_cvd = NAN;
  if (ohms >= RNOMINAL && calcTempCFromOhms(ohms, RNOMINAL, t_cvd)) {
    t = t_cvd;
  }

  if (isnan(t)) {
    Serial.println("T=BAD");
    return;
  }
  if (t < MIN_C || t > MAX_C) {
    Serial.print("T=OUT_OF_RANGE ");
    Serial.println(t, 2);
  }

  if (!isnan(last_good) && fabs(t - last_good) > MAX_JUMP_C) {
    Serial.println("T=SPIKE");
    return;
  }
  last_good = t;

  Serial.print("T=");
  Serial.println(t, 2);

  if (count % DEBUG_RAW_EVERY == 0) {
    Serial.print("RAW=0x"); Serial.print(raw, HEX);
    Serial.print(" RAW15="); Serial.print(raw15);
    Serial.print(" OHMS="); Serial.println(ohms, 3);
  }
}
