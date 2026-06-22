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
# ==================================================

def load_portfolio():
    """读取标的清单：智能兼容 Windows Excel (GBK) 与 Linux (UTF-8) 编码"""
    if not os.path.exists(CSV_FILE):
        print(f"⚠️ 未找到 {CSV_FILE} 文件。")
        return [], [], []
        
    try:
        # 优先尝试 GitHub 标准的 UTF-8 编码
        df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='utf-8')
    except UnicodeDecodeError:
        # 如果报错，自动退网捕获，兼容国内 Excel 默认导出的 GBK 编码
        df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='gbk')
        
    try:
        # 强制将表头转换为小写并去除空格，防止你在 Excel 里多敲了空格
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        yf_tickers = df[df['type'].str.strip().str.lower() == 'yf']['ticker'].str.strip().tolist()
        crypto_tickers = df[df['type'].str.strip().str.lower() == 'crypto']['ticker'].str.strip().tolist()
        fund_tickers = df[df['type'].str.strip().str.lower() == 'jj']['ticker'].str.strip().tolist()
        
        return yf_tickers, crypto_tickers, fund_tickers
    except Exception as e:
        print(f"⚠️ 读取持仓清单解析失败，请检查 CSV 列名是否严格为 ticker 和 type: {e}")
        return [], [], []

def analyze_asset(df, ticker):
    """通用计算引擎：处理均线交叉、52周极值与单周表现"""
    if len(df) < 20:
        return None 

    df['MA5'] = df['close'].rolling(window=5).mean()
    df['MA20'] = df['close'].rolling(window=20).mean()
    
    prev_week = df.iloc[-3]
    last_week = df.iloc[-2]
    
    weekly_return = ((last_week['close'] - prev_week['close']) / prev_week['close']) * 100
    perf_str = f"上涨 {weekly_return:.2f}% 🔴" if weekly_return > 0 else f"下跌 {weekly_return:.2f}% 🟢"
    
    cross_signal = ""
    if prev_week['MA5'] >= prev_week['MA20'] and last_week['MA5'] < last_week['MA20']:
        cross_signal = "⚠️ **触发死叉** (5周下穿20周)"
    elif prev_week['MA5'] <= prev_week['MA20'] and last_week['MA5'] > last_week['MA20']:
        cross_signal = "✅ **触发金叉** (5周上穿20周)"
        
    extremum_signal = ""
    if len(df) >= 53:
        past_52_weeks = df.iloc[-53:-1] 
        high_52w = past_52_weeks['high'].max()
        low_52w = past_52_weeks['low'].min()
        
        if last_week['close'] >= high_52w:
            extremum_signal = "🚀 **创近一年新高**"
        elif last_week['close'] <= low_52w:
            extremum_signal = "🩸 **创近一年新低**"
            
    return {
        "ticker": ticker,
        "performance": perf_str,
        "cross_signal": cross_signal,
        "extremum_signal": extremum_signal
    }

def fetch_yf_data(ticker):
    """获取传统市场数据 (股票/场内ETF)"""
    try:
        data = yf.download(ticker, period="2y", interval="1wk", progress=False)
        data.rename(columns={'Close':'close', 'High':'high', 'Low':'low', 'Open':'open'}, inplace=True)
        return analyze_asset(data, ticker)
    except Exception as e:
        print(f"[{ticker}] (TradFi) 获取异常: {e}")
        return None

def fetch_crypto_data(symbol):
    """获取加密货币数据"""
    try:
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1w', limit=150)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return analyze_asset(df, symbol)
    except Exception as e:
        print(f"[{symbol}] (Crypto) 获取异常: {e}")
        return None

def fetch_fund_data(ticker):
    """获取国内场外公募基金数据并合成为周线"""
    try:
        # 清理可能误填的 jj 前缀
        clean_ticker = str(ticker).replace('jj', '').strip()
        
        # 通过 AkShare 获取天天基金网的历史单位净值
        df = ak.fund_open_fund_info_em(symbol=clean_ticker, indicator="单位净值走势")
        if df is None or df.empty:
            return None
            
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df.set_index('净值日期', inplace=True)
        df.rename(columns={'单位净值': 'close'}, inplace=True)
        
        # 核心算法：将日频的基金净值重采样为周频 (取每周最大值为High，最小值为Low，周五为Close)
        weekly_df = df['close'].resample('W-FRI').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()
        
        return analyze_asset(weekly_df, f"{clean_ticker} (基金)")
    except Exception as e:
        print(f"[{ticker}] (基金) 获取异常: {e}")
        return None

def send_serverchan_report(reports):
    """发送聚合报告"""
    if not SENDKEY:
        print("未检测到 SERVERCHAN_KEY，跳过推送。")
        return

    if not reports:
        md_content = "本周未成功获取任何标的的数据。"
    else:
        focus_pool = [r for r in reports if r['cross_signal'] or r['extremum_signal']]
        
        md_content = "### 🎯 重点异动关注池\n\n"
        if focus_pool:
            for r in focus_pool:
                md_content += f"**{r['ticker']}** (本周{r['performance']})\n"
                if r['cross_signal']: md_content += f"- 趋势: {r['cross_signal']}\n"
                if r['extremum_signal']: md_content += f"- 极值: {r['extremum_signal']}\n"
                md_content += "\n"
        else:
            md_content += "*本周无标的触发均线交叉或极值突破。*\n\n"

        md_content += "---\n\n### 📊 全矩阵资产周度表现\n\n"
        for r in reports:
            md_content += f"- **{r['ticker']}**: {r['performance']}\n"

        md_content += "\n\n---\n*💡 机械指令：请严格遵循既定的战略资产网格，执行纪律性定投与再平衡。*"

    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    data = {
        "title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 全资产周期监控",
        "desp": md_content
    }
    
    requests.post(url, data=data)

if __name__ == "__main__":
    print(f"[{datetime.datetime.now()}] 引擎点火，执行全域资产提取...")
    
    yf_tickers, crypto_tickers, fund_tickers = load_portfolio()
    all_reports = []
    
    for tk in yf_tickers:
        res = fetch_yf_data(tk)
        if res: all_reports.append(res)
            
    for sym in crypto_tickers:
        res = fetch_crypto_data(sym)
        if res: all_reports.append(res)
        
    for fund in fund_tickers:
        res = fetch_fund_data(fund)
        if res: all_reports.append(res)
            
    send_serverchan_report(all_reports)
    print("扫描结束，报告已发出。")
