from src.services.plotting import colors
from src.services.plotting.colors import (
    _resolve_team_color,
    get_driver_color,
    get_team_color,
)


class TestLegacyBehaviorUnchanged:
    """Calls without a year must return exactly what the pre-existing hardcoded
    dictionaries returned, so existing plots do not change."""

    def test_get_driver_color_tla(self):
        assert get_driver_color("VER") == "#3671C6"

    def test_get_driver_color_full_name(self):
        assert get_driver_color("Hamilton") == "#E80020"

    def test_get_team_color_alias(self):
        assert get_team_color("mclaren") == "#FF8000"

    def test_get_team_color_full_name(self):
        assert get_team_color("Red Bull Racing") == "#3671C6"

    def test_unknown_driver_is_white_when_not_in_any_year(self):
        assert get_driver_color("ZZZ") == "#FFFFFF"

    def test_unknown_team_is_white_when_not_in_any_year(self):
        assert get_team_color("Not A Team") == "#FFFFFF"


class Test2026Lookups:
    """Drivers/teams only present in the 2026 grid must resolve via teams.json."""

    def test_driver_only_in_2026_data_is_not_white(self):
        # Lindblad (Racing Bulls) has no entry in the legacy hardcoded dict.
        color = get_driver_color("LIN", 2026)
        assert color != "#FFFFFF"

    def test_teammates_get_different_colors(self):
        first = get_driver_color("LAW", 2026)
        second = get_driver_color("LIN", 2026)
        assert first != second
        assert first != "#FFFFFF"
        assert second != "#FFFFFF"

    def test_team_lookup_by_year(self):
        assert get_team_color("Red Bull Racing", 2026) == "#3671C6"

    def test_team_lookup_by_short_name(self):
        assert get_team_color("MCL", 2026) == "#FF8000"

    def test_unknown_driver_for_known_year_is_white(self):
        assert get_driver_color("ZZZ", 2026) == "#FFFFFF"

    def test_unknown_team_for_known_year_is_white(self):
        assert get_team_color("Not A Team", 2026) == "#FFFFFF"


class TestTeamColorFallbackGuard:
    """The white/missing-colour guard is exercised directly against constructed
    entries, since the current teams.json data may not itself contain a white or
    missing colour for these teams."""

    def test_audi_fallback_on_white(self):
        assert _resolve_team_color({"name": "Audi", "color": "#FFFFFF"}) == "#BB0A30"

    def test_audi_fallback_on_missing(self):
        assert _resolve_team_color({"name": "Audi"}) == "#BB0A30"

    def test_cadillac_fallback_on_white(self):
        assert _resolve_team_color({"name": "Cadillac", "color": "#ffffff"}) == "#0B2C5A"

    def test_haas_fallback_on_missing(self):
        assert _resolve_team_color({"name": "Haas", "color": ""}) == "#B6BABD"

    def test_unmapped_team_fallback_is_white(self):
        assert _resolve_team_color({"name": "Some New Team", "color": "#FFFFFF"}) == "#FFFFFF"

    def test_real_color_passes_through_unchanged(self):
        assert _resolve_team_color({"name": "Ferrari", "color": "#E80020"}) == "#E80020"


class TestModuleLoading:
    def test_teams_data_is_cached(self):
        colors._load_teams_data.cache_clear()
        first = colors._load_teams_data()
        second = colors._load_teams_data()
        assert first is second


class TestContrastHelpers:
    """Year-aware lookups lift colours that vanish on the dark canvas; legacy ones do not."""

    def test_legible_on_dark_lifts_a_too_dark_colour(self):
        lifted = colors.legible_on_dark("#42423e")
        assert lifted.lower() != "#42423e"
        assert colors.relative_luminance(lifted) >= colors.MIN_LUMINANCE

    def test_legible_on_dark_leaves_bright_colours_alone(self):
        assert colors.legible_on_dark("#E80020") == "#E80020"

    def test_legible_on_dark_returns_unparsable_unchanged(self):
        assert colors.legible_on_dark("not-a-colour") == "not-a-colour"

    def test_cadillac_2026_is_legible(self):
        color = get_team_color("Cadillac", 2026)
        assert color.lower() != "#42423e"
        assert colors.relative_luminance(color) >= colors.MIN_LUMINANCE

    def test_ferrari_2026_unchanged(self):
        assert get_team_color("Ferrari", 2026) == "#E80020"

    def test_legacy_ferrari_unchanged(self):
        assert get_team_color("Ferrari") == "#E80020"

    def test_color_distance_identical_is_zero(self):
        assert colors.color_distance("#27F4D2", "#27F4D2") == 0

    def test_color_distance_mercedes_vs_silver_is_clear(self):
        assert colors.color_distance("#27F4D2", "#E8ECEF") >= colors.MIN_PAIR_DELTA_E

    def test_color_distance_unparsable_is_inf(self):
        assert colors.color_distance("nope", "#27F4D2") == float("inf")
        assert colors.color_distance("#27F4D2", "") == float("inf")

    def test_pair_colors_identical_mercedes_gets_silver(self):
        assert colors.pair_colors("#27F4D2", "#27F4D2", "Mercedes") == (
            "#27F4D2", colors.TEAMMATE_ALT_COLORS["mercedes"])

    def test_pair_colors_identical_ferrari_gets_yellow(self):
        assert colors.pair_colors("#E80020", "#E80020", "Ferrari") == (
            "#E80020", colors.TEAMMATE_ALT_COLORS["ferrari"])

    def test_pair_colors_distinct_colours_unchanged(self):
        assert colors.pair_colors("#3671C6", "#FF8000", "McLaren") == ("#3671C6", "#FF8000")

    def test_pair_colors_unknown_team_still_separates(self):
        a, b = colors.pair_colors("#3671C6", "#3671C6", "Not A Team")
        assert a == "#3671C6"
        assert colors.color_distance(a, b) >= colors.MIN_PAIR_DELTA_E

    def test_get_driver_team(self):
        assert colors.get_driver_team("RUS", 2026) == "Mercedes"

    def test_get_driver_team_unknown_driver(self):
        assert colors.get_driver_team("ZZZ", 2026) is None

    def test_every_2026_teammate_alt_contrasts_with_the_team_colour(self):
        teams_2026 = colors._load_teams_data()["2026"]
        checked = 0
        for entry in teams_2026:
            alt = colors.TEAMMATE_ALT_COLORS.get(entry["name"].lower())
            if alt is None:
                continue
            base = colors.legible_on_dark(colors._resolve_team_color(entry))
            assert colors.color_distance(base, alt) >= colors.MIN_PAIR_DELTA_E, entry["name"]
            checked += 1
        assert checked >= 8
