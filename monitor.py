import yfinance as yf
import pandas as pd
import requests
import os
import datetime

# ================= 核心系统配置 =================
# ⚠️ 注意：需在 GitHub Actions Secrets 中配置这两个新变量
WXPUSHER_APP_TOKEN = os.environ.get('WXPUSHER_APP_TOKEN')
WXPUSHER_UID = os.environ.get('WXPUSHER_UID')

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
        
        if 'name' not in df.columns:
            df['name'] = df['ticker']
        else:
            df['name'] = df['name'].fillna(df['ticker'])
            
        if 'pool' not in df.columns:
            df['pool'] = 'core'
        else:
            df['pool'] = df['pool'].fillna('core').str.strip().str.lower()
            
        return df[['ticker', 'name', 'type', 'pool']].to_dict('records')
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
        return None

def analyze_asset(weekly_df, item):
    weekly_df.columns = [str(c).lower() for c in weekly_df.columns]
    prev_week = weekly_df.iloc[-2]
    last_week = weekly_df.iloc[-1]
    
    current_price = last_week['close']
    prev_price = prev_week['close']
    
    pct_change = ((current_price - prev_price) / prev_price) * 100
                
    return {
        "ticker": item['ticker'], 
        "name": item['name'], 
        "type": item['type'], 
        "pool": item['pool'], 
        "current_price": current_price,
        "pct_change": pct_change
    }

def fetch_yf_data(item):
    ticker = item['ticker']
    clean_code = str(ticker).split('.')[0]
    
    if '.SS' in ticker or '.SZ' in ticker:
        sec_id = f"1.{clean_code}" if ticker.endswith('.SS') or clean_code.startswith(('5', '6')) else f"0.{clean_code}"
        em_url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec_id}&klt=101&fqt=1&end=20500101&lmt=100&fields1=f1,f2,f3&fields2=f51,f53,f54,f55"
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
                        if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass

        prefix = 'sh' if '.SS' in ticker else 'sz'
        tc = f"{prefix}{clean_code}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,100,qfq"
        try:
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
                        if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass
        return None

    else:
        try:
            yf_ticker = ticker
            if '.HK' in ticker and len(clean_code) == 5 and clean_code.startswith('0'):
                yf_ticker = f"{clean_code[1:]}.HK"
            data = yf.Ticker(yf_ticker).history(period="1y", interval="1d", auto_adjust=True)
            if len(data) > 0:
                aligned_df = align_to_last_friday(data)
                if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass
            
        try:
            target_codes = []
            if '.HK' in ticker: target_codes = ['hk' + clean_code]
            else: 
                target_codes = [
                    'us' + str(ticker), 'us' + str(ticker).replace('-', '.'),
                    'us' + str(ticker).replace('-', '_'), 'us' + str(ticker).replace('-', '')
                ]
            for tc in target_codes:
                url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,100,qfq"
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
                            if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass
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
            if aligned_df is not None: return analyze_asset(aligned_df, item)
    except: pass
    return None

def fetch_crypto_data(item):
    ticker = item['ticker']
    try:
        yf_symbol = str(ticker).upper().replace('/USDT', '-USD').replace('/USDC', '-USD')
        data = yf.Ticker(yf_symbol).history(period="1y", interval="1d", auto_adjust=True)
        if len(data) > 0:
            aligned_df = align_to_last_friday(data)
            if aligned_df is not None: return analyze_asset(aligned_df, item)
    except: pass

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
                if aligned_df is not None: return analyze_asset(aligned_df, item)
    except: pass
    return None

# ================= 🚀 HTML UI 渲染引擎 (三列极简 APP 视觉) =================

def build_html_table(assets):
    """构建像素级对齐的三列 HTML 数据表"""
    if not assets: return ""
    
    html = '<table style="width:100%; border-collapse: collapse; font-family: -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif;">'
    # 表头设计：去掉了涨跌额，保留名称/代码、最新、涨幅三列
    html += '<tr style="color:#888; font-size:12px; border-bottom:1px solid #eaeaea;">'
    html += '<th style="text-align:left; padding:8px 0; font-weight:normal;">名称/代码</th>'
    html += '<th style="text-align:right; padding:8px 0; font-weight:normal;">最新</th>'
    html += '<th style="text-align:right; padding:8px 0; font-weight:normal;">涨幅</th></tr>'
    
    for r in assets:
        pct = r['pct_change']
        
        # 配色逻辑：中国市场红涨绿跌
        if pct > 0:
            color = "#F9293E" # 东财红
            sign = "+"
        elif pct < 0:
            color = "#00AA3B" # 东财绿
            sign = ""
        else:
            color = "#999999" # 平盘灰
            sign = ""
            
        # 动态小数位：加密货币价格保留2位，ETF/股票保留3位，基金保留4位
        if r['type'] == 'crypto': price_dec = 2
        elif r['type'] == 'jj': price_dec = 4
        else: price_dec = 3
            
        price_str = f"{r['current_price']:.{price_dec}f}"
        pct_str = f"{sign}{pct:.2f}%"
        
        html += '<tr style="border-bottom:1px solid #f5f5f5;">'
        # 核心：利用 div 和 br 实现名称与代码折叠展示
        html += f'<td style="padding:10px 0;"><div style="font-size:15px; color:#333; font-weight:bold; margin-bottom:2px;">{r["name"]}</div><div style="font-size:11px; color:#999;">{r["ticker"]}</div></td>'
        html += f'<td style="text-align:right; padding:10px 0; color:{color}; font-size:16px; font-weight:500;">{price_str}</td>'
        html += f'<td style="text-align:right; padding:10px 0; color:{color}; font-size:15px; font-weight:500;">{pct_str}</td>'
        html += '</tr>'
        
    html += '</table><br>'
    return html

def build_section(title, assets):
    if not assets: return ""
    html = f'<div style="font-size:18px; font-weight:bold; color:#111; margin: 20px 0 10px 0; padding-left: 8px; border-left: 4px solid #337ab7;">{title}</div>'
    html += build_html_table(assets)
    return html

def send_wxpusher_report(all_reports, failed_list):
    # 统一按涨跌幅降序
    all_reports.sort(key=lambda x: x['pct_change'], reverse=True)
    
    # 强制分类逻辑
    core_pool = [r for r in all_reports if 'core' in str(r.get('pool', '')).lower()]
    watch_pool = [r for r in all_reports if 'core' not in str(r.get('pool', '')).lower() and 'watch' in str(r.get('pool', '')).lower()]
    
    html_content = f'<div style="padding: 10px; background-color: #fff;">'
    html_content += f'<h2 style="text-align:center; color:#333; margin-bottom: 20px;">资产全景看板</h2>'
    
    # 构建核心持仓
    if core_pool:
        html_content += build_section("💼 核心持仓", core_pool)
        
    # 构建备选观察
    if watch_pool:
        html_content += build_section("👀 备选观察", watch_pool)
        
    # 盲区公示
    if failed_list:
        failed_names = ", ".join([f['name'] for f in failed_list])
        html_content += f'<div style="margin-top:20px; font-size:12px; color:#f0ad4e; background:#fcf8e3; padding:10px; border-radius:4px;">⚠️ <b>未能获取数据的标的：</b><br>{failed_names}</div>'
        
    html_content += '</div>'

    # 推送至 WxPusher
    if not WXPUSHER_APP_TOKEN or not WXPUSHER_UID:
        print("未检测到 WXPUSHER_APP_TOKEN 或 WXPUSHER_UID，跳过推送。")
        return
        
    url = "https://wxpusher.zjiecode.com/api/send/message"
    payload = {
        "appToken": WXPUSHER_APP_TOKEN,
        "content": html_content,
        "summary": f"📈 {datetime.datetime.now().strftime('%m-%d')} 资产全景看板", 
        "contentType": 2, # 2表示原生HTML渲染
        "uids": [WXPUSHER_UID]
    }
    
    try:
        res = requests.post(url, json=payload)
        print("WxPusher 推送响应:", res.text)
    except Exception as e:
        print(f"WxPusher 推送失败: {e}")

    # 同时归档 Markdown 格式到 Github 仓库 (去掉 HTML 标签纯文本保存)
    try:
        if not os.path.exists(REPORT_DIR): os.makedirs(REPORT_DIR)
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        file_path = os.path.join(REPORT_DIR, f"{today_str}_weekly_report.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# 📈 资产配置周报 ({today_str})\n\n已推送到 WxPusher，查看手机端原生排版。")
    except Exception as e:
        pass

if __name__ == "__main__":
    start_time = datetime.datetime.now()
    print(f"[{start_time}] 引擎点火，执行仿原生 APP 三列极简排版...")
    
    assets_清单 = load_portfolio()
    all_reports = []
    failed_assets = [] 
    
    for item in assets_清单:
        res = None
        if item['type'] == 'yf': res = fetch_yf_data(item)
        elif item['type'] == 'crypto': res = fetch_crypto_data(item)
        elif item['type'] == 'jj': res = fetch_fund_data(item)
        
        if res: all_reports.append(res)
        else: failed_assets.append(item)
            
    send_wxpusher_report(all_reports, failed_assets)
    print(f"扫描完毕，总耗时: {(datetime.datetime.now() - start_time).seconds} 秒")
