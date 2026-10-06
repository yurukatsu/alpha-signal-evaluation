from pathlib import Path

import pytest

from alpha_signal_evaluation.data.calendar import load_calendar

CALENDAR = (
    "#Base_day\tMonthend_day\tTrading_day\tRskmdl_month\tLabel1\tLabel2\n"
    "20001031\t20001031\t20001031\t199912\t199912\t20001031\n"
    "20001130\t20001130\t20001130\t200010\t200010\t20001130\n"
    "20001231\t20001231\t20001229\t200011\t200011\t20001231\n"
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "calendar.tsv"
    path.write_text(text)
    return path


def test_header_hash_is_stripped_and_values_are_strings(tmp_path):
    cal = load_calendar(_write(tmp_path, CALENDAR))
    assert cal.columns == [
        "Base_day",
        "Monthend_day",
        "Trading_day",
        "Rskmdl_month",
        "Label1",
        "Label2",
    ]
    assert cal.keys.tolist() == ["20001031", "20001130", "20001231"]
    assert cal.frame["Rskmdl_month"].iloc[0] == "199912"


def test_positions(tmp_path):
    cal = load_calendar(_write(tmp_path, CALENDAR))
    assert cal.positions("20001130", None).tolist() == [1, 2]
    assert cal.positions(None, "20001130").tolist() == [0, 1]
    assert cal.positions("20001101", "20001201").tolist() == [1]


def test_unknown_index_column(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_calendar(_write(tmp_path, CALENDAR), index="Base")


def test_unsorted_index_is_rejected(tmp_path):
    lines = CALENDAR.splitlines()
    text = "\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n"
    with pytest.raises(ValueError, match="ascending"):
        load_calendar(_write(tmp_path, text))
