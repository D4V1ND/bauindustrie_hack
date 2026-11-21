import pytest
from pydantic import ValidationError

from lv.model import LV, LineItem, load, save


def flange(**changes) -> LineItem:
    fields = dict(
        id="P1/05.01.36",
        oz="05.01.36",
        section=["05 Rohrleitungen", "05.01 Rohrleitungen aus nichtrostendem Stahl"],
        short_text="Flansch DN 150, PN 10",
        long_text="Glatter Flansch, DN 150, PN 10 sonst wie vor.",
        quantity=175,
        unit="St",
    )
    return LineItem(**(fields | changes))


def lv_with(*items: LineItem) -> LV:
    return LV(project="P1", name="Maschinentechnik - Anlagenbau", date="2024-02-27",
              source_file="LV24069.D86", items=list(items))


def test_lv_survives_saving_and_loading(tmp_path):
    lv = lv_with(flange(unit_price=175.40, total_price=30695.00))

    path = save(lv, tmp_path)

    assert path == tmp_path / "P1.json"
    assert load(path) == lv


def test_umlauts_are_written_readably(tmp_path):
    lv = lv_with(flange(short_text="Baustelleneinrichtung und -räumung"))

    path = save(lv, tmp_path)

    assert "räumung" in path.read_text(encoding="utf-8")


def test_new_line_item_is_a_normal_item_read_by_code_without_prices():
    item = flange()

    assert (item.kind, item.read_by, item.unit_price, item.total_price) == ("normal", "gaeb", None, None)


def test_unknown_kind_is_rejected():
    with pytest.raises(ValidationError):
        flange(kind="alternative")


def test_unknown_read_by_is_rejected():
    with pytest.raises(ValidationError):
        flange(read_by="ocr")


def test_lv_is_priced_when_one_item_has_a_unit_price():
    assert lv_with(flange(), flange(id="P1/05.01.37", unit_price=210.0)).priced is True


def test_lv_is_not_priced_when_no_item_has_a_unit_price():
    assert lv_with(flange()).priced is False
