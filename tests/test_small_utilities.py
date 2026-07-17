from student.academic_seeds import _flag
from student.timezones import tz_for_country


def test_timezone_lookup_normalizes_country_and_falls_back_to_utc():
    assert tz_for_country("cl-extra") == "America/Santiago"
    assert tz_for_country("") == "UTC"
    assert tz_for_country("unknown") == "UTC"


def test_country_flag_rejects_invalid_iso_and_normalizes_case():
    assert _flag("") == ""
    assert _flag("CHL") == ""
    assert _flag("cl") == "🇨🇱"
