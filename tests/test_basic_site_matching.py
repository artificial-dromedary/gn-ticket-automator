import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from gn_ticket import basic_site_match


def test_basic_site_match_exact():
    assert basic_site_match("Iqaluit", "Nakasuk School", "Nakasuk School") == "Iqaluit Nakasuk School"


def test_basic_site_match_substring():
    assert basic_site_match("Rankin Inlet", "Maani Ulujuk School", "Maani Ulujuk School") == \
           "Rankin Inlet Maani Ulujuk School"


def test_basic_site_match_igloolik_high_school():
    assert basic_site_match("Igloolik", "Iglulik High School", "Iglulik High School") == \
           "Igloolik High School, Desktop Unit"

