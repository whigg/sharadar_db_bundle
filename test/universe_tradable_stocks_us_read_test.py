import pandas as pd
from sharadar.util.universe import UniverseReader, NamedUniverse
from sharadar.pipeline.engine import make_pipeline_engine
from sharadar.loaders.ingest import get_output_dir
import os
from zipline.pipeline import Pipeline
from zipline.pipeline.data import USEquityPricing
from sharadar.pipeline.filters import TradableStocksUS
import time
import datetime
from zipline.pipeline.factors import CustomFactor
from sharadar.util.logger import log

pipe_start = pd.to_datetime('2019-01-02 ', utc=True)
end = pd.to_datetime('2020-02-28', utc=True)
screen = TradableStocksUS()

universes_db_path = os.path.join(get_output_dir(), "universes.sqlite")

universe_name = 'tradable_stocks_us'

sids = UniverseReader(universes_db_path).get_sid(universe_name, '2020-02-07')
print(len(sids))

spe = make_pipeline_engine()

class DummyFactor1(CustomFactor):
    inputs = []
    window_length = 1
    window_safe = False


    def compute(self, today, assets, out):
        log.info('1', today)
        out[:] = 0

class DummyFactor2(CustomFactor):
    inputs = []
    window_length = 1
    window_safe = False


    def compute(self, today, assets, out):
        log.info('2', today)
        out[:] = 0

pipe = Pipeline(columns={
    'close': USEquityPricing.close.latest,
    'dummy1': DummyFactor1(),
    'dummy2': DummyFactor2()
},
    screen=NamedUniverse(universe_name)
)
start_time = time.time()
stocks = spe.run_pipeline(pipe, pipe_start, end)
print(stocks.shape)
print("--- %s ---" % datetime.timedelta(seconds=(time.time() - start_time)))