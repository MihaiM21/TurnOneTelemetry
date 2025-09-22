import pytest
from unittest.mock import Mock, patch
import pandas as pd
from src.scripts.simple.throttle_comparison import ThrottleComp, ThrottleCompData


class TestThrottleComparison:
    """Test the throttle comparison module"""

    @patch('src.scripts.simple.throttle_comparison.data_aqcuisition.SessionLoader')
    @patch('src.scripts.simple.throttle_comparison.dirOrg.checkForFile')
    def test_throttle_comp_cached_file(self, mock_check_file, mock_session_loader):
        """Test that cached files are returned when available"""
        mock_check_file.return_value = "/path/to/cached/file.png"

        result = ThrottleComp(2025, 1, 'Q')
        assert result == "/path/to/cached/file.png"
        mock_check_file.assert_called_once()

    @patch('src.scripts.simple.throttle_comparison.data_aqcuisition.SessionLoader')
    @patch('src.scripts.simple.throttle_comparison.dirOrg.checkForFile')
    def test_throttle_comp_data_cached_file(self, mock_check_file, mock_session_loader):
        """Test that cached JSON files are returned when available"""
        mock_check_file.return_value = "/path/to/cached/file.json"

        result = ThrottleCompData(2025, 1, 'Q')
        assert result == "/path/to/cached/file.json"
        mock_check_file.assert_called_once()

    @patch('src.scripts.simple.throttle_comparison.plt.savefig')
    @patch('src.scripts.simple.throttle_comparison.data_aqcuisition.SessionLoader')
    @patch('src.scripts.simple.throttle_comparison.dirOrg.checkForFile')
    @patch('src.scripts.simple.throttle_comparison.dirOrg.checkForFolder')
    def test_throttle_comp_generates_plot(self, mock_check_folder, mock_check_file, mock_session_loader, mock_savefig):
        """Test that a new plot is generated when no cached file exists"""
        mock_check_file.return_value = "NULL"

        # Mock session and data
        mock_session = Mock()
        mock_session.event = {'EventName': 'Test GP'}
        mock_session.name = 'Qualifying'
        mock_session.laps = pd.DataFrame({
            'Driver': ['VER', 'HAM', 'LEC'],
        })

        mock_session_loader.return_value.get_session.return_value = mock_session

        # Mock telemetry data
        mock_telemetry = Mock()
        mock_telemetry.__getitem__ = Mock(return_value=[80, 85, 90])
        mock_session.laps.pick_driver.return_value.pick_fastest.return_value.get_car_data.return_value.add_distance.return_value = mock_telemetry

        with patch('src.scripts.simple.throttle_comparison.pd.unique') as mock_unique:
            mock_unique.return_value = ['VER', 'HAM', 'LEC']

            with patch('src.scripts.simple.throttle_comparison.get_driver_color') as mock_color:
                mock_color.return_value = '#FF0000'

                result = ThrottleComp(2025, 1, 'Q')

                assert "outputs/plots/2025/Test GP/Q/Throttle comparison 2025 Test GP Qualifying .png" in result
                mock_savefig.assert_called_once()
