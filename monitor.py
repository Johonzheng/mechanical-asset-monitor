import yfinance as yf
import pandas as pd
import requests
import akshare as ak
import os
import datetime

# ================= 核心系统配置 =================
SENDKEY = os.environ.get('SERVERCHAN_KEY')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, 'portfolio.csv')
REPORT_DIR = os.path.join(BASE_DIR, 'reports')
# ================================================

def load_portfolio():
    if not os.path.exists(CSV_FILE): 
        print(f"⚠️ 未找到 {CSV_FILE} 文件，系统终止。")
        return []
    
    try:
        try:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='utf-8', dtype=str)
        except UnicodeDecodeError:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='gbk', dtype=str)
            
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        if 'active' not in df.columns:
            df['active'] = 'y'
        else:
            df['active'] = df['active'].fillna('y').str.strip().str.lower()
            
        if 'name' not in df.columns:
            df['name'] = df['ticker']
        else:
            df['name'] = df['name'].fillna(df['ticker'])
            
        if 'pool' not in df.columns:
            df['pool'] = 'core'
        else:
            df['pool'] = df['pool'].fillna('core').str.strip().str.lower()
            
        return df[['ticker', 'name', 'type', 'pool', 'active']].to_dict('records')
    except Exception as e:
        print(f"清单读取与解析失败: {e}")
        return []

def align_to_last_friday(df):
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
        
        if pw_ma5 >= pw_ma20 and lw_ma5 < lw_ma20: cross_signal = "死叉"
        elif pw_ma5 <= pw_ma20 and lw_ma5 > lw_ma20: cross_signal = "金叉"
            
        if len(weekly_df) >= 52:
            past_52_weeks = weekly_df.iloc[-53:-1]
            if last_week['close'] >= past_52_weeks['high'].max(): extremum_signal = "新高"
            elif last_week['close'] <= past_52_weeks['low'].min(): extremum_signal = "新低"
                
    return {
        "ticker": item['ticker'], "name": item['name'], "type": item['type'], 
        "pool": item['pool'], "active": item['active'],
        "raw_return": weekly_return, "performance": perf_str, 
        "cross_signal": cross_signal, "extremum_signal": extremum_signal
    }

def fetch_yf_data(item):
    ticker = item['ticker']
    clean_code = str(ticker).split('.')[0]
    
    try:
        yf_ticker = ticker
        if '.HK' in ticker and len(clean_code) == 5 and clean_code.startswith('0'):
            yf_ticker = f"{clean_code[1:]}.HK"
        data = yf.Ticker(yf_ticker).history(period="2y", interval="1d")
        if len(data) > 0:
            aligned_df = align_to_last_friday(data)
            if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception:
        pass
        
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
            us_variants = [
                str(ticker), str(ticker).replace('-', '.'),
                str(ticker).replace('-', '_'), str(ticker).replace('-', '')
            ]
            for variant in us_variants:
                for prefix in ['105', '106', '107']:
                    try:
                        df = ak.stock_us_hist(symbol=f"{prefix}.{variant}", period="daily", adjust="qfq")
                        if df is not None and not df.empty: break
                    except Exception: pass
                if df is not None and not df.empty: break
                
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
    except Exception: pass

    try:
        target_codes = []
        if '.SS' in ticker: target_codes = ['sh' + clean_code]
        elif '.SZ' in ticker: target_codes = ['sz' + clean_code]
        elif '.HK' in ticker: target_codes = ['hk' + clean_code]
        else: 
            target_codes = [
                'us' + str(ticker), 'us' + str(ticker).replace('-', '.'),
                'us' + str(ticker).replace('-', '_'), 'us' + str(ticker).replace('-', '')
            ]
        for tc in target_codes:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,400,qfq"
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if res.status_code == 200:
                json_data = res.json()
                if 'data' in json_data and tc in json_data['data']:
                    stock_data = json_data['data'][tc]
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
    except Exception: pass
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
        if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except: return None

def fetch_crypto_data(item):
    ticker = item['ticker']
    try:
        yf_symbol = str(ticker).upper().replace('/USDT', '-USD').replace('/USDC', '-USD')
        data = yf.Ticker(yf_symbol).history(period="1y", interval="1d")
        if len(data) > 0:
            aligned_df = align_to_last_friday(data)
            if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception: pass

    try:
        base_coin = str(ticker).upper().split('/')[0] 
        url = f"https://api.exchange.coinbase.com/products/{base_coin}-USD/candles?granularity=86400"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if res.status_code == 200:
            candles = res.json()
            if candles:
                df = pd.DataFrame(candles, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
                df['date'] = pd.to_datetime(df['time'], unit='s')
                df.set_index('date', inplace=True)
                df = df.sort_index()
                aligned_df = align_to_last_friday(df)
                if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception: pass
    return None

# ================= 🚀 UI 渲染层 (极致降噪版) =================

def build_signal_section(title, asset_list, key, target_val):
    """强制使用独立无序列表，且代码前置，解决微信压缩问题"""
    matched = [r for r in asset_list if r.get(key) == target_val and r.get('active') != 'n']
    if not matched: 
        return "" # 无信号时静默折叠
    
    md = f"#### {title}\n\n"
    for r in matched:
        md += f"- `{r['ticker']}` **{r['name']}** {r['performance']}\n"
    return md + "\n"

def build_leaderboard_table(title, asset_list):
    """降噪表格：代码前置、去加粗、表现居中"""
    if not asset_list: return ""
    
    md = f"### {title}\n\n"
    md += "| 代码 | 资产名称 | 本周表现 |\n"
    md += "| :--- | :--- | :---: |\n" # 本周表现列居中，视觉更整齐
    for r in asset_list:
        md += f"| `{r['ticker']}` | {r['name']} | {r['performance']} |\n"
    return md + "\n"

def send_and_archive_report(all_reports, failed_list):
    core_pool = [r for r in all_reports if 'core' in str(r.get('pool', '')).lower()]
    watch_pool = [r for r in all_reports if 'watch' in str(r.get('pool', '')).lower()]
    
    core_pool.sort(key=lambda x: x['raw_return'], reverse=True)
    watch_pool.sort(key=lambda x: x['raw_return'], reverse=True)
    
    md = ""
    
    # ━━━━━━━━━ 顶层 1：核心阵地异动 ━━━━━━━━━
    md += "## 🎯 核心持仓异动\n---\n"
    core_signals = ""
    core_signals += build_signal_section("🚀 突破一年新高", core_pool, 'extremum_signal', '新高')
    core_signals += build_signal_section("🩸 跌破一年新低", core_pool, 'extremum_signal', '新低')
    core_signals += build_signal_section("✅ 金叉 (5周上穿20周)", core_pool, 'cross_signal', '金叉')
    core_signals += build_signal_section("⚠️ 死叉 (5周下穿20周)", core_pool, 'cross_signal', '死叉')
    
    if core_signals: md += core_signals
    else: md += "- *本周无异动*\n\n"
    
    # ━━━━━━━━━ 顶层 2：核心资产龙虎榜 ━━━━━━━━━
    md += "## 📊 核心资产龙虎榜\n---\n"
    yf_core = [r for r in core_pool if r['type'] == 'yf']
    jj_core = [r for r in core_pool if r['type'] == 'jj']
    crypto_core = [r for r in core_pool if r['type'] == 'crypto']
    
    md += build_leaderboard_table("🏛️ 股票与场内 ETF", yf_core)
    md += build_leaderboard_table("🏦 场外公募基金", jj_core)
    md += build_leaderboard_table("🪙 加密货币", crypto_core)

    # ━━━━━━━━━ 底部 1：备选宏观异动与龙虎榜 ━━━━━━━━━
    if watch_pool:
        md += "## 🔍 备选宏观雷达\n---\n"
        watch_signals = ""
        watch_signals += build_signal_section("🚀 突破一年新高", watch_pool, 'extremum_signal', '新高')
        watch_signals += build_signal_section("🩸 跌破一年新低", watch_pool, 'extremum_signal', '新低')
        watch_signals += build_signal_section("✅ 金叉 (5周上穿20周)", watch_pool, 'cross_signal', '金叉')
        watch_signals += build_signal_section("⚠️ 死叉 (5周下穿20周)", watch_pool, 'cross_signal', '死叉')

        if watch_signals: md += watch_signals
        else: md += "- *本周无异动*\n\n"

        md += "## 📈 备选资产龙虎榜\n---\n"
        yf_watch = [r for r in watch_pool if r['type'] == 'yf']
        jj_watch = [r for r in watch_pool if r['type'] == 'jj']
        crypto_watch = [r for r in watch_pool if r['type'] == 'crypto']
        
        md += build_leaderboard_table("🏛️ 股票与场内 ETF", yf_watch)
        md += build_leaderboard_table("🏦 场外公募基金", jj_watch)
        md += build_leaderboard_table("🪙 加密货币", crypto_watch)

    # ━━━━━━━━━ 底部 2：故障排查 ━━━━━━━━━
    if failed_list:
        md += "## ⚠️ 核心盲区公示\n---\n"
        md += "> 以下标的未获取到有效对齐数据：\n> \n"
        for fail in failed_list: md += f"> - `{fail['ticker']}` **{fail['name']}**\n"

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
    print(f"[{start_time}] 引擎点火，执行界面视觉降噪重构...")
    
    assets_清单 = load_portfolio()
    all_reports = []
    failed_assets = [] 
    
    print(f"载入有效监控目标总数: {len(assets_清单)}")
    
    for item in assets_清单:
        res = None
        if item['type'] == 'yf': res = fetch_yf_data(item)
        elif item['type'] == 'crypto': res = fetch_crypto_data(item)
        elif item['type'] == 'jj': res = fetch_fund_data(item)
        
        if res: all_reports.append(res)
        else: failed_assets.append(item)
            
    send_and_archive_report(all_reports, failed_assets)
    print(f"扫描完毕，总耗时: {(datetime.datetime.now() - start_time).seconds} 秒")
