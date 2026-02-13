#include <Arduino.h>

// =======================================
// RELAY DIAGNOSTIC BLINK TEST
// Alternates Pin 4 between HIGH and LOW
// every 3 seconds. Watch which state
// makes the relay click.
// =======================================

// CHANGE THIS IF YOUR WIRE IS ON A DIFFERENT PIN!
static const uint8_t TEST_PIN = 4;

void setup() {
  Serial.begin(115200);
  pinMode(TEST_PIN, OUTPUT);
  digitalWrite(TEST_PIN, HIGH); // Start HIGH
  
  Serial.println("=== RELAY DIAGNOSTIC ===");
  Serial.println("Pin 4 will toggle HIGH/LOW every 3 seconds.");
  Serial.println("Watch the relay and tell me which state clicks.");
  Serial.println("========================");
}

void loop() {
  // HIGH for 3 seconds
  digitalWrite(TEST_PIN, HIGH);
  Serial.println(">>> PIN 4 = HIGH <<<");
  delay(3000);
  
  // LOW for 3 seconds
  digitalWrite(TEST_PIN, LOW);
  Serial.println(">>> PIN 4 = LOW <<<");
  delay(3000);
}
