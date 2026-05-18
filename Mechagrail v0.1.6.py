"""
MechaGrail
A KSP 1 craft legality checker. Parses .craft files and evaluates them
against fighter/attacker tournament rules.
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Parts database
# ---------------------------------------------------------------------------
# Mass in tons, cost in funds. Stock KSP and BDArmory parts.
# When a part isn't in the DB, we fall back to 0 mass / 0 cost and flag it.

# ---------------------------------------------------------------------------
# BDA armor mass handling
# ---------------------------------------------------------------------------
# IMPORTANT: KSP/BDA writes the armor mass directly into each part's `modMass`
# field when armor is applied. Since the parser already reads modMass and adds
# it to the part's structural mass, armor mass is accounted for automatically.
# No additional estimation is needed.
#
# This constant remains here as a safety knob: if you find a case where modMass
# DOESN'T include armor mass (e.g. a craft saved by an older BDA version),
# set ARMOR_AREA_K to ~22.85 to enable surface-area estimation as a fallback.
# Leave at 0.0 for normal use.
ARMOR_AREA_K = 0.0

# Resource densities in tons per unit. From KSP's stock ResourcesGeneric.cfg
# and BDArmory's Resources/ folder (BDAmmo_Universal.cfg, Countermeasures.cfg,
# HighExplosive.cfg, Armor.cfg). All values authoritative.
RESOURCE_DENSITY: dict[str, float] = {
    # ---- Stock KSP resources ----
    "LiquidFuel":       0.005,
    "Oxidizer":         0.005,
    "MonoPropellant":   0.004,
    "XenonGas":         0.0001,
    "SolidFuel":        0.0075,
    "Ore":              0.010,
    "ElectricCharge":   0.0,
    "IntakeAir":        0.0,
    "IntakeAtm":        0.0,
    "Ablator":          0.001,

    # ---- BDA small arms / aircraft cannon ammo ----
    "LaserBolt":        0.000168,
    "7.62x39Ammo":      0.000268,
    "7.7x56Ammo":       0.000259,
    "7.92x57mmMauser":  0.0001,
    "9x19mmParaAmmo":   0.000011838,
    "50CalAmmo":        0.000117,   # was 0.0001
    "20x21Ammo":        0.00011,
    "20x102Ammo":       0.000259,   # was 0.000625
    "20x163Ammo":       0.0007,
    "23x115Ammo":       0.000498,
    "23x152Ammo":       0.000511,
    "25x137Ammo":       0.000528,   # was 0.000625
    "30x165Ammo":       0.000727,
    "30x173Ammo":       0.000741,   # was 0.000625
    "30x173HEAmmo":     0.000741,
    "37mmFlaKAmmo":     0.0021,
    "40x53Ammo":        0.001690,
    "40x53HeAmmo":      0.001690,
    "40x311Ammo":       0.00211,

    # ---- BDA mid/large cannon shells (mostly naval) ----
    "57x438Ammo":       0.005675,
    "75x714Ammo":       0.00151,
    "76x636Ammo":       0.0125,
    "3inchShells":      0.0056,
    "90mmShells":       0.0147,
    "100mmShells":      0.0187,
    "CannonShells":     0.0186,
    "105mmShells":      0.0186,
    "105mmHEShells":    0.0186,
    "4p5inchQFShells":  0.02,
    "120mmAmmo":        0.0187,
    "122mmQFShells":    0.02,
    "130Shells":        0.03,
    "5/62Shell":        0.03268,
    "QF5-25Shell":      0.03568,
    "138_140Shells":    0.025,
    "152Shells":        0.037325,
    "155Shells":        0.04562,
    "180Shells":        0.05,
    "203Shells":        0.06602,
    "11inShells":       0.250624,
    "12inShells":       0.3208965,
    "356ApAmmo":        0.35052,
    "356Shells":        0.35052,
    "380Shells":        0.4192,
    "16inchShells":     0.6,
    "460Shells":        0.76485,
    "M65ShellAmmo":     0.8522,
    "406mmNuclearShells": 0.8864,
    "54cmMortarShells": 1.0,
    "TungstenShell":    0.015968,

    # ---- BDA rockets ----
    "Type4Rocket":      0.022,
    "ATRocket":         0.008212,
    "Hades122Rocket":   0.0322,
    "Hydra70Rocket":    0.0122,
    "S-8KOMRocket":     0.0113,
    "Rockets":          0.01,

    # ---- BDA countermeasures (all 0.0007 per stock cfg) ----
    "CMFlare":          0.0007,    # was 0.0001 — 7x off
    "CMChaff":          0.0007,    # was 0.0001 — 7x off
    "CMSmoke":          0.0007,    # was 0.0001 — 7x off
    "CMDecoy":          0.002,
    "CMBubbleCurtain":  0.002,

    # ---- BDA other ----
    "HighExplosive":    0.00025,
    "Armor":            0.001,
    "ClusterBomblets":  0.0030,    # estimate (not in archive sent)
}


PARTS_DB: dict[str, dict] = {
    # =====================================================================
    # Stock KSP parts (values from KSP 1.12.5 part .cfg files — authoritative)
    # =====================================================================
    # ---- Cockpits / command ----
    "Mark1Cockpit":          {"mass": 1.2500, "cost": 1250, "category": "cockpit"},
    "Mark2Cockpit":          {"mass": 1.0000, "cost": 1600, "category": "cockpit"},
    "mk2Cockpit_Standard":   {"mass": 2.0000, "cost": 3500, "category": "cockpit"},
    "mk2Cockpit_Inline":     {"mass": 2.0000, "cost": 3500, "category": "cockpit"},
    "mk3Cockpit_Shuttle":    {"mass": 3.5000, "cost": 10000, "category": "cockpit"},
    "MK1CrewCabin":          {"mass": 1.0000, "cost": 550,  "category": "crew"},
    "crewCabin":             {"mass": 1.0000, "cost": 1200, "category": "crew"},
    "Mark1-2Pod":            {"mass": 2.7200, "cost": 3800, "category": "cockpit"},
    "mk1pod":                {"mass": 0.8400, "cost": 600,  "category": "cockpit"},
    "probeCoreOcto":         {"mass": 0.0300, "cost": 450,  "category": "probe"},
    "probeCoreOcto2":        {"mass": 0.0400, "cost": 1480, "category": "probe"},
    "probeCoreHex":          {"mass": 0.1000, "cost": 650,  "category": "probe"},
    "probeCoreCube":         {"mass": 0.0700, "cost": 360,  "category": "probe"},
    "probeStackLarge":       {"mass": 0.5000, "cost": 3400, "category": "probe"},
    "seatExternalCmd":       {"mass": 0.0500, "cost": 200,  "category": "command_chair"},

    # ---- Fuselage / fuel ----
    "MK1Fuselage":           {"mass": 0.2500, "cost": 550,  "category": "fuselage"},
    "miniFuselage":          {"mass": 0.0250, "cost": 200,  "category": "fuselage"},
    "mk2Fuselage":           {"mass": 0.5700, "cost": 1450, "category": "fuselage"},
    "mk3Fuselage":           {"mass": 0.5700, "cost": 1500, "category": "fuselage"},
    "fuelTankSmallFlat":     {"mass": 0.0625, "cost": 150,  "category": "fuel_tank"},
    "FuelTank":              {"mass": 0.5630, "cost": 250,  "category": "fuel_tank"},

    # ---- Wings ----
    "wingStrake":            {"mass": 0.0500, "cost": 400,  "category": "wing"},
    "structuralWing":        {"mass": 0.1000, "cost": 500,  "category": "wing"},
    "structuralWing2":       {"mass": 0.1000, "cost": 500,  "category": "wing"},
    "structuralWing3":       {"mass": 0.0500, "cost": 250,  "category": "wing"},
    "structuralWing4":       {"mass": 0.0250, "cost": 125,  "category": "wing"},
    "wingConnector":         {"mass": 0.2000, "cost": 500,  "category": "wing"},
    "wingConnector2":        {"mass": 0.1000, "cost": 250,  "category": "wing"},
    "wingConnector3":        {"mass": 0.1500, "cost": 375,  "category": "wing"},
    "wingConnector4":        {"mass": 0.0500, "cost": 100,  "category": "wing"},
    "wingConnector5":        {"mass": 0.0500, "cost": 125,  "category": "wing"},
    "deltaWing":             {"mass": 0.2000, "cost": 600,  "category": "wing"},
    "delta_small":           {"mass": 0.0500, "cost": 200,  "category": "wing"},
    "sweptWing":             {"mass": 0.2750, "cost": 620,  "category": "wing"},
    "sweptWing1":            {"mass": 0.1000, "cost": 250,  "category": "wing"},
    "sweptWing2":            {"mass": 0.0500, "cost": 125,  "category": "wing"},
    "swingWing":             {"mass": 0.0500, "cost": 125,  "category": "wing"},
    "R8winglet":             {"mass": 0.0150, "cost": 50,   "category": "wing"},
    "winglet":               {"mass": 0.1000, "cost": 600,  "category": "wing"},
    "winglet3":              {"mass": 0.0680, "cost": 320,  "category": "wing"},
    "airlinerMainWing":      {"mass": 0.6000, "cost": 1900, "category": "wing"},
    "airlinerTailFin":       {"mass": 0.2500, "cost": 750,  "category": "wing"},

    # ---- Control surfaces ----
    "StandardCtrlSrf":       {"mass": 0.0500, "cost": 400,  "category": "control_surface"},
    "elevon2":               {"mass": 0.0600, "cost": 550,  "category": "control_surface"},
    "elevon3":               {"mass": 0.0800, "cost": 650,  "category": "control_surface"},
    "elevon5":               {"mass": 0.0800, "cost": 800,  "category": "control_surface"},
    "smallCtrlSrf":          {"mass": 0.0400, "cost": 400,  "category": "control_surface"},
    "tailfin":               {"mass": 0.1250, "cost": 600,  "category": "control_surface"},
    "basicFin":              {"mass": 0.0100, "cost": 25,   "category": "fin"},
    "advancedCanard":        {"mass": 0.0500, "cost": 240,  "category": "control_surface"},
    "AdvancedCanard":        {"mass": 0.0800, "cost": 800,  "category": "control_surface"},
    "CanardController":      {"mass": 0.1000, "cost": 720,  "category": "control_surface"},
    "airlinerCtrlSrf":       {"mass": 0.1000, "cost": 600,  "category": "control_surface"},
    "airplaneTail":          {"mass": 0.2000, "cost": 675,  "category": "structural"},
    "airplaneTailB":         {"mass": 0.2000, "cost": 675,  "category": "structural"},

    # ---- Engines (jets) ----
    "JetEngine":             {"mass": 1.5000, "cost": 1400, "category": "engine_jet"},
    "turboFanEngine":        {"mass": 1.8000, "cost": 2250, "category": "engine_jet"},
    "turboFanSize2":         {"mass": 4.5000, "cost": 2600, "category": "engine_jet"},
    "miniJetEngine":         {"mass": 0.2500, "cost": 450,  "category": "engine_jet"},
    "RAPIER":                {"mass": 2.0000, "cost": 6000, "category": "engine_jet"},
    "turboJet":              {"mass": 1.2000, "cost": 2000, "category": "engine_jet"},

    # ---- Intakes / nose / structural ----
    "airScoop":              {"mass": 0.0200, "cost": 250,  "category": "intake"},
    "circularIntake":        {"mass": 0.0100, "cost": 350,  "category": "intake"},
    "CircularIntake":        {"mass": 0.0400, "cost": 680,  "category": "intake"},
    "ramAirIntake":          {"mass": 0.0600, "cost": 2680, "category": "intake"},
    "shockConeIntake":       {"mass": 0.1200, "cost": 3050, "category": "intake"},
    "IntakeRadialLong":      {"mass": 0.0100, "cost": 900,  "category": "intake"},
    "noseCone":              {"mass": 0.0300, "cost": 240,  "category": "structural"},
    "standardNoseCone":      {"mass": 0.0100, "cost": 180,  "category": "structural"},
    "rocketNoseCone":        {"mass": 0.0300, "cost": 240,  "category": "structural"},
    "Size3AdvancedEngine":   {"mass": 1.5000, "cost": 2500, "category": "engine"},

    # ---- Landing gear ----
    "SmallGearBay":          {"mass": 0.0450, "cost": 600,  "category": "gear"},
    "GearLarge":             {"mass": 0.6000, "cost": 1700, "category": "gear"},
    "GearMedium":            {"mass": 0.4000, "cost": 1200, "category": "gear"},
    "GearFixed":             {"mass": 0.0220, "cost": 100,  "category": "gear"},
    "GearFree":              {"mass": 0.0150, "cost": 150,  "category": "gear"},

    # =====================================================================
    # Modded parts (BDArmory, Aviator Arsenal, robotics, etc.)
    # These are estimates; override via ksp_parts_override.json for accuracy.
    # =====================================================================
    # ============================================================
    # Breaking Ground (Serenity) expansion — robotics & propellers
    # ============================================================
    # ---- Hinges (cfg uses underscore; craft files use dot, e.g. hinge.01) ----
    "hinge_01":              {"mass": 0.080, "cost": 120,  "category": "robotic"},
    "hinge_01_s":            {"mass": 0.010, "cost": 50,   "category": "robotic"},
    "hinge_03":              {"mass": 0.100, "cost": 360,  "category": "robotic"},
    "hinge_03_s":            {"mass": 0.080, "cost": 120,  "category": "robotic"},
    "hinge_04":              {"mass": 0.400, "cost": 480,  "category": "robotic"},
    # ---- Pistons ----
    "piston_01":             {"mass": 0.160, "cost": 300,  "category": "robotic"},
    "piston_02":             {"mass": 0.032, "cost": 50,   "category": "robotic"},
    "piston_03":             {"mass": 0.300, "cost": 500,  "category": "robotic"},
    "piston_04":             {"mass": 0.048, "cost": 100,  "category": "robotic"},
    # ---- Rotors (free-spinning) ----
    "rotor_01":              {"mass": 0.020, "cost": 60,   "category": "robotic"},
    "rotor_01s":             {"mass": 0.018, "cost": 60,   "category": "robotic"},
    "rotor_02":              {"mass": 0.100, "cost": 150,  "category": "robotic"},
    "rotor_02s":             {"mass": 0.090, "cost": 150,  "category": "robotic"},
    "rotor_03":              {"mass": 0.600, "cost": 600,  "category": "robotic"},
    "rotor_03s":             {"mass": 0.550, "cost": 600,  "category": "robotic"},
    # ---- Rotor engines ----
    "RotorEngine_02":        {"mass": 0.600, "cost": 200,  "category": "engine_prop"},
    "RotorEngine_03":        {"mass": 1.200, "cost": 550,  "category": "engine_prop"},
    # ---- Roto-servos (rotation servos) ----
    "rotoServo_00":          {"mass": 0.016, "cost": 60,   "category": "robotic"},
    "rotoServo_02":          {"mass": 0.060, "cost": 100,  "category": "robotic"},
    "rotoServo_03":          {"mass": 0.080, "cost": 120,  "category": "robotic"},
    "rotoServo_04":          {"mass": 0.480, "cost": 500,  "category": "robotic"},
    # ---- Propellers / heli blades / fan blades ----
    "smallPropeller":        {"mass": 0.010, "cost": 75,   "category": "wing"},
    "mediumPropeller":       {"mass": 0.035, "cost": 175,  "category": "wing"},
    "largePropeller":        {"mass": 0.120, "cost": 400,  "category": "wing"},
    "smallHeliBlade":        {"mass": 0.015, "cost": 100,  "category": "wing"},
    "mediumHeliBlade":       {"mass": 0.050, "cost": 225,  "category": "wing"},
    "largeHeliBlade":        {"mass": 0.180, "cost": 500,  "category": "wing"},
    "smallFanBlade":         {"mass": 0.010, "cost": 150,  "category": "wing"},
    "mediumFanBlade":        {"mass": 0.035, "cost": 275,  "category": "wing"},
    "largeFanBlade":         {"mass": 0.120, "cost": 550,  "category": "wing"},
    "FanShroud_01":          {"mass": 0.040, "cost": 85,   "category": "structural"},
    "FanShroud_02":          {"mass": 0.130, "cost": 175,  "category": "structural"},
    "FanShroud_03":          {"mass": 0.420, "cost": 325,  "category": "structural"},
    # ---- Robotic controller ----
    "controller1000":        {"mass": 0.010, "cost": 1000, "category": "probe"},
    # ---- Robot arm scanners ----
    "RobotArmScanner_S1":    {"mass": 0.060, "cost": 400,  "category": "probe"},
    "RobotArmScanner_S2":    {"mass": 0.140, "cost": 600,  "category": "probe"},
    "RobotArmScanner_S3":    {"mass": 0.300, "cost": 800,  "category": "probe"},
    # ---- Grip pads/strips (rover/walker feet) ----
    "sGripPad":              {"mass": 0.002, "cost": 30,   "category": "gear"},
    "mGripPad":              {"mass": 0.018, "cost": 75,   "category": "gear"},
    "lGripPad":              {"mass": 0.078, "cost": 300,  "category": "gear"},
    "sGripStrip":            {"mass": 0.012, "cost": 50,   "category": "gear"},
    "lGripStrip":            {"mass": 0.078, "cost": 300,  "category": "gear"},
    # ---- Deployable science ----
    "DeployedCentralStation": {"mass": 0.050, "cost": 800,   "category": "probe"},
    "DeployedGoExOb":         {"mass": 0.040, "cost": 1000,  "category": "probe"},
    "DeployedIONExp":         {"mass": 0.025, "cost": 7000,  "category": "probe"},
    "DeployedRTG":            {"mass": 0.040, "cost": 10000, "category": "probe"},
    "DeployedSatDish":        {"mass": 0.035, "cost": 1000,  "category": "probe"},
    "DeployedSeismicSensor":  {"mass": 0.035, "cost": 4000,  "category": "probe"},
    "DeployedSolarPanel":     {"mass": 0.015, "cost": 300,   "category": "probe"},
    "DeployedWeatherStn":     {"mass": 0.060, "cost": 1300,  "category": "probe"},
    # ---- Nose cones for prop blades ----
    "noseconeTiny":           {"mass": 0.001, "cost": 40,   "category": "structural"},
    "noseconeVS":             {"mass": 0.004, "cost": 80,   "category": "structural"},

    # ============================================================
    # Making History expansion parts (KSP 1.12.5)
    # ============================================================
    "Decoupler_1p5":                          {"mass": 0.09, "cost": 250, "category": "structural"},
    "Decoupler_4":                            {"mass": 0.64, "cost": 450, "category": "structural"},
    "EnginePlate1p5":                         {"mass": 0.14, "cost": 250, "category": "structural"},
    "EnginePlate2":                           {"mass": 0.25, "cost": 300, "category": "structural"},
    "EnginePlate3":                           {"mass": 0.58, "cost": 500, "category": "structural"},
    "EnginePlate4":                           {"mass": 1.0, "cost": 700, "category": "structural"},
    "EnginePlate5":                           {"mass": 0.062, "cost": 150, "category": "structural"},
    "EquiTriangle0":                          {"mass": 0.01, "cost": 10, "category": "structural"},
    "EquiTriangle1":                          {"mass": 0.04, "cost": 20, "category": "structural"},
    "EquiTriangle1p5":                        {"mass": 0.085, "cost": 45, "category": "structural"},
    "EquiTriangle2":                          {"mass": 0.15, "cost": 60, "category": "structural"},
    "HeatShield1p5":                          {"mass": 0.3, "cost": 500, "category": "structural"},
    "InflatableAirlock":                      {"mass": 0.1, "cost": 400, "category": "structural"},
    "LiquidEngineKE-1":                       {"mass": 5.0, "cost": 8000, "category": "engine"},
    "LiquidEngineLV-T91":                     {"mass": 1.0, "cost": 850, "category": "engine"},
    "LiquidEngineLV-TX87":                    {"mass": 2.0, "cost": 2000, "category": "engine"},
    "LiquidEngineRE-I2":                      {"mass": 1.6, "cost": 2300, "category": "engine"},
    "LiquidEngineRE-J10":                     {"mass": 3.3, "cost": 3000, "category": "engine"},
    "LiquidEngineRK-7":                       {"mass": 1.25, "cost": 1100, "category": "engine"},
    "LiquidEngineRV-1":                       {"mass": 0.18, "cost": 800, "category": "engine"},
    "MEMLander":                              {"mass": 1.355, "cost": 3500, "category": "cockpit"},
    "Mk2Pod":                                 {"mass": 1.56, "cost": 2800, "category": "cockpit"},
    "Panel0":                                 {"mass": 0.02, "cost": 15, "category": "structural"},
    "Panel1":                                 {"mass": 0.075, "cost": 30, "category": "structural"},
    "Panel1p5":                               {"mass": 0.17, "cost": 60, "category": "structural"},
    "Panel2":                                 {"mass": 0.3, "cost": 90, "category": "structural"},
    "Pollux":                                 {"mass": 8.0, "cost": 6000, "category": "engine"},
    "Separator_1p5":                          {"mass": 0.12, "cost": 325, "category": "structural"},
    "Separator_4":                            {"mass": 0.85, "cost": 650, "category": "structural"},
    "ServiceModule18":                        {"mass": 0.15, "cost": 300, "category": "structural"},
    "ServiceModule25":                        {"mass": 0.5, "cost": 500, "category": "structural"},
    "Size1p5_Monoprop":                       {"mass": 0.25, "cost": 960, "category": "fuel_tank"},
    "Size1p5_Size0_Adapter_01":               {"mass": 0.1, "cost": 160, "category": "structural"},
    "Size1p5_Size1_Adapter_01":               {"mass": 0.375, "cost": 600, "category": "structural"},
    "Size1p5_Size1_Adapter_02":               {"mass": 0.1, "cost": 160, "category": "structural"},
    "Size1p5_Size2_Adapter_01":               {"mass": 0.75, "cost": 1200, "category": "structural"},
    "Size1p5_Strut_Decoupler":                {"mass": 0.09, "cost": 475, "category": "structural"},
    "Size1p5_Tank_01":                        {"mass": 0.1375, "cost": 220, "category": "fuel_tank"},
    "Size1p5_Tank_02":                        {"mass": 0.275, "cost": 440, "category": "fuel_tank"},
    "Size1p5_Tank_03":                        {"mass": 0.5625, "cost": 900, "category": "fuel_tank"},
    "Size1p5_Tank_04":                        {"mass": 1.125, "cost": 1800, "category": "fuel_tank"},
    "Size1p5_Tank_05":                        {"mass": 0.75, "cost": 1400, "category": "fuel_tank"},
    "Size1to0ServiceModule":                  {"mass": 0.075, "cost": 300, "category": "structural"},
    "Size3_Size4_Adapter_01":                 {"mass": 4.0, "cost": 6400, "category": "structural"},
    "Size4_EngineAdapter_01":                 {"mass": 5.625, "cost": 9000, "category": "structural"},
    "Size4_Tank_01":                          {"mass": 4.0, "cost": 6400, "category": "fuel_tank"},
    "Size4_Tank_02":                          {"mass": 8.0, "cost": 12800, "category": "fuel_tank"},
    "Size4_Tank_03":                          {"mass": 16.0, "cost": 25600, "category": "fuel_tank"},
    "Size4_Tank_04":                          {"mass": 32.0, "cost": 51200, "category": "fuel_tank"},
    "Size_1_5_Cone":                          {"mass": 0.12, "cost": 160, "category": "structural"},
    "Triangle0":                              {"mass": 0.01, "cost": 10, "category": "structural"},
    "Triangle1":                              {"mass": 0.04, "cost": 20, "category": "structural"},
    "Triangle1p5":                            {"mass": 0.085, "cost": 45, "category": "structural"},
    "Triangle2":                              {"mass": 0.15, "cost": 60, "category": "structural"},
    "Tube1":                                  {"mass": 0.075, "cost": 300, "category": "structural"},
    "Tube1p5":                                {"mass": 0.075, "cost": 300, "category": "structural"},
    "Tube2":                                  {"mass": 0.075, "cost": 300, "category": "structural"},
    "Tube3":                                  {"mass": 0.075, "cost": 300, "category": "structural"},
    "Tube4":                                  {"mass": 0.075, "cost": 300, "category": "structural"},
    "fairingSize1p5":                         {"mass": 0.15, "cost": 450, "category": "structural"},
    "fairingSize4":                           {"mass": 0.8, "cost": 1200, "category": "structural"},
    "flagPartSize1p5":                        {"mass": 0.000125, "cost": 7, "category": "structural"},
    "flagPartSize4":                          {"mass": 0.0009, "cost": 25, "category": "structural"},
    "kv1Pod":                                 {"mass": 0.75, "cost": 600, "category": "cockpit"},
    "monopropMiniSphere":                     {"mass": 0.01, "cost": 30, "category": "fuel_tank"},
    "rocketNoseConeSize4":                    {"mass": 0.8, "cost": 1700, "category": "structural"},
    "roverWheelM1-F":                         {"mass": 0.025, "cost": 500, "category": "gear"},

    # ---- Modded engines (Aviator Arsenal / BDA Continued) ----
    "BDA.EJ200":             {"mass": 0.6500, "cost": 2000, "category": "engine_jet"},
    "BDA_EJ200":             {"mass": 0.6500, "cost": 2000, "category": "engine_jet"},
    "SaturnAL31":            {"mass": 1.0500, "cost": 3000, "category": "engine_jet"},

    # ---- BDA Refurbished / PEW (Russian + extended Western munitions) ----
    "PEWR-77":               {"mass": 0.190,  "cost": 2500, "category": "missile_aa"},
    "PEW9M96E2":             {"mass": 0.420,  "cost": 3500, "category": "missile_aa"},
    "THERIM-116":            {"mass": 0.0735, "cost": 700,  "category": "missile_aa"},
    "THERIM-162":            {"mass": 0.281,  "cost": 700,  "category": "missile_aa"},
    "PEW-BLU-82":            {"mass": 6.800,  "cost": 800,  "category": "bomb_unguided"},  # Daisy Cutter
    "PEW-GBU-43/B":          {"mass": 9.850,  "cost": 1100, "category": "bomb_guided"},   # MOAB
    "PEW-GBU-57/A":          {"mass": 13.600, "cost": 1200, "category": "bomb_guided"},   # MOP
    "PEWGBU53A":             {"mass": 0.080,  "cost": 325,  "category": "bomb_guided"},
    "kpaHellfireKinetic":    {"mass": 0.050,  "cost": 1000, "category": "missile_ag"},
    "bahaJdamMk82":          {"mass": 0.227,  "cost": 375,  "category": "bomb_guided"},
    "bahaMk83Bomb":          {"mass": 0.460,  "cost": 175,  "category": "bomb_unguided"},
    "bahaMk83BombBrake":     {"mass": 0.460,  "cost": 200,  "category": "bomb_unguided"},
    "bahaMk84Bomb":          {"mass": 0.920,  "cost": 300,  "category": "bomb_unguided"},
    "bahaMk84BombBrake":     {"mass": 0.920,  "cost": 320,  "category": "bomb_unguided"},
    "bahaJdamMk84":          {"mass": 0.920,  "cost": 950,  "category": "bomb_guided"},
    "bahaSmallSmokeCmPod":   {"mass": 0.003,  "cost": 200,  "category": "cm_smoke"},
    "APARradar":             {"mass": 0.200,  "cost": 1000, "category": "radar"},
    "STIRradar":             {"mass": 0.250,  "cost": 1250, "category": "radar"},
    "smartsradar":           {"mass": 0.500,  "cost": 2000, "category": "radar"},
    "RAMLauncherTurret":     {"mass": 1.750,  "cost": 950,  "category": "weapon_mount"},
    "SEARAMLauncherTurret":  {"mass": 1.750,  "cost": 950,  "category": "weapon_mount"},
    "thephalanx":            {"mass": 3.500,  "cost": 6000, "category": "weapon_gun"},
    "SingleReactiveArmor":   {"mass": 0.002,  "cost": 75,   "category": "armor"},

    # ============================================================
    # BDArmory Extended (values from BDA Extended .cfg files)
    # ============================================================
    "BDAsonarPod1B":                  {"mass": 0.5, "cost": 1000, "category": "radar"},
    "BahaClusterMissile":             {"mass": 0.198, "cost": 3000, "category": "missile_aa_cluster"},  # AA cluster missile per cfg (engageAir=true, engageGround=false)
    "BahaJernasReloadable":           {"mass": 0.75, "cost": 1000, "category": "weapon_mount"},
    "PointDefenseMG":                 {"mass": 0.15, "cost": 2500, "category": "weapon_gun"},
    "TigersharkBDATorpedo":           {"mass": 1.55, "cost": 4000, "category": "missile_ag"},
    "baha100mmTurret":                {"mass": 3.0, "cost": 4000, "category": "weapon_gun"},
    "baha130mmTurret":                {"mass": 4.0, "cost": 4000, "category": "weapon_gun"},
    "baha20mmAmmoDrum":               {"mass": 0.04, "cost": 2000, "category": "ammo"},
    "baha25mmAmmoDrum":               {"mass": 0.04, "cost": 2000, "category": "ammo"},
    "baha30mmAmmoDrum":               {"mass": 0.04, "cost": 2000, "category": "ammo"},
    "baha37mmAmmo":                   {"mass": 0.01, "cost": 1000, "category": "ammo"},
    "baha37mmCIWS":                   {"mass": 4.0, "cost": 4000, "category": "weapon_gun"},
    "baha37mmShellDrum":              {"mass": 0.04, "cost": 2000, "category": "ammo"},
    "baha37mmTurret":                 {"mass": 0.7, "cost": 3000, "category": "weapon_gun"},
    "baha50CalAmmoDrum":              {"mass": 0.04, "cost": 2000, "category": "ammo"},
    "baha57mmAmmo":                   {"mass": 0.01, "cost": 1500, "category": "ammo"},
    "baha57mmShellDrum":              {"mass": 0.025, "cost": 3000, "category": "ammo"},
    "baha57mmTurret":                 {"mass": 0.9, "cost": 3500, "category": "weapon_gun"},
    "baha76mmShellBox":               {"mass": 0.04, "cost": 3500, "category": "ammo"},
    "baha76mmShellDrum":              {"mass": 0.04, "cost": 3500, "category": "ammo"},
    "baha76mmTurret":                 {"mass": 2.0, "cost": 4000, "category": "weapon_gun"},
    "bahaAAHARM":                     {"mass": 0.181, "cost": 2000, "category": "missile_aa"},  # AA variant of HARM — air-to-air, not ARAD per ruleset
    "bahaAIM4FalconGAR11":            {"mass": 0.092, "cost": 5000, "category": "missile_aa"},
    "bahaAIM4FalconGAR2":             {"mass": 0.061, "cost": 400, "category": "missile_aa"},
    "bahaAIM4FalconMK1":              {"mass": 0.061, "cost": 400, "category": "missile_aa"},
    "bahaALRAAM":                     {"mass": 0.22, "cost": 2500, "category": "missile_aa"},
    "bahaAPS":                        {"mass": 0.2, "cost": 4000, "category": "weapon_gun"},
    "bahaATG_ER":                     {"mass": 0.42, "cost": 5500, "category": "missile_ag"},
    "bahaAdvSRIR":                    {"mass": 0.09, "cost": 2400, "category": "missile_aa"},
    "bahaBeamLaser":                  {"mass": 0.3, "cost": 10000, "category": "weapon_gun"},
    "bahaBomblet":                    {"mass": 0.015625, "cost": 0, "category": "submunition"},
    "bahaBombletDispenser":           {"mass": 0.1, "cost": 2000, "category": "bomb_unguided"},
    "bahaCIMS":                       {"mass": 3.0, "cost": 4000, "category": "weapon_gun"},
    "bahaCLSMissileIR":               {"mass": 0.012, "cost": 0, "category": "submunition"},
    "bahaCLSMissileRD":               {"mass": 0.012, "cost": 0, "category": "submunition"},
    "bahaCLS_Long":                   {"mass": 0.36, "cost": 2000, "category": "missile_cruise"},
    "bahaCLS_Short":                  {"mass": 0.18, "cost": 1000, "category": "missile_cruise"},
    "bahaCSAMSubmunition":            {"mass": 0.05, "cost": 0, "category": "submunition"},
    "bahaCannonShellDrum":            {"mass": 0.06, "cost": 4000, "category": "ammo"},
    "bahaChemLaser":                  {"mass": 0.5, "cost": 7600, "category": "weapon_gun"},
    "bahaCloakGenerator":             {"mass": 0.5, "cost": 20000, "category": "ecm_jammer"},
    "bahaClusterMissileSubmunition":  {"mass": 0.01, "cost": 0, "category": "submunition"},
    "bahaClusterSAM":                 {"mass": 0.44, "cost": 3000, "category": "missile_aa"},
    "bahaCombatLaser":                {"mass": 0.16, "cost": 950, "category": "weapon_gun"},
    "bahaDroptank":                   {"mass": 0.05, "cost": 400, "category": "fuel_tank"},
    "bahaERARH":                      {"mass": 0.34, "cost": 4000, "category": "missile_aa"},
    "bahaERATG":                      {"mass": 0.45, "cost": 3500, "category": "missile_ag"},
    "bahaERIR":                       {"mass": 0.337, "cost": 3800, "category": "missile_aa"},
    "bahaERRAAM":                     {"mass": 0.46, "cost": 4000, "category": "missile_aa"},
    "bahaEifreet":                    {"mass": 0.26, "cost": 3000, "category": "missile_ag"},
    "bahaElectroLaser":               {"mass": 0.4, "cost": 2500, "category": "weapon_gun"},
    "bahaFlakLaser":                  {"mass": 0.4, "cost": 8400, "category": "weapon_gun"},
    "bahaFlakShellDrum":              {"mass": 0.025, "cost": 2000, "category": "ammo"},
    "bahaFlakcannon":                 {"mass": 0.65, "cost": 800, "category": "weapon_gun"},
    "bahaFlogger":                    {"mass": 0.072, "cost": 1400, "category": "missile_aa"},
    "bahaGMLR":                       {"mass": 3.0, "cost": 4000, "category": "missile_ag"},
    "bahaGrom":                       {"mass": 0.32, "cost": 3500, "category": "missile_ag"},
    "bahaH70APKWS":                   {"mass": 0.014, "cost": 0, "category": "submunition"},
    "bahaHACM":                       {"mass": 0.14, "cost": 2000, "category": "missile_aa"},
    "bahaHAShM":                      {"mass": 1.2, "cost": 5000, "category": "missile_ss"},
    "bahaHATG":                       {"mass": 0.48, "cost": 1750, "category": "missile_ag"},
    "bahaHCAA":                       {"mass": 0.02, "cost": 800, "category": "missile_aa"},
    "bahaHSAM":                       {"mass": 0.625, "cost": 3600, "category": "missile_aa"},
    "bahaHSRRM":                      {"mass": 0.182, "cost": 1000, "category": "missile_aa"},
    "bahaHarmAA":                     {"mass": 0.14, "cost": 2000, "category": "missile_arad"},
    "bahaHomingH70Launcher":          {"mass": 0.016, "cost": 800, "category": "rocket_pod"},
    "bahaIRSAM":                      {"mass": 0.56, "cost": 4000, "category": "missile_aa"},
    "bahaLAShM":                      {"mass": 0.65, "cost": 3000, "category": "missile_ss"},
    "bahaLAShM_AL":                   {"mass": 0.51, "cost": 3000, "category": "missile_ss"},
    "bahaLGL":                        {"mass": 0.32, "cost": 7600, "category": "missile_ag"},
    "bahaLHARM":                      {"mass": 0.076, "cost": 800, "category": "missile_sidearm"},  # AGM-122 Side Arm — ARAD with ruleset exception (AA base, AG-class penalty)
    "bahaLRIR":                       {"mass": 0.156, "cost": 2400, "category": "missile_aa"},
    "bahaLSAM":                       {"mass": 0.064, "cost": 3000, "category": "missile_aa"},
    "bahaLSRRM":                      {"mass": 0.072, "cost": 800, "category": "missile_aa"},
    "bahaLariat":                     {"mass": 0.192, "cost": 2400, "category": "missile_ag"},
    "bahaLightningGun":               {"mass": 0.4, "cost": 2500, "category": "weapon_gun"},
    "bahaMRARH":                      {"mass": 0.075, "cost": 1200, "category": "missile_aa"},
    "bahaMRRAIR":                     {"mass": 0.085, "cost": 800, "category": "missile_aa"},
    "bahaMRSAM":                      {"mass": 0.134, "cost": 2000, "category": "missile_aa"},
    "bahaMRSARH":                     {"mass": 0.136, "cost": 1000, "category": "missile_aa"},
    "bahaMissileMagazine":            {"mass": 0.05, "cost": 1000, "category": "ammo"},
    "bahaMolot":                      {"mass": 1.2, "cost": 4000, "category": "weapon_gun"},
    "bahaNudelman":                   {"mass": 0.29, "cost": 950, "category": "weapon_gun"},
    "bahaPlasmaBeam":                 {"mass": 0.4, "cost": 2100, "category": "weapon_gun"},
    "bahaPulseLaser":                 {"mass": 0.08, "cost": 7600, "category": "weapon_gun"},
    "bahaPython":                     {"mass": 0.095, "cost": 1800, "category": "missile_aa"},
    "bahaQF6Pounder":                 {"mass": 0.39, "cost": 1250, "category": "weapon_gun"},
    "bahaRIM-66":                     {"mass": 0.7044, "cost": 4000, "category": "missile_aa"},
    "bahaRailgun":                    {"mass": 0.5, "cost": 2100, "category": "weapon_gun"},
    "bahaRedtop":                     {"mass": 0.132, "cost": 800, "category": "missile_aa"},
    "bahaReloadableAdjustableRail":   {"mass": 0.01, "cost": 50, "category": "weapon_mount"},
    "bahaReloadableRailMagazine":     {"mass": 0.05, "cost": 1000, "category": "ammo"},
    "bahaRepeaterCannon":             {"mass": 0.19, "cost": 2250, "category": "weapon_gun"},
    "bahaSARH":                       {"mass": 0.131, "cost": 1000, "category": "missile_aa"},
    "bahaSRAAM":                      {"mass": 0.05, "cost": 0, "category": "submunition"},
    "bahaSRIR":                       {"mass": 0.05, "cost": 350, "category": "missile_aa"},
    "bahaScatterLaser":               {"mass": 0.32, "cost": 7600, "category": "weapon_gun"},
    "bahaSingleGMLR":                 {"mass": 0.35, "cost": 4000, "category": "missile_ag"},
    "bahaSmallMissileTurret":         {"mass": 0.4, "cost": 1000, "category": "weapon_gun"},
    "bahaTorsionGun":                 {"mass": 0.35, "cost": 7500, "category": "weapon_gun"},
    "bahaTwinRail":                   {"mass": 0.02, "cost": 100, "category": "weapon_mount"},
    "bahaTyphoon":                    {"mass": 0.172, "cost": 1500, "category": "missile_aa"},
    "bahaVLSQuad":                    {"mass": 1.0, "cost": 2000, "category": "weapon_mount"},
    "bahaVLSSingle":                  {"mass": 0.25, "cost": 2000, "category": "weapon_mount"},
    "bahaYakB":                       {"mass": 0.05, "cost": 800, "category": "weapon_gun"},
    "baha_SwordfishTorpedo":          {"mass": 0.75, "cost": 3000, "category": "missile_ag"},
    "baha_Torplauncher":              {"mass": 1.0, "cost": 1000, "category": "weapon_mount"},
    "bdMissilePod":                   {"mass": 0.05, "cost": 250, "category": "weapon_mount"},
    "bdReloadableMissilePod":         {"mass": 0.05, "cost": 1000, "category": "weapon_mount"},
    "bdRepulsorGear":                 {"mass": 0.25, "cost": 5000, "category": "gear"},
    "bdWarheadSmall_CR":              {"mass": 0.15, "cost": 180, "category": "bomb_unguided"},
    "bdWarheadSmall_EMP":             {"mass": 0.15, "cost": 180, "category": "bomb_unguided"},
    "bdWarheadSmall_FR":              {"mass": 0.075, "cost": 180, "category": "bomb_unguided"},
    "bdWarheadSmall_N":               {"mass": 0.5, "cost": 180, "category": "bomb_unguided"},
    "bdWarheadSmall_SC":              {"mass": 0.15, "cost": 180, "category": "bomb_unguided"},
    "dcm_ChaffPod":                   {"mass": 0.001, "cost": 600, "category": "cm_chaff"},
    "dcm_CmPod":                      {"mass": 0.001, "cost": 600, "category": "cm_box"},

    # ---- Airplanes Plus (cockpits, engines, structural) ----
    "KP12":                  {"mass": 3.000, "cost": 9500,  "category": "cockpit"},
    "Type-22Cockpit":        {"mass": 2.000, "cost": 1600,  "category": "cockpit"},
    "Type-57-Cockpit":       {"mass": 0.900, "cost": 1500,  "category": "cockpit"},
    "airbuscockpit":         {"mass": 2.500, "cost": 3250,  "category": "cockpit"},
    "bombardiercockpit":     {"mass": 1.870, "cost": 2500,  "category": "cockpit"},
    "fightercockpit":        {"mass": 0.900, "cost": 1650,  "category": "cockpit"},
    "fighterinlinecockpit":  {"mass": 0.800, "cost": 1750,  "category": "cockpit"},
    "fighterlongcockpit":    {"mass": 0.900, "cost": 1500,  "category": "cockpit"},
    "hueycockpit":           {"mass": 1.500, "cost": 1900,  "category": "cockpit"},
    "oh6cockpit":            {"mass": 0.900, "cost": 1100,  "category": "cockpit"},
    "mk3galaxy":             {"mass": 3.000, "cost": 12500, "category": "cockpit"},
    "cfm56":                 {"mass": 2.250, "cost": 1300,  "category": "engine_jet"},
    "turboFanSize1.5":       {"mass": 1.950, "cost": 1000,  "category": "engine_jet"},
    "turboFanSize1_5":       {"mass": 1.950, "cost": 1000,  "category": "engine_jet"},
    "chinookprop":           {"mass": 2.000, "cost": 10000, "category": "engine_prop"},
    "hueyprop":              {"mass": 0.550, "cost": 3000,  "category": "engine_prop"},
    "herculesprop":          {"mass": 1.600, "cost": 1000,  "category": "engine_prop"},
    "tbmProp":               {"mass": 0.300, "cost": 1400,  "category": "engine_prop"},
    "hueytail":              {"mass": 0.150, "cost": 600,   "category": "structural"},
    "s2CargoRamp":           {"mass": 1.500, "cost": 1500,  "category": "structural"},
    "size4ramp":             {"mass": 5.500, "cost": 5000,  "category": "structural"},


    # ============================================================
    # BDArmory parts (values from BDA Continued .cfg files)
    # ============================================================
    # ---- BDA weapons (guns) ----
    "bahaRevolverCannon":    {"mass": 0.120, "cost": 950,  "category": "weapon_gun"},  # from BDA Extended cfg
    "bahaM230ChainGun":      {"mass": 0.100, "cost": 1500, "category": "weapon_gun"},
    "bahaGau-8":             {"mass": 0.550, "cost": 4000, "category": "weapon_gun"},
    "bahaGau-22":            {"mass": 0.300, "cost": 2100, "category": "weapon_gun"},
    "bahaGatlingGun":        {"mass": 0.200, "cost": 1900, "category": "weapon_gun"},
    "bahaBrowningAnm2":      {"mass": 0.040, "cost": 200,  "category": "weapon_gun"},
    "bahaHiddenVulcan":      {"mass": 0.100, "cost": 950,  "category": "weapon_gun"},
    "bahaGoalKeeper":        {"mass": 4.000, "cost": 9000, "category": "weapon_gun"},
    "GoalKeeperBDAcMk1":     {"mass": 4.000, "cost": 7500, "category": "weapon_gun"},
    "BDAcGKmk2":             {"mass": 4.400, "cost": 8000, "category": "weapon_gun"},
    "bahaM1Abrams":          {"mass": 2.000, "cost": 4000, "category": "weapon_gun"},
    "bahaM102Howitzer":      {"mass": 1.000, "cost": 2500, "category": "weapon_gun"},
    "bahaSidamTurret":       {"mass": 1.000, "cost": 4000, "category": "weapon_gun"},
    "bahaOMillennium":       {"mass": 1.000, "cost": 3500, "category": "weapon_gun"},
    "bahaTurret":            {"mass": 0.150, "cost": 400,  "category": "weapon_gun"},
    "missileTurretTest":     {"mass": 0.750, "cost": 1000, "category": "weapon_gun"},

    # ---- BDA ammo ----
    "baha30mmAmmo":          {"mass": 0.010, "cost": 1000, "category": "ammo"},
    "baha25mmAmmo":          {"mass": 0.010, "cost": 850,  "category": "ammo"},
    "baha20mmAmmo":          {"mass": 0.010, "cost": 600,  "category": "ammo"},
    "baha50CalAmmo":         {"mass": 0.010, "cost": 400,  "category": "ammo"},
    "bahaCannonShellBox":    {"mass": 0.015, "cost": 1000, "category": "ammo"},
    "bahaRocketBox":         {"mass": 0.015, "cost": 1500, "category": "ammo"},
    "BDAcUniversalAmmoBox":  {"mass": 0.100, "cost": 2000, "category": "ammo"},
    "UniversalAmmoBoxBDA":   {"mass": 0.100, "cost": 2000, "category": "ammo"},

    # ---- BDA missiles ----
    "bahaAim9":              {"mass": 0.085, "cost": 600,  "category": "missile_aa"},  # not in archive — estimate
    "bahaAim120":            {"mass": 0.152, "cost": 2500, "category": "missile_aa"},
    "AMRAAM_EMP":            {"mass": 0.152, "cost": 8000, "category": "missile_aa"},
    "bahaAGM-65":            {"mass": 0.270, "cost": 2500, "category": "missile_ag"},
    "bahaAGM-114":           {"mass": 0.050, "cost": 1200, "category": "missile_ag"},
    "HellfireEMP":           {"mass": 0.050, "cost": 6000, "category": "missile_ag"},
    "bahaHarm":              {"mass": 0.355, "cost": 2500, "category": "missile_arad"},
    "bahaAgm86B":            {"mass": 1.150, "cost": 5000, "category": "missile_cruise"},
    "bahaRBS-15ALCruise":    {"mass": 1.150, "cost": 3500, "category": "missile_cruise"},
    "bahaRBS-15Cruise":      {"mass": 1.150, "cost": 3000, "category": "missile_cruise"},
    "bahaAIR-2":             {"mass": 0.3729, "cost": 4000, "category": "missile_aa"},
    "bahaPac-3":             {"mass": 0.312, "cost": 4000, "category": "missile_ag"},
    "bahaTowMissile":        {"mass": 0.021, "cost": 600,  "category": "missile_ag"},
    "bahaKKV":               {"mass": 0.045, "cost": 2000, "category": "missile_ag"},
    "bahaHEKV1":             {"mass": 0.140, "cost": 4000, "category": "missile_ag"},
    "bahaABL":               {"mass": 0.800, "cost": 10000, "category": "missile_ag"},

    # ---- KPDynamics Naval pack (buoyancy + propellers) ----
    # Buoyancy parts have a large positive cfg mass paired with an offsetting
    # negative modMass in the craft file, simulating water displacement.
    # KSP shows the net mass (typically ~0.5t for smallBuoyancy in air).
    "smallBuoyancy":         {"mass": 5.0,   "cost": 1500,  "category": "buoyancy"},
    "mediumBuoyancy":        {"mass": 50.0,  "cost": 5000,  "category": "buoyancy"},
    "largeBuoyancy":         {"mass": 350.0, "cost": 15000, "category": "buoyancy"},
    "minnowPropeller":       {"mass": 1.0,   "cost": 500,   "category": "engine_prop"},
    "herringPropeller":      {"mass": 10.5,  "cost": 2500,  "category": "engine_prop"},
    "sturgeonPropeller":     {"mass": 15.0,  "cost": 3500,  "category": "engine_prop"},

    # ---- KPDynamics (Comet/Meteor/AIM-160 etc.) missile pack ----
    "kpaComet":              {"mass": 0.085, "cost": 1300,  "category": "missile_aa"},
    "kpaAim160":             {"mass": 0.071, "cost": 2000,  "category": "missile_aa"},
    "kpaMeteor":             {"mass": 0.190, "cost": 3000,  "category": "missile_aa"},
    "kpaAST3":               {"mass": 0.060, "cost": 1400,  "category": "missile_ag"},
    "kpaSiAW":               {"mass": 0.355, "cost": 2500,  "category": "missile_arad"},  # Stand-in Attack Weapon — anti-radiation
    "kpaLynx":               {"mass": 1.300, "cost": 2600,  "category": "missile_ag"},
    "kpaNeolidas":           {"mass": 1.450, "cost": 7500,  "category": "missile_ag"},
    "kpaSimpson":            {"mass": 1.750, "cost": 6000,  "category": "missile_ag"},
    "kpaSphere":             {"mass": 0.100, "cost": 1450,  "category": "missile_ag"},
    "kpaSphereEW":           {"mass": 0.100, "cost": 3400,  "category": "missile_ag"},
    "kpaSphereGlide":        {"mass": 0.100, "cost": 350,   "category": "bomb_guided"},
    "kpaASN4G":              {"mass": 1.650, "cost": 17500, "category": "missile_cruise"},
    "kpaMdCN":               {"mass": 1.400, "cost": 5000,  "category": "missile_cruise"},
    "kpaSCALP":              {"mass": 1.300, "cost": 5000,  "category": "missile_cruise"},
    "kpaATACMS":             {"mass": 1.670, "cost": 4500,  "category": "missile_ss"},
    "kpaGMLRS":              {"mass": 0.300, "cost": 1200,  "category": "missile_ss"},
    "kpaPRSM":               {"mass": 0.750, "cost": 2550,  "category": "missile_ss"},
    "kpaSUU":                {"mass": 0.800, "cost": 750,   "category": "rocket_pod"},
    "kpaMk141Launcher":      {"mass": 0.250, "cost": 500,   "category": "weapon_mount"},
    "kpaBomblet":             {"mass": 0.015, "cost": 0,    "category": "submunition"},
    "kpaEsperieye":          {"mass": 0.800, "cost": 5000,  "category": "radar"},
    "kpaSNOOPR":             {"mass": 0.650, "cost": 2250,  "category": "radar"},
    "kpaSNOOP7Small":        {"mass": 0.375, "cost": 5250,  "category": "radar"},
    "kpaSNOOP7Large":        {"mass": 1.750, "cost": 11250, "category": "radar"},
    "kpa40Mk4":              {"mass": 1.500, "cost": 4000,  "category": "weapon_gun"},
    "kpa57Mk3":              {"mass": 3.500, "cost": 4000,  "category": "weapon_gun"},
    "kpa127Mod4":            {"mass": 7.500, "cost": 4000,  "category": "weapon_gun"},
    "kpa155Mk51":            {"mass": 15.00, "cost": 4000,  "category": "weapon_gun"},
    "kpaOTO76":              {"mass": 5.000, "cost": 4000,  "category": "weapon_gun"},
    # KPDynamics also re-skins the JDAM bombs:
    "bahaJdamerMk82":        {"mass": 0.227, "cost": 425,   "category": "bomb_guided"},
    "bahaJdamerMk83":        {"mass": 0.460, "cost": 700,   "category": "bomb_guided"},
    "bahaJdamerMk84":        {"mass": 0.920, "cost": 1100,  "category": "bomb_guided"},

    # ---- BDA bombs ----
    "bahaMk82Bomb":          {"mass": 0.227, "cost": 100,  "category": "bomb_unguided"},
    "bahaMk82BombBrake":     {"mass": 0.227, "cost": 125,  "category": "bomb_unguided"},
    "bahaClusterBomb":       {"mass": 0.467, "cost": 200,  "category": "bomb_unguided"},
    "bahaJdamMk83":          {"mass": 0.460, "cost": 600,  "category": "bomb_guided"},

    # ---- BDA rocket pods ----
    "bahaS-8Launcher":       {"mass": 0.040, "cost": 3500, "category": "rocket_pod"},
    "bahaH70Launcher":       {"mass": 0.036, "cost": 300,  "category": "rocket_pod"},
    "bahaH70Turret":         {"mass": 0.416, "cost": 600,  "category": "rocket_pod"},
    "BahaF86Launcher":       {"mass": 0.100, "cost": 400,  "category": "rocket_pod"},

    # ---- BDA mounts / launch rails / bays ----
    "bahaAdjustableRail":    {"mass": 0.010, "cost": 50,   "category": "weapon_mount"},
    "bahaMissileRail":       {"mass": 0.010, "cost": 50,   "category": "weapon_mount"},
    "bahaTriRail":           {"mass": 0.050, "cost": 100,  "category": "weapon_mount"},
    "bdMissileBay":          {"mass": 0.200, "cost": 500,  "category": "weapon_mount"},
    "bdRotBombBay":          {"mass": 0.375, "cost": 500,  "category": "weapon_mount"},

    # ---- BDA countermeasures / sensors / AI ----
    "bahaChaffPod":          {"mass": 0.001, "cost": 50,  "category": "cm_chaff"},
    "bahaCmPod":             {"mass": 0.001, "cost": 50,  "category": "cm_box"},
    "dcm_ChaffPod":          {"mass": 0.001, "cost": 50,  "category": "cm_chaff"},
    "dcm_CmPod":             {"mass": 0.001, "cost": 50,  "category": "cm_box"},
    "bahaDecoyPod":          {"mass": 0.001, "cost": 600,  "category": "cm_box"},
    "bahaSmokeCmPod":        {"mass": 0.003, "cost": 50,  "category": "cm_smoke"},
    "bahaBubblePod":         {"mass": 0.001, "cost": 600,  "category": "cm_box"},
    "bahaFlarePod":          {"mass": 0.001, "cost": 600,  "category": "cm_flare"},  # estimate; mirrors chaff/cm pods
    "bahaECMJammer":         {"mass": 0.300, "cost": 1800, "category": "ecm_jammer"},
    "bahaIRSTpod":           {"mass": 0.200, "cost": 2000, "category": "radar"},
    "awacsRadar":            {"mass": 1.450, "cost": 10000, "category": "radar"},
    "scanLargeRadar":        {"mass": 1.450, "cost": 5000, "category": "radar"},
    "scanLockRadar1":        {"mass": 1.000, "cost": 4000, "category": "radar"},
    "bdRadome1":             {"mass": 0.375, "cost": 2000, "category": "radar"},
    "bdRadome1GA":           {"mass": 0.375, "cost": 2000, "category": "radar"},
    "bdRadome1inline":       {"mass": 0.375, "cost": 2000, "category": "radar"},
    "bdRadome1inlineGA":     {"mass": 0.375, "cost": 2000, "category": "radar"},
    "bdRadome1snub":         {"mass": 0.375, "cost": 2250, "category": "radar"},
    "bdRadome1snubGA":       {"mass": 0.375, "cost": 2250, "category": "radar"},
    "BDAsonarPod1A":         {"mass": 0.001, "cost": 1000, "category": "radar"},
    "radarDataReceiver":     {"mass": 0.050, "cost": 500,  "category": "radar"},
    "bahaCamPod":            {"mass": 0.200, "cost": 600,  "category": "targeting_pod"},
    "bahaFlirBall":          {"mass": 0.080, "cost": 1200, "category": "targeting_pod"},
    "bahaTargetingPod":      {"mass": 0.150, "cost": 1200, "category": "targeting_pod"},
    "bdPilotAI":             {"mass": 0.000, "cost": 0,    "category": "ai"},
    "bdOrbitalAI":           {"mass": 0.000, "cost": 0,    "category": "ai"},
    "bdShipAI":              {"mass": 0.000, "cost": 0,    "category": "ai"},
    "bdVTOLAI":              {"mass": 0.000, "cost": 0,    "category": "ai"},
    "missileController":     {"mass": 0.000, "cost": 0,    "category": "weapons_manager"},
    "bdammGuidanceModule":   {"mass": 0.000, "cost": 0,    "category": "weapons_manager"},

    # ---- BDA armor panels ----
    "BD_PanelArmor":         {"mass": 0.010,    "cost": 5,   "category": "armor"},
    "BD_PanelArmorIsoTri":   {"mass": 0.005,    "cost": 5,   "category": "armor"},
    "BD_PanelArmorTri":      {"mass": 0.005,    "cost": 5,   "category": "armor"},
    "BD0.5x0.5panelArmor":   {"mass": 0.01875,  "cost": 5,   "category": "armor"},
    "BD0.5x0.5slopeArmor":   {"mass": 0.009375, "cost": 2.5, "category": "armor"},
    "BD1.5x0.5panelArmor":   {"mass": 0.05625,  "cost": 15,  "category": "armor"},
    "BD1x0.5ReactiveArmor":  {"mass": 0.010,    "cost": 400, "category": "armor"},
    "BD1x0.5panelArmor":     {"mass": 0.0375,   "cost": 10,  "category": "armor"},
    "BD1x0.5slopeArmor":     {"mass": 0.01875,  "cost": 5,   "category": "armor"},
    "BD1x1panelArmor":       {"mass": 0.075,    "cost": 20,  "category": "armor"},
    "BD1x1slopeArmor":       {"mass": 0.0375,   "cost": 10,  "category": "armor"},
    "BD2x0.5panelArmor":     {"mass": 0.075,    "cost": 20,  "category": "armor"},
    "BD2x1panelArmor":       {"mass": 0.150,    "cost": 40,  "category": "armor"},
    "BD2x1slopeArmor":       {"mass": 0.075,    "cost": 20,  "category": "armor"},
    "BD2x2panelArmor":       {"mass": 0.300,    "cost": 80,  "category": "armor"},
    "BD2x2slopeArmor":       {"mass": 0.150,    "cost": 40,  "category": "armor"},
    "BD3x1panelArmor":       {"mass": 0.225,    "cost": 60,  "category": "armor"},
    "BD4x1panelArmor":       {"mass": 0.300,    "cost": 80,  "category": "armor"},
    "BD4x2panelArmor":       {"mass": 0.600,    "cost": 160, "category": "armor"},
    "BD4x2slopeArmor":       {"mass": 0.300,    "cost": 80,  "category": "armor"},
    "BD4x4panelArmor":       {"mass": 1.200,    "cost": 320, "category": "armor"},
    "BD4x4slopeArmor":       {"mass": 0.600,    "cost": 160, "category": "armor"},
    "BD6x2panelArmor":       {"mass": 0.900,    "cost": 240, "category": "armor"},
    "BD8x2panelArmor":       {"mass": 1.200,    "cost": 320, "category": "armor"},
    "BD8x4panelArmor":       {"mass": 2.400,    "cost": 640, "category": "armor"},
    "BD8x4slopeArmor":       {"mass": 1.200,    "cost": 320, "category": "armor"},
    "BD12x4panelArmor":      {"mass": 3.600,    "cost": 960, "category": "armor"},
    "BD16x4panelArmor":      {"mass": 4.800,    "cost": 1280, "category": "armor"},

    # ---- Misc ----
    "mk1opencockpit_RP_type2": {"mass": 0.360, "cost": 430, "category": "cockpit"},
    "seatExternalCmdweaponized": {"mass": 0.050, "cost": 0, "category": "command_chair"},
    "StingRayBDATorpedo":    {"mass": 0.2655, "cost": 2000, "category": "missile_ag"},
    "patriotLauncherTurret": {"mass": 1.750, "cost": 2500, "category": "weapon_mount"},
    "towLauncherTurret":     {"mass": 0.250, "cost": 600,  "category": "weapon_mount"},
    "bdWarheadSmall":        {"mass": 0.150, "cost": 180,  "category": "bomb_unguided"},
    "bdImpulseGun":          {"mass": 0.100, "cost": 10000, "category": "weapon_gun"},
}

# =====================================================================
# Auto-merged stock KSP parts (KSP 1.12.5)
# =====================================================================
# This table contains every part from the vanilla Squad/Parts/ cfg tree,
# with authoritative mass/cost. It's merged into PARTS_DB at module load
# so PARTS_DB above only needs entries for parts that need explicit
# categories (i.e., parts that participate in rule logic) or for mod
# parts. Anything else can be added here without needing a category.

# 353 stock part entries auto-loaded from KSP 1.12.5 cfg files.
# This table provides mass/cost; categories come from hand-curated PARTS_DB.
_STOCK_PARTS_RAW: dict[str, dict] = {
    "AdvancedCanard": {"mass": 0.08, "cost": 800},
    "CanardController": {"mass": 0.1, "cost": 720},
    "CargoStorageUnit": {"mass": 0.4, "cost": 1500},
    "CircularIntake": {"mass": 0.04, "cost": 680},
    "Clydesdale": {"mass": 21.0, "cost": 18500},
    "ConformalStorageUnit": {"mass": 0.02, "cost": 100},
    "Decoupler_0": {"mass": 0.01, "cost": 150},
    "Decoupler_1": {"mass": 0.04, "cost": 200},
    "Decoupler_2": {"mass": 0.16, "cost": 300},
    "Decoupler_3": {"mass": 0.36, "cost": 375},
    "FuelCell": {"mass": 0.05, "cost": 750},
    "FuelCellArray": {"mass": 0.24, "cost": 4500},
    "GearFixed": {"mass": 0.022, "cost": 100},
    "GearFree": {"mass": 0.015, "cost": 150},
    "GearLarge": {"mass": 0.6, "cost": 1700},
    "GearMedium": {"mass": 0.4, "cost": 1200},
    "GearSmall": {"mass": 0.25, "cost": 700},
    "GooExperiment": {"mass": 0.05, "cost": 800},
    "GrapplingDevice": {"mass": 0.075, "cost": 450},
    "HECS2_ProbeCore": {"mass": 0.2, "cost": 7500},
    "HeatShield0": {"mass": 0.025, "cost": 150},
    "HeatShield1": {"mass": 0.1, "cost": 300},
    "HeatShield2": {"mass": 0.5, "cost": 600},
    "HeatShield3": {"mass": 1.0, "cost": 1100},
    "HighGainAntenna": {"mass": 0.075, "cost": 1200},
    "HighGainAntenna5": {"mass": 0.07, "cost": 600},
    "HighGainAntenna5_v2": {"mass": 0.07, "cost": 600},
    "ISRU": {"mass": 4.25, "cost": 8000},
    "InflatableHeatShield": {"mass": 1.5, "cost": 2400},
    "InfraredTelescope": {"mass": 0.1, "cost": 4500},
    "IntakeRadialLong": {"mass": 0.01, "cost": 900},
    "JetEngine": {"mass": 1.5, "cost": 1400},
    "LargeTank": {"mass": 2.0, "cost": 3000},
    "Large_Crewed_Lab": {"mass": 3.5, "cost": 4000},
    "LaunchEscapeSystem": {"mass": 0.9, "cost": 1000},
    "LgRadialSolarPanel": {"mass": 0.04, "cost": 600},
    "MK1CrewCabin": {"mass": 1.0, "cost": 550},
    "MK1Fuselage": {"mass": 0.25, "cost": 550},
    "MK1IntakeFuselage": {"mass": 0.17, "cost": 720},
    "Magnetometer": {"mass": 0.05, "cost": 2200},
    "Mark1Cockpit": {"mass": 1.25, "cost": 1250},
    "Mark2Cockpit": {"mass": 1.0, "cost": 1600},
    "MassiveBooster": {"mass": 4.5, "cost": 2700},
    "MiniDrill": {"mass": 0.25, "cost": 1000},
    "MiniISRU": {"mass": 1.25, "cost": 1000},
    "Mite": {"mass": 0.075, "cost": 75},
    "Mk1FuselageStructural": {"mass": 0.1, "cost": 380},
    "MpoProbe": {"mass": 0.395, "cost": 9900},
    "MtmStage": {"mass": 0.415, "cost": 21500},
    "OrbitalScanner": {"mass": 0.1, "cost": 1000},
    "PotatoComet": {"mass": 150.0, "cost": 0},
    "PotatoRoid": {"mass": 150.0, "cost": 0},
    "R8winglet": {"mass": 0.1, "cost": 640},
    "RAPIER": {"mass": 2.0, "cost": 6000},
    "RCSBlock_v2": {"mass": 0.04, "cost": 45},
    "RCSFuelTank": {"mass": 0.08, "cost": 330},
    "RCSLinearSmall": {"mass": 0.00125, "cost": 15},
    "RCSTank1-2": {"mass": 0.4, "cost": 1800},
    "RCSblock_01_small": {"mass": 0.005, "cost": 30},
    "RadialDrill": {"mass": 1.25, "cost": 6000},
    "RadialOreTank": {"mass": 0.125, "cost": 300},
    "RelayAntenna100": {"mass": 0.65, "cost": 3000},
    "RelayAntenna5": {"mass": 0.15, "cost": 1800},
    "RelayAntenna50": {"mass": 0.3, "cost": 2400},
    "ReleaseValve": {"mass": 0.01, "cost": 50},
    "Rockomax16_BW": {"mass": 1.0, "cost": 1550},
    "Rockomax32_BW": {"mass": 2.0, "cost": 3000},
    "Rockomax64_BW": {"mass": 4.0, "cost": 5750},
    "Rockomax8BW": {"mass": 0.5, "cost": 800},
    "SSME": {"mass": 4.0, "cost": 18000},
    "ScienceBox": {"mass": 0.05, "cost": 1000},
    "Separator_0": {"mass": 0.01, "cost": 215},
    "Separator_1": {"mass": 0.05, "cost": 275},
    "Separator_2": {"mass": 0.21, "cost": 400},
    "Separator_3": {"mass": 0.48, "cost": 500},
    "ServiceBay_125_v2": {"mass": 0.1, "cost": 500},
    "ServiceBay_250_v2": {"mass": 0.3, "cost": 500},
    "Shrimp": {"mass": 0.15, "cost": 150},
    "Size2LFB": {"mass": 10.5, "cost": 17000},
    "Size2LFB_v2": {"mass": 10.5, "cost": 17000},
    "Size3AdvancedEngine": {"mass": 9.0, "cost": 25000},
    "Size3EngineCluster": {"mass": 15.0, "cost": 39000},
    "Size3LargeTank": {"mass": 9.0, "cost": 13000},
    "Size3MediumTank": {"mass": 4.5, "cost": 6500},
    "Size3SmallTank": {"mass": 2.25, "cost": 3250},
    "Size3To2Adapter_v2": {"mass": 1.875, "cost": 1623},
    "SmallGearBay": {"mass": 0.045, "cost": 600},
    "SmallTank": {"mass": 0.5, "cost": 1000},
    "StandardCtrlSrf": {"mass": 0.05, "cost": 400},
    "SurfAntenna": {"mass": 0.015, "cost": 300},
    "SurfaceScanner": {"mass": 0.005, "cost": 800},
    "SurveyScanner": {"mass": 0.2, "cost": 1500},
    "Thoroughbred": {"mass": 10.0, "cost": 9000},
    "adapterEngines": {"mass": 0.7, "cost": 2500},
    "adapterLargeSmallBi": {"mass": 0.1, "cost": 400},
    "adapterLargeSmallQuad": {"mass": 0.2, "cost": 800},
    "adapterLargeSmallTri": {"mass": 0.15, "cost": 600},
    "adapterMk3-Mk2": {"mass": 1.43, "cost": 2200},
    "adapterMk3-Size2": {"mass": 1.79, "cost": 2500},
    "adapterMk3-Size2Slant": {"mass": 1.79, "cost": 2500},
    "adapterSize2-Mk2": {"mass": 0.57, "cost": 800},
    "adapterSize2-Size1": {"mass": 0.57, "cost": 800},
    "adapterSize2-Size1Slant": {"mass": 0.57, "cost": 800},
    "adapterSize3-Mk3": {"mass": 1.79, "cost": 2500},
    "adapterSmallMiniShort": {"mass": 0.04, "cost": 100},
    "adapterSmallMiniTall": {"mass": 0.05, "cost": 150},
    "advSasModule": {"mass": 0.1, "cost": 1200},
    "airScoop": {"mass": 0.02, "cost": 250},
    "airbrake1": {"mass": 0.05, "cost": 1000},
    "airlinerCtrlSrf": {"mass": 0.17, "cost": 800},
    "airlinerMainWing": {"mass": 0.78, "cost": 2800},
    "airlinerTailFin": {"mass": 0.36, "cost": 1000},
    "airplaneTail": {"mass": 0.2, "cost": 675},
    "airplaneTailB": {"mass": 0.2, "cost": 675},
    "asasmodule1-2": {"mass": 0.2, "cost": 2100},
    "avionicsNoseCone": {"mass": 0.08, "cost": 5200},
    "basicFin": {"mass": 0.01, "cost": 25},
    "batteryBank": {"mass": 0.05, "cost": 880},
    "batteryBankLarge": {"mass": 0.2, "cost": 4500},
    "batteryBankMini": {"mass": 0.01, "cost": 360},
    "batteryPack": {"mass": 0.005, "cost": 80},
    "cargoContainer": {"mass": 0.15, "cost": 600},
    "commDish": {"mass": 0.1, "cost": 1500},
    "crewCabin": {"mass": 2.25, "cost": 4000},
    "cupola": {"mass": 0.94, "cost": 1600},
    "deltaWing": {"mass": 0.2, "cost": 600},
    "delta_small": {"mass": 0.05, "cost": 200},
    "dockingPort1": {"mass": 0.1, "cost": 400},
    "dockingPort2": {"mass": 0.05, "cost": 280},
    "dockingPort3": {"mass": 0.02, "cost": 800},
    "dockingPortLarge": {"mass": 0.2, "cost": 980},
    "dockingPortLateral": {"mass": 0.3, "cost": 700},
    "domeLight1": {"mass": 0.002, "cost": 50},
    "elevon2": {"mass": 0.06, "cost": 550},
    "elevon3": {"mass": 0.08, "cost": 650},
    "elevon5": {"mass": 0.08, "cost": 800},
    "engineLargeSkipper_v2": {"mass": 3.0, "cost": 5300},
    "evaChute": {"mass": 0.004, "cost": 10},
    "evaCylinder": {"mass": 0.005, "cost": 50},
    "evaJetpack": {"mass": 0.02, "cost": 25},
    "evaRepairKit": {"mass": 0.005, "cost": 75},
    "evaScienceKit": {"mass": 0.015, "cost": 150},
    "externalTankCapsule": {"mass": 0.03375, "cost": 50},
    "externalTankRound": {"mass": 0.01375, "cost": 50},
    "externalTankToroid": {"mass": 0.0375, "cost": 147},
    "fairingSize1": {"mass": 0.075, "cost": 300},
    "fairingSize2": {"mass": 0.175, "cost": 600},
    "fairingSize3": {"mass": 0.475, "cost": 900},
    "fireworksLauncherBig": {"mass": 0.03, "cost": 200},
    "fireworksLauncherSmall": {"mass": 0.00125, "cost": 15},
    "flagPartFlat": {"mass": 0.00015, "cost": 7.5},
    "flagPartSize0": {"mass": 2.5e-05, "cost": 2.5},
    "flagPartSize1": {"mass": 5e-05, "cost": 5},
    "flagPartSize2": {"mass": 0.000225, "cost": 12.5},
    "flagPartSize3": {"mass": 0.0005, "cost": 17.5},
    "foldingRadLarge": {"mass": 1.0, "cost": 9000},
    "foldingRadMed": {"mass": 0.25, "cost": 2250},
    "foldingRadSmall": {"mass": 0.05, "cost": 450},
    "fuelLine": {"mass": 0.05, "cost": 150},
    "fuelTank": {"mass": 0.25, "cost": 500},
    "fuelTankSmall": {"mass": 0.125, "cost": 275},
    "fuelTankSmallFlat": {"mass": 0.0625, "cost": 150},
    "fuelTank_long": {"mass": 0.5, "cost": 800},
    "groundAnchor": {"mass": 0.05, "cost": 300},
    "groundLight1": {"mass": 0.002, "cost": 25},
    "groundLight2": {"mass": 0.003, "cost": 35},
    "ionEngine": {"mass": 0.25, "cost": 8000},
    "ksp_r_largeBatteryPack": {"mass": 0.02, "cost": 550},
    "ladder1": {"mass": 0.005, "cost": 100},
    "landerCabinSmall": {"mass": 0.6, "cost": 1500},
    "landingLeg1": {"mass": 0.05, "cost": 440},
    "landingLeg1-2": {"mass": 0.1, "cost": 340},
    "largeAdapter": {"mass": 0.1, "cost": 500},
    "largeAdapter2": {"mass": 0.08, "cost": 450},
    "largeSolarPanel": {"mass": 0.3, "cost": 3000},
    "launchClamp1": {"mass": 0.1, "cost": 200},
    "linearRcs": {"mass": 0.02, "cost": 25},
    "liquidEngine": {"mass": 1.25, "cost": 1100},
    "liquidEngine2": {"mass": 1.5, "cost": 1200},
    "liquidEngine2-2_v2": {"mass": 1.75, "cost": 1300},
    "liquidEngine2_v2": {"mass": 1.5, "cost": 1200},
    "liquidEngine3_v2": {"mass": 0.5, "cost": 390},
    "liquidEngineMainsail_v2": {"mass": 6.0, "cost": 13000},
    "liquidEngineMini_v2": {"mass": 0.13, "cost": 240},
    "liquidEngine_v2": {"mass": 1.25, "cost": 1100},
    "longAntenna": {"mass": 0.005, "cost": 300},
    "mediumDishAntenna": {"mass": 0.05, "cost": 900},
    "microEngine_v2": {"mass": 0.02, "cost": 110},
    "miniFuelTank": {"mass": 0.025, "cost": 70},
    "miniFuselage": {"mass": 0.025, "cost": 200},
    "miniIntake": {"mass": 0.007, "cost": 250},
    "miniJetEngine": {"mass": 0.25, "cost": 450},
    "miniLandingLeg": {"mass": 0.015, "cost": 200},
    "mk1-3pod": {"mass": 2.6, "cost": 3800},
    "mk1pod_v2": {"mass": 0.8, "cost": 600},
    "mk2CargoBayL": {"mass": 0.5, "cost": 500},
    "mk2CargoBayS": {"mass": 0.25, "cost": 320},
    "mk2Cockpit_Inline": {"mass": 2.0, "cost": 3500},
    "mk2Cockpit_Standard": {"mass": 2.0, "cost": 3500},
    "mk2CrewCabin": {"mass": 2.03, "cost": 4200},
    "mk2DockingPort": {"mass": 0.3, "cost": 850},
    "mk2DroneCore": {"mass": 0.2, "cost": 2700},
    "mk2Fuselage": {"mass": 0.57, "cost": 1450},
    "mk2FuselageLongLFO": {"mass": 0.57, "cost": 1450},
    "mk2FuselageShortLFO": {"mass": 0.29, "cost": 750},
    "mk2FuselageShortLiquid": {"mass": 0.29, "cost": 750},
    "mk2FuselageShortMono": {"mass": 0.29, "cost": 750},
    "mk2LanderCabin": {"mass": 2.5, "cost": 3250},
    "mk2LanderCabin_v2": {"mass": 1.355, "cost": 3250},
    "mk2SpacePlaneAdapter": {"mass": 0.29, "cost": 550},
    "mk2_1m_AdapterLong": {"mass": 0.57, "cost": 1050},
    "mk2_1m_Bicoupler": {"mass": 0.29, "cost": 860},
    "mk3CargoBayL": {"mass": 6.0, "cost": 3000},
    "mk3CargoBayM": {"mass": 3.0, "cost": 1500},
    "mk3CargoBayS": {"mass": 1.5, "cost": 750},
    "mk3CargoRamp": {"mass": 4.0, "cost": 3000},
    "mk3Cockpit_Shuttle": {"mass": 3.5, "cost": 10000},
    "mk3CrewCabin": {"mass": 7.9, "cost": 30000},
    "mk3FuselageLFO_100": {"mass": 7.14, "cost": 10000},
    "mk3FuselageLFO_25": {"mass": 1.79, "cost": 2500},
    "mk3FuselageLFO_50": {"mass": 3.57, "cost": 5000},
    "mk3FuselageLF_100": {"mass": 7.14, "cost": 17200},
    "mk3FuselageLF_25": {"mass": 1.79, "cost": 4300},
    "mk3FuselageLF_50": {"mass": 3.57, "cost": 8600},
    "mk3FuselageMONO": {"mass": 1.4, "cost": 5040},
    "nacelleBody": {"mass": 0.15, "cost": 600},
    "navLight1": {"mass": 0.001, "cost": 40},
    "noseCone": {"mass": 0.03, "cost": 240},
    "noseConeAdapter": {"mass": 0.1, "cost": 320},
    "nuclearEngine": {"mass": 3.0, "cost": 10000},
    "omsEngine": {"mass": 0.09, "cost": 150},
    "parachuteDrogue": {"mass": 0.2, "cost": 400},
    "parachuteLarge": {"mass": 0.3, "cost": 850},
    "parachuteRadial": {"mass": 0.1, "cost": 400},
    "parachuteSingle": {"mass": 0.1, "cost": 422},
    "pointyNoseConeA": {"mass": 0.075, "cost": 320},
    "pointyNoseConeB": {"mass": 0.075, "cost": 320},
    "probeCoreCube": {"mass": 0.07, "cost": 360},
    "probeCoreHex": {"mass": 0.1, "cost": 650},
    "probeCoreHex_v2": {"mass": 0.1, "cost": 650},
    "probeCoreOcto2_v2": {"mass": 0.04, "cost": 1480},
    "probeCoreOcto_v2": {"mass": 0.1, "cost": 450},
    "probeCoreSphere_v2": {"mass": 0.05, "cost": 300},
    "probeStackLarge": {"mass": 0.5, "cost": 3400},
    "probeStackSmall": {"mass": 0.1, "cost": 2250},
    "radPanelEdge": {"mass": 0.03, "cost": 450},
    "radPanelLg": {"mass": 0.05, "cost": 450},
    "radPanelSm": {"mass": 0.01, "cost": 150},
    "radialDecoupler": {"mass": 0.025, "cost": 600},
    "radialDecoupler1-2": {"mass": 0.4, "cost": 770},
    "radialDecoupler2": {"mass": 0.05, "cost": 700},
    "radialDrogue": {"mass": 0.075, "cost": 150},
    "radialEngineBody": {"mass": 0.15, "cost": 1650},
    "radialEngineMini_v2": {"mass": 0.02, "cost": 120},
    "radialLiquidEngine1-2": {"mass": 0.9, "cost": 820},
    "radialRCSTank": {"mass": 0.02, "cost": 200},
    "ramAirIntake": {"mass": 0.06, "cost": 2680},
    "rcsTankMini": {"mass": 0.02, "cost": 200},
    "rcsTankRadialLong": {"mass": 0.03, "cost": 250},
    "rocketNoseConeSize3": {"mass": 0.4, "cost": 850},
    "rocketNoseCone_v2": {"mass": 0.2, "cost": 450},
    "rocketNoseCone_v3": {"mass": 0.2, "cost": 450},
    "roverBody": {"mass": 0.15, "cost": 800},
    "roverBody_v2": {"mass": 0.15, "cost": 800},
    "roverWheel1": {"mass": 0.075, "cost": 450},
    "roverWheel2": {"mass": 0.05, "cost": 300},
    "roverWheel3": {"mass": 1.25, "cost": 1200},
    "rtg": {"mass": 0.08, "cost": 23300},
    "sasModule": {"mass": 0.05, "cost": 600},
    "science_module": {"mass": 0.2, "cost": 1800},
    "seatExternalCmd": {"mass": 0.05, "cost": 200},
    "sensorAccelerometer": {"mass": 0.005, "cost": 6000},
    "sensorAtmosphere": {"mass": 0.005, "cost": 6500},
    "sensorBarometer": {"mass": 0.005, "cost": 880},
    "sensorGravimeter": {"mass": 0.005, "cost": 8800},
    "sensorThermometer": {"mass": 0.005, "cost": 900},
    "sepMotor1": {"mass": 0.0125, "cost": 75},
    "shockConeIntake": {"mass": 0.12, "cost": 3050},
    "smallCargoContainer": {"mass": 0.05, "cost": 200},
    "smallClaw": {"mass": 0.03, "cost": 315},
    "smallCtrlSrf": {"mass": 0.04, "cost": 400},
    "smallHardpoint": {"mass": 0.05, "cost": 60},
    "smallRadialEngine": {"mass": 0.09, "cost": 400},
    "smallRadialEngine_v2": {"mass": 0.08, "cost": 230},
    "solarPanelOX10C": {"mass": 0.09, "cost": 1200},
    "solarPanelOX10L": {"mass": 0.09, "cost": 1200},
    "solarPanelSP10C": {"mass": 0.13, "cost": 1400},
    "solarPanelSP10L": {"mass": 0.13, "cost": 1400},
    "solarPanels1": {"mass": 0.025, "cost": 440},
    "solarPanels2": {"mass": 0.025, "cost": 440},
    "solarPanels3": {"mass": 0.0175, "cost": 380},
    "solarPanels4": {"mass": 0.0175, "cost": 380},
    "solarPanels5": {"mass": 0.005, "cost": 75},
    "solidBooster1-1": {"mass": 1.5, "cost": 850},
    "solidBooster_sm_v2": {"mass": 0.45, "cost": 200},
    "solidBooster_v2": {"mass": 0.75, "cost": 400},
    "spotLight1": {"mass": 0.015, "cost": 100},
    "spotLight1_v2": {"mass": 0.015, "cost": 100},
    "spotLight2": {"mass": 0.015, "cost": 100},
    "spotLight2_v2": {"mass": 0.015, "cost": 100},
    "spotLight3": {"mass": 0.005, "cost": 75},
    "stackBiCoupler_v2": {"mass": 0.1, "cost": 400},
    "stackPoint1": {"mass": 0.04, "cost": 250},
    "stackQuadCoupler": {"mass": 0.175, "cost": 2000},
    "stackTriCoupler_v2": {"mass": 0.15, "cost": 680},
    "standardNoseCone": {"mass": 0.01, "cost": 180},
    "stationHub": {"mass": 1.5, "cost": 900},
    "stripLight1": {"mass": 0.001, "cost": 40},
    "structuralIBeam1": {"mass": 0.08, "cost": 50},
    "structuralIBeam2": {"mass": 0.375, "cost": 25},
    "structuralIBeam3": {"mass": 0.1875, "cost": 14},
    "structuralMiniNode": {"mass": 0.15, "cost": 25},
    "structuralPanel1": {"mass": 0.075, "cost": 30},
    "structuralPanel2": {"mass": 0.3, "cost": 90},
    "structuralPylon": {"mass": 0.2, "cost": 125},
    "structuralWing": {"mass": 0.1, "cost": 500},
    "structuralWing2": {"mass": 0.1, "cost": 500},
    "structuralWing3": {"mass": 0.05, "cost": 300},
    "structuralWing4": {"mass": 0.025, "cost": 150},
    "strutConnector": {"mass": 0.05, "cost": 42},
    "strutCube": {"mass": 0.001, "cost": 16},
    "strutOcto": {"mass": 0.001, "cost": 20},
    "sweptWing": {"mass": 0.275, "cost": 620},
    "sweptWing1": {"mass": 0.113, "cost": 500},
    "sweptWing2": {"mass": 0.226, "cost": 500},
    "tailfin": {"mass": 0.125, "cost": 600},
    "telescopicLadder": {"mass": 0.005, "cost": 350},
    "telescopicLadderBay": {"mass": 0.005, "cost": 440},
    "toroidalAerospike": {"mass": 1.0, "cost": 3850},
    "trussAdapter": {"mass": 0.25, "cost": 50},
    "trussPiece1x": {"mass": 0.125, "cost": 25},
    "trussPiece3x": {"mass": 0.375, "cost": 75},
    "turboFanEngine": {"mass": 1.8, "cost": 2250},
    "turboFanSize2": {"mass": 4.5, "cost": 2600},
    "turboJet": {"mass": 1.2, "cost": 2000},
    "vernierEngine": {"mass": 0.08, "cost": 150},
    "wheelMed": {"mass": 0.105, "cost": 0},
    "wingConnector": {"mass": 0.2, "cost": 500},
    "wingConnector2": {"mass": 0.2, "cost": 500},
    "wingConnector3": {"mass": 0.1, "cost": 250},
    "wingConnector4": {"mass": 0.05, "cost": 100},
    "wingConnector5": {"mass": 0.05, "cost": 100},
    "wingShuttleDelta": {"mass": 0.5, "cost": 3000},
    "wingShuttleElevon1": {"mass": 0.15, "cost": 950},
    "wingShuttleElevon2": {"mass": 0.23, "cost": 1300},
    "wingShuttleRudder": {"mass": 0.45, "cost": 2500},
    "wingShuttleStrake": {"mass": 0.1, "cost": 1000},
    "wingStrake": {"mass": 0.05, "cost": 400},
    "winglet": {"mass": 0.037, "cost": 500},
    "winglet3": {"mass": 0.078, "cost": 600},
    "xenonTank": {"mass": 0.024, "cost": 3680},
    "xenonTankLarge": {"mass": 0.19, "cost": 24300},
    "xenonTankRadial": {"mass": 0.0135, "cost": 2220},
}


def _merge_stock_into_partsdb():
    """Merge _STOCK_PARTS_RAW into PARTS_DB.

    Priority rule:
      - Vanilla cfg mass/cost ALWAYS wins (these are authoritative).
      - Hand-curated PARTS_DB entries contribute their category, which
        drives rule logic (weapon detection, CM box counting, etc.).
      - For parts only in _STOCK_PARTS_RAW (no hand entry), category
        defaults to "unknown" — which means no rule will fire on them,
        but their mass/cost will still be correct.

    This means an outdated hand-curated mass value can NEVER cause an
    incorrect total mass — only stale categories, which fail safe.
    """
    for name, entry in _STOCK_PARTS_RAW.items():
        if name in PARTS_DB:
            # Keep category from hand-curated entry, but use cfg mass/cost.
            existing = PARTS_DB[name]
            PARTS_DB[name] = {
                "mass": entry["mass"],
                "cost": entry["cost"],
                "category": existing.get("category", "unknown"),
            }
        else:
            PARTS_DB[name] = {
                "mass": entry["mass"],
                "cost": entry["cost"],
                "category": "unknown",
            }

_merge_stock_into_partsdb()




def load_parts_override(override_path: Optional[Path] = None) -> int:
    """
    Merge a JSON override file into PARTS_DB so users can fix mass/cost
    for their specific modded install without editing this script.

    Looks for `ksp_parts_override.json` next to this script unless a path
    is given. The JSON should be a single object mapping part base names
    to {"mass": ..., "cost": ..., "category": ...}; missing keys keep
    their existing values.

    Example file contents:
        {
            "MK1Fuselage":   {"mass": 0.20},
            "Mark2Cockpit":  {"mass": 1.80},
            "MyModPart":     {"mass": 0.5, "cost": 1200, "category": "wing"}
        }

    Returns the number of part entries merged.
    """
    if override_path is None:
        override_path = Path(__file__).parent / "ksp_parts_override.json"
    if not override_path.exists():
        return 0
    try:
        data = json.loads(override_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    count = 0
    for part_name, overrides in data.items():
        if not isinstance(overrides, dict):
            continue
        existing = PARTS_DB.get(part_name, {"mass": 0.0, "cost": 0.0,
                                            "category": "unknown"})
        # Merge: override values take precedence; missing keys preserved.
        merged = {**existing, **overrides}
        PARTS_DB[part_name] = merged
        count += 1
    return count


# Module-based detection (more robust than name matching)
# Categories of missile (resolved from engageAir/engageGround/engageSLW flags)
MISSILE_COST_BY_TYPE = {
    "aa":            1.0,
    "aa_cluster":    3.0,
    "ag":            0.5,
    "ag_guided":     0.5,
    "ag_unguided":   0.25,
    "cruise":        2.0,
    "ss":            2.0,
    "arad":          2.0,
    "rocket_pod":    1.0,
    # Special: AGM-122 Side Arm. Technically ARAD, but ruleset grants an
    # exception: base value 1.0 (like AA), but classed as ground-attack
    # munition so the ×2 fighter penalty still applies. Net: 1.0 on
    # attacker, 2.0 on fighter.
    "sidearm":       1.0,
}

# "Dumb" munitions: cheap, unguided, treated identically regardless of
# craft class. The fighter-AG / attacker-AA ×2 penalty does NOT apply
# to these. Per ruleset: a dumb bomb is always 0.25 missile-value,
# a rocket pod is always 1.0, for fighters and attackers alike.
DUMB_MUNITION_TYPES = frozenset({"ag_unguided", "rocket_pod"})


# ---------------------------------------------------------------------------
# Craft parser
# ---------------------------------------------------------------------------

@dataclass
class Part:
    raw_name: str                  # the full token e.g. "bahaAim9_4291644772"
    base_name: str                 # the part type e.g. "bahaAim9"
    modules: list[str] = field(default_factory=list)
    engage_air: bool = False
    engage_ground: bool = False
    engage_missile: bool = False
    engage_slw: bool = False       # Surface-launched (naval) weapons
    has_missile_launcher: bool = False
    has_explosive: bool = False
    has_ecm_jammer: bool = False
    cm_types: list[str] = field(default_factory=list)  # CMChaff, CMFlare, CMDropper
    is_reaction_wheel: bool = False
    is_rcs: bool = False
    has_command: bool = False
    has_command_chair: bool = False
    # BDA gun/rocket detection (from ModuleWeapon).
    # has_gun: a fixed gun (ballistic/laser); counts toward gun cost rule.
    # has_turret: a turreted weapon (any kind); banned on aircraft.
    # Rocket pods (weaponType=rocket) set has_missile_launcher instead so
    # they're counted in the missile budget per the ruleset.
    has_gun: bool = False
    has_turret: bool = False
    # KSP's per-part mass adjustment, written by mods like Tweakscale/robotics.
    # Added to the DB dry mass.
    mod_mass: float = 0.0
    # Resources: list of (resource_name, amount) pairs from RESOURCE blocks.
    # Uses the SAVED amount (what's actually loaded in this craft file),
    # not maxAmount (capacity). So a craft saved with empty fuel tanks
    # will have LiquidFuel amount = 0 here.
    resources: list[tuple[str, float]] = field(default_factory=list)
    # BDA armor / hull data from the HitpointTracker module.
    # SelectedArmorType: "None" or a material name ("Titanium", "Steel", etc.)
    # hull_type: base hull material (always set; "Aluminium" is default)
    # armor_thickness_mm: armor thickness in mm (default 10)
    # armor_density: kg/m³ from the cfg (e.g. Titanium = 4506, Steel = 7850)
    armor_type: str = "None"
    hull_type: str = "Aluminium"
    armor_thickness_mm: float = 10.0
    armor_density: float = 0.0  # kg/m³; 0 means no armor mass

    @property
    def db(self) -> Optional[dict]:
        """Look up the part in PARTS_DB. KSP rewrites underscores in cfg
        names to dots in craft files (e.g. cfg `hinge_01` ↔ craft `hinge.01`,
        cfg `BDA_EJ200` ↔ craft `BDA.EJ200`). Try both forms when looking up
        so the DB only needs one entry per part."""
        entry = PARTS_DB.get(self.base_name)
        if entry is not None:
            return entry
        # Try `.` → `_` (cfg form when craft uses dot)
        if "." in self.base_name:
            entry = PARTS_DB.get(self.base_name.replace(".", "_"))
            if entry is not None:
                return entry
        # Try `_` → `.` (craft form when cfg uses underscore)
        if "_" in self.base_name:
            entry = PARTS_DB.get(self.base_name.replace("_", "."))
            if entry is not None:
                return entry
        return None

    @property
    def base_dry_mass(self) -> float:
        """Structural mass — DB base + modMass.

        Note on buoyancy parts: some mods (KPDynamics Naval, etc.) use a
        large positive cfg mass plus an offsetting negative modMass to
        simulate buoyancy. KSP displays the *net* mass (cfg + modMass),
        which is what we want — so we just add modMass as usual. The
        result for a smallBuoyancy is e.g. 5t + (-4.5t) = 0.5t per part."""
        base = self.db["mass"] if self.db else 0.0
        return base + self.mod_mass

    @property
    def armor_mass(self) -> float:
        """BDA armor mass added by selected armor type.

        Formula: surface_area × thickness × density.
        Surface area is estimated as k × (base_dry_mass)^(2/3) with
        k = ARMOR_AREA_K, calibrated empirically against a Titanium-armored
        Voron (10mm Titanium @ 4506 kg/m³ → 16.2t armor across 85 parts).

        This estimate is exact in total for craft similar to the calibration
        target and within ~10-30% for craft with very different part mixes.
        """
        if self.armor_type == "None" or self.armor_density == 0:
            return 0.0
        if self.base_dry_mass <= 0:
            return 0.0
        area_m2 = ARMOR_AREA_K * (self.base_dry_mass ** (2.0 / 3.0))
        thickness_m = self.armor_thickness_mm / 1000.0
        # density is kg/m³, convert mass to tons
        mass_kg = area_m2 * thickness_m * self.armor_density
        return mass_kg / 1000.0  # tons

    @property
    def dry_mass(self) -> float:
        """Total dry mass — structural + armor."""
        return self.base_dry_mass + self.armor_mass

    @property
    def resource_mass(self) -> float:
        """Mass of resources at their SAVED amount in this .craft file."""
        total = 0.0
        for rname, amt in self.resources:
            density = RESOURCE_DENSITY.get(rname, 0.0)
            total += density * amt
        return total

    @property
    def mass(self) -> float:
        """Loaded mass — structural + resources as saved in the file.
        This matches what KSP shows for the craft in its current loaded state."""
        return self.dry_mass + self.resource_mass

    @property
    def cost(self) -> float:
        return self.db["cost"] if self.db else 0.0

    @property
    def category(self) -> str:
        if self.db:
            return self.db["category"]
        return "unknown"


def fix_countermeasures(craft_text: str, allowed_boxes: int,
                        per_box_cap: int = 42) -> tuple[str, dict]:
    """Rewrite a .craft file's RESOURCE blocks so all CM loads are legal.

    Two-step fix:
      1. Cap each box's CM resource amount at `per_box_cap` (42).
      2. If total CM units still exceed `allowed_boxes × per_box_cap`,
         drain flares first (then chaff, smoke, decoy in that order) from
         the most-loaded boxes downward until total fits.

    The boxes themselves are NOT removed — only their loaded `amount`
    values are reduced. `maxAmount` is left untouched so KSP can refill
    them in-game. Returns the rewritten text and a report dict.
    """
    import re

    CM_RESOURCE_NAMES = ("CMChaff", "CMFlare", "CMSmoke", "CMDecoy")
    DRAIN_ORDER = ("CMFlare", "CMChaff", "CMSmoke", "CMDecoy")

    # Find every RESOURCE { ... } block (top-level OR nested) and collect
    # those whose `name = ` is a CM resource. Track each by its byte span
    # in the file so we can rewrite the `amount = ` line in place.
    cm_blocks = []
    pos = 0
    while True:
        m = re.search(r"\bRESOURCE\s*\{", craft_text[pos:])
        if not m:
            break
        start = pos + m.end()
        depth = 1
        j = start
        while j < len(craft_text) and depth > 0:
            if craft_text[j] == "{": depth += 1
            elif craft_text[j] == "}": depth -= 1
            j += 1
        block = craft_text[start:j-1]
        pos = j

        name_m = re.search(r"^\s*name\s*=\s*(\S+)", block, re.MULTILINE)
        amt_m  = re.search(r"^\s*amount\s*=\s*([0-9.eE+-]+)", block, re.MULTILINE)
        if not name_m or not amt_m:
            continue
        rname = name_m.group(1).strip()
        if rname not in CM_RESOURCE_NAMES:
            continue
        try:
            amount = float(amt_m.group(1))
        except ValueError:
            continue

        # Absolute byte positions for the amount value (so we can substitute)
        amt_abs_start = start + amt_m.start(1)
        amt_abs_end   = start + amt_m.end(1)
        cm_blocks.append({
            "name": rname,
            "amount": amount,
            "amt_start": amt_abs_start,
            "amt_end":   amt_abs_end,
        })

    report = {
        "boxes_capped": 0,
        "drained_units": 0.0,
        "drained_by_type": {n: 0.0 for n in CM_RESOURCE_NAMES},
        "before_total": sum(b["amount"] for b in cm_blocks),
        "after_total": 0.0,
        "allowed_total": allowed_boxes * per_box_cap,
        "edits": [],
    }

    # Step 1: cap each box at per_box_cap.
    for b in cm_blocks:
        if b["amount"] > per_box_cap + 0.001:
            old = b["amount"]
            b["amount"] = float(per_box_cap)
            report["boxes_capped"] += 1
            report["edits"].append(
                f"capped {b['name']} {old:.0f}→{per_box_cap}"
            )

    # Step 2: if total still over, drain in priority order from the
    # most-loaded boxes of each type, one at a time, until under cap.
    total = sum(b["amount"] for b in cm_blocks)
    allowed_total = report["allowed_total"]
    if total > allowed_total + 0.001:
        for drain_type in DRAIN_ORDER:
            if total <= allowed_total + 0.001:
                break
            candidates = [b for b in cm_blocks if b["name"] == drain_type and b["amount"] > 0]
            # Drain most-loaded first
            candidates.sort(key=lambda b: -b["amount"])
            for b in candidates:
                if total <= allowed_total + 0.001:
                    break
                excess = total - allowed_total
                drain = min(b["amount"], excess)
                b["amount"] -= drain
                total -= drain
                report["drained_units"] += drain
                report["drained_by_type"][drain_type] += drain

    report["after_total"] = sum(b["amount"] for b in cm_blocks)

    # Rewrite the text. Edits are byte-position based so we apply
    # from end to start to avoid offset shifts.
    cm_blocks.sort(key=lambda b: -b["amt_start"])
    new_text = craft_text
    for b in cm_blocks:
        old_amt_str = new_text[b["amt_start"]:b["amt_end"]]
        # Format consistently — integer if it's whole, else 6 decimals (KSP style)
        if b["amount"] == int(b["amount"]):
            new_amt_str = str(int(b["amount"]))
        else:
            new_amt_str = f"{b['amount']:.6f}"
        new_text = new_text[:b["amt_start"]] + new_amt_str + new_text[b["amt_end"]:]

    return new_text, report


def remove_parts_from_craft(craft_text: str, raw_names_to_remove: set) -> tuple[str, dict]:
    """Rewrite a .craft file with selected PART blocks removed.

    `raw_names_to_remove` is a set of raw_name strings (e.g.
    "bahaRevolverCannon_4287859754") identifying the exact parts to delete.

    Each PART block in a .craft file looks like:
        PART
        {
            part = <raw_name>
            ...
            link = <other_raw_name>
            sym = <other_raw_name>
            ...
        }
    When a PART is removed we must also scrub any `link = <removed>` and
    `sym = <removed>` lines from surviving PART blocks, otherwise KSP
    errors on load. Returns (new_text, report).
    """
    import re

    report = {
        "removed_count": 0,
        "removed_names": [],
        "scrubbed_links": 0,
        "scrubbed_syms": 0,
    }

    # Walk PART blocks; for each, decide keep or drop.
    new_text_parts = []
    pos = 0
    while True:
        m = re.search(r"\bPART\s*\{", craft_text[pos:])
        if not m:
            # No more PART blocks — keep the rest of the file as-is.
            new_text_parts.append(craft_text[pos:])
            break
        block_outer_start = pos + m.start()
        block_brace_open = pos + m.end()
        # Emit text up to and including "PART {"
        new_text_parts.append(craft_text[pos:block_brace_open])
        depth = 1
        j = block_brace_open
        while j < len(craft_text) and depth > 0:
            if craft_text[j] == "{": depth += 1
            elif craft_text[j] == "}": depth -= 1
            j += 1
        # block contents are craft_text[block_brace_open : j-1] (inside braces)
        # closing brace is at j-1
        block_body = craft_text[block_brace_open:j]  # includes final }
        # Find the raw_name in this PART
        name_m = re.search(r"^\s*part\s*=\s*(\S+)", block_body, re.MULTILINE)
        if name_m and name_m.group(1) in raw_names_to_remove:
            # Drop this PART entirely. We already wrote "PART {" — undo.
            # Pop the last piece (which was "...PART {") and replace with
            # just the text before "PART".
            new_text_parts.pop()
            new_text_parts.append(craft_text[pos:block_outer_start])
            report["removed_count"] += 1
            report["removed_names"].append(name_m.group(1))
        else:
            # Keep this PART. Append body unchanged for now.
            new_text_parts.append(block_body)
        pos = j

    new_text = "".join(new_text_parts)

    # Now scrub link = / sym = references to removed parts.
    for removed in raw_names_to_remove:
        # link = removed_name (whole token, end of line)
        link_pattern = re.compile(
            rf"^[ \t]*link\s*=\s*{re.escape(removed)}\s*\r?\n",
            re.MULTILINE
        )
        new_text, n_links = link_pattern.subn("", new_text)
        report["scrubbed_links"] += n_links

        sym_pattern = re.compile(
            rf"^[ \t]*sym\s*=\s*{re.escape(removed)}\s*\r?\n",
            re.MULTILINE
        )
        new_text, n_syms = sym_pattern.subn("", new_text)
        report["scrubbed_syms"] += n_syms

    return new_text, report


def parse_craft(path: Path) -> tuple[str, list[Part]]:
    """Parse a .craft file into a ship name and list of Part objects."""
    text = path.read_text(encoding="utf-8", errors="replace")

    # Ship name
    name_match = re.search(r"^ship\s*=\s*(.+)$", text, re.MULTILINE)
    ship_name = name_match.group(1).strip() if name_match else path.stem

    parts: list[Part] = []
    # Split into PART blocks. A PART block looks like:
    #   PART
    #   {
    #     part = name_12345
    #     ...
    #     MODULE { ... }
    #     ...
    #   }
    # We use a brace-matching walk because MODULEs nest braces.

    i = 0
    while True:
        # Find the next "PART\n{" — accept whitespace variations
        m = re.search(r"\bPART\s*\{", text[i:])
        if not m:
            break
        start = i + m.end()  # right after the opening brace
        # Walk braces to find matching close
        depth = 1
        j = start
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            j += 1
        block = text[start:j-1]
        i = j

        part = _parse_part_block(block)
        if part:
            parts.append(part)

    return ship_name, parts


def _parse_part_block(block: str) -> Optional[Part]:
    # Top-level part name: the first "part = ..." at indent depth 1
    m = re.search(r"^\s*part\s*=\s*([^\r\n]+)", block, re.MULTILINE)
    if not m:
        return None
    raw_name = m.group(1).strip()
    # strip trailing _<digits> persistent id
    base_name = re.sub(r"_\d+$", "", raw_name)

    part = Part(raw_name=raw_name, base_name=base_name)

    # Read modMass adjustment if present (Tweakscale, robotics, etc. write this)
    mm_match = re.search(r"^\s*modMass\s*=\s*([0-9.eE+-]+)", block, re.MULTILINE)
    if mm_match:
        try:
            part.mod_mass = float(mm_match.group(1))
        except ValueError:
            pass

    # Extract MODULE blocks. They look like:
    #   MODULE
    #   {
    #     name = Foo
    #     ...
    #   }
    # Walk the block linearly and find MODULE { ... } regions
    pos = 0
    while True:
        mm = re.search(r"\bMODULE\s*\{", block[pos:])
        if not mm:
            break
        mstart = pos + mm.end()
        depth = 1
        k = mstart
        while k < len(block) and depth > 0:
            c = block[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            k += 1
        mod_block = block[mstart:k-1]
        pos = k

        _ingest_module(part, mod_block)

    # Extract RESOURCE blocks. Same brace-walk approach.
    # Each RESOURCE has name=... and maxAmount=...; we record (name, maxAmount).
    pos = 0
    while True:
        rm = re.search(r"\bRESOURCE\s*\{", block[pos:])
        if not rm:
            break
        rstart = pos + rm.end()
        depth = 1
        k = rstart
        while k < len(block) and depth > 0:
            c = block[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            k += 1
        res_block = block[rstart:k-1]
        pos = k

        name_m = re.search(r"^\s*name\s*=\s*([^\r\n]+)", res_block, re.MULTILINE)
        amt_m = re.search(r"^\s*amount\s*=\s*([0-9.eE+-]+)", res_block,
                          re.MULTILINE)
        if name_m and amt_m:
            try:
                part.resources.append(
                    (name_m.group(1).strip(), float(amt_m.group(1)))
                )
            except ValueError:
                pass

    return part


def _ingest_module(part: Part, mod_block: str):
    """Read a MODULE block's name + relevant fields and update the Part."""
    name_m = re.search(r"^\s*name\s*=\s*([^\r\n]+)", mod_block, re.MULTILINE)
    if not name_m:
        return
    mod_name = name_m.group(1).strip()
    part.modules.append(mod_name)

    if mod_name == "MissileLauncher":
        part.has_missile_launcher = True
        for key in ("engageAir", "engageGround", "engageMissile", "engageSLW"):
            km = re.search(rf"^\s*{key}\s*=\s*(\w+)", mod_block, re.MULTILINE)
            if km:
                val = km.group(1).strip().lower() == "true"
                setattr(part, _engage_field(key), val)

    elif mod_name == "ModuleWeapon":
        # BDA fixed weapons (guns, lasers, rocket pods) all use ModuleWeapon.
        # The cfg's `weaponType` field discriminates gun-vs-rocket, but KSP
        # does NOT save that field into .craft files — it's only in the cfg.
        # So we fall back on the part's DB category instead:
        #   - category == "rocket_pod"   → missile-budget weapon
        #   - anything else (incl. unknown) → fixed gun (gun cost rule)
        cat = part.db["category"] if part.db else "unknown"
        if cat == "rocket_pod":
            part.has_missile_launcher = True
            # Rocket pods are ground-attack by default; the budget code uses
            # the part's `category` to look up cost, but set the engage
            # flags too so the fallback classifier handles unknowns sanely.
            part.engage_ground = True
        else:
            part.has_gun = True

    elif mod_name == "ModuleTurret":
        # BDA turret module. When present, the part is a turreted weapon
        # of some kind (gun, missile, or rocket). Banned on aircraft.
        part.has_turret = True

    elif mod_name == "BDExplosivePart":
        part.has_explosive = True

    elif mod_name == "ModuleECMJammer":
        part.has_ecm_jammer = True

    elif mod_name in ("CMDropper", "CMChaff", "CMFlare"):
        part.cm_types.append(mod_name)

    elif mod_name == "ModuleReactionWheel":
        part.is_reaction_wheel = True

    elif mod_name == "ModuleRCS" or mod_name == "ModuleRCSFX":
        part.is_rcs = True

    elif mod_name == "ModuleCommand":
        part.has_command = True

    elif mod_name == "KerbalSeat":
        part.has_command_chair = True

    elif mod_name == "HitpointTracker":
        # BDA armor / hull data
        sat = re.search(r"SelectedArmorType\s*=\s*([^\r\n]+)", mod_block)
        if sat:
            part.armor_type = sat.group(1).strip()
        ht = re.search(r"^\s*hullType\s*=\s*([^\r\n]+)", mod_block, re.MULTILINE)
        if ht:
            part.hull_type = ht.group(1).strip()
        at = re.search(r"ArmorThickness\s*=\s*([0-9.eE+-]+)", mod_block)
        if at:
            try:
                part.armor_thickness_mm = float(at.group(1))
            except ValueError:
                pass
        # Density is the armor material's density in kg/m³.
        # Only meaningful when SelectedArmorType is not "None".
        den = re.search(r"^\s*Density\s*=\s*([0-9.eE+-]+)", mod_block, re.MULTILINE)
        if den:
            try:
                part.armor_density = float(den.group(1))
            except ValueError:
                pass


def _engage_field(key: str) -> str:
    return {
        "engageAir": "engage_air",
        "engageGround": "engage_ground",
        "engageMissile": "engage_missile",
        "engageSLW": "engage_slw",
    }[key]


# ---------------------------------------------------------------------------
# Missile classification
# ---------------------------------------------------------------------------

def classify_missile(part: Part) -> str:
    """Return a missile cost-type key for parts that are missiles/bombs/etc."""
    cat = part.category
    # Trust the DB category first if we have it
    if cat == "missile_aa":           return "aa"
    if cat == "missile_aa_cluster":   return "aa_cluster"
    if cat == "missile_ag":           return "ag_guided"
    if cat == "missile_cruise":       return "cruise"
    if cat == "missile_ss":           return "ss"
    if cat == "missile_arad":         return "arad"
    if cat == "missile_sidearm":      return "sidearm"
    if cat == "bomb_unguided":        return "ag_unguided"
    if cat == "bomb_guided":          return "ag_guided"
    if cat == "rocket_pod":           return "rocket_pod"

    # Fallback: use engagement flags
    if part.has_missile_launcher:
        if part.engage_air and not part.engage_ground:
            return "aa"
        if part.engage_ground and not part.engage_air:
            return "ag_guided"
        if part.engage_air and part.engage_ground:
            # Per rules: "set to both air and ground" → AA missile
            return "aa"
    return "unknown_weapon"


def missile_count_value(missile_type: str) -> float:
    return MISSILE_COST_BY_TYPE.get(missile_type, 1.0)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    # Optional: when True, the check did not strictly pass but is in a
    # gray area that may warrant manual review rather than a hard fail.
    # The verdict treats this as a soft fail (⚠), distinct from ✗.
    warning: bool = False


def allowed_missiles_by_mass(dry_mass: float) -> float:
    """
    Per rules:
      - 1 additional missile per 1t between 2t and 10t
      - 1 additional missile per 2t between 10t and 20t
      - 1 additional missile per 4t above 20t
    Interpretation: the allowance starts at 0 missiles at 2t, then accrues.
    """
    if dry_mass <= 2.0:
        return 0.0
    allowed = 0.0
    # 2t -> 10t : 1 per 1t  → up to 8 missiles
    band1 = min(dry_mass, 10.0) - 2.0
    allowed += max(0.0, band1) / 1.0
    if dry_mass <= 10.0:
        return allowed
    # 10t -> 20t : 1 per 2t  → up to 5 more
    band2 = min(dry_mass, 20.0) - 10.0
    allowed += band2 / 2.0
    if dry_mass <= 20.0:
        return allowed
    # 20t+ : 1 per 4t
    band3 = dry_mass - 20.0
    allowed += band3 / 4.0
    return allowed


def allowed_cm_boxes_by_mass(dry_mass: float) -> int:
    """1 box at 0t + 1 per ton, max 12."""
    return min(12, 1 + int(dry_mass))  # 1 baseline + 1 per ton


def allowed_jammers_by_mass(dry_mass: float) -> int:
    """1 at 0t, +1 at 12t, +1 at 36t."""
    n = 1
    if dry_mass >= 12.0:
        n += 1
    if dry_mass >= 36.0:
        n += 1
    return n


def evaluate(parts: list[Part], craft_class: str) -> tuple[list[Check], dict]:
    """Run all checks. Returns (list of checks, summary dict)."""
    checks: list[Check] = []
    summary: dict = {}

    # ---- Identify weapons & jettisonable items ----
    # Submunitions (e.g. CLS rocket projectiles, Hydra APKWS rounds, cluster
    # bomblets) are spawned by a launcher that already pays the missile-budget
    # cost. They appear as separate PART entries with MissileLauncher modules,
    # but per the ruleset they should NOT count again. Filter them here.
    weapon_parts = [p for p in parts
                    if p.has_missile_launcher and p.category != "submunition"]
    weapon_categories = {
        "missile_aa", "missile_aa_cluster", "missile_ag",
        "missile_cruise", "missile_ss", "missile_arad",
        "bomb_unguided", "bomb_guided", "rocket_pod",
    }
    jettisonable_parts = [p for p in parts
                          if (p.has_missile_launcher
                              or p.category in weapon_categories)
                          and p.category != "submunition"]

    # ---- Mass totals ----
    # Wet mass = structural + resources at maxAmount (matches KSP VAB display)
    # Dry mass = structural only
    total_wet_mass = sum(p.mass for p in parts)
    total_dry_mass = sum(p.dry_mass for p in parts)
    # "Combat mass" — wet mass minus jettisonable weapons.
    # This is the figure used for missile/CM/jammer allowances.
    combat_mass = sum(p.mass for p in parts if p not in jettisonable_parts)

    # ---- Calibration offset ----
    # Across every craft tested (plain Voron, Titanium-hull Voron, Titanium-armor
    # Voron, DU-armor Voron, Harraser, Grak — all with varying loadouts), the
    # script's computed mass sat exactly 40 kg below KSP's display, regardless
    # of fuel state, armor type, or craft. This is some constant we're not
    # modeling (probably a small per-craft fixture or rounding in BDA), so we
    # just add it back. Without this fix the script reads ~40 kg light on
    # everything; with it, gaps drop to within a few kg.
    MASS_CALIBRATION_OFFSET = 0.040  # tons
    total_wet_mass += MASS_CALIBRATION_OFFSET
    total_dry_mass += MASS_CALIBRATION_OFFSET
    combat_mass += MASS_CALIBRATION_OFFSET

    summary["total_parts"] = len(parts)
    summary["loaded_mass"] = total_wet_mass
    summary["dry_mass"] = total_dry_mass
    summary["combat_mass"] = combat_mass

    # ---- Rule: part count <= 200 ----
    checks.append(Check(
        name="Part count ≤ 200",
        passed=len(parts) <= 200,
        detail=f"{len(parts)} parts (limit 200; exceptions case-by-case)"
    ))

    # ---- Rule: no RCS thrusters ----
    rcs_parts = [p for p in parts if p.is_rcs]
    checks.append(Check(
        name="No RCS thrusters",
        passed=len(rcs_parts) == 0,
        detail=("None found" if not rcs_parts
                else f"Found {len(rcs_parts)}: " + ", ".join(p.base_name for p in rcs_parts))
    ))

    # ---- Rule: ≤ 1 reaction wheel in atmosphere ----
    # Most cockpits/probe cores have an internal reaction wheel module — flag if >1
    reaction_wheel_parts = [p for p in parts if p.is_reaction_wheel]
    checks.append(Check(
        name="≤ 1 reaction wheel",
        passed=len(reaction_wheel_parts) <= 1,
        detail=f"{len(reaction_wheel_parts)} reaction-wheel modules detected "
               f"(includes cockpit/probe internal wheels)"
    ))

    # ---- Missile loadout: count weapons and classify ----
    classified: dict[str, list[Part]] = {}
    for w in weapon_parts:
        t = classify_missile(w)
        classified.setdefault(t, []).append(w)

    # Apply class-based doubling rules
    # Fighters: ground-attack munitions cost doubled (EXCEPT dumb bombs and
    #   rocket pods, which are flat 0.25/1.0 regardless of class).
    # Bombers/Attackers: air-to-air munitions cost doubled
    missile_total = 0.0
    breakdown_lines = []
    for mtype, mlist in classified.items():
        base_val = missile_count_value(mtype)
        multiplier = 1.0
        # AG-class munitions: anything ground-attack. Fighters pay ×2 on these
        # EXCEPT dumb munitions (see DUMB_MUNITION_TYPES), which are flat.
        is_ag = (mtype in ("ag_guided", "ag_unguided", "cruise", "ss", "arad",
                           "rocket_pod", "sidearm")
                 and mtype not in DUMB_MUNITION_TYPES)
        is_aa = mtype in ("aa", "aa_cluster")
        if craft_class == "fighter" and is_ag:
            multiplier = 2.0
        elif craft_class == "attacker" and is_aa:
            multiplier = 2.0
        per_unit = base_val * multiplier
        subtotal = per_unit * len(mlist)
        missile_total += subtotal
        mult_note = f" ×2 ({craft_class} penalty)" if multiplier != 1.0 else ""
        breakdown_lines.append(
            f"  {mtype}: {len(mlist)} × {base_val}{mult_note} = {subtotal:.2f}"
        )

    # ---- Full weapons inventory (for display) ----
    # Group by base_name across ALL weapons (missiles + guns + ammo boxes).
    # Each item is (display_name, count, classification, value_per_unit_after_multiplier).
    from collections import Counter as _Counter
    missile_names = _Counter(w.base_name for w in weapon_parts)
    gun_parts = [p for p in parts if p.category == "weapon_gun"]
    gun_names = _Counter(p.base_name for p in gun_parts)
    ammo_parts = [p for p in parts if p.category == "ammo"]
    ammo_names = _Counter(p.base_name for p in ammo_parts)

    weapons_inventory = []
    # Missiles/bombs/rockets — list with their missile-budget classification
    for name, count in sorted(missile_names.items()):
        # Find one part of this name to classify
        sample = next(w for w in weapon_parts if w.base_name == name)
        mtype = classify_missile(sample)
        # Was this classified via the DB or guessed from MissileLauncher flags?
        # If the part has no DB entry (mass=0), the classification is a guess.
        is_guess = sample.db is None
        base_val = missile_count_value(mtype)
        multiplier = 1.0
        is_ag = (mtype in ("ag_guided", "ag_unguided", "cruise", "ss", "arad",
                           "rocket_pod", "sidearm")
                 and mtype not in DUMB_MUNITION_TYPES)
        is_aa = mtype in ("aa", "aa_cluster")
        if craft_class == "fighter" and is_ag:
            multiplier = 2.0
        elif craft_class == "attacker" and is_aa:
            multiplier = 2.0
        per_unit = base_val * multiplier
        weapons_inventory.append({
            "kind": "munition",
            "name": name,
            "count": count,
            "type": mtype,
            "is_guess": is_guess,
            "value_each": per_unit,
            "value_total": per_unit * count,
        })
    for name, count in sorted(gun_names.items()):
        weapons_inventory.append({
            "kind": "gun",
            "name": name,
            "count": count,
        })
    for name, count in sorted(ammo_names.items()):
        weapons_inventory.append({
            "kind": "ammo",
            "name": name,
            "count": count,
        })

    allowed_missiles = allowed_missiles_by_mass(combat_mass)
    summary["missile_total"] = missile_total
    summary["allowed_missiles"] = allowed_missiles
    summary["missile_breakdown"] = breakdown_lines
    summary["weapons_inventory"] = weapons_inventory

    checks.append(Check(
        name="Missile loadout within allowance",
        passed=missile_total <= allowed_missiles + 1e-6,
        detail=(f"Total missile value: {missile_total:.2f} | "
                f"Allowed (at {combat_mass:.2f}t combat mass): "
                f"{allowed_missiles:.2f}")
    ))

    # ---- No SAMs ----
    sam_parts = [p for p in weapon_parts
                 if p.has_missile_launcher and p.engage_air and p.engage_slw
                 and not p.engage_ground]
    # Heuristic: SAMs primarily target air from a surface launcher.
    # Better: if the part name says "SAM" or category is sam.
    sam_parts += [p for p in parts if "SAM" in p.base_name.upper()
                  and p not in sam_parts]
    checks.append(Check(
        name="No surface-to-air missiles",
        passed=len(sam_parts) == 0,
        detail=("None detected" if not sam_parts
                else f"Possible SAM: {', '.join(p.base_name for p in sam_parts)}")
    ))

    # ---- Gun rules ----
    # 1. No turrets. Aircraft may only mount fixed (non-turreted) guns.
    #    Detected via ModuleTurret on the part.
    # 2. Total gun cost ≤ 300 funds per ton of combat mass.
    #    Sums the `cost` of every part with category == "weapon_gun".
    #    Ammo, mounts, and missiles are NOT included.
    turret_parts = [p for p in parts if p.has_turret]
    checks.append(Check(
        name="No turreted weapons",
        passed=len(turret_parts) == 0,
        detail=("None detected" if not turret_parts
                else f"Turret(s) found: "
                     f"{', '.join(sorted({p.base_name for p in turret_parts}))}")
    ))

    gun_parts = [p for p in parts if p.category == "weapon_gun"]
    total_gun_cost = sum(p.cost for p in gun_parts)
    gun_cost_per_ton = (total_gun_cost / combat_mass) if combat_mass > 0 else 0.0
    GUN_COST_LIMIT_PER_TON = 300.0
    summary["gun_count"] = len(gun_parts)
    summary["gun_total_cost"] = total_gun_cost
    summary["gun_cost_per_ton"] = gun_cost_per_ton
    summary["gun_cost_limit_per_ton"] = GUN_COST_LIMIT_PER_TON
    checks.append(Check(
        name="Gun cost within 300 funds/ton",
        passed=gun_cost_per_ton <= GUN_COST_LIMIT_PER_TON,
        detail=(f"{len(gun_parts)} gun(s), total cost {total_gun_cost:,.0f} funds, "
                f"combat mass {combat_mass:.2f}t → {gun_cost_per_ton:,.1f} funds/ton "
                f"(limit {GUN_COST_LIMIT_PER_TON:.0f})")
    ))

    # ---- Countermeasure boxes + loaded amounts ----
    # Each CM box has a stock per-box capacity of 42 units; total CM units
    # loaded on the craft must fit within `allowed_boxes × 42`. Three
    # failure modes:
    #   1. Any single box loaded > 42 units (player edited maxAmount)
    #      → ILLEGAL outright.
    #   2. Box COUNT > limit AND total loaded > allowed capacity
    #      → ILLEGAL outright.
    #   3. Box COUNT > limit BUT total loaded ≤ allowed capacity
    #      → ⚠ potentially legal (could fit in fewer boxes; needs review).
    PER_BOX_CAPACITY = 42
    CM_RESOURCE_NAMES = ("CMChaff", "CMFlare", "CMSmoke", "CMDecoy")

    cm_box_parts = [p for p in parts
                    if any(c in p.cm_types for c in ("CMDropper", "CMChaff", "CMFlare"))]
    cm_box_count = len(cm_box_parts)
    allowed_cm = allowed_cm_boxes_by_mass(combat_mass)

    # Sum total CM units across all parts (cap-check sums all resource types).
    total_cm_units = 0.0
    overloaded_boxes = []   # boxes with loaded > 42 in any single resource
    for p in cm_box_parts:
        for rn, amt in p.resources:
            if rn in CM_RESOURCE_NAMES:
                total_cm_units += amt
                if amt > PER_BOX_CAPACITY + 0.001:
                    overloaded_boxes.append((p.base_name, rn, amt))

    allowed_capacity = allowed_cm * PER_BOX_CAPACITY

    summary["cm_box_count"] = cm_box_count
    summary["allowed_cm_boxes"] = allowed_cm
    summary["cm_units_loaded"] = total_cm_units
    summary["cm_units_allowed"] = allowed_capacity
    summary["cm_overloaded_boxes"] = overloaded_boxes

    # Build the verdict
    boxes_over = cm_box_count > allowed_cm
    capacity_over = total_cm_units > allowed_capacity + 0.001
    has_overloaded = bool(overloaded_boxes)

    if has_overloaded:
        # Hard fail: someone edited a box past the 42-unit capacity.
        first = overloaded_boxes[0]
        passed = False
        warning = False
        detail = (f"{cm_box_count} boxes (limit {allowed_cm}; hard cap 12), "
                  f"{total_cm_units:.0f} units loaded — "
                  f"ILLEGAL: {first[0]} carries {first[2]:.0f} {first[1]} "
                  f"(per-box cap is {PER_BOX_CAPACITY}). "
                  f"{len(overloaded_boxes)} overloaded box(es) total.")
    elif boxes_over and capacity_over:
        # Hard fail: too many boxes AND total loadout exceeds what's allowed.
        passed = False
        warning = False
        detail = (f"{cm_box_count} boxes (limit {allowed_cm}; hard cap 12), "
                  f"{total_cm_units:.0f} units loaded "
                  f"(allowed capacity {allowed_capacity:.0f}).")
    elif boxes_over and not capacity_over:
        # Potentially legal: too many boxes, but the LOADED count would fit
        # in the legal number of boxes. Player may have throttled the loadout.
        passed = False
        warning = True
        detail = (f"⚠ POTENTIALLY LEGAL — {cm_box_count} boxes "
                  f"(limit {allowed_cm}; hard cap 12), but only "
                  f"{total_cm_units:.0f} units loaded "
                  f"(within capacity of {allowed_capacity:.0f}). "
                  f"Player has extra empty boxes; needs manual review.")
    else:
        # Both within limits.
        passed = True
        warning = False
        detail = (f"{cm_box_count} boxes (limit {allowed_cm}; hard cap 12), "
                  f"{total_cm_units:.0f} / {allowed_capacity:.0f} units loaded.")

    checks.append(Check(
        name="Countermeasure boxes within allowance",
        passed=passed,
        detail=detail,
        warning=warning,
    ))

    # ---- ECM Jammers ----
    # Only count permanent jammers mounted on the aircraft.
    # Some munitions (e.g. KPDynamics EW glide bombs) carry ModuleECMJammer
    # internally — that's the bomb's payload, not an aircraft jammer.
    # Exclude any part that is also a missile launcher.
    jammer_parts = [p for p in parts
                    if p.has_ecm_jammer and not p.has_missile_launcher]
    allowed_jammers = allowed_jammers_by_mass(combat_mass)
    has_aa_weapon = any(classify_missile(w) in ("aa", "aa_cluster")
                       for w in weapon_parts)
    if has_aa_weapon:
        # Rule: aircraft with AA weapons get no more than 1 jammer
        # AND it's also subject to mass scaling — take the tighter limit
        jammer_limit = min(1, allowed_jammers)
        jammer_rule_note = " (capped at 1 because craft has AA weapons)"
    else:
        jammer_limit = allowed_jammers
        jammer_rule_note = ""
    summary["jammer_count"] = len(jammer_parts)
    summary["jammer_limit"] = jammer_limit
    checks.append(Check(
        name="ECM jammers within allowance",
        passed=len(jammer_parts) <= jammer_limit,
        detail=f"{len(jammer_parts)} jammers (limit {jammer_limit}){jammer_rule_note}"
    ))

    # ---- Unknown parts (informational) ----
    unknown = [p for p in parts if not p.db]
    summary["unknown_parts"] = unknown

    return checks, summary


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
# tkinter is imported lazily inside main() so the parser module can be
# imported in environments where tkinter is unavailable (e.g. headless CI).

def _build_gui():
    import tkinter as tk
    from tkinter import filedialog, ttk, scrolledtext, messagebox

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("MechaGrail — KSP Craft Legality Checker")
            self.geometry("900x720")
            self.minsize(720, 560)

            self.craft_path: Optional[Path] = None
            self.parts: list[Part] = []
            self.ship_name: str = ""
            self._disclaimer_shown: bool = False

            self._build_menu()
            self._build_ui()

        def _build_menu(self):
            """Top menu bar with a reference dropdown for the parts database."""
            menubar = tk.Menu(self)
            ref_menu = tk.Menu(menubar, tearoff=False)
            ref_menu.add_command(label="Weapons by class…",
                                 command=self.show_weapons_reference)
            menubar.add_cascade(label="Reference", menu=ref_menu)
            self.config(menu=menubar)

        def show_weapons_reference(self):
            """Open a window listing every weapon part in PARTS_DB grouped by
            category. Useful for tournament admins auditing what the script
            considers each weapon to be. A class toggle (Fighter / Attacker)
            shows the missile-budget cost of each munition under that class."""
            WEAPON_CATEGORIES = [
                ("Air-to-Air missiles",       "missile_aa"),
                ("AA cluster missiles",       "missile_aa_cluster"),
                ("Air-to-Ground missiles",    "missile_ag"),
                ("Anti-Radiation missiles",   "missile_arad"),
                ("Side Arm (special)",        "missile_sidearm"),
                ("Cruise missiles",           "missile_cruise"),
                ("Surface-to-Surface",        "missile_ss"),
                ("Guided bombs",              "bomb_guided"),
                ("Unguided bombs",            "bomb_unguided"),
                ("Rocket pods",               "rocket_pod"),
                ("Submunitions (no budget)",  "submunition"),
                ("Fixed guns",                "weapon_gun"),
                ("Weapon mounts / rails",     "weapon_mount"),
                ("Ammo boxes",                "ammo"),
                ("Chaff dispensers",          "cm_chaff"),
                ("Flare dispensers",          "cm_flare"),
                ("Decoy dispensers",          "cm_decoy"),
                ("ECM jammers",               "ecm_jammer"),
                ("Radars",                    "radar"),
            ]
            # Map of category → missile-type-string (matches classify_missile)
            CAT_TO_MTYPE = {
                "missile_aa":          "aa",
                "missile_aa_cluster":  "aa_cluster",
                "missile_ag":          "ag_guided",
                "missile_arad":        "arad",
                "missile_sidearm":     "sidearm",
                "missile_cruise":      "cruise",
                "missile_ss":          "ss",
                "bomb_guided":         "ag_guided",
                "bomb_unguided":       "ag_unguided",
                "rocket_pod":          "rocket_pod",
            }

            def ord_cost_for(cat_key: str, craft_class: str) -> Optional[float]:
                """Return the missile-budget cost a part in this category would
                contribute under the given craft_class. Returns None for
                non-munition categories (guns, ammo, CM, etc.)."""
                mtype = CAT_TO_MTYPE.get(cat_key)
                if mtype is None:
                    return None
                base = MISSILE_COST_BY_TYPE.get(mtype, 0.0)
                multiplier = 1.0
                is_ag = (mtype in ("ag_guided", "ag_unguided", "cruise", "ss",
                                   "arad", "rocket_pod", "sidearm")
                         and mtype not in DUMB_MUNITION_TYPES)
                is_aa = mtype in ("aa", "aa_cluster")
                if craft_class == "fighter" and is_ag:
                    multiplier = 2.0
                elif craft_class == "attacker" and is_aa:
                    multiplier = 2.0
                return base * multiplier

            win = tk.Toplevel(self)
            win.title("Weapons reference — PARTS_DB classifications")
            win.geometry("820x620")

            # Top filter bar
            top = ttk.Frame(win, padding=6)
            top.pack(side=tk.TOP, fill=tk.X)
            ttk.Label(top, text="Filter category: ").pack(side=tk.LEFT)
            options = ["All"] + [label for label, _ in WEAPON_CATEGORIES]
            filter_var = tk.StringVar(value="All")
            combo = ttk.Combobox(top, textvariable=filter_var,
                                 values=options, state="readonly", width=28)
            combo.pack(side=tk.LEFT)

            ttk.Label(top, text="  Search: ").pack(side=tk.LEFT)
            search_var = tk.StringVar()
            search_entry = ttk.Entry(top, textvariable=search_var, width=16)
            search_entry.pack(side=tk.LEFT)

            # Class toggle on the right
            ttk.Label(top, text="   View as: ").pack(side=tk.LEFT)
            ref_class_var = tk.StringVar(value="fighter")
            ttk.Radiobutton(top, text="Fighter", variable=ref_class_var,
                            value="fighter").pack(side=tk.LEFT)
            ttk.Radiobutton(top, text="Attacker", variable=ref_class_var,
                            value="attacker").pack(side=tk.LEFT)

            # Table area
            text = scrolledtext.ScrolledText(win, wrap=tk.NONE,
                                             font=("Consolas", 10))
            text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
            text.tag_config("head", font=("Consolas", 11, "bold"))
            text.tag_config("sub", foreground="#666")
            text.tag_config("dim", foreground="#aaa")

            def refresh(*_):
                cat_filter = filter_var.get()
                search = search_var.get().strip().lower()
                craft_class = ref_class_var.get()
                text.config(state=tk.NORMAL)
                text.delete("1.0", tk.END)
                text.insert(tk.END,
                            f"Viewing as: {craft_class.upper()}  "
                            f"(ord. cost reflects class penalty if any)\n\n", "sub")
                shown = 0
                for label, cat_key in WEAPON_CATEGORIES:
                    if cat_filter != "All" and cat_filter != label:
                        continue
                    items = sorted(
                        ((name, e["mass"], e["cost"])
                         for name, e in PARTS_DB.items()
                         if e.get("category") == cat_key
                         and (not search or search in name.lower()))
                    )
                    if not items:
                        continue
                    text.insert(tk.END, f"── {label} ({len(items)}) ──\n", "head")
                    text.insert(tk.END,
                                f"  {'Part name':<32s} {'Mass (t)':>10s} "
                                f"{'Cost':>10s} {'Ord. cost':>12s}\n",
                                "sub")
                    oc = ord_cost_for(cat_key, craft_class)
                    for name, m, c in items:
                        if oc is None:
                            oc_str = "—"
                        else:
                            oc_str = f"{oc:.2f}"
                        text.insert(tk.END,
                                    f"  {name:<32s} {m:>10.4f} {c:>10} "
                                    f"{oc_str:>12s}\n")
                        shown += 1
                    text.insert(tk.END, "\n")
                if shown == 0:
                    text.insert(tk.END, "No parts match the current filter.\n", "sub")
                text.config(state=tk.DISABLED)

            combo.bind("<<ComboboxSelected>>", refresh)
            search_var.trace_add("write", lambda *_: refresh())
            ref_class_var.trace_add("write", lambda *_: refresh())
            refresh()

        def _build_ui(self):
            # Top bar: file picker + class selector + check button
            top = ttk.Frame(self, padding=10)
            top.pack(side=tk.TOP, fill=tk.X)

            ttk.Button(top, text="Open .craft file…",
                       command=self.on_open).pack(side=tk.LEFT)

            ttk.Label(top, text="  Class: ").pack(side=tk.LEFT)
            self.class_var = tk.StringVar(value="fighter")
            ttk.Radiobutton(top, text="Fighter", variable=self.class_var,
                            value="fighter",
                            command=self._maybe_recheck).pack(side=tk.LEFT)
            ttk.Radiobutton(top, text="Attacker / Bomber",
                            variable=self.class_var, value="attacker",
                            command=self._maybe_recheck).pack(side=tk.LEFT)

            ttk.Button(top, text="Re-check",
                       command=self.on_check).pack(side=tk.RIGHT)
            ttk.Button(top, text="Fix craft file",
                       command=self.on_fix_craft).pack(side=tk.RIGHT, padx=(0, 6))

            # File label
            self.file_label = ttk.Label(self, text="No file loaded.",
                                         padding=(10, 0))
            self.file_label.pack(side=tk.TOP, fill=tk.X)

            # Output area
            self.output = scrolledtext.ScrolledText(self, wrap=tk.WORD,
                                                     font=("Consolas", 10))
            self.output.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                             padx=10, pady=(6, 10))
            self.output.tag_config("ok",   foreground="#127a12")
            self.output.tag_config("fail", foreground="#b00020")
            self.output.tag_config("warn", foreground="#a86800")
            self.output.tag_config("head", font=("Consolas", 11, "bold"))
            self.output.tag_config("sub",  font=("Consolas", 10, "bold"))

        # ---- actions ----
        def on_open(self):
            path = filedialog.askopenfilename(
                title="Select KSP .craft file",
                filetypes=[("KSP craft files", "*.craft"), ("All files", "*.*")]
            )
            if not path:
                return
            self.craft_path = Path(path)
            try:
                self.ship_name, self.parts = parse_craft(self.craft_path)
            except Exception as e:
                self.file_label.config(text=f"Failed to parse: {e}")
                return
            self.file_label.config(
                text=f"Loaded: {self.craft_path.name}  —  ship: {self.ship_name}  "
                     f"({len(self.parts)} parts)"
            )
            # Show disclaimer once per session before any analysis is displayed
            self._show_disclaimer_once()
            # Tony, you can't do that.
            self._check_wood()
            self.on_check()

        def _show_disclaimer_once(self):
            """Display the legal disclaimer one time per session. After the
            user acknowledges, the flag is set and subsequent craft loads
            don't repeat it."""
            if self._disclaimer_shown:
                return
            messagebox.showwarning(
                "Disclaimer",
                "Warning: This is a tool to ease checking the legality "
                "of craft. This tool cannot account for drop tanks, part "
                "clipping, or any other building decisions which violate "
                "the rules but are otherwise not easily discernible from "
                "the craft file. All final rulings are at the discretion "
                "of the GM/Mods."
            )
            self._disclaimer_shown = True

        def _check_wood(self):
            """Pop up an angry rejection if any part uses Wood for hull or armor.
            Wood is completely illegal as a hull material per the rules."""
            wood_parts = [
                p for p in self.parts
                if (p.hull_type and "wood" in p.hull_type.lower())
                or (p.armor_type and "wood" in p.armor_type.lower())
            ]
            if wood_parts:
                count = len(wood_parts)
                examples = ", ".join(
                    sorted({p.base_name for p in wood_parts})[:3]
                )
                if len({p.base_name for p in wood_parts}) > 3:
                    examples += ", …"
                messagebox.showerror(
                    "Illegal material",
                    f"Tony, you can't do that.\n\n"
                    f"{count} part(s) use Wood as a hull or armor material, "
                    f"which is completely illegal under the ruleset.\n\n"
                    f"Offending parts: {examples}"
                )

        def _maybe_recheck(self):
            if self.parts:
                self.on_check()

        def on_fix_craft(self):
            """Open the Fix Craft dialog: shows every offending category with
            checkboxes for each individual part, lets the user choose which
            ones to remove, then writes a fixed .craft file.

            A degraded craft is better than a deleted one — players get to
            decide what to sacrifice rather than losing the whole airframe.
            """
            if not self.parts or not self.craft_path:
                messagebox.showinfo("Fix craft file",
                                    "Open a .craft file first.")
                return

            craft_class = self.class_var.get()
            checks, summary = evaluate(self.parts, craft_class)

            # Determine which rules are violated and which parts are offenders
            failed_rule_names = [c.name for c in checks if not c.passed]
            warning_rule_names = [c.name for c in checks if c.warning]

            if not failed_rule_names and not warning_rule_names:
                messagebox.showinfo(
                    "Fix craft file",
                    "This craft already passes all rule checks — "
                    "nothing to fix."
                )
                return

            # Build category lists for the picker
            weapon_cats = {"missile_aa", "missile_aa_cluster", "missile_ag",
                           "missile_cruise", "missile_ss", "missile_arad",
                           "missile_sidearm", "bomb_guided", "bomb_unguided",
                           "rocket_pod"}
            gun_parts   = [p for p in self.parts if p.category == "weapon_gun"]
            missile_parts = [p for p in self.parts
                             if p.has_missile_launcher
                             and p.category != "submunition"]
            cm_box_parts = [p for p in self.parts
                            if any(c in p.cm_types
                                   for c in ("CMDropper", "CMChaff", "CMFlare"))]
            jammer_parts = [p for p in self.parts
                            if p.has_ecm_jammer and not p.has_missile_launcher]

            # Build the dialog
            win = tk.Toplevel(self)
            win.title("Fix craft file")
            win.geometry("760x680")
            win.transient(self)
            win.grab_set()

            # Top: violation summary
            top = ttk.Frame(win, padding=8)
            top.pack(side=tk.TOP, fill=tk.X)
            ttk.Label(top,
                      text=f"Craft: {self.ship_name}",
                      font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
            ttk.Label(top,
                      text=f"Class: {craft_class.title()}  "
                           f"|  Combat mass: {summary['combat_mass']:.2f}t  "
                           f"|  Parts: {len(self.parts)}").pack(anchor=tk.W)
            ttk.Separator(top).pack(fill=tk.X, pady=(6, 0))

            if failed_rule_names or warning_rule_names:
                viol = ttk.Frame(win, padding=(8, 4))
                viol.pack(side=tk.TOP, fill=tk.X)
                ttk.Label(viol, text="Violations:",
                          font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
                for c in checks:
                    if not c.passed:
                        mark = "⚠" if c.warning else "✗"
                        color = "#aa8800" if c.warning else "#cc0000"
                        lbl = ttk.Label(viol, text=f"  {mark} {c.name}: {c.detail}",
                                        foreground=color, wraplength=720)
                        lbl.pack(anchor=tk.W)

            # Middle: scrollable picker
            mid_frame = ttk.LabelFrame(win, text="Select items to remove",
                                       padding=8)
            mid_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                           padx=8, pady=4)

            # Use a Canvas with a scrollbar for an arbitrarily long list
            canvas = tk.Canvas(mid_frame, borderwidth=0, highlightthickness=0)
            scrollbar = ttk.Scrollbar(mid_frame, orient="vertical",
                                       command=canvas.yview)
            scrollable = ttk.Frame(canvas)
            scrollable.bind("<Configure>", lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=scrollable, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            # Holders for checkbox state. Each entry: (part_obj, BooleanVar).
            removal_vars: list[tuple] = []  # [(part, var), ...]
            # CM cap option
            cm_cap_var = tk.BooleanVar(value=False)

            def add_section(parent, title, parts_list, default_state=False):
                if not parts_list:
                    return
                ttk.Label(parent, text=title,
                          font=("Segoe UI", 10, "bold")).pack(
                    anchor=tk.W, pady=(8, 2))
                # Group by base_name
                from collections import defaultdict as _dd
                grouped = _dd(list)
                for p in parts_list:
                    grouped[p.base_name].append(p)
                for base_name in sorted(grouped):
                    instances = grouped[base_name]
                    sample = instances[0]
                    # Header line for this part type
                    cat_label = sample.category
                    mass_each = sample.mass
                    cost_each = sample.cost
                    type_info = f"({cat_label}, {mass_each:.3f}t, {cost_each} funds each)"
                    ttk.Label(parent,
                              text=f"  {len(instances)}× {base_name} {type_info}",
                              foreground="#444").pack(anchor=tk.W)
                    # Checkbox per instance (use ID suffix for clarity)
                    for inst in instances:
                        v = tk.BooleanVar(value=default_state)
                        removal_vars.append((inst, v))
                        suffix = inst.raw_name.split("_")[-1]
                        cb = ttk.Checkbutton(
                            parent,
                            text=f"      Remove {base_name}  (id …{suffix[-6:]})",
                            variable=v,
                        )
                        cb.pack(anchor=tk.W)

            # Section: missiles / bombs / rockets
            if missile_parts:
                add_section(scrollable, "Missiles / Bombs / Rocket pods",
                            missile_parts)

            # Section: guns
            if gun_parts:
                add_section(scrollable, "Fixed guns", gun_parts)

            # Section: CM boxes
            if cm_box_parts:
                add_section(scrollable, "Countermeasure boxes", cm_box_parts)
                # Option: also cap CM amounts (per-box ≤42 + drain total)
                ttk.Separator(scrollable).pack(fill=tk.X, pady=4)
                ttk.Checkbutton(
                    scrollable,
                    text="  Also: cap each CM box at 42 + drain total to "
                         "allowed capacity (drains flares first)",
                    variable=cm_cap_var,
                ).pack(anchor=tk.W)

            # Section: ECM jammers
            if jammer_parts:
                add_section(scrollable, "ECM jammers", jammer_parts)

            # Bottom: action buttons + projection
            bottom = ttk.Frame(win, padding=8)
            bottom.pack(side=tk.BOTTOM, fill=tk.X)

            preview_label = ttk.Label(bottom, text="", wraplength=720,
                                       foreground="#0a6")
            preview_label.pack(anchor=tk.W, pady=(0, 4))

            def projected_summary():
                """Recompute the verdict on the in-memory parts list with the
                selected items removed."""
                to_remove_ids = {p.raw_name for p, v in removal_vars if v.get()}
                remaining = [p for p in self.parts
                             if p.raw_name not in to_remove_ids]
                _, s = evaluate(remaining, craft_class)
                # Build a one-line projection
                missile_line = (f"Missiles: {s['missile_total']:.2f}/"
                                f"{s['allowed_missiles']:.2f}")
                gun_line = (f"Gun: {s['gun_cost_per_ton']:.0f}/t "
                            f"(limit 300)")
                cm_line = (f"CMs: {s['cm_box_count']}/{s['allowed_cm_boxes']} "
                           f"boxes, {s['cm_units_loaded']:.0f}/"
                           f"{s['cm_units_allowed']:.0f} units")
                preview_label.config(
                    text=f"Projection after removals: {missile_line}  |  "
                         f"{gun_line}  |  {cm_line}"
                )

            def on_recompute():
                projected_summary()

            def on_apply():
                # Collect raw names to remove
                to_remove_ids = {p.raw_name for p, v in removal_vars if v.get()}

                # Read craft text fresh from disk so we don't compound edits
                try:
                    text = self.craft_path.read_text(encoding="utf-8",
                                                      errors="replace")
                except Exception as e:
                    messagebox.showerror("Fix craft file",
                                         f"Could not read craft file:\n{e}")
                    return

                # Step 1: remove parts (if any)
                rem_report = {"removed_count": 0, "scrubbed_links": 0,
                              "scrubbed_syms": 0}
                if to_remove_ids:
                    text, rem_report = remove_parts_from_craft(text,
                                                                to_remove_ids)

                # Step 2: cap CMs if requested. Need allowed_boxes for the
                # POST-removal craft (since removing CM boxes changes count).
                cm_report = {"boxes_capped": 0, "drained_units": 0.0,
                             "before_total": 0.0, "after_total": 0.0,
                             "allowed_total": 0}
                if cm_cap_var.get():
                    # Recompute allowed_boxes from post-removal craft
                    tmp_path = Path("/tmp/__mechagrail_intermediate.craft")
                    tmp_path.write_text(text, encoding="utf-8")
                    try:
                        _, tmp_parts = parse_craft(tmp_path)
                        _, tmp_sum = evaluate(tmp_parts, craft_class)
                        allowed_boxes = tmp_sum["allowed_cm_boxes"]
                    except Exception:
                        allowed_boxes = summary["allowed_cm_boxes"]
                    text, cm_report = fix_countermeasures(text, allowed_boxes)

                if (rem_report["removed_count"] == 0
                        and cm_report["boxes_capped"] == 0
                        and cm_report["drained_units"] == 0):
                    messagebox.showinfo(
                        "Fix craft file",
                        "Nothing selected to fix."
                    )
                    return

                # Ask: overwrite or new save?
                summary_lines = [
                    f"Applied fixes:",
                    f"  Parts removed:       {rem_report['removed_count']}",
                    f"  Link refs scrubbed:  {rem_report['scrubbed_links']}",
                    f"  Sym refs scrubbed:   {rem_report['scrubbed_syms']}",
                    f"  CM boxes capped:     {cm_report['boxes_capped']}",
                    f"  CM units drained:    {cm_report['drained_units']:.0f}",
                    "",
                    "Do you want to overwrite the file? No makes a new save.",
                ]
                choice = messagebox.askyesnocancel(
                    "Fix craft file",
                    "\n".join(summary_lines),
                    parent=win,
                )
                if choice is None:
                    return  # Cancel — don't write anything
                if choice:
                    out_path = self.craft_path
                else:
                    out_path = self.craft_path.with_name(
                        self.craft_path.stem + "_fixed"
                        + self.craft_path.suffix
                    )
                try:
                    out_path.write_text(text, encoding="utf-8")
                except Exception as e:
                    messagebox.showerror("Fix craft file",
                                         f"Could not write fixed file:\n{e}")
                    return

                action = "Overwrote" if out_path == self.craft_path else "Saved"
                messagebox.showinfo("Fix craft file",
                                    f"{action}: {out_path.name}",
                                    parent=win)

                # Reload + re-check if we overwrote the loaded file
                if out_path == self.craft_path:
                    try:
                        self.ship_name, self.parts = parse_craft(self.craft_path)
                        self.on_check()
                    except Exception:
                        pass
                win.destroy()

            btn_bar = ttk.Frame(bottom)
            btn_bar.pack(anchor=tk.E)
            ttk.Button(btn_bar, text="Preview verdict",
                       command=on_recompute).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_bar, text="Apply",
                       command=on_apply).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_bar, text="Cancel",
                       command=win.destroy).pack(side=tk.LEFT, padx=4)

            # Compute initial projection (with nothing selected)
            projected_summary()

        def on_fix_cms(self):
            """Cap each CM box's loaded amount at 42 and, if the total still
            exceeds allowed_boxes × 42, drain flares first (then chaff, smoke,
            decoy) until under cap. Saves a copy with '_fixed.craft' suffix."""
            if not self.parts or not self.craft_path:
                messagebox.showinfo("Fix CMs",
                                    "Open a .craft file first.")
                return

            # Get the current allowed_boxes from the latest evaluation
            _, summary = evaluate(self.parts, self.class_var.get())
            allowed_boxes = summary["allowed_cm_boxes"]

            # Load raw text and run the fixer
            try:
                original_text = self.craft_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception as e:
                messagebox.showerror("Fix CMs",
                                     f"Could not read craft file:\n{e}")
                return

            new_text, report = fix_countermeasures(original_text, allowed_boxes)

            if (report["boxes_capped"] == 0
                    and report["drained_units"] == 0):
                messagebox.showinfo(
                    "Fix CMs",
                    f"Nothing to fix.\n\n"
                    f"All CM boxes already ≤ 42 units, and total of "
                    f"{report['before_total']:.0f} fits within "
                    f"{report['allowed_total']:.0f} allowed."
                )
                return

            # Ask whether to overwrite the original or save a copy.
            # Yes = overwrite the original; No = save copy with _fixed suffix;
            # Cancel = abort.
            preview_lines = [
                f"Found CM issues to fix:",
                f"  Boxes capped to 42:  {report['boxes_capped']}",
                f"  Units drained:       {report['drained_units']:.0f}",
                f"  Total: {report['before_total']:.0f} → "
                f"{report['after_total']:.0f} (cap {report['allowed_total']:.0f})",
                "",
                f"Do you want to overwrite the file? No makes a new save.",
            ]
            choice = messagebox.askyesnocancel(
                "Fix CMs",
                "\n".join(preview_lines)
            )
            if choice is None:
                return  # Cancel
            if choice:
                out_path = self.craft_path  # overwrite
            else:
                out_path = self.craft_path.with_name(
                    self.craft_path.stem + "_fixed" + self.craft_path.suffix
                )

            try:
                out_path.write_text(new_text, encoding="utf-8")
            except Exception as e:
                messagebox.showerror("Fix CMs",
                                     f"Could not write fixed file:\n{e}")
                return

            # If we overwrote, reload the in-memory parts so subsequent
            # re-checks see the fixed state.
            if out_path == self.craft_path:
                try:
                    self.ship_name, self.parts = parse_craft(self.craft_path)
                    self.on_check()
                except Exception:
                    pass

            # Build a friendly summary of what was changed
            action = "Overwrote" if out_path == self.craft_path else "Saved"
            lines = [
                f"{action}: {out_path.name}",
                "",
                f"Boxes capped to 42:   {report['boxes_capped']}",
                f"Total units drained:  {report['drained_units']:.0f}",
            ]
            drained_breakdown = [
                f"  {n}: {amt:.0f}"
                for n, amt in report['drained_by_type'].items()
                if amt > 0
            ]
            if drained_breakdown:
                lines.append("Drained breakdown:")
                lines.extend(drained_breakdown)
            lines.extend([
                "",
                f"Total CM units: {report['before_total']:.0f} "
                f"→ {report['after_total']:.0f} "
                f"(cap {report['allowed_total']:.0f})",
            ])
            messagebox.showinfo("Fix CMs", "\n".join(lines))

        def on_check(self):
            self.output.delete("1.0", tk.END)
            if not self.parts:
                self._write("No craft loaded. Open a .craft file first.\n", "warn")
                return

            checks, summary = evaluate(self.parts, self.class_var.get())

            # Header
            self._write(f"Craft: {self.ship_name}\n", "head")
            self._write(f"Class: {self.class_var.get().title()}\n\n")

            # Summary
            self._write("── Summary ──\n", "sub")
            self._write(f"Parts:                    {summary['total_parts']}\n")
            self._write(f"Loaded mass (as saved):   {summary['loaded_mass']:.3f} t\n")
            self._write(f"Combat mass (no weapons): {summary['combat_mass']:.3f} t\n")
            self._write(f"Dry mass (no resources):  {summary['dry_mass']:.3f} t\n\n")

            # Weapons inventory
            inv = summary.get("weapons_inventory", [])
            munitions = [w for w in inv if w["kind"] == "munition"]
            guns      = [w for w in inv if w["kind"] == "gun"]
            ammo      = [w for w in inv if w["kind"] == "ammo"]

            if munitions or guns or ammo:
                self._write("── Weapons aboard ──\n", "sub")
                if munitions:
                    for w in munitions:
                        guess_tag = " ⚠ best-guess" if w.get("is_guess") else ""
                        line = (f"  {w['count']}× {w['name']:<28s} "
                                f"({w['type']:<11s}) "
                                f"= {w['value_total']:.2f} missile-value"
                                f"{guess_tag}\n")
                        # Color whole line yellow if this is a guess
                        self._write(line, "warn" if w.get("is_guess") else None)
                if guns:
                    for w in guns:
                        self._write(f"  {w['count']}× {w['name']:<28s} (gun)\n")
                if ammo:
                    for w in ammo:
                        self._write(f"  {w['count']}× {w['name']:<28s} (ammo box)\n")
                # Footer if any guesses were made
                if any(w.get("is_guess") for w in munitions):
                    self._write(
                        "  ⚠  One or more weapons aren't in the parts database. "
                        "Type and count are guessed from BDA module flags; "
                        "mass is treated as 0 t. Add the part to the override "
                        "file or PARTS_DB for accurate accounting.\n",
                        "warn"
                    )
                self._write("\n")

            # Missile budget rollup (compact, after the inventory)
            if summary["missile_breakdown"]:
                self._write("── Missile budget ──\n", "sub")
                for line in summary["missile_breakdown"]:
                    self._write(line + "\n")
                self._write(
                    f"  TOTAL: {summary['missile_total']:.2f}  /  "
                    f"allowed {summary['allowed_missiles']:.2f}\n\n"
                )

            # Countermeasure/jammer info
            self._write("── Defensive systems ──\n", "sub")
            self._write(f"Countermeasure boxes:  {summary['cm_box_count']}  /  "
                        f"{summary['allowed_cm_boxes']}\n")
            self._write(f"ECM jammers:           {summary['jammer_count']}  /  "
                        f"{summary['jammer_limit']}\n\n")

            # Checks
            self._write("── Rule checks ──\n", "sub")
            fails = 0
            warnings = 0
            for c in checks:
                if c.passed:
                    mark, tag = "✓", "ok"
                elif c.warning:
                    mark, tag = "⚠", "warn"
                    warnings += 1
                else:
                    mark, tag = "✗", "fail"
                    fails += 1
                self._write(f"  {mark} {c.name}\n", tag)
                self._write(f"      {c.detail}\n")
            self._write("\n")

            # Verdict
            if fails == 0 and warnings == 0:
                self._write("VERDICT: LEGAL\n", "ok")
            elif fails == 0 and warnings > 0:
                self._write(f"VERDICT: POTENTIALLY LEGAL — {warnings} "
                            f"item{'s' if warnings != 1 else ''} need"
                            f"{'' if warnings != 1 else 's'} manual review\n",
                            "warn")
            else:
                self._write(f"VERDICT: ILLEGAL — {fails} rule violation"
                            f"{'s' if fails != 1 else ''}", "fail")
                if warnings:
                    self._write(f" (+{warnings} warning"
                                f"{'s' if warnings != 1 else ''})", "warn")
                self._write("\n", "fail")

            # Unknown parts (informational)
            if summary["unknown_parts"]:
                self._write("\n── Unknown parts (not in DB; mass/cost treated as 0) ──\n",
                            "warn")
                names = sorted({p.base_name for p in summary["unknown_parts"]})
                for n in names:
                    self._write(f"  • {n}\n", "warn")
                self._write("\nAdd these to PARTS_DB to get accurate mass/cost.\n",
                            "warn")

        def _write(self, text: str, tag: Optional[str] = None):
            if tag:
                self.output.insert(tk.END, text, tag)
            else:
                self.output.insert(tk.END, text)

    return App


def main():
    n_overrides = load_parts_override()
    AppCls = _build_gui()
    app = AppCls()
    if n_overrides:
        app.file_label.config(
            text=f"Loaded {n_overrides} part override(s) from ksp_parts_override.json"
        )
    app.mainloop()


if __name__ == "__main__":
    main()
