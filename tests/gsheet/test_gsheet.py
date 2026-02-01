import pytest
from os import environ
import pandas
from datetime import date, timedelta
from src.config import settings

import src.gsheet.utils as utils
import src.gsheet.group_attendance.service as service
from src.database.postgres.core import engine as CONN


class TestGSheetGroup:
    @pytest.mark.integration
    @pytest.mark.gsheet
    def testGroupAttendance(self, monkeypatch, client):
        monkeypatch.setenv("ROSTER_SHEET_KEY", environ.get("TEST_SHEET_KEY"))
        response = client.post(
            "/api/gsheet/group-attendance",
            params={
                "spreadsheet_id": environ.get("TEST_SHEET_KEY"),
                "spreadsheet_name": "Test Group",
                "start_date": date(2025, 10, 1),
                "end_date": date(2025, 10, 15),
            },
        )
        assert response.status_code == 201

        gc = utils.create_credentials()
        output_spreadsheet = gc.open_by_key(environ.get("TEST_SHEET_KEY"))
        output_worksheet = output_spreadsheet.worksheet("Test Group")
        output_df = pandas.DataFrame(output_worksheet.get_all_records())

        cti_ids = service.fetch_cti_ids_from_sheet(
            environ.get("TEST_SHEET_KEY"), "Test Group", gc
        )
        attendance_data = service.fetch_group_attendance(
            CONN, date(2025, 10, 1), date(2025, 10, 15), cti_ids
        )
        utils.write_to_gsheet(
            attendance_data, "Test Group", gc, environ.get("TEST_SHEET_KEY")
        )

        assert output_df.shape == attendance_data.shape

    @pytest.mark.integration
    @pytest.mark.gsheet
    def testDefaultLookbackDays(self, monkeypatch, client):
        monkeypatch.setenv("ROSTER_SHEET_KEY", environ.get("TEST_SHEET_KEY"))

        # 1) Call endpoint WITHOUT dates
        response = client.post(
            "/api/gsheet/group-attendance",
            params={
                "spreadsheet_id": environ.get("TEST_SHEET_KEY"),
                "spreadsheet_name": "Test Group",
            },
        )
        assert response.status_code == 201

        gc = utils.create_credentials()
        output_spreadsheet = gc.open_by_key(environ.get("TEST_SHEET_KEY"))
        output_worksheet = output_spreadsheet.worksheet("Test Group")
        output_df = pandas.DataFrame(output_worksheet.get_all_records())

        # 2) Recompute dates the endpoint should have used
        end_date = date.today()
        start_date = end_date - timedelta(
            days=settings.default_attendance_lookback_days
        )

        # 3) Fetch CTI IDs (this clears), then attendance, then write back
        cti_ids = service.fetch_cti_ids_from_sheet(
            environ.get("TEST_SHEET_KEY"), "Test Group", gc
        )
        attendance_data = service.fetch_group_attendance(
            CONN, start_date, end_date, cti_ids
        )
        utils.write_to_gsheet(
            attendance_data, "Test Group", gc, environ.get("TEST_SHEET_KEY")
        )

        # 4) Now compare shapes
        assert output_df.shape == attendance_data.shape