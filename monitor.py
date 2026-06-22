import yfinance as yf
import pandas as pd
import requests
import ccxt
import akshare as ak
import os
import datetime

# ================= 核心系统配置 =================
SENDKEY = os.environ.get('SERVERCHAN_KEY')
CSV_FILE = 'portfolio.csv'
REPORT_DIR = 'reports'  # 自动归档的文件夹名称
# ================================================

def load_portfolio():
    """读取本地持仓清单"""
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

def analyze_asset(df, item, calc_signals=True):
    """核心计算引擎"""
    if len(df) < 2: return None 
    df.columns = [str(c).lower() for c in df.columns]
    
    prev_week = df.iloc[-3] if len(df) >= 3 else df.iloc[-2]
    last_week = df.iloc[-2] if len(df) >= 3 else df.iloc[-1]
    
    weekly_return = ((last_week['close'] - prev_week['close']) / prev_week['close']) * 100
    if weekly_return > 0:
        perf_str = f"+{weekly_return:.2f}% 🔴"
    elif weekly_return < 0:
        perf_str = f"{weekly_return:.2f}% 🟢"
    else:
        perf_str = f"0.00% ⚪"
    
    cross_signal, extremum_signal = "", ""
    
    if calc_signals and len(df) >= 20:
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        pw_ma5, pw_ma20 = df.iloc[-3]['ma5'], df.iloc[-3]['ma20']
        lw_ma5, lw_ma20 = df.iloc[-2]['ma5'], df.iloc[-2]['ma20']
        
        if pw_ma5 >= pw_ma20 and lw_ma5 < lw_ma20:
            cross_signal = "⚠️ **死叉离场** (5周下穿20周)"
        elif pw_ma5 <= pw_ma20 and lw_ma5 > lw_ma20:
            cross_signal = "✅ **金叉确立** (5周上穿20周)"
            
        if len(df) >= 53:
            past_52_weeks = df.iloc[-53:-1]
            if last_week['close'] >= past_52_weeks['high'].max():
                extremum_signal = "🚀 **突破一年新高**"
            elif last_week['close'] <= past_52_weeks['low'].min():
                extremum_signal = "🩸 **跌破一年新低**"
                
    return {
        "ticker": item['ticker'],
        "name": item['name'],
        "raw_return": weekly_return,
        "performance": perf_str,
        "cross_signal": cross_signal,
        "extremum_signal": extremum_signal
    }

def fetch_yf_data(item):
    try:
        data = yf.Ticker(item['ticker']).history(period="2y", interval="1wk")
        if len(data) == 0: return None
        return analyze_asset(data, item, calc_signals=True)
    except: return None

def fetch_fund_data(item):
    try:
        clean_ticker = str(item['ticker']).replace('jj', '').strip()
        df = ak.fund_open_fund_info_em(symbol=clean_ticker, indicator="单位净值走势")
        if df is None or df.empty: return None
        
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df.set_index('净值日期', inplace=True)
        df.rename(columns={'单位净值': 'close'}, inplace=True)
        
        weekly_df = df['close'].resample('W-FRI').agg({'close': 'last'}).dropna()
        return analyze_asset(weekly_df, item, calc_signals=False)
    except: return None

def fetch_crypto_data(item):
    try:
        ohlcv = ccxt.binance().fetch_ohlcv(item['ticker'], timeframe='1w', limit=50)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return analyze_asset(df, item, calc_signals=False)
    except: return None

def send_and_archive_report(yf_reps, crypto_reps, fund_reps):
    """排序、聚合、生成Markdown、本地归档并发送推送"""
    yf_reps.sort(key=lambda x: x['raw_return'], reverse=True)
    crypto_reps.sort(key=lambda x: x['raw_return'], reverse=True)
    fund_reps.sort(key=lambda x: x['raw_return'], reverse=True)

    focus_pool = [r for r in yf_reps if r['cross_signal'] or r['extremum_signal']]
    
    # 构造 Markdown
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
        for r in yf_reps:
            md += f"- **{r['name']}** `{r['performance']}`\n"
        md += "\n"
            
    if fund_reps:
        md += "### 🏦 场外公募基金\n"
        for r in fund_reps:
            md += f"- **{r['name']}** `{r['performance']}`\n"
        md += "\n"
            
    if crypto_reps:
        md += "### 🪙 加密货币\n"
        for r in crypto_reps:
            md += f"- **{r['name']}** `{r['performance']}`\n"

    # === [新增] 本地生成 MD 文件并归档 ===
    try:
        if not os.path.exists(REPORT_DIR):
            os.makedirs(REPORT_DIR)
        
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        file_path = os.path.join(REPORT_DIR, f"{today_str}_weekly_report.md")
        
        with open(file_path, "w", encoding="utf-8") as f:
            # 加上大标题以便在 GitHub 网页端阅读
            f.write(f"# 📈 资产网格周报 ({today_str})\n\n" + md)
            
        print(f"✅ 报告已本地生成，路径: {file_path}")
    except Exception as e:
        print(f"⚠️ 报告本地归档失败: {e}")

    # === [执行] 推送 Server 酱 ===
    if not SENDKEY: 
        print("未检测到 SERVERCHAN_KEY 环境变量，跳过推送。")
        return
        
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    res = requests.post(url, data={"title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 资产网格周报", "desp": md})
    print("Server酱 推送响应:", res.text)

if __name__ == "__main__":
    start_time = datetime.datetime.now()
    print(f"[{start_time}] 引擎点火，执行极速全域数据抽取...")
    
    yf_assets, crypto_assets, fund_assets = load_portfolio()
    yf_reports, crypto_reports, fund_reports = [], [], []
    
    print(f"载入目标: TradFi({len(yf_assets)}), Crypto({len(crypto_assets)}), Funds({len(fund_assets)})")
    
    for item in yf_assets:
        res = fetch_yf_data(item)
        if res: yf_reports.append(res)
        
    for item in crypto_assets:
        res = fetch_crypto_data(item)
        if res: crypto_reports.append(res)
        
    for item in fund_assets:
        res = fetch_fund_data(item)
        if res: fund_reports.append(res)
            
    send_and_archive_report(yf_reports, crypto_reports, fund_reports)
    
    end_time = datetime.datetime.now()
    print(f"扫描完毕，报告已发出。总耗时: {(end_time - start_time).seconds} 秒")
