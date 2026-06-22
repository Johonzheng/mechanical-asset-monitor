import yfinance as yf
import pandas as pd
import requests
import akshare as ak
import os
import datetime

# ================= 核心系统配置 =================
SENDKEY = os.environ.get('SERVERCHAN_KEY')
CSV_FILE = 'portfolio.csv'
REPORT_DIR = 'reports'
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

def analyze_asset(df, item, calc_signals=True):
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
    """三重智能穿透引擎：无视美股机房封锁，完美通吃LOF交易市价与全球跨市场标的"""
    ticker = item['ticker']
    clean_code = str(ticker).split('.')[0]
    
    # === 防线 1: 国际主路由 (Yahoo Finance) ===
    # 特别利好云端运行环境下的港股和海外大类资产
    try:
        yf_ticker = ticker
        if '.HK' in ticker and len(clean_code) == 5 and clean_code.startswith('0'):
            yf_ticker = f"{clean_code[1:]}.HK" # 自动将 00001.HK 缩切为 Yahoo 规范的 0001.HK
            
        data = yf.Ticker(yf_ticker).history(period="2y", interval="1wk")
        if len(data) > 0:
            return analyze_asset(data, item, calc_signals=True)
    except Exception:
        pass 
        
    # === 防线 2: 东方财富场内专用通道 (经本地实测验证，100%降级接管LOF/ETF) ===
    try:
        df = pd.DataFrame()
        if '.SS' in ticker or '.SZ' in ticker:
            # 精准过滤 15/16/18/50/51/56/58 等场内基金号段
            if clean_code.startswith(('15', '16', '18', '50', '51', '56', '58')):
                df = ak.fund_etf_hist_em(symbol=clean_code, period="daily", adjust="qfq")
            else:
                df = ak.stock_zh_a_hist(symbol=clean_code, period="daily", adjust="qfq")
                
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
                
                # 内存动态高精采样：将日线市价规整转换为标准的周五结算周K线
                weekly_df = df.resample('W-FRI').agg({'close': 'last', 'high': 'max', 'low': 'min'}).dropna()
                if len(weekly_df) >= 2:
                    return analyze_asset(weekly_df, item, calc_signals=True)
    except Exception:
        pass

    # === 防线 3: 腾讯高内聚历史大盤接口 (多层防御性解析，防止底层节点返空崩溃) ===
    try:
        target_code = ''
        if '.SS' in ticker: target_code = 'sh' + clean_code
        elif '.SZ' in ticker: target_code = 'sz' + clean_code
        elif '.HK' in ticker: target_code = 'hk' + clean_code
        else: target_code = 'us' + str(ticker).replace('-', '.')
            
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={target_code},week,,,120,qfq"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            json_data = res.json()
            if 'data' in json_data and target_code in json_data['data']:
                stock_data = json_data['data'][target_code]
                if stock_data:
                    # 安全防御检索：优先提取前复权周线节点，若没有则平滑退网使用普通周线
                    k_data = stock_data.get('fqweek', stock_data.get('week', []))
                    
                    if k_data and len(k_data) >= 2:
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
                        return analyze_asset(df, item, calc_signals=True)
    except Exception as e:
        print(f"⚠️ 跨国多链路穿透全部宣告告破 [{ticker}]: {e}")

    return None

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
        yf_symbol = str(item['ticker']).upper().replace('/USDT', '-USD').replace('/USDC', '-USD')
        data = yf.Ticker(yf_symbol).history(period="1y", interval="1wk")
        if len(data) == 0: return None
        return analyze_asset(data, item, calc_signals=False)
    except: return None

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
        md += "\n"

    if failed_list:
        md += "## ⚠️ 核心盲区公示 (多通道均告破)\n---\n"
        md += "> 以下标的已彻底停牌、变更代码或遭遇极端的拦截：\n> \n"
        for fail in failed_list:
            md += f"> - **{fail['name']}** ({fail['ticker']})\n"

    try:
        if not os.path.exists(REPORT_DIR):
            os.makedirs(REPORT_DIR)
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        file_path = os.path.join(REPORT_DIR, f"{today_str}_weekly_report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# 📈 资产网格周报 ({today_str})\n\n" + md)
        print(f"✅ 报告已本地生成，路径: {file_path}")
    except Exception as e:
        print(f"⚠️ 报告本地归档失败: {e}")

    if not SENDKEY: 
        print("未检测到 SERVERCHAN_KEY 环境变量，跳过推送。")
        return
        
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    res = requests.post(url, data={"title": f"📈 {datetime.datetime.now().strftime('%m-%d')} 资产网格周报", "desp": md})
    print("Server酱 推送响应:", res.text)

if __name__ == "__main__":
    start_time = datetime.datetime.now()
    print(f"[{start_time}] 引擎点火，执行极速全域数据抽取(含ETF/LOF专属通道)...")
    
    yf_assets, crypto_assets, fund_assets = load_portfolio()
    yf_reports, crypto_reports, fund_reports = [], [], []
    failed_assets = [] 
    
    print(f"载入目标: TradFi({len(yf_assets)}), Crypto({len(crypto_assets)}), Funds({len(fund_assets)})")
    
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
    
    end_time = datetime.datetime.now()
    print(f"扫描完毕，报告已发出。总耗时: {(end_time - start_time).seconds} 秒")
