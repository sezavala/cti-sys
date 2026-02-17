from fastapi import HTTPException
from sqlalchemy import select, func, cast, and_, Date
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.postgresql import array_agg

from src.database.postgres.models import Attendance, StudentAttendance, StudentEmail
import gspread
import pandas
import numpy as np
from typing import List, Dict
from datetime import date
from src.config import settings

def fetch_group_attendance(eng: Engine, start_date: date, end_date: date, cti_ids: Dict[int, str]):
    """
    Fetch attendance records and create an attendance matrix of select cti_ids and a date range,
    given the associated Accelerate tables
    @param eng: A SQLAlchemy Engine object that connects to the database
    """
    if not cti_ids:
        return pandas.DataFrame(columns=["cti_id", "email"])

    # 1) Build cti_id -> email mapping, defaulting to "NOT FOUND"
    id_to_email = fetch_cti_emails(eng, cti_ids)

    # 2) All attendance rows for CTI ids and date ranges
    attendance_query = (
        select(
            StudentAttendance.cti_id,
            Attendance.session_id,
            cast(Attendance.session_start, Date).label("session_date"),
        )
        .join(Attendance, Attendance.session_id == StudentAttendance.session_id)
        .where(
            and_(
                StudentAttendance.cti_id.in_(cti_ids),
                cast(Attendance.session_start, Date).between(start_date, end_date),
            )
        )
    )

    attendance_frame = pandas.read_sql(attendance_query, eng)

    # 3) If there are no sessions in this range, return only cti_id + email
    if attendance_frame.empty:
        final_df = pandas.DataFrame(
            [{"cti_id": cid, "email": id_to_email.get(cid, "NOT FOUND")} for cid in cti_ids]
        )
        final_df = final_df.astype(str)
        final_df.index = range(len(final_df))
        return final_df

    # Normalize date
    attendance_frame["session_date"] = pandas.to_datetime(
        attendance_frame["session_date"]
    ).dt.date


    # 4) if a date has only 1 session_id: 'YYYY-MM-DD'
    #    else a date has N sessions: 'YYYY-MM-DD - 1', ..., 'YYYY-MM-DD - N'
    by_date = (
        attendance_frame[["session_id", "session_date"]]
        .drop_duplicates()
        .sort_values(["session_date", "session_id"])
    )

    session_cols = {}
    col_order = []

    # Loop through sessions dates and the session ids that occured on that date
    for session_date, group in by_date.groupby("session_date", sort=True):
        group = group.sort_values("session_id")
        if len(group) == 1:
            # Only one session this date
            col_name = session_date.strftime("%Y-%m-%d")
            sid = group["session_id"].iloc[0]
            session_cols[sid] = col_name
            col_order.append(col_name)
        else:
            # Multiple sessions on same date, enumerate
            for idx, sid in enumerate(group["session_id"], start=1):
                col_name = f"{session_date.strftime('%Y-%m-%d')} - {idx}"
                session_cols[sid] = col_name
                col_order.append(col_name)

    # 5) Build the full result grid
    result_grid = pandas.DataFrame(
        index=pandas.Index(cti_ids, name="cti_id"),
        columns=col_order,
        data=False,
    )

    # 6) Mark True where they attended
    attendance_frame["col_name"] = attendance_frame["session_id"].map(session_cols)
    # Safety
    attendance_frame = attendance_frame.dropna(subset=["col_name"])

    for row in attendance_frame.itertuples(index=False):
        cid = row.cti_id
        col = row.col_name
        if cid in result_grid.index and col in result_grid.columns:
            result_grid.at[cid, col] = True

    # 7) Attach email as first columns
    result_grid.insert(
        0,
        "email",
        [id_to_email.get(cid, "NOT FOUND") for cid in result_grid.index],
    )
    result_grid.insert(0, "cti_id", result_grid.index)

    # 8) Normalize to final form
    final_df = result_grid.reset_index(drop=True)

    # Everything as string for gspread
    final_df = final_df.astype(str)

    return final_df

def fetch_cti_ids_from_sheet(spreadsheet_id: str, worksheet_name: str, gc: gspread.client.Client) -> List[int]:
    sh = gc.open_by_key(spreadsheet_id)
    worksheet = sh.worksheet(worksheet_name)

    headers = worksheet.row_values(1)
    headers = [header.strip().lower() for header in headers]

    try:
        column_index = headers.index("cti_id") + 1
    except ValueError:
        print("Column name not found")
        return
    
    column_values = worksheet.col_values(column_index)

    data = []

    for value in column_values[1:]:
        if value:
            try:
                data.append(int(value))
            except ValueError:
                # Skip
                continue
    
    worksheet.clear()
    
    return data

def fetch_cti_emails(eng: Engine, cti_ids: List[int]) -> Dict[int, str]:
    ids_to_email = dict.fromkeys(cti_ids, "NOT FOUND")

    attendance_query = (
        select(
            StudentEmail.cti_id,
            StudentEmail.email
        )
        .where(
            and_(
                StudentEmail.cti_id.in_(cti_ids),
                StudentEmail.is_primary
            )
        )
    )

    email_frame = pandas.read_sql(attendance_query, eng)
    for index, row in email_frame.iterrows():
        ids_to_email[row.cti_id] = row.email

    return ids_to_email