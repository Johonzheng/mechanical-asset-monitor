import yfinance as yf
import pandas as pd
import requests
import ccxt
import os
import datetime

# ================= 核心策略配置区 =================
# A股/港股/美股/各类ETF标的池
YF_TICKERS = ['SPY', 'QQQ', 'GLD', '0700.HK', '600519.SS', '510300.SS']
# 加密货币标的池
CRYPTO_TICKERS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

# 从 GitHub Secrets 获取通信密钥
SENDKEY = os.environ.get('SERVERCHAN_KEY')
# ==================================================

def analyze_asset(df, ticker, is_crypto=False):
    """计算标的的周均线交叉、52周极值与周表现"""
    if len(df) < 52:
        return None 

    df['MA5'] = df['close'].rolling(window=5).mean()
    df['MA20'] = df['close'].rolling(window=20).mean()
    
    prev_week = df.iloc[-3]
    last_week = df.iloc[-2]
    
    # 计算涨跌幅
    weekly_return = ((last_week['close'] - prev_week['close']) / prev_week['close']) * 100
    perf_str = f"上涨 {weekly_return:.2f}%" if weekly_return > 0 else f"下跌 {weekly_return:.2f}%"
    
    # 均线系统
    cross_signal = ""
    if prev_week['MA5'] >= prev_week['MA20'] and last_week['MA5'] < last_week['MA20']:
        cross_signal = "⚠️ **死叉风控** (5周下穿20周)"
    elif prev_week['MA5'] <= prev_week['MA20'] and last_week['MA5'] > last_week['MA20']:
        cross_signal = "✅ **金叉确立** (5周上穿20周)"
        
    # 极值系统 (前52周)
    past_52_weeks = df.iloc[-53:-1] 
    high_52w = past_52_weeks['high'].max()
    low_52w = past_52_weeks['low'].min()
    
    extremum_signal = ""
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
    try:
        data = yf.download(ticker, period="2y", interval="1wk", progress=False)
        data.rename(columns={'Close':'close', 'High':'high', 'Low':'low'}, inplace=True)
        return analyze_asset(data, ticker)
    except Exception as e:
        print(f"[{ticker}] 数据提取失败: {e}")
        return None

def fetch_crypto_data(symbol):
    try:
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1w', limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return analyze_asset(df, symbol, is_crypto=True)
    except Exception as e:
        print(f"[{symbol}] 数据提取失败: {e}")
        return None

def send_serverchan(reports):
    """构建 Server酱 Markdown 报表并发送"""
    if not SENDKEY:
        print("未检测到 SERVERCHAN_KEY，终止推送。")
        return

    focus_pool = [r for r in reports if r['cross_signal'] or r['extremum_signal']]
    
    # 构建正文内容
    md_content = "### 🎯 重点信号监控\n\n"
    if focus_pool:
        for r in focus_pool:
            md_content += f"**{r['ticker']}** (本周{r['performance']})\n\n"
            if r['cross_signal']: md_content += f"- 均线动作: {r['cross_signal']}\n"
            if r['extremum_signal']: md_content += f"- 极值动作: {r['extremum_signal']}\n"
            md_content += "\n"
    else:
        md_content += "*本周无标的触发均线交叉或极值突破。*\n\n"

    md_content += "---\n\n### 📊 全维度表现速览\n\n"
    for r in reports:
        md_content += f"- **{r['ticker']}**: {r['performance']}\n"

    md_content += "\n\n---\n*💡 机械提示：请严格基于战略预设比例执行再平衡动作。*"

    # 发送请求
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    data = {
        "title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 资产周报已送达",
        "desp": md_content
    }
    res = requests.post(url, data=data)
    print("Server酱 推送结果:", res.text)

if __name__ == "__main__":
    print(f"[{datetime.datetime.now()}] 初始化跨市场数据提取...")
    reports = []
    
    for tk in YF_TICKERS:
        res = fetch_yf_data(tk)
        if res: reports.append(res)
            
    for sym in CRYPTO_TICKERS:
        res = fetch_crypto_data(sym)
        if res: reports.append(res)
            
    send_serverchan(reports)
    print("检视周期完成，报告已出库。")
