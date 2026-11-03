#!/usr/bin/env python3
"""Green Vehicle Guide CSV import: row choice, the car's new entry, and the file written. Made-up CSV in the guide's real header layout."""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools"))
from lib import gvg_import as G  # noqa: E402

HEADER = ["ModelReleaseYear", "Make", "ModelReleaseVersion", "Model", "Variant", "EngineDisplacement", "EngineConfiguration",
          "EngineInduction", "FwdGearsNo", "TransmissionTypeDescription", "SideDoorNo", "SeatingCapacity", "BodyStyle",
          "DrivingWheelsNo", "FuelType", "CO2EmissionsCombined", "CO2EmissionsUrban", "CO2EmissionsExtraUrban",
          "FuelConsumptionCombined", "FuelConsumptionUrban", "FuelConsumptionExtraUrban", "EnergyConsumptionWhkm",
          "ElectricRangeKm", "AirPollutionStandard", "StationaryNoiseData", "TestSpeed", "IsCurrentModel", "ModelEndYear",
          "FuelLifeCycleCO2", "AnnualTailpipeCO2", "Test Cycle", "AnnualFuelCost"]


def row(year, make, model, variant, disp, fuel, wheels, comb, urb, ext, co2, current="Yes", trans="Auto"):
    d = {h: "" for h in HEADER}
    d.update(ModelReleaseYear=year, Make=make, Model=model, Variant=variant, EngineDisplacement=disp, FuelType=fuel,
             DrivingWheelsNo=wheels, FuelConsumptionCombined=comb, FuelConsumptionUrban=urb, FuelConsumptionExtraUrban=ext,
             CO2EmissionsCombined=co2, IsCurrentModel=current, TransmissionTypeDescription=trans, AnnualFuelCost="1344")
    return d


ROWS = [
    row("2018", "Mazda", "CX-8", "AWD", "2.2", "Diesel", "4", "6", "6.9", "5.5", "158"),
    row("2023", "Toyota", "Corolla", "Ascent Hybrid", "1.8", "Petrol Hybrid", "2", "3.5", "3.4", "3.6", "80"),
    row("2023", "Toyota", "Corolla", "ZR Hybrid", "1.8", "Petrol Hybrid", "2", "3.7", "3.6", "3.8", "85"),
    row("2022", "Toyota", "Corolla", "Old Hybrid", "1.8", "Petrol Hybrid", "2", "4.0", "4.0", "4.0", "90"),
    row("2023", "Toyota", "Corolla", "Ascent Sport", "2.0", "Premium Unleaded", "2", "6.0", "7.0", "5.4", "139"),
    row("2023", "Toyota", "Corolla Cross", "GX", "2.0", "Premium Unleaded", "2", "6.1", "7.1", "5.5", "140"),
    row("2023", "Toyota", "Corolla", "Two gearboxes A", "1.5", "Petrol", "2", "5.0", "5.0", "5.0", "115"),
    row("2023", "Toyota", "Corolla", "Same figures B", "1.5", "Petrol", "2", "5.0", "5.0", "5.0", "115"),
    row("2024", "Volkswagen", "Golf", "GTI", "2.0", "Premium Unleaded", "2", "7.2", "9.0", "6.0", "165"),
    row("2024", "Volkswagen", "Golf", "110TSI", "2.0", "Premium Unleaded", "2", "5.9", "7.0", "5.2", "135"),
    row("2019", "Toyota", "Landcruiser Prado", "GXL", "2.8", "Diesel", "4", "8.0", "9.0", "7.4", "211", current="No"),
    row("2021", "Toyota", "Landcruiser Prado", "GXL", "2.8", "Diesel", "4", "7.9", "9.1", "7.3", "208", current="No"),
]

RULE_HYBRID = dict(make="Toyota", model=["Corolla"], disp=1.8, fuel="hybrid")


class ChoiceTest(unittest.TestCase):
    def test_one_row_is_chosen(self):
        r, c, note = G.choose(ROWS, G.MATCH["mazda_cx8_diesel"])
        self.assertEqual((r["Variant"], len(c)), ("AWD", 1))

    def test_different_rows_are_not_guessed(self):
        r, c, note = G.choose(ROWS, RULE_HYBRID)
        self.assertIsNone(r)
        self.assertEqual(len(c), 2)                      # newest year only; the 2022 row is not offered
        self.assertIn("different", note)

    def test_pick_chooses_one(self):
        r, c, note = G.choose(ROWS, RULE_HYBRID, pick=2)
        self.assertEqual(r["Variant"], "ZR Hybrid")
        self.assertIsNone(G.choose(ROWS, RULE_HYBRID, pick=3)[0])

    def test_same_figures_need_no_pick(self):
        rule = dict(make="Toyota", model=["Corolla"], disp=1.5, fuel="petrol")
        r, c, note = G.choose(ROWS, rule)
        self.assertIsNotNone(r)
        self.assertEqual(len(c), 2)

    def test_model_must_match_whole_name(self):
        rule = dict(make="Toyota", model=["Corolla"], disp=2.0, fuel="petrol")
        self.assertEqual([r["Variant"] for r in G.candidates(ROWS, rule)], ["Ascent Sport"])     # not the Corolla Cross

    def test_contains_model_and_variant_text(self):
        self.assertEqual(G.choose(ROWS, G.MATCH["golf_gti_p98"])[0]["Variant"], "GTI")
        self.assertEqual(G.choose(ROWS, G.MATCH["prado_diesel"])[0]["ModelReleaseYear"], "2021")   # none current: newest year

    def test_no_row(self):
        self.assertEqual(G.choose(ROWS, G.MATCH["camry_hybrid"])[2], "no row")

    def test_row_without_a_figure_is_skipped(self):
        bad = [row("2024", "Kia", "Carnival", "S", "2.2", "Diesel", "2", "", "", "", "")]
        self.assertEqual(G.candidates(bad, G.MATCH["kia_carnival_diesel"]), [])

    def test_fuel_class(self):
        self.assertEqual([G.fuel_class(x) for x in ("Diesel", "Petrol Hybrid", "Premium Unleaded", "Electric", "LPG")],
                         ["diesel", "hybrid", "petrol", "hybrid", "other"])


class ApplyTest(unittest.TestCase):
    SPEC = dict(name="Mazda CX-8", fuel_type="Diesel", co2_g_per_km=1, consumption=dict(combined_l_per_100km=7.5),
                running_costs=dict(tyres_per_km=0.026))

    def test_figures_replaced_costs_kept(self):
        r = ROWS[0]
        r = dict(r, _file="GVG-SearchResults-20261103203613.csv", _date="2026-11-03")
        new = G.apply_row(self.SPEC, r)
        self.assertEqual(new["adr_consumption"], dict(combined_l_per_100km=6.0, urban_l_per_100km=6.9, highway_l_per_100km=5.5))
        self.assertEqual(new["consumption"], dict(combined_l_per_100km=7.5))                    # the lab figure does not replace it
        self.assertEqual(G.apply_row(self.SPEC, r, use_label=True)["consumption"], new["adr_consumption"])
        self.assertEqual((new["co2_g_per_km"], new["fuel_type"], new["running_costs"]), (158, "Diesel", dict(tyres_per_km=0.026)))
        self.assertEqual((new["gvg"]["downloaded"], new["gvg"]["variant"]), ("2026-11-03", "AWD"))
        self.assertEqual(self.SPEC["consumption"], dict(combined_l_per_100km=7.5))                # the original is untouched


class FilesTest(unittest.TestCase):
    def test_read_find_and_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "GVG-SearchResults-20261103203613.csv")
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=HEADER, quoting=csv.QUOTE_ALL)
                w.writeheader()
                w.writerows(ROWS[:2])
            with open(os.path.join(d, "other.csv"), "w") as f:
                f.write("a,b\n1,2\n")
            self.assertEqual(G.find_files([], None, downloads=d), [p])                          # default: GVG-*.csv only
            self.assertEqual(len(G.find_files([d])), 2)                                         # a named folder: every csv
            rows = G.read_rows(G.find_files([d]))                                               # other.csv has no guide columns
            self.assertEqual((len(rows), rows[0]["_date"]), (2, "2026-11-03"))
            out = os.path.join(d, "comparison_vehicles.json")
            with open(out, "w") as f:
                json.dump({"default_vehicle": "x", "vehicles": {"keep": {"a": 1}}}, f)
            G.merge_into_file(out, {"new": {"b": 2}})
            with open(out) as f:
                data = json.load(f)
            self.assertEqual((data["default_vehicle"], sorted(data["vehicles"])), ("x", ["keep", "new"]))

    def test_owner_rules_change_a_rule(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "gvg_matches.json"), "w") as f:
                json.dump({"corolla_hybrid": {"variant": "ZR"}}, f)
            rules = G.load_rules(d)
            self.assertEqual((rules["corolla_hybrid"]["variant"], rules["corolla_hybrid"]["disp"]), ("ZR", 1.8))
            self.assertNotIn("variant", G.MATCH["corolla_hybrid"])


class CommandTest(unittest.TestCase):
    def test_dry_run_then_write(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "commands"))
        import import_gvg_fuel_economy as C
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "GVG-SearchResults-20261103203613.csv")
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=HEADER, quoting=csv.QUOTE_ALL)
                w.writeheader()
                w.writerows(ROWS)
            lines = []
            done, left = C.run([p], d, write=False, out=lines.append)
            self.assertIn("mazda_cx8_diesel", done)
            self.assertIn("corolla_hybrid", left)                                               # two different rows
            self.assertFalse(os.path.exists(os.path.join(d, "comparison_vehicles.json")))      # dry run saves nothing
            self.assertTrue(any("Nothing saved" in x for x in lines))
            done, left = C.run([p], d, write=True, picks={"corolla_hybrid": 1}, out=lines.append)
            self.assertIn("corolla_hybrid", done)
            with open(os.path.join(d, "comparison_vehicles.json")) as f:
                data = json.load(f)["vehicles"]
            self.assertEqual(data["mazda_cx8_diesel"]["adr_consumption"]["combined_l_per_100km"], 6.0)
            self.assertEqual(data["mazda_cx8_diesel"]["consumption"]["combined_l_per_100km"], 7.5)    # typed figure kept
            self.assertEqual(data["corolla_hybrid"]["adr_consumption"]["combined_l_per_100km"], 3.5)
            self.assertIn("running_costs", data["mazda_cx8_diesel"])


if __name__ == "__main__":
    unittest.main()
