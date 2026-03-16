# OBD Pre-Processing Threshold Rationale

> **Document owner:** Li-Ta Hsu
> **Created:** 2026-03-16
> **Tracks:** GitHub Issue #6 / APP-32

This document traces every hard-coded threshold, algorithm choice, and
heuristic in the OBD log pre-processing pipeline back to its source —
whether that is an OBD-II standard, a vehicle service manual, academic
literature, empirical tuning, or domain-expert knowledge.

**Source-type legend**

| Tag | Category | Meaning |
|-----|----------|---------|
| [STD] | **Standard** | SAE J1979, ISO 15031-6, or equivalent industry spec |
| [MAN] | **Service Manual** | Vehicle manufacturer documentation *(reserved — no values currently sourced from service manuals)* |
| [LIT] | **Literature** | Peer-reviewed paper, textbook, or library documentation |
| [EMP] | **Empirical** | Tuned on project OBD logs — needs validation on broader fleet |
| [EXP] | **Expert** | Domain-expert knowledge from automotive diagnostic practice |

---

## A. PID Operating Ranges

**File:** `obd_agent/log_summarizer.py`
(`_OPERATING_RANGES` dict)

These ranges gate the out-of-range anomaly check in `_detect_anomalies()`.
A reading outside the range is flagged as anomalous.

### RPM: 0 – 8 000 rpm

- **Source:** [STD] SAE J1979 / ISO 15031-5, PID 0x0C.
  The standard encodes RPM as a 16-bit value with resolution 0.25 rpm,
  giving a theoretical maximum of 16 383.75 rpm.  The 8 000 rpm upper
  bound is a practical limit for passenger-vehicle and light-motorcycle
  engines.
- **Rationale:** Most naturally-aspirated gasoline engines rev-limit
  between 6 000 and 8 000 rpm.  Values above 8 000 almost always
  indicate a sensor glitch or an exotic powertrain not in the project's
  target fleet.
- **Applicability:** Passenger cars and scooters / small-displacement
  motorcycles.  High-revving sport-bike engines (e.g. 600 cc inline-4)
  can exceed 14 000 rpm — widen for those fleets.

### COOLANT_TEMP: −40 – 110 °C

- **Source:** [STD] SAE J1979 PID 0x05.  Encoding: single byte,
  offset −40, range −40 °C to 215 °C.  The 110 °C upper bound is an
  [EXP] expert-derived operating limit, not the sensor limit.
- **Rationale:** Normal thermostat-regulated operating temperature for a
  pressurised cooling system is 85 – 100 °C.  Sustained readings above
  105 – 110 °C indicate cooling-system failure; 110 °C provides early
  warning before the ~120 °C zone where head-gasket and warpage damage
  typically begins.
- **Applicability:** Passenger vehicles with pressurised liquid cooling.
  Air-cooled or heavy-diesel engines may need different limits.

### SHORT_FUEL_TRIM_1 / LONG_FUEL_TRIM_1: −25 – 25 %

- **Source:** [STD] SAE J1979 PIDs 0x06 / 0x07.  Encoding:
  single byte, range −100 % to +99.2 %, but the ECU normally operates
  within a much tighter band.  [EXP] The ±25 % window is a
  widely-used diagnostic convention: trims outside ±25 % almost
  universally trigger a fuel-system DTC (P0170 – P0175).
- **Rationale:** ±10 % is considered healthy; ±25 % is the outer
  boundary before the ECU itself flags a fault.  Using ±25 % avoids
  false positives from cold-start enrichment or altitude compensation
  while still catching genuine fuelling faults.
- **Applicability:** Universal for closed-loop gasoline engines.
  Diesel engines do not report this PID.

### INTAKE_PRESSURE: 0 – 255 kPa

- **Source:** [STD] SAE J1979 PID 0x0B.  Single-byte encoding
  with 1 kPa resolution gives the full 0 – 255 kPa range.
- **Rationale:** This is the sensor's maximum representable value.
  Naturally-aspirated engines at wide-open throttle read close to
  atmospheric (~101 kPa); forced-induction engines can exceed 200 kPa.
  The full byte range is kept to avoid false positives on turbocharged
  vehicles.
- **Applicability:** Universal OBD-II.

### SPEED: 0 – 250 km/h

- **Source:** [STD] SAE J1979 PID 0x0D.  Single-byte encoding,
  range 0 – 255 km/h.  [EXP] 250 km/h is a practical cap for
  passenger vehicles and motorcycles in the project's target fleet.
- **Rationale:** Most passenger vehicles are electronically limited to
  180 – 250 km/h.  Readings above 250 km/h on the target fleet
  (scooters, sedans) are almost certainly erroneous.
- **Applicability:** Adjust upward for high-performance or unrestricted
  vehicles (e.g. German Autobahn fleet).

### THROTTLE_POS: 0 – 100 %

- **Source:** [STD] SAE J1979 PID 0x11.  Defined as 0 – 100 %
  by the standard.
- **Rationale:** Physical constraint — the throttle plate cannot open
  beyond fully open.
- **Applicability:** Universal OBD-II.

### ENGINE_LOAD: 0 – 100 %

- **Source:** [STD] SAE J1979 PID 0x04 (Calculated Engine Load).
  Defined as 0 – 100 % by the standard.
- **Rationale:** Physical constraint — calculated load is normalised
  to full capacity.
- **Applicability:** Universal OBD-II.

---

## B. Anomaly Detection Parameters

**File:** `obd_agent/anomaly_detector.py`

### RPM Off Threshold: < 50 rpm (`_RPM_OFF`)

- **Source:** [EXP] Automotive diagnostic convention.
- **Rationale:** A running engine at idle typically produces 600 – 900
  rpm.  During cranking, RPM briefly reads 150 – 300 rpm.  Below 50 rpm
  the signal is either electrical noise from the CKP sensor or the
  engine is genuinely off.  The 50 rpm threshold sits well below the
  lowest possible cranking speed, eliminating false "off" classifications
  during key-on cranking.
- **Applicability:** Universal for 4-stroke engines.

### Speed Moving Threshold: >= 5 km/h (`_SPEED_MOVING`)

- **Source:** [EMP] Empirical — chosen to reject GPS / VSS noise at
  standstill.
- **Rationale:** OBD-II vehicle-speed sensors can report 1 – 3 km/h
  when stationary due to sensor quantisation and tyre-circumference
  rounding.  5 km/h provides a margin above this noise floor while
  still capturing low-speed manoeuvring.
- **Applicability:** Passenger vehicles and scooters.  Heavy vehicles
  with larger tyres may have a different noise floor.

### Throttle Cruise Std: <= 3.0 % (`_THROTTLE_CRUISE_STD`)

- **Source:** [EMP] Empirical — derived from project OBD logs during
  steady-state highway driving.
- **Rationale:** During cruise (constant speed on flat road), throttle
  position fluctuates by roughly 1 – 2 % around a setpoint.  A standard
  deviation <= 3 % captures this band with margin.  Values above 3 %
  indicate active acceleration / deceleration rather than steady cruise.
- **Applicability:** Primarily passenger vehicles.  Vehicles with
  electronic cruise control will show even lower variance.

### Severity Tier Thresholds: 0.33 / 0.66 (`_SEVERITY_THRESHOLDS`)

- **Source:** [EMP] Empirical — equal-width tercile split of the
  [0, 1] composite score.
- **Rationale:** Divides the normalised composite score into three
  equal bands: low [0, 0.33), medium [0.33, 0.66), high [0.66, 1.0].
  This is the simplest defensible partition when no calibrated
  ground-truth severity labels are available.  The boundaries should be
  re-tuned once labelled incident data is collected.
- **Applicability:** Project-specific.  Adjust with labelled data.

### Severity Weights: 40 / 25 / 15 / 20 (`_compute_severity`)

| Component | Weight | Normalisation |
|-----------|--------|---------------|
| Anomaly score | 40 % | Clipped to [0, 1] |
| Signal count | 25 % | N / 8 (capped at 8 signals) |
| Duration | 15 % | seconds / 300 (capped at 5 min) |
| Criticality | 20 % | 1.0 if any critical signal, else 0.0 |

- **Source:** [EXP] Expert knowledge, iteratively adjusted
  during early pilot log reviews.
- **Rationale:** The anomaly score dominates because it is the most
  objective measure.  Signal breadth (25 %) reflects that multi-signal
  anomalies are more likely to represent a real fault.  Duration (15 %)
  penalises sustained deviations.  Criticality (20 %) boosts events
  touching safety-relevant signals (RPM, coolant, fuel trims).  Duration
  has the lowest weight because even brief events can be significant
  (e.g. a misfire spike).
- **Applicability:** Project-specific.  Needs validation on a larger
  fleet.

### Duration Cap: 300 s (5 min) (`_compute_severity`)

- **Source:** [EMP] Empirical — based on typical OBD log durations
  in the project dataset.
- **Rationale:** Most individual anomaly windows in pilot logs are
  under 5 minutes.  Capping at 300 s prevents a single long event
  from dominating the severity score.  The normalised duration component
  saturates at 1.0 beyond this cap.
- **Applicability:** Suitable for short diagnostic drive cycles
  (10 – 30 min).  For long-haul fleet monitoring, consider increasing.

### Signal Count Cap: 8 (`_compute_severity`)

- **Source:** [EMP] Matches the 8 critical PIDs defined in
  `_CRITICAL_SIGNALS`.
- **Rationale:** An anomaly touching all 8 critical signals is
  maximally broad.  Normalising by 8 ensures that signal_norm saturates
  at 1.0 when every critical signal is involved.
- **Applicability:** Tied to the critical-PID list.

### Ruptures Pelt Penalty: 3.0 (`pen` parameter)

- **Source:** [LIT] Ruptures library documentation
  ([ruptures.readthedocs.io](https://ruptures.readthedocs.io/)).
  The Pelt algorithm's penalty controls sensitivity: lower values detect
  more change-points, higher values fewer.
- **Rationale:** 3.0 is the library's recommended starting point for
  the RBF kernel on standardised data.  In pilot testing it produced a
  reasonable number of change-points (2 – 8 per signal per log) without
  over-segmenting noisy signals.
- **Applicability:** Sensitive to signal variance.  Re-tune if the
  fleet produces noisier or smoother data.

### Isolation Forest Contamination: 0.05 (5 %) (`contamination` parameter)

- **Source:** [LIT] scikit-learn `IsolationForest`
  documentation.  `contamination` is the expected proportion of outliers
  in the dataset.
- **Rationale:** 5 % is a standard starting point in anomaly-detection
  literature when the true outlier rate is unknown.  In OBD data,
  assuming roughly 1 in 20 samples is anomalous provides a conservative
  baseline — enough to surface real faults without flooding the report
  with noise.
- **Applicability:** If a fleet's actual fault rate is known to be
  higher or lower, adjust accordingly.

### Isolation Forest n_estimators: 100

- **Source:** [LIT] scikit-learn default for `IsolationForest`.
- **Rationale:** 100 trees is the library default and provides a good
  bias-variance trade-off for datasets of the size encountered in
  single-session OBD logs (typically 100 – 3 000 rows).
- **Applicability:** Universal default; increase for very large
  datasets if runtime permits.

### Minimum Rows for Change-Point Detection: 20 (`_MIN_ROWS_CHANGEPOINT`)

- **Source:** [EMP] Empirical — minimum needed for ruptures Pelt to
  produce meaningful segments.
- **Rationale:** With the default `min_size=10` in Pelt, at least two
  full segments are needed (2 x 10 = 20 rows).  Fewer rows produce
  either no change-points or trivial one-point segments.
- **Applicability:** Scales with `min_segment_length`.

### Minimum Rows for Isolation Forest: 30 (`_MIN_ROWS_ISOLATION_FOREST`)

- **Source:** [EMP] Empirical — ensures enough samples for
  meaningful z-score normalisation and tree construction.
- **Rationale:** Isolation Forest needs sufficient data to estimate
  the distribution.  With fewer than 30 rows, z-score normalisation
  is unstable and the forest tends to label random noise as outliers.
  30 is a common small-sample threshold in statistics (cf. Central
  Limit Theorem rule of thumb).
- **Applicability:** Conservative floor; increase for higher-
  dimensional data.

### Top Contributing Signals: 5 (`_detect_multivariate_outliers`)

- **Source:** [EMP] UX / readability decision.
- **Rationale:** Reporting more than 5 signals per anomaly event makes
  the output harder for the LLM and human reviewers to prioritise.  5
  provides enough detail to identify the fault while keeping the report
  concise.
- **Applicability:** Adjust based on downstream consumer needs.

---

## C. Diagnostic Clue Thresholds

**File:** `obd_agent/rules/diagnostic_rules.yaml`

### STAT_001 — Engine Off: RPM max <= 50

- **Source:** [EXP] Same rationale as `_RPM_OFF` in
  anomaly_detector.py (see section B above).
- **Rationale:** If the maximum RPM reading in the entire session
  never exceeds 50, the engine was not running.
- **Applicability:** Universal for 4-stroke engines.

### STAT_002 — Stationary: Speed max <= 1 km/h

- **Source:** [EMP] Empirical — slightly tighter than the
  `_SPEED_MOVING` threshold (5 km/h) because this is a *maximum*
  check, not a mean.
- **Rationale:** If the maximum speed never exceeds 1 km/h, the
  vehicle did not move.  The 1 km/h tolerance accounts for VSS sensor
  quantisation noise (typically 0 – 1 km/h at rest).
- **Applicability:** Universal.

### STAT_003 — Constant Coolant Temperature: Std <= 0.5 °C

- **Source:** [EXP] Automotive diagnostic practice.
- **Rationale:** During normal operation, coolant temperature
  oscillates 2 – 5 °C around the thermostat setpoint as the thermostat
  cycles open/closed.  A standard deviation <= 0.5 °C means the
  temperature is essentially flat — either the engine never warmed up
  (short drive, thermostat stuck open) or the sensor is stuck.
- **Applicability:** Vehicles with conventional wax-pellet thermostats.
  Electric vehicles with thermal management loops may show different
  patterns.

### STAT_004 — Fuel Trim High Variation: Short Fuel Trim Std > 5.0 %

- **Source:** [EXP] Diagnostic convention.
- **Rationale:** Short-term fuel trim corrects cycle-by-cycle.  Under
  steady-state conditions, std is typically 1 – 3 %.  Above 5 % the
  ECU is making large corrections, suggesting a vacuum leak, injector
  imbalance, or O2 sensor degradation.
- **Applicability:** Closed-loop gasoline engines.

### STAT_005 — High Throttle Variance: Throttle Std > 10.0 %

- **Source:** [EMP] Empirical — derived from pilot logs.
- **Rationale:** During steady driving, throttle-position std is
  typically 2 – 5 %.  Above 10 % indicates aggressive driving, a
  sticking throttle body, or electronic throttle control (ETC) issues.
  Used as a contextual clue rather than a fault indicator.
- **Applicability:** All drive-by-wire vehicles.

### STAT_006 — Stable Intake Temperature: Std <= 2.0 °C

- **Source:** [EMP] Empirical.
- **Rationale:** Intake-air temperature normally varies slowly as the
  engine compartment heats up.  A std <= 2 °C over the session is
  normal for short drives.  This is an informational clue, not a fault.
- **Applicability:** Universal.

### STAT_007 — Elevated Coolant Temperature: Mean > 100 °C

- **Source:** [EXP] Consistent with the 110 °C operating-range
  upper bound (see section A).  The 100 °C mean threshold is more
  aggressive because a *mean* above 100 °C indicates sustained
  overheating, not just a momentary spike.
- **Rationale:** Normal mean operating temperature is 85 – 95 °C.
  A mean above 100 °C over a session strongly suggests cooling-system
  degradation (fan failure, low coolant, head-gasket leak).
- **Applicability:** Pressurised liquid-cooled engines.

### STAT_008 — High Long Fuel Trim: Max > 20.0 %

- **Source:** [STD] Near the OBD-II DTC trigger point.  P0170 –
  P0175 (fuel trim malfunction) typically set when long-term trim
  exceeds +/- 20 – 25 % depending on manufacturer calibration.
- **Rationale:** A maximum LTFT above 20 % means the ECU is
  compensating heavily for a lean or rich condition.  This threshold
  provides early warning before the ECU sets a MIL-on DTC.
- **Applicability:** Closed-loop gasoline engines.

### STAT_009 — Fuel Trim Spike: Max Abs Change > 10.0 %

- **Source:** [EXP] Diagnostic practice.
- **Rationale:** A sudden 10 %+ swing in short-term fuel trim within a
  single sample interval suggests an abrupt event — injector dropout,
  sudden vacuum leak, or fuel-pressure transient.  Normal sample-to-
  sample variation is 1 – 4 %.
- **Applicability:** Closed-loop gasoline engines.

### STAT_010 — Consistently Low Engine Load: Max <= 20.0 %

- **Source:** [EMP] Empirical.
- **Rationale:** If engine load never exceeds 20 % during an entire
  session, the engine was under minimal stress (idle or very light
  driving).  This is a contextual clue — diagnostic conclusions based
  on such a session may not reflect behaviour under normal load.
- **Applicability:** Universal.

### INTER_001 — RPM Oscillation Without Throttle Input

Conditions: `engine_rpm std > 100` AND `throttle_position std <= 2.0`

- **Source:** [EXP] Classic idle-instability diagnostic pattern.
- **Rationale:** If RPM fluctuates significantly (std > 100 rpm) while
  the driver is not touching the throttle (std <= 2 %), the idle-air
  control or fuel delivery is unstable.  Normal idle RPM std is 10 –
  40 rpm.  100 rpm threshold flags clearly abnormal oscillation.
  The 2.0 % throttle std confirms the driver is not actively modulating.
- **Applicability:** All vehicles with idle-speed control.

### INTER_002 — Low MAF Relative to Engine Load

Condition: `mass_airflow mean < engine_load mean * 0.1`

- **Source:** [EXP] Stoichiometric reasoning.
- **Rationale:** Engine load is a normalised measure of air filling.
  If the MAF sensor reports very low airflow relative to the computed
  load, the MAF sensor is likely dirty, failed, or disconnected.  The
  0.1 ratio is a coarse sanity check — a properly functioning MAF
  should report much higher values relative to load.
- **Applicability:** Vehicles equipped with a MAF sensor (not all
  OBD-II vehicles have one).

### INTER_003 — Fuel Trim Deviation with RPM Instability

Conditions: `short_fuel_trim_1 std > 5.0` AND `engine_rpm std > 50`

- **Source:** [EXP] Combined diagnostic pattern.
- **Rationale:** Fuel-trim instability (std > 5 %) combined with RPM
  instability (std > 50 rpm) suggests the two are causally linked —
  e.g. a vacuum leak causing both lean fuelling corrections and uneven
  idle.  The RPM threshold (50 rpm) is lower than INTER_001 (100 rpm)
  because the fuel-trim deviation already provides additional evidence.
- **Applicability:** Closed-loop gasoline engines.

### DTC Prefix Mapping (DTC_001 – DTC_003)

| Rule | Prefix | Subsystem | Standard |
|------|--------|-----------|----------|
| DTC_001 | P030x | Misfire | [STD] SAE J2012 / ISO 15031-6 section 6.3 |
| DTC_002 | P042x | Catalyst efficiency | [STD] SAE J2012 / ISO 15031-6 section 6.3 |
| DTC_003 | P040x | EGR system | [STD] SAE J2012 / ISO 15031-6 section 6.3 |

- **Source:** [STD] SAE J2012 / ISO 15031-6 define the
  standardised DTC prefix-to-subsystem mapping.  P0300 – P0312 are
  misfire codes, P0420 – P0424 are catalyst efficiency codes, P0400 –
  P0409 are EGR codes.
- **Applicability:** Universal OBD-II.

### NEG_001 — RPM Instability Without Misfire DTC

Condition: `engine_rpm std > 100` but no P030x codes present.

- **Source:** [EXP] Negative-evidence diagnostic reasoning.
- **Rationale:** If RPM is unstable enough to flag INTER_001 but the
  ECU has not set a misfire code, possible explanations include: (a) the
  misfire rate is below the ECU's DTC threshold, (b) the issue is
  mechanical (e.g. mount vibration) rather than combustion-related, or
  (c) the ECU's misfire monitor has not completed.
- **Applicability:** Universal.

### NEG_002 — Elevated Coolant Without Overheating DTC

Condition: `coolant_temperature max > 100 °C` but no P048x codes.

- **Source:** [EXP] Negative-evidence reasoning.
- **Rationale:** The ECU may not set a coolant-overheating DTC until
  a higher threshold (often 115 – 120 °C) or until the condition
  persists for a manufacturer-defined duration.  Flagging the
  discrepancy prompts the technician to investigate even without a
  stored code.
- **Applicability:** Vehicles with coolant-temperature DTCs (P0480 –
  P0489).

---

## D. Signal Processing Constants

### Resampling Interval: 1.0 s

**File:** `obd_agent/time_series_normalizer.py`
(`normalize_rows` and `normalize_log_file`, `interval_seconds` parameter)

- **Source:** [LIT] Nyquist-informed choice given OBD-II
  polling rates.
- **Rationale:** OBD-II Mode 01 polling over a serial ELM327 adapter
  typically yields 1 – 10 samples per second depending on the number
  of PIDs requested.  A 1 s uniform grid is at or below the raw
  sampling rate, so no information is lost due to aliasing for the
  phenomena of interest (engine RPM trends, temperature ramps).  Finer
  grids (e.g. 0.1 s) would introduce mostly interpolated values with
  no additional diagnostic information.
- **Applicability:** ELM327-based OBD adapters.  CAN-bus loggers with
  higher native rates could benefit from a finer grid.

### Shannon Entropy Histogram Bins: 10

**File:** `obd_agent/statistics_extractor.py`
(`_shannon_entropy`, `n_bins` parameter)

- **Source:** [LIT] Common default in information-theory
  applications.
- **Rationale:** 10 bins provides a reasonable trade-off between
  resolution and stability for typical OBD signal distributions (100 –
  3 000 samples).  Fewer bins (e.g. 5) lose detail in multi-modal
  distributions; more bins (e.g. 50) produce noisy estimates on short
  signals.  Sturges' rule (`ceil(1 + log2(n))`) for n = 1 000 gives
  ~11 bins, confirming that 10 is in the right range.
- **Applicability:** General-purpose.  For very long logs (>10 000
  samples), consider increasing.

### Autocorrelation Minimum Samples: 3

**File:** `obd_agent/statistics_extractor.py`
(`_autocorrelation_lag1`)

- **Source:** [LIT] Statistical minimum for Pearson correlation.
- **Rationale:** Lag-1 autocorrelation requires at least 2 pairs
  (x[0],x[1]) and (x[1],x[2]), meaning n >= 3.  With fewer samples the
  correlation is undefined (returns NaN).
- **Applicability:** Universal statistical constraint.

### Entropy Minimum Samples: 2

**File:** `obd_agent/statistics_extractor.py`
(`_shannon_entropy`)

- **Source:** [LIT] Information theory — entropy requires at
  least two observations to form a non-degenerate distribution.
- **Applicability:** Universal.

### Rounding Precision: 4 Decimal Places

**File:** `obd_agent/statistics_extractor.py`
(`_compute_signal_stats`, `decimal_places` parameter)

- **Source:** [EMP] Pragmatic choice.
- **Rationale:** OBD-II sensor resolution is typically 8 – 16 bits,
  giving at most ~4 significant figures for most PIDs.  Rounding to
  4 decimal places preserves all meaningful precision while producing
  clean JSON output.
- **Applicability:** Universal.

### Percentiles: 5, 25, 50, 75, 95

**File:** `obd_agent/statistics_extractor.py`
(`_compute_signal_stats`)

- **Source:** [LIT] Standard descriptive-statistics convention.
- **Rationale:** The 5-number summary (min, Q1, median, Q3, max) is
  extended with P5 and P95 to characterise the tails without being as
  sensitive to single outliers as min/max.  This set is widely used in
  exploratory data analysis and signal characterisation.
- **Applicability:** Universal.

### Top Clues / Anomaly Events for RAG Query: 5

**File:** `obd_agent/summary_formatter.py`
(`_format_impl`, `clue_strings[:5]` and `events[:5]`)

- **Source:** [EMP] Context-window budget decision.
- **Rationale:** The RAG query string is used to retrieve relevant
  service-manual chunks.  Including more than 5 clues produces an
  overly broad query that dilutes retrieval precision.  5 items keep
  the query focused on the most salient diagnostic findings while
  staying well within embedding-model token limits.
- **Applicability:** Adjust if the RAG retriever's behaviour changes
  (e.g. a model with higher-dimensional embeddings may tolerate longer
  queries).

---

## E. Log Summariser Heuristics

**File:** `obd_agent/log_summarizer.py`

### Range-Shift Detection: > 2 Standard Deviations (`_detect_anomalies`)

- **Source:** [LIT] Standard statistical convention (the
  "two-sigma" rule).
- **Rationale:** If the first sample differs from the session mean by
  more than 2 standard deviations, the signal likely experienced an
  initial transient (e.g. cold-start enrichment, warm-up ramp).  The
  2-sigma threshold corresponds to roughly the outer 5 % of a normal
  distribution, balancing sensitivity and specificity.
- **Applicability:** Universal for normally-distributed signals.
  Skewed distributions may warrant a different multiplier.

### Constant-Then-Change Ratio: 90 % Mode Frequency (`_detect_anomalies`)

- **Source:** [EMP] Empirical.
- **Rationale:** If 90 % or more of readings are identical (the mode),
  the signal is effectively constant with a few exceptions — suggesting
  a sensor stuck at a value with occasional glitches, or a step-change
  event.  The 90 % threshold was chosen to avoid false positives from
  signals with slight natural quantisation (e.g. throttle position
  rounding to the same integer for most of an idle period).
- **Applicability:** Most useful for integer-valued or
  coarsely-quantised PIDs.

---

## F. OBD Agent Configuration Defaults

**File:** `obd_agent/config.py`

These are runtime configuration defaults, not diagnostic thresholds, but
are documented here for completeness.

### Serial Baud Rate: 115 200

- **Source:** [STD] ELM327 / STN1110 default baud rate.
- **Rationale:** 115 200 bps is the factory default for virtually all
  ELM327-compatible OBD adapters.
- **Applicability:** Universal for ELM327.  Some STN adapters support
  higher rates (230 400, 500 000).

### Snapshot Poll Interval: 30 s

- **Source:** [EMP] Empirical — balances data granularity with
  storage and processing cost.
- **Rationale:** For a diagnostic drive cycle, 30 s captures trends in
  temperature, fuel trims, and load without generating excessive data.
  The normalised time-series pipeline (1 s grid) operates on the raw
  log, not snapshots, so this interval only affects the real-time
  snapshot endpoint.
- **Applicability:** Adjust for use case: 5 – 10 s for active
  troubleshooting, 60 s for long-term monitoring.

### Max HTTP Retry Attempts: 3

- **Source:** [EMP] Standard retry convention.
- **Rationale:** Three retries with the default back-off cover
  transient network glitches (container restart, brief DNS failure)
  without blocking the agent for too long.
- **Applicability:** General-purpose.

### Offline Buffer Size: 100 snapshots

- **Source:** [EMP] Memory-budget decision.
- **Rationale:** 100 snapshots x ~4 KB each = ~400 KB — negligible
  memory footprint while buffering roughly 50 minutes of data at the
  default 30 s interval.  If the API is unreachable for longer, older
  snapshots are dropped (FIFO) to prevent unbounded memory growth.
- **Applicability:** Increase for long-haul or intermittent-
  connectivity scenarios.

---

## Appendix: Values Marked "Empirical — Needs Validation"

The following thresholds are based on project-internal tuning and should
be validated on a broader dataset before deploying to a different fleet:

| Parameter | Value | File | Section |
|-----------|-------|------|---------|
| Speed moving threshold | 5 km/h | anomaly_detector.py | B |
| Throttle cruise std | 3.0 % | anomaly_detector.py | B |
| Severity tier thresholds | 0.33 / 0.66 | anomaly_detector.py | B |
| Severity weights | 40/25/15/20 | anomaly_detector.py | B |
| Duration cap | 300 s | anomaly_detector.py | B |
| Ruptures penalty | 3.0 | anomaly_detector.py | B |
| Throttle high variance | 10.0 % | diagnostic_rules.yaml | C |
| Intake temp stable | 2.0 °C std | diagnostic_rules.yaml | C |
| Engine load low | 20.0 % max | diagnostic_rules.yaml | C |
| Constant-then-change | 90 % mode | log_summarizer.py | E |
| Snapshot poll interval | 30 s | config.py | F |
| Offline buffer size | 100 snapshots | config.py | F |
