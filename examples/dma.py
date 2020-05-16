from zipline.api import order_target, record, symbol
import matplotlib.pyplot as plt
import pyfolio as pf
import pandas as pd
from sharadar.util.run_algo import run_algorithm
from collections import OrderedDict
from sharadar.util import performance
from sharadar.util.performance import change_extension
from logbook import Logger, FileHandler, DEBUG, INFO, StreamHandler
import sys
import os
log = Logger('Algorithm')
path, ext = os.path.splitext(__file__)
log.handlers.append(FileHandler(path + ".log", level=DEBUG, bubble=True))
log.handlers.append(StreamHandler(sys.stdout, level=INFO))



# silence warnings
import warnings
warnings.filterwarnings('ignore')

def initialize(context):
    context.i = 0
    context.asset = symbol('AAPL')
    log.info("*  *  *  I N I T  *  *  *")


def handle_data(context, data):
    # Skip first 300 days to get full windows
    context.i += 1
    if context.i < 300:
        return

    # Compute averages
    # data.history() has to be called with the same params
    # from above and returns a pandas dataframe.
    short_mavg = data.history(context.asset, 'price', bar_count=100, frequency="1d").mean()
    long_mavg = data.history(context.asset, 'price', bar_count=300, frequency="1d").mean()

    # Trading logic
    if short_mavg > long_mavg:
        # order_target orders as many shares as needed to
        # achieve the desired number of shares.
        order_target(context.asset, 100)
    elif short_mavg < long_mavg:
        order_target(context.asset, 0)

    # Save values for later inspection
    record(AAPL=data.current(context.asset, 'price'),
           short_mavg=short_mavg,
           long_mavg=long_mavg)


def analyze_old(context, perf):
    fig = plt.figure()
    ax1 = fig.add_subplot(211)
    perf.portfolio_value.plot(ax=ax1)
    ax1.set_ylabel('portfolio value in $')

    ax2 = fig.add_subplot(212)
    perf['AAPL'].plot(ax=ax2)
    perf[['short_mavg', 'long_mavg']].plot(ax=ax2)

    perf_trans = perf.ix[[t != [] for t in perf.transactions]]
    buys = perf_trans.ix[[t[0]['amount'] > 0 for t in perf_trans.transactions]]
    sells = perf_trans.ix[
        [t[0]['amount'] < 0 for t in perf_trans.transactions]]
    ax2.plot(buys.index, perf.short_mavg.ix[buys.index],
             '^', markersize=10, color='m')
    ax2.plot(sells.index, perf.short_mavg.ix[sells.index],
             'v', markersize=10, color='k')
    ax2.set_ylabel('price in $')
    plt.legend(loc=0)
    plt.show()

def analyze(context, perf):
    performance.analyze(context, perf, __file__)

def run_this_algorithm(initial_input=1e6, start_date='2011-01-01', end_date='2020-01-01'):
    # runs the zipline ALGO function
    run_algorithm(start=pd.Timestamp(start_date, tz='utc'),
                  end=pd.Timestamp(end_date, tz='utc'),
                  capital_base=initial_input,
                  initialize=initialize,
                  handle_data=handle_data,
                  analyze=analyze,
                  output=change_extension(__file__, '_perf.pickle'),
                  state_filename=change_extension(__file__, '_context.pickle')
                  )

if __name__ == "__main__":
    # execute only if run as a script
    run_this_algorithm()