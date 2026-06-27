import yfinance as yf
import pandas as pd
import requests
import akshare as ak
import os
import datetime

# ================= 核心系统配置 =================
SENDKEY = os.environ.get('SERVERCHAN_KEY')

# 【绝对路径锁定】动态获取当前脚本所在目录，防止本地运行存错位置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, 'portfolio.csv')
REPORT_DIR = os.path.join(BASE_DIR, 'reports')
# ================================================

def load_portfolio():
    if not os.path.exists(CSV_FILE): 
        print(f"⚠️ 未找到 {CSV_FILE} 文件，系统终止。")
        return [], [], []
    
    try:
        try:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='utf-8', dtype=str)
        except UnicodeDecodeError:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='gbk', dtype=str)
            
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        if 'name' not in df.columns:
            df['name'] = df['ticker']
        else:
            df['name'] = df['name'].fillna(df['ticker'])
            
        yf_assets = df[df['type'].str.strip().str.lower() == 'yf'][['ticker', 'name']].to_dict('records')
        crypto_assets = df[df['type'].str.strip().str.lower() == 'crypto'][['ticker', 'name']].to_dict('records')
        fund_assets = df[df['type'].str.strip().str.lower() == 'jj'][['ticker', 'name']].to_dict('records')
        
        return yf_assets, crypto_assets, fund_assets
    except Exception as e:
        print(f"清单读取与解析失败: {e}")
        return [], [], []

def align_to_last_friday(df):
    """【时间轴强锁核心】降维打击接口错位，手动生成纯净周线"""
    if df is None or df.empty: return None
    try:
        if df.index.tz is not None: df.index = df.index.tz_localize(None)
        today = pd.Timestamp.today().normalize()
        days_since_friday = (today.dayofweek - 4) % 7
        last_friday = today - pd.Timedelta(days=days_since_friday)
        
        df_filtered = df[df.index <= (last_friday + pd.Timedelta(hours=23, minutes=59))]
        if df_filtered.empty: return None
            
        weekly_df = df_filtered.resample('W-FRI').agg({
            'close': 'last', 'high': 'max', 'low': 'min'
        }).dropna()
        if len(weekly_df) < 2: return None
        return weekly_df
    except Exception as e:
        print(f"时间轴对齐失败: {e}")
        return None

def analyze_asset(weekly_df, item, calc_signals=True):
    weekly_df.columns = [str(c).lower() for c in weekly_df.columns]
    prev_week = weekly_df.iloc[-2]
    last_week = weekly_df.iloc[-1]
    
    weekly_return = ((last_week['close'] - prev_week['close']) / prev_week['close']) * 100
    if weekly_return > 0: perf_str = f"+{weekly_return:.2f}% 🔴"
    elif weekly_return < 0: perf_str = f"{weekly_return:.2f}% 🟢"
    else: perf_str = f"0.00% ⚪"
    
    cross_signal, extremum_signal = "", ""
    if calc_signals and len(weekly_df) >= 20:
        weekly_df['ma5'] = weekly_df['close'].rolling(window=5).mean()
        weekly_df['ma20'] = weekly_df['close'].rolling(window=20).mean()
        
        pw_ma5, pw_ma20 = weekly_df.iloc[-2]['ma5'], weekly_df.iloc[-2]['ma20']
        lw_ma5, lw_ma20 = weekly_df.iloc[-1]['ma5'], weekly_df.iloc[-1]['ma20']
        
        if pw_ma5 >= pw_ma20 and lw_ma5 < lw_ma20: cross_signal = "⚠️ **死叉离场** (5周下穿20周)"
        elif pw_ma5 <= pw_ma20 and lw_ma5 > lw_ma20: cross_signal = "✅ **金叉确立** (5周上穿20周)"
            
        if len(weekly_df) >= 52:
            past_52_weeks = weekly_df.iloc[-53:-1]
            if last_week['close'] >= past_52_weeks['high'].max(): extremum_signal = "🚀 **突破一年新高**"
            elif last_week['close'] <= past_52_weeks['low'].min(): extremum_signal = "🩸 **跌破一年新低**"
                
    return {
        "ticker": item['ticker'], "name": item['name'], "raw_return": weekly_return,
        "performance": perf_str, "cross_signal": cross_signal, "extremum_signal": extremum_signal
    }

def fetch_yf_data(item):
    ticker = item['ticker']
    clean_code = str(ticker).split('.')[0]
    
    # === 防线 A: Yahoo Finance ===
    try:
        yf_ticker = ticker
        if '.HK' in ticker and len(clean_code) == 5 and clean_code.startswith('0'):
            yf_ticker = f"{clean_code[1:]}.HK"
        data = yf.Ticker(yf_ticker).history(period="2y", interval="1d")
        if len(data) > 0:
            aligned_df = align_to_last_friday(data)
            if aligned_df is not None:
                return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception:
        pass
        
    # === 防线 B: 东方财富双模容错专线 (新增全量美股接管) ===
    try:
        df = pd.DataFrame()
        if '.HK' in ticker:
            df = ak.stock_hk_hist(symbol=clean_code, period="daily", adjust="qfq")
        elif '.SS' in ticker or '.SZ' in ticker:
            if clean_code.startswith(('15', '16', '18', '50', '51', '56', '58')):
                try: df = ak.fund_etf_hist_em(symbol=clean_code, period="daily", adjust="qfq")
                except Exception: df = ak.fund_etf_hist_em(symbol=clean_code, period="daily", adjust="")
            else:
                try: df = ak.stock_zh_a_hist(symbol=clean_code, period="daily", adjust="qfq")
                except Exception: df = ak.stock_zh_a_hist(symbol=clean_code, period="daily", adjust="")
        else:
            # 🎯 针对美股的修复：自动遍历纳斯达克(105), 纽交所(106), 美交所(107)
            us_symbol = str(ticker).replace('-', '.') # 兼容 BRK-B 写法
            for prefix in ['105', '106', '107']:
                try:
                    df = ak.stock_us_hist(symbol=f"{prefix}.{us_symbol}", period="daily", adjust="qfq")
                    if df is not None and not df.empty: break
                except Exception:
                    df = pd.DataFrame()
                
        if df is not None and not df.empty:
            df.columns = [str(c).strip() for c in df.columns]
            rename_dict = {'日期': 'date', '开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low'}
            df.rename(columns=rename_dict, inplace=True)
            
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                
            df.columns = [str(c).lower() for c in df.columns]
            if 'close' in df.columns:
                if 'high' not in df.columns: df['high'] = df['close']
                if 'low' not in df.columns: df['low'] = df['close']
                
                aligned_df = align_to_last_friday(df)
                if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception:
        pass

    # === 防线 C: 腾讯日线兜底 ===
    try:
        target_code = ''
        if '.SS' in ticker: target_code = 'sh' + clean_code
        elif '.SZ' in ticker: target_code = 'sz' + clean_code
        elif '.HK' in ticker: target_code = 'hk' + clean_code
        else: target_code = 'us' + str(ticker).replace('-', '.')
            
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={target_code},day,,,400,qfq"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            json_data = res.json()
            if 'data' in json_data and target_code in json_data['data']:
                stock_data = json_data['data'][target_code]
                k_data = stock_data.get('fqday', stock_data.get('day', []))
                
                if k_data and len(k_data) >= 5:
                    cleaned_rows = []
                    for row in k_data:
                        if len(row) >= 6:
                            cleaned_rows.append({
                                'date': row[0], 'open': float(row[1]), 'close': float(row[2]),
                                'high': float(row[3]), 'low': float(row[4])
                            })
                    
                    df = pd.DataFrame(cleaned_rows)
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    
                    aligned_df = align_to_last_friday(df)
                    if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception:
        pass

    return None

def fetch_fund_data(item):
    try:
        clean_ticker = str(item['ticker']).replace('jj', '').strip()
        df = ak.fund_open_fund_info_em(symbol=clean_ticker, indicator="单位净值走势")
        if df is None or df.empty: return None
        
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df.set_index('净值日期', inplace=True)
        df.rename(columns={'单位净值': 'close'}, inplace=True)
        df['high'] = df['close']
        df['low'] = df['close']
        
        aligned_df = align_to_last_friday(df)
        if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=False)
    except: return None

def fetch_crypto_data(item):
    """🎯 专治加密货币：无缝衔接 Coinbase 机构公开 API，彻底绕开雅虎与云端封锁"""
    ticker = item['ticker']
    try:
        yf_symbol = str(ticker).upper().replace('/USDT', '-USD').replace('/USDC', '-USD')
        data = yf.Ticker(yf_symbol).history(period="1y", interval="1d")
        if len(data) > 0:
            aligned_df = align_to_last_friday(data)
            if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=False)
    except Exception:
        pass

    try:
        # 提取币种核心符号 (如 BTC/USDT 提取 BTC)
        base_coin = str(ticker).upper().split('/')[0] 
        # 直接调用 Coinbase Pro 公开接口，获取每日 K 线 (86400 秒 = 1天)
        url = f"https://api.exchange.coinbase.com/products/{base_coin}-USD/candles?granularity=86400"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        
        if res.status_code == 200:
            candles = res.json()
            if candles:
                # Coinbase 返回格式: [ time, low, high, open, close, volume ]
                df = pd.DataFrame(candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
                df['date'] = pd.to_datetime(df['time'], unit='s')
                df.set_index('date', inplace=True)
                df = df.sort_index()
                
                aligned_df = align_to_last_friday(df)
                if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=False)
    except Exception:
        pass

    return None

def send_and_archive_report(yf_reps, crypto_reps, fund_reps, failed_list):
    yf_reps.sort(key=lambda x: x['raw_return'], reverse=True)
    crypto_reps.sort(key=lambda x: x['raw_return'], reverse=True)
    fund_reps.sort(key=lambda x: x['raw_return'], reverse=True)

    focus_pool = [r for r in yf_reps if r['cross_signal'] or r['extremum_signal']]
    
    md = "## 🎯 核心阵地异动 (仅股/ETF)\n---\n"
    if focus_pool:
        for r in focus_pool:
            md += f"> **{r['name']}** ({r['ticker']})\n"
            md += f"> 表现: {r['performance']}\n"
            if r['cross_signal']: md += f"> 趋势: {r['cross_signal']}\n"
            if r['extremum_signal']: md += f"> 位置: {r['extremum_signal']}\n\n"
    else:
        md += "> *本周常规池无标的触发均线交叉或极值。*\n\n"

    md += "## 📊 资产涨跌幅龙虎榜\n---\n"
    if yf_reps:
        md += "### 🏛️ 股票与场内 ETF\n"
        for r in yf_reps: md += f"- **{r['name']}** `{r['performance']}`\n"
        md += "\n"
    if fund_reps:
        md += "### 🏦 场外公募基金\n"
        for r in fund_reps: md += f"- **{r['name']}** `{r['performance']}`\n"
        md += "\n"
    if crypto_reps:
        md += "### 🪙 加密货币\n"
        for r in crypto_reps: md += f"- **{r['name']}** `{r['performance']}`\n"
        md += "\n"

    if failed_list:
        md += "## ⚠️ 核心盲区公示 (多通道均告破)\n---\n"
        md += "> 以下标的未获取到有效对齐数据：\n> \n"
        for fail in failed_list: md += f"> - **{fail['name']}** ({fail['ticker']})\n"

    try:
        if not os.path.exists(REPORT_DIR): os.makedirs(REPORT_DIR)
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        file_path = os.path.join(REPORT_DIR, f"{today_str}_weekly_report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# 📈 资产网格周报 ({today_str})\n\n" + md)
        print(f"✅ 报告已归档，路径: {file_path}")
    except Exception as e:
        print(f"⚠️ 报告归档失败: {e}")

    if not SENDKEY: 
        print("未检测到 SERVERCHAN_KEY 环境变量，跳过推送。")
        return
        
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    res = requests.post(url, data={"title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 资产网格周报", "desp": md})
    print("Server酱 推送响应:", res.text)

if __name__ == "__main__":
    start_time = datetime.datetime.now()
    print(f"[{start_time}] 引擎点火，执行时空强制锁定抽取(含美股与Crypto抗封锁补丁)...")
    
    yf_assets, crypto_assets, fund_assets = load_portfolio()
    yf_reports, crypto_reports, fund_reports = [], [], []
    failed_assets = [] 
    
    for item in yf_assets:
        res = fetch_yf_data(item)
        if res: yf_reports.append(res)
        else: failed_assets.append(item)
        
    for item in crypto_assets:
        res = fetch_crypto_data(item)
        if res: crypto_reports.append(res)
        else: failed_assets.append(item)
        
    for item in fund_assets:
        res = fetch_fund_data(item)
        if res: fund_reports.append(res)
        else: failed_assets.append(item)
            
    send_and_archive_report(yf_reports, crypto_reports, fund_reports, failed_assets)
    print(f"扫描完毕，总耗时: {(datetime.datetime.now() - start_time).seconds} 秒")
