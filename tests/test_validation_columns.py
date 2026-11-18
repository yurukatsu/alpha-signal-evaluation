"""config の列名・role の誤りが、計算前の検証（validate）で見つかること。"""

import pandas as pd
import pytest

from alpha_signal_evaluation import ConfigValidationError, evaluate, validate


def _errors(dataset, check_files=True, **overrides) -> list[str]:
    return validate(dataset.write_config(**overrides), check_files=check_files).errors


def _signals(dataset, **changes) -> dict:
    data = dataset.config()["data"]
    data["signals"] = {**data["signals"], **changes}
    return data


def test_valid_config_has_no_column_errors(dataset):
    assert _errors(dataset) == []


def test_missing_role_mapping_is_reported_without_reading_files(dataset):
    data = _signals(dataset, columns={})
    universe = {
        **dataset.config()["universe"],
        "columns": {"asset_id": "code"},
    }  # 単一ファイルは date も要る
    for check_files in (True, False):
        text = "\n".join(_errors(dataset, check_files, data=data, universe=universe))
        assert (
            "data.signals[0].columns: no column is mapped to required role(s) ['asset_id']" in text
        )
        assert "universe.columns: no column is mapped to required role(s) ['date']" in text


def test_classification_needs_category_unless_one_hot(dataset):
    data = dataset.config()["data"]
    data["classification"] = {**data["classification"], "columns": {"asset_id": "code"}}
    assert any("['category']" in e for e in _errors(dataset, data=data))
    data["classification"]["one_hot"] = True
    assert not any("data.classification.columns" in e for e in _errors(dataset, False, data=data))


def test_wrong_column_names_are_found_in_the_first_file(dataset):
    data = _signals(dataset, columns={"asset_id": "nri_code"}, values=["perfect", "noise"])
    (error,) = _errors(dataset, data=data)
    assert error.startswith("data.signals[0].columns: columns ['nri_code'] not found in ")
    assert "available: ['code', 'perfect', 'noise']" in error


def test_wrong_signal_and_return_columns_are_reported(dataset):
    data = _signals(dataset, values=["perfect", "nope"])
    returns = dataset.config()["returns"]
    returns[0]["series"]["total"]["column"] = "rets"
    errors = _errors(dataset, data=data, returns=returns)
    assert any(e.startswith("data.signals[0].values: columns ['nope'] not found") for e in errors)
    assert any(e.startswith("returns[0].series: columns ['rets'] not found") for e in errors)
    # 検証で止まるので、データを読む前に（計算せずに）エラーになる
    with pytest.raises(ConfigValidationError, match="rets"):
        evaluate(dataset.write_config(data=data, returns=returns))


def test_headerless_file_gets_a_hint(dataset):
    (dataset.root / "nohead").mkdir()
    for r in dict.fromkeys(dataset.rskmdl):
        pd.DataFrame({"code": dataset.codes, "s": "S1"}).to_csv(
            dataset.root / "nohead" / f"{r}.csv", index=False, header=False
        )
    data = dataset.config()["data"]
    data["classification"] = {**data["classification"], "path": "nohead/{Rskmdl_month}.csv"}
    (error,) = _errors(dataset, data=data)
    assert "columns ['code', 'gics_sector'] not found" in error
    assert "read_options: {header: null, names: [...]}" in error

    data["classification"]["read_options"] = {"header": None, "names": ["code", "gics_sector"]}
    assert _errors(dataset, data=data) == []


def test_column_check_is_skipped_with_skip_files(dataset):
    data = _signals(dataset, values=["perfect", "nope"])
    assert _errors(dataset, check_files=False, data=data) == []
