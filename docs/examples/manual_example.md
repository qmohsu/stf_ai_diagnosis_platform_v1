---
source_pdf: MWS150A_Service_Manual.pdf
vehicle_model: MWS-150-A
language: zh-CN
translated: true
exported_at: "2026-03-30T12:00:00Z"
page_count: 415
section_count: 5
---

# MWS-150-A Service Manual

<!-- NOTE: Truncated example showing selected chapters only.
     Chapters 2, 5-7 are omitted for brevity. -->

<!-- page:1 -->

This manual covers maintenance and repair procedures for the
Yamaha MWS-150-A (TRICITY 155) scooter. Model year 2020-2024.

## Chapter 1: General Information

<!-- page:5 -->

### 1.1 Specifications

| Specification | Value | Unit |
|---------------|-------|------|
| Engine type | Liquid-cooled 4-stroke SOHC | - |
| Displacement | 155 | cc |
| Bore x Stroke | 58.0 x 58.7 | mm |
| Compression ratio | 11.6:1 | - |
| Idle speed | 1300 +/- 100 | rpm |
| Spark plug type | CPR8EA-9 (NGK) | - |
| Spark plug gap | 0.8-0.9 | mm |

### 1.2 Torque Specifications

| Part | Torque | Unit |
|------|--------|------|
| Cylinder head bolt | 32 | N-m |
| Spark plug | 12.5 | N-m |
| Drain bolt | 20 | N-m |
| Oil filter | 12 | N-m |

## Chapter 3: Fuel System

<!-- page:42 -->

### 3.1 Fuel System Overview

The fuel system consists of the fuel tank, fuel pump, fuel filter,
throttle body, and fuel injector. The ECU controls fuel injection
timing and duration based on sensor inputs.

> **WARNING:** Always relieve fuel system pressure before
> disconnecting any fuel line.

### 3.2 Fuel System Troubleshooting

<!-- page:45 -->

![Fuel injector exploded diagram](images/MWS150A_Service_Manual/p045-1.png)

*Vision description: Exploded view of the fuel injector assembly
showing nozzle (A), O-ring seal (B), pintle valve (C), and solenoid
coil (D). Torque spec callout: 12 N-m for retaining bolt.*

#### DTC: P0171 — System Too Lean (Bank 1)

This code indicates that the fuel system is running lean or a
vacuum leak exists.

**Possible Causes:**
1. Vacuum leaks in intake manifold
2. Faulty Mass Air Flow (MAF) sensor
3. Clogged or leaking fuel injectors
4. Weak fuel pump

**Diagnostic Steps:**
1. Check intake manifold for vacuum leaks using smoke test.
2. Inspect MAF sensor readings at idle (expected: 2.5-4.5 g/s).
3. Perform fuel injector balance test.
4. Measure fuel pressure at rail (expected: 294 kPa).

See [Fuel System Overview](#3-1-fuel-system-overview) for system
diagram.

#### DTC: P0174 — System Too Lean (Bank 2)

Same diagnostic procedure as P0171. Check for bank-specific
vacuum leaks on the secondary intake runner.

## Chapter 4: Ignition System

<!-- page:68 -->

### 4.1 Ignition System Overview

The transistorised coil ignition (TCI) system uses a single
ignition coil controlled by the ECU. Ignition timing is
calculated from crankshaft position sensor and throttle position
sensor inputs.

### 4.2 Ignition Troubleshooting

<!-- page:72 -->

#### DTC: P0300 — Random/Multiple Cylinder Misfire

Indicates misfires detected across multiple cycles.

**Possible Causes:**
- Worn spark plugs (check gap: 0.8-0.9 mm)
- Failed ignition coil (measure primary resistance: 0.2-0.8 ohm)
- Vacuum leaks
- Low fuel pressure

## Chapter 8: Electrical System

<!-- page:180 -->

### 8.1 Wiring Diagrams

![Main wiring harness](images/MWS150A_Service_Manual/p180-1.png)

*Vision description: Complete wiring diagram showing main harness
routing from ECU (connector A, 32-pin) to ignition coil, fuel
injector, sensors (TPS, CKP, ECT, IAT), and instrument cluster.
Colour coding: red = battery, black = ground, green = signal.*

### 8.2 ECU Connector Pinout

| Pin | Wire Colour | Signal | Description |
|-----|-------------|--------|-------------|
| A1 | Red/White | B+ | Battery positive |
| A2 | Black | GND | Chassis ground |
| A5 | Green | TPS | Throttle position sensor |
| A8 | Blue/Yellow | CKP | Crankshaft position |
| A12 | Yellow/Red | INJ | Fuel injector drive |

## Appendix: DTC Index

| DTC | Description | Section |
|-----|-------------|---------|
| P0171 | System Too Lean (Bank 1) | [3.2 Fuel System Troubleshooting](#3-2-fuel-system-troubleshooting) |
| P0174 | System Too Lean (Bank 2) | [3.2 Fuel System Troubleshooting](#3-2-fuel-system-troubleshooting) |
| P0300 | Random/Multiple Cylinder Misfire | [4.2 Ignition Troubleshooting](#4-2-ignition-troubleshooting) |
