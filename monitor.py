import yfinance as yf
import pandas as pd
import requests
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
        df.columns = [str(c).strip().lower() for c in df.columns]
        
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
    
    cross_signal, extremum_signal, ma_trend = "", "", ""
    if calc_signals and len(weekly_df) >= 20:
        weekly_df['ma5'] = weekly_df['close'].rolling(window=5).mean()
        weekly_df['ma20'] = weekly_df['close'].rolling(window=20).mean()
        
        pw_ma5, pw_ma20 = weekly_df.iloc[-2]['ma5'], weekly_df.iloc[-2]['ma20']
        lw_ma5, lw_ma20 = weekly_df.iloc[-1]['ma5'], weekly_df.iloc[-1]['ma20']
        
        if pw_ma5 >= pw_ma20 and lw_ma5 < lw_ma20: cross_signal = "死叉"
        elif pw_ma5 <= pw_ma20 and lw_ma5 > lw_ma20: cross_signal = "金叉"
        
        if lw_ma5 >= lw_ma20: ma_trend = "多头"
        else: ma_trend = "空头"
            
        if len(weekly_df) >= 52:
            past_52_weeks = weekly_df.iloc[-53:-1]
            if last_week['close'] >= past_52_weeks['high'].max(): extremum_signal = "新高"
            elif last_week['close'] <= past_52_weeks['low'].min(): extremum_signal = "新低"
                
    return {
        "ticker": item['ticker'], "name": item['name'], "type": item['type'], 
        "pool": item['pool'], "active": item['active'],
        "raw_return": weekly_return, "performance": perf_str, 
        "cross_signal": cross_signal, "extremum_signal": extremum_signal,
        "ma_trend": ma_trend 
    }

def fetch_yf_data(item):
    ticker = item['ticker']
    clean_code = str(ticker).split('.')[0]
    
    # =========================================================================
    # 🇨🇳 路由 1：A股与国内场内 ETF (带 .SS / .SZ)
    # 核心纪律：绝对禁止走 Yahoo Finance，防止除权除息数据缺失导致暴涨暴跌假象！
    # =========================================================================
    if '.SS' in ticker or '.SZ' in ticker:
        # 首选：东财底层接口 (fqt=1 前复权)
        sec_id = f"1.{clean_code}" if ticker.endswith('.SS') or clean_code.startswith(('5', '6')) else f"0.{clean_code}"
        em_url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec_id}&klt=101&fqt=1&end=20500101&lmt=400&fields1=f1,f2,f3&fields2=f51,f53,f54,f55"
        try:
            res = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if res.status_code == 200:
                json_data = res.json()
                if json_data.get('data') and json_data['data'].get('klines'):
                    klines = json_data['data']['klines']
                    if len(klines) >= 5:
                        cleaned_rows = []
                        for k in klines:
                            parts = k.split(',')
                            cleaned_rows.append({
                                'date': parts[0], 'close': float(parts[1]), 
                                'high': float(parts[2]), 'low': float(parts[3])
                            })
                        df = pd.DataFrame(cleaned_rows)
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)
                        aligned_df = align_to_last_friday(df)
                        if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
        except Exception:
            pass

        # 备选：腾讯兜底 (qfqday 前复权)
        prefix = 'sh' if '.SS' in ticker else 'sz'
        tc = f"{prefix}{clean_code}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,400,qfq"
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if res.status_code == 200:
                json_data = res.json()
                if 'data' in json_data and tc in json_data['data']:
                    stock_data = json_data['data'][tc]
                    # 必须获取 qfqday 才是前复权！
                    k_data = stock_data.get('qfqday', stock_data.get('day', []))
                    if k_data and len(k_data) >= 5:
                        cleaned_rows = []
                        for row in k_data:
                            if len(row) >= 6:
                                cleaned_rows.append({
                                    'date': row[0], 'close': float(row[2]),
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

    # =========================================================================
    # 🇺🇸 🇭🇰 路由 2：海外资产 (美股/港股)
    # =========================================================================
    else:
        try:
            yf_ticker = ticker
            if '.HK' in ticker and len(clean_code) == 5 and clean_code.startswith('0'):
                yf_ticker = f"{clean_code[1:]}.HK"
            data = yf.Ticker(yf_ticker).history(period="2y", interval="1d", auto_adjust=True)
            if len(data) > 0:
                aligned_df = align_to_last_friday(data)
                if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
        except Exception:
            pass
            
        try:
            target_codes = []
            if '.HK' in ticker: target_codes = ['hk' + clean_code]
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
                        k_data = stock_data.get('qfqday', stock_data.get('day', []))
                        if k_data and len(k_data) >= 5:
                            cleaned_rows = []
                            for row in k_data:
                                if len(row) >= 6:
                                    cleaned_rows.append({
                                        'date': row[0], 'close': float(row[2]),
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
    clean_ticker = str(item['ticker']).replace('jj', '').strip()
    em_url = f"http://fund.eastmoney.com/pingzhongdata/{clean_ticker}.js"
    try:
        res = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            content = res.text
            start_idx = content.find('Data_netWorthTrend = ') + len('Data_netWorthTrend = ')
            end_idx = content.find(';', start_idx)
            import json
            data = json.loads(content[start_idx:end_idx])
            
            cleaned_rows = []
            for entry in data:
                import time
                date_str = time.strftime('%Y-%m-%d', time.localtime(entry['x']/1000))
                cleaned_rows.append({'date': date_str, 'close': float(entry['y']), 'high': float(entry['y']), 'low': float(entry['y'])})
            
            df = pd.DataFrame(cleaned_rows)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            aligned_df = align_to_last_friday(df)
            if aligned_df is not None: return analyze_asset(aligned_df, item, calc_signals=True)
    except Exception:
        pass
    return None

def fetch_crypto_data(item):
    ticker = item['ticker']
    try:
        yf_symbol = str(ticker).upper().replace('/USDT', '-USD').replace('/USDC', '-USD')
        data = yf.Ticker(yf_symbol).history(period="1y", interval="1d", auto_adjust=True)
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

# ================= 🚀 UI 渲染层 (极简纯净版) =================

def build_signal_section(title, asset_list, key, target_val):
    md = f"#### {title}\n\n"
    matched = [r for r in asset_list if r.get(key) == target_val and r.get('active') != 'n']
    if matched:
        for r in matched:
            md += f"- **{r['name']}** {r['performance']}\n"
    else:
        md += "> *无*\n"
    return md + "\n"

def build_leaderboard_list(title, asset_list):
    if not asset_list: return ""
    md = f"### {title}\n\n"
    for r in asset_list:
        md += f"- **{r['name']}** {r['performance']}\n"
    return md + "\n"

def send_and_archive_report(all_reports, failed_list):
    core_pool = [r for r in all_reports if 'core' in str(r.get('pool', '')).lower()]
    watch_pool = [r for r in all_reports if 'watch' in str(r.get('pool', '')).lower()]
    
    core_pool.sort(key=lambda x: x['raw_return'], reverse=True)
    watch_pool.sort(key=lambda x: x['raw_return'], reverse=True)
    
    md = ""
    
    # ━━━━━━━━━ 顶层：核心持仓异动 ━━━━━━━━━
    md += "## 🎯 核心持仓异动\n---\n"
    md += build_signal_section("🚀 突破一年新高", core_pool, 'extremum_signal', '新高')
    md += build_signal_section("🩸 跌破一年新低", core_pool, 'extremum_signal', '新低')
    md += build_signal_section("✅ 金叉 (5周上穿20周)", core_pool, 'cross_signal', '金叉')
    md += build_signal_section("⚠️ 死叉 (5周下穿20周)", core_pool, 'cross_signal', '死叉')
    
    # ━━━━━━━━━ 中层：核心龙虎榜与趋势阵营 ━━━━━━━━━
    md += "## 📊 核心资产龙虎榜\n---\n"
    yf_core = [r for r in core_pool if r['type'] == 'yf']
    jj_core = [r for r in core_pool if r['type'] == 'jj']
    crypto_core = [r for r in core_pool if r['type'] == 'crypto']
    
    md += build_leaderboard_list("🏛️ 股票与场内 ETF", yf_core)
    md += build_leaderboard_list("🏦 场外公募基金", jj_core)
    md += build_leaderboard_list("🪙 加密货币", crypto_core)

    md += "## 🌊 核心趋势阵营\n---\n"
    md += build_signal_section("📈 5周线在20周线上 (多头排列)", core_pool, 'ma_trend', '多头')
    md += build_signal_section("📉 5周线在20周线下 (空头排列)", core_pool, 'ma_trend', '空头')

    # ━━━━━━━━━ 底部：备选宏观异动、龙虎榜与趋势阵营 ━━━━━━━━━
    if watch_pool:
        md += "## 🔍 备选自选宏观雷达\n---\n"
        md += build_signal_section("🚀 突破一年新高", watch_pool, 'extremum_signal', '新高')
        md += build_signal_section("🩸 跌破一年新低", watch_pool, 'extremum_signal', '新低')
        md += build_signal_section("✅ 金叉 (5周上穿20周)", watch_pool, 'cross_signal', '金叉')
        md += build_signal_section("⚠️ 死叉 (5周下穿20周)", watch_pool, 'cross_signal', '死叉')

        md += "## 📈 备选资产龙虎榜\n---\n"
        yf_watch = [r for r in watch_pool if r['type'] == 'yf']
        jj_watch = [r for r in watch_pool if r['type'] == 'jj']
        crypto_watch = [r for r in watch_pool if r['type'] == 'crypto']
        
        md += build_leaderboard_list("🏛️ 股票与场内 ETF", yf_watch)
        md += build_leaderboard_list("🏦 场外公募基金", jj_watch)
        md += build_leaderboard_list("🪙 加密货币", crypto_watch)

        md += "## 🌊 备选趋势阵营\n---\n"
        md += build_signal_section("📈 5周线在20周线上 (多头排列)", watch_pool, 'ma_trend', '多头')
        md += build_signal_section("📉 5周线在20周线下 (空头排列)", watch_pool, 'ma_trend', '空头')

    # ━━━━━━━━━ 最终：故障排查 ━━━━━━━━━
    md += "## ⚠️ 核心盲区公示\n---\n"
    if failed_list:
        md += "> 以下标的未获取到有效对齐数据：\n> \n"
        for fail in failed_list:
            md += f"> - **{fail['name']}**\n"
    else:
        md += "> *无*\n"
    md += "\n"

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
    print(f"[{start_time}] 引擎点火，执行严格前复权隔离路由机制...")
    
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
