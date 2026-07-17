from student.timezones import COUNTRY_TZ


def test_timezone_catalog_has_iso_keys_iana_values_and_chile():
    assert COUNTRY_TZ["CL"] == "America/Santiago"
    assert all(len(country) == 2 and country.isupper() for country in COUNTRY_TZ)
    assert all("/" in timezone or timezone == "UTC" for timezone in COUNTRY_TZ.values())
