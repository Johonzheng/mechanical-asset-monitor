import yfinance as yf
import pandas as pd
import requests
import ccxt
import akshare as ak
import os
import datetime

# ================= 系统与环境配置 =================
SENDKEY = os.environ.get('SERVERCHAN_KEY')
CSV_FILE = 'portfolio.csv'

# 【名称引擎】初始化全局场外公募基金名称字典
FUND_NAME_DICT = {}
try:
    print("正在拉取全网基金名称字典...")
    fund_em_df = ak.fund_name_em()
    FUND_NAME_DICT = dict(zip(fund_em_df['基金代码'].astype(str), fund_em_df['基金简称']))
except Exception as e:
    print(f"全局基金名称获取失败: {e}")
# ==================================================

def load_portfolio():
    """读取清单，支持用户在 CSV 中自定义 name 列。修复数字前导零丢失问题"""
    if not os.path.exists(CSV_FILE):
        return [], [], []
        
    try:
        # 强制将所有列按字符串读取，防止 005287 变成 5287
        try:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='utf-8', dtype=str)
        except UnicodeDecodeError:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='gbk', dtype=str)
            
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        # 自动初始化或填充 name 列
        if 'name' not in df.columns:
            df['name'] = df['ticker']
        else:
            df['name'] = df['name'].fillna(df['ticker'])
            
        yf_assets = df[df['type'].str.strip().str.lower() == 'yf'][['ticker', 'name']].to_dict('records')
        crypto_assets = df[df['type'].str.strip().str.lower() == 'crypto'][['ticker', 'name']].to_dict('records')
        fund_assets = df[df['type'].str.strip().str.lower() == 'jj'][['ticker', 'name']].to_dict('records')
        
        return yf_assets, crypto_assets, fund_assets
    except Exception as e:
        print(f"读取持仓清单解析失败: {e}")
        return [], [], []

def analyze_asset(df, item, calc_signals=True):
    """通用计算引擎：通过 calc_signals 参数控制是否计算均线与极值"""
    if len(df) < 2: 
        return None # 至少需要两周数据来计算涨跌幅

    # 统一列名小写，兼容不同数据源
    df.columns = [str(c).lower() for c in df.columns]
    
    # 获取最近两周数据
    prev_week = df.iloc[-3] if len(df) >= 3 else df.iloc[-2]
    last_week = df.iloc[-2] if len(df) >= 3 else df.iloc[-1]
    
    # === 1. 计算周表现 (所有资产均参与) ===
    weekly_return = ((last_week['close'] - prev_week['close']) / prev_week['close']) * 100
    perf_str = f"上涨 {weekly_return:.2f}% 🔴" if weekly_return > 0 else f"下跌 {weekly_return:.2f}% 🟢"
    
    cross_signal = ""
    extremum_signal = ""
    
    # === 2. 计算均线与极值 (仅受准许的资产参与，且需要足够的数据量) ===
    if calc_signals and len(df) >= 20:
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        pw_ma5, pw_ma20 = df.iloc[-3]['ma5'], df.iloc[-3]['ma20']
        lw_ma5, lw_ma20 = df.iloc[-2]['ma5'], df.iloc[-2]['ma20']
        
        if pw_ma5 >= pw_ma20 and lw_ma5 < lw_ma20:
            cross_signal = "⚠️ **死叉** (5周下穿20周)"
        elif pw_ma5 <= pw_ma20 and lw_ma5 > lw_ma20:
            cross_signal = "✅ **金叉** (5周上穿20周)"
            
        if len(df) >= 53:
            past_52_weeks = df.iloc[-53:-1]
            high_52w = past_52_weeks['high'].max()
            low_52w = past_52_weeks['low'].min()
            
            if last_week['close'] >= high_52w:
                extremum_signal = "🚀 **创近一年新高**"
            elif last_week['close'] <= low_52w:
                extremum_signal = "🩸 **创近一年新低**"
                
    return {
        "ticker": item['ticker'],
        "name": item['name'],
        "performance": perf_str,
        "cross_signal": cross_signal,
        "extremum_signal": extremum_signal
    }

def fetch_yf_data(item):
    """获取传统市场数据 (股票/ETF) - 参与均线计算"""
    ticker = item['ticker']
    try:
        # 改用更稳定的 history 接口彻底解决多层索引导致的数据丢失问题
        data = yf.Ticker(ticker).history(period="2y", interval="1wk")
        if len(data) == 0:
            return None
        return analyze_asset(data, item, calc_signals=True)
    except Exception as e:
        print(f"[{ticker}] 获取异常: {e}")
        return None

def fetch_crypto_data(item):
    """获取加密货币数据 - 不参与均线计算"""
    symbol = item['ticker']
    try:
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1w', limit=50)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return analyze_asset(df, item, calc_signals=False)
    except Exception as e:
        print(f"[{symbol}] 获取异常: {e}")
        return None

def fetch_fund_data(item):
    """获取场外基金数据 - 不参与均线计算，自动匹配中文名"""
    ticker = item['ticker']
    try:
        clean_ticker = str(ticker).replace('jj', '').strip()
        
        # 优先使用字典匹配基金中文名，若没匹配到则保留原代码
        if item['name'] == ticker and clean_ticker in FUND_NAME_DICT:
            item['name'] = FUND_NAME_DICT[clean_ticker]
            
        df = ak.fund_open_fund_info_em(symbol=clean_ticker, indicator="单位净值走势")
        if df is None or df.empty:
            return None
            
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df.set_index('净值日期', inplace=True)
        df.rename(columns={'单位净值': 'close'}, inplace=True)
        
        # 转换周线
        weekly_df = df['close'].resample('W-FRI').agg({'close': 'last'}).dropna()
        return analyze_asset(weekly_df, item, calc_signals=False)
    except Exception as e:
        print(f"[{ticker}] 获取异常: {e}")
        return None

def send_serverchan_report(yf_reps, crypto_reps, fund_reps):
    """分类聚合报告并推送"""
    if not SENDKEY:
        print("未检测到 SERVERCHAN_KEY。")
        return

    # 只从传统资产中提取重点异动（因为基金/Crypto的信号被关闭了）
    focus_pool = [r for r in yf_reps if r['cross_signal'] or r['extremum_signal']]
    
    md_content = "### 🎯 重点异动关注池 (股票/ETF)\n\n"
    if focus_pool:
        for r in focus_pool:
            display_title = f"{r['name']}" if r['name'] == r['ticker'] else f"{r['name']} ({r['ticker']})"
            md_content += f"**{display_title}** | {r['performance']}\n"
            if r['cross_signal']: md_content += f"- {r['cross_signal']}\n"
            if r['extremum_signal']: md_content += f"- {r['extremum_signal']}\n"
            md_content += "\n"
    else:
        md_content += "*本周常规池内无标的触发均线交叉或极值突破。*\n\n"

    md_content += "---\n### 📊 常规资产表现速览\n\n"
    
    if yf_reps:
        md_content += "**1. 股票与场内 ETF**\n"
        for r in yf_reps:
            display_title = f"{r['name']}" if r['name'] == r['ticker'] else f"{r['name']} ({r['ticker']})"
            md_content += f"- {display_title}: {r['performance']}\n"
        md_content += "\n"
            
    if fund_reps:
        md_content += "**2. 场外公募基金**\n"
        for r in fund_reps:
            display_title = f"{r['name']}" if r['name'] == r['ticker'] else f"{r['name']} ({r['ticker']})"
            md_content += f"- {display_title}: {r['performance']}\n"
        md_content += "\n"
            
    if crypto_reps:
        md_content += "**3. 加密货币**\n"
        for r in crypto_reps:
            md_content += f"- **{r['name']}**: {r['performance']}\n"

    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    data = {"title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 核心资产监控哨兵", "desp": md_content}
    requests.post(url, data=data)

if __name__ == "__main__":
    print("引擎点火，执行隔离计算与提取...")
    yf_assets, crypto_assets, fund_assets = load_portfolio()
    
    yf_reports, crypto_reports, fund_reports = [], [], []
    
    for item in yf_assets:
        res = fetch_yf_data(item)
        if res: yf_reports.append(res)
            
    for item in crypto_assets:
        res = fetch_crypto_data(item)
        if res: crypto_reports.append(res)
        
    for item in fund_assets:
        res = fetch_fund_data(item)
        if res: fund_reports.append(res)
            
    send_serverchan_report(yf_reports, crypto_reports, fund_reports)
    print("扫描结束，模块解耦报告已发出。")
