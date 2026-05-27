"""Tests for eBay CSV generator: titles, prices, categories, sanitization."""
import csv
import os
import pytest

from src.bulk.csv_generator import (
    EbayCSVGenerator, ListingConfig,
    MAX_TITLE_LENGTH, MAX_EBAY_IMAGES, CONDITION_MAP,
)


@pytest.fixture
def gen():
    return EbayCSVGenerator(ListingConfig(markup_pct=15.0, min_price_eur=5.0))


class TestPriceCalculation:
    def test_markup_applied(self, gen):
        assert gen.calculate_price(10.0) == 11.5  # 10 * 1.15

    def test_minimum_enforced(self, gen):
        assert gen.calculate_price(3.0) is None  # 3 * 1.15 = 3.45 < 5.0

    def test_none_input(self, gen):
        assert gen.calculate_price(None) is None

    def test_exact_minimum(self, gen):
        price = gen.calculate_price(4.35)  # 4.35 * 1.15 = 5.0025
        assert price is not None
        assert price >= 5.0

    def test_rounding(self, gen):
        price = gen.calculate_price(10.33)
        assert price == round(10.33 * 1.15, 2)

    def test_custom_markup(self):
        gen20 = EbayCSVGenerator(ListingConfig(markup_pct=20.0, min_price_eur=1.0))
        assert gen20.calculate_price(10.0) == 12.0

    def test_zero_markup(self):
        gen0 = EbayCSVGenerator(ListingConfig(markup_pct=0.0, min_price_eur=1.0))
        assert gen0.calculate_price(10.0) == 10.0


class TestTitleGeneration:
    def test_brand_model_description(self, gen):
        title = gen.generate_title(
            lpn="LPNWE0001",
            brand="Samsung",
            model="Galaxy S21",
            description="Smartphone 128GB",
            condition_code="PERFECTO",
        )
        assert "Samsung" in title
        assert "Galaxy S21" in title
        assert "Smartphone 128GB" in title
        assert "[Perfecto #0001]" in title

    def test_truncation_to_max_length(self, gen):
        title = gen.generate_title(
            lpn="LPNWE0001",
            brand="Samsung",
            model="Galaxy S21 Ultra 5G",
            description="Super long product description that goes on and on",
            condition_code="PERFECTO",
        )
        assert len(title) <= MAX_TITLE_LENGTH

    def test_truncation_preserves_word_boundary(self, gen):
        title = gen.generate_title(
            lpn="LPNWE0001",
            brand="A",
            model="B",
            description="word1 word2 word3 word4 word5 word6 word7 word8 word9 word10",
        )
        assert "..." in title or len(title) <= MAX_TITLE_LENGTH

    def test_uid_from_lpn_last_4(self, gen):
        title = gen.generate_title(lpn="LPNWE9876", brand="Test")
        assert "#9876]" in title

    def test_short_lpn(self, gen):
        title = gen.generate_title(lpn="AB", brand="X")
        assert "#AB]" in title

    def test_condition_suffix_con_tara(self, gen):
        title = gen.generate_title(lpn="LPNWE0001", brand="X", condition_code="CON_TARA")
        assert "[Con tara #0001]" in title

    def test_condition_suffix_para_piezas(self, gen):
        title = gen.generate_title(lpn="LPNWE0001", brand="X", condition_code="PARA_PIEZAS")
        assert "[Para piezas #0001]" in title

    def test_condition_suffix_default(self, gen):
        title = gen.generate_title(lpn="LPNWE0001", brand="X")
        assert "[Usado #0001]" in title

    def test_no_brand_model(self, gen):
        title = gen.generate_title(lpn="LPNWE0001")
        assert "Articulo de liquidacion" in title

    def test_unknown_brand_ignored(self, gen):
        title = gen.generate_title(lpn="LPNWE0001", brand="unknown", model="generic")
        assert "unknown" not in title.lower().split("[")[0]

    def test_separator_for_multiple_parts(self, gen):
        title = gen.generate_title(lpn="LPN1", brand="Sony", model="WH-1000XM4")
        assert " - " in title

    def test_special_chars_stripped_from_description(self, gen):
        title = gen.generate_title(
            lpn="LPN1", description="Product ★ with © symbols ® here"
        )
        assert "★" not in title
        assert "©" not in title


class TestSanitization:
    def test_strips_newlines(self, gen):
        result = gen.sanitize_text("line1\nline2\rline3")
        assert "\n" not in result
        assert "\r" not in result

    def test_strips_tabs(self, gen):
        result = gen.sanitize_text("col1\tcol2")
        assert "\t" not in result

    def test_strips_unicode_line_separators(self, gen):
        result = gen.sanitize_text("text\u2028more\u2029end")
        assert "\u2028" not in result

    def test_row_sanitization(self, gen):
        row = ["normal", "with\nnewline", 42, "tab\there"]
        clean = gen.sanitize_row(row)
        assert "\n" not in clean[1]
        assert "\t" not in clean[3]
        assert clean[2] == 42  # non-string unchanged


class TestImagePiping:
    def test_pipe_separated(self):
        urls = "https://img1.jpg, https://img2.jpg, https://img3.jpg"
        result = EbayCSVGenerator.get_images_piped(urls)
        assert result == "https://img1.jpg|https://img2.jpg|https://img3.jpg"

    def test_max_12_images(self):
        urls = " ".join(f"https://img{i}.jpg" for i in range(20))
        result = EbayCSVGenerator.get_images_piped(urls)
        assert len(result.split("|")) == MAX_EBAY_IMAGES

    def test_filters_non_http(self):
        urls = "https://img1.jpg data:image/png;base64,xxx https://img2.jpg"
        result = EbayCSVGenerator.get_images_piped(urls)
        assert "data:" not in result
        assert result == "https://img1.jpg|https://img2.jpg"

    def test_empty_input(self):
        assert EbayCSVGenerator.get_images_piped("") == ""
        assert EbayCSVGenerator.get_images_piped(None) == ""


class TestDescription:
    def test_includes_all_parts(self, gen):
        desc = gen.build_description(
            description="Product desc",
            features="Feature 1, Feature 2",
            condition_description="Minor scratches",
            asin="B09EXAMPLE",
        )
        assert "Product desc" in desc
        assert "Feature 1" in desc
        assert "Estado: Minor scratches" in desc
        assert "ASIN Amazon: B09EXAMPLE" in desc
        assert "segunda mano" in desc

    def test_skips_nan_values(self, gen):
        desc = gen.build_description(description="nan", features="none", asin="")
        assert "nan" not in desc.lower().split("segunda")[0]

    def test_no_newlines_in_output(self, gen):
        desc = gen.build_description(description="line1\nline2\r\nline3")
        assert "\n" not in desc
        assert "\r" not in desc


class TestConditionMapping:
    def test_perfecto(self):
        assert CONDITION_MAP["PERFECTO"] == 3000

    def test_con_tara(self):
        assert CONDITION_MAP["CON_TARA"] == 3000

    def test_para_piezas(self):
        assert CONDITION_MAP["PARA_PIEZAS"] == 7000


class TestBuildListingRow:
    def test_full_item(self, gen):
        item = {
            "lpn": "LPNWE0001",
            "source_price": 20.0,
            "brand": "Sony",
            "model": "WH-1000XM4",
            "description": "Headphones",
            "condition_code": "PERFECTO",
            "image_urls": "https://img1.jpg",
        }
        row = gen.build_listing_row(item)
        assert row is not None
        assert row["lpn"] == "LPNWE0001"
        assert row["price"] == 23.0  # 20 * 1.15
        assert row["condition_id"] == 3000
        assert "Sony" in row["title"]

    def test_returns_none_for_low_price(self, gen):
        item = {"lpn": "LPN1", "source_price": 2.0}
        assert gen.build_listing_row(item) is None

    def test_unknown_brand_becomes_unbranded(self, gen):
        item = {"lpn": "LPN1", "source_price": 20.0, "brand": "unknown"}
        row = gen.build_listing_row(item)
        assert row["brand"] == "Unbranded"

    def test_unknown_model_becomes_dna(self, gen):
        item = {"lpn": "LPN1", "source_price": 20.0, "model": "unknown"}
        row = gen.build_listing_row(item)
        assert row["mpn"] == "Does Not Apply"

    def test_condition_description_truncated(self, gen):
        item = {
            "lpn": "LPN1",
            "source_price": 20.0,
            "condition_description": "X" * 100,
        }
        row = gen.build_listing_row(item)
        assert len(row["condition_description"]) <= 65


class TestWriteCSV:
    def test_add_csv_structure(self, gen, tmp_path):
        items = [{
            "lpn": "LPN1", "category": "175837", "title": "Test Item",
            "condition_id": 3000, "condition_description": "Good",
            "brand": "Sony", "mpn": "XM4", "color": "Black",
            "price": 25.0, "images": "https://img.jpg",
            "description": "Desc",
        }]
        path = gen.write_add_csv(items, str(tmp_path))
        assert os.path.exists(path)

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header[0].startswith("*Action")
            assert "SiteID=Spain" in header[0]
            assert "Currency=EUR" in header[0]
            row = next(reader)
            assert row[0] == "Add"
            assert row[1] == "LPN1"

    def test_end_csv_structure(self, gen, tmp_path):
        items = [{"lpn": "LPN1", "ebay_item_id": "12345"}]
        path = gen.write_end_csv(items, str(tmp_path))

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "ItemID" in header
            assert "EndCode" in header
            row = next(reader)
            assert row[0] == "End"
            assert row[1] == "12345"
            assert row[2] == "NotAvailable"
            assert row[3] == "LPN1"

    def test_add_csv_with_policies(self, tmp_path):
        gen_p = EbayCSVGenerator(ListingConfig(
            shipping_profile="MyShipping",
            return_profile="MyReturns",
            payment_profile="MyPayment",
        ))
        items = [{
            "lpn": "LPN1", "category": "175837", "title": "T",
            "condition_id": 3000, "condition_description": "",
            "brand": "B", "mpn": "M", "color": "", "price": 10.0,
            "images": "", "description": "D",
        }]
        path = gen_p.write_add_csv(items, str(tmp_path))

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "Shipping profile name" in header
            row = next(reader)
            idx = header.index("Shipping profile name")
            assert row[idx] == "MyShipping"

    def test_add_csv_without_policies(self, gen, tmp_path):
        items = [{
            "lpn": "LPN1", "category": "175837", "title": "T",
            "condition_id": 3000, "condition_description": "",
            "brand": "B", "mpn": "M", "color": "", "price": 10.0,
            "images": "", "description": "D",
        }]
        path = gen.write_add_csv(items, str(tmp_path))

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "Shipping type" in header
            row = next(reader)
            idx = header.index("Shipping type")
            assert row[idx] == "Flat"


class TestGenerateAddCSVs:
    def test_chunks_into_files(self, tmp_path):
        gen = EbayCSVGenerator(ListingConfig(markup_pct=0, min_price_eur=1.0))
        items = [
            {"lpn": f"LPN{i}", "source_price": 10.0, "brand": "B"}
            for i in range(2500)
        ]
        paths = gen.generate_add_csvs(items, str(tmp_path))
        assert len(paths) == 3  # 1000 + 1000 + 500

    def test_skips_low_price(self, tmp_path, gen):
        items = [
            {"lpn": "OK", "source_price": 20.0},
            {"lpn": "LOW", "source_price": 1.0},
        ]
        paths = gen.generate_add_csvs(items, str(tmp_path))
        assert len(paths) == 1
        with open(paths[0], "r") as f:
            reader = csv.reader(f)
            next(reader)  # header
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0][1] == "OK"
