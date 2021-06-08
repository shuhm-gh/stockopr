#-*- encoding:utf-8 -*-

import os, sys
import time, datetime

import pandas
import pandas as pd
import xlrd
import urllib.request
import json

import socket

from sqlalchemy import create_engine

sys.path.append(".")

import config.config as config
import util.mysqlcli as mysqlcli
import util.dt as dt
import acquisition.basic as basic
import acquisition.tx as tx
import acquisition.wy as wy
import acquisition.quote_db as quote_db
import acquisition.quote_www as quote_www

# timeout in seconds
timeout = 5
socket.setdefaulttimeout(timeout)

def save_stock_basic_info(xlsfile):
    stock_list = tx.get_stock_list(xlsfile)
    stock_list = sorted(stock_list)
    basic.save_stock_list_into_db(stock_list)


def update_stock_basic_info(xlsfile):
    # http://stock.gtimg.cn/data/get_hs_xls.php?id=ranka&type=1&metric=chr
    stock_list = tx.get_stock_list(xlsfile)
    stock_list = sorted(stock_list)
    basic.upsert_stock_list_into_db(stock_list)


# 指数
def save_sh_index_trade_info():
    val = quote_www.get_price_urllib('999999')
    if val:
        quote_db.insert_into_quote([val,])


def save_quote_tx(xls):
    _xls = xls if xls else tx.download_quote_xls()
    if _xls:
        trade_date = tx.get_trade_date(_xls)
        db_latest_trade_date = quote_db.get_latest_trade_date()
        if trade_date.day == db_latest_trade_date.day:
            return

        val_list = tx.get_quote(_xls)
        quote_db.insert_into_quote(val_list)
        save_stock_basic_info(_xls)
        today = datetime.date.today()
        if today.day == 1:
            update_stock_basic_info(_xls)


# 网易行情接口
def save_quote_wy():
    df_quote = wy.get_quote()

    # 修改数据库
    '''
    update quote set
    percent=FORMAT(percent*100, 2), hs=FORMAT(hs*100, 2), lb=FORMAT(lb, 2),
    wb=FORMAT(wb*100, 2), zf=FORMAT(zf*100, 2), five_minute=FORMAT(five_minute*100, 2)
    where trade_date >= '2017-04-05 00:00:00';
    '''
    # 部分值转换
    key_list = ['PERCENT', 'HS', 'WB', 'ZF', 'FIVE_MINUTE']
    for key in key_list:
        df_quote[key] = round(df_quote[key]*100, 2)
    key = 'LB'
    df_quote[key] = round(df_quote[key], 2)
    # print(df_quote[df_quote['CODE'] == '600839'])

    with mysqlcli.get_cursor() as c:
        try:
            # clear temp table
            c.execute('truncate table temp_quote')

            # MySql connection in sqlAlchemy
            engine = create_engine('mysql+pymysql://{0}:{1}@127.0.0.1:3306/stock?charset=utf8mb4'.format(config.db_user, config.db_passwd))
            connection = engine.connect()

            # Do not insert the row number (index=False)
            df_quote.to_sql(name='temp_quote', con=engine, if_exists='append', index=False, chunksize=20000)
            # connection.close()

            sql_str = "select code, close, high, low, open, yestclose from quote where code in ('000001', '000002', '000003', '000004', '000005') and trade_date in (select max(trade_date) from quote);"
            c.execute(sql_str)
            r1 = c.fetchall()

            sql_str = "select code, close, high, low, open, yestclose from temp_quote where code in ('000001', '000002', '000003', '000004', '000005') and trade_date in (select max(trade_date) from temp_quote);"
            c.execute(sql_str)
            r2 = c.fetchall()

            r1_sorted = sorted(r1, key = lambda x:x['code'])
            r2_sorted = sorted(r2, key = lambda x:x['code'])
            if r1_sorted != r2_sorted:
                c.execute('insert into quote select * from temp_quote;')
                # c.execute('insert into temp_quote_test select * from temp_quote;')
            else:
                print('not trade day')
        except Exception as e:
            print(e)


def save_quote_xl():
    df_quote = tx.get_today_all()
    try:
        # MySql connection in sqlAlchemy
        engine = create_engine('mysql+pymysql://{0}:{1}@127.0.0.1:3306/stock?charset=utf8mb4'.format(config.db_user, config.db_passwd))

        df_quote.loc[:, 'trade_date'] = df_quote.index

        tmp = df_quote['code'].duplicated()
        if tmp.any():
            print('duplicated...')
            df_quote = df_quote[~df_quote['code'].duplicated(keep='first')]

        # df_basic = df_quote.loc[:, ['code', 'name']]
        stock_list = list(zip(df_quote['code'], df_quote['name']))
        # basic.save_stock_list_into_db(stock_list)
        # stock_list = stock_list[:10]
        basic.upsert_stock_list_into_db(stock_list)

        df_quote = df_quote.drop('name', axis=1)
        # Do not insert the row number (index=False)
        df_quote.to_sql(name='quote', con=engine, if_exists='append', index=False, chunksize=20000)
        # df_quote.to_csv('2021-06-07.csv')
    except Exception as e:
        print(e)


def save_quote_tx_one_day(trade_day):
    code_list = basic.get_all_stock_code()
    # code_list = code_list[:5]

    retry_code_list = []
    empty_code_list = []
    df_quote = pandas.DataFrame()
    for n in range(5):
        for code in code_list:
            df = tx.get_kline_data_tx(code, count=1, start_date=trade_day, end_date=trade_day)
            if isinstance(df, pandas.DataFrame):
                if df.empty:
                    empty_code_list.append(code)
                    print('{} no data'.format(code))
                    continue
                if df.index[-1] != trade_day:
                    continue
                df_quote = df_quote.append(df)
            else:
                retry_code_list.append(code)
        # time.sleep(0.5)
        if not retry_code_list:
            break
        code_list = retry_code_list
        print('retry [{}] -\n'.format(len(retry_code_list), str(retry_code_list)))
        print('empty [{}] -\n'.format(len(empty_code_list), str(empty_code_list)))

    try:
        # MySql connection in sqlAlchemy
        engine = create_engine('mysql+pymysql://{0}:{1}@127.0.0.1:3306/stock?charset=utf8mb4'.format(config.db_user, config.db_passwd))

        df_quote.loc[:, 'trade_date'] = df_quote.index

        # Do not insert the row number (index=False)
        df_quote.to_sql(name='quote', con=engine, if_exists='append', index=False, chunksize=20000)
    except Exception as e:
        print(e)


def save_quote(trade_date=None, xls=None):
    # save_quote_tx(xls)
    save_quote_xl()
    # save_quote_wy()
    # save_sh_index_trade_info()


def fix_price_divisor():
    code = '300502'
    df_quote = tx.get_kline_data(code, period='m30', count=250)
    df_tmp: pd.Series = pd.Series(df_quote.iloc[-1], name=datetime.datetime(2021, 6, 9))
    df_tmp['close'] = 50
    df_quote = df_quote.append(df_tmp)
    divisor_date = datetime.datetime(2021, 6, 8)
    df_quote = quote_db.compute_price_divisor(df_quote, divisor_date=divisor_date)
    try:
        # MySql connection in sqlAlchemy
        engine = create_engine('mysql+pymysql://{0}:{1}@127.0.0.1:3306/stock?charset=utf8mb4'.format(config.db_user, config.db_passwd))

        df_quote.loc[:, 'trade_date'] = df_quote.index

        # Do not insert the row number (index=False)
        df_quote.to_sql(name='quote', con=engine, if_exists='append', index=False, chunksize=20000)
    except Exception as e:
        print(e)


if __name__ == '__main__':
    fix_price_divisor()
    exit(0)
    if not dt.istradeday():
        pass
        # exit(0)
    xls = None
    # xls = 'data/xls/2021-05-24.xls'
    save_quote(xls)
    # trade_date = datetime.date(2021, 6, 4)
    # # trade_date = datetime.date.today()
    # save_quote_tx_one_day(trade_date)
