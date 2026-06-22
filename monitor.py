import yfinance as yf
import pandas as pd
import requests
import ccxt
import os
import datetime

# ================= 系统与环境配置 =================
# 从 GitHub Secrets 获取 Server酱 通信密钥
SENDKEY = os.environ.get('SERVERCHAN_KEY')
CSV_FILE = 'portfolio.csv'
# ==================================================

def load_portfolio():
    """从同目录下的 portfolio.csv 动态读取标的清单"""
    if not os.path.exists(CSV_FILE):
        print(f"⚠️ 致命错误: 未找到 {CSV_FILE} 文件，请在同级目录创建该文件。")
        return [], []
        
    try:
        # 读取 CSV 并清理列名的空格
        df = pd.read_csv(CSV_FILE, skipinitialspace=True)
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        # 提取并清理标的代码
        yf_tickers = df[df['type'].str.strip().str.lower() == 'yf']['ticker'].str.strip().tolist()
        crypto_tickers = df[df['type'].str.strip().str.lower() == 'crypto']['ticker'].str.strip().tolist()
        
        return yf_tickers, crypto_tickers
    except Exception as e:
        print(f"⚠️ 读取持仓清单解析失败: {e}")
        return [], []

def analyze_asset(df, ticker, is_crypto=False):
    """核心计算引擎：处理均线交叉、52周极值与单周表现"""
    # 至少需要 20 周数据才能计算 20 周均线
    if len(df) < 20:
        print(f"[{ticker}] 数据量不足 20 周，跳过分析。")
        return None 

    # 1. 计算 5周与 20周均线
    df['MA5'] = df['close'].rolling(window=5).mean()
    df['MA20'] = df['close'].rolling(window=20).mean()
    
    # 提取最近两周数据进行确认 (规避本周尚未完全收盘的 K 线漂移干扰)
    prev_week = df.iloc[-3]
    last_week = df.iloc[-2]
    
    # 2. 计算近一周涨跌幅
    weekly_return = ((last_week['close'] - prev_week['close']) / prev_week['close']) * 100
    perf_str = f"上涨 {weekly_return:.2f}% 🔴" if weekly_return > 0 else f"下跌 {weekly_return:.2f}% 🟢"
    
    # 3. 判定均线交叉系统
    cross_signal = ""
    # 死叉：上上周 MA5>=MA20，且上周 MA5<MA20
    if prev_week['MA5'] >= prev_week['MA20'] and last_week['MA5'] < last_week['MA20']:
        cross_signal = "⚠️ **触发死叉** (5周下穿20周)"
    # 金叉：上上周 MA5<=MA20，且上周 MA5>MA20
    elif prev_week['MA5'] <= prev_week['MA20'] and last_week['MA5'] > last_week['MA20']:
        cross_signal = "✅ **触发金叉** (5周上穿20周)"
        
    # 4. 判定极值系统 (需要至少 53 周数据来计算过去的 52 周)
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
    """获取并清洗传统市场数据 (支持美股/A股/港股/ETF/期货平替)"""
    try:
        data = yf.download(ticker, period="2y", interval="1wk", progress=False)
        data.rename(columns={'Close':'close', 'High':'high', 'Low':'low', 'Open':'open'}, inplace=True)
        return analyze_asset(data, ticker, is_crypto=False)
    except Exception as e:
        print(f"[{ticker}] (TradFi) 数据获取异常: {e}")
        return None

def fetch_crypto_data(symbol):
    """获取并清洗加密货币数据"""
    try:
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1w', limit=150)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return analyze_asset(df, symbol, is_crypto=True)
    except Exception as e:
        print(f"[{symbol}] (Crypto) 数据获取异常: {e}")
        return None

def send_serverchan_report(reports):
    """聚合报告并通过 Server酱 发送微信通知"""
    if not SENDKEY:
        print("系统提示: 未检测到 SERVERCHAN_KEY 环境变量，已跳过推送环节。")
        return

    if not reports:
        md_content = "本周未成功获取任何标的的数据，请检查清单或接口状态。"
    else:
        # 分离出需要重点关注的异动标的
        focus_pool = [r for r in reports if r['cross_signal'] or r['extremum_signal']]
        
        md_content = "### 🎯 重点异动关注池\n\n"
        if focus_pool:
            for r in focus_pool:
                md_content += f"**{r['ticker']}** (本周{r['performance']})\n"
                if r['cross_signal']: md_content += f"- 趋势: {r['cross_signal']}\n"
                if r['extremum_signal']: md_content += f"- 极值: {r['extremum_signal']}\n"
                md_content += "\n"
        else:
            md_content += "*本周监控池内无标的触发均线交叉或极值突破。*\n\n"

        md_content += "---\n\n### 📊 全矩阵资产周度表现\n\n"
        for r in reports:
            md_content += f"- **{r['ticker']}**: {r['performance']}\n"

        md_content += "\n\n---\n*💡 机械指令：请严格遵循既定的战略资产网格，执行纪律性定投与再平衡。*"

    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    data = {
        "title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 多资产周期监控简报",
        "desp": md_content
    }
    
    try:
        res = requests.post(url, data=data)
        print("Server酱 通道推送完毕，状态响应:", res.text)
    except Exception as e:
        print(f"推送请求发送失败: {e}")

if __name__ == "__main__":
    print(f"[{datetime.datetime.now()}] 引擎点火，执行全域资产数据抽取...")
    
    # 动态载入配置单
    yf_tickers, crypto_tickers = load_portfolio()
    print(f"读取队列: 传统金融标的 {len(yf_tickers)} 个 | 加密资产 {len(crypto_tickers)} 个")
    
    all_reports = []
    
    # 批处理传统资产
    for tk in yf_tickers:
        res = fetch_yf_data(tk)
        if res: all_reports.append(res)
            
    # 批处理加密资产
    for sym in crypto_tickers:
        res = fetch_crypto_data(sym)
        if res: all_reports.append(res)
            
    # 执行汇总与通知分发
    send_serverchan_report(all_reports)
    print("全域扫描周期结束，任务下线。")
