import pandas as pd
import numpy as np
import sqlite3
import click
from contextlib import closing
from zipline.utils.calendars import get_calendar
from zipline.data.session_bars import SessionBarReader
from sharadar.util.logger import log
from zipline.data.adjustments import SQLiteAdjustmentWriter
from six import (
    iteritems,
    string_types,
    viewkeys,
)

from zipline.data.bar_reader import (
    NoDataBeforeDate,
)

from singleton_decorator import singleton
from sharadar.util.cache import cached

SCHEMA = """
CREATE TABLE IF NOT EXISTS "properties" (
"key" TEXT,
  "0" TEXT
);
CREATE INDEX "ix_properties_key" ON "properties" ("key");

CREATE TABLE IF NOT EXISTS "prices" (
  "date" TIMESTAMP NOT NULL,
  "sid" INTEGER NOT NULL,
  "open" REAL NOT NULL,
  "high" REAL NOT NULL,
  "low" REAL NOT NULL,
  "close" REAL NOT NULL,
  "volume" REAL NOT NULL,
  PRIMARY KEY (date, sid)
);
CREATE INDEX  "ix_prices_date" ON "prices" ("date");
CREATE INDEX "ix_prices_sid" ON "prices" ("sid");
"""

SCHEMA_ADJUST = """
CREATE TABLE IF NOT EXISTS "splits" (
"index" INTEGER,
  "effective_date" INTEGER,
  "ratio" REAL,
  "sid" INTEGER,
  PRIMARY KEY (effective_date, sid)
);
CREATE INDEX IF NOT EXISTS "ix_splits_index"ON "splits" ("index");

CREATE TABLE IF NOT EXISTS "mergers" (
"index" INTEGER,
  "effective_date" INTEGER,
  "ratio" REAL,
  "sid" INTEGER,
  PRIMARY KEY (effective_date, sid)
);
CREATE INDEX IF NOT EXISTS "ix_mergers_index"ON "mergers" ("index");

CREATE TABLE IF NOT EXISTS "dividend_payouts" (
"date" TIMESTAMP,
  "amount" REAL,
  "sid" INTEGER,
  "record_date" INTEGER,
  "declared_date" INTEGER,
  "pay_date" INTEGER,
  "ex_date" INTEGER,
  PRIMARY KEY (date, sid)
);
CREATE INDEX IF NOT EXISTS "ix_dividend_payouts_date"ON "dividend_payouts" ("date");

CREATE TABLE IF NOT EXISTS "stock_dividend_payouts" (
"index" INTEGER,
  "sid" INTEGER,
  "ex_date" INTEGER,
  "declared_date" INTEGER,
  "record_date" INTEGER,
  "pay_date" INTEGER,
  "payment_sid" INTEGER,
  "ratio" REAL,
  PRIMARY KEY (sid, ex_date)
);
CREATE INDEX IF NOT EXISTS "ix_stock_dividend_payouts_index"ON "stock_dividend_payouts" ("index");

CREATE TABLE IF NOT EXISTS "dividends" (
"index" INTEGER,
  "effective_date" INTEGER,
  "ratio" REAL,
  "sid" INTEGER,
  PRIMARY KEY (effective_date, sid)
);

CREATE INDEX IF NOT EXISTS "ix_dividends_index"ON "dividends" ("index");
CREATE INDEX IF NOT EXISTS splits_sids ON splits(sid);
CREATE INDEX IF NOT EXISTS splits_effective_date ON splits(effective_date);
CREATE INDEX IF NOT EXISTS mergers_sids ON mergers(sid);
CREATE INDEX IF NOT EXISTS mergers_effective_date ON mergers(effective_date);
CREATE INDEX IF NOT EXISTS dividends_sid ON dividends(sid);
CREATE INDEX IF NOT EXISTS dividends_effective_date ON dividends(effective_date);
CREATE INDEX IF NOT EXISTS dividend_payouts_sid ON dividend_payouts(sid);
CREATE INDEX IF NOT EXISTS dividends_payouts_ex_date ON dividend_payouts(ex_date);
CREATE INDEX IF NOT EXISTS stock_dividend_payouts_sid ON stock_dividend_payouts(sid);
CREATE INDEX IF NOT EXISTS stock_dividends_payouts_ex_date ON stock_dividend_payouts(ex_date);
"""
# Sqlite Maximum Number Of Columns in a table or query
SQLITE_MAX_COLUMN = 2000

@singleton
class SQLiteDailyBarWriter(object):
    def __init__(self, filename, calendar):
        self._filename = filename
        self._calendar = calendar

        # Create schema, if not exists
        with closing(sqlite3.connect(self._filename)) as con, con, closing(con.cursor()) as c:
            c.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='prices'")
            if c.fetchone()[0] == 0:
                c.executescript(SCHEMA)


    def _validate(self, data):
        if not isinstance(data, pd.DataFrame):
            raise ValueError("data must be an instance of DataFrame.")
        if data.index.names != ['date', 'sid']:
            raise ValueError("data indexes must be ['date', 'sid'].")

    def write(self, data):
        self._validate(data)

        df = data[['open', 'high', 'low', 'close', 'volume']]
        with closing(sqlite3.connect(self._filename)) as con, con, closing(con.cursor()) as c:
            properties = pd.Series({'calendar_name' : self._calendar.name})
            properties.to_sql('properties', con, index_label='key', if_exists="replace")

            with click.progressbar(length=len(df), label="Inserting price data...") as pbar:     
                count = 0
                for index, row in df.iterrows():
                    sql = "INSERT OR REPLACE INTO prices (date, sid, open, high, low, close, volume) VALUES ('%s',%f,%f,%f,%f,%f,%f)"
                    values = index + tuple(row.values)
                    try:
                        c.execute(sql % values)
                    except sqlite3.OperationalError as e:
                        log.error(str(e) + "; values: " + str(values))
                    count += 1
                    pbar.update(count)

@singleton
class SQLiteDailyBarReader(SessionBarReader):
    """
    Reader for pricing data written by SQLiteDailyBarWriter.


    See Also
    --------
    zipline.data.us_equity_pricing.BcolzDailyBarReader
    """
    def __init__(self, filename):
        self._filename = filename

    def _query(self, sql):
        with closing(sqlite3.connect(self._filename)) as con, con, closing(con.cursor()) as c:
            c.execute(sql)
            return c.fetchall()
        
    def _exist_sid(self, sid):
        sql = "SELECT COUNT(DISTINCT(sid)) FROM prices WHERE sid = %d" % sid
        res = self._query(sql)
        return res[0][0] == 1

    def _fmt_date(self, dt):
        return pd.to_datetime(dt).strftime('%Y-%m-%d') + " 00:00:00"

    @cached
    def get_value(self, sid, dt, field):
        day = self._fmt_date(dt)
        sql = "SELECT %s FROM prices WHERE sid = %d and date = '%s'" % (field, sid, day)
        res = self._query(sql)
        if len(res) == 0:
            if self._exist_sid(sid):
                raise NoDataBeforeDate("No data on or before day={0} for sid={1}".format(dt, sid))
            else:
                raise KeyError(sid)
        return res[0][0]

    @cached
    def load_dataframe(self, field, start_dt, end_dt, sids):
        data = self.load_raw_arrays([field], start_dt, end_dt, sids)
        sessions = self.trading_calendar.sessions_in_range(start_dt, end_dt)
        df = pd.DataFrame(data[0], index=sessions)
        df.columns = sids
        return df

    @cached
    def load_series(self, field, start_dt, end_dt, sid):
        data = self.load_raw_arrays([field], start_dt, end_dt, [sid])
        sessions = self.trading_calendar.sessions_in_range(start_dt, end_dt)
        return pd.Series(data[0][:, 0], index=sessions)

    @cached
    def load_raw_arrays(self, fields, start_dt, end_dt, sids):
        start_day = self._fmt_date(start_dt)
        end_day = self._fmt_date(end_dt)
        sessions = self.trading_calendar.sessions_in_range(start_dt, end_dt)
        log.debug("Loading raw arrays for %d assets (%s)." % (len(sids), type(sids)))

        if any(not isinstance(x, (int, np.integer)) for x in sids):
            sids = [x.sid for x in sids]

        raw_arrays = []
        with sqlite3.connect(self._filename) as conn:
            for field in fields:
                query = "SELECT date, sid, %s FROM prices WHERE sid in (%s) and date >= '%s' AND date <= '%s';" \
                        % (field, ",".join(map(str, sids)), str(start_day), str(end_day))
                df = pd.read_sql_query(query, conn)
                result = df.pivot(index='date', columns='sid', values=field)
                result = result.reindex(index=list(map(self._fmt_date, sessions)), columns=sids)
                raw_arrays.append(result.values)

        return raw_arrays

    def get_last_traded_dt(self, sid, dt):
        day = self._fmt_date(dt)
        sql = "SELECT date FROM prices WHERE sid = %d and date = '%s'" % (sid, day)
        res = self._query(sql)
        if len(res) == 0:
            if self._exist_sid(sid):
                return pd.NaT
            else:
                raise KeyError(sid)

        return pd.Timestamp(res[0][0], tz='UTC')

    @property
    def last_available_dt(self):
        sql = "SELECT MAX(date) FROM prices"
        res = self._query(sql)
        if len(res) == 0:
            return pd.NaT
        return pd.Timestamp(res[0][0], tz='UTC')

    @property
    def trading_calendar(self):
        sql = 'SELECT "0" FROM properties WHERE key="calendar_name"'
        res = self._query(sql)
        if len(res) == 0:
            raise ValueError("No trading calendar defined.")
        return get_calendar(res[0][0])

    @property
    def first_trading_day(self):
        sql = "SELECT MIN(date) FROM prices"
        res = self._query(sql)
        if len(res) == 0:
            return pd.NaT
        return pd.Timestamp(res[0][0], tz='UTC')

    @property
    def sessions(self):
        cal = self.trading_calendar
        return cal.sessions_in_range(self.first_trading_day, self.last_available_dt)


class SQLiteDailyAdjustmentWriter(SQLiteAdjustmentWriter):


    def __init__(self, filename, equity_daily_bar_reader, calendar):
        self._filename = filename
        self._equity_daily_bar_reader = equity_daily_bar_reader
        self._calendar = calendar

        # Create schema, if not exists
        with closing(sqlite3.connect(self._filename)) as con, con, closing(con.cursor()) as c:
            c.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='dividends'")
            if c.fetchone()[0] == 0:
                c.executescript(SCHEMA_ADJUST)

    def _write(self, tablename, expected_dtypes, frame):
        if frame is None or frame.empty:
            # keeping the dtypes correct for empty frames is not easy
            frame = pd.DataFrame(
                np.array([], dtype=list(expected_dtypes.items())),
            )
        else:
            if frozenset(frame.columns) != frozenset(expected_dtypes):
                raise ValueError(
                    "Unexpected frame columns:\n"
                    "Expected Columns: %s\n"
                    "Received Columns: %s" % (
                        set(expected_dtypes),
                        frame.columns.tolist(),
                    )
                )

            actual_dtypes = frame.dtypes
            for colname, expected in iteritems(expected_dtypes):
                actual = actual_dtypes[colname]
                if not np.issubdtype(actual, expected):
                    raise TypeError(
                        "Expected data of type {expected} for column"
                        " '{colname}', but got '{actual}'.".format(
                            expected=expected,
                            colname=colname,
                            actual=actual,
                        ),
                    )

        with closing(sqlite3.connect(self._filename)) as con, con, closing(con.cursor()) as c:
            with click.progressbar(length=len(frame), label="Inserting price data...") as pbar:
                count = 0
                for index, row in frame.iterrows():
                    sql = "INSERT OR REPLACE INTO %s VALUES ('%s', %s)"
                    cmd = sql % (tablename, index, ', '.join(map(str, row.values)))
                    try:
                        c.execute(cmd)
                    except sqlite3.OperationalError as e:
                        log.error(str(e) + ": " + cmd)
                    count += 1
                    pbar.update(count)

    def write(self, splits=None, mergers=None, dividends=None, stock_dividends=None):
        self.write_frame('splits', splits)
        self.write_frame('mergers', mergers)
        self.write_dividend_data(dividends, stock_dividends)

