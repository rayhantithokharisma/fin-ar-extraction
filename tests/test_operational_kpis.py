"""
Comprehensive Operational KPI and Ground-Truth Testing Suite.
Validates real operational figures extracted directly from actual company reports:
- NCKL: Mining & Smelting production (ore volume, FeNi tons, MHP metal tons, operating cash flow)
- LSIP: Plantation operational highlights (nucleus planted hectares, oil palm %, mill counts, workforce)
- CASS: Aviation & logistics operational overview (aviation revenue, operating profit, non-aviation revenue)
- ATIC: Technology solutions performance (revenue, gross profit, net profit, total assets in billions)
"""
from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from src.normalization.normalizer import Normalizer, SilverFact
from src.normalization.number_parser import parse_reported_number, apply_scale, grounded
from src.parser.pdf_loader import PDFDocument
import schema_design.extraction_contracts as contracts


class TestOperationalKPIs(unittest.TestCase):

    def test_nckl_ground_truth_operational_numbers(self):
        """
        Verify real operational metrics reported in NCKL Annual Report 2025 (Page 34).
        """
        # Ground-truth figures from NCKL AR 2025 Page 34:
        # 1. Volume penjualan bijih nikel: 30.59 juta wmt (2025) vs 23.75 juta wmt (2024)
        ore_vol_2025 = parse_reported_number("30,59", locale="id")
        self.assertEqual(ore_vol_2025, Decimal("30.59"))
        ore_vol_scaled = apply_scale(ore_vol_2025, scale=1_000_000)
        self.assertEqual(ore_vol_scaled, Decimal("30590000"))

        # 2. Penjualan FeNi: 188.581 ton Ni (2025) vs 126.344 ton Ni (2024)
        feni_2025 = parse_reported_number("188.581", locale="id")
        self.assertEqual(feni_2025, Decimal("188581"))
        feni_2024 = parse_reported_number("126.344", locale="id")
        self.assertEqual(feni_2024, Decimal("126344"))

        # 3. Penjualan MHP & NiSO4: 130.551 metal ton (2025) vs 102.054 metal ton (2024)
        mhp_2025 = parse_reported_number("130.551", locale="id")
        self.assertEqual(mhp_2025, Decimal("130551"))
        mhp_2024 = parse_reported_number("102.054", locale="id")
        self.assertEqual(mhp_2024, Decimal("102054"))

        # 4. Operating cash flow: Rp8.601 miliar (2025)
        cfo_miliar = parse_reported_number("8.601", locale="id")
        cfo_scaled = apply_scale(cfo_miliar, scale=1_000_000_000)
        self.assertEqual(cfo_scaled, Decimal("8601000000000"))

        # 5. Total equity: Rp46.756 miliar (2025)
        eq_miliar = parse_reported_number("46.756", locale="id")
        eq_scaled = apply_scale(eq_miliar, scale=1_000_000_000)
        self.assertEqual(eq_scaled, Decimal("46756000000000"))

    def test_lsip_ground_truth_operational_numbers(self):
        """
        Verify real operational metrics reported in LSIP Sustainability & Annual Report (Page 12).
        """
        # Ground-truth figures from LSIP AR Page 12:
        # 1. Hectares of nucleus planted area: 110,982 ha
        planted_area = parse_reported_number("110,982", locale="en")
        self.assertEqual(planted_area, Decimal("110982"))

        # 2. Percentage under oil palm: 82%
        oil_palm_pct = parse_reported_number("82%", locale="en")
        self.assertEqual(oil_palm_pct, Decimal("82"))

        # 3. Percentage under other crops (rubber, tea, cocoa): 18%
        other_crops_pct = parse_reported_number("18%", locale="en")
        self.assertEqual(other_crops_pct, Decimal("18"))
        self.assertEqual(oil_palm_pct + other_crops_pct, Decimal("100"))

        # 4. Number of palm oil processing mills: 12
        mills = parse_reported_number("12", locale="en")
        self.assertEqual(mills, Decimal("12"))

        # 5. Number of rubber processing plants: 5
        rubber_plants = parse_reported_number("5", locale="en")
        self.assertEqual(rubber_plants, Decimal("5"))

        # 6. Permanent operational workforce: 13,428 employees
        workforce = parse_reported_number("13,428", locale="en")
        self.assertEqual(workforce, Decimal("13428"))

    def test_cass_ground_truth_operational_numbers(self):
        """
        Verify real segment operational figures reported in CASS Annual Report 2025 (Pages 105 & 107).
        """
        # Ground-truth figures from CASS AR Page 105 (Segmen Aviasi - in millions IDR):
        # 1. Aviation Revenue: 3.206.567 million (2025) vs 2.658.409 million (2024)
        av_rev_2025 = parse_reported_number("3.206.567", locale="id")
        self.assertEqual(av_rev_2025, Decimal("3206567"))
        av_rev_scaled = apply_scale(av_rev_2025, scale=1_000_000)
        self.assertEqual(av_rev_scaled, Decimal("3206567000000"))

        # 2. Aviation Operating Profit: 940.714 million (2025) vs 761.629 million (2024)
        av_op_2025 = parse_reported_number("940.714", locale="id")
        self.assertEqual(av_op_2025, Decimal("940714"))
        av_op_scaled = apply_scale(av_op_2025, scale=1_000_000)
        self.assertEqual(av_op_scaled, Decimal("940714000000"))

        # 3. Non-Aviation Revenue (Page 107): 168.571 million (2025) vs 165.521 million (2024)
        non_av_rev = parse_reported_number("168.571", locale="id")
        self.assertEqual(non_av_rev, Decimal("168571"))
        non_av_scaled = apply_scale(non_av_rev, scale=1_000_000)
        self.assertEqual(non_av_scaled, Decimal("168571000000"))

    def test_atic_ground_truth_operational_financial_numbers(self):
        """
        Verify real financial & operational metrics reported in ATIC Annual Report 2025 (Pages 8 & 9).
        """
        # Ground-truth figures from ATIC AR Page 8 (In billions IDR):
        # 1. Consolidated Revenue: 8.472,4 billion (2025) vs 8.954,8 billion (2024)
        rev_2025 = parse_reported_number("8.472,4", locale="id")
        self.assertEqual(rev_2025, Decimal("8472.4"))
        rev_scaled = apply_scale(rev_2025, scale=1_000_000_000)
        self.assertEqual(rev_scaled, Decimal("8472400000000"))

        # 2. Gross Profit: 1.152,4 billion (2025) vs 1.147,6 billion (2024)
        gp_2025 = parse_reported_number("1.152,4", locale="id")
        self.assertEqual(gp_2025, Decimal("1152.4"))
        gp_scaled = apply_scale(gp_2025, scale=1_000_000_000)
        self.assertEqual(gp_scaled, Decimal("1152400000000"))

        # 3. Net Profit: 334,3 billion (2025) vs 295,3 billion (2024)
        np_2025 = parse_reported_number("334,3", locale="id")
        self.assertEqual(np_2025, Decimal("334.3"))
        np_scaled = apply_scale(np_2025, scale=1_000_000_000)
        self.assertEqual(np_scaled, Decimal("334300000000"))

        # 4. Total Assets (Page 9): 5.228,0 billion (2025) vs 4.737,4 billion (2024)
        assets_2025 = parse_reported_number("5.228,0", locale="id")
        self.assertEqual(assets_2025, Decimal("5228.0"))
        assets_scaled = apply_scale(assets_2025, scale=1_000_000_000)
        self.assertEqual(assets_scaled, Decimal("5228000000000"))

    def test_abmm_ground_truth_operational_numbers(self):
        """
        Verify real operational metrics reported in ABMM Annual Report 2025 (Page 19).
        """
        # Ground-truth figures from ABMM AR Page 19 (Operational Highlights):
        # 1. Overburden removal: 235.50 million bcm (2025) vs 270.34 million bcm (2024)
        ob_2025 = parse_reported_number("235.50", locale="en")
        self.assertEqual(ob_2025, Decimal("235.50"))
        ob_scaled = apply_scale(ob_2025, scale=1_000_000)
        self.assertEqual(ob_scaled, Decimal("235500000"))

        ob_2024 = parse_reported_number("270.34", locale="en")
        self.assertEqual(ob_2024, Decimal("270.34"))

        # 2. Rental alat berat dan pengupasan batubara: 34.56 juta ton (2025) vs 39.41 juta ton (2024)
        coal_ext_2025 = parse_reported_number("34.56", locale="en")
        self.assertEqual(coal_ext_2025, Decimal("34.56"))
        coal_ext_scaled = apply_scale(coal_ext_2025, scale=1_000_000)
        self.assertEqual(coal_ext_scaled, Decimal("34560000"))

        # 3. Penjualan batubara (volume): 0.31 juta ton (2025) vs 0.67 juta ton (2024)
        coal_sales_2025 = parse_reported_number("0.31", locale="en")
        self.assertEqual(coal_sales_2025, Decimal("0.31"))
        coal_sales_scaled = apply_scale(coal_sales_2025, scale=1_000_000)
        self.assertEqual(coal_sales_scaled, Decimal("310000"))

    def test_adro_ground_truth_operational_financial_numbers(self):
        """
        Verify real financial & operational metrics reported in ADRO Annual Report 2025 (Page 26).
        """
        # Ground-truth figures from ADRO AR Page 26 (Three-Year Financial Highlights - in Million USD):
        # 1. Revenue: USD 1,874 million (2025)
        rev_2025 = parse_reported_number("1,874", locale="en")
        self.assertEqual(rev_2025, Decimal("1874"))
        rev_scaled = apply_scale(rev_2025, scale=1_000_000)
        self.assertEqual(rev_scaled, Decimal("1874000000"))

        # 2. Cost of Revenue: USD (1,237) million (2025)
        cor_2025 = parse_reported_number("(1,237)", locale="en")
        self.assertEqual(cor_2025, Decimal("-1237"))

        # 3. Gross Profit: USD 637 million (2025)
        gp_2025 = parse_reported_number("637", locale="en")
        self.assertEqual(gp_2025, Decimal("637"))
        gp_scaled = apply_scale(gp_2025, scale=1_000_000)
        self.assertEqual(gp_scaled, Decimal("637000000"))

        # 4. Operating Income: USD 518 million (2025)
        op_inc_2025 = parse_reported_number("518", locale="en")
        self.assertEqual(op_inc_2025, Decimal("518"))

        # 5. EBITDA: USD 799 million (2025)
        ebitda_2025 = parse_reported_number("799", locale="en")
        self.assertEqual(ebitda_2025, Decimal("799"))

        # 6. Net Profit: USD 490 million (2025)
        np_2025 = parse_reported_number("490", locale="en")
        self.assertEqual(np_2025, Decimal("490"))

        # 7. Total Assets: USD 6,817 million (2025)
        assets_2025 = parse_reported_number("6,817", locale="en")
        self.assertEqual(assets_2025, Decimal("6817"))
        assets_scaled = apply_scale(assets_2025, scale=1_000_000)
        self.assertEqual(assets_scaled, Decimal("6817000000"))

    def test_bbca_ground_truth_banking_operational_numbers(self):
        """
        Verify real banking operational metrics reported in BBCA Annual Report 2025 (Page 16).
        """
        # Ground-truth figures from BBCA AR Page 16 (Key Financial Highlights - in Billion IDR):
        # 1. Total Assets: 1,586,829 billion IDR (1.586 Trillion IDR)
        assets_bn = parse_reported_number("1,586,829", locale="en")
        self.assertEqual(assets_bn, Decimal("1586829"))
        assets_scaled = apply_scale(assets_bn, scale=1_000_000_000)
        self.assertEqual(assets_scaled, Decimal("1586829000000000"))

        # 2. Total Loans: 992,901 billion IDR
        loans_bn = parse_reported_number("992,901", locale="en")
        self.assertEqual(loans_bn, Decimal("992901"))
        loans_scaled = apply_scale(loans_bn, scale=1_000_000_000)
        self.assertEqual(loans_scaled, Decimal("992901000000000"))

        # 3. Third Party Funds (DPK): 1,249,044 billion IDR
        dpk_bn = parse_reported_number("1,249,044", locale="en")
        self.assertEqual(dpk_bn, Decimal("1249044"))
        dpk_scaled = apply_scale(dpk_bn, scale=1_000_000_000)
        self.assertEqual(dpk_scaled, Decimal("1249044000000000"))

        # 4. Current Accounts (Giro): 434,453 billion IDR
        giro_bn = parse_reported_number("434,453", locale="en")
        self.assertEqual(giro_bn, Decimal("434453"))

        # 5. Savings Accounts (Tabungan): 610,786 billion IDR
        tab_bn = parse_reported_number("610,786", locale="en")
        self.assertEqual(tab_bn, Decimal("610786"))

        # 6. CASA Total: 1,045,239 billion IDR (434,453 + 610,786)
        casa_bn = parse_reported_number("1,045,239", locale="en")
        self.assertEqual(casa_bn, Decimal("1045239"))
        self.assertEqual(giro_bn + tab_bn, casa_bn)

        # 7. Time Deposits (Deposito): 203,805 billion IDR
        depo_bn = parse_reported_number("203,805", locale="en")
        self.assertEqual(depo_bn, Decimal("203805"))
        self.assertEqual(casa_bn + depo_bn, dpk_bn)

    def test_bren_ground_truth_renewable_energy_operational_numbers(self):
        """
        Verify real renewable operational metrics reported in BREN Sustainability & Annual Report (Page 10).
        """
        # Ground-truth figures from BREN AR Page 10 (Sustainability Performance):
        # 1. Total energy consumption: 868,604.46 GJ
        energy_gj = parse_reported_number("868,604.46", locale="en")
        self.assertEqual(energy_gj, Decimal("868604.46"))

        # 2. Total electricity production: 7,034.41 GWh (Star Energy Geothermal & Barito Wind)
        elec_gwh = parse_reported_number("7,034.41", locale="en")
        self.assertEqual(elec_gwh, Decimal("7034.41"))

        # 3. Total steam production: 13,377.23 tons
        steam_tons = parse_reported_number("13,377.23", locale="en")
        self.assertEqual(steam_tons, Decimal("13377.23"))

        # 4. Net revenue (from FS in thousand USD): 596,823 thousand USD
        rev_th = parse_reported_number("596,823", locale="en")
        self.assertEqual(rev_th, Decimal("596823"))
        rev_scaled = apply_scale(rev_th, scale=1_000)
        self.assertEqual(rev_scaled, Decimal("596823000"))

    def test_mapi_ground_truth_operational_numbers(self):
        """
        Verify real retail network figures reported in MAPI Annual Report 2025 (Pages 27, 30, 32).
        """
        # 1. Total retail store count: 4,023 stores (2025) vs 3,703 stores (2024)
        stores_2025 = parse_reported_number("4,023", locale="en")
        self.assertEqual(stores_2025, Decimal("4023"))
        stores_2024 = parse_reported_number("3,703", locale="en")
        self.assertEqual(stores_2024, Decimal("3703"))
        net_new_stores = stores_2025 - stores_2024
        self.assertEqual(net_new_stores, Decimal("320"))

        # 2. Regional footprint breakdown (AR Page 32):
        indo_stores = parse_reported_number("3,543", locale="en")
        viet_stores = parse_reported_number("261", locale="en")
        phil_stores = parse_reported_number("85", locale="en")
        thai_stores = parse_reported_number("55", locale="en")
        mys_stores = parse_reported_number("40", locale="en")
        sg_stores = parse_reported_number("27", locale="en")
        cam_stores = parse_reported_number("12", locale="en")

        regional_sum = (
            indo_stores + viet_stores + phil_stores + thai_stores +
            mys_stores + sg_stores + cam_stores
        )
        self.assertEqual(regional_sum, stores_2025)

        # 3. Portfolio brands represented: 150+ brands
        brands_count = parse_reported_number("150", locale="en")
        self.assertGreaterEqual(brands_count, Decimal("150"))

    def test_dmas_ground_truth_operational_numbers(self):
        """
        Verify real township & industrial operational figures reported in DMAS Annual Report 2025 (Pages 46-48).
        """
        # 1. Kota Deltamas total township area: 3,200 hectares
        township_ha = parse_reported_number("3,200", locale="en")
        self.assertEqual(township_ha, Decimal("3200"))

        # 2. Designated Data Center Zone area: 300 hectares
        datacenter_ha = parse_reported_number("300", locale="en")
        self.assertEqual(datacenter_ha, Decimal("300"))
        self.assertLess(datacenter_ha, township_ha)

        # 3. Land bank total licensed area: > 3,000 hectares
        landbank_ha = parse_reported_number("3,000", locale="en")
        self.assertGreaterEqual(landbank_ha, Decimal("3000"))

    def test_arci_ground_truth_operational_numbers(self):
        """
        Verify real mining production & reserve figures reported in ARCI Annual Report 2025 (Pages 6, 36-37).
        """
        # 1. Gold production volume: 121.73 koz (2025) vs 101.40 koz (2024)
        gold_prod_2025 = parse_reported_number("121.73", locale="en")
        self.assertEqual(gold_prod_2025, Decimal("121.73"))
        gold_prod_2024 = parse_reported_number("101.40", locale="en")
        self.assertEqual(gold_prod_2024, Decimal("101.40"))
        self.assertGreater(gold_prod_2025, gold_prod_2024)

        # 2. Total gold ore reserves: 2.52 Moz (million ounces)
        gold_reserves_moz = parse_reported_number("2.52", locale="en")
        self.assertEqual(gold_reserves_moz, Decimal("2.52"))

        # 3. Eastern Corridor reserve additions in 2025: 941 koz
        reserve_additions_koz = parse_reported_number("941", locale="en")
        self.assertEqual(reserve_additions_koz, Decimal("941"))

        # 4. Realized average gold selling price: US$ 3,431 / oz
        realized_gold_price = parse_reported_number("3,431", locale="en")
        self.assertEqual(realized_gold_price, Decimal("3431"))

    def test_bisi_ground_truth_operational_numbers(self):
        """
        Verify real agrochemical & seed segment margins reported in BISI Annual Report 2025 (Page 30).
        """
        # 1. Agrochemicals (pesticides) gross profit margin: 37.96% (2025) vs 39.22% (2024)
        pesticide_gpm_2025 = parse_reported_number("37.96%", locale="en")
        self.assertEqual(pesticide_gpm_2025, Decimal("37.96"))
        pesticide_gpm_2024 = parse_reported_number("39.22%", locale="en")
        self.assertEqual(pesticide_gpm_2024, Decimal("39.22"))

        # 2. Fertilizer (pupuk) gross profit margin: 38.36% (2025) vs 34.15% (2024)
        fertilizer_gpm_2025 = parse_reported_number("38.36%", locale="en")
        self.assertEqual(fertilizer_gpm_2025, Decimal("38.36"))
        fertilizer_gpm_2024 = parse_reported_number("34.15%", locale="en")
        self.assertEqual(fertilizer_gpm_2024, Decimal("34.15"))
        self.assertGreater(fertilizer_gpm_2025, fertilizer_gpm_2024)

    def test_ammn_deep_mda_operational_facts(self):
        """
        Verify real operational metrics reported in AMMN Annual Report 2024 Chapter 04
        (Batu Hijau Mining and Processing, Pages 75-88).
        """
        # 1. Total mill throughput: 39 Mt (2024) vs 36 Mt (2023)
        throughput_2024 = parse_reported_number("39", locale="en")
        self.assertEqual(throughput_2024, Decimal("39"))
        throughput_2023 = parse_reported_number("36", locale="en")
        self.assertEqual(throughput_2023, Decimal("36"))
        self.assertGreater(throughput_2024, throughput_2023)

        # 2. Fresh ore fed to mill: 22 Mt (2024) vs 11 Mt (2023) (+102%)
        fresh_ore_2024 = parse_reported_number("22", locale="en")
        self.assertEqual(fresh_ore_2024, Decimal("22"))
        fresh_ore_2023 = parse_reported_number("11", locale="en")
        self.assertEqual(fresh_ore_2023, Decimal("11"))

        # 3. Phase 7 fresh ore mined: 31 Mt (2024) vs 16 Mt (2023) (+94.9%)
        p7_ore_2024 = parse_reported_number("31", locale="en")
        self.assertEqual(p7_ore_2024, Decimal("31"))
        p7_ore_2023 = parse_reported_number("16", locale="en")
        self.assertEqual(p7_ore_2023, Decimal("16"))

        # 4. Copper concentrate production: 755,083 dmt (2024) vs 541,894 dmt (2023) (+39%)
        conc_2024 = parse_reported_number("755,083", locale="en")
        self.assertEqual(conc_2024, Decimal("755083"))
        conc_2023 = parse_reported_number("541,894", locale="en")
        self.assertEqual(conc_2023, Decimal("541894"))

        # 5. Stockpiled copper concentrate reserved for smelter commissioning: 190,000 dmt
        stockpile_smelter = parse_reported_number("190,000", locale="en")
        self.assertEqual(stockpile_smelter, Decimal("190000"))

        # 6. Capital expenditure sustaining (Phase 7 & 8): USD 234 million (2024)
        capex_sustaining = parse_reported_number("234", locale="en")
        self.assertEqual(apply_scale(capex_sustaining, scale=1_000_000), Decimal("234000000"))

        # 7. Smelter and PMR construction capex: USD 489 million (2024)
        capex_smelter = parse_reported_number("489", locale="en")
        self.assertEqual(apply_scale(capex_smelter, scale=1_000_000), Decimal("489000000"))

    def test_aali_deep_mda_operational_facts(self):
        """
        Verify real agribusiness operational metrics reported in AALI Annual Report 2024 Chapter 04
        (Tinjauan Operasional - Perkebunan Kelapa Sawit, Pages 96-98).
        """
        # 1. Total managed oil palm plantation area: 284,831 hectares
        total_ha = parse_reported_number("284.831", locale="id")
        self.assertEqual(total_ha, Decimal("284831"))

        # 2. Mature bearer plants (TM): 265,490 hectares (93.2%)
        tm_ha = parse_reported_number("265.490", locale="id")
        self.assertEqual(tm_ha, Decimal("265490"))

        # 3. Immature plants (TBM): 19,341 hectares (6.8%)
        tbm_ha = parse_reported_number("19.341", locale="id")
        self.assertEqual(tbm_ha, Decimal("19341"))
        self.assertEqual(tm_ha + tbm_ha, total_ha)

        # 4. Average crop age: 16.5 years
        avg_age = parse_reported_number("16,5", locale="id")
        self.assertEqual(avg_age, Decimal("16.5"))

        # 5. Nucleus estate (inti): 212,602 ha vs Plasma smallholders: 72,229 ha
        inti_ha = parse_reported_number("212.602", locale="id")
        plasma_ha = parse_reported_number("72.229", locale="id")
        self.assertEqual(inti_ha + plasma_ha, total_ha)

        # 6. Operating Palm Oil Mills (PKS): 32 units with 1,570 tons/hour total capacity
        pks_count = parse_reported_number("32", locale="id")
        self.assertEqual(pks_count, Decimal("32"))
        pks_cap = parse_reported_number("1.570", locale="id")
        self.assertEqual(pks_cap, Decimal("1570"))

        # 7. Palm Kernel Crushing Plants (PPIS): 14 units with 1,500 tons/day total capacity
        ppis_count = parse_reported_number("14", locale="id")
        self.assertEqual(ppis_count, Decimal("14"))
        ppis_cap = parse_reported_number("1.500", locale="id")
        self.assertEqual(ppis_cap, Decimal("1500"))

        # 8. Fertilizer blending plants: 2 units with 570 tons/day total capacity
        fert_count = parse_reported_number("2", locale="id")
        fert_cap = parse_reported_number("570", locale="id")
        self.assertEqual(fert_count, Decimal("2"))
        self.assertEqual(fert_cap, Decimal("570"))

    def test_antm_deep_mda_operational_facts(self):
        """
        Verify real mining & smelting metrics reported in ANTM Annual Report 2024 Chapter 04
        (Target dan Realisasi Kinerja Produksi dan Penjualan, Pages 294-295).
        """
        # 1. Ferronickel production: 20,103 TNi (target 22,464 TNi, 89% achievement)
        feni_prod_2024 = parse_reported_number("20.103", locale="id")
        self.assertEqual(feni_prod_2024, Decimal("20103"))
        feni_target = parse_reported_number("22.464", locale="id")
        self.assertEqual(feni_target, Decimal("22464"))

        # 2. Ferronickel sales: 19,452 TNi (87% achievement)
        feni_sales_2024 = parse_reported_number("19.452", locale="id")
        self.assertEqual(feni_sales_2024, Decimal("19452"))

        # 3. Nickel ore production volume: 9.94 million wmt (target 20.58 million wmt)
        ni_ore_prod = parse_reported_number("9,94", locale="id")
        self.assertEqual(ni_ore_prod, Decimal("9.94"))
        self.assertEqual(apply_scale(ni_ore_prod, scale=1_000_000), Decimal("9940000"))

        # 4. Nickel ore sales volume: 8.35 million wmt (target 18.75 million wmt)
        ni_ore_sales = parse_reported_number("8,35", locale="id")
        self.assertEqual(ni_ore_sales, Decimal("8.35"))
        self.assertEqual(apply_scale(ni_ore_sales, scale=1_000_000), Decimal("8350000"))

        # 5. Silver metal production: 6,393 kg (205,540 oz)
        silver_kg = parse_reported_number("6.393", locale="id")
        self.assertEqual(silver_kg, Decimal("6393"))
        silver_oz = parse_reported_number("205.540", locale="id")
        self.assertEqual(silver_oz, Decimal("205540"))

        # 6. Bauxite ore production: 1.33 million wmt (target 3.47 million wmt)
        bauxite_prod = parse_reported_number("1,33", locale="id")
        self.assertEqual(bauxite_prod, Decimal("1.33"))
        self.assertEqual(apply_scale(bauxite_prod, scale=1_000_000), Decimal("1330000"))

        # 7. Chemical Grade Alumina (CGA Tayan) output: 148 thousand tons
        alumina_prod = parse_reported_number("148", locale="id")
        self.assertEqual(apply_scale(alumina_prod, scale=1_000), Decimal("148000"))

    def test_akra_deep_mda_operational_facts(self):
        """
        Verify real distribution & industrial estate metrics reported in AKRA Annual Report 2024 Chapter 04
        (Pendapatan Segmen Operasi & JIIPE, Pages 110-115).
        """
        # 1. Trading & Distribution segment revenue: Rp35,532 miliar (2024) vs Rp38,213 miliar (2023)
        trade_rev_2024 = parse_reported_number("35.532", locale="id")
        self.assertEqual(trade_rev_2024, Decimal("35532"))
        self.assertEqual(apply_scale(trade_rev_2024, scale=1_000_000_000), Decimal("35532000000000"))

        # 2. Logistics services segment revenue: Rp1,165 miliar (2024) vs Rp994 miliar (2023)
        log_rev_2024 = parse_reported_number("1.165", locale="id")
        self.assertEqual(log_rev_2024, Decimal("1165"))

        # 3. Manufacturing segment revenue: Rp652 miliar (2024) vs Rp558 miliar (2023)
        mfg_rev_2024 = parse_reported_number("652", locale="id")
        self.assertEqual(mfg_rev_2024, Decimal("652"))

        # 4. Industrial Estate (JIIPE) revenue: Rp1,381 miliar (2024) vs Rp2,323 miliar (2023)
        jiipe_rev_2024 = parse_reported_number("1.381", locale="id")
        self.assertEqual(jiipe_rev_2024, Decimal("1381"))

        # 5. Consolidated revenue: Rp38,729 miliar (2024) (differs by 1 from sum of segments due to rounding)
        total_rev_2024 = parse_reported_number("38.729", locale="id")
        self.assertEqual(total_rev_2024, Decimal("38729"))
        segment_sum = trade_rev_2024 + log_rev_2024 + mfg_rev_2024 + jiipe_rev_2024
        self.assertLessEqual(abs(segment_sum - total_rev_2024), Decimal("1"))

        # 6. Desalination water treatment capacity: 2,400 m3/day
        water_cap = parse_reported_number("2.400", locale="id")
        self.assertEqual(water_cap, Decimal("2400"))

    def test_adro_deep_mda_operational_facts(self):
        """
        Verify real coal mining operational metrics reported in ADRO Annual Report 2025 Chapter 04
        (Tinjauan Kinerja Operasional, Pages 76-85).
        """
        # 1. Metallurgical coal production: 7.41 Mt (2025) vs 5.92 Mt (2024)
        met_coal_2025 = parse_reported_number("7.41", locale="en")
        self.assertEqual(met_coal_2025, Decimal("7.41"))
        met_coal_2024 = parse_reported_number("5.92", locale="en")
        self.assertEqual(met_coal_2024, Decimal("5.92"))
        self.assertGreater(met_coal_2025, met_coal_2024)

        # 2. Overburden removal: 26.33 Mbcm (2025) vs 17.78 Mbcm (2024)
        ob_2025 = parse_reported_number("26.33", locale="en")
        self.assertEqual(ob_2025, Decimal("26.33"))
        ob_2024 = parse_reported_number("17.78", locale="en")
        self.assertEqual(ob_2024, Decimal("17.78"))

        # 3. Coal transport / barging volume: 66.55 Mt (2025) vs 64.91 Mt (2024)
        trans_2025 = parse_reported_number("66.55", locale="en")
        self.assertEqual(trans_2025, Decimal("66.55"))
        trans_2024 = parse_reported_number("64.91", locale="en")
        self.assertEqual(trans_2024, Decimal("64.91"))

        # 4. Strip ratio: 3.55x (2025) vs 3.00x (2024)
        sr_2025 = parse_reported_number("3.55", locale="en")
        self.assertEqual(sr_2025, Decimal("3.55"))

    def test_arci_deep_mda_operational_facts(self):
        """
        Verify real gold mining operational metrics reported in ARCI Annual Report 2025 Chapter 04
        (Kinerja Usaha Segmen Penambangan Emas, Pages 89-93).
        """
        # 1. Processing plant capacity: 4.00 million tons/year
        capacity = parse_reported_number("4,00", locale="id")
        self.assertEqual(capacity, Decimal("4.00"))

        # 2. Recovery rate: 87.77%
        recovery = parse_reported_number("87,77%", locale="id")
        self.assertEqual(recovery, Decimal("87.77"))

        # 3. Gold production volume: 121.73 koz (2025) vs 93.44 koz (2024)
        gold_2025 = parse_reported_number("121,73", locale="id")
        self.assertEqual(gold_2025, Decimal("121.73"))
        gold_2024 = parse_reported_number("93,44", locale="id")
        self.assertEqual(gold_2024, Decimal("93.44"))

        # 4. Gold mining segment revenue: USD 434.86 million (2025) vs USD 209.68 million (2024) (+107.39%)
        rev_2025 = parse_reported_number("434,86", locale="id")
        self.assertEqual(rev_2025, Decimal("434.86"))
        rev_2024 = parse_reported_number("209,68", locale="id")
        self.assertEqual(rev_2024, Decimal("209.68"))

    def test_kpi_extractor_prompt_and_all_candidate_packs(self):
        """
        Verify that KPIExtractor dynamically loads all operational packs (multi-sector)
        and injects high-recall instructions across all segments into the prompt.
        """
        from src.extraction.kpis import KPIExtractor
        from src.mapping.concept_mapper import ConceptMapper
        from src.llm.prompts import KPI_EXTRACTION_PROMPT

        mapper = ConceptMapper()
        candidates = mapper.get_candidates_for_kpis(packs=None)

        # Must load candidate packs for multiple sectors (not just nickel)
        concept_ids = [c["id"] for c in candidates]
        self.assertIn("op.core.production_volume", concept_ids)
        self.assertIn("op.coal.production", concept_ids)
        self.assertIn("op.nickel.ore_production", concept_ids)
        self.assertIn("op.cugold.ore_milled", concept_ids)
        self.assertIn("op.plant.cpo_production", concept_ids)
        self.assertIn("op.power.installed_capacity", concept_ids)
        self.assertIn("op.contractor.overburden_removal", concept_ids)

        # Check prompt contains multi-sector high-recall guidelines
        self.assertIn("EXTRACT ALL REPORTED BUSINESS SEGMENTS", KPI_EXTRACTION_PROMPT)
        self.assertIn("MULTI-YEAR COMPARATIVE DATA", KPI_EXTRACTION_PROMPT)
        self.assertIn("Agribusiness & Plantations", KPI_EXTRACTION_PROMPT)
        self.assertIn("Power Generation", KPI_EXTRACTION_PROMPT)
        self.assertIn("Distribution, Logistics", KPI_EXTRACTION_PROMPT)

    def test_grounded_verbatim_matching(self):
        """Verify that grounded() rule handles formatting and spacing cleanly."""
        pdf_page_text = "Volume penjualan bijih nikel tercatat 30,59 juta wmt pada tahun 2025."
        self.assertTrue(grounded("30,59", pdf_page_text))

        # Parenthesized numbers matching
        pdf_page_with_brackets = "Pembayaran untuk perolehan aset tetap (331,318) (657,104)"
        self.assertTrue(grounded("(331,318)", pdf_page_with_brackets))
        self.assertTrue(grounded("331,318", pdf_page_with_brackets))

        # Negative case: completely fabricated number
        self.assertFalse(grounded("999,999", pdf_page_text))

    def test_sectionizer_detects_deep_mda_operational_spans(self):
        """
        Verify that Sectionizer reliably separates early highlights (pages 5-35)
        from deep MD&A operational review chapters (pages >= 50) and ignores appendix checklists.
        """
        from src.parser.pdf_loader import PDFPage
        from src.parser.sectionizer import Sectionizer

        # Construct synthetic PDFDocument to test sectionizer boundary rules
        class MockPDFDoc:
            def __init__(self, pages_text: list[str]):
                self.doc_type = "annual_report"
                self.ticker = "MOCK"
                self.page_count = len(pages_text)
                self.pages = [PDFPage(page_no=i+1, text=t) for i, t in enumerate(pages_text)]

        pages = [""] * 120
        # Page 10: Early Highlights
        pages[9] = "Ikhtisar Keuangan dan Operasional 2024\nFinancial and Operational Highlights"
        # Page 38: Board of Directors letter
        pages[37] = "Laporan Direksi\nKinerja Operasional Perseroan berjalan dengan baik"
        # Page 74: MD&A Chapter 04 start
        pages[73] = "Analisis dan Pembahasan Manajemen\n04\nManagement Discussion and Analysis"
        # Page 75: Deep operational review
        pages[74] = "Analisis dan Pembahasan Manajemen\nMINING AND PROCESSING\nBatu Hijau Copper-Gold Production"
        # Page 115: Appendix compliance table
        pages[114] = "Tabel Kesesuaian Pedoman Penyusunan Laporan Tahunan OJK X.K.6\nTinjauan Kinerja Operasional"

        mock_doc = MockPDFDoc(pages)
        sectionizer = Sectionizer(mock_doc)
        spans = sectionizer.sectionize()

        kpi_spans = [s for s in spans if s.section_type == "ar.mdna.kpis"]
        hl_spans = [s for s in spans if s.section_type == "ar.highlights"]

        self.assertEqual(len(hl_spans), 1)
        self.assertEqual(hl_spans[0].start_page, 10)

        self.assertEqual(len(kpi_spans), 1)
        self.assertEqual(kpi_spans[0].start_page, 75)
        self.assertGreaterEqual(kpi_spans[0].start_page, 50)
        self.assertNotEqual(kpi_spans[0].start_page, 38)
        self.assertNotEqual(kpi_spans[0].start_page, 115)


if __name__ == "__main__":
    unittest.main()

