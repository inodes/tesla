"""fuel_auto.py - when the app should fetch the newest month of NSW FuelCheck prices by itself.

FuelCheck publishes a month's price history early in the next month. The app looks every few hours (app.fuel.auto_sync_hours, 0 = never) and runs
sync_fuel_history.py --sync-all when the prices it holds end before the last day of last month. Only with a fuel database already there (fuel prices
set up once); a month not published yet is simply tried again later.

    latest(data_dir)          -> the last day held ("YYYY-MM-DD"), or "" when there is no fuel database
    due(data_dir, today=None) -> True when last month is not held yet
"""
import datetime
import os
import sqlite3


def latest(data_dir):
    path = os.path.join(data_dir, "fuel", "fuel_history.db")
    if not os.path.isfile(path):
        return ""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            row = con.execute("SELECT MAX(date) FROM fuel_daily_averages").fetchone()
        finally:
            con.close()
        return (row and row[0]) or ""
    except sqlite3.Error:
        return ""


def due(data_dir, today=None):
    have = latest(data_dir)
    if not have:
        return False
    today = today or datetime.date.today()
    last_of_last_month = today.replace(day=1) - datetime.timedelta(days=1)
    return have < last_of_last_month.isoformat()
