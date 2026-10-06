"""Tests for scenes/flight/flight_scene.py - pure helper functions."""

from unittest.mock import MagicMock, patch

from display.scroller import EASING_STEPS
from display.scroller import _tick_offset as tick_to_offset
from scenes.flight.airline_logo import (
    AirlineLogoWidget,
    NullWidget,
    airline_icao_from_flight,
)
from scenes.flight.callsign_bar import (
    AirlineNameBar,
    CallsignBar,
    airline_name_from_flight,
    make_callsign_bar,
)
from scenes.flight.flight_scene import callsigns_match, telemetry_changed
from scenes.flight.journey import make_label
from scenes.flight.journey.full_label import FullNameLabel, abbreviate
from scenes.flight.journey.short_label import (
    _ARROW_TIP_OFFSET,
    _ARROW_WIDTH,
    _ARROW_WIDTH_SMALL,
    _DEST_OFFSET,
    ShortCodeLabel,
    _code_font,
    _display_code,
)
from utilities.flight import Flight
from setup import screen

# ---------------------------------------------------------------------------
# callsigns_match
# ---------------------------------------------------------------------------


class TestCallsignsMatch:
    def test_identical_lists(self):
        a = [Flight(callsign="BAW123"), Flight(callsign="UAL456")]
        b = [Flight(callsign="BAW123"), Flight(callsign="UAL456")]
        assert callsigns_match(a, b) is True

    def test_different_order(self):
        a = [Flight(callsign="BAW123"), Flight(callsign="UAL456")]
        b = [Flight(callsign="UAL456"), Flight(callsign="BAW123")]
        assert callsigns_match(a, b) is True

    def test_different_sets(self):
        a = [Flight(callsign="BAW123"), Flight(callsign="UAL456")]
        b = [Flight(callsign="BAW123"), Flight(callsign="DAL789")]
        assert callsigns_match(a, b) is False

    def test_both_empty(self):
        assert callsigns_match([], []) is True

    def test_one_empty(self):
        assert callsigns_match([Flight(callsign="BAW123")], []) is False

    def test_duplicate_callsigns_different_length(self):
        a = [Flight(callsign="BAW123"), Flight(callsign="BAW123")]
        b = [Flight(callsign="BAW123")]
        # Different list lengths mean the flight set changed even when
        # the callsigns are identical, so on_data() must reset.
        assert callsigns_match(a, b) is False

    def test_duplicate_callsigns_same_length(self):
        a = [Flight(callsign="BAW123"), Flight(callsign="BAW123")]
        b = [Flight(callsign="BAW123"), Flight(callsign="BAW123")]
        assert callsigns_match(a, b) is True


# ---------------------------------------------------------------------------
# telemetry_changed
# ---------------------------------------------------------------------------


class TestTelemetryChanged:
    def test_no_change(self):
        old = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=450,
                heading=90,
            )
        ]
        new = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=450,
                heading=90,
            )
        ]
        assert telemetry_changed(old, new) is False

    def test_altitude_changed(self):
        old = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=450,
                heading=90,
            )
        ]
        new = [
            Flight(
                callsign="BAW123",
                altitude=34000,
                ground_speed=450,
                heading=90,
            )
        ]
        assert telemetry_changed(old, new) is True

    def test_ground_speed_changed(self):
        old = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=450,
                heading=90,
            )
        ]
        new = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=460,
                heading=90,
            )
        ]
        assert telemetry_changed(old, new) is True

    def test_heading_changed(self):
        old = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=450,
                heading=90,
            )
        ]
        new = [
            Flight(
                callsign="BAW123",
                altitude=35000,
                ground_speed=450,
                heading=120,
            )
        ]
        assert telemetry_changed(old, new) is True

    def test_new_flight_not_in_old(self):
        old = [Flight(callsign="BAW123", altitude=35000)]
        new = [Flight(callsign="UAL456", altitude=30000)]
        # New flight not in lookup - no comparison possible, so no change detected
        assert telemetry_changed(old, new) is False

    def test_empty_lists(self):
        assert telemetry_changed([], []) is False


# ---------------------------------------------------------------------------
# abbreviate
# ---------------------------------------------------------------------------


class TestAbbreviate:
    def test_international(self):
        assert "Intl" in abbreviate("Glasgow International Airport")

    def test_airport_removed(self):
        result = abbreviate("Heathrow Airport")
        assert "Airport" not in result

    def test_regional(self):
        result = abbreviate("Edinburgh Regional Airport")
        assert "Reg" in result
        assert "Regional" not in result

    def test_municipal(self):
        result = abbreviate("Bristol Municipal Airport")
        assert "Muni" in result
        assert "Municipal" not in result

    def test_multiple_replacements(self):
        result = abbreviate("London International Regional Municipal Airport")
        assert "Intl" in result
        assert "Reg" in result
        assert "Muni" in result
        assert "Airport" not in result
        assert "International" not in result
        assert "Regional" not in result
        assert "Municipal" not in result

    def test_no_replacements_needed(self):
        result = abbreviate("Glasgow")
        assert result == "Glasgow"

    def test_collapses_whitespace(self):
        result = abbreviate("Glasgow  International   Airport")
        # Extra spaces should be collapsed
        assert "  " not in result


# ---------------------------------------------------------------------------
# tick_to_offset
# ---------------------------------------------------------------------------


class TestTickToOffset:
    def test_all_easing_steps(self):
        for i, expected in enumerate(EASING_STEPS):
            assert tick_to_offset(i) == expected

    def test_beyond_easing_steps(self):
        assert tick_to_offset(len(EASING_STEPS)) == 1
        assert tick_to_offset(len(EASING_STEPS) + 5) == 1
        assert tick_to_offset(100) == 1


# ---------------------------------------------------------------------------
# airline_icao_from_flight
# ---------------------------------------------------------------------------


class TestAirlineIcaoFromFlight:
    def test_airline_icao_from_field(self):
        flight = Flight(airline_icao="BAW")
        assert airline_icao_from_flight(flight) == "BAW"

    def test_falls_back_to_callsign_prefix(self):
        flight = Flight(icao_callsign="UAL1583")
        assert airline_icao_from_flight(flight) == "UAL"

    def test_airline_icao_takes_priority_over_callsign(self):
        flight = Flight(airline_icao="ENY", icao_callsign="UAL1583")
        assert airline_icao_from_flight(flight) == "ENY"

    def test_empty_everything(self):
        assert airline_icao_from_flight(Flight()) == ""

    def test_short_callsign_no_airline_icao(self):
        assert airline_icao_from_flight(Flight(icao_callsign="A1")) == ""

    def test_non_alpha_callsign_prefix(self):
        assert airline_icao_from_flight(Flight(icao_callsign="1AB234")) == ""

    def test_strips_whitespace(self):
        flight = Flight(airline_icao="  baw  ")
        assert airline_icao_from_flight(flight) == "BAW"

    def test_callsign_fallback_rejected_non_airline(self):
        # GAF (German Air Force) is a valid 3-letter ICAO designator
        # but is a non-commercial operator - rejected by the blocklist.
        assert airline_icao_from_flight(Flight(icao_callsign="GAF123")) == ""

    def test_callsign_fallback_no_iata_passes_blocklist(self):
        # SHT (British Airways Shuttle) has no IATA mapping but is a
        # legitimate commercial airline - passes the blocklist.
        assert airline_icao_from_flight(Flight(icao_callsign="SHT7Z")) == "SHT"

    def test_callsign_fallback_with_override_passes_blocklist(self):
        # EAI (Aer Lingus Regional) -> override -> EIN (Aer Lingus),
        # which is a commercial airline - passes the blocklist.
        assert airline_icao_from_flight(Flight(icao_callsign="EAI123")) == "EIN"

    def test_api_airline_icao_not_gated_by_blocklist(self):
        # An API-provided airline_icao for a non-commercial operator (e.g.
        # a military code) is trusted as-is - the blocklist only applies to
        # the inferred paths.
        assert airline_icao_from_flight(Flight(airline_icao="GAF")) == "GAF"


class TestAirlineIcaoFromOperator:
    """The Mode S operator code sits between airline_icao and the callsign."""

    def test_operator_used_when_no_airline_icao(self):
        flight = Flight(operator_icao="BAW", icao_callsign="SHT7Z")
        assert airline_icao_from_flight(flight) == "BAW"

    def test_airline_icao_takes_priority_over_operator(self):
        flight = Flight(airline_icao="EIN", operator_icao="EAI")
        assert airline_icao_from_flight(flight) == "EIN"

    def test_operator_resolves_callsign_collision(self):
        # EAG is shared by Emerald Airlines UK and European Aeronautical
        # Group UK.  The callsign prefix alone cannot tell them apart; the
        # per-airframe operator code can.
        flight = Flight(operator_icao="EIN", icao_callsign="EAG56R")
        assert airline_icao_from_flight(flight) == "EIN"

    def test_operator_brand_override_applied(self):
        # Emerald Airlines (EAI) flies as Aer Lingus Regional -> EIN logo.
        flight = Flight(operator_icao="EAI", icao_callsign="EAG56R")
        assert airline_icao_from_flight(flight) == "EIN"

    def test_operator_rejected_when_non_airline(self):
        flight = Flight(operator_icao="GAF", icao_callsign="GAF123")
        assert airline_icao_from_flight(flight) == ""

    def test_unknown_operator_falls_through_to_callsign(self):
        # A code the airline database has never heard of would only produce
        # a missing-logo placeholder, so the callsign prefix gets a turn.
        flight = Flight(operator_icao="ZZZ", icao_callsign="BAW117")
        assert airline_icao_from_flight(flight) == "BAW"

    def test_malformed_operator_ignored(self):
        flight = Flight(operator_icao="G-ABCD", icao_callsign="BAW117")
        assert airline_icao_from_flight(flight) == "BAW"

    def test_operator_strips_whitespace_and_case(self):
        assert airline_icao_from_flight(Flight(operator_icao="  ein  ")) == "EIN"


# ---------------------------------------------------------------------------
# AirlineLogoWidget
# ---------------------------------------------------------------------------


def _make_panel_and_canvas():
    panel = MagicMock()
    canvas = MagicMock()
    return panel, canvas


class TestAirlineLogoWidget:
    def test_width_is_0_before_draw(self):
        panel, _ = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        assert widget.width == 0
        assert widget.icon_drawn is False

    def test_width_is_18_after_icon_drawn(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        widget.draw(canvas, Flight(airline_icao="BCO"))
        assert widget.width == 18
        assert widget.icon_drawn is True

    def test_draw_blanks_then_draws_image(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        # BCO icon exists in assets/airlines/airline_logos_16/
        flight = Flight(airline_icao="BCO")
        widget.draw(canvas, flight)
        # Should have blanked the region (set_pixel calls) then drawn the image
        assert panel.set_pixel.called
        assert panel.draw_image.called
        assert panel.draw_image.call_args.args[2].size == (18, 18)

    def test_draw_once_skips_repaint(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        flight = Flight(airline_icao="BCO")
        widget.draw(canvas, flight)
        panel.reset_mock()
        # Second draw with same airline_icao - should skip
        widget.draw(canvas, flight)
        assert not panel.set_pixel.called
        assert not panel.draw_image.called

    def test_reset_forces_repaint(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        flight = Flight(airline_icao="BCO")
        widget.draw(canvas, flight)
        panel.reset_mock()
        widget.reset()
        widget.draw(canvas, flight)
        assert panel.set_pixel.called
        assert panel.draw_image.called

    def test_flight_change_repaints(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        widget.draw(canvas, Flight(airline_icao="BCO"))
        panel.reset_mock()
        widget.draw(canvas, Flight(airline_icao="AAL"))
        assert panel.set_pixel.called
        assert panel.draw_image.called

    def test_missing_icon_no_draw(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        # QQQ icon file does not exist - no outline, no image, width=0
        widget.draw(canvas, Flight(airline_icao="QQQ"))
        assert panel.set_pixel.called  # blanks the region
        assert not panel.draw_image.called
        assert not panel.draw_line.called
        assert widget.width == 0
        assert widget.icon_drawn is False

    def test_empty_airline_icao_no_draw(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        widget.draw(canvas, Flight())
        assert not panel.draw_image.called
        assert not panel.draw_line.called
        assert widget.width == 0
        assert widget.icon_drawn is False

    def test_callsign_fallback_missing_icon_no_draw(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        # No airline_icao, callsign prefix is alphabetic but has no matching icon
        widget.draw(canvas, Flight(icao_callsign="QQQ999"))
        assert not panel.draw_image.called
        assert not panel.draw_line.called
        assert widget.width == 0
        assert widget.icon_drawn is False

    def test_callsign_fallback_non_airline_no_draw(self):
        panel, canvas = _make_panel_and_canvas()
        widget = AirlineLogoWidget(panel)
        # GAF (German Air Force) is a non-commercial operator - rejected
        # by the blocklist, so no icon is drawn even if a logo file existed.
        widget.draw(canvas, Flight(icao_callsign="GAF123"))
        assert not panel.draw_image.called
        assert not panel.draw_line.called
        assert widget.width == 0
        assert widget.icon_drawn is False


class TestNullWidget:
    def test_width_is_0(self):
        assert NullWidget().width == 0

    def test_draw_is_noop(self):
        panel, canvas = _make_panel_and_canvas()
        widget = NullWidget()
        widget.draw(canvas, Flight(airline_icao="BAW"))
        assert not panel.set_pixel.called
        assert not panel.draw_image.called

    def test_reset_is_noop(self):
        widget = NullWidget()
        widget.reset()  # should not raise


# ---------------------------------------------------------------------------
# make_label
# ---------------------------------------------------------------------------


class TestMakeLabel:
    def test_style_0_iata_returns_stacked_journey_label(self):
        cfg = MagicMock()
        cfg.airport_display_style = 0
        cfg.airport_code_format = "iata"
        panel, _ = _make_panel_and_canvas()
        label = make_label(cfg, panel)
        assert isinstance(label, FullNameLabel)

    def test_style_0_icao_returns_short_code_label(self):
        cfg = MagicMock()
        cfg.airport_display_style = 0
        cfg.airport_code_format = "icao"
        panel, _ = _make_panel_and_canvas()
        label = make_label(cfg, panel)
        assert isinstance(label, ShortCodeLabel)

    def test_style_1_returns_full_name_label(self):
        cfg = MagicMock()
        cfg.airport_display_style = 1
        panel, _ = _make_panel_and_canvas()
        label = make_label(cfg, panel)
        assert isinstance(label, FullNameLabel)

    def test_style_4_returns_full_name_label(self):
        cfg = MagicMock()
        cfg.airport_display_style = 4
        panel, _ = _make_panel_and_canvas()
        label = make_label(cfg, panel)
        assert isinstance(label, FullNameLabel)


# ---------------------------------------------------------------------------
# ShortCodeLabel
# ---------------------------------------------------------------------------


class TestShortCodeLabel:
    def test_loop_completed_after_draw(self):
        panel, canvas = _make_panel_and_canvas()
        # draw_text returns advance width; mock it to return small ints
        panel.draw_text.side_effect = lambda *a, **k: 5
        label = ShortCodeLabel(panel)
        assert label.loop_completed is False
        flight = Flight(origin="GLA", destination="LHR")
        label.draw(canvas, flight, 1, 63)
        assert label.loop_completed is True

    def test_reset_clears_loop_completed(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        label = ShortCodeLabel(panel)
        label.draw(canvas, Flight(origin="GLA", destination="LHR"), 1, 63)
        label.reset()
        assert label.loop_completed is False

    def test_text_origin_shifts_with_icon(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        label = ShortCodeLabel(panel)
        label.draw(canvas, Flight(origin="GLA", destination="LHR"), 19, 45)
        # First draw_text call should be at x=19 (the text_x_origin)
        first_call_args = panel.draw_text.call_args_list[0]
        assert first_call_args[0][2] == 19  # x argument position


class TestShortCodeFontSelection:
    """All journey airport codes render with the thum font."""

    base, base_bold, compact = object(), object(), object()

    def test_three_char_home_uses_bold(self):
        assert (
            _code_font(True, False, self.base, self.base_bold, self.compact)
            is self.base_bold
        )

    def test_three_char_non_home_uses_base(self):
        assert (
            _code_font(False, False, self.base, self.base_bold, self.compact)
            is self.base
        )

    def test_pair_compact_forces_compact(self):
        assert (
            _code_font(True, True, self.base, self.base_bold, self.compact)
            is self.compact
        )
        assert (
            _code_font(False, True, self.base, self.base_bold, self.compact)
            is self.compact
        )

    def test_display_code_keeps_three_and_four_chars(self):
        assert _display_code("GLA") == "GLA"
        assert _display_code("98KY") == "98KY"

    def test_display_code_truncates_longer_codes(self):
        # >4-char local codes are administrative numbering; truncate.
        assert _display_code("SP0002") == "SP00"

    def test_blank_filler_untouched(self):
        assert _display_code("???") == "???"

    def _draw(self, origin, destination, text_x_origin=1, icon_required=False):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 24
        label = ShortCodeLabel(panel)
        label.draw(
            canvas,
            Flight(origin=origin, destination=destination),
            text_x_origin,
            screen.WIDTH - text_x_origin,
            icon_required=icon_required,
        )
        return [call.args[1] for call in panel.draw_text.call_args_list]

    def test_draw_font_matrix_no_logo(self):
        from setup import fonts

        # All airport codes use the same tiny font.
        cases = {
            ("GLA", "LHR"): [fonts.thum, fonts.thum],
            ("GLA", "98KY"): [fonts.thum, fonts.thum],
            ("98KY", "LHR"): [fonts.thum, fonts.thum],
            ("98KY", "0I8"): [fonts.thum, fonts.thum],
        }
        for (origin, destination), expected in cases.items():
            assert (
                self._draw(origin, destination) == expected
            ), f"{origin}->{destination}"

    def test_draw_font_matrix_with_logo(self):
        from setup import fonts

        assert self._draw("GLA", "LHR", text_x_origin=19, icon_required=True) == [
            fonts.thum,
            fonts.thum,
        ]
        assert self._draw("98KY", "0I8", text_x_origin=19, icon_required=True) == [
            fonts.thum,
            fonts.thum,
        ]

    def test_icon_layout_shifts_both_airport_codes_right_two_pixels(self):
        panel, canvas = _make_panel_and_canvas()
        label = ShortCodeLabel(panel)
        label.draw(canvas, Flight(origin="GLA", destination="LHR"), 19, 45, True)

        assert [call.args[2] for call in panel.draw_text.call_args_list] == [19, 46]

    def _draw_with_cfg(
        self, origin, destination, data, text_x_origin=1, icon_required=False
    ):
        from setup.configuration import Config

        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 24
        test_cfg = Config.__new__(Config)
        test_cfg.data_store = data
        label = ShortCodeLabel(panel, test_cfg)
        label.draw(
            canvas,
            Flight(origin=origin, destination=destination),
            text_x_origin,
            screen.WIDTH - text_x_origin,
            icon_required=icon_required,
        )
        return [(call.args[5], call.args[1]) for call in panel.draw_text.call_args_list]

    def test_icao_format_converts_codes(self):
        from setup import fonts

        # IATA and ICAO codes render in the same tiny font.
        pairs = self._draw_with_cfg("GLA", "LHR", {"airport_code_format": "icao"})
        assert dict(pairs) == {"EGPF": fonts.thum, "EGLL": fonts.thum}

    def test_iata_format_keeps_codes(self):
        from setup import fonts

        pairs = self._draw_with_cfg("GLA", "LHR", {"airport_code_format": "iata"})
        assert dict(pairs) == {"GLA": fonts.thum, "LHR": fonts.thum}

    def test_icao_format_unknown_code_untouched(self):
        # FR24's QQQ filler is not in the reverse table; it renders as-is.
        from setup import fonts

        pairs = self._draw_with_cfg("QQQ", "GLA", {"airport_code_format": "icao"})
        assert dict(pairs) == {"QQQ": fonts.thum, "EGPF": fonts.thum}

    def test_iata_format_home_code_bold(self):
        # Home comparison happens on display codes; the default format
        # keeps both codes the same size.
        from setup import fonts

        pairs = self._draw_with_cfg(
            "GLA",
            "LHR",
            {"airport_code_format": "iata", "home_airport_code": "GLA"},
        )
        assert dict(pairs) == {"GLA": fonts.thum, "LHR": fonts.thum}


class TestShortCodeGeometry:
    """Real font metrics: 4-char codes must clear arrow and panel edge.

    Panel is 64px wide; no-icon text starts at x=3 and icon-mode text
    starts at x=19 after the 18px airline icon.
    """

    def test_four_char_no_icon_clears_arrow_and_panel(self):
        from setup import fonts

        w = fonts.thum.text_width("98KY")
        assert w == 16
        assert 3 + w <= 3 + _ARROW_TIP_OFFSET - _ARROW_WIDTH
        assert 3 + _DEST_OFFSET + w <= 64

    def test_three_char_no_icon_fits(self):
        from setup import fonts

        w = fonts.thum.text_width("GLA")
        assert w == 12
        assert 3 + _DEST_OFFSET + w <= 64

    def test_four_char_icon_clears_arrow_and_panel(self):
        from setup import fonts

        w = fonts.thum.text_width("98KY")
        assert w == 16
        assert 19 + w <= 19 + 25 - _ARROW_WIDTH_SMALL
        last_glyph = fonts.thum.get_glyph(ord("Y"))
        assert last_glyph is not None
        last_glyph_ink_end = (
            19 + 27 + w - last_glyph.dwidth + last_glyph.bbx_xoff + last_glyph.bbx_w
        )
        assert last_glyph_ink_end <= screen.WIDTH

    def test_mixed_pair_tiny_sizes_fit(self):
        # A 3-char code is strictly narrower than the 4-char worst case.
        from setup import fonts

        assert fonts.thum.text_width("GLA") <= fonts.thum.text_width("98KY")


# ---------------------------------------------------------------------------
# CallsignBar / AirlineNameBar / make_callsign_bar
# ---------------------------------------------------------------------------


class TestAirlineNameFromFlight:
    def test_known_airline(self):
        # BAW -> British Airways in airlines.json
        flight = Flight(airline_icao="BAW")
        assert airline_name_from_flight(flight) == "British Airways"

    def test_unknown_airline(self):
        flight = Flight(airline_icao="PPP")
        assert airline_name_from_flight(flight) == ""

    def test_empty_icao(self):
        flight = Flight()
        assert airline_name_from_flight(flight) == ""


class TestMakeCallsignBar:
    def test_callsign_mode_returns_callsign_bar(self):
        cfg = MagicMock()
        cfg.info_bar_mode = "callsign"
        panel, _ = _make_panel_and_canvas()
        bar = make_callsign_bar(cfg, panel)
        assert isinstance(bar, CallsignBar)

    def test_airline_mode_returns_airline_name_bar(self):
        cfg = MagicMock()
        cfg.info_bar_mode = "airline"
        panel, _ = _make_panel_and_canvas()
        bar = make_callsign_bar(cfg, panel)
        assert isinstance(bar, AirlineNameBar)


class TestCallsignBar:
    def test_draw_callsign_text(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = CallsignBar(panel)
        flights = [Flight(callsign="BAW123")]
        bar.draw(canvas, flights, 0)
        assert panel.draw_text.called
        assert panel.draw_square.called  # background blank
        from setup import fonts

        text_call = panel.draw_text.call_args
        assert text_call.args[1] is fonts.thum
        assert text_call.args[3] == 26
        assert all(call.args[2] == 22 for call in panel.draw_square.call_args_list)

    def test_cached_redraw_skips(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = CallsignBar(panel)
        flights = [Flight(callsign="BAW123")]
        bar.draw(canvas, flights, 0)
        panel.reset_mock()
        bar.draw(canvas, flights, 0)
        assert not panel.draw_text.called
        assert not panel.draw_square.called

    def test_reset_clears_cache(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = CallsignBar(panel)
        flights = [Flight(callsign="BAW123")]
        bar.draw(canvas, flights, 0)
        panel.reset_mock()
        bar.reset()
        bar.draw(canvas, flights, 0)
        assert panel.draw_text.called

    def test_draws_index_for_multiple_flights(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = CallsignBar(panel)
        flights = [Flight(callsign="BAW123"), Flight(callsign="UAL456")]
        bar.draw(canvas, flights, 0)
        # The last draw_text call should be the N/M index
        last_call = panel.draw_text.call_args_list[-1]
        assert "1/2" in last_call[0]


class TestAirlineNameBar:
    def test_creates_scroller_on_first_draw(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = AirlineNameBar(panel)
        flights = [Flight(airline_icao="BAW")]
        bar.draw(canvas, flights, 0)
        assert bar.scroller is not None
        assert panel.draw_square.called  # background blank
        assert any(
            call.args[2] == 22 for call in panel.draw_square.call_args_list
        )

    def test_rebuilds_scroller_on_flight_change(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = AirlineNameBar(panel)
        flights = [
            Flight(icao_callsign="BAW123", airline_icao="BAW"),
            Flight(icao_callsign="UAL456", airline_icao="UAL"),
        ]
        bar.draw(canvas, flights, 0)
        first_scroller = bar.scroller
        bar.draw(canvas, flights, 1)
        assert bar.scroller is not first_scroller

    def test_reset_clears_scroller(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = AirlineNameBar(panel)
        flights = [Flight(airline_icao="BAW")]
        bar.draw(canvas, flights, 0)
        bar.reset()
        assert bar.scroller is None

    def test_unknown_airline_falls_back_to_callsign(self):
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        cfg = MagicMock()
        cfg.info_bar_mode = "callsign"
        bar = AirlineNameBar(panel, cfg)
        # PPP is not in airlines.json; falls back to the display callsign
        flights = [Flight(airline_icao="PPP", callsign="PPP123")]
        bar.draw(canvas, flights, 0)
        assert bar.scroller is not None
        # The spans should reconstruct the callsign (split by colour)
        assert "".join(s.text for s in bar.spans) == "PPP123"

    def test_rebuilds_scroller_when_flight_count_changes(self):
        """The scroller width depends on flight_count (index area reserved
        when >1 flight).  Dropping to a single flight must rebuild the
        scroller at full width even when the displayed flight_id is
        unchanged - otherwise the bar keeps its narrow width and leaves
        a blank gap where the N/M index used to be.
        """
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        bar = AirlineNameBar(panel)
        # Two flights sharing the same flight_id (e.g. duplicate feed
        # entries) - callsigns_match sees the set change only via length.
        flights_two = [
            Flight(icao_callsign="BAW123", airline_icao="BAW"),
            Flight(icao_callsign="BAW123", airline_icao="BAW"),
        ]
        bar.draw(canvas, flights_two, 0)
        narrow_scroller = bar.scroller
        assert narrow_scroller is not None
        narrow_width = narrow_scroller.width

        # Rescan drops to a single flight with the same flight_id.
        flights_one = [Flight(icao_callsign="BAW123", airline_icao="BAW")]
        bar.draw(canvas, flights_one, 0)
        assert bar.scroller is not narrow_scroller
        assert bar.scroller.width > narrow_width


# ---------------------------------------------------------------------------
# build_spans - mode selection (0=model, 1=telemetry, 2=custom)
# ---------------------------------------------------------------------------


class TestBuildSpans:
    """Verify build_spans() dispatches to the correct span builder."""

    def _make_scene(self, flights):
        """Build a minimal FlightScene with mocked panel/canvas."""
        from scenes.flight.flight_scene import FlightScene

        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        overhead = MagicMock()
        overhead.error = None
        overhead.new_data = False
        overhead.data = []
        overhead.processing = False
        scene = FlightScene(canvas, panel, overhead, refresh_interval=60)
        scene.flights = flights
        return scene

    def test_mode_0_returns_model_spans(self):
        scene = self._make_scene([Flight(plane="Boeing 787")])
        cfg = MagicMock()
        cfg.details = 0
        spans = scene.build_spans(cfg)
        assert len(spans) == 1
        assert spans[0].text == "BOEING 787"
        from setup import fonts

        assert spans[0].font is fonts.tiny

    def test_mode_1_returns_telemetry_spans(self):
        scene = self._make_scene(
            [Flight(altitude=38000, ground_speed=480, heading=270)]
        )
        cfg = MagicMock()
        cfg.details = 1
        cfg.height_unit = "ft"
        cfg.speed_unit = "kts"
        spans = scene.build_spans(cfg)
        texts = [s.text for s in spans]
        assert "38000" in texts
        assert "480" in texts
        assert "270" in texts
        from setup import fonts

        assert all(span.font is fonts.tiny for span in spans if span.text not in "^~}*")
        assert all(
            span.font is fonts.small_symbols for span in spans if span.text in "^~}*"
        )

    def test_mode_2_returns_custom_spans(self):
        scene = self._make_scene([Flight(plane="Boeing 787", callsign="BAW123")])
        cfg = MagicMock()
        cfg.details = 2
        cfg.details_custom_template = "{callsign} | {plane}"
        cfg.height_unit = "ft"
        cfg.speed_unit = "kts"
        spans = scene.build_spans(cfg)
        texts = [s.text for s in spans if s.text]
        assert "BAW123" in texts
        assert "B788" in texts

    def test_mode_2_keeps_plane_static_and_scrolls_remaining_template(self):
        from scenes.flight.flight_scene import PLANE_DETAILS_Y

        scene = self._make_scene([Flight(plane="Boeing 787", callsign="BAW123")])
        cfg = MagicMock()
        cfg.details = 2
        cfg.details_custom_template = "{plane} | {callsign}"
        scroller = MagicMock()
        scroller.loop_count = 0

        with patch("scenes.flight.flight_scene.Config.instance", return_value=cfg):
            with patch(
                "scenes.flight.flight_scene.Scroller", return_value=scroller
            ) as scroller_factory:
                scene.draw_plane_details()

        static_plane = scene.details_static_span
        assert static_plane is not None
        assert static_plane.text == "B788"
        assert scene.panel.draw_text.call_args.args == (
            scene.canvas,
            static_plane.font,
            0,
            PLANE_DETAILS_Y + 1,
            static_plane.colour,
            "B788",
        )
        args = scroller_factory.call_args.args
        assert args[2] == static_plane.width + 1
        assert args[4] == screen.WIDTH - args[2]
        assert [span.text for span in args[5]] == [" | ", "BAW123"]

    def test_mode_2_scrolls_long_plane_inside_fixed_width_slot(self):
        from scenes.flight.flight_scene import PLANE_DETAILS_Y, PLANE_TYPE_MAX_WIDTH

        scene = self._make_scene(
            [Flight(plane="Airbus A350-1000", callsign="BAW123")]
        )
        cfg = MagicMock()
        cfg.details = 2
        cfg.details_custom_template = "{plane} | {callsign}"
        plane_scroller = MagicMock()
        plane_scroller.loop_count = 0
        details_scroller = MagicMock()
        details_scroller.loop_count = 0

        with patch("scenes.flight.flight_scene.Config.instance", return_value=cfg):
            with patch(
                "scenes.flight.flight_scene.Scroller",
                side_effect=[plane_scroller, details_scroller],
            ) as scroller_factory:
                scene.draw_plane_details()

        plane_args = scroller_factory.call_args_list[0].args
        assert plane_args[2] == 0
        assert plane_args[3] == PLANE_DETAILS_Y + 1
        assert plane_args[4] == PLANE_TYPE_MAX_WIDTH
        assert [span.text for span in plane_args[5]] == ["A350-1000"]

        details_args = scroller_factory.call_args_list[1].args
        assert details_args[2] == PLANE_TYPE_MAX_WIDTH + 1
        assert [span.text for span in details_args[5]] == [" | ", "BAW123"]

    def test_mode_2_empty_template_returns_warning(self):
        from scenes.flight.custom_details import NOT_DEFINED_TEXT

        scene = self._make_scene([Flight(plane="Boeing 787")])
        cfg = MagicMock()
        cfg.details = 2
        cfg.details_custom_template = ""
        cfg.height_unit = "ft"
        cfg.speed_unit = "kts"
        spans = scene.build_spans(cfg)
        assert len(spans) == 1
        assert spans[0].text == NOT_DEFINED_TEXT


class TestRegistrationCallsigns:
    """A bare aircraft registration must never resolve to an airline.

    General aviation aircraft broadcast their registration in the callsign
    field.  Stripped of its dash by the feed, G-BSFE arrives as ``GBSFE`` -
    and its first 3 characters collide with a real ICAO designator (GBS =
    Global Air Services Nigeria).
    """

    def test_uk_registration_rejected(self):
        assert airline_icao_from_flight(Flight(icao_callsign="GBSFE")) == ""

    def test_german_registration_rejected(self):
        assert airline_icao_from_flight(Flight(icao_callsign="DAIZY")) == ""

    def test_irish_registration_rejected(self):
        assert airline_icao_from_flight(Flight(icao_callsign="EIDEA")) == ""

    def test_canadian_registration_rejected(self):
        assert airline_icao_from_flight(Flight(icao_callsign="CGABC")) == ""

    def test_us_registration_rejected(self):
        # N512SP fails the alphabetic-prefix test rather than the digit test.
        assert airline_icao_from_flight(Flight(icao_callsign="N512SP")) == ""

    def test_three_letter_callsign_with_no_flight_number_rejected(self):
        assert airline_icao_from_flight(Flight(icao_callsign="GBS")) == ""

    def test_airline_callsigns_still_resolve(self):
        for callsign, expected in [
            ("BAW117", "BAW"),
            ("UAL1583", "UAL"),
            ("SHT7Z", "SHT"),
            ("EAG56R", "EAG"),
            ("EAI123", "EIN"),
        ]:
            assert airline_icao_from_flight(Flight(icao_callsign=callsign)) == expected

    def test_api_airline_icao_still_wins_for_registration_callsign(self):
        # If a provider positively identified the carrier, the registration
        # shape of the callsign is irrelevant.
        flight = Flight(airline_icao="EIN", icao_callsign="GBSFE")
        assert airline_icao_from_flight(flight) == "EIN"


class TestAirlineNameOwnerFallback:
    def test_owner_used_when_no_airline(self):
        flight = Flight(icao_callsign="GBSFE", owner="Leading Edge Flight Training")
        assert airline_name_from_flight(flight) == "Leading Edge Flight Training"

    def test_airline_name_preferred_over_owner(self):
        flight = Flight(icao_callsign="BAW117", owner="Some Leasing Co")
        assert airline_name_from_flight(flight) == "British Airways"

    def test_owner_used_when_code_not_in_database(self):
        # ZZZ resolves to no airline name -> owner fills the bar instead.
        flight = Flight(airline_icao="ZZZ", owner="Private Owner")
        assert airline_name_from_flight(flight) == "Private Owner"

    def test_empty_when_neither_known(self):
        assert airline_name_from_flight(Flight(icao_callsign="GBSFE")) == ""


# ---------------------------------------------------------------------------
# ICAO journey-code display (airport_code_format)
# ---------------------------------------------------------------------------


class TestJourneyCodeDisplay:
    def _cfg(self, **overrides):
        from setup.configuration import Config

        c = Config.__new__(Config)
        c.data_store = {"airport_code_format": "iata", **overrides}
        return c

    # -- full-name label prefix spans --

    def test_full_label_spans_include_scrollable_arrows_and_names(self):
        from scenes.flight.journey.full_label import build_journey_spans

        cfg = self._cfg(airport_code_format="icao")
        flight = Flight(
            origin="GLA",
            destination="LHR",
            origin_name="Glasgow Airport",
            destination_name="Heathrow Airport",
        )
        origin_spans, dest_spans = build_journey_spans(cfg, flight)
        assert [span.text for span in origin_spans] == ["Glasgow Airport"]
        assert [span.text for span in dest_spans] == ["Heathrow Airport"]
        from setup import fonts

        assert origin_spans[0].font is fonts.thum
        assert dest_spans[0].font is fonts.thum

    def test_full_label_spans_iata_unchanged(self):
        from scenes.flight.journey.full_label import build_journey_spans

        cfg = self._cfg()
        flight = Flight(origin="GLA", destination="LHR")
        origin_spans, dest_spans = build_journey_spans(cfg, flight)
        assert [span.text for span in origin_spans] == ["Unknown"]
        assert [span.text for span in dest_spans] == ["Unknown"]

    def test_full_label_keeps_icao_codes_static_with_airline_icon(self):
        from scenes.flight.journey.full_label import FullNameLabel
        from setup import fonts
        from setup.configuration import Config

        cfg = self._cfg(airport_display_style=1, airport_code_format="icao")
        flight = Flight(
            origin="GLA",
            destination="LHR",
            origin_name="Glasgow Airport",
            destination_name="Heathrow Airport",
        )
        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *args, **kwargs: len(args[-1]) * 5
        label = FullNameLabel(panel)
        scroller = MagicMock()
        scroller.scroll_max = 0
        scroller.all_looped.return_value = True

        with patch.object(Config, "instance", return_value=cfg):
            with patch(
                "scenes.flight.journey.full_label.Scroller", return_value=scroller
            ) as scroller_factory:
                label.draw(canvas, flight, 19, 45, icon_required=True)

        drawn_text = [call.args[-1] for call in panel.draw_text.call_args_list]
        assert "EGPF" in drawn_text
        assert "EGLL" in drawn_text
        assert panel.draw_square.call_args.args[4] == 20
        code_calls = [
            call
            for call in panel.draw_text.call_args_list
            if call.args[-1] in ("EGPF", "EGLL")
        ]
        assert [call.args[3] for call in code_calls] == [5, 16]
        assert all(call.args[1] is fonts.thum for call in code_calls)
        origin_scroll = scroller_factory.call_args_list[0].args
        destination_scroll = scroller_factory.call_args_list[1].args
        assert origin_scroll[2] == 19
        assert destination_scroll[2] == 19
        assert origin_scroll[3] == 5
        assert destination_scroll[3] == 16
        assert [span.text for span in origin_scroll[5]] == ["Glasgow Airport"]
        assert [span.text for span in destination_scroll[5]] == ["Heathrow Airport"]

    # -- scene redraw key: flipping the format must reset the label --

    def _scene_with_spy_label(self):
        from scenes.flight.flight_scene import FlightScene
        from setup.configuration import Config

        panel, canvas = _make_panel_and_canvas()
        panel.draw_text.side_effect = lambda *a, **k: 5
        overhead = MagicMock()
        overhead.error = None
        overhead.new_data = False
        overhead.data = []
        overhead.processing = False
        scene = FlightScene(canvas, panel, overhead, refresh_interval=60)
        scene.journey_label = MagicMock()
        scene.flights = [Flight(origin="GLA", destination="LHR")]
        scene.flight_index = 0
        return scene, Config

    def test_code_format_flip_resets_journey_label(self):
        scene, Config = self._scene_with_spy_label()
        cfg = self._cfg(airport_display_style=0)

        with patch.object(Config, "instance", return_value=cfg):
            scene.draw_journey()
            assert scene.journey_label.reset.call_count == 1  # first draw

            cfg.data_store["airport_code_format"] = "icao"
            scene.draw_journey()
            assert scene.journey_label.reset.call_count == 2  # format flip

            scene.draw_journey()
            assert scene.journey_label.reset.call_count == 2  # unchanged

    def test_same_format_no_extra_reset(self):
        scene, Config = self._scene_with_spy_label()
        cfg = self._cfg(airport_display_style=0)

        with patch.object(Config, "instance", return_value=cfg):
            scene.draw_journey()
            scene.draw_journey()
            assert scene.journey_label.reset.call_count == 1

    def test_journey_starts_at_x3_without_logo(self):
        scene, Config = self._scene_with_spy_label()
        scene.airline_logo = MagicMock(width=0)
        cfg = self._cfg(airport_display_style=1)

        with patch.object(Config, "instance", return_value=cfg):
            scene.draw_journey()

        assert scene.journey_label.draw.call_args.args[2:5] == (3, 61, False)

    def test_journey_starts_after_18px_logo(self):
        scene, Config = self._scene_with_spy_label()
        scene.airline_logo = MagicMock(width=18)
        cfg = self._cfg(airport_display_style=1)

        with patch.object(Config, "instance", return_value=cfg):
            scene.draw_journey()

        assert scene.journey_label.draw.call_args.args[2:5] == (19, 45, True)
